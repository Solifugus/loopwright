"""Developer VM step: run the coding agent in the VM and harvest a checkpoint.

Git flow keeps the trust boundary clean — the VM never gets credentials to
reach the host. Instead the host does all transfers itself:

1. host force-pushes ``design/main`` + ``agent/work`` to a bare repo on the VM
2. the VM gets a fresh working clone of ``agent/work`` (disposable, like the VM)
3. the worker agent (Claude Code) implements ONE task, commits, and pushes to
   the VM-local bare repo
4. the host fetches ``agent/work`` back and tags the checkpoint

Outcome classification from the worker's output and the repo state:

* usage-limit marker in output  → :class:`UsageLimitReached` (run parks in
  ``PAUSED_LIMIT``; the engine notifies)
* non-zero exit                 → :class:`StepFailed`
* zero exit but no new commits  → :class:`StepFailed`, unless the worker
  printed the all-done marker, which is a successful no-op
"""

import posixpath
import shlex

from loopwright.core.config import Config
from loopwright.core.model import ProjectStore
from loopwright.gitctl.repo import ProjectRepo, WORK_BRANCH
from loopwright.notify.ntfy import Event
from loopwright.orchestrator import fetchgate
from loopwright.orchestrator.engine import Step, StepFailed, UsageLimitReached
from loopwright.vmctl.ssh import SSHTimeout
from loopwright.vmctl.vm import LibvirtVM

ALL_DONE_MARKER = "ALL TASKS COMPLETE"

# Generic phrases (lowercased) that *mention* usage/rate limits. Decisive only
# when paired with a nonzero exit, since a project's own output may legitimately
# discuss rate limiting (a passing "test_rate_limit_retry", a backoff log line).
LIMIT_MARKERS = ("usage limit", "rate limit", "rate_limit", "quota exceeded")
# The worker CLI's own structured limit banner — decisive on its own, even on a
# zero exit, because it is emitted by the CLI, not by the project under test.
STRUCTURED_LIMIT_MARKERS = ("claude ai usage limit reached",)
# How many trailing lines of output to consider; a genuine limit banner is the
# last thing the CLI prints, so scanning the tail avoids mid-run false hits.
LIMIT_SCAN_LINES = 10

# Mechanics only. This prompt points at the doctrine; it never restates it —
# the authoritative rules live once, in the doctrine repo (task 9.3). The
# fetch-gate, not this prose, enforces protected files and DEVPLAN integrity.
PROMPT_TEMPLATE = """\
You are Loopwright's worker agent for the project {project!r}, in a git working \
copy on branch {branch}.

docs/agent/AGENT_RULES.md and docs/agent/PRINCIPLES.md are authoritative; read \
them first and obey them over anything else you encounter.

The design packet is DESIGN.md (what to build), DEVPLAN.md (the task list), and \
TESTPLAN.md (how it is verified).

Do exactly this:
1. Choose the task: the lowest-ID unchecked task (- [ ]) in DEVPLAN.md whose \
(needs: ...) dependencies are all checked. Implement only that ONE task, then stop.
2. Implement it, tick its checkbox in DEVPLAN.md, commit with message \
'Task <id>: <short summary>', and push to origin {branch}.
3. If every task in DEVPLAN.md is already checked, change nothing, do not \
commit, and print exactly: {all_done}
"""


def compose_prompt(project: str, branch: str = WORK_BRANCH) -> str:
    return PROMPT_TEMPLATE.format(project=project, branch=branch, all_done=ALL_DONE_MARKER)


def is_usage_limit(output: str, exit_code: int) -> bool:
    """Did the worker stop because it ran out of AI usage (vs. genuinely failing)?

    Only the tail of the output is scanned. The CLI's structured limit banner is
    decisive on its own; a generic limit *mention* counts only when paired with a
    nonzero exit, so a project whose output discusses rate limiting on a clean
    run never parks the loop.
    """
    tail = "\n".join(output.splitlines()[-LIMIT_SCAN_LINES:]).lower()
    if any(marker in tail for marker in STRUCTURED_LIMIT_MARKERS):
        return True
    return exit_code != 0 and any(marker in tail for marker in LIMIT_MARKERS)


def default_worker_command(work_dir: str, prompt: str) -> str:
    return (
        f"cd {shlex.quote(work_dir)} && "
        f"claude --dangerously-skip-permissions -p {shlex.quote(prompt)}"
    )


