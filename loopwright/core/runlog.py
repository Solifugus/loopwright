"""Structured run logs: one JSONL entry per line under ``projects/<name>/logs/``.

Any component (orchestrator steps, VM control, git control) appends entries;
the web UI tails and filters them. Malformed lines are skipped on read so a
crashed writer can never take the viewer down with it.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LEVELS = ("debug", "info", "warning", "error")
LOG_FILENAME = "run.jsonl"


class RunLog:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.path = self.directory / LOG_FILENAME

    def log(self, step: str, message: str, level: str = "info", **extra) -> dict:
        """Append one entry; extra keyword fields ride along but can't shadow core keys."""
        if level not in LEVELS:
            raise ValueError(f"unknown log level {level!r}; use one of {LEVELS}")
        entry = {
            **extra,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": level,
            "step": step,
            "message": message,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry

    def read(
        self,
        level: str | None = None,
        step: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Entries in write order, optionally filtered; ``limit`` keeps the newest N."""
        if not self.path.is_file():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if level is not None and entry.get("level") != level:
                continue
            if step is not None and entry.get("step") != step:
                continue
            entries.append(entry)
        if limit is not None and limit > 0:
            entries = entries[-limit:]
        return entries

    def steps(self) -> list[str]:
        """Distinct step names seen so far, for filter dropdowns."""
        return sorted({e.get("step", "") for e in self.read() if e.get("step")})
