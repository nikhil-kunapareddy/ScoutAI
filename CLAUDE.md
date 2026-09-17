# CLAUDE.md

Scout is a multi-agent platform for Slack DM bots, built on **LangGraph**. An
agent = a system prompt + a set of tools, running on Claude (default), Ollama, or
the Meta Llama API, switchable per-user at runtime. Two job-search agents ship
with it, both tailored to the user's resume in `data/`, plus a Referral Window
that keeps the list of companies the user has a connection at — which the job
agents read when they rank results, and which the Window itself searches as a
scope, so its answer is every opening the user could ask a referral for.

`README.md` is the short, outward-facing intro; `docs/` is every other document
— the user-facing set (`getting-started`, `configuration`, `architecture`,
`extending`, `deployment`, `operations`) and the project ones (`CONTRIBUTING`,
`CODE_OF_CONDUCT`, `SECURITY`, `CHANGELOG`), all indexed by `docs/README.md`.
Only three files stay in the root, each because something reads it there:
`README.md` and `LICENSE` are named by path in `pyproject.toml`, and this file
is the working map and the conventions to keep.

## Commands

```bash
make install                      # venv + `pip install -e ".[dev]"`
source .venv/bin/activate

scout run                         # BigTech Agent (default); python run.py still works
scout run --agent edu             # same as AGENT=edu scout run
scout run --agent referral        # Referral Window (the referral list)
scout run --agent resume          # Resume Parser alone, for debugging

scout digest                      # run every job agent, DM one merged report
scout stats --days 7              # read the turn metrics back
scout agents                      # what is registered, and each agent's tools
scout doctor                      # what is configured here; non-zero if it can't start

make check                        # ruff, mypy, pytest --cov — what CI runs
```

`scout/cli.py` imports `settings` inside each command, never at module scope:
`--agent` has to reach the environment before the import that layers
`.env.<agent>`. `tests/test_cli.py` guards that.

Deployed on an EC2 `t4g.micro` under systemd; `./deploy/deploy.sh` ships the
working tree and restarts. See the AWS section of docs/deployment.md.

## Layout

| Path | What lives there |
|------|------------------|
| `scout/cli.py` | The command line: `run`, `digest`, `stats`, `agents`, `doctor` |
| `run.py` | Thin shim: `python run.py` == `scout run` (the systemd units call it) |
| `scout/checks.py` | What `scout doctor` reports; local reads only, never a network call |
| `scout/core/agent.py` | `AgentSpec`, `AgentState`, `ConversationalAgent`, and the graph builder |
| `scout/core/runner.py` | `GraphRunner` and `Agent` — driving a compiled graph for many users |
| `scout/core/models.py` | One LangChain chat model per backend, built lazily |
| `scout/core/settings.py` | All shared config, from `.env` + `.env.<agent>` |
| `scout/tools/` | `ToolRegistry` + tool modules (`clock`, `location`, `resume`, `referrals`) |
| `scout/tools/jobs/` | One module per job source, over shared parts: `fetch` (HTTP), `feeds` (RSS), `posting` (the record, merging, rendering), `relevance` (which titles count), `hosted_board` (the Greenhouse/Ashby shape), `directory` (company → board) |
| `scout/agents/` | One `AgentSpec` per agent, plus `resume_tailored.py` (the orchestration) |
| `scout/slack/bot.py` | Slack adapter; talks only to `ConversationalAgent` |
| `scout/slack/formatting.py` | `split_message`, shared by the bot and `notify` |
| `scout/slack/notify.py` | Opening a DM nobody asked for (digest, alerts) |
| `scout/core/checkpoints.py` | In-memory or SQLite checkpointer, per `CHECKPOINT_DB` |
| `scout/core/referrals.py` | The referral list store — JSON in `state/`, keyed by user |
| `scout/core/metrics.py` | The one-line-per-turn record |
| `scout/core/tracing.py` | Langfuse — the per-turn call tree; the only module that imports it |
| `scout/digest.py`, `stats.py`, `alert.py` | Scheduled entry points, read back, failure DM |
| `deploy/` | `deploy.sh` plus the systemd units the box runs |
| `scripts/sync_requirements.py` | Regenerates `requirements*.txt` from `pyproject.toml` |
| `docs/` | Every document except the root three: the user-facing set plus `CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY` and `CHANGELOG`, indexed by `docs/README.md` |

