"""The fetch-gate: deterministic inspection of every worker push.

Because the host performs every fetch, the fetch is the enforcement
chokepoint (design doc, "Fetch-gate"). Before a fetched range is allowed to
become a checkpoint, the Orchestrator inspects the diff it introduced:

* pushes touching ``DESIGN.md``, ``PRINCIPLES.md``, or ``AGENT_RULES.md`` are
  rejected — those files are the human's, enforced by mechanism not trust;
* ``DEVPLAN.md`` is append-only from the agent's side: only checkbox ticks
  (``- [ ]`` → ``- [x]``), appended new tasks, and ``(DEFERRED)`` annotations
  on still-unchecked tasks are legal. Deletions, reorderings, and edits to
  checked items are rejected.
* an inserted task must carry a *fresh* stable ID: if an inserted task line's
  ID already exists in the before-version, the push is rejected. Task IDs are
  the contract the ``(needs:)`` graph and rollback are keyed on, so reusing
  one silently would corrupt them (design doc, DEVPLAN.md). Mid-file inserts
  of tasks with fresh IDs remain legal — position is not constrained, only ID
  uniqueness and the no-deletion/no-reorder/no-checked-edit rules above.

The gate is pure inspection: no AI, no side effects. It returns a
:class:`GateVerdict`; the caller (the Developer VM step) is responsible for
resetting the branch, logging, notifying, and failing the step on rejection.
"""

import difflib
import hashlib
import posixpath
import re
from dataclasses import dataclass, field

from loopwright.gitctl.repo import GitError, ProjectRepo

# Matched on basename so the gate holds whether the packet sits at the repo
# root (v0.1) or under docs/ (later restructures).
PROTECTED_BASENAMES = frozenset({"DESIGN.md", "PRINCIPLES.md", "AGENT_RULES.md"})
DEVPLAN_BASENAME = "DEVPLAN.md"
DECISIONS_BASENAME = "DECISIONS.md"

# A Markdown heading line (any level) opening a decision-log entry.
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.*\S)\s*$")
_PROVISIONAL = "PROVISIONAL"

# A DEVPLAN task line: an optional-indent list bullet, a checkbox, then text.
_CHECKBOX_RE = re.compile(r"^(\s*[-*]\s+)\[([ xX])\](.*)$")
# The stable task ID leading the task text: e.g. "1", "1.", "**9.1 Fetch...".
_TASK_ID_RE = re.compile(r"^\s*\*{0,2}\s*(\d+(?:\.\d+)*)")
_DEFERRED = "(DEFERRED)"


@dataclass
class GateVerdict:
    """Outcome of inspecting one fetched range."""

    ok: bool
    reason: str = ""
    offending_files: list[str] = field(default_factory=list)
    devplan_diff: str = ""


def inspect_range(repo: ProjectRepo, before: str, after: str) -> GateVerdict:
    """Inspect the diff ``before..after`` and rule on whether it may be accepted."""
    changed = repo.changed_files(before, after)

    protected = sorted(
        path for path in changed if posixpath.basename(path) in PROTECTED_BASENAMES
    )
    if protected:
        return GateVerdict(
            ok=False,
            reason="protected file(s) modified: " + ", ".join(protected),
            offending_files=protected,
        )

    for path in changed:
        if posixpath.basename(path) != DEVPLAN_BASENAME:
            continue
        legal, diff = _inspect_devplan(repo, before, after, path)
        if not legal:
            return GateVerdict(
                ok=False,
                reason=f"illegal {path} edit (only ticks, appends, and DEFERRED allowed)",
                offending_files=[path],
                devplan_diff=diff,
            )

    return GateVerdict(ok=True)


def _file_lines(repo: ProjectRepo, ref: str, path: str) -> list[str]:
    try:
        return repo.show(ref, path).splitlines()
    except GitError:
        return []  # file absent at this ref (e.g. freshly created)


