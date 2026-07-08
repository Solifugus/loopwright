"""Independent verification step: a checkpoint means the suite was re-run (9.2)."""

import subprocess

import pytest

from loopwright.core.model import ProjectStore, RunState
from loopwright.core.runlog import RunLog
from loopwright.gitctl.repo import ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier
from loopwright.orchestrator.engine import Engine, Step, StepContext, StepFailed
from loopwright.orchestrator.verifystep import VerifyTestsStep
from loopwright.vmctl.ssh import CommandResult, DryRunSSH
from loopwright.vmctl.vm import RUNNING, SHUT_OFF, DryRunVM

PACKET = {
    "DESIGN.md": "# demo design\n",
    "DEVPLAN.md": "- [x] 1. done\n",
    "TESTPLAN.md": "# tests\n",
    "scripts/test.sh": "#!/bin/bash\necho all tests passed\n",
}


@pytest.fixture
def env(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    store.create("demo", str(tmp_path / "host.git"))
    run = store.load_run("demo")
    run.transition(RunState.READY)
    store.save_run("demo", run)

    repo = ProjectRepo.init(tmp_path / "host.git", PACKET)
    vm_bare = tmp_path / "vm.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "design/main", str(vm_bare)],
        capture_output=True,
        check=True,
    )
    notifier = NullNotifier()
    ctx = StepContext(
        store=store, project="demo", log=RunLog(tmp_path / "logs"), notifier=notifier
    )
    return {"store": store, "repo": repo, "vm_bare": vm_bare, "ctx": ctx, "notifier": notifier}


def make_step(env, ssh=None, vm=None, **kwargs):
    return VerifyTestsStep(
        project="demo",
        repo=env["repo"],
        vm=vm or DryRunVM("LoopWright_Dev", state=RUNNING),
        ssh=ssh or DryRunSSH(),
        vm_repo_url=str(env["vm_bare"]),
        remote_repo_dir="loopwright/demo.git",
        remote_verify_dir="loopwright/demo-verify",
        **kwargs,
    )


def test_independent_pass_tags_checkpoint_and_notifies(env):
    ssh = DryRunSSH()
    detail = make_step(env, ssh=ssh)(env["ctx"])

    # a fresh clone into the throwaway verify dir, then scripts/test.sh
    assert any("clone -q -b agent/work" in c and "demo-verify" in c for c in ssh.commands)
    assert any("bash scripts/test.sh" in c for c in ssh.commands)

    assert detail["checkpoint"] == "checkpoint/0001-worker-session"
    assert env["repo"].checkpoints() == ["checkpoint/0001-worker-session"]
    events = [event for event, _, _ in env["notifier"].events]
    assert events == [Event.CHECKPOINT_PASSED]


def test_failed_verification_tags_no_checkpoint(env):
    ssh = DryRunSSH()
    ssh.queue(
        CommandResult(0, "", ""),  # git init bare
        CommandResult(0, "", ""),  # fresh clone
        CommandResult(1, "", "2 tests failed"),  # scripts/test.sh
    )
    with pytest.raises(StepFailed, match="independent verification failed"):
        make_step(env, ssh=ssh)(env["ctx"])

    assert env["repo"].checkpoints() == []  # no tag when verification fails
    assert env["notifier"].events == []  # and no CHECKPOINT_PASSED


def test_missing_test_script_fails_before_touching_vm(env, tmp_path):
    bare = tmp_path / "noscript.git"
    repo = ProjectRepo.init(bare, {"DESIGN.md": "# d\n"})  # no scripts/test.sh
    vm = DryRunVM("LoopWright_Dev", state=SHUT_OFF)
    step = make_step(env, vm=vm)
    step.repo = repo
    with pytest.raises(StepFailed, match="no scripts/test.sh"):
        step(env["ctx"])
    assert vm.calls == []  # VM untouched
    assert repo.checkpoints() == []


def test_starts_dev_vm_if_shut_off(env):
    vm = DryRunVM("LoopWright_Dev", state=SHUT_OFF)
    make_step(env, vm=vm)(env["ctx"])
    assert vm.is_running()
    assert env["repo"].checkpoints() == ["checkpoint/0001-worker-session"]


def test_engine_records_verify_step(env):
    step_impl = make_step(env)
    outcome = Engine(
        env["store"], "demo", [Step(step_impl.name, step_impl)], notifier=env["notifier"]
    ).run()
    assert outcome == "completed"
    run = env["store"].load_run("demo")
    assert run.step_result("verify-tests")["status"] == "ok"
    assert run.step_result("verify-tests")["detail"]["checkpoint"] == "checkpoint/0001-worker-session"
