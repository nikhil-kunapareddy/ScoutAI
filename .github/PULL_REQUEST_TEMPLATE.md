## What this changes

<!-- One or two sentences. If it fixes an issue, add "Fixes #123". -->

## Why

<!-- The problem, or the behaviour you wanted. Prose is fine. -->

## Checklist

- [ ] `make check` passes (ruff, mypy, tests with coverage)
- [ ] Tests cover the new behaviour — no network, no credentials
- [ ] Docs updated if behaviour or configuration changed
- [ ] A new agent is one file in `scout/agents/` and touches no graph code
- [ ] A new tool documents every parameter in its `Args:` block (that block is
      the schema the model reads)
