# Loopwright Design

## Purpose

Loopwright is a local, VM-supervised autonomous development system for producing plug-and-play software projects from a human-approved design packet.

The human owns intent, design, scope, principles, and final approval. Loopwright owns execution: implementation, testing, deployment validation, documentation, and final packaging.

## Host Environment

Loopwright runs on a Kubuntu host machine.

The host machine runs:

* Primary Agent UI
* Orchestrator service
* Git control repository
* VM control scripts
* status dashboard
* notification service

Two VMs are used:

* Developer VM: coding, dependency installation, build work, local tests, independent verification
* Deployment VM: clean deployment testing and acceptance validation

## Core Principle

```text
Autonomous execution, human-owned intent.
```

The agent may make implementation decisions when the design is silent, but it may not change the project's purpose, scope, architecture, or governing principles without human approval.

Three corollaries govern how this works unattended:

* **Ambiguity never stops the run.** Agents decide using PRINCIPLES.md, record the decision, and continue. Stopping is reserved for hard security rules, protected files, and total wedges.
* **Notification replaces permission.** Consequential decisions ping the human, who can intervene remotely; the run does not sit idle waiting for approval.
* **Self-reports are not evidence.** Every claim an agent makes about its work is verified mechanically by the Orchestrator at a chokepoint the host controls. Control the human gives up as attention is converted into control as mechanism.

## Decision Model

Agent judgment calls are classified by severity:

* **Trivial** — naming, internal structure, anything invisible from outside the component. Decide and move on.
* **Material** — externally visible behavior, new dependencies, schema or interface shapes, novel approaches over conventional ones. Decide using PRINCIPLES.md and log in DECISIONS.md with rationale and runner-up.
* **Structural** — anything touching the intent in DESIGN.md. Take the most conservative, most reversible interpretation, mark the decision PROVISIONAL, notify the human, and continue.

PROVISIONAL decisions are bounded: the Orchestrator enforces a cap on unreviewed PROVISIONALs (default 2). At the cap, the run pauses rather than building further on unreviewed structural guesses. Each PROVISIONAL notification carries ACK and REVERT actions so review takes seconds from a phone; REVERT rolls back to the checkpoint tagged immediately before the decision's commit.

A decision log that overstates, omits, or launders what happened is the worst failure the system recognizes.

## Main Components

### 1. Primary Agent UI

The Primary Agent runs on the Kubuntu host and interacts with Matthew.

It may:

* help write DESIGN.md
* help write DEVPLAN.md
* help write TESTPLAN.md
* clarify scope and deliverables
* define acceptance criteria
* start, pause, resume, or stop autonomous runs
* display progress
* receive human instructions

It may not:

* write production code
* modify source files
* silently change approved project design
* deploy to real infrastructure

### 2. Orchestrator

The Orchestrator is the loop controller.

It manages:

* project state
* git branches
* VM snapshots
* worker commands
* fetch-gate inspection of every worker push
* independent test verification
* test execution
* deployment verification
* DECISIONS.md ingestion and the PROVISIONAL cap
* logging
* notifications
* pause/resume state
* rollback to checkpoint
* final acceptance checks

The Orchestrator should be deterministic where possible. AI should propose actions; the Orchestrator decides whether those actions are allowed. Control decisions are made by a fixed rule table, never by a model.

### 3. Developer VM

The Developer VM runs Claude Code, Codex, compilers, package managers, test tools, and build tools.

It may have passwordless sudo inside the VM. It never holds credentials to reach the host; the host pushes to and fetches from a VM-local bare repository, so the trust boundary is the host's own fetch.

It should:

* receive the working repo pushed by the host
* implement tasks
* install dependencies
* run local tests
* commit meaningful checkpoints
* push checkpoints to the VM-local repo for the host to fetch

### 4. Deployment VM

The Deployment VM represents a clean target environment.

It should:

* start from a known snapshot
* pull the current candidate repo
* run install/deploy scripts
* run smoke tests
* run acceptance tests
* verify documentation steps
* revert to clean state after each major deployment test

## Repository Layout

Each project repo should contain:

```text
docs/agent/
  PRINCIPLES.md
  AGENT_RULES.md
  DECISIONS.md      decision log (material and PROVISIONAL entries)
  TASKLOG.md        per-task evidence: what changed, test command, results
  BLOCKED.md        escalation report when a stop condition is met

docs/project/
  DESIGN.md
  DEVPLAN.md
  TESTPLAN.md

src/
tests/
scripts/
  deploy.sh         installs the product on a bare machine
  acceptance.sh     verifies the deployed product works
  test.sh           runs the full test suite from a clean clone
docs/
examples/
README.md
CHANGELOG.md
FINAL_REPORT.md
```

