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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config" and getattr(args, "config_command", None) == "check":
        return cmd_config_check(args.config)
    if args.command == "config":
        parser.parse_args(["config", "--help"])
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
