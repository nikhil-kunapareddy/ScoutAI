# Operations

What running Scout looks like once it is running: the daily digest, one log line
per turn, tracing when you want the full picture, and an alert when a unit dies.

- [The daily digest](#the-daily-digest)
- [Turn metrics](#turn-metrics)
- [Reading them back](#reading-them-back)
- [Tracing with Langfuse](#tracing-with-langfuse)
- [Alerts](#alerts)
- [When the bot goes quiet](#when-the-bot-goes-quiet)

## The daily digest

```bash
scout digest                      # the default agent's digest
scout digest --agent edu          # the Edu Agent's
scout digest --agent referral     # what is open across your referral list
```

**One agent, one DM, in that agent's own Slack window.** Each agent is a
separate Slack app, so the digest runs as one process per agent and posts with
that agent's own bot token — the BigTech report lands in the BigTech DM, the
Referral Window's in its own. A single process can only hold one token, which
is why running them together would stack every report into one conversation.

Which agents *have* a digest is **derived, not listed**: any spec with
`in_digest` counts, so a new job agent gets a morning report without touching
`scout/digest.py`. It is a separate flag from `tailor_with_resume` because the
two answer different questions — the Referral Window searches a scope rather
than a résumé, and still has something to say every morning.

Each agent runs on its own thread (`digest:<key>`), kept apart from the threads
the Slack bot uses. Two things follow. The digest never eats into your chat
history's `MAX_TURNS` window — ask the bot something right afterwards and it has
no idea one happened. And because the thread persists (given `CHECKPOINT_DB`),
each agent can see what it reported on previous days and skip repeats. That
thread memory is the only dedupe available here: agents return prose, not the
structured `JobPosting` objects their tools built.

### What it looks like

The header is written by `run_digest`; the rest is the agent, held to a layout
by `DIGEST_REQUEST`:

```
*BigTech Agent — Sat 19 Sep 2026*

1. *Senior ML Engineer, Recommendations* — Netflix
    Ranking and retrieval, which is what your last two roles were.
    2 days ago · https://explore.jobs.netflix.net/careers/job/…

2. *Applied Scientist II* — Amazon
    Referral: you know someone here.
    4 days ago · https://amazon.jobs/en/jobs/…

Searched all 22 boards. Bloomberg gives no posting dates; Cisco was unreachable.
```

The layout is asked for rather than rendered, because what an agent returns is
prose — the structured `JobPosting` objects its tools built are gone by then,
which is the same reason the thread is the only dedupe. The fields themselves
were already identical across every source, from `render_postings`; pinning the
request is what stops the *report* drifting between days and between backends.
Two rules in it earn their place: repeat the tool's date word for word (Workday
gives only "5 Days Ago", Bloomberg gives nothing), and leave the title and date
to the code, or the message grows two headers.

A failed run still DMs you: the message says what broke, because a silent
morning is indistinguishable from a bot that died.

Needs `DIGEST_SLACK_USER` and `SLACK_BOT_TOKEN`. It posts over the Web API and
never opens a socket, so no `SLACK_APP_TOKEN`. `DIGEST_MAX_ROLES` is per agent,
since each run reads its own `.env.<agent>` — the Referral Window's is set
higher, because a capped list would break the one claim it makes.

On the box a timer per agent fires them at 08:00, 08:10 and 08:20
`America/Los_Angeles`, with `MAX_TOOL_HOPS` raised to 8 since one digest turn
sweeps every source. The timezone is the reader's, not the host's — the box
runs on Eastern and systemd converts. Staggered rather than simultaneous: see
[deployment](deployment.md#aws--what-this-repo-runs-on).

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
CloudWatch Logs Insights if the box's role is ever granted Logs, and readable as-is.

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

## Tracing with Langfuse

The `turn` line says what a turn cost. Langfuse says what it *did*: every graph
node, model call and tool call, nested, with the prompts, the tool output, the
token counts and the latencies attached. Set both keys and restart:

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your own instance
LANGFUSE_ENVIRONMENT=production            # tells the box from a laptop
```

The pair *is* the switch — one key alone leaves tracing off, and `scout doctor`
warns rather than letting a half-filled `.env` read as "traced".

One turn is one trace, rooted in a span Scout opens itself:

```
answer-slack-dm                     ← the trace: input = your message, output = the reply
└─ parse_resume                        the résumé stage, once per thread
│  ├─ ChatOllama          generation   model + token usage
│  └─ get_resume_profile  tool
└─ job_agent              agent        the job agent, as its own node in the agent graph
   ├─ ChatOllama          generation
   ├─ get_current_time    tool
   ├─ search_netflix_jobs tool
   └─ ChatOllama          generation   what it decided after the tools answered
```

| In Langfuse | Is |
|---|---|
| Trace name | `answer-slack-dm`, or `run-job-digest`. Deliberately free of the user, the agent, and the model: dashboards, saved views and judges target names, so anything per-run in one fragments all three |
| Input / output | Your message, and the reply you were sent — including the "I got stuck" one. Not the graph's message list, which answers a different question |
| Session | The checkpointer thread — so a user's turns line up in the order the bot replays them, and `digest:bigtech` is its own session |
| User | The same id. A digest thread is not a person and is not dressed up as one |
| Tags | The agent's display name, and `slack` or `digest` — the dimension worth comparing cost and latency across, since a digest turn sweeps every source on a raised hop limit |
| Metadata | `scout_backend`, the backend the turn was *asked* for. It differs from the model on the generation exactly when the fallback rescued the turn |
| Release | `scout.__version__`, which is the one thing a log line cannot tell you after a redeploy |
| Environment | `LANGFUSE_ENVIRONMENT`, when set |

Each model call is its own `generation`, interleaved with the `tool` calls it
asked for, so you can see what the agent decided after each tool answered —
rather than one generation wrapping the whole loop. The conditional edges
(`tools_condition`, `_needs_profile`) are dropped on the way out: they carry no
model call, no tool result, and Langfuse bills what it stores.

Scout wires this up itself, in [`scout/core/tracing.py`](../scout/core/tracing.py)
— the only module that knows Langfuse exists. Three things follow from how it is
wired, and they are the ones worth trusting:

- **A turn that cannot be traced still answers.** The client and the handler are
  built inside one `try`; a failure is logged once and tracing stays off until
  the process restarts.
- **An unreachable Langfuse costs nothing.** Export is batched on a background
  thread, so a turn does not wait for it. `scout digest` shuts the client down
  on the way out — what Langfuse asks a short-lived process to do — which is the
  one place a dead backend costs a couple of seconds, after the DM has already
  been sent.
- **The handler is built on the first traced turn, never at import**, so
  `scout doctor`, `scout stats`, and the test suite start no exporter thread.

Traces carry prompts and completions, **résumé profile included**.
`LANGFUSE_HIDE_CONTENT=true` masks them and keeps the rest — the call tree, the
latencies, and the token counts.

Tracing complements the `turn` line rather than replacing it: the log line is
the cheap always-on record, a trace is what you open when a digest returns
something odd and you want to see which tool returned what.

## Alerts

`scout-alert@.service` DMs you when a unit fails, wired up as systemd's
`OnFailure=`. Slack rather than CloudWatch on purpose: the box's role has
no CloudWatch permissions, and an alert that needs a console visit is an
alert nobody reads.

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
- **Turns are logged but no traces arrive** — `scout doctor` will say whether
  both Langfuse keys are set; if they are, the export failure is logged by the
  exporter itself, at `WARNING` and above.
- **Two replies to every message** — two instances share one bot token.
- **Your conversations with two agents are interleaved** — they share a
  `CHECKPOINT_DB`.

`logs/bot.log` rotates at `LOG_MAX_BYTES` (5 MB, 3 backups). `LOG_LEVEL=DEBUG`
also un-mutes slack-bolt, urllib3, and the provider SDKs, which is loud and
occasionally exactly what you need.