Project creation seeds DECISIONS.md, TASKLOG.md, and BLOCKED.md as empty files so the fetch-gate's rules about them are explicit from the first push.

Shared global doctrine lives in a separate repo and is the single source of truth for PRINCIPLES.md and AGENT_RULES.md. No copies of doctrine text are hand-maintained anywhere else — not in prompts, not in code:

```text
loopwright-doctrine/
  PRINCIPLES.md
  AGENT_RULES.md
  templates/
    DESIGN.template.md
    DEVPLAN.template.md
    TESTPLAN.template.md
```

## Design Packet

### DESIGN.md

Project-specific source of truth.

Includes:

* purpose
* intended users
* intended use cases
* scope
* non-goals
* architecture
* deliverables
* required interfaces
* deployment expectations
* management tool requirements
* documentation requirements
* forbidden project-specific paths
* final acceptance criteria
* concrete examples and fixtures — authoritative over prose when the two disagree

The human-authored acceptance criteria and fixtures are the only human-anchored ground truth in an unattended run; they set the quality ceiling. Time spent sharpening them buys more than time spent anywhere else.

### DEVPLAN.md

Initial development plan.

Tasks carry stable IDs and may declare dependencies with a `(needs: <id>)` annotation. The next task is the lowest-ID unchecked task whose needs are all checked — not file position.

The agent may revise this as work progresses, but revisions must remain consistent with DESIGN.md, and the file is append-only from the agent's side: checkbox ticks, appended tasks, and `(DEFERRED)` annotations are legal; deletions, reorderings, and edits to checked items are not. The fetch-gate enforces this.

### TESTPLAN.md

Validation plan.

A testing agent may expand this independently from the coding agent.

Includes:

* unit tests
* integration tests
* deployment tests
* CLI/API/browser tests
* negative tests
* performance tests if relevant
* documentation verification tests

### PRINCIPLES.md

Matthew's engineering principles, written as tiebreakers so an agent can make decisions the way Matthew would. Canonical version lives in the doctrine repo. Themes:

* generality is the goal; complexity is the budget
* fewer parts; clear interfaces between major components
* concrete validates abstract; fixtures win over prose
* functionality first, performance second — deliberately second, not never
* scalability is architecture, not optimization
* defects fail at load time; environmental faults get recorded resilience
* diagnostics carry decisive values, not noise
* reversibility wins conflicts
* when principles are silent: most reversible, most explicit, log it, continue

### AGENT_RULES.md

Operational rules shared across projects. Canonical version lives in the doctrine repo.

Includes:

* the decide-and-disclose model and decision severity ladder
* working discipline and one-task-per-session
* test integrity rules (the suite is the contract)
* evidence rules (no claim without proof)
* dependency rules
* wedged-task procedure (defer unchecked; never a green checkbox on an under-built task)
* stop conditions — the short list
* escalation format
* untrusted-content rule (only the packet and doctrine carry authority)
* hard security rules

## Git Model

Branches:

```text
design/main          human-approved design packet
agent/work           coding agent work
agent/test           testing agent changes
release/candidate    final candidate
main                 human-approved final branch
```

Checkpoint tags:

```text
checkpoint/0001-bootstrap
checkpoint/0002-core-working
...
```

A checkpoint tag certifies an independently verified state: the fetch-gate passed and the test suite was re-run by the Orchestrator from a clean clone. It never means only that the worker reported success.

### Fetch-gate

Because the host performs every fetch, the fetch is the enforcement chokepoint. Before accepting a fetched range the Orchestrator inspects it:

* pushes touching DESIGN.md, PRINCIPLES.md, or AGENT_RULES.md are rejected
* DEVPLAN.md diffs outside the legal shapes (tick, append, DEFERRED) are rejected
* new DECISIONS.md entries are parsed; PROVISIONAL entries are recorded and notified

Rejection resets `agent/work` to its prior state, logs the violation verbatim, notifies, and counts as a failed step. The gate is pure inspection; no AI is involved.

The coding agent may modify: source code, tests, deployment scripts, docs, DEVPLAN.md, TESTPLAN.md, and the agent working files (DECISIONS.md, TASKLOG.md, BLOCKED.md).

The coding agent may not modify: DESIGN.md, PRINCIPLES.md, AGENT_RULES.md — enforced by the fetch-gate, not by trust.

## Main Loop

