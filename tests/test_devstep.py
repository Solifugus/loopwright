import subprocess

import pytest

from loopwright.core.model import ProjectStore, RunState
from loopwright.core.runlog import RunLog
from loopwright.gitctl.repo import ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier
from loopwright.orchestrator.devstep import (
    ALL_DONE_MARKER,
    DeveloperVMStep,
    compose_prompt,
    is_usage_limit,
)
from loopwright.orchestrator.engine import (
    PAUSED_LIMIT,
    Engine,
    Step,
    StepContext,
    StepFailed,
    UsageLimitReached,
)
from loopwright.vmctl.ssh import CommandResult, DryRunSSH
from loopwright.vmctl.vm import SHUT_OFF, DryRunVM

PACKET = {
    "DESIGN.md": "# demo design\n",
    "DEVPLAN.md": "- [ ] 1. say hello\n",
    "TESTPLAN.md": "# tests\n",
}


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


class ScriptedWorkerSSH(DryRunSSH):
    """Dry-run SSH that really commits to the 'VM' bare repo when the worker runs."""

    def __init__(self, vm_bare, workspace, commit_on_worker=True):
        super().__init__()
        self.vm_bare = vm_bare
        self.workspace = workspace
        self.commit_on_worker = commit_on_worker

    def run(self, command, timeout=600):
        result = super().run(command, timeout=timeout)
        if "claude" in command and self.commit_on_worker and result.ok:
            clone = self.workspace / "worker-clone"
            subprocess.run(
                ["git", "clone", "-q", "-b", "agent/work", str(self.vm_bare), str(clone)],
                capture_output=True,
                check=True,
            )
            (clone / "hello.py").write_text("print('hello')\n")
            git(clone, "-c", "user.name=Worker", "-c", "user.email=w@vm", "add", "-A")
            git(
                clone,
                "-c", "user.name=Worker", "-c", "user.email=w@vm",
                "commit", "-q", "-m", "Task 1: say hello",
            )
            git(clone, "push", "-q", "origin", "agent/work")
            subprocess.run(["rm", "-rf", str(clone)], check=True)
        return result


@pytest.fixture
def env(tmp_path):
    """Host repo, VM bare repo, store with a READY project, and a step context."""
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
        store=store,
        project="demo",
        log=RunLog(tmp_path / "logs"),
        notifier=notifier,
    )
    return {
        "tmp": tmp_path,
        "store": store,
        "repo": repo,
        "vm_bare": vm_bare,
        "ctx": ctx,
        "notifier": notifier,
    }


def make_step(env, ssh, vm=None, **kwargs):
    return DeveloperVMStep(
        project="demo",
        repo=env["repo"],
        vm=vm or DryRunVM("LoopWright_Dev", state=SHUT_OFF),
        ssh=ssh,
        vm_repo_url=str(env["vm_bare"]),
        remote_repo_dir="loopwright/demo.git",
        remote_work_dir="loopwright/demo",
        **kwargs,
    )


def test_success_path_accepts_push_without_tagging(env):
    # 9.2: the dev step only *accepts* a worker push (fetch-gate passed). The
    # checkpoint tag is earned later by the independent verify-tests step, so
    # dev-code must NOT tag or emit CHECKPOINT_PASSED itself.
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"])
    vm = DryRunVM("LoopWright_Dev", state=SHUT_OFF)
    step = make_step(env, ssh, vm=vm)

    detail = step(env["ctx"])

    assert vm.is_running()  # step started the VM
    assert detail["tasks_remaining"] is True
    assert "checkpoint" not in detail  # tagging moved to verify-tests
    assert env["repo"].checkpoints() == []
    events = [event for event, _, _ in env["notifier"].events]
    assert Event.CHECKPOINT_PASSED not in events
    # the worker's commit actually landed on the host's agent/work
    assert detail["commit"] == env["repo"].head_of("agent/work")


def test_worker_command_points_at_doctrine_without_restating_rules(env):
    # 9.3: the prompt is mechanics-only and points at the doctrine; it must not
    # duplicate any rule text — the doctrine repo is the single source of truth.
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"])
    make_step(env, ssh)(env["ctx"])
    worker_cmd = next(c for c in ssh.commands if "claude" in c)
    assert "cd loopwright/demo" in worker_cmd
    assert "--dangerously-skip-permissions" in worker_cmd
    assert "docs/agent/AGENT_RULES.md" in worker_cmd
    assert "docs/agent/PRINCIPLES.md" in worker_cmd
    # no rule text restated from the doctrine files
    assert "Never modify" not in worker_cmd
    assert "spending" not in worker_cmd


def test_working_copy_merges_design_main(env):
    """Re-approved packets must reach the worker: clone merges origin/design/main."""
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"])
    make_step(env, ssh)(env["ctx"])
    clone_cmd = next(c for c in ssh.commands if "git clone" in c)
    assert "merge -q --no-edit origin/design/main" in clone_cmd


def test_usage_limit_raises(env):
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    ssh.queue(
        CommandResult(0, "", ""),  # git init
        CommandResult(0, "", ""),  # clone
        CommandResult(1, "Claude AI usage limit reached|1751800000", ""),  # worker
    )
    with pytest.raises(UsageLimitReached):
        make_step(env, ssh)(env["ctx"])


