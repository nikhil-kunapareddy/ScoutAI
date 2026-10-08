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

## Model

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Required.** Claude is the only model every agent runs on; without a key every turn fails, and `scout doctor` says so. |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | Any Claude model id. Haiku 4.5 is the cheapest. |
| `ANTHROPIC_MAX_TOKENS` | `16000` | Per-reply ceiling. |
| `ANTHROPIC_EFFORT` | empty | Thinking depth: `low`, `medium`, `high`, `xhigh`, `max`. Empty sends none, which Haiku 4.5 requires — it rejects the option. Set it for an Opus or Sonnet model; `medium` keeps a multi-hop tool loop snappy in Slack. |
| `MODEL_REQUEST_TIMEOUT_SECONDS` | `300` | Generous, because a long reply at a high effort can take minutes. |

There is no fallback model. `ChatAnthropic` retries transient API errors on its
own; a call that still fails ends the turn with the error in the reply, and the
user's next message starts clean.

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
| `RESUME_DIR` | `data` | The folder holding `resume.pdf`, the one file the Resume Parser reads. Relative to the project root, so a clone needs nothing here; set it if you installed the package instead of cloning. |
| `REFERRALS_FILE` | `state/referrals.json` | The referral list. Unlike `CHECKPOINT_DB` this has a real default: history is disposable, a list you typed by hand is not. |

`state/` is excluded from `deploy.sh`'s rsync, so a redeploy cannot overwrite
the box's list with a laptop's.

## Daily digest

| Variable | Default | Notes |
|---|---|---|
| `DIGEST_SLACK_USER` | — | Your Slack member id (profile → **Copy member ID**) — yours, not the bot's. Required by `scout digest`. |
| `DIGEST_MAX_ROLES` | `25` | Roles asked of each agent, so the message holds this many *per section*. |

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
| `LOG_LEVEL` | `INFO` | `DEBUG` also un-mutes slack-bolt, urllib3, and the Anthropic SDK. |
| `LOG_MAX_BYTES` | `5242880` | `logs/bot.log` rotates at 5 MB. |
| `LOG_BACKUP_COUNT` | `3` | Rotated files kept. |

## Tool HTTP requests

| Variable | Default | Notes |
|---|---|---|
| `TOOL_REQUEST_TIMEOUT_SECONDS` | `30` | Job-board APIs and the one scraped page. |
| `GEO_REQUEST_TIMEOUT_SECONDS` | `10` | `ip-api.com`, for `get_location`. |

## Langfuse (optional)

Traces every graph node, model call, and tool call, grouped by conversation.
Read by Scout itself — [`scout/core/tracing.py`](../scout/core/tracing.py) is
the only module that knows Langfuse exists.

| Variable | Default | Notes |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | — | `pk-lf-…`, from your project's **Settings → API keys**. |
| `LANGFUSE_SECRET_KEY` | — | `sk-lf-…`. Both keys together are the switch: one alone leaves tracing off, and `scout doctor` warns that it did. |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | `https://us.cloud.langfuse.com`, or your own instance. **Region matters** — a US key pair does not authenticate against the EU host. `LANGFUSE_BASE_URL` is accepted too, and wins if both are set, because that is the order the SDK reads them in. |
| `LANGFUSE_ENVIRONMENT` | — | Separates the deployed box from a laptop pointed at the same project. `LANGFUSE_TRACING_ENVIRONMENT`, the SDK's own name, is accepted too. |
| `LANGFUSE_HIDE_CONTENT` | `false` | Masks prompts and completions, keeping the call tree, latencies, and token counts. |

Traces carry prompts and completions, **including the résumé profile in the
system prompt** — `LANGFUSE_HIDE_CONTENT` is the answer to that. See
[operations](operations.md#tracing-with-langfuse).

## What is not configured here

An agent's system prompt and its tool set live on its
`AgentSpec` in `scout/agents/` — they are code, not environment. Adding a
setting means adding it to `settings.py` and to `.env.example`; scattered
`os.environ` reads are what that file exists to prevent.
