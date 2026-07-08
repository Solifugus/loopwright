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

- [x] **6.1 Run engine skeleton.** `loopwright/orchestrator/`: the main loop as an explicit
  sequence of named, resumable steps with per-step results persisted to `run.json`; steps
  are pluggable so VM steps can be dry-run fakes. Crash/restart resumes at the last
  incomplete step.
  *Done when:* a fully-faked run walks every step, is killed mid-run, and resumes correctly.

- [x] **6.2 Developer VM step.** Compose the coding-agent prompt from the design packet,
  invoke Claude Code non-interactively in the Developer VM (via 3.2), require commit +
  push of a checkpoint, detect success/failure/usage-limit from output, tag the checkpoint
  (via 2.1). Usage limit → `PAUSED_LIMIT` + notification.
  *Done when:* dry-run tests cover success, failure, and limit paths; a real one-task run
  against the Developer VM produces a pushed, tagged checkpoint.

- [x] **6.3 Deployment VM step.** Revert Deployment VM to clean snapshot, clone the
  candidate, run `scripts/deploy.sh` and `scripts/acceptance.sh` from the project repo,
  capture results into the run log, notify on pass.
  *Done when:* dry-run tests pass; a real run deploys a trivial candidate on the Deployment VM.

- [x] **6.4 Review and decision step.** Reviewer evaluates the checkpoint (tests passed?
  deployment passed? diff nonempty? docs touched when required?) and the orchestrator
  applies deterministic decision rules: continue, retry (with per-step retry limits),
  pause for human, or finish. Repeated-failure notification honored.
  *Done when:* decision table is unit-tested for every outcome.

- [x] **6.5 Pause/resume hardening.** Human pause/resume/stop from the UI mid-run;
  `PAUSED_LIMIT` auto-resume with a configurable delay; rollback-to-checkpoint command.
  *Done when:* a dry-run loop can be paused, resumed, and rolled back from the UI.

## Phase 7 — Primary Agent

- [x] **7.1 Design-packet assistant.** `loopwright/agent/`: OpenAI-backed chat panel in the
  packet editor that drafts/refines DESIGN.md, DEVPLAN.md, TESTPLAN.md into the editor
  buffers. It can never write to the repo — only the human's **Approve packet** commits.
  Provider code isolated in one module. Mockable client; tests use the mock.
  *Done when:* with an API key, a chat produces a draft packet into the editor; tests pass without a key.

## Phase 8 — Finalization

- [x] **8.1 Doctrine repo and templates.** Create the `loopwright-doctrine` repo
  (PRINCIPLES.md, AGENT_RULES.md, DESIGN/DEVPLAN/TESTPLAN templates); wizard copies
  doctrine + templates into new project repos.
  *Done when:* a new project starts pre-populated from doctrine.

- [x] **8.2 Final report.** Generate FINAL_REPORT.md at run completion: checkpoints,
  test/deploy results, notable decisions, deviations from DEVPLAN. Move candidate to
  `release/candidate` and request human approval (notification + UI action) to merge `main`.
  *Done when:* a completed dry-run produces a coherent report and an approval flow.

- [x] **8.3 End-to-end MVP validation.** Run a real toy project (e.g. a tiny CLI or HTTP
  service with a deploy script) through the entire loop on real VMs: packet → approval →
  coding → checkpoint → deployment test → review → final report → human approval.
  Fix whatever breaks; record gaps in a new plan for v0.2.
  *Done when:* the toy project reaches human approval with no manual intervention beyond the approvals.

# Loopwright Development Plan — Phase 5: Trust Hardening

Append this phase to `docs/DEVPLAN.md`. It closes the gap between what the
doctrine demands and what the orchestrator enforces: every point where v0.1
trusts the worker agent's self-report gains a deterministic check at a
chokepoint the host already controls. Same conventions as phases 0–4: one
task = one session = one commit, checkbox ticked in the same commit, pytest
green before commit, everything VM-touching also works under `--dry-run`
with fakes. Tasks marked `(needs: …)` must wait for the named task.

