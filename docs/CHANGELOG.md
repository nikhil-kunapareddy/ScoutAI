# Changelog

Notable changes to Scout. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from its
first release.

## [Unreleased]

### Changed

- **The digest is one report per agent, in that agent's own Slack DM.** It used
  to run every job agent in a single process and post one merged message — and
  since a process holds one bot token, all of it arrived in whichever app was
  the default. Now it runs once per agent (`scout digest --agent edu`, or the
  `AGENT` the unit sets), so each report is posted by its own Slack app and
  lands in its own conversation.

  On the box that is `scout-digest@.service`, a template beside `scout@.service`
  reading the same per-agent env file, started by one timer per agent at 08:00,
  08:10 and 08:20 `America/Los_Angeles`. Staggered because a `t4g.micro` with no
  swap cannot hold three more Python processes at once, and with the timezone
  written out — the reader's, not the host's, which is Eastern — so systemd
  converts and the schedule cannot drift if either is ever re-set.

  `scout stats` reads `scout-digest@*` alongside `scout@*`, and `scout doctor`'s
  `digest` line now names the agent whose window the report would land in.

- **Which agents have a digest is its own flag, `AgentSpec.in_digest`**, instead
  of being read off `tailor_with_resume`. The two answer different questions:
  one prepends the résumé profile, the other means "this agent has something to
  report every morning". Splitting them is what lets the **Referral Window join
  the digest** — a daily sweep of every opening across the companies you have a
  connection at — while staying untailored, and while still being unable to
  search outside the list, since `search_referral_jobs` remains its only search
  tool.

  `DIGEST_MAX_ROLES` is per agent now that each runs its own process, and the
  Referral Window's is set higher: a capped list would break the one claim that
  agent makes.

- **The digest asks for a fixed layout.** `DIGEST_REQUEST` used to ask only for
  "one short line on why it fits", so the shape of the report drifted between
  days and between backends. It now pins the per-role format, forbids inventing
  a posting date where the source gives none, and leaves the title and date to
  `run_digest` — which writes them — so a report cannot arrive with two headers.

### Removed

- **LangSmith.** `langsmith` is no longer a declared dependency, `scout doctor`
  no longer has a `langsmith` line, and the `LANGSMITH_` variables are gone from
  `.env.example` and the docs. Langfuse — which Scout wires up itself in
  `core/tracing.py`, and which carries the session, the tags and the reply as
  the trace output — is the one tracing backend now. `langsmith` still arrives
  transitively with `langchain-core`, so clear `LANGSMITH_TRACING` from any
  `.env` that has it; leaving it set traces from the library, not from Scout.

- A dead `SYSTEM_PROMPT` variable in `.env`, left over from before `AgentSpec`
  carried each agent's prompt. No code path had read it for some time.

### Added

- Fourteen more companies for the BigTech Agent, taking it to twenty-two: Microsoft,
  Apple, NVIDIA, Oracle, Salesforce, Adobe, Uber, Cisco, Bloomberg, ServiceNow,
  Anthropic, OpenAI, Snowflake and DoorDash. Every board was checked live for a
  US filter and a posting date before it was added.

  Most of them cost no new code. Anthropic and DoorDash are lines in
  `greenhouse.BOARDS`; OpenAI and Snowflake are lines in `ashby.BOARDS`. Two new
  shapes cover five more: `smartrecruiters.py` is a third `HostedBoard`, and
  `workday.py` is a `WorkdayTenant` filled in for NVIDIA, Salesforce and Adobe.
  The remaining six — Microsoft, Apple, Oracle, Uber, Cisco, Bloomberg — are one
  module each, because their platforms share nothing.

  Not every source can claim recency, and none of them pretend to. The Workday
  sites publish only how long ago a role went up ("Posted 5 Days Ago"), which
  goes into `posted_label` the way `northeastern.py` already does it; Bloomberg
  publishes no date at all and says so, like `google.py`. The alternative — one
  extra request per job to read an absolute date — was rejected as too slow for
  what it buys, particularly since Adobe re-stamps its whole board daily and
  would gain nothing from it.

  Two companies from the same list were deliberately left out. **Palantir** would
  need `fetch` taught to read a bare top-level JSON array — Lever's shape, which
  currently reads as "nothing open" rather than "unreachable" — for two matching
  US roles. **Meta** is technically reachable but its `robots.txt` prohibits
  automated collection without written permission. Both are named as gaps by
  `search_referral_jobs` rather than silently missing.

- `fetch.get_json` and `fetch.post_json`, for sources whose rows sit below the
  top level (Microsoft under `data`, Cisco under `refineSearch.data`, Oracle under
  `items[0]`). They hand back the decoded body and leave the navigating — and the
  judgement about what a missing key means — to the source.

