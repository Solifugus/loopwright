"""Host configuration.

Loaded from ``~/.config/loopwright/config.yaml`` (override with the
``LOOPWRIGHT_CONFIG`` environment variable or an explicit path). A missing file
yields defaults; a malformed or unrecognized file is an error — silent
misconfiguration must not be possible.
"""

import os
import shutil
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("~/.config/loopwright/config.yaml")


class ConfigError(Exception):
    """Raised when the config file is malformed or contains invalid values."""


@dataclass
class VMConfig:
    domain: str  # libvirt domain name
    host: str  # IP or hostname reachable over SSH
    user: str = "master"
    snapshot: str | None = None  # clean snapshot to revert to before deployment tests


@dataclass
class Config:
    projects_dir: Path = Path("~/.local/share/loopwright/projects")
    doctrine_dir: Path | None = None  # clone of loopwright-doctrine; None = built-ins
    libvirt_uri: str = "qemu:///system"
    dev_vm: VMConfig = field(
        default_factory=lambda: VMConfig(domain="LoopWright_Dev", host="192.168.122.20")
    )
    test_vm: VMConfig = field(
        default_factory=lambda: VMConfig(domain="loopwright_test", host="192.168.122.120")
    )
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str | None = None
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_model: str = "gpt-4o"
    limit_resume_minutes: int = 30  # auto-resume delay after a usage-limit pause

    def __post_init__(self) -> None:
        self.projects_dir = Path(self.projects_dir).expanduser()
        if self.doctrine_dir is not None:
            self.doctrine_dir = Path(self.doctrine_dir).expanduser()


def _build_vm(section: str, data: object) -> VMConfig:
    if not isinstance(data, dict):
        raise ConfigError(f"{section}: expected a mapping, got {type(data).__name__}")
    allowed = {f.name for f in fields(VMConfig)}
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"{section}: unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")
    missing = {"domain", "host"} - set(data)
    if missing:
        raise ConfigError(f"{section}: missing required keys {sorted(missing)}")
    for key, value in data.items():
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{section}.{key}: expected a non-empty string")
    return VMConfig(**data)


def config_path() -> Path:
    env = os.environ.get("LOOPWRIGHT_CONFIG")
    return Path(env).expanduser() if env else DEFAULT_CONFIG_PATH.expanduser()


def load_config(path: Path | str | None = None) -> Config:
    resolved = Path(path).expanduser() if path else config_path()
    if not resolved.is_file():
        return Config()

    try:
        raw = yaml.safe_load(resolved.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{resolved}: invalid YAML: {exc}") from exc
    if raw is None:
        return Config()
    if not isinstance(raw, dict):
        raise ConfigError(f"{resolved}: expected a top-level mapping")

    allowed = {f.name for f in fields(Config)}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"{resolved}: unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")

    kwargs: dict = {}
    for key, value in raw.items():
        if key in ("dev_vm", "test_vm"):
            kwargs[key] = _build_vm(key, value)
        elif key in ("projects_dir", "doctrine_dir"):
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{resolved}: {key} must be a non-empty string")
            kwargs[key] = Path(value)
        elif key == "limit_resume_minutes":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigError(
                    f"{resolved}: limit_resume_minutes must be a non-negative integer"
                )
            kwargs[key] = value
        elif key == "ntfy_topic":
            if value is not None and (not isinstance(value, str) or not value):
                raise ConfigError(f"{resolved}: ntfy_topic must be a non-empty string or null")
            kwargs[key] = value
        else:
            if not isinstance(value, str) or not value:
                raise ConfigError(f"{resolved}: {key} must be a non-empty string")
            kwargs[key] = value
    return Config(**kwargs)


def check_config(config: Config) -> list[tuple[str, str]]:
    """Return (level, message) findings; levels are 'ok', 'warn', 'error'."""
    findings: list[tuple[str, str]] = []

    try:
        config.projects_dir.mkdir(parents=True, exist_ok=True)
        findings.append(("ok", f"projects_dir writable: {config.projects_dir}"))
    except OSError as exc:
        findings.append(("error", f"projects_dir not creatable: {config.projects_dir}: {exc}"))

    for tool in ("git", "ssh", "virsh"):
        if shutil.which(tool):
            findings.append(("ok", f"{tool} found"))
        else:
            findings.append(("error", f"{tool} not found on PATH"))

    for label, vm in (("dev_vm", config.dev_vm), ("test_vm", config.test_vm)):
        findings.append(("ok", f"{label}: domain={vm.domain} ssh={vm.user}@{vm.host}"))

    if config.doctrine_dir is None:
        findings.append(("warn", "doctrine_dir not set: new projects use built-in doctrine"))
    elif (config.doctrine_dir / "PRINCIPLES.md").is_file():
        findings.append(("ok", f"doctrine_dir: {config.doctrine_dir}"))
    else:
        findings.append(
            ("error", f"doctrine_dir has no PRINCIPLES.md: {config.doctrine_dir}")
        )

    if config.ntfy_topic:
        findings.append(("ok", f"notifications: {config.ntfy_server}/{config.ntfy_topic}"))
    else:
        findings.append(("warn", "ntfy_topic not set: notifications are disabled"))

    if os.environ.get(config.openai_api_key_env):
        findings.append(("ok", f"{config.openai_api_key_env} is set"))
    else:
        findings.append(
            ("warn", f"{config.openai_api_key_env} not set: Primary Agent will be unavailable")
        )
    return findings
