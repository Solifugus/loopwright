import subprocess

import pytest

from loopwright import service
from loopwright.core.model import IllegalTransition, ProjectStore, Run, RunState
from loopwright.gitctl.repo import BRANCHES, GitError, ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / "projects")


@pytest.fixture
def doctrine(tmp_path):
    """A minimal valid doctrine dir (the two files create_project requires)."""
    base = tmp_path / "doctrine"
    base.mkdir()
    (base / "PRINCIPLES.md").write_text("# Real Principles\n")
    (base / "AGENT_RULES.md").write_text("# Real Rules\n")
    return base


def repo_file(repo_path, ref, name) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_path), "show", f"{ref}:{name}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_create_project_builds_store_packet_and_repo(store, doctrine):
    project = service.create_project(store, "demo", doctrine_dir=doctrine)
    assert (store.project_dir("demo") / "project.yaml").is_file()
    for filename in service.PACKET_FILES:
        assert (service.packet_dir(store, "demo") / filename).is_file()
    repo = ProjectRepo(project.repo_path)
    assert repo.branches() == sorted(BRANCHES)
    assert "demo — Design" in repo_file(project.repo_path, "design/main", "DESIGN.md")


def test_create_project_cleans_up_on_git_failure(store, doctrine, monkeypatch):
    def boom(*args, **kwargs):
        raise GitError("simulated git failure")

    monkeypatch.setattr(ProjectRepo, "init", boom)
    with pytest.raises(GitError):
        service.create_project(store, "demo", doctrine_dir=doctrine)
    assert not store.project_dir("demo").exists()

    monkeypatch.undo()
    service.create_project(store, "demo", doctrine_dir=doctrine)  # retry must work


def test_save_and_load_packet_roundtrip(store, doctrine):
    service.create_project(store, "demo", doctrine_dir=doctrine)
    service.save_packet(store, "demo", {"DESIGN.md": "# new design\n"})
    files = service.load_packet(store, "demo")
    assert files["DESIGN.md"] == "# new design\n"
    assert "Development Plan" in files["DEVPLAN.md"]  # untouched files keep their content


def test_approve_packet_commits_and_moves_to_ready(store, doctrine):
    project = service.create_project(store, "demo", doctrine_dir=doctrine)
    service.save_packet(store, "demo", {"DESIGN.md": "# approved design\n"})
    before = ProjectRepo(project.repo_path).head_of("design/main")

    commit = service.approve_packet(store, "demo")

    assert commit != before
    assert store.load_run("demo").state is RunState.READY
    assert repo_file(project.repo_path, "design/main", "DESIGN.md") == "# approved design\n"


def test_reapprove_while_ready_commits_again(store, doctrine):
    project = service.create_project(store, "demo", doctrine_dir=doctrine)
    first = service.approve_packet(store, "demo")
    service.save_packet(store, "demo", {"DESIGN.md": "# v2\n"})
    second = service.approve_packet(store, "demo")
    assert first != second
    assert store.load_run("demo").state is RunState.READY
    assert repo_file(project.repo_path, "design/main", "DESIGN.md") == "# v2\n"


def test_approve_rejected_outside_draft_and_ready(store, doctrine):
    service.create_project(store, "demo", doctrine_dir=doctrine)
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


def test_list_checkpoints_returns_tags(store, doctrine):
    project = service.create_project(store, "demo", doctrine_dir=doctrine)
    repo = ProjectRepo(project.repo_path)
    repo.tag_checkpoint("first")
    repo.tag_checkpoint("second")
    assert service.list_checkpoints(store, "demo") == [
        "checkpoint/0001-first",
        "checkpoint/0002-second",
    ]


# --- doctrine and templates (tasks 8.1, 9.3) ---


@pytest.fixture
def doctrine_with_templates(tmp_path):
    base = tmp_path / "doctrine-t"
    (base / "templates").mkdir(parents=True)
    (base / "PRINCIPLES.md").write_text("# Real Principles\n")
    (base / "AGENT_RULES.md").write_text("# Real Rules\n")
    (base / "templates" / "DESIGN.md").write_text("# {{PROJECT}} design from doctrine\n")
    (base / "templates" / "DEVPLAN.md").write_text("# {{PROJECT}} plan\n\n- [ ] 1. start\n")
    (base / "templates" / "TESTPLAN.md").write_text("# {{PROJECT}} tests\n")
    return base