## The two graphs

Every agent compiles to the same model/tools loop (`build_agent_graph`):

```
START ──▶ model ──tool calls?──▶ tools ──┐
             │ none                      │
             ▼                           │
            END      ◀───────────────────┘
```

A `tailor_with_resume` agent is wrapped in the orchestration graph
(`resume_tailored.py`), which caches the resume profile *in graph state*:

```
START ──▶ (profile cached?) ──yes──▶ job_agent ──▶ END
               │ no                  (subgraph)
               ▼                          ▲
         parse_resume ─── profile ────────┘
```

Three seams hold the layers apart — keep them intact:

- **LangChain chat models** — one provider each, in `models.py`. New provider =
  one builder plus one entry in `_BUILDERS`.
- **`ConversationalAgent`** — everything `scout/slack/` knows about an agent.
  `GraphRunner` implements it once for both `Agent` and `ResumeTailoredAgent`, so
  the adapter has no special cases. Don't reach past it from the Slack layer.
- **`AgentSpec`** — declares an agent. Adding one must not touch the graph.

## Conventions

- **Adding an agent:** one file in `scout/agents/` defining `SPEC = AgentSpec(...)`,
  registered in `AGENTS` in `scout/agents/__init__.py`. Omit `default_backend` to
  inherit `settings.DEFAULT_BACKEND`. Never edit the graph to add an agent.
- **The digest derives its agents**, it does not list them: `tailor_with_resume`
  is the marker, so a new job agent joins the digest by existing. Don't add a
  registry beside `AGENTS`.
- **Adding a tool:** a module with `register(reg: ToolRegistry)` defining `@reg.tool`
  functions, then add the module to the relevant spec's `tool_modules`. The
  signature and Google-style `Args:` block *are* the schema LangChain derives and
  the model reads — annotate every parameter and document it.
- **Adding a job source:** one module per source under `scout/tools/jobs/` (the
  platforms differ too much to share an implementation). Import the shared parts
  from the module that owns them — `fetch.get_rows`/`get_text`, `feeds.items`,
  `posting.JobPosting`/`merge_queries`/`render_postings`, `relevance.is_ai_ml_role`
  — rather than reimplementing them, so every source renders identically and
  means the same thing by "unreachable". A source on a platform that hosts many
  companies is a `HostedBoard`, not a ninth implementation.
- **Every source exposes its search twice:** a registered tool returning Slack
  text, and a `search(...) -> list[JobPosting] | None` matching `Searcher`, which
  the tool is a thin renderer over. `None` means the source was unreachable and
  is never the same answer as `[]`. If the source is a company board, add it to
  `jobs/directory.py` so a referral there is searchable.
- **Tools never raise for an expected failure** (site down, no results). Return a
  sentence the model can read and act on. Argument *types* are now LangChain's
  problem — see below.
- **A tool that needs to know *who* is asking takes `*, config: RunnableConfig`.**
  LangChain injects it and hides it from the schema, so the model never sees a
  user id and cannot get one wrong. `referrals.owner_for(config)` turns it into
  an owner. See `scout/tools/referrals.py`.
- **Observability is added to a config, never to a code path.** `respond`
  passes its run config through `tracing.observed`, which returns an
  instrumented copy or the original; nothing outside `core/tracing.py` imports
  Langfuse, and no branch in the graph knows whether a turn is traced.
- **Config goes in `settings.py`**, not scattered `os.environ` reads. Nothing there
  raises on import — that's what keeps the package importable without a `.env`.
