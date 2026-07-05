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

* Developer VM: coding, dependency installation, build work, local tests
* Deployment VM: clean deployment testing and acceptance validation

## Core Principle

```text
Autonomous execution, human-owned intent.
```

The agent may make implementation decisions when the design is silent, but it may not change the project’s purpose, scope, architecture, or governing principles without human approval.

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
* test execution
* deployment verification
* logging
* notifications
* pause/resume state
* final acceptance checks

The Orchestrator should be deterministic where possible. AI should propose actions; the Orchestrator decides whether those actions are allowed.

### 3. Developer VM

The Developer VM runs Claude Code, Codex, compilers, package managers, test tools, and build tools.

It may have passwordless sudo inside the VM.

It should:

* clone/pull the working repo
* implement tasks
* install dependencies
* run local tests
* commit meaningful checkpoints
* push checkpoints back to the host-controlled repo

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

docs/project/
  DESIGN.md
  DEVPLAN.md
  TESTPLAN.md

src/
tests/
scripts/
docs/
examples/
README.md
CHANGELOG.md
FINAL_REPORT.md
```

Shared global doctrine should live in a separate repo:

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

### DEVPLAN.md

Initial development plan.

The agent may revise this as work progresses, but revisions must remain consistent with DESIGN.md.

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

Matthew’s general engineering principles.

Examples:

* prefer simplicity
* prefer boring dependencies
* make deployment repeatable
* make failure visible
* avoid hidden manual steps
* document reality, not aspiration
* tests must prove intended purpose
* every important operation should be reversible

### AGENT_RULES.md

Operational rules shared across projects.

Includes:

* allowed actions
* forbidden actions
* stop conditions
* VM authority
* git rules
* dependency rules
* notification rules
* human approval rules

## Git Model

Suggested branches:

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
checkpoint/0003-tests-pass
checkpoint/0004-deploy-pass
checkpoint/0005-docs-complete
```

The coding agent may modify:

* source code
* tests
* deployment scripts
* docs
* DEVPLAN.md
* TESTPLAN.md

The coding agent may not modify without approval:

* DESIGN.md
* PRINCIPLES.md
* AGENT_RULES.md

## Main Loop

```text
1. Human and Primary Agent prepare design packet
2. Human approves design packet
3. Orchestrator creates project run
4. Developer VM snapshot is created
5. Deployment VM snapshot is created
6. Coding agent reads design packet
7. Coding agent implements next task
8. Testing agent updates or expands tests
9. Local tests run in Developer VM
10. Code is committed and pushed to host repo
11. Orchestrator tags checkpoint
12. Deployment VM pulls candidate
13. Deployment script runs from clean state
14. Acceptance tests run
15. Reviewer agent evaluates diff, logs, tests, and docs
16. Orchestrator decides continue, retry, pause, or finish
17. Final candidate is produced
18. Human reviews and approves
```

## Final Acceptance

A project is not done until:

* fresh deployment succeeds
* all tests pass
* management tool works if relevant
* README quickstart works
* docs match actual behavior
* deployment does not require undocumented manual steps
* final report is generated
* repo is clean and organized
* human approval is requested

## Notifications

Loopwright should notify Matthew on major events:

* run started
* checkpoint passed
* deployment passed
* repeated failure
* Claude/Codex limit reached
* human approval needed
* final candidate ready

Recommended first notification options:

* ntfy.sh
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

The Orchestrator should support:

* pause
* resume
* stop
* rollback to checkpoint
* inspect logs
* take manual control

## Security Model

The host machine is trusted.

The VMs are disposable workspaces.

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

## First MVP

Version 0.1 should include:

* local web UI
* project creation wizard
* design packet editor
* git repo setup
* VM start/stop/snapshot/revert scripts
* Claude Code invocation on Developer VM
* deployment test on Deployment VM
* checkpoint tagging
* log viewer
* ntfy/Pushover notification
* final report generation

## Later Features

* LangGraph orchestration
* multiple worker agents
* browser testing with Playwright
* visual test review
* project templates
* doctrine repo synchronization
* cost tracking
* agent performance history
* reusable deployment targets
* WorkSplicer integration
* Conatus integration
