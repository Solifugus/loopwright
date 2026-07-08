# Loopwright

A local, VM-supervised autonomous software development system.

> **Autonomous execution, human-owned intent.**
> You own purpose, design, scope, principles, and final approval. Loopwright owns
> implementation, testing, deployment validation, documentation, and packaging.

You write a **design packet** (what to build, how it's verified, and the acceptance
criteria). Loopwright then drives a coding agent to implement it task by task inside a
disposable VM, independently re-runs the test suite, proves the result installs and
works from scratch on a second clean VM, and hands you a release candidate to approve —
notifying your phone when it needs a decision. Every claim the agent makes is checked by
mechanism at a chokepoint the host controls; the agent can never touch your host, your
secrets, or your `main` branch.

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [1. Install Loopwright](#1-install-loopwright-host)
- [2. Create the doctrine repository](#2-create-the-doctrine-repository-required)
- [3. Configure the host](#3-configure-the-host)
- [4. Set up the two VMs](#4-set-up-the-two-vms)
- [5. Verify the setup](#5-verify-the-setup)
- [Building a web application, end to end](#building-a-web-application-end-to-end)
- [The project scripts: your deployment contract](#the-project-scripts-your-deployment-contract)
- [Operating a run](#operating-a-run)
- [CLI reference](#cli-reference)
- [Configuration reference](#configuration-reference)
- [Current limitations](#current-limitations-v01)
- [Development](#development)

---

## How it works

Three machines with strict role separation:

```
        ┌─────────────────────────────┐
        │ Kubuntu Host (trusted)      │
        │  • Web UI + Primary Agent   │
        │  • Orchestrator (the loop)  │
        │  • Authoritative git repos  │
        │  • VM control, notifications│
        └──────────────┬──────────────┘
             pushes / fetches (host-initiated only)
        ┌──────────────┴──────────────┐
   ┌────▼────────────┐        ┌────────▼─────────┐
   │ Developer VM    │        │ Deployment VM    │
   │ (disposable)    │        │ (disposable)     │
   │  Claude Code    │        │  clean snapshot  │
   │  build & test   │        │  deploy + accept │
   └─────────────────┘        └──────────────────┘
```

Each development cycle runs a fixed, deterministic sequence. The AI *proposes*; the
Orchestrator *decides* what is allowed:

1. **`dev-code`** — the host pushes the repo to a bare repo on the Developer VM; Claude
   Code implements the next eligible DEVPLAN task, commits, and pushes back. The host
   **fetch-gate** inspects the returned diff: pushes touching `DESIGN.md`,
   `PRINCIPLES.md`, or `AGENT_RULES.md` are rejected; `DEVPLAN.md` may only gain checkbox
   ticks, appended tasks (with fresh IDs), and `(DEFERRED)` notes — no deletions,
   reorders, or edits to done items. New `PROVISIONAL` decisions are recorded and pinged
   to you.
2. **`verify-tests`** — the Orchestrator makes a *fresh clone* on the Developer VM and
   re-runs `scripts/test.sh` itself. Only when that independent run passes is a
   `checkpoint/NNNN` tag created. A checkpoint therefore certifies "the Orchestrator
   re-ran the suite and it passed," never "the worker said so."
3. **`deploy-test`** — the Deployment VM reverts to a clean snapshot, pulls the
   candidate, and runs `scripts/deploy.sh` then `scripts/acceptance.sh`, proving the
   product installs and works from nothing.
4. **decision** — a fixed rule table chooses: continue to the next task, retry a failed
   step, pause for you, or finish and produce a release candidate.

You stay in the loop by exception: consequential ("structural") guesses are logged as
`PROVISIONAL` and notified with **Ack / Revert** buttons; after two unreviewed ones the
run pauses rather than build further on unreviewed guesses.

See [`docs/loopwright-design.md`](docs/loopwright-design.md) for the full design.

---

## Requirements

**Host** (trusted machine — your workstation):

- Kubuntu / Ubuntu-family Linux with **KVM/QEMU + libvirt** (`virsh`) and, optionally,
  `virt-manager`.
- **Python 3.12+**.
- SSH client with key-based access to both VMs.
- (Optional) An OpenAI API key, if you want the Primary Agent chat assistant to help you
  draft the design packet. Everything else works without it.

**Two libvirt VMs** (disposable workspaces):

- **Developer VM** — a desktop-class VM with the build toolchain and **Claude Code
  installed and logged in**. GUI recommended (XFCE) for browser/GUI testing.
- **Deployment VM** — a minimal server VM representing a clean target environment.

Both VMs need `openssh-server`, a user with passwordless sudo *inside the VM only*, and
the host's SSH public key authorized. The host never gives the VMs any credentials — all
git transfer is host-initiated. Full VM build instructions are in
[`docs/loopwrite-host-and-vm-setup.md`](docs/loopwrite-host-and-vm-setup.md).

---

## 1. Install Loopwright (host)

```bash
git clone https://github.com/Solifugus/loopwright.git
cd loopwright
make install        # creates .venv and installs the CLI in editable mode
.venv/bin/loopwright --version
```

The `loopwright` command lives at `.venv/bin/loopwright`. Add `.venv/bin` to your `PATH`
or activate the venv (`source .venv/bin/activate`) to use `loopwright` directly. The rest
of this guide writes it as `loopwright`.

---

## 2. Create the doctrine repository (required)

Loopwright keeps your engineering principles and agent rules in **one** place — a
separate `loopwright-doctrine` repository — and copies them into every new project.
There is no built-in fallback: **project creation refuses to run without a valid
doctrine directory.**

```bash
mkdir -p ~/development/loopwright-doctrine/templates
cd ~/development/loopwright-doctrine
git init
```

Create these files:

```
loopwright-doctrine/
  PRINCIPLES.md              # your engineering tiebreakers (simplicity, reversibility, …)
  AGENT_RULES.md             # the rules the worker must obey (test integrity, security, …)
  templates/
    DESIGN.md                # design-packet template ({{PROJECT}} is substituted)
    DEVPLAN.md               # dev-plan template
    TESTPLAN.md              # test-plan template
```

`PRINCIPLES.md` and `AGENT_RULES.md` are authoritative and are committed read-only into
each project under `docs/agent/`; the worker prompt points at them and the fetch-gate
blocks any attempt to change them. Use `{{PROJECT}}` in the templates where you want the
project name filled in. (The Loopwright design docs list good starting content for
`PRINCIPLES.md` and `AGENT_RULES.md`.)

---

## 3. Configure the host

Copy the example config and edit it:

```bash
mkdir -p ~/.config/loopwright
cp examples/config.example.yaml ~/.config/loopwright/config.yaml
$EDITOR ~/.config/loopwright/config.yaml
```

A complete config:

```yaml
projects_dir: ~/development/loopwright/projects   # where project state lives

# REQUIRED — your loopwright-doctrine checkout (must contain PRINCIPLES.md + AGENT_RULES.md)
doctrine_dir: ~/development/loopwright-doctrine

libvirt_uri: qemu:///system

dev_vm:                       # the Developer VM (coding + independent verification)
  domain: LoopWright_Dev      # libvirt domain name (see `virsh list --all`)
  host: 192.168.122.20        # reachable over SSH from the host
  user: master

test_vm:                      # the Deployment VM (clean deploy + acceptance)
  domain: loopwright_test
  host: 192.168.122.120
  user: master
  snapshot: deployment-ready  # clean snapshot reverted before every deploy test

ntfy_server: https://ntfy.sh
ntfy_topic: loopwright-yourname   # pick a hard-to-guess topic; leave unset to disable

# Public URL of the web UI, reachable from your phone. When set, PROVISIONAL
# notifications carry Ack/Revert buttons that POST back to these routes.
web_base_url: http://192.168.122.1:8000

openai_api_key_env: OPENAI_API_KEY   # env var holding the key for the Primary Agent
openai_model: gpt-4o

limit_resume_minutes: 30      # auto-resume delay after an AI usage-limit pause
provisional_cap: 2            # unreviewed structural decisions allowed before pausing
```

Validate it:

```bash
loopwright config check
```

`config check` errors (not just warns) if `doctrine_dir` is missing or lacks the two
doctrine files, and reports the status of your VMs, tools, notifications, and API key.

---

## 4. Set up the two VMs

Follow [`docs/loopwrite-host-and-vm-setup.md`](docs/loopwrite-host-and-vm-setup.md) for
the full build. The essentials:

**Developer VM**
- Toolchain: `git`, `build-essential`, `python3`, `node`, `docker`/`podman`, plus
  browsers and `xvfb`/`xdotool` if you'll do GUI/browser testing.
- **Claude Code installed and authenticated** (`claude -p "hello"` must return real
  output — not a login prompt).
- `openssh-server`; a user (e.g. `master`) with `NOPASSWD` sudo *inside the VM*; the
  host's SSH public key in `~/.ssh/authorized_keys`.
- Snapshots so a run always starts from a known state, e.g. `base-os` →
  `toolchain-installed` → `ready-for-run`.

**Deployment VM**
- Minimal server (no GUI needed). `git`, `curl`, `openssh-server`, and whatever runtime
  your product needs installed by its own `deploy.sh`.
- Same SSH/sudo setup.
- A clean snapshot named to match `test_vm.snapshot` (e.g. `deployment-ready`). The
  Deployment VM reverts to it before every deploy test, so nothing leaks between runs.

**Trust boundary.** The host pushes each project to a bare repo at
`loopwright/<project>.git` on each VM over SSH and fetches results back; the VMs never
hold credentials to reach the host. Confirm passwordless SSH works both ways of the
transfer:

```bash
loopwright vm status dev          # should print the Developer VM's power state
loopwright vm exec dev 'echo ok'  # should print: ok
```

---

## 5. Verify the setup

```bash
loopwright config check           # all green / no errors
loopwright vm status dev
loopwright vm status test
loopwright notify test            # a test push should reach your phone (if ntfy configured)
```

Start the web UI (in its own terminal — leave it running):

```bash
loopwright serve                  # or: make run  → http://127.0.0.1:8000
```

---

## Building a web application, end to end

This walks through taking a small web app from nothing to an approved, deploy-proven
release. The example is a tiny FastAPI "notes" service, but the flow is identical for any
web app.

### Step A — create the project

Either from the web UI (**New project** wizard at `/projects/new`) or the CLI:

```bash
loopwright project create notes-api
```

This creates `projects/notes-api/`, initializes the authoritative git repo with the
branch model (`design/main → agent/work → agent/test → release/candidate → main`), copies
your doctrine templates into the packet drafts, and seeds empty
`docs/agent/{DECISIONS,TASKLOG,BLOCKED}.md`.

### Step B — write the design packet

Open `http://127.0.0.1:8000/projects/notes-api/packet`. You edit three files in the
browser. If you configured an OpenAI key, the **Primary Agent** chat panel can draft and
refine them into the editor for you — but it can *only* write into the editor buffers,
never the repo. **You** own this content.

The packet is the whole contract. Spend your time here; it sets the quality ceiling.

**`DESIGN.md`** — purpose, scope, architecture, and — most importantly — concrete
**acceptance criteria** and examples. For example:

```markdown
# notes-api — Design

## Purpose
A single-user HTTP notes service: create, list, and delete short text notes,
persisted to a local SQLite file.

## Architecture
- Python 3.12 + FastAPI, served by uvicorn on port 8080.
- SQLite via the stdlib `sqlite3`; no ORM.

## Interfaces
- `POST /notes {"text": "..."}` → 201 with the created note as JSON.
- `GET  /notes`                 → 200 with a JSON array of notes.
- `DELETE /notes/{id}`          → 204.

## Deployment
- `scripts/deploy.sh` installs the app and starts it as a service on port 8080.
- Runs on a clean Ubuntu Server VM with only git + curl preinstalled.

## Acceptance Criteria
- [ ] `POST /notes` then `GET /notes` returns the created note.
- [ ] `DELETE /notes/{id}` removes it; a subsequent `GET` omits it.
- [ ] The service survives a restart with its notes intact.
- [ ] `README` quickstart works with no undocumented manual steps.
```

**`DEVPLAN.md`** — small, ordered tasks, each finishable in one worker session, each with
a stable ID. Include tasks that produce the deployment contract scripts:

```markdown
# notes-api — Development Plan

- [ ] 1. Project skeleton: FastAPI app, `sqlite` storage module, pyproject.
- [ ] 2. Implement POST/GET/DELETE /notes with tests.
- [ ] 3. scripts/test.sh — run the full suite from a clean clone.
- [ ] 4. scripts/deploy.sh — install deps and run the app as a service on :8080.
- [ ] 5. scripts/acceptance.sh — curl the running service to prove the criteria.
- [ ] 6. README quickstart + docs.
```

Tasks may declare dependencies with `(needs: <id>)`; the worker always picks the
lowest-ID unchecked task whose needs are all checked.

**`TESTPLAN.md`** — how it's verified (unit, integration, deployment/acceptance). The
template already reminds the worker that the project must carry `scripts/test.sh`,
`scripts/deploy.sh`, and `scripts/acceptance.sh`.

### Step C — approve the packet

Click **Approve packet** in the editor (or it's committed when you approve). This commits
the packet to `design/main` and moves the run to `READY`. From here the packet's
protected files can only change with your explicit approval.

### Step D — start the autonomous run

Runs are launched from the CLI (see [Current limitations](#current-limitations-v01)):

```bash
loopwright run loop notes-api
```

The Orchestrator now cycles `dev-code → verify-tests → deploy-test → decide` until every
task is checked and the deployment passes, or until it needs you. Useful flags:

```bash
loopwright run loop notes-api \
  --max-cycles 40 \
  --dev-timeout 1800 \
  --verify-timeout 900 \
  --deploy-timeout 900
```

Leave it running; it will notify you on the events below.

### Step E — watch progress

- **Dashboard** — `http://127.0.0.1:8000/projects/notes-api` shows the live state,
  cycle count, step results, checkpoints, and pending provisional decisions (HTMX polls
  every few seconds).
- **Logs** — `/projects/notes-api/logs` tails the structured run log with filters.
- **Notifications** — run started, checkpoint passed, deployment passed, provisional
  decision, repeated failure, usage-limit reached, approval needed, candidate ready.

### Step F — review structural decisions (Ack / Revert)

When the agent makes a *structural* guess (something touching your intent), it logs a
`PROVISIONAL` entry and pings you with **Ack** and **Revert** buttons:

- **Ack** — you're fine with the guess; it's dismissed.
- **Revert** — roll `agent/work` back to the checkpoint tagged just before that decision,
  discarding it and anything built on it.

Both are idempotent (a late double-tap is harmless). From the CLI:

```bash
loopwright provisional list notes-api
loopwright provisional ack    notes-api <decision-id>
loopwright provisional revert notes-api <decision-id>
```

If two structural decisions go unreviewed, the run pauses until you handle one.

### Step G — final report and release approval

When all tasks are done and deployment passes, the Orchestrator promotes the work to
`release/candidate`, writes `FINAL_REPORT.md` (checkpoints, test/deploy results,
decisions, and any deviations from the DEVPLAN), and notifies you that a candidate is
ready. Review it at `/projects/notes-api/report`, then click **Approve release**. That
fast-forwards `main` to the candidate and marks the run `DONE`.

You now have, in `projects/notes-api/repo.git`, a `main` branch that:
provably installs from a clean machine, passes an independently re-run test suite behind
a rule-checked diff, and carries a final report — with your explicit approval on it.

---

## The project scripts: your deployment contract

Three scripts make the product self-proving. The worker writes them as it implements the
DEVPLAN; the Orchestrator runs them, not the worker's word:

| Script | Run by | Must do |
|---|---|---|
| `scripts/test.sh` | `verify-tests` on the Developer VM, from a **fresh clone** | Run the full suite; exit non-zero on any failure. A checkpoint is only tagged when this passes independently. |
| `scripts/deploy.sh` | `deploy-test` on the Deployment VM, from a **clean snapshot** | Install and start the product on a bare machine with no undocumented manual steps. |
| `scripts/acceptance.sh` | `deploy-test`, after `deploy.sh` | Exercise the *running, deployed* product and prove the acceptance criteria (e.g. `curl` the endpoints). Exit non-zero on failure. |

For the notes-api example, `acceptance.sh` might `curl -fsS -X POST .../notes`, then
`GET /notes` and `grep` for the text — failing the run if the deployed service doesn't
behave.

---

## Operating a run

- **Pause / Resume / Stop** — from the dashboard, or drive the state machine directly.
  The loop honors a pause/stop between steps by reloading state from disk.
- **Rollback** — roll `agent/work` back to any checkpoint from the dashboard, or
  `loopwright provisional revert` to undo a specific decision.
- **Usage limits** — if Claude Code hits a usage limit, the run parks in `PAUSED_LIMIT`,
  notifies you, and auto-resumes after `limit_resume_minutes`. Detection scans only the
  tail of output paired with the exit status, so a project whose *own* output mentions
  rate limiting won't falsely park the run.
- **Crash-safe** — every step result is persisted immediately; restarting `run loop`
  resumes at the first incomplete step.

---

## CLI reference

```
loopwright config check [--config PATH]      Validate configuration; report findings

loopwright project create <name>             Create a project (repo + packet from doctrine)
loopwright project list                      List projects and their run states

loopwright vm status|start|stop <vm>         Control a VM ('dev', 'test', or a domain name)
loopwright vm snapshots|snapshot|revert <vm> ... Manage VM snapshots
loopwright vm exec <vm> '<cmd>'              Run a command in a VM over SSH

loopwright run dev <project>                 Run one Developer VM coding session
loopwright run deploy <project>              Run one Deployment VM test of the candidate
loopwright run loop <project> [flags]        Run full dev→verify→deploy cycles until done

loopwright provisional list <project>        List unreviewed PROVISIONAL decisions
loopwright provisional ack <project> <id>    Acknowledge (dismiss) a decision
loopwright provisional revert <project> <id> Roll back to a decision's pre-checkpoint

loopwright serve [--host H] [--port P]       Run the web UI (default 127.0.0.1:8000)
loopwright notify test [--message M]         Send a test notification
```

`run loop` flags: `--retry-limit`, `--max-cycles`, `--dev-timeout`, `--verify-timeout`,
`--deploy-timeout`, `--limit-resume-minutes`.

---

## Configuration reference

| Key | Default | Meaning |
|---|---|---|
| `projects_dir` | `~/.local/share/loopwright/projects` | Where per-project state and repos live |
| `doctrine_dir` | *(required)* | `loopwright-doctrine` checkout; must contain PRINCIPLES.md + AGENT_RULES.md |
| `libvirt_uri` | `qemu:///system` | libvirt connection URI |
| `dev_vm` / `test_vm` | — | `{domain, host, user, snapshot}` for each VM |
| `ntfy_server` | `https://ntfy.sh` | ntfy server base URL |
| `ntfy_topic` | *(unset → notifications off)* | ntfy topic to publish to |
| `web_base_url` | *(unset)* | Public UI URL; enables Ack/Revert buttons on provisional pushes |
| `openai_api_key_env` | `OPENAI_API_KEY` | Env var holding the Primary Agent's API key |
| `openai_model` | `gpt-4o` | Model for the Primary Agent |
| `limit_resume_minutes` | `30` | Auto-resume delay after a usage-limit pause |
| `provisional_cap` | `2` | Unreviewed structural decisions before a run pauses |

---

## Current limitations (v0.1)

- **The web UI cannot launch the autonomous loop yet.** Start runs with
  `loopwright run loop <project>` from the CLI. Everything else — pause/resume/stop,
  rollback, release approval, provisional ack/revert — works from the browser.
- The Developer VM is not reverted between sessions (only the Deployment VM is); the dev
  step always uses a fresh clone, but installed packages persist.

See [`docs/V0.2-NOTES.md`](docs/V0.2-NOTES.md) for the full list of deferrals.

---

## Development

```bash
make install                                  # .venv + editable install with dev tools
make test                                     # pytest (all tests)
make lint                                     # ruff
make run                                       # run the web UI
.venv/bin/pytest tests/test_cli.py -k version # run a single test
```

Package layout: `core/` (domain + config), `gitctl/` (git control + fetch-gate helpers),
`vmctl/` (virsh + SSH, with dry-run fakes), `notify/`, `web/` (FastAPI + Jinja2/HTMX),
`orchestrator/` (engine, steps, fetch-gate, decision rules, loop), `agent/` (Primary
Agent). Anything that touches VMs also works under injected fakes so it's testable
without real VMs.

Design and planning docs:

- [`docs/loopwright-design.md`](docs/loopwright-design.md) — system design and the trust model
- [`docs/loopwrite-host-and-vm-setup.md`](docs/loopwrite-host-and-vm-setup.md) — host/VM setup
- [`docs/DEVPLAN.md`](docs/DEVPLAN.md) — build plan and progress
- [`CLAUDE.md`](CLAUDE.md) — guidance for working in this repo