- **Anything that can't be shared between agents goes in `.env.<agent>`**, which
  layers over `.env`. Today that's the Slack token pair (one app per agent) and
  `CHECKPOINT_DB`. Shared keys stay in `.env` — don't copy them per agent.
- **`langchain` is a dependency of Langfuse's handler, not of Scout.** Nothing
  in the package imports it — langfuse's LangChain integration imports it to
  tell v0 from v1 and refuses to load without it. Keep importing from
  `langchain_core`.
- **Dependencies are declared in `pyproject.toml`.** `requirements*.txt` are
  generated (`make requirements`); `tests/test_packaging.py` fails on drift,
  because the Dockerfile and `deploy.sh` install from the flat list.
- Ruff, `line-length = 100`, py310 target. `ANN` is on for `scout/`, so every
  function there is annotated, and `mypy` runs with `disallow_untyped_defs`.
  The two scoped `mypy` overrides (docx2txt, `scout.core.models`) are explained
  in `pyproject.toml` — prefer fixing a type over widening them.

## Invariants worth not breaking

- **History is the checkpointer thread**, one per Slack user, keyed by user id.
  It holds provider-agnostic LangChain messages, which is what lets a user switch
  model mid-conversation. `reset` deletes the thread.
- **Tool traffic is checkpointed too** (unlike the old hand-rolled loop, where it
  was per-turn). The model remembers what it looked up; it also counts against
  the `MAX_TURNS` window in `_within_window`.
- **`trim_messages(..., start_on="human")` is load-bearing.** Providers reject a
  conversation opening on the assistant's side, and a tool result must never lead
  it or be split from the call it answers.
- **`bind_tools` discards kwargs bound before it.** In `models.with_tools`, the
  provider options go on *after* the tools or they vanish silently — that is what
  `test_tools_and_request_options_both_survive_binding` guards.
- **The hop limit repairs the thread.** On `GraphRecursionError`, `_give_up`
  answers the abandoned tool calls before recording the apology; skipping it
  breaks the user's *next* message, not just this one.
- **A subgraph is handed the recursion limit afresh**, so `_min_steps` is a floor,
  never an addition — adding to it hands the inner loop extra tool hops.
- **The fallback retry lives in the model node**, not around the turn: a node that
  raises commits nothing, so the retry starts clean while keeping tool results
  the turn already fetched. An empty `FALLBACK_BACKEND` disables it (the
  container has no Ollama).
- **Per-user locks stay.** slack-bolt dispatches on a thread pool; one lock per
  user serializes their turns while other users run concurrently.
- **State is in memory unless `CHECKPOINT_DB` says otherwise** — see
  `core/checkpoints.py`. Empty means `InMemorySaver`, which is what tests and
  local runs get. A path means SQLite (WAL, `check_same_thread=False`, because
  slack-bolt is threaded), and then history *and* the cached resume profile
  outlive the process. The deployed bot sets it, so a changed resume needs
  `--reset` to take effect.
- **The annotation `RunnableConfig` must stay exactly that.** Widening it to
  `RunnableConfig | None` stops LangChain recognising it: the injection silently
  stops and `config` reappears as a parameter the model is asked to fill.
  `test_the_user_id_is_not_in_the_schema_the_model_sees` guards it.
- **The referral list is deliberately *not* checkpointed.** History is
  disposable — `--reset` drops a thread — but the list is something the user
  typed once. It is JSON in `state/`, which `deploy.sh` excludes from its rsync,
  so a redeploy can't overwrite the box's copy with a laptop's.
- **Job agents get `referrals_read`, never `referrals`.** Only the Referral
  Window may write. A searching agent with `add_referral` in reach eventually
  records something mid-search that the user never asked for.
- **The Referral Window searches only within the list.** It writes the list *and*
  searches, which the rule above would otherwise forbid; what makes it safe is
  that its single search tool is `search_referral_jobs`, so there is nothing to
  search with off-list. Never hand it a per-source tool.
  `test_the_referral_window_can_only_search_within_the_list` guards it.
