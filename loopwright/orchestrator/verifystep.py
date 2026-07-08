"""Independent verification step: the worker's word is not evidence.

Between the developer step (which harvests a worker push through the
fetch-gate) and the deployment step, the Orchestrator re-runs the project's
whole test suite itself, from a *fresh clone* of ``agent/work`` on the
Developer VM. Only when that independent run passes does a checkpoint get
tagged — so a ``checkpoint/NNNN`` tag certifies "the Orchestrator re-ran the
suite and it passed," never merely "the worker said so" (design doc, Git
Model and Main Loop).

The candidate must carry its own suite runner:

* ``scripts/test.sh`` — runs the full suite from a clean clone, exits nonzero
  on any failure.

Git transfer uses the same trust-boundary model as the other VM steps: the
host pushes the branch to a bare repo on the VM and the VM only ever sees its
own local clone.
"""

import posixpath
import shlex

from loopwright.core.config import Config
from loopwright.core.model import ProjectStore
from loopwright.gitctl.repo import WORK_BRANCH, ProjectRepo
from loopwright.notify.ntfy import Event
from loopwright.orchestrator.engine import Step, StepFailed
from loopwright.vmctl.ssh import SSHTimeout
from loopwright.vmctl.vm import LibvirtVM, VMError

REQUIRED_SCRIPTS = ("scripts/test.sh",)


class VerifyTestsStep:
    """Callable engine step; collaborators are injected so tests use fakes."""

    name = "verify-tests"

    def __init__(
        self,
        project: str,
        repo: ProjectRepo,
        vm,
        ssh,
        vm_repo_url: str,
        remote_repo_dir: str,
        remote_verify_dir: str,
        branch: str = WORK_BRANCH,
        timeout: int = 1800,
        checkpoint_slug: str = "worker-session",
    ):
        self.project = project
        self.repo = repo
        self.vm = vm
        self.ssh = ssh
        self.vm_repo_url = vm_repo_url
        self.remote_repo_dir = remote_repo_dir
        self.remote_verify_dir = remote_verify_dir
        self.branch = branch
        self.timeout = timeout
        self.checkpoint_slug = checkpoint_slug

    def _fresh_clone(self, ctx) -> None:
        """Push the host's accepted branch and clone it fresh on the VM."""
        parent = posixpath.dirname(self.remote_repo_dir) or "."
        init = self.ssh.run(
            f"mkdir -p {shlex.quote(parent)} && "
            f"git init --bare -q {shlex.quote(self.remote_repo_dir)}"
        )
        if not init.ok:
            raise StepFailed(f"could not init repo on developer VM: {init.stderr.strip()}")
        self.repo.push_to(self.vm_repo_url, [self.branch])
        work = shlex.quote(self.remote_verify_dir)
        clone = self.ssh.run(
            f"rm -rf {work} && "
            f"git clone -q -b {shlex.quote(self.branch)} "
            f"{shlex.quote(self.remote_repo_dir)} {work}"
        )
        if not clone.ok:
            raise StepFailed(f"could not make a fresh clone to verify: {clone.stderr.strip()}")
        ctx.log.log(self.name, f"fresh clone of {self.branch} made for independent verification")

    def _run_tests(self, ctx) -> str:
        script = "scripts/test.sh"
        command = f"cd {shlex.quote(self.remote_verify_dir)} && bash {shlex.quote(script)}"
        try:
            result = self.ssh.run(command, timeout=self.timeout)
        except SSHTimeout as exc:
            raise StepFailed(f"{script} timed out: {exc}") from exc
        output = (result.stdout or "") + (result.stderr or "")
        tail = output[-1500:]
        if not result.ok:
            ctx.log.log(
                self.name,
                f"{script} failed ({result.exit_code}): {tail[-300:]}",
                level="error",
                exit_code=result.exit_code,
            )
            raise StepFailed(f"independent verification failed: {script} exited {result.exit_code}")
        ctx.log.log(self.name, f"{script} passed under independent verification", exit_code=0)
        return tail

    def __call__(self, ctx) -> dict:
        for script in REQUIRED_SCRIPTS:
            if not self.repo.has_file(self.branch, script):
                raise StepFailed(
                    f"candidate has no {script}; the project must provide it so the "
                    "Orchestrator can re-run the suite independently"
                )

        try:
            if not self.vm.is_running():
                ctx.log.log(self.name, "starting developer VM for verification")
                self.vm.start()
        except VMError as exc:
            raise StepFailed(f"could not start developer VM: {exc}") from exc

        self._fresh_clone(ctx)
        commit = self.repo.head_of(self.branch)
        test_tail = self._run_tests(ctx)

        # The tag lands only after an independent pass — this is the single
        # checkpoint-tagging site among the orchestrator steps.
        tag = self.repo.tag_checkpoint(self.checkpoint_slug, ref=self.branch)
        ctx.log.log(self.name, f"independently verified; checkpoint tagged: {tag}")
        if ctx.notifier is not None:
            ctx.notifier.notify(
                Event.CHECKPOINT_PASSED, f"{tag} at {commit[:10]}", project=self.project
            )
        return {"commit": commit, "checkpoint": tag, "test_tail": test_tail}


def verify_step_from_config(
    config: Config, store: ProjectStore, project: str, timeout: int = 1800
) -> Step:
    """Build the real independent-verification step for a project from host config."""
    from loopwright.vmctl.ssh import SSHRunner

    meta = store.load_project(project)
    vm_config = config.dev_vm
    impl = VerifyTestsStep(
        project=project,
        repo=ProjectRepo(meta.repo_path),
        vm=LibvirtVM(vm_config.domain, config.libvirt_uri),
        ssh=SSHRunner(vm_config.host, vm_config.user),
        vm_repo_url=f"{vm_config.user}@{vm_config.host}:loopwright/{project}.git",
        remote_repo_dir=f"loopwright/{project}.git",
        remote_verify_dir=f"loopwright/{project}-verify",
        timeout=timeout,
    )
    return Step(impl.name, impl)