- Langfuse tracing, for the inside of a turn: every graph node, model call and
  tool call, with prompts, tool output, token counts and latencies, grouped by
  conversation — the checkpointer thread becomes the Langfuse session, the agent
  becomes a tag, and `scout.__version__` becomes the release. It lives in
  `scout/core/tracing.py`, the only module that knows Langfuse exists, and is
  attached per turn in `GraphRunner.respond` rather than being a second code
  path. Both keys together are the switch; `LANGFUSE_HIDE_CONTENT` masks the
  prompts, `scout doctor` reports which state it is in, and a handler that
  cannot be built leaves turns untraced rather than failing them. The host and
  the environment are read under both their names (`LANGFUSE_BASE_URL` as well
  as `LANGFUSE_HOST`), in the order the SDK itself reads them, so the reported
  value is the one used.

  Shaped to Langfuse's own tracing guidance, and checked against a real trace:
  one trace per turn, named `answer-slack-dm` or `run-job-digest`; a root span
  Scout owns, so the trace's input and output are the question and the reply
  rather than a graph state dump; a `generation` per model call interleaved with
  its `tool` calls; `slack`/`digest` tags and a `scout_backend` metadata field;
  the routing edges dropped on export; and `shutdown()` — not `flush()` — when a
  short-lived command exits.
- `scout` command line with one entry point per job: `scout run`, `scout
  digest`, `scout stats`, `scout agents`, and `scout doctor`. `python run.py`
  and `python -m scout.digest` keep working.
- `scout doctor` — reports what is configured in a checkout and what is
  missing, reading only local files, so it is safe to run on a broken box.
- Continuous integration: ruff, mypy, the test suite on Python 3.10–3.13, and a
  job that installs the built wheel and runs the console script.
- Type-checking with `mypy` under `disallow_untyped_defs`, and a `py.typed`
  marker so the package's types are visible to anything that imports it.
- MIT licence, contributing guide, security policy, code of conduct, issue and
  pull-request templates, Dependabot, `Makefile`, and `.editorconfig`.
- Documentation split into task-shaped pages under `docs/`, starting with a
  getting-started walkthrough.
- Tests for the clock, location, and résumé tools, and for the new command line
  and configuration report.
- Full coverage: every statement and every branch in `scout/` is exercised, and
  `fail_under = 100` keeps it that way.

### Changed

- `scout doctor`'s `tracing` line is now two, `langfuse` and `langsmith`: with
  two backends configurable, one line called "tracing" could not say which was.
- `pyproject.toml` is the single source of truth for dependencies;
  `requirements.txt` and `requirements-dev.txt` are generated from it by
  `scripts/sync_requirements.py`, and a test fails if they drift.
- Message splitting moved from `scout/slack/bot.py` to
  `scout/slack/formatting.py`, so the digest no longer imports a private
  helper from the bot.
- Job sources read their JSON rows through one shared `json_rows` helper, which
  checks the shape a board actually returned.
- The job package's shared parts are split by the job they do —
  `fetch` (HTTP, and what "unreachable" means), `feeds` (RSS), `posting` (the
  record, merging queries, rendering) and `relevance` (which titles count). Each
  source imports from the module that owns the name instead of one grab-bag
  `__init__`, and every request now goes through `fetch`, which is also the one
  seam the suite stubs.
- Greenhouse and Ashby share `HostedBoard` rather than two copies of the same
  code. A board platform now declares its URL, its rows key, its companies and
  how to read one row; the slug lookup, the fetch and the three replies a board
  tool owes are written once.
- `scout/core/agent.py` split along the seam it already had: declaring an agent
  and building its graph stay there, driving a compiled graph moved to
  `scout/core/runner.py` (`GraphRunner`, `Agent`).
- The package logger is `logging_config.logger()` rather than
  `logging.getLogger("scout")` repeated in nine modules, and the required-setting
  lists behind `scout doctor` and the start-up failure are declared once, in
  `settings.BOT_REQUIRES` / `DIGEST_REQUIRES`.

### Removed

- Dead code: the unused loggers in `scout/alert.py` and `scout/core/referrals.py`,
  `ToolRegistry.names()` and three re-exports in `scout/slack/__init__.py` that
  nothing imported, an unreachable branch in `stats.parse`, and `stats.main()` —
  a second copy of the argument parsing that `scout stats` already does.

### Fixed

- The `scout` console script pointed at `run:main`, a module that is not part
  of the package: installing the wheel produced a command that could not start.
- `langgraph-checkpoint-sqlite` and `langsmith` were listed in
  `requirements.txt` but not in `pyproject.toml`, so `pip install .` produced
  an install that could not even import `scout.core.checkpoints`.
- The test suite sent LangSmith traces from every scripted turn when the
  developer's `.env` had tracing on. It now pins `LANGSMITH_TRACING=false`, so a
  run touches no network whether or not a `.env` is present.

## Before this changelog

The platform — the LangGraph agent runtime, the Slack adapter, the two
job-search agents, the résumé hand-off, the Referral Window, the daily digest,
turn metrics, and the EC2 deployment — was built before this file existed. See
the commit history for that.
