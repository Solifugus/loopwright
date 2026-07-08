"""Fetch-gate: deterministic inspection of every worker push (task 9.1)."""

import subprocess

import pytest

from loopwright.core.model import ProjectStore, RunState
from loopwright.core.runlog import RunLog
from loopwright.gitctl.repo import ProjectRepo
from loopwright.notify.ntfy import Event, NullNotifier
from loopwright.orchestrator import fetchgate
from loopwright.orchestrator.devstep import DeveloperVMStep
from loopwright.orchestrator.engine import StepContext, StepFailed
from loopwright.vmctl.ssh import DryRunSSH
from loopwright.vmctl.vm import SHUT_OFF, DryRunVM

PACKET = {
    "DESIGN.md": "# demo design\n",
    "DEVPLAN.md": "- [ ] 1. say hello\n- [ ] 2. say goodbye\n",
    "TESTPLAN.md": "# tests\n",
}


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """A host repo whose agent/work starts from the packet above."""
    return ProjectRepo.init(tmp_path / "host.git", PACKET)


def commit(repo, files, message="worker push"):
    """Commit files onto agent/work; return the new head sha."""
    repo.commit_files(files, "agent/work", message)
    return repo.head_of("agent/work")


# --- pure inspection -------------------------------------------------------


def test_legal_push_passes(repo):
    before = repo.head_of("agent/work")
    after = commit(
        repo,
        {
            "src/hello.py": "print('hi')\n",
            # task 1 ticked, a brand-new task appended
            "DEVPLAN.md": "- [x] 1. say hello\n- [ ] 2. say goodbye\n- [ ] 3. new task\n",
        },
    )
    verdict = fetchgate.inspect_range(repo, before, after)
    assert verdict.ok
    assert verdict.offending_files == []


def test_deferred_annotation_on_unchecked_is_legal(repo):
    before = repo.head_of("agent/work")
    after = commit(
        repo,
        {"DEVPLAN.md": "- [ ] 1. say hello\n- [ ] 2. say goodbye (DEFERRED)\n"},
    )
    assert fetchgate.inspect_range(repo, before, after).ok


@pytest.mark.parametrize("protected", ["DESIGN.md", "PRINCIPLES.md", "AGENT_RULES.md"])
def test_protected_file_rejected(repo, protected):
    before = repo.head_of("agent/work")
    after = commit(repo, {protected: "# tampered\n"})
    verdict = fetchgate.inspect_range(repo, before, after)
    assert not verdict.ok
    assert protected in verdict.offending_files


def test_protected_file_rejected_under_docs_path(repo):
    """Matched on basename, so a future docs/ layout is covered too."""
    before = repo.head_of("agent/work")
    after = commit(repo, {"docs/agent/AGENT_RULES.md": "# tampered\n"})
    verdict = fetchgate.inspect_range(repo, before, after)
    assert not verdict.ok
    assert "docs/agent/AGENT_RULES.md" in verdict.offending_files


def test_devplan_task_deletion_rejected(repo):
    before = repo.head_of("agent/work")
    after = commit(repo, {"DEVPLAN.md": "- [ ] 1. say hello\n"})  # task 2 removed
    verdict = fetchgate.inspect_range(repo, before, after)
    assert not verdict.ok
    assert "DEVPLAN.md" in verdict.offending_files
    assert verdict.devplan_diff  # the offending diff is captured verbatim


def test_devplan_reorder_rejected(repo):
    before = repo.head_of("agent/work")
    after = commit(repo, {"DEVPLAN.md": "- [ ] 2. say goodbye\n- [ ] 1. say hello\n"})
    assert not fetchgate.inspect_range(repo, before, after).ok


def test_editing_checked_item_rejected(repo):
    initial = repo.head_of("agent/work")
    # legally tick task 1 first
    mid = commit(repo, {"DEVPLAN.md": "- [x] 1. say hello\n- [ ] 2. say goodbye\n"})
    assert fetchgate.inspect_range(repo, initial, mid).ok
    # now rewrite the checked task's text — forbidden
    after = commit(repo, {"DEVPLAN.md": "- [x] 1. say HELLO LOUDLY\n- [ ] 2. say goodbye\n"})
    assert not fetchgate.inspect_range(repo, mid, after).ok