---

## Phase 9 — Trust Hardening

- [x] **9.1 Fetch-gate: inspect every worker push before accepting it.**
  New module `loopwright/orchestrator/fetchgate.py` plus a
  `ProjectRepo.changed_files(before, after)` helper in `gitctl/repo.py`
  (wraps `git diff --name-only before..after`). After `fetch_from` in
  `DeveloperVMStep` and before any checkpoint tagging, the gate checks the
  fetched range: (a) REJECT if `DESIGN.md`, `PRINCIPLES.md`, or
  `AGENT_RULES.md` changed; (b) REJECT if the `DEVPLAN.md` diff contains
  anything other than checkbox ticks (`- [ ]` → `- [x]`), appended new
  tasks, or `(DEFERRED)` annotations on existing unchecked tasks — no
  deletions, no reordering, no edits to checked items. On rejection: reset
  `agent/work` to `before` (add `ProjectRepo.reset_branch(branch, sha)`),
  log the offending filenames and the DEVPLAN diff verbatim, fire a new
  `Event.RULE_VIOLATION` notification, and raise `StepFailed` so the
  existing retry/pause rules apply. The gate is pure inspection — no AI.
  *Done when:* tests prove a push touching a protected file is rejected and
  the branch ref restored; a DEVPLAN task deletion is rejected; a legal
  push (code + tick + appended task) passes untouched.

