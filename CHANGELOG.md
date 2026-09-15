# Changelog

Notable changes to Scout. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from its
first release.

## [Unreleased]

### Added

- `scout` command line with one entry point per job: `scout run`, `scout
  digest`, `scout stats`, `scout agents`, and `scout doctor`. `python run.py`
  and `python -m scout.digest` keep working.
- `scout doctor` — reports what is configured in a checkout and what is
  missing, reading only local files, so it is safe to run on a broken box.
- Continuous integration: ruff, mypy, the test suite on Python 3.10–3.13, and a
  job that installs the built wheel and runs the console script.
- Type-checking with `mypy` under `disallow_untyped_defs`, and a `py.typed`
  marker so the package's types are visible to anything that imports it.
- MIT licence, contributing guide, security policy, code of conduct, issue and
  pull-request templates, Dependabot, `Makefile`, and `.editorconfig`.
- Documentation split into task-shaped pages under `docs/`, starting with a
  getting-started walkthrough.
- Tests for the clock, location, and résumé tools, and for the new command line
  and configuration report.

### Changed

- `pyproject.toml` is the single source of truth for dependencies;
  `requirements.txt` and `requirements-dev.txt` are generated from it by
  `scripts/sync_requirements.py`, and a test fails if they drift.
- Message splitting moved from `scout/slack/bot.py` to
  `scout/slack/formatting.py`, so the digest no longer imports a private
  helper from the bot.
- Job sources read their JSON rows through one shared `json_rows` helper, which
  checks the shape a board actually returned.

### Fixed

- The `scout` console script pointed at `run:main`, a module that is not part
  of the package: installing the wheel produced a command that could not start.
- `langgraph-checkpoint-sqlite` and `langsmith` were listed in
  `requirements.txt` but not in `pyproject.toml`, so `pip install .` produced
  an install that could not even import `scout.core.checkpoints`.

## Before this changelog

The platform — the LangGraph agent runtime, the Slack adapter, the two
job-search agents, the résumé hand-off, the Referral Window, the daily digest,
turn metrics, and the EC2 deployment — was built before this file existed. See
the commit history for that.
