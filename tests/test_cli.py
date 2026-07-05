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


def test_installed_entry_point_runs():
    result = subprocess.run(
        [sys.executable, "-m", "loopwright.cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout
