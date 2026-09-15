# Configuration

Every setting is an environment variable, read once at start-up by
[`scout/core/settings.py`](../scout/core/settings.py). Nothing there raises on
import, so a missing value leaves an empty string and the package stays
importable — credentials are checked when a command that needs them starts.

`scout doctor` prints what is actually in effect.

## Where settings come from

Three sources, and the first one to define a key wins:

```
real environment  >  .env.<agent>  >  .env
```

| File | What belongs in it |
|---|---|
| `.env` | Everything the agents share: model keys, limits, timeouts |
| `.env.<agent>` | Only what cannot be shared — today the Slack token pair, and `CHECKPOINT_DB` |
| The environment | Overrides for a single run, and how production injects secrets |

That ordering is deliberate: on the deployed box, systemd's `EnvironmentFile`
is authoritative over the `.env` that `deploy.sh` copies up, because
`load_dotenv()` never overwrites a variable that is already set.

`AGENT` is read *before* the files are loaded, since it decides which
`.env.<agent>` layers on top. `scout run --agent edu` sets it for you.

## Slack

| Variable | Default | Notes |
|---|---|---|
| `SLACK_BOT_TOKEN` | — | `xoxb-…`, from **OAuth & Permissions**. Required to run a bot or send a digest. |
| `SLACK_APP_TOKEN` | — | `xapp-…`, from **Basic Information → App-Level Tokens**, scope `connections:write`. Required to run a bot; the digest does not need it. |

Both belong in `.env.<agent>`: each agent is a separate Slack app, and two
apps' tokens cannot share one name. Two processes on one bot token both answer
every DM.

## Agent selection

| Variable | Default | Notes |
|---|---|---|
| `AGENT` | `bigtech` | Which agent this process runs: `bigtech`, `edu`, `referral`, `resume`. `scout agents` lists them. |

One process per agent. The digest runs them all in-process and needs no second
app.

## Models

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the Claude backend, and makes it the default. |
| `ANTHROPIC_MODEL` | `claude-opus-5` | Any Claude model id. |
| `ANTHROPIC_MAX_TOKENS` | `16000` | Per-reply ceiling. |
| `ANTHROPIC_EFFORT` | `medium` | Thinking depth: `low`, `medium`, `high`, `xhigh`, `max`. Trades latency for depth; `medium` keeps a multi-hop tool loop snappy in Slack. |
| `OLLAMA_HOST` | `http://localhost:11434` | Where the local model server listens. |
| `OLLAMA_MODEL` | `llama3.2:3b` | Pull it first: `ollama pull llama3.2:3b`. |
| `LLAMA_API_KEY` | — | Enables the Meta Llama API backend (`--api`). |
| `LLAMA_MODEL` | `Llama-4-Maverick-17B-128E-Instruct-FP8` | |
| `LLAMA_BASE_URL` | `https://api.llama.com/compat/v1` | Meta's OpenAI-compatible *base* URL, not the `/chat/completions` path. |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `300` | Generous, because generation on a local model can take minutes. |

Users switch backends per conversation with `--claude`, `--ollama`, and
`--api`; the choice lasts until they change it or the process restarts.

## Backend selection and fallback

| Variable | Default | Notes |
|---|---|---|
| `FALLBACK_BACKEND` | `ollama` | Where a failed model call is retried, once, inside the same turn. Empty disables the retry. |

The default backend is not configured directly: it is Claude when
`ANTHROPIC_API_KEY` is set, and Ollama otherwise. With no key there is nothing
to fall back *from*, so the retry becomes a no-op.

**Set `FALLBACK_BACKEND=` (empty) in a container.** There is no Ollama in the
image, so leaving it on makes every Claude failure fail twice.

## Conversation

| Variable | Default | Notes |
|---|---|---|
| `MAX_TURNS` | `20` | Message pairs kept per user. Tool calls and their results count too. |
| `MAX_TOOL_HOPS` | `5` | Tool round-trips allowed in one message. The digest raises this to 8, since one turn sweeps every source. |

## State

| Variable | Default | Notes |
|---|---|---|
| `CHECKPOINT_DB` | *(empty)* | Empty keeps history in memory, so a restart is a clean slate — right for local runs and tests. A path (absolute, or relative to the project root) means SQLite, and both history and the parsed résumé profile survive restarts. |

Give each interactive agent its **own** path in its `.env.<agent>`. Thread ids
are the bare Slack user id, so two bots sharing one database interleave your
conversations into a single history. The digest is already safe: it uses
`digest:<key>` threads.

With `CHECKPOINT_DB` set, a changed résumé needs a `--reset` to take effect —
the cached profile outlives the process too.

## Résumé and referrals

| Variable | Default | Notes |
|---|---|---|
| `RESUME_DIR` | `data` | Where the Resume Parser looks. Relative to the project root, so a clone needs nothing here; set it if you installed the package instead of cloning. |
| `REFERRALS_FILE` | `state/referrals.json` | The referral list. Unlike `CHECKPOINT_DB` this has a real default: history is disposable, a list you typed by hand is not. |

`state/` is excluded from `deploy.sh`'s rsync, so a redeploy cannot overwrite
the box's list with a laptop's.

## Daily digest

| Variable | Default | Notes |
|---|---|---|
| `DIGEST_SLACK_USER` | — | Your Slack member id (profile → **Copy member ID**) — yours, not the bot's. Required by `scout digest`. |
| `DIGEST_MAX_ROLES` | `5` | Roles asked of each agent, so the message holds this many *per section*. |

`DIGEST_SLACK_USER` does double duty: digest threads are not people, so it is
also the owner whose referral list a digest turn reads.

## Turn metrics

| Variable | Default | Notes |
|---|---|---|
| `USD_PER_MTOK_IN` | `0` | Dollars per million input tokens. |
| `USD_PER_MTOK_OUT` | `0` | Dollars per million output tokens. |

Left at zero, each turn's log line reports tokens only. Rates are
configuration rather than a table in the code because they change, and a stale
hard-coded number is worse than none. See [operations](operations.md).

## Logging

| Variable | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG` also un-mutes slack-bolt, urllib3, and the provider SDKs. |
| `LOG_MAX_BYTES` | `5242880` | `logs/bot.log` rotates at 5 MB. |
| `LOG_BACKUP_COUNT` | `3` | Rotated files kept. |

## Tool HTTP requests

| Variable | Default | Notes |
|---|---|---|
| `TOOL_REQUEST_TIMEOUT_SECONDS` | `30` | Job-board APIs and the one scraped page. |
| `GEO_REQUEST_TIMEOUT_SECONDS` | `10` | `ip-api.com`, for `get_location`. |

## Tracing (optional)

Read by LangSmith itself, not by Scout — no code path looks at them:

| Variable | Notes |
|---|---|
| `LANGSMITH_TRACING` | `true` turns on per-node tracing. LangGraph instruments itself. |
| `LANGSMITH_API_KEY` | `lsv2_pt_…` |
| `LANGSMITH_PROJECT` | Project name to group traces under. |
| `LANGSMITH_HIDE_INPUTS` / `LANGSMITH_HIDE_OUTPUTS` | Keep the call tree, latency, and token counts while leaving prompts and completions behind. |

Tracing sends prompts and completions off the machine, **including the résumé
profile in the system prompt**. The `HIDE_` pair exists for exactly that
reason.

## What is not configured here

An agent's system prompt, its tool set, and its default backend live on its
`AgentSpec` in `scout/agents/` — they are code, not environment. Adding a
setting means adding it to `settings.py` and to `.env.example`; scattered
`os.environ` reads are what that file exists to prevent.
