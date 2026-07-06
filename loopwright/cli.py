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
