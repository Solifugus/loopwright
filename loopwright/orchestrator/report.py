"""FINAL_REPORT.md generation.

The report is assembled purely from persisted facts — run.json, the run log,
the repo's checkpoints, and DEVPLAN.md as the workers left it — so it
documents reality, not aspiration (PRINCIPLES.md rule 5).
"""

import re
from datetime import datetime, timezone

from loopwright.core.model import ProjectStore
from loopwright.core.runlog import RunLog
from loopwright.gitctl.repo import WORK_BRANCH, GitError, ProjectRepo

CHECKED_RE = re.compile(r"^\s*-\s*\[x\]\s*(.+)$", re.MULTILINE | re.IGNORECASE)
UNCHECKED_RE = re.compile(r"^\s*-\s*\[ \]\s*(.+)$", re.MULTILINE)


def generate_report(store: ProjectStore, name: str) -> str:
    project = store.load_project(name)
    run = store.load_run(name)
    repo = ProjectRepo(project.repo_path)
    log = RunLog(store.project_dir(name) / "logs")

    checkpoints = repo.checkpoints()
    decisions = log.read(step="review")
    # engine bookkeeping ("step started/completed") isn't a deployment result
    deploy_entries = [
        entry
        for entry in log.read(step="deploy-test")
        if not entry.get("message", "").startswith("step ")
    ]
    try:
        devplan = repo.show(WORK_BRANCH, "DEVPLAN.md")
    except GitError:
        devplan = ""
    done_tasks = CHECKED_RE.findall(devplan)
    open_tasks = UNCHECKED_RE.findall(devplan)

    lines = [
        f"# Final Report — {project.name}",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by Loopwright.",
        "",
        "## Summary",
        "",
        f"- Run state: **{run.state.value}** after **{run.cycle + 1}** cycle(s)",
        f"- Checkpoints tagged: **{len(checkpoints)}**",
        f"- DEVPLAN tasks: **{len(done_tasks)} complete**, **{len(open_tasks)} open**",
        f"- Project created: {project.created}",
        "",
        "## Checkpoints",
        "",
    ]
    if checkpoints:
        lines.extend(f"- `{tag}`" for tag in checkpoints)
    else:
        lines.append("- none")

    lines += ["", "## Deployment results", ""]
    if deploy_entries:
        for entry in deploy_entries:
            marker = "✗" if entry.get("level") == "error" else "✓"
            lines.append(f"- {marker} {entry.get('ts', '')} — {entry.get('message', '')}")
    else:
        lines.append("- no deployment test entries recorded")

    lines += ["", "## Orchestrator decisions", ""]
    if decisions:
        lines.extend(
            f"- {entry.get('ts', '')} — {entry.get('message', '')}" for entry in decisions
        )
    else:
        lines.append("- none recorded")

    lines += ["", "## Deviations from DEVPLAN", ""]
    if open_tasks:
        lines.append("The following planned tasks were **not** completed:")
        lines.append("")
        lines.extend(f"- [ ] {task}" for task in open_tasks)
    else:
        lines.append("None — every DEVPLAN task is checked off.")

    lines += [
        "",
        "## Run history",
        "",
        *(
            f"- {entry['at']}: {entry['from']} → {entry['to']}"
            for entry in run.history
        ),
        "",
        "---",
        "",
        "Approving this release fast-forwards `main` to `release/candidate`.",
        "",
    ]
    return "\n".join(lines)
