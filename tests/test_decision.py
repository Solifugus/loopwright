import pytest

from loopwright.core.model import ProjectStore, Run, RunState
from loopwright.notify.ntfy import Event, NullNotifier
from loopwright.orchestrator.decision import (
    Action,
    Review,
    decide,
    evaluate,
)
from loopwright.orchestrator.engine import Step, StepFailed, UsageLimitReached
from loopwright.orchestrator.loop import (
    FINISHED,
    MAX_CYCLES_REACHED,
    PAUSED_FOR_HUMAN,
    run_loop,
)

# --- the decision table, every outcome (DEVPLAN 6.4 'done when') ---


def review(worker_ok=True, deployment_ok=True, tasks_remaining=True, failed_step=None):
    return Review(
        worker_ok=worker_ok,
        deployment_ok=deployment_ok,
        tasks_remaining=tasks_remaining,
        checkpoint="checkpoint/0001-x" if worker_ok else None,
        failed_step=failed_step,
    )


@pytest.mark.parametrize(
    ("rev", "attempts", "expected_action"),
    [
        # rule 1: failure with retry budget left
        (review(worker_ok=False, failed_step="dev-code"), {}, Action.RETRY),
        (review(deployment_ok=False, failed_step="deploy-test"), {"deploy-test": 1}, Action.RETRY),
        # rule 2: budget exhausted
        (review(worker_ok=False, failed_step="dev-code"), {"dev-code": 2}, Action.PAUSE),
        # rule 3: everything passed, nothing left to do
        (review(tasks_remaining=False), {}, Action.FINISH),
        # rule 4: everything passed, more tasks
        (review(tasks_remaining=True), {}, Action.CONTINUE),
        # rule 5: incomplete results (e.g. limit-parked step, missing deploy)
        (review(worker_ok=False, failed_step=None), {}, Action.PAUSE),
        (review(deployment_ok=False, failed_step=None), {}, Action.PAUSE),
    ],
)
def test_decision_table(rev, attempts, expected_action):
    decision = decide(rev, attempts, retry_limit=2)
    assert decision.action is expected_action


def test_retry_names_the_step_and_counts():
    decision = decide(review(worker_ok=False, failed_step="dev-code"), {"dev-code": 1}, 3)
    assert decision.action is Action.RETRY
    assert decision.step == "dev-code"
    assert "retry 2 of 3" in decision.reason


def test_retry_limit_zero_pauses_immediately():
    decision = decide(review(worker_ok=False, failed_step="dev-code"), {}, retry_limit=0)
    assert decision.action is Action.PAUSE


# --- evaluate: facts from run.json ---


def test_evaluate_full_success():
    run = Run()
    run.record_step("dev-code", "ok", "t", {"tasks_remaining": True, "checkpoint": "c/0001-x"})
    run.record_step("deploy-test", "ok", "t", {})
    rev = evaluate(run)
    assert rev.worker_ok and rev.deployment_ok
    assert rev.tasks_remaining is True
    assert rev.checkpoint == "c/0001-x"
    assert rev.failed_step is None


def test_evaluate_all_done():
    run = Run()
    run.record_step("dev-code", "ok", "t", {"tasks_remaining": False})
    run.record_step("deploy-test", "ok", "t", {})
    assert evaluate(run).tasks_remaining is False


def test_evaluate_failed_step():
    run = Run()
    run.record_step("dev-code", "ok", "t", {"tasks_remaining": True})
    run.record_step("deploy-test", "failed", "t", {"error": "acceptance failed"})
    rev = evaluate(run)
    assert rev.failed_step == "deploy-test"
    assert rev.deployment_ok is False


def test_evaluate_limit_step_is_not_a_failure():
    run = Run()
    run.record_step("dev-code", "limit", "t", {"reason": "usage limit"})
    rev = evaluate(run)
    assert rev.failed_step is None
    assert rev.worker_ok is False  # falls to rule 5 (PAUSE) if ever decided on


