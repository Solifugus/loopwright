"""Loopwright command-line interface."""

import argparse

from loopwright import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopwright",
        description="Local, VM-supervised autonomous software development system.",
    )
    parser.add_argument("--version", action="version", version=f"loopwright {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    config_parser = subparsers.add_parser("config", help="configuration commands")
    config_sub = config_parser.add_subparsers(dest="config_command")
    check = config_sub.add_parser("check", help="validate config and report environment status")
    check.add_argument("--config", help="path to config file (default: ~/.config/loopwright/config.yaml)")

    vm_parser = subparsers.add_parser("vm", help="VM control commands")
    vm_sub = vm_parser.add_subparsers(dest="vm_command")
    for name, help_text in [
        ("status", "show VM state"),
        ("start", "start the VM"),
        ("stop", "gracefully shut down the VM"),
        ("snapshots", "list snapshots"),
    ]:
        p = vm_sub.add_parser(name, help=help_text)
        p.add_argument("vm", help="'dev', 'test', or a libvirt domain name")
    snap = vm_sub.add_parser("snapshot", help="create a snapshot")
    snap.add_argument("vm", help="'dev', 'test', or a libvirt domain name")
    snap.add_argument("name")
    snap.add_argument("--description", default="")
    revert = vm_sub.add_parser("revert", help="revert to a snapshot")
    revert.add_argument("vm", help="'dev', 'test', or a libvirt domain name")
    revert.add_argument("name")
    ex = vm_sub.add_parser("exec", help="run a command in the VM over SSH")
    ex.add_argument("vm", help="'dev' or 'test'")
    ex.add_argument("cmd")
    ex.add_argument("--timeout", type=int, default=600)

    project_parser = subparsers.add_parser("project", help="project commands")
    project_sub = project_parser.add_subparsers(dest="project_command")
    create = project_sub.add_parser("create", help="create a new project in the store")
    create.add_argument("name", help="lowercase letters, digits, '-' and '_'")
    project_sub.add_parser("list", help="list projects")

    run_parser = subparsers.add_parser("run", help="orchestrator run commands")
    run_sub = run_parser.add_subparsers(dest="run_command")
    run_dev = run_sub.add_parser("dev", help="run one Developer VM coding session")
    run_dev.add_argument("project")
    run_dev.add_argument("--timeout", type=int, default=3600, help="worker timeout in seconds")
    run_deploy = run_sub.add_parser("deploy", help="run a Deployment VM test of the candidate")
    run_deploy.add_argument("project")
    run_deploy.add_argument("--timeout", type=int, default=1800, help="script timeout in seconds")
    run_loop_parser = run_sub.add_parser("loop", help="run full dev→deploy cycles until done")
    run_loop_parser.add_argument("project")
    run_loop_parser.add_argument("--retry-limit", type=int, default=2)
    run_loop_parser.add_argument("--max-cycles", type=int, default=25)
    run_loop_parser.add_argument("--dev-timeout", type=int, default=3600)
    run_loop_parser.add_argument("--deploy-timeout", type=int, default=1800)

    serve_parser = subparsers.add_parser("serve", help="run the web UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    notify_parser = subparsers.add_parser("notify", help="notification commands")
    notify_sub = notify_parser.add_subparsers(dest="notify_command")
    notify_test = notify_sub.add_parser("test", help="send a test notification")
    notify_test.add_argument("--message", default="If you can read this, notifications work.")

    return parser


def cmd_config_check(config_file: str | None) -> int:
    from loopwright.core.config import ConfigError, check_config, config_path, load_config

    path = config_file or config_path()
    try:
        config = load_config(config_file)
    except ConfigError as exc:
        print(f"error: {exc}")
        return 1

    print(f"config file: {path}")
    findings = check_config(config)
    for level, message in findings:
        print(f"  [{level:5}] {message}")
    return 1 if any(level == "error" for level, _ in findings) else 0


def resolve_vm(config, ident: str):
    """Map 'dev'/'test' to configured VMs; anything else is a raw domain name."""
    if ident == "dev":
        return config.dev_vm
    if ident == "test":
        return config.test_vm
    from loopwright.core.config import VMConfig

    return VMConfig(domain=ident, host="")


def cmd_vm(args) -> int:
    from loopwright.core.config import load_config
    from loopwright.vmctl import ssh as ssh_mod
    from loopwright.vmctl import vm as vm_mod

    config = load_config()
    vm_config = resolve_vm(config, args.vm)

    if args.vm_command == "exec":
        if not vm_config.host:
            print(f"error: 'exec' needs an SSH host; use 'dev' or 'test', not {args.vm!r}")
            return 1
        runner = ssh_mod.SSHRunner(vm_config.host, vm_config.user)
        try:
            result = runner.run(args.cmd, timeout=args.timeout)
        except ssh_mod.SSHTimeout as exc:
            print(f"error: {exc}")
            return 1
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="")
        return result.exit_code

    machine = vm_mod.LibvirtVM(vm_config.domain, config.libvirt_uri)
    try:
        if args.vm_command == "status":
            print(f"{machine.domain}: {machine.state()}")
        elif args.vm_command == "start":
            machine.start()
            print(f"{machine.domain}: started")
        elif args.vm_command == "stop":
            machine.shutdown()
            print(f"{machine.domain}: shut off")
        elif args.vm_command == "snapshots":
            for snapshot_name in machine.snapshots():
                print(snapshot_name)
        elif args.vm_command == "snapshot":
            machine.snapshot_create(args.name, args.description)
            print(f"{machine.domain}: snapshot {args.name!r} created")
        elif args.vm_command == "revert":
            machine.snapshot_revert(args.name)
            print(f"{machine.domain}: reverted to {args.name!r}")
    except vm_mod.VMError as exc:
        print(f"error: {exc}")
        return 1
    return 0


def cmd_project(args) -> int:
    from loopwright import service
    from loopwright.core.config import load_config
    from loopwright.core.model import ProjectStore
    from loopwright.gitctl.repo import GitError

    config = load_config()
    store = ProjectStore(config.projects_dir)

    if args.project_command == "create":
        try:
            service.create_project(store, args.name)
        except (ValueError, FileExistsError, GitError) as exc:
            print(f"error: {exc}")
            return 1
        print(f"created project {args.name!r} in {store.project_dir(args.name)}")
        return 0

    for name in store.list_projects():
        run = store.load_run(name)
        print(f"{name}  [{run.state.value}]")
    return 0


def cmd_run_step(kind: str, project: str, timeout: int) -> int:
    from loopwright.core.config import load_config
    from loopwright.core.model import ProjectStore
    from loopwright.notify.ntfy import from_config
    from loopwright.orchestrator.deploystep import deploy_step_from_config
    from loopwright.orchestrator.devstep import dev_step_from_config
    from loopwright.orchestrator.engine import Engine, EngineError, StepFailed

    factory = dev_step_from_config if kind == "dev" else deploy_step_from_config
    config = load_config()
    store = ProjectStore(config.projects_dir)
    try:
        step = factory(config, store, project, timeout=timeout)
        engine = Engine(store, project, [step], notifier=from_config(config))
        outcome = engine.run()
    except FileNotFoundError:
        print(f"error: no project named {project!r}")
        return 1
    except (EngineError, StepFailed) as exc:
        print(f"error: {exc}")
        return 1
    run = store.load_run(project)
    print(f"outcome: {outcome} (run state: {run.state.value})")
    for step_result in run.steps:
        print(f"  {step_result['name']}: {step_result['status']}")
        if step_result["detail"].get("checkpoint"):
            print(f"    checkpoint: {step_result['detail']['checkpoint']}")
    return 0 if outcome in ("completed", "paused-limit") else 1


def cmd_run_loop(args) -> int:
    from loopwright.core.config import load_config
    from loopwright.core.model import ProjectStore
    from loopwright.notify.ntfy import from_config
    from loopwright.orchestrator.deploystep import deploy_step_from_config
    from loopwright.orchestrator.devstep import dev_step_from_config
    from loopwright.orchestrator.engine import EngineError
    from loopwright.orchestrator.loop import run_loop

    config = load_config()
    store = ProjectStore(config.projects_dir)
    try:
        steps = [
            dev_step_from_config(config, store, args.project, timeout=args.dev_timeout),
            deploy_step_from_config(config, store, args.project, timeout=args.deploy_timeout),
        ]
        outcome = run_loop(
            store,
            args.project,
            steps,
            notifier=from_config(config),
            retry_limit=args.retry_limit,
            max_cycles=args.max_cycles,
        )
    except FileNotFoundError:
        print(f"error: no project named {args.project!r}")
        return 1
    except EngineError as exc:
        print(f"error: {exc}")
        return 1
    run = store.load_run(args.project)
    print(f"outcome: {outcome} (run state: {run.state.value}, cycles: {run.cycle + 1})")
    return 0 if outcome == "finished" else 1


def cmd_serve(host: str, port: int) -> int:
    import uvicorn

    from loopwright.web.app import create_app_from_config

    uvicorn.run(create_app_from_config(), host=host, port=port)
    return 0


def cmd_notify_test(message: str) -> int:
    from loopwright.core.config import load_config
    from loopwright.notify.ntfy import Event, NullNotifier, from_config

    config = load_config()
    notifier = from_config(config)
    if isinstance(notifier, NullNotifier):
        print("ntfy_topic is not set in config; nothing sent")
        return 1
    if notifier.notify(Event.TEST, message):
        print(f"sent to {notifier.url}")
        return 0
    print(f"error: could not deliver to {notifier.url}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config" and getattr(args, "config_command", None) == "check":
        return cmd_config_check(args.config)
    if args.command == "config":
        parser.parse_args(["config", "--help"])
        return 0
    if args.command == "vm" and getattr(args, "vm_command", None):
        return cmd_vm(args)
    if args.command == "project" and getattr(args, "project_command", None):
        return cmd_project(args)
    if args.command == "project":
        parser.parse_args(["project", "--help"])
        return 0
    if args.command == "serve":
        return cmd_serve(args.host, args.port)
    if args.command == "run" and getattr(args, "run_command", None) in ("dev", "deploy"):
        return cmd_run_step(args.run_command, args.project, args.timeout)
    if args.command == "run" and getattr(args, "run_command", None) == "loop":
        return cmd_run_loop(args)
    if args.command == "run":
        parser.parse_args(["run", "--help"])
        return 0
    if args.command == "notify" and getattr(args, "notify_command", None) == "test":
        return cmd_notify_test(args.message)
    if args.command == "notify":
        parser.parse_args(["notify", "--help"])
        return 0
    if args.command == "vm":
        parser.parse_args(["vm", "--help"])
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