def parse_provisionals(repo: ProjectRepo, before: str, after: str) -> list[dict]:
    """Parse PROVISIONAL entries *added* to docs/agent/DECISIONS.md in before..after.

    Returns ``[{id, summary, commit}]`` — one per added heading line whose text
    contains ``PROVISIONAL``. Pure parsing: the caller attaches the preceding
    checkpoint, persists the entries, and fires notifications. The id is a
    content hash of (commit, summary) so re-ingesting the same push is
    idempotent.
    """
    changed = repo.changed_files(before, after)
    entries: list[dict] = []
    for path in changed:
        if posixpath.basename(path) != DECISIONS_BASENAME:
            continue
        before_lines = _file_lines(repo, before, path)
        after_lines = _file_lines(repo, after, path)
        for line in _added_lines(before_lines, after_lines):
            heading = _HEADING_RE.match(line)
            if heading and _PROVISIONAL in heading.group(1):
                summary = heading.group(1).strip()
                ident = hashlib.sha1(f"{after}:{summary}".encode()).hexdigest()[:8]
                entries.append({"id": ident, "summary": summary, "commit": after})
    return entries


def _added_lines(before_lines: list[str], after_lines: list[str]) -> list[str]:
    """Lines present in `after` that are new relative to `before` (insert/replace)."""
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    added: list[str] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added.extend(after_lines[j1:j2])
    return added


def _inspect_devplan(
    repo: ProjectRepo, before: str, after: str, path: str
) -> tuple[bool, str]:
    """Return (legal, unified_diff). Legal = only ticks, appends, and DEFERRED."""
    before_lines = _file_lines(repo, before, path)
    after_lines = _file_lines(repo, after, path)
    diff = "\n".join(
        difflib.unified_diff(
            before_lines, after_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
        )
    )

    before_ids = _task_ids(before_lines)
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            # A removed line is a deletion (and the tell-tale of a reorder).
            return False, diff
        if tag == "insert":
            # Pure new lines — appended tasks. Existing lines keep their order,
            # so this never masks a reorder (that shows up as a delete). An
            # inserted task, though, must carry a fresh ID: reusing an existing
            # task's ID would corrupt the (needs:)/rollback contract.
            for line in after_lines[j1:j2]:
                task_id = _task_id(line)
                if task_id is not None and task_id in before_ids:
                    return False, diff
            continue
        if tag == "replace":
            if (i2 - i1) != (j2 - j1):
                return False, diff
            for old, new in zip(before_lines[i1:i2], after_lines[j1:j2]):
                if not _legal_line_edit(old, new):
                    return False, diff
    return True, diff


def _task_id(line: str) -> str | None:
    """The stable ID of a DEVPLAN task line, or None if the line isn't a task."""
    checkbox = _CHECKBOX_RE.match(line)
    if not checkbox:
        return None
    ident = _TASK_ID_RE.match(checkbox.group(3))
    return ident.group(1) if ident else None


def _task_ids(lines: list[str]) -> set[str]:
    return {task_id for line in lines if (task_id := _task_id(line)) is not None}


def _legal_line_edit(old: str, new: str) -> bool:
    """Is changing ``old`` into ``new`` a legal tick or DEFERRED annotation?"""
    om = _CHECKBOX_RE.match(old)
    nm = _CHECKBOX_RE.match(new)
    if not om or not nm:
        return False  # editing a non-task line is never legal
    old_bullet, old_box, old_text = om.group(1), om.group(2).lower(), om.group(3)
    new_bullet, new_box, new_text = nm.group(1), nm.group(2).lower(), nm.group(3)
    if old_bullet != new_bullet:
        return False
    if old_box == "x":
        return False  # edits to checked items are forbidden
    # old is unchecked from here.
    if new_box == "x":
        return old_text == new_text  # a tick may not also rewrite the task text
    # Still unchecked: the only legal change is adding a (DEFERRED) annotation.
    if _DEFERRED not in old_text and _DEFERRED in new_text:
        return new_text.replace(_DEFERRED, "").rstrip() == old_text.rstrip()
    return False
