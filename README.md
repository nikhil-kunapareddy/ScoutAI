# Scout

A small **multi-agent platform** for Slack-based AI agents. Each agent is a Slack
DM bot backed by either a local LLM (via [Ollama](https://ollama.com)) or the
hosted [Meta Llama API](https://llama.developer.meta.com) — switchable per-user
at runtime — and can call tools for live information.

The first agent is **BigTech Agent**: a job-search assistant for AI/ML roles
(current time, date, location, your resume, and recent Amazon/Google openings).
Adding another agent is one small file — see [Adding an agent](#adding-an-agent).

## Architecture

```
Slack DM
   │
   ▼
scout/slack/app.py ──── DM handler + text commands (--ollama, --api, --reset, ...)
   │
   ▼
scout/core/agent.py ─── Agent: conversation history + tool-call loop (per-user backend)
   │                          │                        ▲
   │ (model request)          │ (tool calls)           │ defined by
   ▼                          ▼                        │
scout/core/backends/   scout/tools/ ── ToolRegistry    scout/agents/bigtech.py (AgentSpec)
   ├── ollama.py           ├── datetime_tools.py   get_current_time, get_current_date
   └── llama_api.py        ├── location.py         get_location
                           ├── resume.py           get_resume_profile
                           ├── amazon_jobs.py      search_amazon_jobs
                           └── google_jobs.py      search_google_jobs
```

An `AgentSpec` declares an agent (name, system prompt, tool set, default
backend). `Agent` runs it: the chosen backend decides when to call a tool,
`Agent` runs the registered Python function in-process and feeds the result back,
and the model folds it into its reply.

## Project layout

| Path | Purpose |
|------|---------|
| `run.py` | Entry point — runs the agent named by `AGENT` (default `bigtech`) |
| `scout/core/agent.py` | `Agent` runtime (history + tool loop) and `AgentSpec` |
| `scout/core/settings.py` | Shared infra config from `.env` (tokens, models, timeouts) |
| `scout/core/paths.py` | Filesystem paths (no env dependencies) |
| `scout/core/backends/` | One module per model backend (Ollama, Llama API) |
| `scout/tools/` | Shared tool library + the tool registry |
| `scout/slack/app.py` | Slack adapter — wires an `Agent` to Slack DMs |
| `scout/agents/` | One `AgentSpec` per agent (`bigtech.py`, …) |
| `data/` | Your resume(s) — read by `get_resume_profile` |
| `logs/` | Runtime logs (`bot.log`) |

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
cd /Users/nikhilkunapareddy/Documents/ScoutAI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your Slack tokens into .env
```

Make sure Ollama is running and the model is pulled:

```bash
ollama serve                  # if not already running
ollama pull llama3.2:3b
```

Drop your resume into `data/` (PDF, DOCX, TXT, or MD) so job search can tailor
results to your background.

## Run it

```bash
python run.py
```

Then just DM the bot in plain language — it routes through the model with all
tools available. For example:

- "latest amazon jobs" → recent Amazon AI/ML roles (title, link, date posted).
- "what google roles are open?" → recent Google AI/ML roles (newest-first;
  Google doesn't publish posting dates, so no date is shown).
- "what's my location?" / "what time is it?" / "read my resume".

Control commands (typed as plain messages) are listed under
[Choosing a model backend](#choosing-a-model-backend) below — e.g. `--reset`,
`--ollama`, `--api`, `--help`.

## Choosing a model backend

Each user can switch, at any time, between the local Ollama model and the hosted
**Meta Llama API** by sending a text command in the DM. The choice persists
per-user until changed (and resets to the agent's `default_backend` on restart):

- **`--ollama`** — use the local Ollama model (`OLLAMA_MODEL`).
- **`--api`** (or `--llama`) — use the Meta Llama API (`LLAMA_MODEL`). Requires
  `LLAMA_API_KEY` in `.env` (get one at https://llama.developer.meta.com).
- **`--backend`** — show which model you're currently using.
- **`--help`** — list all commands.

Conversation history is stored as plain text and shared across backends, so you
can switch mid-conversation without losing context.

## Adding a tool

1. Create `scout/tools/your_tool.py` with a `register(reg: ToolRegistry)`
   function that defines one or more `@reg.tool` functions. The function's
   signature and docstring become the tool's JSON schema, so type the parameters
   and write a clear docstring.
2. Add the module to the agent(s) that should use it — list it in the
   `tool_modules` of the relevant `AgentSpec` in `scout/agents/`.
3. Restart. The model discovers it automatically.

## Adding an agent

1. Create `scout/agents/your_agent.py` defining a `SPEC = AgentSpec(...)` with a
   `key`, `name`, `system_prompt`, `tool_modules` (a subset of `scout/tools/`),
   and `default_backend`.
2. Register it in `scout/agents/__init__.py` (`AGENTS = {... your_agent.SPEC.key: your_agent.SPEC}`).
3. Each agent is its own Slack app (separate bot tokens), so run it as a separate
   process with its own `.env`: `AGENT=your_agent python run.py`.

## Notes

- Conversation history is per-user, in memory (last 20 turns). `--reset` or a
  restart clears it.
- The bot only responds in DMs (`channel_type == "im"`); it ignores channels
  and other bots.
- `get_location` uses IP-based geolocation (`ip-api.com`) — city-level, and it
  shares the host's public IP with that service.
- `search_amazon_jobs` uses Amazon's own public careers JSON API (not HTML
  scraping), filtered to US, AI/ML-relevant roles posted recently.
