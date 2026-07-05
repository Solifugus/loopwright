"""Project and Run domain models with file-based persistence.

A project lives in ``<store root>/<name>/`` as:

* ``project.yaml`` — immutable-ish metadata
* ``run.json`` — the current run's state machine and transition history
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class IllegalTransition(Exception):
    """Raised when a run is asked to move to a state it cannot reach."""


class RunState(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    PAUSED_LIMIT = "PAUSED_LIMIT"
    REVIEW = "REVIEW"
    DONE = "DONE"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


# state -> states it may legally move to
TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.DRAFT: frozenset({RunState.READY}),
    RunState.READY: frozenset({RunState.RUNNING, RunState.STOPPED}),
    RunState.RUNNING: frozenset(
        {
            RunState.PAUSED,
            RunState.PAUSED_LIMIT,
            RunState.REVIEW,
            RunState.FAILED,
            RunState.STOPPED,
        }
    ),
    RunState.PAUSED: frozenset({RunState.RUNNING, RunState.STOPPED}),
    RunState.PAUSED_LIMIT: frozenset({RunState.RUNNING, RunState.STOPPED}),
    RunState.REVIEW: frozenset(
        {RunState.RUNNING, RunState.DONE, RunState.FAILED, RunState.STOPPED}
    ),
    RunState.DONE: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.STOPPED: frozenset(),
}

TERMINAL_STATES = frozenset(s for s, targets in TRANSITIONS.items() if not targets)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Project:
    name: str
    repo_path: str
    created: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not NAME_RE.match(self.name):
            raise ValueError(
                f"invalid project name {self.name!r}: use lowercase letters, "
                "digits, '-' and '_', starting with a letter or digit"
            )

    def to_dict(self) -> dict:
        return {"name": self.name, "repo_path": self.repo_path, "created": self.created}

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(name=data["name"], repo_path=data["repo_path"], created=data["created"])


@dataclass
class Run:
    state: RunState = RunState.DRAFT
    history: list[dict] = field(default_factory=list)

    def transition(self, new_state: RunState) -> None:
        if new_state not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"cannot move from {self.state.value} to {new_state.value}")
        self.history.append({"from": self.state.value, "to": new_state.value, "at": _now()})
        self.state = new_state

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict:
        return {"state": self.state.value, "history": self.history}

    @classmethod
    def from_dict(cls, data: dict) -> "Run":
        return cls(state=RunState(data["state"]), history=list(data["history"]))


class ProjectStore:
    """Reads and writes projects under a root directory."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def project_dir(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise ValueError(f"invalid project name: {name!r}")
        return self.root / name

    def create(self, name: str, repo_path: str) -> Project:
        project = Project(name=name, repo_path=repo_path)
        pdir = self.project_dir(name)
        if pdir.exists():
            raise FileExistsError(f"project {name!r} already exists at {pdir}")
        pdir.mkdir(parents=True)
        self.save_project(project)
        self.save_run(name, Run())
        return project

    def save_project(self, project: Project) -> None:
        path = self.project_dir(project.name) / "project.yaml"
        path.write_text(yaml.safe_dump(project.to_dict(), sort_keys=False))

    def load_project(self, name: str) -> Project:
        path = self.project_dir(name) / "project.yaml"
        return Project.from_dict(yaml.safe_load(path.read_text()))

    def save_run(self, name: str, run: Run) -> None:
        path = self.project_dir(name) / "run.json"
        path.write_text(json.dumps(run.to_dict(), indent=2))

    def load_run(self, name: str) -> Run:
        path = self.project_dir(name) / "run.json"
        return Run.from_dict(json.loads(path.read_text()))

    def list_projects(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            p.name for p in self.root.iterdir() if (p / "project.yaml").is_file()
        )
