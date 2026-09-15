# Everyday commands. `make` on its own lists them.
#
# Nothing here needs credentials or a network: `make check` is exactly what CI
# runs, so a green local run means a green pull request.

PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.DEFAULT_GOAL := help
.PHONY: help venv install test cov lint fmt typecheck check requirements doctor run digest clean

help: ## List the available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	$(PYTHON) -m venv $(VENV)

install: venv ## Install Scout (editable) and its development dependencies
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev]"

test: ## Run the test suite (no network, no credentials)
	$(BIN)/pytest

cov: ## Run the tests with a coverage report
	$(BIN)/pytest --cov --cov-report=term-missing

lint: ## Check style and common mistakes
	$(BIN)/ruff check .

fmt: ## Apply the fixes ruff can make safely
	$(BIN)/ruff check --fix .

typecheck: ## Type-check the package
	$(BIN)/mypy

requirements: ## Regenerate requirements*.txt from pyproject.toml
	$(BIN)/python scripts/sync_requirements.py

check: lint typecheck cov ## Everything CI runs
	$(BIN)/python scripts/sync_requirements.py --check

doctor: ## Report what is configured in this checkout
	$(BIN)/python -m scout doctor

run: ## Start the bot for the agent named by AGENT
	$(BIN)/python -m scout run

digest: ## Run the daily digest once, now
	$(BIN)/python -m scout digest

clean: ## Remove caches and build artifacts
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache .coverage
	find . -name __pycache__ -type d -prune -not -path "./$(VENV)/*" -exec rm -rf {} +
