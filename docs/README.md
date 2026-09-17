# Scout documentation

| | |
|---|---|
| **[Getting started](getting-started.md)** | Install, create the Slack app, and have your first conversation. Start here. |
| **[Configuration](configuration.md)** | Every setting, its default, and which file it belongs in. |
| **[Architecture](architecture.md)** | How a DM becomes a ranked list of roles: the two graphs, the three seams, and the invariants behind them. |
| **[Extending](extending.md)** | Add a job source, a tool, an agent, or a model provider. |
| **[Deployment](deployment.md)** | Docker, AWS, GCP, launchd — and what it costs. |
| **[Operations](operations.md)** | The daily digest, turn metrics, Langfuse tracing, alerts, and what to check when it goes quiet. |

Taking part:

| | |
|---|---|
| **[Contributing](CONTRIBUTING.md)** | The conventions, and the one command that runs everything CI runs. |
| **[Code of conduct](CODE_OF_CONDUCT.md)** | What is expected of everyone taking part. |
| **[Security](SECURITY.md)** | What Scout handles, and how to report a vulnerability. |
| **[Changelog](CHANGELOG.md)** | What changed. |

`README.md`, `LICENSE` and `CLAUDE.md` stay in the repository root: the first two
are named by path in `pyproject.toml` and are what GitHub reads for the landing
page and the licence badge, and the third is the working map Claude Code loads
from the root of a checkout.
