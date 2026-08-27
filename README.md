# Scout

A small **multi-agent platform** for Slack-based AI agents. Each agent is a Slack
DM bot backed by the hosted [Anthropic Claude API](https://docs.claude.com)
(default), a local LLM (via [Ollama](https://ollama.com)), or the hosted
[Meta Llama API](https://llama.developer.meta.com) — switchable per-user at
runtime — and can call tools for live information.

Two job-search agents ship with it, both tailored to your resume:

| Agent | `AGENT=` | What it searches |
|-------|----------|------------------|
| **BigTech Agent** (default) | `bigtech` | Amazon, Google, Netflix, and Greenhouse-hosted companies (Databricks, Airbnb, Stripe, Pinterest, Reddit, Coinbase, Dropbox, Robinhood) |
| **University Agent** | `university` | Northeastern University, Boston University |
| **Resume Parser** | `resume` | Nothing — the profile-extraction stage, runnable alone for debugging |

Adding another agent is one small file — see [Adding an agent](#adding-an-agent).

## Architecture

```
Slack DM
   │
   ▼
scout/slack/bot.py ────── SlackBot: DM handling + text commands (--claude, --reset, …)
   │  talks only to ConversationalAgent, so it needs no pipeline knowledge
   ▼
scout/agents/resume_tailored.py   resume profile ──▶ job agent
   │                              (when a spec sets tailor_with_resume)
   ▼
scout/core/agent.py ───── Agent: conversation history + tool-call loop (per-user
   │                      backend, replayed on the fallback backend if it fails)
   │ (model request)          │ (tool calls)          ▲ declared by
   ▼                          ▼                       │
scout/core/backends/     scout/tools/ ─ ToolRegistry   scout/agents/*.py (AgentSpec)
   ├── anthropic_api.py     ├── clock.py         get_current_time, get_current_date
   ├── ollama.py            ├── location.py      get_location
   └── llama_api.py         ├── resume.py        get_resume_profile
        (all ChatBackend)   └── jobs/            one module per source
                                ├── amazon.py            search_amazon_jobs
                                ├── google.py            search_google_jobs
                                ├── netflix.py           search_netflix_jobs
                                ├── greenhouse.py        search_greenhouse_jobs
                                ├── northeastern.py      search_northeastern_jobs
                                └── boston_university.py search_boston_university_jobs
```

An `AgentSpec` declares an agent (name, system prompt, tool set, default backend,
whether to tailor to the resume). `Agent` runs it: the chosen backend decides when
to call a tool, `Agent` runs the registered Python function in-process and feeds
the result back, and the model folds it into its reply.

Two abstractions keep the layers apart:

- **`ChatBackend`** (`scout/core/backends/base.py`) — one provider, normalized.
  Adding a model provider means one module plus one line in `_BACKEND_CLASSES`.
- **`ConversationalAgent`** (`scout/core/agent.py`) — what the Slack layer needs
  from an agent. Both a plain `Agent` and the two-stage `ResumeTailoredAgent`
  implement it, so the adapter has no special cases.

## Project layout

| Path | Purpose |
|------|---------|
| `run.py` | Entry point — runs the agent named by `AGENT` (default `bigtech`) |
| `scout/core/agent.py` | `AgentSpec`, the `ConversationalAgent` interface, and the `Agent` runtime |
| `scout/core/settings.py` | Shared config from `.env` (tokens, models, limits, timeouts) |
| `scout/core/logging_config.py` | Console + rotating-file logging |
| `scout/core/paths.py` | Filesystem paths (no env dependencies) |
| `scout/core/backends/` | One module per model backend (Claude, Ollama, Llama API) |
| `scout/tools/` | Tool library + registry; `clock`, `location`, `resume` |
| `scout/tools/jobs/` | One module per job source; `__init__.py` holds what they share |
| `scout/slack/bot.py` | Slack adapter — `SlackBot` wires an agent to Slack DMs |
| `scout/agents/` | One `AgentSpec` per agent, plus the hand-off (`resume_tailored.py`) |
| `tests/` | pytest suite — no network, no credentials needed |
| `Dockerfile` | Worker image for ECS / GCE / Cloud Run worker pools |
| `data/` | Your resume(s) — read by `get_resume_profile` (git-ignored) |
| `logs/` | Runtime logs (`bot.log`, rotated; git-ignored) |

## One-time Slack setup

1. https://api.slack.com/apps → **Create New App** → **From scratch**.
2. **Socket Mode** → toggle on → generate an **App-Level Token** with scope
   `connections:write`. Save the `xapp-...` token.
3. **OAuth & Permissions** → add Bot Token Scopes: `chat:write`, `im:history`,
   `im:read`, `im:write`.
4. **Event Subscriptions** → enable → under *Subscribe to bot events* add `message.im`.
5. **App Home** → enable the *Messages Tab* and *Allow users to send Slash
   commands and messages from the messages tab* (this is what lets you DM the bot).
6. **Install App** → copy the **Bot User OAuth Token** (`xoxb-...`).

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your Slack tokens into .env
```

Add your Anthropic API key to `.env` — this is the default backend:

```
ANTHROPIC_API_KEY=sk-ant-...   # https://console.anthropic.com/settings/keys
```

Ollama is the fallback (and the default if no Anthropic key is set), so it's
worth having running and pulled:

```bash
ollama serve                  # if not already running
ollama pull llama3.2:3b
```

Drop your resume into `data/` (PDF, DOCX, TXT, or MD). The Resume Parser reads
the most recently modified file there.

## Run it

```bash
python run.py                     # BigTech Agent (default)
AGENT=university python run.py    # University Agent
```

Then just DM the bot in plain language — it routes through the model with all
tools available. For example:

- "latest amazon jobs" → recent Amazon AI/ML roles (title, link, date posted).
- "what google roles are open?" → recent Google AI/ML roles (newest-first;
  Google doesn't publish posting dates, so no date is shown).
- "anything at databricks or stripe?" → the Greenhouse boards for those companies.
- "what's my location?" / "what time is it?"

Every reply is tailored to the resume in `data/`: on your first message the
Resume Parser distills it into a profile (target titles, skills, search
keywords), which is cached per-user and prepended to every job-search turn.

## Commands

Typed as ordinary DM messages (Slack slash commands can't run in DM threads):

- **`--claude`** (or `--anthropic`) — use the Anthropic Claude API (`ANTHROPIC_MODEL`).
- **`--ollama`** — use the local Ollama model (`OLLAMA_MODEL`).
- **`--api`** (or `--llama`) — use the Meta Llama API (`LLAMA_MODEL`).
- **`--backend`** — show which model you're currently using.
- **`--reset`** — clear your conversation history (and re-read your resume).
- **`--help`** — list all commands.

## Choosing a model backend

Agents start on **Claude** (`ANTHROPIC_MODEL`, default `claude-opus-5`) when
`ANTHROPIC_API_KEY` is set, and on the local Ollama model otherwise. Each user
can switch at any time; the choice persists per-user until changed, and resets to
the default on restart.

Conversation history is stored as plain text and shared across backends, so you
can switch mid-conversation without losing context.

### Fallback

If a turn fails on the chosen backend — missing key, rate limit, outage, timeout —
the whole turn is replayed once on `FALLBACK_BACKEND` (default `ollama`) and the
reply carries a note saying so. Because history holds only plain text, nothing
from the failed attempt leaks into the retry. Your chosen backend isn't changed,
so the next message tries it again.

## Deployment

The bot is a **worker**, not a web service: Socket Mode holds an outbound
websocket to Slack and listens on no port. That means it has to stay running —
Slack cannot wake a stopped process, because there is no address to reach it at.
(Ping-to-wake is possible, but only by switching to the HTTP Events API; see
[Ping-to-wake](#ping-to-wake) below.)

The `Dockerfile` builds that worker and runs the same on any of the platforms
below. Build and try it locally first:

```bash
docker build -t scout .
docker run --rm --env-file .env scout
```

Two things the image expects:

- **Secrets come from the platform**, not the image — `.dockerignore` excludes
  `.env`. Pass them with `--env-file` locally, and with Secret Manager (GCP) or
  SSM/Secrets Manager (AWS) in the cloud.
- **`FALLBACK_BACKEND` is empty in the image.** There is no Ollama in the
  container, so an empty value disables the retry rather than making every
  Claude failure fail twice.

### GCP

**Compute Engine `e2-micro`** is the simplest fit, and it's in the always-free
tier in `us-west1`, `us-central1`, and `us-east1`:

```bash
gcloud compute instances create-with-container scout \
  --machine-type=e2-micro --zone=us-central1-a \
  --container-image=REGION-docker.pkg.dev/PROJECT/scout/scout:latest \
  --container-env-file=.env \
  --container-restart-policy=always
```

Cloud Run is the other option, but a Cloud Run *service* must listen on `$PORT`,
which a Socket Mode worker doesn't do — use a Cloud Run **worker pool** (built
for exactly this) if it's available in your project, or stay on GCE.

### AWS

**ECS Fargate** with `desiredCount: 1` is the standard always-on container
(~$6/month at 0.25 vCPU / 0.5 GB). No load balancer or target group is needed,
since nothing connects inbound — which also means no public IP and no security
group ingress. Pull secrets in the task definition:

```json
"secrets": [
  {"name": "SLACK_BOT_TOKEN", "valueFrom": "arn:aws:ssm:...:parameter/scout/SLACK_BOT_TOKEN"},
  {"name": "SLACK_APP_TOKEN", "valueFrom": "arn:aws:ssm:...:parameter/scout/SLACK_APP_TOKEN"},
  {"name": "ANTHROPIC_API_KEY", "valueFrom": "arn:aws:ssm:...:parameter/scout/ANTHROPIC_API_KEY"}
]
```

A `t4g.nano` EC2 instance or a Lightsail container runs it for ~$3–5/month if
you'd rather not deal with ECS.

### Running it on your Mac instead

A `launchd` LaunchAgent with `RunAtLoad` and `KeepAlive` restarts the bot on
crash and starts it at login. Free, and it keeps the Ollama fallback — but the
bot is unreachable whenever the Mac sleeps, so it suits a desktop better than a
laptop.

### Ping-to-wake

Scale-to-zero (Cloud Run at `min-instances=0`, or Lambda behind a Function URL)
needs three changes, none of which exist yet:

1. **An HTTP adapter.** `slack_bolt` ships `SlackRequestHandler` for
   wsgi/asgi/flask/fastapi/aws_lambda. Because the Slack layer only talks to
   `ConversationalAgent`, this is a sibling of `bot.py`, not a rewrite.
2. **Ack within 3 seconds.** Slack retries up to three times otherwise, and a
   real turn takes 10–60s. Ack immediately, do the work in a background worker,
   and post the reply with `chat.postMessage` — plus **dedupe on the Slack event
   id**, or a slow cold start gets you three replies to one message.
3. **Persistence.** Conversation history and the parsed resume profile are held
   in memory, so a container that scales to zero re-parses the resume (a PDF read
   plus a Claude call) before nearly every first message.

## Development

```bash
pip install -r requirements-dev.txt
pytest                     # 170 tests, no network or credentials required
ruff check .
```

The suite scripts the model backends and stubs every HTTP call, so it runs in CI
without a `.env`. That's also why `scout.core.settings` never raises on import:
credentials are validated at start-up by `require_slack_credentials()` instead.

## Adding a tool

1. Create `scout/tools/your_tool.py` with a `register(reg: ToolRegistry)`
   function that defines one or more `@reg.tool` functions. The signature and
   docstring become the JSON schema the model reads, so annotate the parameters
   and write a Google-style `Args:` block — each entry becomes that parameter's
   description.
2. Add the module to the `tool_modules` of the relevant `AgentSpec` in
   `scout/agents/`.
3. Restart. The model discovers it automatically.

Tools should never raise for an expected failure (a site being down, a bad
argument): return a sentence the model can read and act on.

For a new job source, add a module under `scout/tools/jobs/`; the package's
`__init__.py` has the shared pieces — `clamp_int`, `is_ai_ml_role`, and the
`JobPosting`/`render_postings` output format every source uses.

## Adding an agent

1. Create `scout/agents/your_agent.py` defining a `SPEC = AgentSpec(...)` with a
   `key`, `name`, `system_prompt`, and `tool_modules` (a subset of `scout/tools/`).
   `default_backend` is optional — omit it to inherit `settings.DEFAULT_BACKEND`.
   Set `tailor_with_resume=True` to run it behind the Resume Parser hand-off.
2. Register it in `scout/agents/__init__.py` (`AGENTS = {… your_agent.SPEC.key: your_agent.SPEC}`).
3. Each agent is its own Slack app (separate bot tokens), so run it as a separate
   process with its own `.env`: `AGENT=your_agent python run.py`.

## Operational notes

- Conversation history is per-user and in memory (last `MAX_TURNS` exchanges).
  `--reset` or a restart clears it; there is no persistence.
- The bot only responds in DMs (`channel_type == "im"`); it ignores channels,
  other bots, and message edits.
- Long replies are split across several Slack messages on line boundaries, so
  job links are never cut in half.
- `logs/bot.log` rotates at `LOG_MAX_BYTES` (5 MB, 3 backups).
- `get_location` uses IP-based geolocation (`ip-api.com`) — city-level, HTTP-only
  on the free tier, and it shares the host's public IP with that service.
- The job tools use each employer's own public endpoint (Amazon's careers JSON,
  Greenhouse's board API, Workday for Northeastern, an RSS feed for BU) rather
  than scraping, except for Google, which publishes no API.
- Secrets live only in `.env`, and `.env`, `data/`, and `logs/` are all
  git-ignored — see `.gitignore`.
