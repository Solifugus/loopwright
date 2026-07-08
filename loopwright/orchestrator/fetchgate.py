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

The gate is pure inspection: no AI, no side effects. It returns a
:class:`GateVerdict`; the caller (the Developer VM step) is responsible for
resetting the branch, logging, notifying, and failing the step on rejection.
"""

import difflib
import posixpath
import re
from dataclasses import dataclass, field

from loopwright.gitctl.repo import GitError, ProjectRepo

# Matched on basename so the gate holds whether the packet sits at the repo
# root (v0.1) or under docs/ (later restructures).
PROTECTED_BASENAMES = frozenset({"DESIGN.md", "PRINCIPLES.md", "AGENT_RULES.md"})
DEVPLAN_BASENAME = "DEVPLAN.md"

# A DEVPLAN task line: an optional-indent list bullet, a checkbox, then text.
_CHECKBOX_RE = re.compile(r"^(\s*[-*]\s+)\[([ xX])\](.*)$")
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

    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            # A removed line is a deletion (and the tell-tale of a reorder).
            return False, diff
        if tag == "insert":
            # Pure new lines — appended tasks. Existing lines keep their order,
            # so this never masks a reorder (that shows up as a delete).
            continue
        if tag == "replace":
            if (i2 - i1) != (j2 - j1):
                return False, diff
            for old, new in zip(before_lines[i1:i2], after_lines[j1:j2]):
                if not _legal_line_edit(old, new):
                    return False, diff
    return True, diff


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
