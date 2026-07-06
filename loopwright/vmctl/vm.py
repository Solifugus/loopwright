"""VM control via virsh.

``LibvirtVM`` shells out to ``virsh`` for a named libvirt domain. ``DryRunVM``
is a stand-in with the same surface that records every call and simulates
state, so orchestrator logic is testable without real VMs.
"""

import subprocess
import time

RUNNING = "running"
SHUT_OFF = "shut off"


class VMError(Exception):
    """A VM operation failed; message carries the virsh command and stderr."""


def _virsh(uri: str, *args: str) -> str:
    result = subprocess.run(
        ["virsh", "-c", uri, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise VMError(f"virsh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


class LibvirtVM:
    def __init__(self, domain: str, uri: str = "qemu:///system"):
        self.domain = domain
        self.uri = uri

    def state(self) -> str:
        return _virsh(self.uri, "domstate", self.domain).strip()

    def is_running(self) -> bool:
        return self.state() == RUNNING

    def start(self) -> None:
        """Start the domain; a no-op if it is already running."""
        if self.is_running():
            return
        _virsh(self.uri, "start", self.domain)

    def shutdown(self, wait_timeout: int = 180, poll_interval: float = 3.0) -> None:
        """Gracefully shut down and wait; a no-op if already shut off."""
        if self.state() == SHUT_OFF:
            return
        _virsh(self.uri, "shutdown", self.domain)
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if self.state() == SHUT_OFF:
                return
            time.sleep(poll_interval)
        raise VMError(f"{self.domain}: did not shut off within {wait_timeout}s")

    def destroy(self) -> None:
        """Force off (pull the plug); a no-op if already shut off."""
        if self.state() == SHUT_OFF:
            return
        _virsh(self.uri, "destroy", self.domain)

    def snapshot_create(self, name: str, description: str = "") -> None:
        _virsh(self.uri, "snapshot-create-as", self.domain, name, description)

    def snapshot_revert(self, name: str) -> None:
        _virsh(self.uri, "snapshot-revert", self.domain, name)

    def snapshots(self) -> list[str]:
        out = _virsh(self.uri, "snapshot-list", self.domain, "--name")
        return [line for line in out.splitlines() if line.strip()]


class DryRunVM:
    """In-memory fake with the same surface as LibvirtVM. Records all calls."""

    def __init__(self, domain: str, state: str = SHUT_OFF, snapshots: list[str] | None = None):
        self.domain = domain
        self._state = state
        self._snapshots: list[str] = list(snapshots or [])
        self.calls: list[tuple] = []

    def state(self) -> str:
        self.calls.append(("state",))
        return self._state

    def is_running(self) -> bool:
        return self._state == RUNNING

    def start(self) -> None:
        self.calls.append(("start",))
        self._state = RUNNING

    def shutdown(self, wait_timeout: int = 180, poll_interval: float = 3.0) -> None:
        self.calls.append(("shutdown",))
        self._state = SHUT_OFF

    def destroy(self) -> None:
        self.calls.append(("destroy",))
        self._state = SHUT_OFF

    def snapshot_create(self, name: str, description: str = "") -> None:
        self.calls.append(("snapshot_create", name, description))
        if name in self._snapshots:
            raise VMError(f"{self.domain}: snapshot {name!r} already exists")
        self._snapshots.append(name)

    def snapshot_revert(self, name: str) -> None:
        self.calls.append(("snapshot_revert", name))
        if name not in self._snapshots:
            raise VMError(f"{self.domain}: no snapshot named {name!r}")

    def snapshots(self) -> list[str]:
        self.calls.append(("snapshots",))
        return list(self._snapshots)
