import json
import signal
import subprocess
import sys
import time

import pytest

from loopwright.core.model import ProjectStore, Run, RunState
from loopwright.orchestrator.engine import (
    COMPLETED,
    FAILED,
    INTERRUPTED,
    Engine,
    EngineError,
    Step,
    StepFailed,
)


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


def ready(store, name="demo"):
    store.create(name, "/nowhere/repo.git")
    run = store.load_run(name)
    run.transition(RunState.READY)
    store.save_run(name, run)


def make_steps(names, record):
    return [Step(name, lambda ctx, n=name: record.append(n) or {"did": n}) for name in names]


def test_full_walk_completes_and_persists(store):
    ready(store)
    record = []
    engine = Engine(store, "demo", make_steps(["one", "two", "three"], record))

    assert engine.run() == COMPLETED
    assert record == ["one", "two", "three"]

    run = store.load_run("demo")
    assert run.state is RunState.REVIEW
    assert [s["name"] for s in run.steps] == ["one", "two", "three"]
    assert all(s["status"] == "ok" for s in run.steps)
    assert run.steps[0]["detail"] == {"did": "one"}
    assert run.steps[0]["started"] <= run.steps[0]["finished"]


def test_engine_logs_steps(store):
    ready(store)
    engine = Engine(store, "demo", make_steps(["one"], []))
    engine.run()
    messages = [(e["step"], e["message"]) for e in engine.log.read()]
    assert ("engine", "run started") in messages
    assert ("one", "step started") in messages
    assert ("one", "step completed") in messages


def test_crash_then_resume_skips_completed_steps(store):
    ready(store)
    calls = []

    def boom(ctx):
        raise KeyboardInterrupt  # simulates a kill: not a controlled failure

    steps = [
        Step("one", lambda ctx: calls.append("one")),
        Step("two", boom),
        Step("three", lambda ctx: calls.append("three")),
    ]
    with pytest.raises(KeyboardInterrupt):
        Engine(store, "demo", steps).run()

    run = store.load_run("demo")
    assert run.state is RunState.RUNNING  # crash leaves the run resumable
    assert [s["name"] for s in run.steps] == ["one"]  # in-flight step not recorded

    fixed = [
        Step("one", lambda ctx: calls.append("one-again")),
        Step("two", lambda ctx: calls.append("two")),
        Step("three", lambda ctx: calls.append("three")),
    ]
    assert Engine(store, "demo", fixed).run() == COMPLETED
    assert "one-again" not in calls  # completed step was skipped on resume
    assert calls == ["one", "two", "three"]
    assert store.load_run("demo").state is RunState.REVIEW


def test_controlled_failure_records_and_moves_to_review(store):
    ready(store)

    def bad(ctx):
        raise StepFailed("tests did not pass")

    steps = [Step("one", lambda ctx: None), Step("two", bad), Step("three", lambda ctx: None)]
    assert Engine(store, "demo", steps).run() == FAILED

    run = store.load_run("demo")
    assert run.state is RunState.REVIEW  # decision rules choose retry/pause/fail
    assert run.step_result("two")["status"] == "failed"
    assert run.step_result("two")["detail"] == {"error": "tests did not pass"}
    assert run.step_result("three") is None


def test_record_step_replaces_result_for_same_name():
    run = Run()
    run.record_step("two", "failed", "2026-01-01T00:00:00+00:00", {"error": "x"})
    run.record_step("two", "ok", "2026-01-01T00:01:00+00:00", {"attempt": 2})
    assert len(run.steps) == 1
    assert run.step_result("two")["status"] == "ok"
    assert run.step_result("two")["detail"] == {"attempt": 2}


def test_failed_run_needs_decision_before_rerun(store):
    ready(store)
    Engine(store, "demo", [Step("x", lambda ctx: (_ for _ in ()).throw(StepFailed("no")))]).run()
    # the run sits in REVIEW; only a decision (retry/new cycle) moves it back
    with pytest.raises(EngineError, match="cannot run from state REVIEW"):
        Engine(store, "demo", [Step("x", lambda ctx: None)]).run()


def test_pause_between_steps_interrupts(store):
    ready(store)

    def pause_from_ui(ctx):
        run = ctx.store.load_run(ctx.project)
        run.transition(RunState.PAUSED)
        ctx.store.save_run(ctx.project, run)
        return {"paused": True}

    calls = []
    steps = [
        Step("one", pause_from_ui),
        Step("two", lambda ctx: calls.append("two")),
    ]
    assert Engine(store, "demo", steps).run() == INTERRUPTED
    assert calls == []
    run = store.load_run("demo")
    assert run.state is RunState.PAUSED
    assert run.step_result("one")["status"] == "ok"

    # resume from the UI, then the engine picks up at step two
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)
    assert Engine(store, "demo", steps).run() == COMPLETED
    assert calls == ["two"]


def test_cannot_run_from_draft(store):
    store.create("demo", "/nowhere/repo.git")
    with pytest.raises(EngineError, match="cannot run from state DRAFT"):
        Engine(store, "demo", [Step("x", lambda ctx: None)]).run()


def test_duplicate_step_names_rejected(store):
    with pytest.raises(EngineError, match="duplicate step names"):
        Engine(store, "demo", [Step("x", lambda ctx: None), Step("x", lambda ctx: None)])


def test_empty_sequence_rejected(store):
    with pytest.raises(EngineError, match="at least one step"):
        Engine(store, "demo", [])


KILL_SCRIPT = """
import sys, time
from loopwright.core.model import ProjectStore
from loopwright.orchestrator.engine import Engine, Step

store = ProjectStore(sys.argv[1])
marker = sys.argv[2]

def slow(ctx):
    with open(marker, "w") as f:
        f.write("step two in flight")
    time.sleep(60)

steps = [
    Step("one", lambda ctx: {"ok": True}),
    Step("two", slow),
    Step("three", lambda ctx: {"ok": True}),
]
Engine(store, "demo", steps).run()
"""


def test_real_sigkill_mid_run_then_resume(store, tmp_path):
    """The DEVPLAN's 'done when': a faked run killed mid-run resumes correctly."""
    ready(store)
    marker = tmp_path / "in-flight"
    proc = subprocess.Popen(
        [sys.executable, "-c", KILL_SCRIPT, str(store.root), str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.time() + 30
    while not marker.exists():
        assert proc.poll() is None, proc.stderr.read().decode()
        assert time.time() < deadline, "step two never started"
        time.sleep(0.05)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)

    run = store.load_run("demo")
    assert run.state is RunState.RUNNING
    assert [s["name"] for s in run.steps] == ["one"]

    calls = []
    steps = [
        Step("one", lambda ctx: calls.append("one")),
        Step("two", lambda ctx: calls.append("two")),
        Step("three", lambda ctx: calls.append("three")),
    ]
    assert Engine(store, "demo", steps).run() == COMPLETED
    assert calls == ["two", "three"]  # resumed exactly at the killed step

    run = store.load_run("demo")
    assert run.state is RunState.REVIEW
    assert all(s["status"] == "ok" for s in run.steps)


def test_run_json_backward_compatible_without_steps(store):
    store.create("demo", "/nowhere/repo.git")
    path = store.project_dir("demo") / "run.json"
    data = json.loads(path.read_text())
    data.pop("steps", None)
    path.write_text(json.dumps(data))
    run = store.load_run("demo")  # must not raise
    assert run.steps == []
