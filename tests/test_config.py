import pytest

from loopwright.cli import main
from loopwright.core.config import (
    Config,
    ConfigError,
    check_config,
    config_path,
    load_config,
)


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_limit_resume_minutes_parses(tmp_path):
    assert load_config(write(tmp_path, "limit_resume_minutes: 45\n")).limit_resume_minutes == 45


@pytest.mark.parametrize("bad", ["limit_resume_minutes: -5", "limit_resume_minutes: soon"])
def test_limit_resume_minutes_invalid(tmp_path, bad):
    with pytest.raises(ConfigError, match="limit_resume_minutes"):
        load_config(write(tmp_path, bad + "\n"))


def test_provisional_cap_parses(tmp_path):
    assert load_config(write(tmp_path, "provisional_cap: 5\n")).provisional_cap == 5
    assert load_config(write(tmp_path, "")).provisional_cap == 2  # default


@pytest.mark.parametrize("bad", ["provisional_cap: -1", "provisional_cap: lots"])
def test_provisional_cap_invalid(tmp_path, bad):
    with pytest.raises(ConfigError, match="provisional_cap"):
        load_config(write(tmp_path, bad + "\n"))


def test_web_base_url_parses(tmp_path):
    config = load_config(write(tmp_path, "web_base_url: http://192.168.1.10:8000\n"))
    assert config.web_base_url == "http://192.168.1.10:8000"
    assert load_config(write(tmp_path, "")).web_base_url is None  # default


def test_web_base_url_invalid(tmp_path):
    with pytest.raises(ConfigError, match="web_base_url"):
        load_config(write(tmp_path, "web_base_url: 42\n"))


def test_missing_file_yields_defaults(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config == Config()
    assert config.dev_vm.domain == "LoopWright_Dev"
    assert config.test_vm.host == "192.168.122.120"


def test_empty_file_yields_defaults(tmp_path):
    assert load_config(write(tmp_path, "")) == Config()


def test_full_valid_config(tmp_path):
    path = write(
        tmp_path,
        """
projects_dir: /srv/lw/projects
libvirt_uri: qemu:///session
dev_vm: {domain: dev, host: 10.0.0.1, user: bob}
test_vm: {domain: test, host: 10.0.0.2}
ntfy_server: https://ntfy.example.com
ntfy_topic: my-topic
openai_api_key_env: MY_KEY
""",
    )
    config = load_config(path)
    assert str(config.projects_dir) == "/srv/lw/projects"
    assert config.libvirt_uri == "qemu:///session"
    assert config.dev_vm.user == "bob"
    assert config.test_vm.user == "master"  # default preserved
    assert config.ntfy_topic == "my-topic"
    assert config.openai_api_key_env == "MY_KEY"


def test_projects_dir_expands_user(tmp_path):
    config = load_config(write(tmp_path, "projects_dir: ~/lw-projects"))
    assert "~" not in str(config.projects_dir)


def test_example_config_is_valid():
    config = load_config("examples/config.example.yaml")
    assert config.dev_vm.domain == "LoopWright_Dev"
    assert config.test_vm.domain == "loopwright_test"


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("- a\n- b\n", "top-level mapping"),
        ("bogus_key: 1\n", "unknown keys"),
        ("libvirt_uri: 42\n", "non-empty string"),
        ("projects_dir: ''\n", "projects_dir"),
        ("dev_vm: not-a-map\n", "expected a mapping"),
        ("dev_vm: {domain: d}\n", "missing required keys"),
        ("dev_vm: {domain: d, host: h, extra: x}\n", "unknown keys"),
        ("dev_vm: {domain: d, host: 42}\n", "non-empty string"),
        ("ntfy_topic: 123\n", "ntfy_topic"),
        ("{invalid yaml::\n", "invalid YAML"),
    ],
)
def test_invalid_configs_raise_clear_errors(tmp_path, text, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_config(write(tmp_path, text))


def test_env_var_overrides_default_path(tmp_path, monkeypatch):
    path = write(tmp_path, "libvirt_uri: qemu:///session")
    monkeypatch.setenv("LOOPWRIGHT_CONFIG", str(path))
    assert config_path() == path
    assert load_config().libvirt_uri == "qemu:///session"


def test_check_reports_writable_projects_dir_and_warnings(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = Config(projects_dir=tmp_path / "projects")
    findings = check_config(config)
    levels = {message: level for level, message in findings}
    assert any("projects_dir writable" in m and lvl == "ok" for m, lvl in levels.items())
    assert any("ntfy_topic not set" in m and lvl == "warn" for m, lvl in levels.items())
    assert any("OPENAI_API_KEY not set" in m and lvl == "warn" for m, lvl in levels.items())
    assert (tmp_path / "projects").is_dir()


def test_check_flags_uncreatable_projects_dir(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file")
    config = Config(projects_dir=blocker / "projects")
    findings = check_config(config)
    assert any(level == "error" and "not creatable" in msg for level, msg in findings)


def make_doctrine(tmp_path):
    base = tmp_path / "doctrine"
    base.mkdir()
    (base / "PRINCIPLES.md").write_text("# p\n")
    (base / "AGENT_RULES.md").write_text("# r\n")
    return base


def test_check_errors_without_doctrine(tmp_path):
    config = Config(projects_dir=tmp_path / "projects")  # doctrine_dir defaults None
    findings = check_config(config)
    assert any(level == "error" and "doctrine_dir not set" in msg for level, msg in findings)


def test_check_errors_on_incomplete_doctrine(tmp_path):
    base = tmp_path / "doctrine"
    base.mkdir()
    (base / "PRINCIPLES.md").write_text("# p\n")  # AGENT_RULES.md missing
    config = Config(projects_dir=tmp_path / "projects", doctrine_dir=base)
    findings = check_config(config)
    assert any(level == "error" and "AGENT_RULES.md" in msg for level, msg in findings)


def test_check_ok_with_valid_doctrine(tmp_path):
    config = Config(projects_dir=tmp_path / "projects", doctrine_dir=make_doctrine(tmp_path))
    findings = check_config(config)
    assert any(level == "ok" and "doctrine_dir:" in msg for level, msg in findings)
    assert not any("doctrine" in msg and level == "error" for level, msg in findings)


def test_cli_config_check_ok(tmp_path, capsys):
    doctrine = make_doctrine(tmp_path)
    path = write(
        tmp_path, f"projects_dir: {tmp_path}/projects\ndoctrine_dir: {doctrine}\n"
    )
    assert main(["config", "check", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "config file:" in out
    assert "[ok" in out


def test_cli_config_check_bad_file(tmp_path, capsys):
    path = write(tmp_path, "bogus_key: 1\n")
    assert main(["config", "check", "--config", str(path)]) == 1
    assert "error:" in capsys.readouterr().out
