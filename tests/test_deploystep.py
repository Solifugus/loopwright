import subprocess

import pytest

from loopwright.core.model import ProjectStore, RunState
from loopwright.core.runlog import RunLog
from loopwright.gitctl.repo import ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier
from loopwright.orchestrator.deploystep import DeploymentVMStep
from loopwright.orchestrator.engine import Engine, Step, StepContext, StepFailed
from loopwright.vmctl.ssh import CommandResult, DryRunSSH
from loopwright.vmctl.vm import RUNNING, SHUT_OFF, DryRunVM

PACKET = {
    "DESIGN.md": "# demo design\n",
    "DEVPLAN.md": "- [x] 1. done\n",
    "TESTPLAN.md": "# tests\n",
    "scripts/deploy.sh": "#!/bin/bash\necho deployed\n",
    "scripts/acceptance.sh": "#!/bin/bash\necho accepted\n",
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
    return {
        "store": store,
        "repo": repo,
        "vm_bare": vm_bare,
        "ctx": ctx,
        "notifier": notifier,
    }


def make_step(env, ssh=None, vm=None, **kwargs):
    kwargs.setdefault("clean_snapshot", "clean-test")
    kwargs.setdefault("ssh_wait_retries", 3)
    kwargs.setdefault("ssh_wait_delay", 0)
    return DeploymentVMStep(
        project="demo",
        repo=env["repo"],
        vm=vm or DryRunVM("loopwright_test", state=SHUT_OFF, snapshots=["clean-test"]),
        ssh=ssh or DryRunSSH(),
        vm_repo_url=str(env["vm_bare"]),
        remote_repo_dir="loopwright/demo.git",
        remote_work_dir="loopwright/demo",
        **kwargs,
    )


def test_success_reverts_deploys_and_notifies(env):
    vm = DryRunVM("loopwright_test", state=RUNNING, snapshots=["clean-test"])
    ssh = DryRunSSH()
    detail = make_step(env, ssh=ssh, vm=vm)(env["ctx"])

    # running VM: shut down, revert to clean snapshot, start again
    assert vm.calls[:3] == [("shutdown",), ("snapshot_revert", "clean-test"), ("start",)]
    scripts = [c for c in ssh.commands if "bash scripts/" in c]
    assert "bash scripts/deploy.sh" in scripts[0]
    assert "bash scripts/acceptance.sh" in scripts[1]
    assert detail["commit"] == env["repo"].head_of("agent/work")
    events = [event for event, _, _ in env["notifier"].events]
    assert events == [Event.DEPLOYMENT_PASSED]


def test_shut_off_vm_skips_shutdown(env):
    vm = DryRunVM("loopwright_test", state=SHUT_OFF, snapshots=["clean-test"])
    make_step(env, vm=vm)(env["ctx"])
    assert ("shutdown",) not in vm.calls
    assert ("snapshot_revert", "clean-test") in vm.calls


def test_missing_deploy_script_fails_before_touching_vm(env, tmp_path):
    bare_no_scripts = tmp_path / "noscripts.git"
    repo = ProjectRepo.init(bare_no_scripts, {"DESIGN.md": "# d\n"})
    vm = DryRunVM("loopwright_test", state=SHUT_OFF, snapshots=["clean-test"])
    step = make_step(env, vm=vm)
    step.repo = repo
    with pytest.raises(StepFailed, match="no scripts/deploy.sh"):
        step(env["ctx"])
    assert vm.calls == []  # VM untouched


def test_deploy_script_failure(env):
    ssh = DryRunSSH()
    ssh.queue(
        CommandResult(0, "", ""),  # ssh reachability probe
        CommandResult(0, "", ""),  # git init
        CommandResult(0, "", ""),  # clone
        CommandResult(7, "", "missing dependency: cowsay"),  # deploy.sh
    )
    with pytest.raises(StepFailed, match="scripts/deploy.sh failed with exit code 7"):
        make_step(env, ssh=ssh)(env["ctx"])
    assert env["notifier"].events == []


def test_acceptance_failure_after_deploy_ok(env):
    ssh = DryRunSSH()
    ssh.queue(
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, "deployed", ""),  # deploy.sh ok
        CommandResult(1, "", "service not responding"),  # acceptance.sh fails
    )
    with pytest.raises(StepFailed, match="scripts/acceptance.sh failed"):
        make_step(env, ssh=ssh)(env["ctx"])


def test_no_snapshot_configured_warns_and_proceeds(env):
    vm = DryRunVM("loopwright_test", state=SHUT_OFF)
    make_step(env, vm=vm, clean_snapshot=None)(env["ctx"])
    assert ("snapshot_revert", "clean-test") not in vm.calls
    warnings = [e for e in env["ctx"].log.read(level="warning")]
    assert any("no clean snapshot" in e["message"] for e in warnings)


def test_unknown_snapshot_is_controlled_failure(env):
    vm = DryRunVM("loopwright_test", state=SHUT_OFF, snapshots=[])
    with pytest.raises(StepFailed, match="could not revert"):
        make_step(env, vm=vm)(env["ctx"])


def test_waits_for_ssh_to_come_up(env):
    ssh = DryRunSSH()
    ssh.queue(
        CommandResult(255, "", "Connection refused"),  # first probe fails
        CommandResult(255, "", "Connection refused"),  # second probe fails
        # third probe uses the default success result
    )
    detail = make_step(env, ssh=ssh)(env["ctx"])
    assert detail["commit"]
    probes = [c for c in ssh.commands if c == "true"]
    assert len(probes) == 3


def test_ssh_never_up_fails(env):
    ssh = DryRunSSH()
    ssh.queue(*[CommandResult(255, "", "no route") for _ in range(3)])
    with pytest.raises(StepFailed, match="did not become reachable"):
        make_step(env, ssh=ssh)(env["ctx"])


def test_engine_run_with_deploy_step(env):
    step_impl = make_step(env)
    outcome = Engine(
        env["store"], "demo", [Step(step_impl.name, step_impl)], notifier=env["notifier"]
    ).run()
    assert outcome == "completed"
    run = env["store"].load_run("demo")
    assert run.state is RunState.REVIEW
    assert run.step_result("deploy-test")["status"] == "ok"
