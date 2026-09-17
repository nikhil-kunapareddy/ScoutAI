# Contributing

Thanks for looking. This is a small, opinionated codebase: an agent is a system
prompt plus a set of tools, and the interesting decisions are written down
rather than implied. If you keep to the four conventions below, a change is
usually a single file.

## Set up

```bash
git clone https://github.com/nikhil-kunapareddy/ScoutAI.git
cd ScoutAI
make install          # virtualenv, dependencies, and the `scout` command
make test             # the whole suite in about a second: no network, no credentials
```

No `.env` is needed to work on the code — only to talk to Slack. See
[getting-started.md](getting-started.md) for that.

## The loop

```bash
make check            # exactly what CI runs: ruff, mypy, tests + coverage
```

`make` on its own lists every target. If `make check` is green locally, CI will
be green too; it runs the same four commands.

## The four conventions

1. **Adding an agent is one file.** A module in `scout/agents/` defining
   `SPEC = AgentSpec(...)`, registered in `AGENTS`. If your change touches the
   graph to add an agent, something has gone wrong — see
   [extending.md](extending.md).
2. **A tool's signature and docstring are its schema.** LangChain derives what
   the model sees from your annotations and the Google-style `Args:` block, so
   annotate every parameter and document it. `ruff` enforces the second half.
3. **Tools never raise for an expected failure.** A dead job board, no results,
   a malformed page: return a sentence the model can read and act on. An
   exception costs the user their whole turn.
4. **Tests touch no network and need no credentials.** Chat models are scripted
   (`ScriptedModel` in `tests/conftest.py`) and HTTP is stubbed (`FakeRequests`,
   same file). A test that reaches a real job board or model API does not belong
   here — it fails in CI, on a plane, and at 3 a.m.

## Style

- `ruff` with `line-length = 100`, targeting Python 3.10. `make fmt` applies
  what it can fix.
- Full type annotations in `scout/` — `mypy` runs with `disallow_untyped_defs`.
- Docstrings and comments explain *why*. The what is already in the code, and
  this repo leans on that: several comments exist because the alternative broke
  something subtle. Please keep that habit, and add the reason when you change
  one of those lines.
- Names read as prose. `_within_window`, `take_newest`, `answered_by`.

## Invariants worth not breaking

Some behaviour here is load-bearing in ways a test name alone won't tell you:
the trimming that keeps a conversation valid, the hop limit that repairs a
thread, the injected `config` that keeps user ids out of the model's schema.
They are listed, with the reasoning, in
[architecture.md](architecture.md#invariants-worth-not-breaking).
Each one has a test; if you find yourself deleting that test, read the entry
first.

## Pull requests

- One topic per pull request, with a subject line in the imperative
  ("Add the Referral Window agent"), and the *why* in the body.
- Update the docs in the same change when behaviour or configuration moves.
  `configuration.md` and `.env.example` are a pair.
- Add a line to `CHANGELOG.md` under `Unreleased`.
- Dependencies are declared in `pyproject.toml`; `requirements*.txt` are
  generated from it with `make requirements`.

## Never commit

`.env`, `.env.<agent>`, anything in `data/` (résumés), `logs/`, or `state/`.
They are git-ignored, and `.dockerignore` keeps secrets out of the image too.
If you think you have committed a credential, say so in the pull request
immediately — rotating a Slack token takes a minute, and history is forever.
