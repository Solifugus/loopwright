"""Command execution inside VMs over SSH.

Key-based auth only (BatchMode) — if a password would be needed, the command
fails instead of hanging. ``DryRunSSH`` records commands and returns scripted
results for tests.
"""

import shlex
import subprocess
from dataclasses import dataclass


class SSHTimeout(Exception):
    """The remote command did not finish within the allotted time."""


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def repo_sync_command(remote_dir: str, repo_url: str, branch: str) -> str:
    """Shell command that clones the repo or updates an existing clone."""
    d, url, b = shlex.quote(remote_dir), shlex.quote(repo_url), shlex.quote(branch)
    return (
        f"if [ -d {d}/.git ]; then "
        f"git -C {d} fetch origin && git -C {d} checkout {b} && "
        f"git -C {d} pull --ff-only origin {b}; "
        f"else git clone --branch {b} {url} {d}; fi"
    )


class _RunnerBase:
    def run(self, command: str, timeout: int = 600) -> CommandResult:
        raise NotImplementedError

    def ensure_repo(self, remote_dir: str, repo_url: str, branch: str) -> CommandResult:
        return self.run(repo_sync_command(remote_dir, repo_url, branch))


class SSHRunner(_RunnerBase):
    def __init__(self, host: str, user: str, connect_timeout: int = 10):
        self.host = host
        self.user = user
        self.connect_timeout = connect_timeout

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"

    def argv(self, command: str) -> list[str]:
        return [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
            self.target,
            command,
        ]

    def run(self, command: str, timeout: int = 600) -> CommandResult:
        try:
            result = subprocess.run(
                self.argv(command),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise SSHTimeout(f"{self.target}: timed out after {timeout}s: {command}") from exc
        return CommandResult(result.returncode, result.stdout, result.stderr)


class DryRunSSH(_RunnerBase):
    """Records commands; returns queued results, or success by default."""

    def __init__(self, host: str = "dry-run", user: str = "nobody"):
        self.host = host
        self.user = user
        self.commands: list[str] = []
        self._queue: list[CommandResult] = []

    def queue(self, *results: CommandResult) -> None:
        self._queue.extend(results)

    def run(self, command: str, timeout: int = 600) -> CommandResult:
        self.commands.append(command)
        if self._queue:
            return self._queue.pop(0)
        return CommandResult(0, "", "")
