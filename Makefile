.PHONY: demo test run run-target clean

VENV ?= .venv
PYTHON = $(VENV)/bin/python3
PYTEST = $(VENV)/bin/pytest

demo:
	bash scripts/demo.sh

test:
	$(PYTEST) tests/ -v

clean:
	rm -rf data/*.db* logs/*.log demo-target/__pycache__ tests/__pycache__ aegis/__pycache__

run:
	$(PYTHON) -m aegis.main --host 0.0.0.0 --port 8000 --db data/aegis.db

run-target:
	$(PYTHON) demo-target/app.py