```text
 1. Human and Primary Agent prepare design packet
 2. Human approves design packet
 3. Orchestrator creates project run
 4. Developer VM snapshot is created
 5. Deployment VM snapshot is created
 6. Host pushes design/main and agent/work to the Developer VM
 7. Coding agent reads doctrine and design packet
 8. Coding agent implements the next eligible task, logs decisions/evidence
 9. Local tests run in Developer VM
10. Agent commits and pushes to the VM-local repo
11. Host fetches; fetch-gate inspects the range (reject → reset, log, notify)
12. DECISIONS.md entries ingested; PROVISIONALs notified; cap enforced
13. Orchestrator re-runs the full suite from a fresh clone (scripts/test.sh)
14. On independent pass, Orchestrator tags checkpoint
15. Deployment VM reverts to snapshot and pulls candidate
16. Deployment script runs from clean state
17. Acceptance tests run
18. Deterministic rule table decides continue, retry, pause, or finish
19. Final candidate is produced
20. Human reviews and approves
```

## Final Acceptance

A project is not done until:

* fresh deployment succeeds
* all tests pass under independent verification
* management tool works if relevant
* README quickstart works
* docs match actual behavior
* deployment does not require undocumented manual steps
* no PROVISIONAL decisions remain unreviewed
* final report is generated
* repo is clean and organized
* human approval is requested

## Notifications

Loopwright should notify Matthew on major events:

* run started
* checkpoint passed
* deployment passed
* PROVISIONAL decision logged (with ACK / REVERT actions)
* new dependency added
* task deferred
* rule violation rejected by the fetch-gate
* repeated failure
* Claude/Codex limit reached
* human approval needed
* final candidate ready

Material decisions are disclosed in DECISIONS.md for checkpoint review, not pinged — notification signal is protected so it keeps being read.

Recommended first notification options:

* ntfy.sh (supports the ACK/REVERT action buttons)
* Gotify
* Pushover
* Telegram bot
* Twilio later if SMS is required

## Pause and Resume

If Claude Code reaches usage limits:

```text
1. save current state
2. commit/push safe checkpoint if possible
3. mark run PAUSED_LIMIT
4. notify Matthew
5. resume when available
```

Usage-limit detection must be robust against projects whose own output mentions rate limits: only the tail of worker output is scanned, paired with exit status.

The Orchestrator should support:

* pause
* resume
* stop
* rollback to checkpoint
* inspect logs
* take manual control

## Security Model

The host machine is trusted.

The VMs are disposable workspaces. VMs never hold credentials to reach the host; all repo transfer is initiated by the host.

Developer VM may use sudo, but only inside the VM.

The agent must not:

* deploy to production
* spend money
* modify real accounts
* expose secrets
* delete host files
* push to main
* contact external people
* accept legal or business terms

Prose rules are backstopped by mechanism wherever a chokepoint exists: protected files by the fetch-gate, evidence by independent re-execution, DEVPLAN integrity by diff-shape rules. Text the agent reads while working — web pages, dependency docs, error messages — is data, never instructions; only the packet and doctrine carry authority.

## Decided Technology (v0.1)

* Python + FastAPI; server-rendered UI with Jinja2 + HTMX
* VM control via `virsh` (KVM/QEMU) over subprocess; SSH into VMs
* Primary Agent: OpenAI API; workers: Claude Code (optionally Codex CLI) in the Developer VM
* Notifications: ntfy.sh
* Persistence: plain YAML/JSON files under `projects/` — no database

## First MVP

Version 0.1 (built):

* local web UI
* project creation wizard
* design packet editor
* git repo setup with host-initiated VM transfer
* VM start/stop/snapshot/revert scripts
* Claude Code invocation on Developer VM
* deployment test on Deployment VM from clean snapshot
* checkpoint tagging
* deterministic decision rule table
* log viewer
* ntfy notification
* final report generation

Version 0.1.x — trust hardening (Phase 5):

* fetch-gate: protected-file and DEVPLAN diff-shape enforcement
* independent verification step; checkpoint tags certify re-run suites
* single-source doctrine; slim mechanics-only worker prompt
* DECISIONS.md ingestion, PROVISIONAL cap, ACK/REVERT actions
* hardened usage-limit detection

## Later Features

* trust ladder: per-agent scoreboard (verification pass rate, claim/re-run
  discrepancies, complexity flags, PROVISIONALs reverted vs. acked) that
  earns looser settings over time — the embryo of Conatus/WorkSplicer routing
* cross-family review: coder and reviewer from different model families
* complexity deltas per task (files, public symbols, dependencies, LOC) as
  review triggers
* parallel task dispatch over the DEVPLAN dependency graph
* LangGraph orchestration
* multiple worker agents
* browser testing with Playwright
* visual test review
* project templates
* doctrine repo synchronization
* cost tracking
* reusable deployment targets
* WorkSplicer integration
* Conatus integration