def test_create_project_uses_doctrine_templates(store, doctrine_with_templates):
    project = service.create_project(store, "demo", doctrine_dir=doctrine_with_templates)
    drafts = service.load_packet(store, "demo")
    assert drafts["DESIGN.md"] == "# demo design from doctrine\n"  # placeholder substituted
    assert "- [ ] 1. start" in drafts["DEVPLAN.md"]

    # 9.3: canonical doctrine lands under docs/agent/ so the prompt can point at it.
    repo = ProjectRepo(project.repo_path)
    assert (
        repo_file(project.repo_path, "design/main", "docs/agent/PRINCIPLES.md")
        == "# Real Principles\n"
    )
    assert (
        repo_file(project.repo_path, "design/main", "docs/agent/AGENT_RULES.md")
        == "# Real Rules\n"
    )
    assert repo.has_file("agent/work", "docs/agent/AGENT_RULES.md")  # reaches worker clones


def test_create_project_requires_doctrine_dir(store):
    # 9.3: no built-in fallback — creation refuses to proceed without doctrine.
    with pytest.raises(ValueError, match="doctrine_dir is required"):
        service.create_project(store, "demo", doctrine_dir=None)
    assert not store.project_dir("demo").exists()  # nothing left behind


def test_create_project_refuses_incomplete_doctrine(store, tmp_path):
    base = tmp_path / "doctrine"
    (base / "templates").mkdir(parents=True)
    (base / "PRINCIPLES.md").write_text("# only principles\n")  # AGENT_RULES.md missing

    with pytest.raises(ValueError, match="missing AGENT_RULES.md"):
        service.create_project(store, "demo", doctrine_dir=base)
    assert not store.project_dir("demo").exists()


def test_templates_still_fall_back_per_file(store, tmp_path):
    """Doctrine is mandatory, but missing *template* files still use built-ins."""
    base = tmp_path / "doctrine"
    base.mkdir()
    (base / "PRINCIPLES.md").write_text("# p\n")
    (base / "AGENT_RULES.md").write_text("# r\n")
    (base / "templates").mkdir()
    (base / "templates" / "DESIGN.md").write_text("# {{PROJECT}} custom design\n")

    service.create_project(store, "demo", doctrine_dir=base)
    drafts = service.load_packet(store, "demo")
    assert drafts["DESIGN.md"] == "# demo custom design\n"
    assert "Development Plan" in drafts["DEVPLAN.md"]  # built-in template fallback


# --- rollback to checkpoint (task 6.5) ---


def rollback_env(store, doctrine):
    """Project in READY with a checkpoint, then agent/work advanced past it."""
    project = service.create_project(store, "demo", doctrine_dir=doctrine)
    run = store.load_run("demo")
    run.transition(RunState.READY)
    run.record_step("dev-code", "ok", "t", {"checkpoint": "x"})
    store.save_run("demo", run)
    repo = ProjectRepo(project.repo_path)
    tag = repo.tag_checkpoint("good-state")  # at current agent/work
    old_head = repo.head_of("agent/work")
    repo.commit_packet({"DESIGN.md": "# moved on\n"}, message="advance design/main")
    repo.reset_branch("agent/work", "design/main")  # simulate later worker commits
    assert repo.head_of("agent/work") != old_head
    return repo, tag, old_head


def test_rollback_rewinds_agent_work_and_clears_steps(store, doctrine):
    repo, tag, old_head = rollback_env(store, doctrine)
    head = service.rollback_to_checkpoint(store, "demo", tag)
    assert head == old_head
    assert repo.head_of("agent/work") == old_head
    run = store.load_run("demo")
    assert run.steps == []
    assert run.state is RunState.READY  # state untouched
    log_entries = service.run_log(store, "demo").read(step="rollback")
    assert any(tag in e["message"] for e in log_entries)


def test_rollback_refused_while_running(store, doctrine):
    repo, tag, _ = rollback_env(store, doctrine)
    run = store.load_run("demo")
    run.transition(RunState.RUNNING)
    store.save_run("demo", run)
    with pytest.raises(ValueError, match="cannot roll back while the run is RUNNING"):
        service.rollback_to_checkpoint(store, "demo", tag)


def test_rollback_unknown_tag(store, doctrine):
    rollback_env(store, doctrine)
    with pytest.raises(ValueError, match="unknown checkpoint"):
        service.rollback_to_checkpoint(store, "demo", "checkpoint/9999-nope")