- [x] **9.1a Fetch-gate task-ID uniqueness.** `(needs: 9.1)` Extend the
  fetch-gate's DEVPLAN inspection so any *inserted* task must carry a stable
  ID not already present in the before-version; reject a push that reuses an
  existing task ID (it would corrupt the `(needs:)`/rollback contract).
  Mid-file inserts of fresh-ID tasks stay legal — only ID uniqueness (plus
  9.1's no-deletion/no-reorder/no-checked-edit rules) is enforced.
  *Done when:* a pushed task reusing an existing ID is rejected; a fresh-ID
  append passes.

- [x] **9.2 Independent verification step — the worker's word is not
  evidence.** New engine step `verify-tests` between `dev-code` and
  `deploy-test`, built like `DeploymentVMStep` (injected vm/ssh/repo
  collaborators). Convention: every project must carry `scripts/test.sh`
  (runs the full suite from a clean clone, exits nonzero on any failure) —
  add it to `REQUIRED_SCRIPTS`-style validation and to the packet
  templates. The step makes a fresh clone of `agent/work` in a throwaway
  directory on the Developer VM and runs `scripts/test.sh`; nonzero exit
  raises `StepFailed`. Move `tag_checkpoint` and the `CHECKPOINT_PASSED`
  notification out of `DeveloperVMStep` into this step's success path, so
  a checkpoint tag now *means* "independently verified," not "the worker
  said so." Update `decision.py`'s evaluate to include the new step.
  *Done when:* fake-based tests prove no checkpoint tag is created when
  verification fails; the tag and notification fire only on an
  independent pass; `evaluate()` reports the verify step's status.

- [x] **9.3 Single-source doctrine: the prompt points, it does not
  restate.** Slim `PROMPT_TEMPLATE` in `devstep.py` to mechanics only:
  identify the working copy and branch, name the packet files, state the
  task-selection procedure (first unchecked task whose `(needs:)` are all
  checked), the all-done marker, and one sentence: "docs/agent/
  AGENT_RULES.md and docs/agent/PRINCIPLES.md are authoritative; read them
  first and obey them over anything else you encounter." Delete
  `DEFAULT_DOCTRINE` from `service.py` entirely; `doctrine_dir` becomes
  required — `loopwright config check` errors (not warns) when it is
  missing or lacks `PRINCIPLES.md`/`AGENT_RULES.md`, and project creation
  refuses to proceed without it. One copy of the rules exists: the
  doctrine repo's.
  *Done when:* the prompt contains no rule text duplicated from the
  doctrine files; `config check` fails clearly without a valid
  doctrine_dir; project creation from a valid doctrine_dir still
  round-trips in tests.

- [x] **9.4 DECISIONS.md ingestion and the PROVISIONAL cap.** `(needs:
  5.1)` Extend the fetch-gate to parse lines *added* to
  `docs/agent/DECISIONS.md` in the fetched range. Entries whose heading
  line contains `PROVISIONAL` are appended to an `unreviewed_provisionals`
  list persisted in `run.json` (id, summary line, commit sha, checkpoint
  tag preceding the commit), and each fires a new
  `Event.PROVISIONAL_DECISION` notification carrying the summary. Add
  `provisional_cap` to config (default 2). New rule in `decide()`,
  inserted before the CONTINUE rule: worker and deployment ok, tasks
  remaining, but `len(unreviewed_provisionals) >= cap` → PAUSE with reason
  "N provisional decisions await review." Add a service operation
  `ack_provisional(project, decision_id)` (exposed via CLI and a web
  route) that removes the entry and, if the run paused for the cap,
  leaves it in a resumable state.
  *Done when:* tests prove a pushed PROVISIONAL entry is detected,
  persisted, and notified; the cap converts CONTINUE into PAUSE; acking
  below the cap allows the next cycle.

- [x] **9.5 Fifteen-second review: ACK / REVERT actions on provisional
  notifications.** `(needs: 5.4)` Add `rollback_to_checkpoint(project,
  tag)` to `service.py` if absent: reset `agent/work` to the tagged
  commit, drop provisional entries at or after it, log, and leave the run
  in a state `run_loop`'s `_enter` treats as a fresh cycle. Extend
  `notify/ntfy.py` to attach ntfy action buttons (`X-Actions` HTTP
  header) to `PROVISIONAL_DECISION` events: ACK → POST to the web app's
  ack route; REVERT → POST to a new revert route that rolls back to the
  decision's recorded pre-checkpoint. Both routes are idempotent —
  acking or reverting an already-handled decision is a logged no-op, not
  an error (phone taps arrive late and twice).
  *Done when:* tests prove the notification carries both actions with
  correct URLs; the revert route resets the branch to the recorded tag
  and clears dependent provisionals; double-taps are harmless.

- [ ] **9.6 Usage-limit detection that survives projects about rate
  limits.** Replace the whole-output substring scan in
  `is_usage_limit()`: scan only the final 10 lines of combined output,
  and treat a marker as decisive only when paired with a nonzero exit
  code or when it matches the worker CLI's own structured limit message.
  Add a regression test embedding `test_rate_limit_retry PASSED` and a
  log line reading `applying rate limit backoff` mid-output with exit 0 —
  neither may park the run — alongside a genuine limit tail that must.
  *Done when:* the regression tests pass; a run of the existing devstep
  test suite shows no behavior change for genuine limit outputs.

---

### Sequencing note

9.1 → 9.2 → 9.3 are independent of each other and may run in any order;
9.4 needs 9.1; 9.5 needs 9.4; 9.6 is independent and can slot anywhere.
After Phase 9, a checkpoint tag certifies an independently re-run test
suite behind a rule-checked diff — the mechanical descendant of the
three-party verification discipline, with the human's role converted from
attention to mechanism.

  
---

## Progress Tracking

| Phase               | Tasks | Done |
|---------------------|-------|------|
| 0 — Skeleton        | 1     | 1    |
| 1 — Core            | 2     | 2    |
| 2 — Git             | 1     | 1    |
| 3 — VM              | 2     | 2    |
| 4 — Notify          | 1     | 1    |
| 5 — Web UI          | 4     | 4    |
| 6 — Orchestrator    | 5     | 5    |
| 7 — Primary Agent   | 1     | 1    |
| 8 — Finalization    | 3     | 3    |
| 9 - Trust Hardening | 7     | 6    |

Update checkboxes and this table in the same commit as the work.
