"""Deployment VM step: prove the candidate installs and works from scratch.

The Deployment VM is reverted to a clean snapshot before every test, so
nothing left over from a previous run can mask a missing dependency or an
undocumented manual step. The candidate must carry its own proof:

* ``scripts/deploy.sh``      — installs the product on a bare machine
* ``scripts/acceptance.sh``  — verifies the deployed product actually works

Git transfer uses the same trust-boundary model as the developer step: the
host pushes the candidate branch to a bare repo on the VM and the VM only
ever sees its own local copy.
"""

import posixpath
import shlex
import time

from loopwright.core.config import Config
from loopwright.core.model import ProjectStore
from loopwright.gitctl.repo import WORK_BRANCH, ProjectRepo
from loopwright.notify.ntfy import Event
from loopwright.orchestrator.engine import Step, StepFailed
from loopwright.vmctl.ssh import SSHTimeout
from loopwright.vmctl.vm import LibvirtVM, VMError

REQUIRED_SCRIPTS = ("scripts/deploy.sh", "scripts/acceptance.sh")


class DeploymentVMStep:
    """Callable engine step; collaborators are injected so tests use fakes."""

    name = "deploy-test"

    def __init__(
        self,
        project: str,
        repo: ProjectRepo,
        vm,
        ssh,
        vm_repo_url: str,
        remote_repo_dir: str,
        remote_work_dir: str,
        clean_snapshot: str | None = None,
        branch: str = WORK_BRANCH,
        timeout: int = 1800,
        ssh_wait_retries: int = 24,
        ssh_wait_delay: float = 5.0,
    ):
        self.project = project
        self.repo = repo
        self.vm = vm
        self.ssh = ssh
        self.vm_repo_url = vm_repo_url
        self.remote_repo_dir = remote_repo_dir
        self.remote_work_dir = remote_work_dir
        self.clean_snapshot = clean_snapshot
        self.branch = branch
        self.timeout = timeout
        self.ssh_wait_retries = ssh_wait_retries
        self.ssh_wait_delay = ssh_wait_delay

    def _fresh_vm(self, ctx) -> None:
        if self.clean_snapshot:
            try:
                if self.vm.is_running():
                    self.vm.shutdown()
                self.vm.snapshot_revert(self.clean_snapshot)
            except VMError as exc:
                raise StepFailed(f"could not revert deployment VM: {exc}") from exc
            ctx.log.log(self.name, f"deployment VM reverted to snapshot {self.clean_snapshot!r}")
        else:
            ctx.log.log(
                self.name,
                "no clean snapshot configured for the deployment VM; testing on it as-is",
                level="warning",
            )
        try:
            if not self.vm.is_running():
                self.vm.start()
        except VMError as exc:
            raise StepFailed(f"could not start deployment VM: {exc}") from exc
        self._wait_for_ssh(ctx)

    def _wait_for_ssh(self, ctx) -> None:
        for attempt in range(self.ssh_wait_retries):
            try:
                if self.ssh.run("true", timeout=15).ok:
                    if attempt:
                        ctx.log.log(self.name, f"deployment VM reachable after {attempt} retries")
                    return
            except SSHTimeout:
                pass
            time.sleep(self.ssh_wait_delay)
        raise StepFailed("deployment VM did not become reachable over SSH")

    def _sync_candidate(self, ctx) -> None:
        parent = posixpath.dirname(self.remote_repo_dir) or "."
        init = self.ssh.run(
            f"mkdir -p {shlex.quote(parent)} && "
            f"git init --bare -q {shlex.quote(self.remote_repo_dir)}"
        )
        if not init.ok:
            raise StepFailed(f"could not init repo on deployment VM: {init.stderr.strip()}")
        self.repo.push_to(self.vm_repo_url, [self.branch])
        work = shlex.quote(self.remote_work_dir)
        clone = self.ssh.run(
            f"rm -rf {work} && "
            f"git clone -q -b {shlex.quote(self.branch)} "
            f"{shlex.quote(self.remote_repo_dir)} {work}"
        )
        if not clone.ok:
            raise StepFailed(f"could not clone candidate on VM: {clone.stderr.strip()}")
        ctx.log.log(self.name, f"candidate ({self.branch}) synced to deployment VM")

    def _run_script(self, ctx, script: str) -> str:
        command = f"cd {shlex.quote(self.remote_work_dir)} && bash {shlex.quote(script)}"
        try:
            result = self.ssh.run(command, timeout=self.timeout)
        except SSHTimeout as exc:
            raise StepFailed(f"{script} timed out: {exc}") from exc
        output = (result.stdout or "") + (result.stderr or "")
        tail = output[-1500:]
        if not result.ok:
            ctx.log.log(self.name, f"{script} failed ({result.exit_code}): {tail[-300:]}",
                        level="error", exit_code=result.exit_code)
            raise StepFailed(f"{script} failed with exit code {result.exit_code}")
        ctx.log.log(self.name, f"{script} passed", exit_code=0)
        return tail

    def __call__(self, ctx) -> dict:
        for script in REQUIRED_SCRIPTS:
            if not self.repo.has_file(self.branch, script):
                raise StepFailed(
                    f"candidate has no {script}; the project must provide it "
                    "so deployment is reproducible"
                )

        self._fresh_vm(ctx)
        self._sync_candidate(ctx)
        commit = self.repo.head_of(self.branch)

        deploy_tail = self._run_script(ctx, "scripts/deploy.sh")
        acceptance_tail = self._run_script(ctx, "scripts/acceptance.sh")

        if ctx.notifier is not None:
            ctx.notifier.notify(
                Event.DEPLOYMENT_PASSED,
                f"deploy + acceptance passed at {commit[:10]}",
                project=self.project,
            )
        return {
            "commit": commit,
            "deploy_tail": deploy_tail,
            "acceptance_tail": acceptance_tail,
        }


def deploy_step_from_config(
    config: Config, store: ProjectStore, project: str, timeout: int = 1800
) -> Step:
    """Build the real Deployment VM step for a project from host config."""
    from loopwright.vmctl.ssh import SSHRunner

    meta = store.load_project(project)
    vm_config = config.test_vm
    impl = DeploymentVMStep(
        project=project,
        repo=ProjectRepo(meta.repo_path),
        vm=LibvirtVM(vm_config.domain, config.libvirt_uri),
        ssh=SSHRunner(vm_config.host, vm_config.user),
        vm_repo_url=f"{vm_config.user}@{vm_config.host}:loopwright/{project}.git",
        remote_repo_dir=f"loopwright/{project}.git",
        remote_work_dir=f"loopwright/{project}",
        clean_snapshot=vm_config.snapshot,
        timeout=timeout,
    )
    return Step(impl.name, impl)
