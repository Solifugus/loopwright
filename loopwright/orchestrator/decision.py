"""Reviewer and deterministic decision rules.

``evaluate`` extracts hard facts from the run's persisted step results;
``decide`` maps those facts to exactly one action via a fixed rule table.
No AI is involved here on purpose — the design doc's rule is that agents
propose, but the orchestrator's control decisions must be deterministic
and auditable.

Rule table (first match wins):

1. a step failed and its retry budget remains       → RETRY that step
2. a step failed and the budget is exhausted        → PAUSE (repeated failure)
3. worker + deployment ok, no tasks remaining       → FINISH (candidate ready)
4. worker + deployment ok, tasks remaining          → CONTINUE (next cycle)
5. anything else (missing/limit/partial results)    → PAUSE (needs a human)

("docs touched when required" from the design doc is deferred until packets
can declare documentation requirements — v0.2 material.)
"""

from dataclasses import dataclass
from enum import Enum

from loopwright.core.model import Run

DEV_STEP = "dev-code"
DEPLOY_STEP = "deploy-test"


class Action(str, Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    PAUSE = "pause"
    FINISH = "finish"


@dataclass
class Review:
    """Facts about the just-finished cycle, extracted from run.json."""

    worker_ok: bool
    deployment_ok: bool
    tasks_remaining: bool
    checkpoint: str | None
    failed_step: str | None


@dataclass
class Decision:
    action: Action
    reason: str
    step: str | None = None  # which step to retry, for Action.RETRY


def evaluate(run: Run) -> Review:
    dev = run.step_result(DEV_STEP)
    deploy = run.step_result(DEPLOY_STEP)
    worker_ok = bool(dev and dev["status"] == "ok")
    return Review(
        worker_ok=worker_ok,
        deployment_ok=bool(deploy and deploy["status"] == "ok"),
        tasks_remaining=(
            bool(dev["detail"].get("tasks_remaining", True)) if worker_ok else True
        ),
        checkpoint=dev["detail"].get("checkpoint") if dev else None,
        failed_step=next((s["name"] for s in run.steps if s["status"] == "failed"), None),
    )


def decide(
    review: Review, attempts: dict[str, int] | None = None, retry_limit: int = 2
) -> Decision:
    attempts = attempts or {}
    if review.failed_step is not None:
        used = attempts.get(review.failed_step, 0)
        if used < retry_limit:
            return Decision(
                Action.RETRY,
                f"{review.failed_step} failed; retry {used + 1} of {retry_limit}",
                step=review.failed_step,
            )
        return Decision(
            Action.PAUSE,
            f"{review.failed_step} failed {used + 1} times; a human needs to look",
            step=review.failed_step,
        )
    if review.worker_ok and review.deployment_ok:
        if not review.tasks_remaining:
            return Decision(Action.FINISH, "all tasks complete and deployment passed")
        return Decision(Action.CONTINUE, "cycle passed and tasks remain")
    return Decision(Action.PAUSE, "run results are incomplete; a human needs to look")
