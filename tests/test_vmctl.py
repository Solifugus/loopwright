import subprocess

import pytest

from loopwright.vmctl import vm as vm_mod
from loopwright.vmctl.ssh import (
    CommandResult,
    DryRunSSH,
    SSHRunner,
    SSHTimeout,
    repo_sync_command,
)
from loopwright.vmctl.vm import RUNNING, SHUT_OFF, DryRunVM, LibvirtVM, VMError

# --- DryRunVM ---


def test_dry_run_vm_lifecycle_and_call_recording():
    vm = DryRunVM("demo")
    assert vm.state() == SHUT_OFF
    vm.start()
    assert vm.is_running()
    vm.shutdown()
    assert vm.state() == SHUT_OFF
    assert [c[0] for c in vm.calls] == ["state", "start", "shutdown", "state"]


def test_dry_run_vm_snapshots():
    vm = DryRunVM("demo", snapshots=["base-os"])
    vm.snapshot_create("toolchain", "with tools")
    assert vm.snapshots() == ["base-os", "toolchain"]
    vm.snapshot_revert("base-os")
    with pytest.raises(VMError, match="no snapshot"):
        vm.snapshot_revert("missing")
    with pytest.raises(VMError, match="already exists"):
        vm.snapshot_create("base-os")


# --- LibvirtVM (virsh calls faked via monkeypatched subprocess) ---


class FakeVirsh:
    """Substitutes subprocess.run; scripted stdout per virsh subcommand."""

    def __init__(self, responses):
        self.responses = dict(responses)
        self.commands = []

    def __call__(self, argv, capture_output, text):
        assert argv[:3] == ["virsh", "-c", "qemu:///test"]
        sub = argv[3]
        self.commands.append(argv[3:])
        stdout, code = self.responses.get(sub, ("", 0))
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="boom" if code else "")


def make_vm(monkeypatch, responses):
    fake = FakeVirsh(responses)
    monkeypatch.setattr(vm_mod.subprocess, "run", fake)
    return LibvirtVM("demo", uri="qemu:///test"), fake


def test_libvirt_state_and_snapshot_listing(monkeypatch):
    vm, fake = make_vm(
        monkeypatch,
        {"domstate": ("running\n", 0), "snapshot-list": ("base-os\ntoolchain\n\n", 0)},
    )
    assert vm.state() == RUNNING
    assert vm.snapshots() == ["base-os", "toolchain"]


def test_libvirt_start_is_noop_when_running(monkeypatch):
    vm, fake = make_vm(monkeypatch, {"domstate": ("running\n", 0)})
    vm.start()
    assert [c[0] for c in fake.commands] == ["domstate"]


def test_libvirt_start_when_shut_off(monkeypatch):
    vm, fake = make_vm(monkeypatch, {"domstate": ("shut off\n", 0)})
    vm.start()
    assert [c[0] for c in fake.commands] == ["domstate", "start"]


def test_libvirt_shutdown_noop_when_off(monkeypatch):
    vm, fake = make_vm(monkeypatch, {"domstate": ("shut off\n", 0)})
    vm.shutdown()
    assert [c[0] for c in fake.commands] == ["domstate"]


def test_libvirt_error_carries_stderr(monkeypatch):
    vm, fake = make_vm(monkeypatch, {"snapshot-revert": ("", 1)})
    with pytest.raises(VMError, match="boom"):
        vm.snapshot_revert("nope")


def test_libvirt_snapshot_create_args(monkeypatch):
    vm, fake = make_vm(monkeypatch, {})
    vm.snapshot_create("base-os", "fresh install")
    assert fake.commands == [["snapshot-create-as", "demo", "base-os", "fresh install"]]


# --- SSH ---


def test_ssh_argv_uses_batch_mode():
    runner = SSHRunner("192.0.2.1", "master", connect_timeout=7)
    argv = runner.argv("echo hi")
    assert argv[0] == "ssh"
    assert "BatchMode=yes" in argv
    assert "ConnectTimeout=7" in argv
    assert argv[-2:] == ["master@192.0.2.1", "echo hi"]


def test_ssh_run_captures_result(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 3, stdout="out", stderr="err")

    monkeypatch.setattr("loopwright.vmctl.ssh.subprocess.run", fake_run)
    result = SSHRunner("h", "u").run("false")
    assert (result.exit_code, result.stdout, result.stderr) == (3, "out", "err")
    assert not result.ok


def test_ssh_timeout_raises(monkeypatch):
    def fake_run(argv, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(argv, timeout)

    monkeypatch.setattr("loopwright.vmctl.ssh.subprocess.run", fake_run)
    with pytest.raises(SSHTimeout, match="timed out after 5s"):
        SSHRunner("h", "u").run("sleep 99", timeout=5)


def test_repo_sync_command_clones_or_pulls():
    cmd = repo_sync_command("/home/master/work", "ssh://host/repo.git", "agent/work")
    assert "git clone --branch agent/work" in cmd
    assert "pull --ff-only origin agent/work" in cmd
    assert cmd.count("/home/master/work") == 5


def test_repo_sync_command_quotes_hostile_input():
    cmd = repo_sync_command("/tmp/x; rm -rf /", "url", "branch")
    assert "; rm -rf /'" in cmd  # quoted, not executable


def test_dry_run_ssh_records_and_queues():
    runner = DryRunSSH()
    runner.queue(CommandResult(1, "", "denied"))
    first = runner.run("whoami")
    second = runner.run("echo again")
    assert not first.ok and first.stderr == "denied"
    assert second.ok
    assert runner.commands == ["whoami", "echo again"]


def test_dry_run_ensure_repo_composes_sync_command():
    runner = DryRunSSH()
    runner.ensure_repo("/work", "ssh://h/r.git", "agent/work")
    assert runner.commands == [repo_sync_command("/work", "ssh://h/r.git", "agent/work")]
