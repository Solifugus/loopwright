# Loopwright

A local, VM-supervised autonomous software development system.

**Autonomous execution, human-owned intent.** The human owns purpose, design, scope,
principles, and final approval. Loopwright owns implementation, testing, deployment
validation, documentation, and packaging.

See `docs/loopwright-design.md` for the system design and `docs/DEVPLAN.md` for the
build plan and current progress.

## Development

```bash
make install   # create .venv and install in editable mode with dev tools
make test      # run pytest
make lint      # run ruff
make run       # run the loopwright CLI
```
