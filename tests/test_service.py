import subprocess

import pytest

from loopwright import service
from loopwright.core.model import ProjectStore, RunState
from loopwright.gitctl.repo import BRANCHES, GitError, ProjectRepo


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
