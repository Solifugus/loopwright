import subprocess

import pytest

from loopwright import service
from loopwright.core.model import IllegalTransition, ProjectStore, Run, RunState
from loopwright.gitctl.repo import BRANCHES, GitError, ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


def repo_file(repo_path, ref, name) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), "show", f"{ref}:{name}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_create_project_builds_store_packet_and_repo(store):
    project = service.create_project(store, "demo")
    assert (store.project_dir("demo") / "project.yaml").is_file()
    for filename in service.PACKET_FILES:
        assert (service.packet_dir(store, "demo") / filename).is_file()
    repo = ProjectRepo(project.repo_path)
    assert repo.branches() == sorted(BRANCHES)
    assert "demo — Design" in repo_file(project.repo_path, "design/main", "DESIGN.md")


def test_create_project_cleans_up_on_git_failure(store, monkeypatch):
    def boom(*args, **kwargs):
        raise GitError("simulated git failure")

    monkeypatch.setattr(ProjectRepo, "init", boom)
    with pytest.raises(GitError):
        service.create_project(store, "demo")
    assert not store.project_dir("demo").exists()

    monkeypatch.undo()
    service.create_project(store, "demo")  # retry after failure must work


def test_save_and_load_packet_roundtrip(store):
    service.create_project(store, "demo")
    service.save_packet(store, "demo", {"DESIGN.md": "# new design\n"})
    files = service.load_packet(store, "demo")
    assert files["DESIGN.md"] == "# new design\n"
    assert "Development Plan" in files["DEVPLAN.md"]  # untouched files keep their content


def test_approve_packet_commits_and_moves_to_ready(store):
    project = service.create_project(store, "demo")
    service.save_packet(store, "demo", {"DESIGN.md": "# approved design\n"})
    before = ProjectRepo(project.repo_path).head_of("design/main")

    commit = service.approve_packet(store, "demo")

    assert commit != before
    assert store.load_run("demo").state is RunState.READY
    assert repo_file(project.repo_path, "design/main", "DESIGN.md") == "# approved design\n"


def test_reapprove_while_ready_commits_again(store):
    project = service.create_project(store, "demo")
    first = service.approve_packet(store, "demo")
    service.save_packet(store, "demo", {"DESIGN.md": "# v2\n"})
    second = service.approve_packet(store, "demo")
    assert first != second
    assert store.load_run("demo").state is RunState.READY
    assert repo_file(project.repo_path, "design/main", "DESIGN.md") == "# v2\n"


def test_approve_rejected_outside_draft_and_ready(store):
    service.create_project(store, "demo")
    run = store.load_run("demo")
    run.transition(RunState.READY)
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)

    with pytest.raises(ValueError, match="RUNNING"):
        service.approve_packet(store, "demo")


# --- run controls (task 5.3) ---


def make_run(store, name, *states):
    store.create(name, "/nowhere/repo.git")
    run = store.load_run(name)
    for state in states:
        run.transition(state)
    store.save_run(name, run)
    return run


def test_start_from_ready_notifies(store):
    make_run(store, "demo", RunState.READY)
    notifier = NullNotifier()
    run = service.control_run(store, "demo", "start", notifier=notifier)
    assert run.state is RunState.RUNNING
    assert store.load_run("demo").state is RunState.RUNNING
    assert notifier.events == [(Event.RUN_STARTED, "Run started for demo", "demo")]


def test_pause_resume_stop_flow(store):
    make_run(store, "demo", RunState.READY, RunState.RUNNING)
    assert service.control_run(store, "demo", "pause").state is RunState.PAUSED
    assert service.control_run(store, "demo", "resume").state is RunState.RUNNING
    assert service.control_run(store, "demo", "stop").state is RunState.STOPPED


def test_resume_from_paused_limit(store):
    make_run(store, "demo", RunState.READY, RunState.RUNNING, RunState.PAUSED_LIMIT)
    assert service.control_run(store, "demo", "resume").state is RunState.RUNNING


def test_start_from_draft_is_illegal(store):
    make_run(store, "demo")
    notifier = NullNotifier()
    with pytest.raises(IllegalTransition, match="cannot start"):
        service.control_run(store, "demo", "start", notifier=notifier)
    assert store.load_run("demo").state is RunState.DRAFT
    assert notifier.events == []


def test_start_from_paused_is_illegal_use_resume(store):
    make_run(store, "demo", RunState.READY, RunState.RUNNING, RunState.PAUSED)
    with pytest.raises(IllegalTransition, match="cannot start"):
        service.control_run(store, "demo", "start")


def test_unknown_action_raises(store):
    make_run(store, "demo", RunState.READY)
    with pytest.raises(ValueError, match="unknown run action"):
        service.control_run(store, "demo", "explode")


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ((), []),
        ((RunState.READY,), ["start", "stop"]),
        ((RunState.READY, RunState.RUNNING), ["pause", "stop"]),
        ((RunState.READY, RunState.RUNNING, RunState.PAUSED), ["resume", "stop"]),
        ((RunState.READY, RunState.RUNNING, RunState.PAUSED_LIMIT), ["resume", "stop"]),
        ((RunState.READY, RunState.RUNNING, RunState.REVIEW), ["stop"]),
        ((RunState.READY, RunState.STOPPED), []),
    ],
)
def test_available_actions_by_state(states, expected):
    run = Run()
    for state in states:
        run.transition(state)
    assert sorted(service.available_actions(run)) == sorted(expected)


def test_list_checkpoints_without_repo_is_empty(store):
    store.create("demo", "/nowhere/repo.git")
    assert service.list_checkpoints(store, "demo") == []


def test_list_checkpoints_returns_tags(store):
    project = service.create_project(store, "demo")
    repo = ProjectRepo(project.repo_path)
    repo.tag_checkpoint("first")
    repo.tag_checkpoint("second")
    assert service.list_checkpoints(store, "demo") == [
        "checkpoint/0001-first",
        "checkpoint/0002-second",
    ]
