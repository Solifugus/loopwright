import pytest

from loopwright.core.model import (
    TERMINAL_STATES,
    TRANSITIONS,
    IllegalTransition,
    Project,
    ProjectStore,
    Run,
    RunState,
)

ALL_STATES = list(RunState)


def make_run_in(state: RunState) -> Run:
    run = Run()
    run.state = state
    return run


@pytest.mark.parametrize("src", ALL_STATES)
@pytest.mark.parametrize("dst", ALL_STATES)
def test_full_transition_matrix(src, dst):
    run = make_run_in(src)
    if dst in TRANSITIONS[src]:
        run.transition(dst)
        assert run.state == dst
        assert run.history[-1]["from"] == src.value
        assert run.history[-1]["to"] == dst.value
        assert "at" in run.history[-1]
    else:
        with pytest.raises(IllegalTransition):
            run.transition(dst)
        assert run.state == src
        assert run.history == []


def test_happy_path_through_lifecycle():
    run = Run()
    for state in [
        RunState.READY,
        RunState.RUNNING,
        RunState.PAUSED,
        RunState.RUNNING,
        RunState.PAUSED_LIMIT,
        RunState.RUNNING,
        RunState.REVIEW,
        RunState.RUNNING,
        RunState.REVIEW,
        RunState.DONE,
    ]:
        run.transition(state)
    assert run.state == RunState.DONE
    assert run.is_terminal
    assert len(run.history) == 10


def test_terminal_states_have_no_exits():
    assert TERMINAL_STATES == {RunState.DONE, RunState.FAILED, RunState.STOPPED}
    for state in TERMINAL_STATES:
        run = make_run_in(state)
        assert run.is_terminal


def test_every_nonterminal_state_can_reach_a_terminal_state():
    for src, targets in TRANSITIONS.items():
        if src in TERMINAL_STATES:
            continue
        reachable, frontier = set(), {src}
        while frontier:
            state = frontier.pop()
            for nxt in TRANSITIONS[state]:
                if nxt not in reachable:
                    reachable.add(nxt)
                    frontier.add(nxt)
        assert reachable & TERMINAL_STATES, f"{src} cannot reach any terminal state"


def test_invalid_project_names_rejected():
    for bad in ["", "Has Spaces", "UPPER", "../escape", "-leading", "a/b"]:
        with pytest.raises(ValueError):
            Project(name=bad, repo_path="/tmp/x")


def test_project_roundtrip(tmp_path):
    store = ProjectStore(tmp_path)
    created = store.create("demo-app", repo_path=str(tmp_path / "repos" / "demo-app.git"))
    loaded = store.load_project("demo-app")
    assert loaded == created


def test_run_roundtrip_preserves_state_and_history(tmp_path):
    store = ProjectStore(tmp_path)
    store.create("demo", repo_path="/r")
    run = store.load_run("demo")
    assert run.state == RunState.DRAFT

    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)

    reloaded = store.load_run("demo")
    assert reloaded == run
    assert reloaded.state == RunState.RUNNING
    assert [h["to"] for h in reloaded.history] == ["READY", "RUNNING"]


def test_run_roundtrip_preserves_provisionals(tmp_path):
    store = ProjectStore(tmp_path)
    store.create("demo", repo_path="/r")
    run = store.load_run("demo")
    assert run.add_provisional({"id": "x", "summary": "s", "commit": "c", "checkpoint": None})
    assert not run.add_provisional({"id": "x", "summary": "dup"})  # id dedupe
    store.save_run("demo", run)

    reloaded = store.load_run("demo")
    assert reloaded == run
    assert reloaded.provisionals[0]["id"] == "x"
    assert reloaded.remove_provisional("x") is True
    assert reloaded.provisionals == []


def test_create_duplicate_project_raises(tmp_path):
    store = ProjectStore(tmp_path)
    store.create("demo", repo_path="/r")
    with pytest.raises(FileExistsError):
        store.create("demo", repo_path="/r")


def test_list_projects(tmp_path):
    store = ProjectStore(tmp_path)
    assert store.list_projects() == []
    store.create("bravo", repo_path="/r")
    store.create("alpha", repo_path="/r")
    (tmp_path / "not-a-project").mkdir()
    assert store.list_projects() == ["alpha", "bravo"]


def test_store_rejects_traversal_names(tmp_path):
    store = ProjectStore(tmp_path)
    with pytest.raises(ValueError):
        store.project_dir("../outside")
