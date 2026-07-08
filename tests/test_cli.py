import subprocess
import sys

import pytest

from loopwright import __version__
from loopwright.cli import main


def test_version_flag_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help_and_succeeds(capsys):
    assert main([]) == 0
    assert "usage: loopwright" in capsys.readouterr().out


@pytest.fixture
def temp_store_config(tmp_path, monkeypatch):
    doctrine = tmp_path / "doctrine"
    doctrine.mkdir()
    (doctrine / "PRINCIPLES.md").write_text("# p\n")
    (doctrine / "AGENT_RULES.md").write_text("# r\n")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"projects_dir: {tmp_path / 'projects'}\ndoctrine_dir: {doctrine}\n"
    )
    monkeypatch.setenv("LOOPWRIGHT_CONFIG", str(config_file))
    return tmp_path / "projects"


def test_project_create_and_list(temp_store_config, capsys):
    assert main(["project", "create", "demo"]) == 0
    out = capsys.readouterr().out
    assert "created project 'demo'" in out
    assert (temp_store_config / "demo" / "project.yaml").is_file()
    assert (temp_store_config / "demo" / "run.json").is_file()

    assert main(["project", "list"]) == 0
    assert "demo  [DRAFT]" in capsys.readouterr().out


def test_project_create_duplicate_fails(temp_store_config, capsys):
    assert main(["project", "create", "demo"]) == 0
    assert main(["project", "create", "demo"]) == 1
    assert "already exists" in capsys.readouterr().out


def test_project_create_invalid_name_fails(temp_store_config, capsys):
    assert main(["project", "create", "Bad Name"]) == 1
    assert "invalid project name" in capsys.readouterr().out


def test_installed_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "loopwright.cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout
