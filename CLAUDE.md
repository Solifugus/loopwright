# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install   # create .venv and pip install -e ".[dev]"
make test      # pytest (all tests)
make lint      # ruff check loopwright tests
make run       # run the loopwright CLI
.venv/bin/pytest tests/test_cli.py -k version   # run a single test
```

## Current State

Implementation is underway, tracked in `docs/DEVPLAN.md`. The package skeleton is `loopwright/` with subpackages `core/` (domain + config), `gitctl/` (git control), `vmctl/` (virsh + SSH), `notify/`, `web/` (FastAPI/HTMX UI), `orchestrator/` (run loop), `agent/` (Primary Agent). Read these before doing any work here:

- `docs/loopwright-design.md` — system design: components, git model, main loop, acceptance criteria
- `docs/loopwrite-host-and-vm-setup.md` — host/VM environment setup guide
- `docs/DEVPLAN.md` — the phased task list for building Loopwright itself; work tasks in order, one task per session, tick the checkbox and progress table in the same commit as the work

**Decided stack:** Python + FastAPI, server-rendered Jinja2 + HTMX UI (no JS build step); `virsh` subprocess wrappers + SSH for VM control; OpenAI API for the Primary Agent; Claude Code as the in-VM worker agent; ntfy.sh notifications; file-based persistence (no database in MVP). Anything touching VMs must support a `--dry-run` mode testable without real VMs.

Any implementation work must follow the architecture and rules laid out in those documents. If a decision would change the project's purpose, scope, architecture, or governing principles, ask the human first — that is the project's core rule: **"Autonomous execution, human-owned intent."**

## What Loopwright Is

Loopwright is a local, VM-supervised autonomous software development system. A human approves a "design packet" (DESIGN.md / DEVPLAN.md / TESTPLAN.md); Loopwright then autonomously implements, tests, deploy-validates, documents, and packages the project.

## Architecture

Three machines with strict role separation:

1. **Kubuntu Host** (trusted) — runs the Primary Agent UI, the Orchestrator, the authoritative bare git repos, VM control scripts (KVM/QEMU + virt-manager), status dashboard, and notification service (ntfy.sh/Gotify/Pushover/Telegram).
2. **Developer VM** (disposable) — runs Claude Code/Codex and build/test tooling; clones the repo, implements tasks, runs local tests, pushes checkpoints back to the host. Has passwordless sudo *inside the VM only*.
3. **Deployment VM** (disposable, minimal) — reverts to a clean snapshot for every major test, pulls the candidate, runs deploy scripts and acceptance/smoke tests to prove the product installs and works from scratch.

Key component boundaries:

- The **Primary Agent** (host) helps write the design packet and controls runs, but may never write production code, modify source files, or deploy.
- The **Orchestrator** (host) is the deterministic loop controller: AI proposes actions; the Orchestrator decides whether they are allowed. It manages project state, branches, VM snapshots, checkpoint tags, pause/resume, and notifications.
- **Worker agents** (Developer VM) do the actual coding/testing/review/documentation.

## Git Model (for managed projects)

Branches: `design/main` (human-approved packet) → `agent/work` / `agent/test` → `release/candidate` → `main` (human-approved). Checkpoints are tagged `checkpoint/NNNN-name`. Agents never push directly to `main`.

Files agents may modify: source, tests, deployment scripts, docs, DEVPLAN.md, TESTPLAN.md.
Files agents may **not** modify without human approval: DESIGN.md, PRINCIPLES.md, AGENT_RULES.md.

## Hard Security Rules

Agents must never: deploy to production, spend money, modify real accounts, expose or access real secrets, delete host files, push to main, contact external people, or accept legal/business terms.

## Planned Layout

The host directory structure will eventually be `doctrine/`, `orchestrator/`, `ui/`, `projects/`, `logs/`, `vm-control/`, `templates/`, `notifications/`. Shared doctrine (PRINCIPLES.md, AGENT_RULES.md, design-packet templates) lives in a separate `loopwright-doctrine` repo.

## MVP (v0.1) Scope

Local web UI, project creation wizard, design packet editor, git repo setup, VM start/stop/snapshot/revert scripts, Claude Code invocation on the Developer VM, deployment test on the Deployment VM, checkpoint tagging, log viewer, ntfy/Pushover notifications, pause/resume, and final report generation. Later features (LangGraph, multi-agent, Playwright testing, cost tracking, etc.) are explicitly out of MVP scope.

## Engineering Principles

From the design docs: prefer simplicity and boring dependencies, be explicit and minimize magic, automate everything repeatable, make failures visible, document reality (not aspiration), favor reversibility, and ensure deployment is reproducible with no undocumented manual steps.
