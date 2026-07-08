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
from loopwright.gitctl.repo import WORK_BRANCH, GitError, ProjectRepo
from loopwright.notify.ntfy import Event

PACKET_FILES = ("DESIGN.md", "DEVPLAN.md", "TESTPLAN.md")
DOCTRINE_FILES = ("PRINCIPLES.md", "AGENT_RULES.md")
PROJECT_PLACEHOLDER = "{{PROJECT}}"

# Minimal built-in doctrine used when no doctrine_dir is configured. The
# canonical, fuller versions live in the loopwright-doctrine repo.
DEFAULT_DOCTRINE = {
    "PRINCIPLES.md": (
        "# Engineering Principles\n\n"
        "1. Prefer simplicity and boring dependencies.\n"
        "2. Be explicit; minimize magic.\n"
        "3. Automate everything repeatable.\n"
        "4. Make failures visible.\n"
        "5. Document reality, not aspiration.\n"
        "6. Favor reversibility.\n"
        "7. Deployment must be reproducible from a clean machine via\n"
        "   scripts/deploy.sh, proven by scripts/acceptance.sh.\n"
        "8. Tests accompany logic; the suite passes before every commit.\n"
    ),
    "AGENT_RULES.md": (
        "# Agent Rules\n\n"
        "- Work only on agent/work; one DEVPLAN task per session; tests must\n"
        "  pass; tick the checkbox and push, or the work doesn't exist.\n"
        "- Never modify DESIGN.md, PRINCIPLES.md, or AGENT_RULES.md.\n"
        "- Never: deploy to production, spend money, touch real accounts or\n"
        "  secrets, modify files outside the working copy, contact external\n"
        "  people or services, or accept legal terms.\n"
        "- If a task seems to require breaking a rule, stop and report.\n"
    ),
}

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
            "The project MUST provide these scripts so the Orchestrator can verify\n"
            "it independently — a checkpoint is only tagged once they pass:\n\n"
            "- `scripts/test.sh` — runs the full suite from a clean clone; exits\n"
            "  nonzero on any failure.\n"
            "- `scripts/deploy.sh` — installs the product on a bare machine.\n"
            "- `scripts/acceptance.sh` — verifies the deployed product works.\n\n"
            "- ...\n"
        ),
    }


def packet_dir(store: ProjectStore, name: str) -> Path:
    return store.project_dir(name) / "packet"


def load_packet_templates(name: str, doctrine_dir: Path | None = None) -> dict[str, str]:
    """Doctrine templates with {{PROJECT}} substituted; built-ins fill any gaps."""
    files = default_packet(name)
    if doctrine_dir is not None:
        template_dir = Path(doctrine_dir).expanduser() / "templates"
        for filename in PACKET_FILES:
            path = template_dir / filename
            if path.is_file():
                files[filename] = path.read_text().replace(PROJECT_PLACEHOLDER, name)
    return files


def load_doctrine_files(doctrine_dir: Path | None = None) -> dict[str, str]:
    files = dict(DEFAULT_DOCTRINE)
    if doctrine_dir is not None:
        base = Path(doctrine_dir).expanduser()
        for filename in DOCTRINE_FILES:
            path = base / filename
            if path.is_file():
                files[filename] = path.read_text()
    return files


def create_project(
    store: ProjectStore, name: str, doctrine_dir: Path | None = None
) -> Project:
    """Create the store entry, packet drafts, and the bare git repository.

    The initial design/main commit carries the doctrine (PRINCIPLES.md,
    AGENT_RULES.md) alongside the packet, so every worker clone includes the
    rules it must follow.
    """
    repo_path = store.project_dir(name) / "repo.git"
    project = store.create(name, str(repo_path))
    try:
        packet_files = load_packet_templates(name, doctrine_dir)
        pdir = packet_dir(store, name)
        pdir.mkdir()
        for filename, content in packet_files.items():
            (pdir / filename).write_text(content)
        ProjectRepo.init(repo_path, {**load_doctrine_files(doctrine_dir), **packet_files})
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


# States in which agent/work may be rewound: never while agents are working.
ROLLBACK_STATES = frozenset(
    {RunState.READY, RunState.REVIEW, RunState.PAUSED, RunState.PAUSED_LIMIT}
)


def rollback_to_checkpoint(store: ProjectStore, name: str, tag: str) -> str:
    """Rewind agent/work to a checkpoint tag; clears recorded step results."""
    project = store.load_project(name)
    run = store.load_run(name)
    if run.state not in ROLLBACK_STATES:
        raise ValueError(f"cannot roll back while the run is {run.state.value}")
    repo = ProjectRepo(project.repo_path)
    if tag not in repo.checkpoints():
        raise ValueError(f"unknown checkpoint {tag!r}")
    repo.reset_branch(WORK_BRANCH, tag)
    run.steps = []
    store.save_run(name, run)
    head = repo.head_of(WORK_BRANCH)
    run_log(store, name).log("rollback", f"agent/work rewound to {tag} ({head[:10]})")
    return head


def promote_candidate(store: ProjectStore, name: str) -> str:
    """Point release/candidate at agent/work and commit FINAL_REPORT.md onto it."""
    from loopwright.orchestrator.report import generate_report

    project = store.load_project(name)
    run = store.load_run(name)
    if run.state is not RunState.REVIEW:
        raise ValueError(f"cannot promote a candidate while the run is {run.state.value}")
    repo = ProjectRepo(project.repo_path)
    repo.reset_branch("release/candidate", WORK_BRANCH)
    report = generate_report(store, name)
    commit = repo.commit_files(
        {"FINAL_REPORT.md": report}, branch="release/candidate", message="Final report"
    )
    run_log(store, name).log(
        "release", f"candidate promoted with final report ({commit[:10]})"
    )
    return report


def release_status(store: ProjectStore, name: str) -> dict:
    """Whether a release candidate is awaiting human approval."""
    project = store.load_project(name)
    try:
        repo = ProjectRepo(project.repo_path)
        candidate = repo.head_of("release/candidate")
        main = repo.head_of("main")
    except GitError:
        return {"pending": False}
    return {"pending": candidate != main, "candidate": candidate, "main": main}


def approve_release(store: ProjectStore, name: str) -> str:
    """Human approval: fast-forward main to release/candidate, run → DONE."""
    project = store.load_project(name)
    run = store.load_run(name)
    if run.state is not RunState.REVIEW:
        raise ValueError(f"cannot approve a release while the run is {run.state.value}")
    if not release_status(store, name)["pending"]:
        raise ValueError("no release candidate is awaiting approval")
    repo = ProjectRepo(project.repo_path)
    repo.reset_branch("main", "release/candidate")
    run.transition(RunState.DONE)
    store.save_run(name, run)
    head = repo.head_of("main")
    run_log(store, name).log("release", f"release approved; main is now {head[:10]}")
    return head


def run_log(store: ProjectStore, name: str) -> RunLog:
    return RunLog(store.project_dir(name) / "logs")


def list_checkpoints(store: ProjectStore, name: str) -> list[str]:
    """Checkpoint tags for the project, or [] when the repo doesn't exist yet."""
    project = store.load_project(name)
    try:
        return ProjectRepo(project.repo_path).checkpoints()
    except GitError:
        return []
