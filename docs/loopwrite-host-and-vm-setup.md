# Loopwright Host and VM Setup Guide

*Version 0.1 Draft*
*Date: 2026-07-05*

# Purpose

Loopwright is a local, VM-supervised autonomous software development system.

The human developer owns:

* Purpose
* Architecture
* Scope
* Design principles
* Final approval

Loopwright owns:

* Implementation
* Testing
* Deployment validation
* Documentation
* Packaging
* Reporting

The governing principle is:

> **Autonomous execution, human-owned intent.**

---

# Overall Architecture

```text
                 ┌─────────────────────┐
                 │ Kubuntu Host        │
                 │                     │
                 │  Primary Agent UI   │
                 │  Orchestrator       │
                 │  Git Control        │
                 │  Notifications      │
                 │  VM Control         │
                 └─────────┬───────────┘
                           │
                ┌──────────┴──────────┐
                │                     │
        ┌───────▼───────┐     ┌──────▼───────┐
        │ Developer VM  │     │ Deployment VM│
        │               │     │              │
        │ Claude Code   │     │ Clean System │
        │ Codex         │     │ Deploy Test  │
        │ Build Tools   │     │ Acceptance   │
        │ GUI Testing   │     │ Validation   │
        └───────────────┘     └──────────────┘
```

---

# Host Machine

## Operating System

* Kubuntu LTS
* KVM/QEMU
* virt-manager

## Suggested Directory Structure

```text
~/development/loopwright/

    doctrine/
    orchestrator/
    ui/
    projects/
    logs/
    vm-control/
    templates/
    notifications/
```

---

# Global Doctrine Repository

Create a separate repository:

```text
loopwright-doctrine/

    PRINCIPLES.md
    AGENT_RULES.md

    templates/
        DESIGN.template.md
        DEVPLAN.template.md
        TESTPLAN.template.md
```

This repository should be shared among all projects.

---

# Project Repository Structure

Each project should contain:

```text
docs/

    agent/
        PRINCIPLES.md
        AGENT_RULES.md

    project/
        DESIGN.md
        DEVPLAN.md
        TESTPLAN.md

src/
tests/
scripts/
examples/

README.md
CHANGELOG.md
FINAL_REPORT.md
```

---

# Project Design Packet

## DESIGN.md

Contains:

* project purpose
* intended users
* intended use cases
* scope
* exclusions
* architecture
* interfaces
* deliverables
* deployment requirements
* management tool requirements
* documentation requirements
* acceptance criteria
* project-specific forbidden paths

The coding agents may never modify this file.

---

## DEVPLAN.md

Contains:

* proposed implementation phases
* milestones
* sequencing
* dependencies

The coding agent may revise this file if necessary.

---

## TESTPLAN.md

Contains:

* unit tests
* integration tests
* deployment tests
* browser tests
* GUI tests
* negative tests
* acceptance tests
* documentation verification tests

The testing agent may expand this file.

---

## PRINCIPLES.md

Contains standing engineering principles such as:

* prefer simplicity
* prefer boring dependencies
* prefer explicitness
* minimize magic
* automate everything repeatable
* make failures visible
* document reality
* favor reversibility
* ensure deployment reproducibility

---

## AGENT_RULES.md

Contains:

* allowed actions
* forbidden actions
* stop conditions
* notification rules
* approval rules
* git rules
* deployment rules
* security rules

---

# Developer VM

## Purpose

The Developer VM acts as the software workshop.

Responsibilities:

* implementation
* dependency installation
* builds
* testing
* local deployment
* GUI testing
* documentation generation

---

## Operating System

Recommended:

* Kubuntu Minimal
* Xubuntu
* Ubuntu + XFCE

XFCE is recommended for low overhead.

---

## Suggested Resources

Initial recommendation:

```text
8 CPUs
16 GB RAM
100 GB disk
```

Adjust upward as needed.

---

## Software

Install:

```text
git
build-essential
curl
wget
python
node
docker/podman
playwright
chromium
firefox
xdotool
xvfb
openssh-server
```

Also install:

```text
Claude Code
Codex CLI
```

---

## Privileges

Create a normal user account:

```text
loopwright
```

Grant passwordless sudo:

```text
loopwright ALL=(ALL) NOPASSWD:ALL
```

Do not run the entire session as root.

---

## VM Snapshots

Create:

```text
base-os
toolchain-installed
ready-for-run
```

---

# Deployment VM

## Purpose

The Deployment VM validates that the product actually works.

Responsibilities:

* fresh deployment
* installation verification
* acceptance testing
* smoke testing
* deployment reproducibility

---

## Operating System

Recommended:

```text
Ubuntu Server LTS
```

No GUI initially.

---

## Suggested Resources

```text
4 CPUs
8 GB RAM
50 GB disk
```

---

## Software

Install only:

```text
git
curl
openssh-server
docker/podman (optional)
runtime dependencies
```

---

## VM Snapshots

Create:

```text
base-os
deployment-ready
clean-test
```

---

# Git Workflow

The host machine owns the authoritative repositories.

Suggested branches:

```text
design/main
agent/work
agent/test
release/candidate
main
```

Suggested tags:

```text
checkpoint/0001-bootstrap
checkpoint/0002-core
checkpoint/0003-tests
checkpoint/0004-deploy
checkpoint/0005-docs
```

---

# Repository Flow

```text
Host Bare Repo
        ↓
Developer VM Clone
        ↓
Agent Work
        ↓
Checkpoint Push
        ↓
Host Repository
        ↓
Deployment VM Clone
        ↓
Deployment Testing
```

Never develop directly on shared folders.

---

# Primary Agent

The Primary Agent runs on the host.

It may:

* discuss requirements
* develop DESIGN.md
* develop DEVPLAN.md
* develop TESTPLAN.md
* clarify intent
* define acceptance criteria

It may not:

* write production code
* deploy software
* modify implementation

Suggested implementation:

```text
OpenAI API
```

---

# Worker Agents

Worker agents run inside the Developer VM.

Examples:

```text
Claude Code
Codex CLI
Testing Agent
Reviewer Agent
Documentation Agent
```

---

# Main Loop

```text
1. Human creates project
2. Human and Primary Agent develop design packet
3. Human approves design packet
4. Orchestrator starts run
5. Developer VM snapshot created
6. Deployment VM snapshot created
7. Worker agent reads design packet
8. Implementation proceeds
9. Tests run
10. Checkpoint committed
11. Checkpoint pushed
12. Deployment VM deploys
13. Acceptance tests run
14. Review performed
15. Continue/retry/pause
16. Final candidate produced
17. Human reviews
18. Release approved
```

---

# Notifications

Notify on:

* run started
* checkpoint success
* deployment success
* repeated failures
* usage limit reached
* approval required
* final candidate complete

Initial notification options:

```text
ntfy.sh
Gotify
Telegram
Pushover
```

---

# Pause and Resume

When usage limits occur:

```text
save state
commit checkpoint
push checkpoint
mark paused
notify human
resume later
```

---

# Security Rules

Developer VM may:

* install packages
* use sudo
* modify local files
* create users
* run services

Agents may not:

* deploy to production
* spend money
* access real secrets
* delete host files
* push directly to main
* contact external persons
* make legal decisions

---

# Version 0.1 Goals

Version 0.1 should support:

* project creation
* design packet editing
* VM launch
* Claude Code execution
* checkpoint commits
* deployment testing
* notifications
* pause/resume
* progress viewing
* final report generation

---

# Future Features

Potential future additions:

* LangGraph orchestration
* multiple cooperating agents
* Playwright browser testing
* GUI testing
* visual verification
* cost accounting
* agent performance metrics
* reusable project templates
* WorkSplicer integration
* Conatus integration
* autonomous business document generation
* autonomous operations manuals
* autonomous cookbook/tutorial generation

```
```