def test_evaluate_empty_run():
    rev = evaluate(Run())
    assert not rev.worker_ok and not rev.deployment_ok
    assert rev.failed_step is None


# --- the loop wiring decisions to the engine ---


@pytest.fixture
def store(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    store.create("demo", "/nowhere/repo.git")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    store.save_run("demo", run)
    return store


def dev_like(fn):
    return Step("dev-code", fn)


def deploy_like(fn=lambda ctx: {}):
    return Step("deploy-test", fn)


def test_loop_continues_then_finishes(store):
    notifier = NullNotifier()
    dev_calls = {"n": 0}

    def dev(ctx):
        dev_calls["n"] += 1
        return {"tasks_remaining": dev_calls["n"] < 3, "checkpoint": f"c/{dev_calls['n']}"}

    outcome = run_loop(store, "demo", [dev_like(dev), deploy_like()], notifier=notifier)

    assert outcome == FINISHED
    assert dev_calls["n"] == 3  # two CONTINUE cycles, then FINISH
    run = store.load_run("demo")
    assert run.state is RunState.REVIEW  # candidate awaits human approval (8.2)
    assert run.cycle == 2
    events = [event for event, _, _ in notifier.events]
    assert events[-1] == Event.CANDIDATE_READY


def test_loop_retries_flaky_step_then_finishes(store):
    deploy_calls = {"n": 0}

    def flaky_deploy(ctx):
        deploy_calls["n"] += 1
        if deploy_calls["n"] == 1:
            raise StepFailed("first deploy failed")
        return {}

    dev_calls = {"n": 0}

    def dev(ctx):
        dev_calls["n"] += 1
        return {"tasks_remaining": False}

    outcome = run_loop(store, "demo", [dev_like(dev), deploy_like(flaky_deploy)])

    assert outcome == FINISHED
    assert deploy_calls["n"] == 2  # failed once, retried once
    assert dev_calls["n"] == 1  # retry did NOT re-run the completed dev step


def test_loop_pauses_after_retries_exhausted(store):
    notifier = NullNotifier()
    calls = {"n": 0}

    def always_fails(ctx):
        calls["n"] += 1
        raise StepFailed("acceptance keeps failing")

    outcome = run_loop(
        store, "demo",
        [dev_like(lambda ctx: {"tasks_remaining": False}), deploy_like(always_fails)],
        notifier=notifier,
        retry_limit=1,
    )

    assert outcome == PAUSED_FOR_HUMAN
    assert calls["n"] == 2  # original attempt + 1 retry
    assert store.load_run("demo").state is RunState.REVIEW
    events = [event for event, _, _ in notifier.events]
    assert Event.REPEATED_FAILURE in events


def test_loop_usage_limit_then_human_stop(store):
    def limited(ctx):
        raise UsageLimitReached("out of tokens")

    def human_stops(seconds):
        run = store.load_run("demo")
        if run.state is RunState.PAUSED_LIMIT:
            run.transition(RunState.STOPPED)
            store.save_run("demo", run)

    outcome = run_loop(
        store, "demo", [dev_like(limited), deploy_like()],
        limit_resume_delay=9999, sleep=human_stops,
    )
    assert outcome == "stopped"
    assert store.load_run("demo").state is RunState.STOPPED


def test_loop_stops_at_max_cycles(store):
    notifier = NullNotifier()
    outcome = run_loop(
        store, "demo",
        [dev_like(lambda ctx: {"tasks_remaining": True}), deploy_like()],
        notifier=notifier,
        max_cycles=3,
    )
    assert outcome == MAX_CYCLES_REACHED
    events = [event for event, _, _ in notifier.events]
    assert Event.APPROVAL_NEEDED in events


def test_loop_respects_ui_stop(store):
    def dev_then_stop(ctx):
        run = ctx.store.load_run(ctx.project)
        run.transition(RunState.STOPPED)
        ctx.store.save_run(ctx.project, run)
        return {"tasks_remaining": True}

    outcome = run_loop(store, "demo", [dev_like(dev_then_stop), deploy_like()])
    assert outcome == "stopped"
    assert store.load_run("demo").state is RunState.STOPPED


# --- pause/resume hardening (task 6.5) ---


def test_pause_mid_run_then_human_resume(store):
    """UI pause during a step halts the loop; Resume picks up where it left off."""
    calls = []

    def dev_and_pause(ctx):
        calls.append("dev")
        run = ctx.store.load_run(ctx.project)
        run.transition(RunState.PAUSED)  # human clicked Pause while dev ran
        ctx.store.save_run(ctx.project, run)
        return {"tasks_remaining": False}

    def human_resumes(seconds):
        run = store.load_run("demo")
        if run.state is RunState.PAUSED:
            run.transition(RunState.RUNNING)
            store.save_run("demo", run)

    def deploy(ctx):
        calls.append("deploy")
        return {}

    outcome = run_loop(
        store, "demo", [dev_like(dev_and_pause), deploy_like(deploy)], sleep=human_resumes
    )
    assert outcome == FINISHED
    assert calls == ["dev", "deploy"]  # dev not re-run after resume


def test_pause_then_human_stop(store):
    def dev_and_pause(ctx):
        run = ctx.store.load_run(ctx.project)
        run.transition(RunState.PAUSED)
        ctx.store.save_run(ctx.project, run)
        return {"tasks_remaining": False}

    def human_stops(seconds):
        run = store.load_run("demo")
        if run.state is RunState.PAUSED:
            run.transition(RunState.STOPPED)
            store.save_run("demo", run)

    outcome = run_loop(store, "demo", [dev_like(dev_and_pause), deploy_like()], sleep=human_stops)
    assert outcome == "stopped"


def test_usage_limit_auto_resumes_after_delay(store):
    sleeps = []
    attempts = {"n": 0}

    def limited_once(ctx):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise UsageLimitReached("out of tokens")
        return {"tasks_remaining": False}

    outcome = run_loop(
        store, "demo",
        [dev_like(limited_once), deploy_like()],
        poll_interval=1.0,
        limit_resume_delay=3.0,
        sleep=sleeps.append,
    )
    assert outcome == FINISHED
    assert attempts["n"] == 2  # limit-parked step re-ran after auto-resume
    assert len(sleeps) >= 3  # waited out the delay in poll_interval slices
    assert store.load_run("demo").state is RunState.REVIEW


def test_loop_restarts_from_review(store):
    run = store.load_run("demo")
    run.transition(RunState.RUNNING)
    run.record_step("dev-code", "ok", "t", {"tasks_remaining": True})
    run.transition(RunState.REVIEW)
    store.save_run("demo", run)

    outcome = run_loop(
        store, "demo",
        [dev_like(lambda ctx: {"tasks_remaining": False}), deploy_like()],
    )
    assert outcome == FINISHED
    run = store.load_run("demo")
    assert run.cycle == 1  # restart began a fresh cycle


def test_loop_resumes_from_paused_entry(store):
    run = store.load_run("demo")
    run.transition(RunState.RUNNING)
    run.transition(RunState.PAUSED)
    store.save_run("demo", run)

    outcome = run_loop(
        store, "demo",
        [dev_like(lambda ctx: {"tasks_remaining": False}), deploy_like()],
    )
    assert outcome == FINISHED


def test_loop_rejects_bad_entry_states(store, tmp_path):
    from loopwright.orchestrator.engine import EngineError

    fresh = ProjectStore(tmp_path / "p2")
    fresh.create("demo", "/nowhere/repo.git")  # still DRAFT
    with pytest.raises(EngineError, match="cannot start the loop from state DRAFT"):
        run_loop(fresh, "demo", [dev_like(lambda ctx: {}), deploy_like()])
