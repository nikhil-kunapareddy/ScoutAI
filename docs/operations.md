# Operations

What running Scout looks like once it is running: the daily digest, one log line
per turn, tracing when you want the full picture, and an alert when a unit dies.

- [The daily digest](#the-daily-digest)
- [Turn metrics](#turn-metrics)
- [Reading them back](#reading-them-back)
- [Tracing with LangSmith](#tracing-with-langsmith)
- [Alerts](#alerts)
- [When the bot goes quiet](#when-the-bot-goes-quiet)

## The daily digest

```bash
scout digest          # or: python -m scout.digest
```

Runs every job agent, then DMs you one merged report. Which agents run is
**derived, not listed**: any spec with `tailor_with_resume` counts, so a new job
agent joins tomorrow's digest without touching `scout/digest.py`.

Each agent runs on its own thread (`digest:<key>`), kept apart from the threads
the Slack bot uses. Two things follow. The digest never eats into your chat
history's `MAX_TURNS` window — ask the bot something right afterwards and it has
no idea one happened. And because the thread persists (given `CHECKPOINT_DB`),
each agent can see what it reported on previous days and skip repeats. That
thread memory is the only dedupe available here: agents return prose, not the
structured `JobPosting` objects their tools built.

One agent failing does not lose the others: its section says so, and the rest
are still delivered.

Needs `DIGEST_SLACK_USER` and `SLACK_BOT_TOKEN`. It posts over the Web API and
never opens a socket, so no `SLACK_APP_TOKEN`. On the box a systemd timer fires
it at 08:00 local with `MAX_TOOL_HOPS` raised to 8, since one digest turn sweeps
every source.

## Turn metrics

Every turn writes one line, from `GraphRunner.respond` — the single place that
sees a whole turn:

```
turn agent="BigTech Agent" thread=U0B8… backend=anthropic answered_by=anthropic \
  outcome=ok seconds=12.4 model_calls=3 tool_calls=4 in_tokens=8123 out_tokens=512
```

| Field | Meaning |
|---|---|
| `outcome` | `ok`, `stuck` (ran out of tool hops), or `error` (raised out of the graph) |
| `answered_by` | Differs from `backend` exactly when the fallback rescued the turn |
| `model_calls` | One per hop through the model node — the hop count, in effect |
| `in_tokens` / `out_tokens` | Summed across the turn's model calls |
| `usd` | Only when `USD_PER_MTOK_IN`/`OUT` are set |

`key=value` pairs on purpose: greppable with `journalctl`, parseable by
CloudWatch Logs Insights if the box ever gets an IAM role, and readable as-is.

Rates are configuration rather than a table baked into the code — they differ
per backend, they change, and a stale hard-coded number is worse than none.
Tokens are always reported, so spend stays derivable after the fact either way.

## Reading them back

```bash
scout stats --days 7                            # from the journal, on the box
journalctl -u 'scout@*' -o cat | scout stats -   # or from a pipe
```

```
agent                         turns  fail  tools    in_tok  out_tok  med_s  max_s      usd
BigTech Agent                    31     1     58    248913    12044   11.8   41.2     1.24
Edu Agent                        14     0     19     96422     4103    8.1   18.7     0.47
```

Parsing is deliberately forgiving: a line that does not look like a turn is
skipped rather than fatal, so this keeps working when the log format grows a
field.

## Tracing with LangSmith

For the full picture of a turn — every node, model call, and tool call, with the
prompts and tool output attached — set these and restart. No code change;
LangGraph instruments itself:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_...
LANGSMITH_PROJECT=scout
```

It complements the `turn` line rather than replacing it: the log line is the
cheap always-on record, LangSmith is what you open when a digest returns
something odd and you want to see which tool returned what.

Be aware it sends prompts and completions off the box, **résumé profile
included**. `LANGSMITH_HIDE_INPUTS=true` and `LANGSMITH_HIDE_OUTPUTS=true` keep
the call tree, latency, and token counts while leaving the content behind.
Tracing is best-effort and batched in the background, so an unreachable
LangSmith slows nothing and fails no turns.

## Alerts

`scout-alert@.service` DMs you when a unit fails, wired up as systemd's
`OnFailure=`. Slack rather than CloudWatch on purpose: the box has no IAM role,
and an alert that needs a console visit is an alert nobody reads.

Note what it does and does not catch. `Restart=always` makes a single crash
silent and automatic, so an alert means the unit **exhausted its restart
burst** — which is the signal worth waking up for. A bot that is running but has
quietly lost its websocket still looks healthy; the daily digest is the backstop
for that, since its absence is visible.

## When the bot goes quiet

In order, because each step is cheaper than the next:

```bash
scout doctor                              # is it still configured? (local reads only)
systemctl status 'scout@*'                # is it running?
journalctl -u scout@bigtech -n 50 -o cat  # what did it say last?
scout stats --days 1                      # did any turn complete, and how did it end?
```

Common outcomes:

- **`outcome=stuck` on every turn** — the question needs more hops than
  `MAX_TOOL_HOPS` allows, or a source is timing out and eating the budget.
- **`answered_by` never matches `backend`** — the hosted key is missing,
  expired, or rate-limited, and every turn is being rescued by the fallback.
- **Nothing in the log at all** — the websocket is gone but the process is
  alive. `systemctl restart scout@<agent>`.
- **Two replies to every message** — two instances share one bot token.
- **Your conversations with two agents are interleaved** — they share a
  `CHECKPOINT_DB`.

`logs/bot.log` rotates at `LOG_MAX_BYTES` (5 MB, 3 backups). `LOG_LEVEL=DEBUG`
also un-mutes slack-bolt, urllib3, and the provider SDKs, which is loud and
occasionally exactly what you need.