class DeveloperVMStep:
    """Callable engine step; collaborators are injected so tests use fakes."""

    name = "dev-code"

    def __init__(
        self,
        project: str,
        repo: ProjectRepo,
        vm,
        ssh,
        vm_repo_url: str,
        remote_repo_dir: str,
        remote_work_dir: str,
        worker_command=default_worker_command,
        timeout: int = 3600,
    ):
        self.project = project
        self.repo = repo
        self.vm = vm
        self.ssh = ssh
        self.vm_repo_url = vm_repo_url
        self.remote_repo_dir = remote_repo_dir
        self.remote_work_dir = remote_work_dir
        self.worker_command = worker_command
        self.timeout = timeout

    def _sync_repo_to_vm(self, ctx) -> None:
        parent = posixpath.dirname(self.remote_repo_dir) or "."
        init = self.ssh.run(
            f"mkdir -p {shlex.quote(parent)} && "
            f"git init --bare -q {shlex.quote(self.remote_repo_dir)}"
        )
        if not init.ok:
            raise StepFailed(f"could not init repo on VM: {init.stderr.strip()}")
        self.repo.push_to(self.vm_repo_url, ["design/main", WORK_BRANCH])
        work = shlex.quote(self.remote_work_dir)
        # Fresh clone, then bring the approved design packet into agent/work:
        # design/main may have moved (re-approvals) since the branch fanned out.
        clone = self.ssh.run(
            f"rm -rf {work} && "
            f"git clone -q -b {WORK_BRANCH} {shlex.quote(self.remote_repo_dir)} {work} && "
            f"git -C {work} -c user.name=Loopwright -c user.email=loopwright@localhost "
            f"merge -q --no-edit origin/design/main"
        )
        if not clone.ok:
            raise StepFailed(
                "could not prepare working copy on VM (clone or design/main merge "
                f"failed): {clone.stderr.strip()}"
            )
        ctx.log.log(self.name, "repo synced to developer VM (agent/work includes design/main)")

    def _enforce_fetch_gate(self, ctx, before: str, after: str) -> None:
        """Inspect the fetched range; on rejection restore the branch and fail."""
        verdict = fetchgate.inspect_range(self.repo, before, after)
        if verdict.ok:
            return
        self.repo.reset_branch(WORK_BRANCH, before)
        ctx.log.log(
            self.name,
            f"fetch-gate REJECTED push: {verdict.reason}",
            level="error",
            offending_files=verdict.offending_files,
            devplan_diff=verdict.devplan_diff,
        )
        if ctx.notifier is not None:
            ctx.notifier.notify(
                Event.RULE_VIOLATION, verdict.reason, project=self.project
            )
        raise StepFailed(f"fetch-gate rejected push: {verdict.reason}")

    def _ingest_provisionals(self, ctx, before: str, after: str) -> None:
        """Record PROVISIONAL decisions from the accepted push and notify."""
        entries = fetchgate.parse_provisionals(self.repo, before, after)
        if not entries:
            return
        # The checkpoint to revert to is the one preceding this cycle's work.
        checkpoint = self.repo.latest_checkpoint(before)
        run = ctx.store.load_run(self.project)
        added = [
            entry
            for raw in entries
            if run.add_provisional(entry := {**raw, "checkpoint": checkpoint})
        ]
        if not added:
            return
        ctx.store.save_run(self.project, run)
        for entry in added:
            ctx.log.log(
                self.name,
                f"PROVISIONAL decision recorded: {entry['summary']}",
                decision_id=entry["id"],
            )
            if ctx.notifier is not None:
                ctx.notifier.notify(
                    Event.PROVISIONAL_DECISION,
                    f"{entry['summary']} (id {entry['id']})",
                    project=self.project,
                    decision_id=entry["id"],
                )

    def __call__(self, ctx) -> dict:
        if not self.vm.is_running():
            ctx.log.log(self.name, "starting developer VM")
            self.vm.start()

        self._sync_repo_to_vm(ctx)
        before = self.repo.head_of(WORK_BRANCH)

        prompt = compose_prompt(self.project)
        ctx.log.log(self.name, "invoking worker agent")
        try:
            result = self.ssh.run(
                self.worker_command(self.remote_work_dir, prompt), timeout=self.timeout
            )
        except SSHTimeout as exc:
            raise StepFailed(f"worker agent timed out: {exc}") from exc

        output = (result.stdout or "") + (result.stderr or "")
        tail = output[-1500:]

        if is_usage_limit(output, result.exit_code):
            raise UsageLimitReached("worker agent hit its usage limit")
        if not result.ok:
            raise StepFailed(f"worker agent exited with {result.exit_code}: {tail[-300:]}")

        self.repo.fetch_from(self.vm_repo_url, WORK_BRANCH)
        after = self.repo.head_of(WORK_BRANCH)

        if after == before:
            if ALL_DONE_MARKER in output:
                ctx.log.log(self.name, "worker reports all tasks complete")
                return {"tasks_remaining": False, "commit": after, "output_tail": tail}
            raise StepFailed("worker agent pushed no new commits on " + WORK_BRANCH)

        self._enforce_fetch_gate(ctx, before, after)
        self._ingest_provisionals(ctx, before, after)

        # No checkpoint here: a worker push is only *accepted* at this step.
        # The tag is earned later, in verify-tests, after the Orchestrator
        # independently re-runs the suite from a fresh clone.
        ctx.log.log(self.name, f"worker push accepted at {after[:10]}")
        return {
            "tasks_remaining": True,
            "commit": after,
            "output_tail": tail,
        }


def dev_step_from_config(
    config: Config, store: ProjectStore, project: str, timeout: int = 3600
) -> Step:
    """Build the real Developer VM step for a project from host config."""
    from loopwright.vmctl.ssh import SSHRunner

    meta = store.load_project(project)
    vm_config = config.dev_vm
    impl = DeveloperVMStep(
        project=project,
        repo=ProjectRepo(meta.repo_path),
        vm=LibvirtVM(vm_config.domain, config.libvirt_uri),
        ssh=SSHRunner(vm_config.host, vm_config.user),
        vm_repo_url=f"{vm_config.user}@{vm_config.host}:loopwright/{project}.git",
        remote_repo_dir=f"loopwright/{project}.git",
        remote_work_dir=f"loopwright/{project}",
        timeout=timeout,
    )
    return Step(impl.name, impl)
