import json

import pytest

from loopwright.core.runlog import RunLog


@pytest.fixture
def log(tmp_path):
    return RunLog(tmp_path / "logs")


def test_log_appends_jsonl_and_returns_entry(log):
    entry = log.log("clone", "cloning repository")
    assert entry["step"] == "clone"
    assert entry["message"] == "cloning repository"
    assert entry["level"] == "info"
    assert "T" in entry["ts"]

    lines = log.path.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry


def test_read_preserves_write_order(log):
    for i in range(5):
        log.log("step", f"message {i}")
    messages = [e["message"] for e in log.read()]
    assert messages == [f"message {i}" for i in range(5)]


def test_filter_by_level_and_step(log):
    log.log("clone", "ok")
    log.log("build", "compiling", level="debug")
    log.log("build", "boom", level="error")

    assert [e["message"] for e in log.read(level="error")] == ["boom"]
    assert [e["message"] for e in log.read(step="build")] == ["compiling", "boom"]
    assert [e["message"] for e in log.read(step="build", level="debug")] == ["compiling"]


def test_limit_keeps_newest(log):
    for i in range(10):
        log.log("s", f"m{i}")
    assert [e["message"] for e in log.read(limit=3)] == ["m7", "m8", "m9"]


def test_extra_fields_ride_along_but_cannot_shadow_core(log):
    entry = log.log("deploy", "done", exit_code=0, message_id="x", level_hint="whatever")
    assert entry["exit_code"] == 0
    assert entry["message"] == "done"  # extra can't overwrite core keys
    reread = log.read()[0]
    assert reread["exit_code"] == 0


def test_invalid_level_raises(log):
    with pytest.raises(ValueError, match="unknown log level"):
        log.log("s", "m", level="critical")


def test_read_missing_file_is_empty(log):
    assert log.read() == []
    assert log.steps() == []


def test_malformed_lines_are_skipped(log):
    log.log("good", "first")
    with log.path.open("a") as handle:
        handle.write("not json at all\n")
        handle.write('["a", "list"]\n')
        handle.write("\n")
    log.log("good", "second")
    assert [e["message"] for e in log.read()] == ["first", "second"]


def test_steps_lists_distinct_names(log):
    log.log("clone", "a")
    log.log("build", "b")
    log.log("clone", "c")
    assert log.steps() == ["build", "clone"]
