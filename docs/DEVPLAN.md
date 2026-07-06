# Loopwright Development Plan

This is the development plan for building Loopwright itself (v0.1 MVP as scoped in
`loopwright-design.md`). It is organized into phases of small, independently
completable tasks. **Each task is sized to be achievable in a single Claude Code
session**: it names its deliverables and a concrete "done when" test, and ends with a
commit.

## Decided Technology

- **Language/stack:** Python + FastAPI, server-rendered UI with Jinja2 + HTMX (no JS build step)
- **VM control:** wrap `virsh` (KVM/QEMU) via subprocess; SSH into VMs for command execution
- **Primary Agent LLM:** OpenAI API
- **Worker agents:** Claude Code (and optionally Codex CLI) running inside the Developer VM
- **Notifications:** ntfy.sh first
- **Persistence:** plain files (YAML/JSON) under `projects/` — no database for MVP

## Working Conventions

- Work tasks in order within a phase; phases 2–4 are independent of each other and may be reordered.
- One task = one session = at least one commit. Mark the task's checkbox in this file in the same commit.
- Every task with logic gets pytest coverage; `pytest` must pass before commit.
- Anything touching VMs must also work in `--dry-run` mode so it can be tested without real VMs.

---

## Phase 0 — Project Skeleton

- [x] **0.1 Python scaffold.** Create `pyproject.toml` (project name `loopwright`), package
  layout `loopwright/` with subpackage stubs (`core/`, `gitctl/`, `vmctl/`, `notify/`,
  `web/`, `orchestrator/`, `agent/`), dev tooling (ruff, pytest), a `loopwright` CLI
  entry point (`loopwright --version`), and a `Makefile` (or justfile) with `install`,
  `test`, `lint`, `run` targets. Update CLAUDE.md with the real commands.
  *Done when:* `make install && make test && make lint` all pass; `loopwright --version` prints.

## Phase 1 — Core Domain and State

- [x] **1.1 Project model and state store.** `loopwright/core/`: a `Project` with metadata
  (name, repo path, created date) persisted as YAML in `projects/<name>/project.yaml`,
  and a `Run` with an explicit state machine
  (`DRAFT → READY → RUNNING → PAUSED | PAUSED_LIMIT → RUNNING → REVIEW → DONE | FAILED | STOPPED`)
  persisted as `projects/<name>/run.json`. Illegal transitions raise. Full pytest coverage
  of transitions and round-trip persistence.
  *Done when:* tests prove legal/illegal transitions and reload-from-disk equality.

- [x] **1.2 Configuration.** `loopwright/core/config.py`: load host config from
  `~/.config/loopwright/config.yaml` (paths, VM names, SSH targets, ntfy topic, OpenAI key
  env var name) with validated defaults and a `loopwright config check` CLI command.
  *Done when:* missing/invalid config produces clear errors; example config file committed.

## Phase 2 — Git Control

- [x] **2.1 Repo management.** `loopwright/gitctl/`: create a bare authoritative repo per
  project, initialize the branch model (`design/main`, `agent/work`, `agent/test`,
  `release/candidate`, `main`), commit design-packet files to `design/main`, and tag
  checkpoints (`checkpoint/NNNN-slug`, auto-incrementing). All via subprocess `git`;
  tests run against temp directories.
  *Done when:* tests create a project repo, commit a packet, tag two checkpoints, and list them.

## Phase 3 — VM Control

- [x] **3.1 virsh wrapper.** `loopwright/vmctl/`: start/stop/status/snapshot-create/
  snapshot-revert for named VMs via `virsh`, plus a `DryRunVM` fake that records calls.
  CLI: `loopwright vm status|start|stop|snapshot|revert <vm>`.
  *Done when:* dry-run tests pass; CLI works against a real VM if one exists (manual check).

- [x] **3.2 VM command execution.** SSH command runner (subprocess `ssh`, key-based) with
  timeout, captured output, and exit codes; helper to clone/pull the host repo inside a VM.
  Dry-run fake included.
  *Done when:* dry-run tests pass; a real `loopwright vm exec <vm> 'echo hi'` works (manual check).

## Phase 4 — Notifications

- [x] **4.1 ntfy notifier.** `loopwright/notify/`: event types from the design doc
  (run started, checkpoint passed, deployment passed, repeated failure, limit reached,
  approval needed, candidate ready) posted to a configurable ntfy topic; a `NullNotifier`
  for tests; `loopwright notify test` CLI command.
  *Done when:* unit tests pass; a real ntfy message arrives on the configured topic (manual check).

## Phase 5 — Web UI

- [x] **5.1 FastAPI skeleton.** `loopwright/web/`: app factory, Jinja2 + HTMX base layout,
  project list and project detail pages reading from the Phase 1 store. `make run` serves
  on localhost.
  *Done when:* creating a project via CLI makes it appear in the browser.

