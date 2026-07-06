"""The orchestrator's main loop: an explicit sequence of named, resumable steps.

Persistence rules that make the loop crash-safe:

* A step's result is written into ``run.json`` the moment the step finishes.
  A crash or kill therefore loses at most the step that was in flight.
* Re-running the engine skips steps already recorded ``ok`` and picks up at
  the first incomplete or failed one.
* Between steps the engine reloads run state from disk — if a human paused or
  stopped the run from the UI, the loop halts right there.

Failure semantics:

* A step raising :class:`StepFailed` is a *controlled* failure: it is recorded
  and the run moves to ``FAILED``.
* Any other exception is a crash: nothing is recorded and the run stays
  ``RUNNING``, so the next engine invocation resumes at that step.

Steps are plain ``(name, fn)`` pairs where ``fn(ctx)`` returns an optional
detail dict. VM-touching steps get dry-run fakes injected by the caller.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from loopwright.core.model import ProjectStore, Run, RunState
from loopwright.core.runlog import RunLog
from loopwright.notify.ntfy import Event

COMPLETED = "completed"
FAILED = "failed"
INTERRUPTED = "interrupted"
PAUSED_LIMIT = "paused-limit"


class EngineError(Exception):
    """The engine was asked to do something structurally impossible."""


class StepFailed(Exception):
    """Raised by a step to fail the run with a human-readable reason."""


class UsageLimitReached(Exception):
    """Raised by a step when the worker agent hit its usage limit.

    The engine parks the run in ``PAUSED_LIMIT`` instead of failing it; the
    step is re-run from scratch once the run is resumed.
    """


@dataclass
class Step:
    name: str
    fn: Callable[["StepContext"], dict | None]


@dataclass
class StepContext:
    """What every step gets to work with."""

    store: ProjectStore
    project: str
    log: RunLog
    notifier: object = None
    extras: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Engine:
    def __init__(self, store: ProjectStore, project: str, steps: list[Step], notifier=None):
        names = [step.name for step in steps]
        if len(names) != len(set(names)):
            raise EngineError(f"duplicate step names in sequence: {names}")
        if not steps:
            raise EngineError("engine needs at least one step")
        self.store = store
        self.project = project
        self.steps = list(steps)
        self.log = RunLog(store.project_dir(project) / "logs")
        self.ctx = StepContext(store=store, project=project, log=self.log, notifier=notifier)

    def _load_running(self) -> Run:
        return self.store.load_run(self.project)

    def run(self) -> str:
        """Walk all remaining steps; returns COMPLETED, FAILED, or INTERRUPTED."""
        run = self._load_running()
        if run.state is RunState.READY:
            run.transition(RunState.RUNNING)
            self.store.save_run(self.project, run)
            self.log.log("engine", "run started")
        elif run.state is RunState.RUNNING:
            done = sum(1 for s in run.steps if s["status"] == "ok")
            self.log.log("engine", f"resuming: {done}/{len(self.steps)} steps already complete")
        else:
            raise EngineError(f"cannot run from state {run.state.value}")

        for step in self.steps:
            run = self._load_running()
            if run.state is not RunState.RUNNING:
                self.log.log("engine", f"halting: run moved to {run.state.value}")
                return INTERRUPTED

            result = run.step_result(step.name)
            if result is not None and result["status"] == "ok":
                continue

            started = _now()
            self.log.log(step.name, "step started")
            try:
                detail = step.fn(self.ctx) or {}
            except UsageLimitReached as exc:
                run = self._load_running()
                run.record_step(step.name, "limit", started, {"reason": str(exc)})
                if run.state is RunState.RUNNING:
                    run.transition(RunState.PAUSED_LIMIT)
                self.store.save_run(self.project, run)
                self.log.log(step.name, f"usage limit reached: {exc}", level="warning")
                if self.ctx.notifier is not None:
                    self.ctx.notifier.notify(
                        Event.LIMIT_REACHED, f"{self.project}: {exc}", project=self.project
                    )
                return PAUSED_LIMIT
            except StepFailed as exc:
                run = self._load_running()
                run.record_step(step.name, "failed", started, {"error": str(exc)})
                if run.state is RunState.RUNNING:
                    run.transition(RunState.FAILED)
                self.store.save_run(self.project, run)
                self.log.log(step.name, f"step failed: {exc}", level="error")
                return FAILED

            run = self._load_running()
            run.record_step(step.name, "ok", started, detail)
            self.store.save_run(self.project, run)
            self.log.log(step.name, "step completed")

        run = self._load_running()
        if run.state is RunState.RUNNING:
            run.transition(RunState.REVIEW)
            self.store.save_run(self.project, run)
        self.log.log("engine", "all steps complete; run is ready for review")
        return COMPLETED
