VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install test lint run clean

$(VENV)/bin/python:
	python3 -m venv $(VENV)

install: $(VENV)/bin/python
	$(PIP) install -q -e ".[dev]"

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check loopwright tests

run:
	$(VENV)/bin/loopwright

clean:
	rm -rf $(VENV) *.egg-info loopwright.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