def test_editing_non_task_line_rejected(repo):
    before = repo.head_of("agent/work")
    after = commit(
        repo,
        {"DEVPLAN.md": "# heading added by agent\n- [ ] 1. say hello\n- [ ] 2. say goodbye\n"},
    )
    # inserting a heading line is a pure append-in-place... but replacing the
    # first task's neighbourhood is fine only if it's an insert; a genuine edit
    # of an existing non-task line is rejected:
    after2 = commit(
        repo,
        {"DEVPLAN.md": "# heading added by agent\n- [ ] 1. say hello RENAMED\n- [ ] 2. say goodbye\n"},
    )
    assert fetchgate.inspect_range(repo, before, after).ok  # pure insertion is legal
    assert not fetchgate.inspect_range(repo, after, after2).ok  # editing task text is not


# --- enforcement through the Developer VM step -----------------------------


class ContentWorkerSSH(DryRunSSH):
    """Dry-run SSH that commits caller-supplied files to the VM repo on `claude`."""

    def __init__(self, vm_bare, workspace, files):
        super().__init__()
        self.vm_bare = vm_bare
        self.workspace = workspace
        self.files = files

    def run(self, command, timeout=600):
        result = super().run(command, timeout=timeout)
        if "claude" in command and result.ok:
            clone = self.workspace / "worker-clone"
            subprocess.run(
                ["git", "clone", "-q", "-b", "agent/work", str(self.vm_bare), str(clone)],
                capture_output=True,
                check=True,
            )
            for rel, content in self.files.items():
                target = clone / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            git(clone, "-c", "user.name=Worker", "-c", "user.email=w@vm", "add", "-A")
            git(
                clone,
                "-c", "user.name=Worker", "-c", "user.email=w@vm",
                "commit", "-q", "-m", "worker change",
            )
            git(clone, "push", "-q", "origin", "agent/work")
            subprocess.run(["rm", "-rf", str(clone)], check=True)
        return result


@pytest.fixture
def env(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    store.create("demo", str(tmp_path / "host.git"))
    run = store.load_run("demo")
    run.transition(RunState.READY)
    store.save_run("demo", run)

    host_repo = ProjectRepo.init(tmp_path / "host.git", PACKET)
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
        "tmp": tmp_path,
        "repo": host_repo,
        "vm_bare": vm_bare,
        "ctx": ctx,
        "notifier": notifier,
    }


def make_step(env, ssh):
    return DeveloperVMStep(
        project="demo",
        repo=env["repo"],
        vm=DryRunVM("LoopWright_Dev", state=SHUT_OFF),
        ssh=ssh,
        vm_repo_url=str(env["vm_bare"]),
        remote_repo_dir="loopwright/demo.git",
        remote_work_dir="loopwright/demo",
    )


def test_step_rejects_protected_file_and_restores_branch(env):
    before = env["repo"].head_of("agent/work")
    ssh = ContentWorkerSSH(env["vm_bare"], env["tmp"], {"DESIGN.md": "# tampered\n"})
    step = make_step(env, ssh)

    with pytest.raises(StepFailed, match="fetch-gate rejected"):
        step(env["ctx"])

    # branch ref restored to the pre-push commit, no checkpoint tagged
    assert env["repo"].head_of("agent/work") == before
    assert env["repo"].checkpoints() == []
    events = [event for event, _, _ in env["notifier"].events]
    assert Event.RULE_VIOLATION in events


def test_step_rejects_devplan_deletion_and_restores_branch(env):
    before = env["repo"].head_of("agent/work")
    ssh = ContentWorkerSSH(
        env["vm_bare"], env["tmp"], {"DEVPLAN.md": "- [ ] 1. say hello\n"}  # task 2 deleted
    )
    with pytest.raises(StepFailed, match="fetch-gate rejected"):
        make_step(env, ssh)(env["ctx"])
    assert env["repo"].head_of("agent/work") == before
    assert env["repo"].checkpoints() == []


def test_step_accepts_legal_push(env):
    before = env["repo"].head_of("agent/work")
    ssh = ContentWorkerSSH(
        env["vm_bare"],
        env["tmp"],
        {
            "src/hello.py": "print('hi')\n",
            "DEVPLAN.md": "- [x] 1. say hello\n- [ ] 2. say goodbye\n- [ ] 3. next\n",
        },
    )
    detail = make_step(env, ssh)(env["ctx"])
    # a legal push is accepted: the branch advances, no rule violation fires.
    # (Checkpoint tagging is the verify-tests step's job as of 9.2.)
    assert detail["commit"] == env["repo"].head_of("agent/work") != before
    assert "checkpoint" not in detail
    events = [event for event, _, _ in env["notifier"].events]
    assert Event.RULE_VIOLATION not in events
