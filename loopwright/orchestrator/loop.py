"""The cycle loop: engine walks the steps, the reviewer decides what's next.

One *cycle* is a full pass of the step sequence (code on the dev VM, then
prove it on the deployment VM). After each engine run the deterministic
decision rules pick exactly one of: run another cycle, retry the failed
step, finish with a candidate, or stop and wait for a human.
"""

from loopwright.core.model import ProjectStore, RunState
from loopwright.core.runlog import RunLog
from loopwright.notify.ntfy import Event
from loopwright.orchestrator.decision import Action, decide, evaluate
from loopwright.orchestrator.engine import COMPLETED, FAILED, Engine, Step

FINISHED = "finished"
PAUSED_FOR_HUMAN = "paused-for-human"
MAX_CYCLES_REACHED = "max-cycles-reached"


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


def run_loop(
    store: ProjectStore,
    project: str,
    steps: list[Step],
    notifier=None,
    retry_limit: int = 2,
    max_cycles: int = 25,
) -> str:
    """Drive cycles until finish, pause, park, or the cycle cap.

    Returns one of: ``finished``, ``paused-for-human``, ``paused-limit``,
    ``interrupted``, ``max-cycles-reached``.
    """
    log = RunLog(store.project_dir(project) / "logs")
    attempts: dict[str, int] = {}
    for _ in range(max_cycles):
        outcome = Engine(store, project, steps, notifier=notifier).run()
        if outcome not in (COMPLETED, FAILED):
            # paused-limit or interrupted: already parked with notification
            return outcome

        review = evaluate(store.load_run(project))
        decision = decide(review, attempts, retry_limit)
        log.log("review", f"decision: {decision.action.value} — {decision.reason}")

        if decision.action is Action.RETRY:
            attempts[decision.step] = attempts.get(decision.step, 0) + 1
            _retry_step(store, project, decision.step)
            continue
        if decision.action is Action.CONTINUE:
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

    log.log("review", f"stopping after {max_cycles} cycles without finishing", level="warning")
    if notifier is not None:
        notifier.notify(
            Event.APPROVAL_NEEDED,
            f"hit the {max_cycles}-cycle cap without finishing",
            project=project,
        )
    return MAX_CYCLES_REACHED