def test_worker_failure_raises_stepfailed(env):
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    ssh.queue(
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(2, "", "claude: command not found"),
    )
    with pytest.raises(StepFailed, match="exited with 2"):
        make_step(env, ssh)(env["ctx"])


def test_no_commits_without_marker_fails(env):
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    with pytest.raises(StepFailed, match="no new commits"):
        make_step(env, ssh)(env["ctx"])


def test_all_done_marker_is_successful_noop(env):
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    ssh.queue(
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(0, f"Nothing to do.\n{ALL_DONE_MARKER}\n", ""),
    )
    detail = make_step(env, ssh)(env["ctx"])
    assert detail["tasks_remaining"] is False
    assert env["repo"].checkpoints() == []  # no checkpoint for a no-op


def test_engine_parks_run_on_usage_limit(env):
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    ssh.queue(
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(1, "You have hit your rate limit for today", ""),
    )
    step_impl = make_step(env, ssh)
    engine = Engine(
        env["store"], "demo", [Step(step_impl.name, step_impl)], notifier=env["notifier"]
    )

    assert engine.run() == PAUSED_LIMIT
    run = env["store"].load_run("demo")
    assert run.state is RunState.PAUSED_LIMIT
    assert run.step_result("dev-code")["status"] == "limit"
    events = [event for event, _, _ in env["notifier"].events]
    assert Event.LIMIT_REACHED in events


def test_engine_resume_after_limit_reruns_step(env):
    # park it first
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    ssh.queue(
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(1, "usage limit reached", ""),
    )
    step_impl = make_step(env, ssh)
    Engine(env["store"], "demo", [Step(step_impl.name, step_impl)]).run()

    # human resumes; this time the worker succeeds
    run = env["store"].load_run("demo")
    run.transition(RunState.RUNNING)
    env["store"].save_run("demo", run)

    good_ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"])
    good_impl = make_step(env, good_ssh)
    outcome = Engine(env["store"], "demo", [Step(good_impl.name, good_impl)]).run()

    assert outcome == "completed"
    run = env["store"].load_run("demo")
    assert run.state is RunState.REVIEW
    assert run.step_result("dev-code")["status"] == "ok"
    # dev-code accepts the push but no longer tags a checkpoint (moved to 9.2's
    # verify-tests step); the commit still landed on agent/work.
    assert "checkpoint" not in run.step_result("dev-code")["detail"]
    assert env["repo"].head_of("agent/work") == run.step_result("dev-code")["detail"]["commit"]


def test_engine_fails_run_on_worker_failure(env):
    ssh = ScriptedWorkerSSH(env["vm_bare"], env["tmp"], commit_on_worker=False)
    ssh.queue(
        CommandResult(0, "", ""),
        CommandResult(0, "", ""),
        CommandResult(1, "", "boom"),
    )
    step_impl = make_step(env, ssh)
    outcome = Engine(env["store"], "demo", [Step(step_impl.name, step_impl)]).run()
    assert outcome == "failed"
    assert env["store"].load_run("demo").state is RunState.REVIEW


def test_compose_prompt_mentions_project_and_marker():
    prompt = compose_prompt("myproj")
    assert "'myproj'" in prompt
    assert "agent/work" in prompt
    assert ALL_DONE_MARKER in prompt


@pytest.mark.parametrize(
    ("text", "exit_code", "expected"),
    [
        # the CLI's structured banner is decisive on its own, any exit
        ("Claude AI usage limit reached|123456", 1, True),
        ("Claude AI usage limit reached|123456", 0, True),
        # generic mentions count only when paired with a nonzero exit
        ("You have hit your RATE LIMIT", 1, True),
        ("rate_limit_error from API", 1, True),
        ("quota exceeded for the day", 1, True),
        # 9.6: a project *about* rate limits exiting cleanly must NOT park the run
        ("implemented rate limiting middleware... just kidding, all good", 0, False),
        ("test_rate_limit_retry PASSED", 0, False),
        ("applying rate limit backoff", 0, False),
        ("Task 1 complete, tests pass", 0, False),
        ("", 0, False),
    ],
)
def test_is_usage_limit(text, exit_code, expected):
    assert is_usage_limit(text, exit_code) is expected


def test_usage_limit_ignores_rate_limit_chatter_on_clean_exit():
    """A project whose own output discusses rate limits, exiting 0, is not a limit."""
    output = (
        "Running suite...\n"
        "test_rate_limit_retry PASSED\n"
        "applying rate limit backoff\n"
        + "".join(f"case {i} ok\n" for i in range(20))
        + "All 42 tests passed\nTask 3 complete\n"
    )
    assert is_usage_limit(output, 0) is False


def test_usage_limit_detects_genuine_tail_banner():
    output = "...\nfinished implementing\n...\nClaude AI usage limit reached|1751900000\n"
    assert is_usage_limit(output, 1) is True


def test_usage_limit_only_scans_the_tail():
    """A limit-looking line far above the tail is ignored, even on a nonzero exit."""
    output = "rate limit reached\n" + "".join(f"line {i}\n" for i in range(20)) + "done\n"
    assert is_usage_limit(output, 1) is False