- [x] **5.2 Creation wizard and packet editor.** New-project wizard (name → creates store
  entry + git repo via Phase 2) and a design-packet editor: edit DESIGN.md / DEVPLAN.md /
  TESTPLAN.md in the browser; an explicit **Approve packet** action commits them to
  `design/main` and moves the run to `READY`.
  *Done when:* a project can go from nothing to an approved packet entirely in the browser.

- [x] **5.3 Run controls and dashboard.** Start / pause / resume / stop buttons wired to
  the run state machine, current-state badge, checkpoint list, HTMX polling for updates.
  (Buttons drive state + notifications only; the real loop arrives in Phase 6.)
  *Done when:* state changes from the UI persist and notify.

- [x] **5.4 Log viewer.** Structured run log (`projects/<name>/logs/`, JSONL with timestamp,
  step, level) written by orchestrator components, and a UI page that tails/filters it.
  *Done when:* log entries written by any component appear in the browser.

## Phase 6 — Orchestrator Loop

- [ ] **6.1 Run engine skeleton.** `loopwright/orchestrator/`: the main loop as an explicit
  sequence of named, resumable steps with per-step results persisted to `run.json`; steps
  are pluggable so VM steps can be dry-run fakes. Crash/restart resumes at the last
  incomplete step.
  *Done when:* a fully-faked run walks every step, is killed mid-run, and resumes correctly.

- [ ] **6.2 Developer VM step.** Compose the coding-agent prompt from the design packet,
  invoke Claude Code non-interactively in the Developer VM (via 3.2), require commit +
  push of a checkpoint, detect success/failure/usage-limit from output, tag the checkpoint
  (via 2.1). Usage limit → `PAUSED_LIMIT` + notification.
  *Done when:* dry-run tests cover success, failure, and limit paths; a real one-task run
  against the Developer VM produces a pushed, tagged checkpoint.

- [ ] **6.3 Deployment VM step.** Revert Deployment VM to clean snapshot, clone the
  candidate, run `scripts/deploy.sh` and `scripts/acceptance.sh` from the project repo,
  capture results into the run log, notify on pass.
  *Done when:* dry-run tests pass; a real run deploys a trivial candidate on the Deployment VM.

- [ ] **6.4 Review and decision step.** Reviewer evaluates the checkpoint (tests passed?
  deployment passed? diff nonempty? docs touched when required?) and the orchestrator
  applies deterministic decision rules: continue, retry (with per-step retry limits),
  pause for human, or finish. Repeated-failure notification honored.
  *Done when:* decision table is unit-tested for every outcome.

- [ ] **6.5 Pause/resume hardening.** Human pause/resume/stop from the UI mid-run;
  `PAUSED_LIMIT` auto-resume with a configurable delay; rollback-to-checkpoint command.
  *Done when:* a dry-run loop can be paused, resumed, and rolled back from the UI.

## Phase 7 — Primary Agent

- [ ] **7.1 Design-packet assistant.** `loopwright/agent/`: OpenAI-backed chat panel in the
  packet editor that drafts/refines DESIGN.md, DEVPLAN.md, TESTPLAN.md into the editor
  buffers. It can never write to the repo — only the human's **Approve packet** commits.
  Provider code isolated in one module. Mockable client; tests use the mock.
  *Done when:* with an API key, a chat produces a draft packet into the editor; tests pass without a key.

## Phase 8 — Finalization

- [ ] **8.1 Doctrine repo and templates.** Create the `loopwright-doctrine` repo
  (PRINCIPLES.md, AGENT_RULES.md, DESIGN/DEVPLAN/TESTPLAN templates); wizard copies
  doctrine + templates into new project repos.
  *Done when:* a new project starts pre-populated from doctrine.

- [ ] **8.2 Final report.** Generate FINAL_REPORT.md at run completion: checkpoints,
  test/deploy results, notable decisions, deviations from DEVPLAN. Move candidate to
  `release/candidate` and request human approval (notification + UI action) to merge `main`.
  *Done when:* a completed dry-run produces a coherent report and an approval flow.

- [ ] **8.3 End-to-end MVP validation.** Run a real toy project (e.g. a tiny CLI or HTTP
  service with a deploy script) through the entire loop on real VMs: packet → approval →
  coding → checkpoint → deployment test → review → final report → human approval.
  Fix whatever breaks; record gaps in a new plan for v0.2.
  *Done when:* the toy project reaches human approval with no manual intervention beyond the approvals.

---

## Progress Tracking

| Phase | Tasks | Done |
|-------|-------|------|
| 0 — Skeleton | 1 | 1 |
| 1 — Core | 2 | 2 |
| 2 — Git | 1 | 1 |
| 3 — VM | 2 | 2 |
| 4 — Notify | 1 | 1 |
| 5 — Web UI | 4 | 4 |
| 6 — Orchestrator | 5 | 0 |
| 7 — Primary Agent | 1 | 0 |
| 8 — Finalization | 3 | 0 |

Update checkboxes and this table in the same commit as the work.
