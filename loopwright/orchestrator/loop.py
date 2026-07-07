"""The cycle loop: engine walks the steps, the reviewer decides what's next.

One *cycle* is a full pass of the step sequence (code on the dev VM, then
prove it on the deployment VM). After each engine run the deterministic
decision rules pick exactly one of: run another cycle, retry the failed
step, finish with a candidate, or stop and wait for a human.

Pause behavior: the loop stays alive while the run is paused and polls the
run state — a human Resume (or Stop) from the UI takes effect at the next
poll. A ``PAUSED_LIMIT`` park auto-resumes after ``limit_resume_delay``
seconds unless a human intervenes first.

The loop is restartable: entered with a run in ``REVIEW`` it starts a fresh
cycle (e.g. after a rollback), and in ``PAUSED``/``PAUSED_LIMIT`` it resumes.
"""

import time

from loopwright.core.model import ProjectStore, RunState
from loopwright.core.runlog import RunLog
from loopwright.notify.ntfy import Event
from loopwright.orchestrator.decision import Action, decide, evaluate
from loopwright.orchestrator.engine import (
    COMPLETED,
    FAILED,
    Engine,
    EngineError,
    Step,
)

FINISHED = "finished"
PAUSED_FOR_HUMAN = "paused-for-human"
MAX_CYCLES_REACHED = "max-cycles-reached"
STOPPED = "stopped"


def _retry_step(store: ProjectStore, project: str, step_name: str) -> None:
    run = store.load_run(project)
    run.steps = [s for s in run.steps if s["name"] != step_name]
    run.transition(RunState.RUNNING)
    store.save_run(project, run)


def _new_cycle(store: ProjectStore, project: str) -> None:
    run = store.load_run(project)
    run.steps = []
    run.cycle += 1
    run.transition(RunState.RUNNING)
    store.save_run(project, run)


def _enter(store: ProjectStore, project: str, log: RunLog) -> None:
    """Normalize the entry state so the loop is restartable."""
    run = store.load_run(project)
    if run.state is RunState.REVIEW:
        log.log("loop", "restarting from REVIEW with a fresh cycle")
        _new_cycle(store, project)
    elif run.state in (RunState.PAUSED, RunState.PAUSED_LIMIT):
        log.log("loop", f"resuming from {run.state.value}")
        run.transition(RunState.RUNNING)
        store.save_run(project, run)
    elif run.state not in (RunState.READY, RunState.RUNNING):
        raise EngineError(f"cannot start the loop from state {run.state.value}")


def _wait_while_paused(
    store: ProjectStore,
    project: str,
    log: RunLog,
    poll_interval: float,
    limit_resume_delay: float,
    sleep,
) -> str:
    """Block until the run leaves its paused state; returns 'resumed' or 'stopped'."""
    limit_waited = 0.0
    announced = False
    while True:
        run = store.load_run(project)
        if run.state is RunState.RUNNING:
            log.log("loop", "resumed")
            return "resumed"
        if run.state is RunState.PAUSED:
            sleep(poll_interval)
            continue
        if run.state is RunState.PAUSED_LIMIT:
            if not announced:
                log.log(
                    "loop",
                    f"usage-limit pause; auto-resume in {limit_resume_delay:.0f}s "
                    "unless stopped",
                )
                announced = True
            if limit_waited >= limit_resume_delay:
                run.transition(RunState.RUNNING)
                store.save_run(project, run)
                log.log("loop", "auto-resuming after usage-limit delay")
                return "resumed"
            sleep(poll_interval)
            limit_waited += poll_interval
            continue
        log.log("loop", f"halting: run is {run.state.value}")
        return "stopped"


def run_loop(
    store: ProjectStore,
    project: str,
    steps: list[Step],
    notifier=None,
    retry_limit: int = 2,
    max_cycles: int = 25,
    poll_interval: float = 2.0,
    limit_resume_delay: float = 1800.0,
    sleep=time.sleep,
) -> str:
    """Drive cycles until finish, human pause, stop, or the cycle cap.

    Returns one of: ``finished``, ``paused-for-human``, ``stopped``,
    ``max-cycles-reached``.
    """
    log = RunLog(store.project_dir(project) / "logs")
    _enter(store, project, log)
    attempts: dict[str, int] = {}
    cycles_used = 0
    while True:
        outcome = Engine(store, project, steps, notifier=notifier).run()

        if outcome not in (COMPLETED, FAILED):
            # engine interrupted (pause/stop) or parked on a usage limit
            if _wait_while_paused(
                store, project, log, poll_interval, limit_resume_delay, sleep
            ) == "stopped":
                return STOPPED
            continue  # resumed: engine picks up at the first incomplete step

        review = evaluate(store.load_run(project))
        decision = decide(review, attempts, retry_limit)
        log.log("review", f"decision: {decision.action.value} — {decision.reason}")

        if decision.action is Action.RETRY:
            attempts[decision.step] = attempts.get(decision.step, 0) + 1
            _retry_step(store, project, decision.step)
            continue
        if decision.action is Action.CONTINUE:
            cycles_used += 1
            if cycles_used >= max_cycles:
                log.log("loop", f"hit the {max_cycles}-cycle cap", level="warning")
                if notifier is not None:
                    notifier.notify(
                        Event.APPROVAL_NEEDED,
                        f"hit the {max_cycles}-cycle cap without finishing",
                        project=project,
                    )
                return MAX_CYCLES_REACHED
            attempts.clear()
            _new_cycle(store, project)
            continue
        if decision.action is Action.FINISH:
            if notifier is not None:
                notifier.notify(Event.CANDIDATE_READY, decision.reason, project=project)
            return FINISHED
        # Action.PAUSE — leave the run in REVIEW for the human
        if notifier is not None:
            notifier.notify(Event.REPEATED_FAILURE, decision.reason, project=project)
        return PAUSED_FOR_HUMAN
