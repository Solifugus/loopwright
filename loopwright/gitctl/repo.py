"""Authoritative per-project git repositories.

The host owns one bare repository per project. All access goes through
subprocess ``git`` — no libraries, no surprises. The branch model comes from
the design doc:

* ``design/main`` — human-approved design packet
* ``agent/work`` — coding agent work
* ``agent/test`` — testing agent changes
* ``release/candidate`` — final candidate
* ``main`` — human-approved final branch

Checkpoints are annotated tags named ``checkpoint/NNNN-slug``.
"""

import re
import subprocess
import tempfile
from pathlib import Path

BRANCHES = ["design/main", "agent/work", "agent/test", "release/candidate", "main"]
DESIGN_BRANCH = "design/main"
WORK_BRANCH = "agent/work"

CHECKPOINT_RE = re.compile(r"^checkpoint/(\d{4})-([a-z0-9][a-z0-9-]*)$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Commits made by the orchestrator itself (packet commits) use this identity.
GIT_IDENTITY = ["-c", "user.name=Loopwright", "-c", "user.email=loopwright@localhost"]


class GitError(Exception):
    """A git command failed; the message carries the command and stderr."""


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *GIT_IDENTITY, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


class ProjectRepo:
    """A bare, host-owned project repository."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if not (self.path / "HEAD").is_file():
            raise GitError(f"not a git repository: {self.path}")

    @classmethod
    def init(cls, path: Path | str, packet_files: dict[str, str]) -> "ProjectRepo":
        """Create the bare repo, commit the packet to design/main, fan out branches."""
        path = Path(path)
        if path.exists():
            raise GitError(f"repo path already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["git", "init", "--bare", "-b", DESIGN_BRANCH, str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise GitError(f"git init --bare failed: {result.stderr.strip()}")

        repo = cls(path)
        repo.commit_packet(packet_files, message="Initial design packet")
        for branch in BRANCHES:
            if branch != DESIGN_BRANCH:
                _run_git(path, "branch", branch, DESIGN_BRANCH)
        return repo

    def commit_packet(self, files: dict[str, str], message: str = "Update design packet") -> str:
        """Commit files onto design/main via a throwaway clone; returns the commit hash."""
        if not files:
            raise ValueError("no files to commit")
        with tempfile.TemporaryDirectory(prefix="loopwright-packet-") as tmp:
            clone = Path(tmp) / "clone"
            result = subprocess.run(
                ["git", "clone", "--quiet", str(self.path), str(clone)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise GitError(f"git clone failed: {result.stderr.strip()}")
            _run_git(clone, "checkout", "-B", DESIGN_BRANCH)
            for rel_path, content in files.items():
                target = (clone / rel_path).resolve()
                if not target.is_relative_to(clone.resolve()):
                    raise ValueError(f"file path escapes repository: {rel_path!r}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            _run_git(clone, "add", "-A")
            _run_git(clone, "commit", "--allow-empty", "-m", message)
            _run_git(clone, "push", "--quiet", "origin", DESIGN_BRANCH)
            return _run_git(clone, "rev-parse", "HEAD").strip()

    def branches(self) -> list[str]:
        out = _run_git(self.path, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return sorted(line for line in out.splitlines() if line)

    def head_of(self, branch: str) -> str:
        return _run_git(self.path, "rev-parse", branch).strip()

    def checkpoints(self) -> list[str]:
        out = _run_git(self.path, "tag", "--list", "checkpoint/*")
        return sorted(line for line in out.splitlines() if line)

    def next_checkpoint_number(self) -> int:
        numbers = [
            int(m.group(1))
            for tag in self.checkpoints()
            if (m := CHECKPOINT_RE.match(tag))
        ]
        return max(numbers, default=0) + 1

    def tag_checkpoint(self, slug: str, ref: str = WORK_BRANCH, message: str | None = None) -> str:
        """Create the next checkpoint/NNNN-slug annotated tag on ref; returns the tag name."""
        if not SLUG_RE.match(slug):
            raise ValueError(
                f"invalid checkpoint slug {slug!r}: use lowercase letters, digits and '-'"
            )
        tag = f"checkpoint/{self.next_checkpoint_number():04d}-{slug}"
        _run_git(self.path, "tag", "-a", tag, "-m", message or f"Checkpoint: {slug}", ref)
        return tag