- **`search_referral_jobs` names what it could not check** — a board that was
  down, a company with no board — separately from "nothing open". Its only claim
  is completeness, and a silently short list is the one way to break it. That is
  why company-to-board routing is a table in `jobs/directory.py` rather than
  something the model is asked to get right.
- **The digest runs on `digest:<key>` threads, which are not people.**
  `owner_for` maps them onto `DIGEST_SLACK_USER`; without that the daily report
  looks up a user id that has no referrals and quietly stops ranking by them.
- **Two bots on one `CHECKPOINT_DB` share a thread.** Thread ids are the bare
  Slack user id (`_config` in `core/agent.py`), not namespaced by agent, so each
  interactive agent needs its own DB path in its `.env.<agent>` — otherwise your
  conversations with the two bots interleave into one history. The digest is
  already safe: it uses `digest:<key>`.
- **Monitoring never breaks a turn.** The Langfuse import, client and handler
  are built inside one `try` in `tracing.handler`, which logs once and leaves
  tracing off for the process; export is batched on a background thread, so an
  unreachable backend costs a turn nothing. The traced config is a *copy*, so
  the checkpointer reads and the `_give_up` repair stay outside the trace.
  `test_a_turn_still_answers_when_tracing_will_not_start` guards it.
- **Trace names are an API, and the trace's input/output is the reply.**
  `SLACK_TRACE`/`DIGEST_TRACE` carry no user, agent or model: Langfuse
  dashboards, saved views and judges target names, so anything per-run in one
  fragments all three. `traced` owns the root span for the same reason the name
  matters — trace-level input and output are read off it, and they have to be
  the question and the answer, not LangGraph's message list. `Turn.answered`
  is how the reply gets there, on the stuck path too.
- **Langfuse settings the SDK also reads are read under both names.**
  `LANGFUSE_BASE_URL` outranks the `host=` we pass to the client, so reading
  only `LANGFUSE_HOST` let `scout doctor` report the EU default while traces
  went to the US one. `_env_any` reads what the SDK reads, in its order.
  `test_env_any_reads_the_names_in_order` guards it.
- **Langfuse's key pair is the switch**, unlike `LANGSMITH_TRACING`, which
  LangSmith reads itself. `tracing.REQUIRES` is shared with `scout doctor`, so
  the report and the runtime cannot disagree; half a pair is a warning, because
  it is the one state that reads as "on" in a `.env` while tracing nothing.
- **The suite must ship no traces.** `tests/conftest.py` pins
  `LANGSMITH_TRACING=false`, empties the Langfuse key pair *and* the two host
  names — the host because the SDK reads `LANGFUSE_BASE_URL` ahead of the host
  it is handed, which had one test posting to Langfuse Cloud. In
  `tests/test_tracing.py` the `scripted_handler` fixture pulls in `root_span`
  and `attributes` for the same reason: tracing on with a real client builds a
  real span and queues it for export.
- The bot answers DMs only (`channel_type == "im"`), ignoring channels, bots, and
  edits.

## Tests

`tests/` scripts the chat models (`ScriptedModel` in `conftest.py`, installed into
`scout.core.models` as a real backend) and stubs every HTTP call (`FakeRequests`
and `call_tool`, same file), so the suite runs in CI with no `.env` and no
network. Keep it that way — a test that reaches a real job board or model API
doesn't belong here. Job sources are stubbed at one seam, `jobs/fetch.requests`,
which is the point of them all going through `fetch`.

A test must not depend on the developer's own `.env` or `data/` either: point
`settings` at a `tmp_path` (see `tests/test_checks.py`), or it passes here and
fails in CI. Coverage is 100% of statements *and* branches, and `fail_under`
holds it there; CI runs 3.10–3.13.

The suite also pins `LANGSMITH_TRACING=false` (`tests/conftest.py`): importing
`settings` loads the developer's `.env`, and a test run must not ship traces to
a hosted service.

## Don't commit

`.env`, `data/` (resumes), and `logs/` are git-ignored. Secrets come from the
platform, never the image — `.dockerignore` excludes `.env`.
