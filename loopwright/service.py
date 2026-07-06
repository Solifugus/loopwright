"""Project-level operations shared by the CLI and the web UI.

A project's editable design packet lives as plain files in
``projects/<name>/packet/``. The git repository only sees the packet when a
human explicitly approves it, which commits the drafts to ``design/main``.
"""

import shutil
from pathlib import Path

from loopwright.core.model import (
    TRANSITIONS,
    IllegalTransition,
    Project,
    ProjectStore,
    Run,
    RunState,
)
from loopwright.core.runlog import RunLog
from loopwright.gitctl.repo import GitError, ProjectRepo
from loopwright.notify.ntfy import Event

PACKET_FILES = ("DESIGN.md", "DEVPLAN.md", "TESTPLAN.md")

# Human-initiated run controls. Each maps to a target state; extra guards below
# keep "start" and "resume" meaning what they say even though both target RUNNING.
ACTION_TARGET = {
    "start": RunState.RUNNING,
    "pause": RunState.PAUSED,
    "resume": RunState.RUNNING,
    "stop": RunState.STOPPED,
}

_ACTION_FROM = {
    "start": frozenset({RunState.READY}),
    "pause": frozenset({RunState.RUNNING}),
    "resume": frozenset({RunState.PAUSED, RunState.PAUSED_LIMIT}),
    "stop": frozenset(
        {
            RunState.READY,
            RunState.RUNNING,
            RunState.PAUSED,
            RunState.PAUSED_LIMIT,
            RunState.REVIEW,
        }
    ),
}


def default_packet(name: str) -> dict[str, str]:
    """Placeholder packet; task 8.1 replaces these with doctrine templates."""
    return {
        "DESIGN.md": (
            f"# {name} — Design\n\n"
            "## Purpose\n\nWhat is being built, and why.\n\n"
            "## Requirements\n\n- ...\n\n"
            "## Acceptance Criteria\n\n- ...\n"
        ),
        "DEVPLAN.md": (
            f"# {name} — Development Plan\n\n"
            "Small tasks, each completable in one worker session.\n\n"
            "- [ ] 1. ...\n"
        ),
        "TESTPLAN.md": (
            f"# {name} — Test Plan\n\n"
            "How the product is verified, including deployment acceptance tests.\n\n"
            "- ...\n"
        ),
    }


def packet_dir(store: ProjectStore, name: str) -> Path:
    return store.project_dir(name) / "packet"


def create_project(store: ProjectStore, name: str) -> Project:
    """Create the store entry, packet drafts, and the bare git repository."""
    repo_path = store.project_dir(name) / "repo.git"
    project = store.create(name, str(repo_path))
    try:
        files = default_packet(name)
        pdir = packet_dir(store, name)
        pdir.mkdir()
        for filename, content in files.items():
            (pdir / filename).write_text(content)
        ProjectRepo.init(repo_path, files)
    except Exception:
        shutil.rmtree(store.project_dir(name), ignore_errors=True)
        raise
    return project


def load_packet(store: ProjectStore, name: str) -> dict[str, str]:
    pdir = packet_dir(store, name)
    return {
        filename: (pdir / filename).read_text() if (pdir / filename).is_file() else ""
        for filename in PACKET_FILES
    }


def save_packet(store: ProjectStore, name: str, files: dict[str, str]) -> None:
    pdir = packet_dir(store, name)
    pdir.mkdir(exist_ok=True)
    for filename in PACKET_FILES:
        if filename in files:
            (pdir / filename).write_text(files[filename])


def approve_packet(store: ProjectStore, name: str) -> str:
    """Commit the packet drafts to design/main; first approval moves DRAFT → READY."""
    project = store.load_project(name)
    run = store.load_run(name)
    if run.state not in (RunState.DRAFT, RunState.READY):
        raise ValueError(f"cannot approve the packet while the run is {run.state.value}")
    repo = ProjectRepo(project.repo_path)
    commit = repo.commit_packet(load_packet(store, name), message="Approve design packet")
    if run.state is RunState.DRAFT:
        run.transition(RunState.READY)
        store.save_run(name, run)
    return commit


def available_actions(run: Run) -> list[str]:
    """Run-control buttons that are legal from the run's current state."""
    return [
        action
        for action, sources in _ACTION_FROM.items()
        if run.state in sources and ACTION_TARGET[action] in TRANSITIONS[run.state]
    ]


def control_run(store: ProjectStore, name: str, action: str, notifier=None) -> Run:
    """Apply a human run-control action; raises IllegalTransition when not allowed."""
    if action not in ACTION_TARGET:
        raise ValueError(f"unknown run action {action!r}")
    run = store.load_run(name)
    if run.state not in _ACTION_FROM[action]:
        raise IllegalTransition(f"cannot {action} while the run is {run.state.value}")
    run.transition(ACTION_TARGET[action])
    store.save_run(name, run)
    if action == "start" and notifier is not None:
        notifier.notify(Event.RUN_STARTED, f"Run started for {name}", project=name)
    return run


def run_log(store: ProjectStore, name: str) -> RunLog:
    return RunLog(store.project_dir(name) / "logs")


def list_checkpoints(store: ProjectStore, name: str) -> list[str]:
    """Checkpoint tags for the project, or [] when the repo doesn't exist yet."""
    project = store.load_project(name)
    try:
        return ProjectRepo(project.repo_path).checkpoints()
    except GitError:
        return []
