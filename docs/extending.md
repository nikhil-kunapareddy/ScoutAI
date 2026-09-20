# Extending Scout

Four things you might want to add, in order of how often it comes up. Each is
one file plus one line, and none of them touches the graph.

- [A job source](#a-job-source)
- [A tool](#a-tool)
- [An agent](#an-agent)
- [A model provider](#a-model-provider)

## A job source

One module per source under `scout/tools/jobs/`. The platforms differ too much
to share an implementation — a JSON search API is nothing like an RSS feed — so
the rule is: **fetch and parse your own way, render like everyone else.**

Reuse the shared parts, importing each from the module that owns it:

| From | Helper | What it is for |
|---|---|---|
| `fetch` | `get_rows(url, key, params)` | GET a JSON board: the rows, or `None` if it can't be reached |
| `fetch` | `get_text(url, params)` | The same for a page or an RSS feed |
| `fetch` | `post_rows(url, key, payload)` | The same where the query goes in the body (Workday) |
| `feeds` | `items(feed)`, `tag_text(item, tag)` | Reading an RSS feed |
| `posting` | `JobPosting` | The normalised posting every source produces |
| `posting` | `render_postings(header, postings)` | The one Slack output format |
| `posting` | `merge_queries(queries, postings_for)` | Run several queries at one source and de-dupe |
| `posting` | `take_newest(postings, limit)` | Sort newest-first and cut |
| `posting` | `clamp_int(value, default, min, max)` | Bounds a schema can't express (`days=0`, `limit=999`) |
| `posting` | `DEFAULT_LIMIT`, `MAX_LIMIT` | Shared result-count bounds |
| `relevance` | `is_ai_ml_role(title)` | The AI/ML title filter, by phrase or standalone token |
| `relevance` | `matches_keywords(title, terms)` | Title filtering where the source has no server-side search |
| `relevance` | `search_queries(keywords)` | The caller's phrase, or the profile's default queries |

`fetch` is also the one place the suite stubs, so a source that uses it is
testable without touching `requests` at all.

```python
"""Acme job search, via the public JSON endpoint acme.com/careers uses."""

from __future__ import annotations

from ..registry import ToolRegistry
from . import fetch
from .posting import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    JobPosting,
    clamp_int,
    render_postings,
    take_newest,
)
from .relevance import is_ai_ml_role

SEARCH_URL = "https://acme.com/api/jobs"
ORGANIZATION = "Acme"


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def search_acme_jobs(keywords: str = "", limit: int = DEFAULT_LIMIT) -> str:
        """Search Acme's careers site for recent US AI/ML openings.

        Args:
            keywords: Optional phrase to narrow titles (e.g. "machine learning").
            limit: Maximum number of roles to return.
        """
        postings = search(keywords, limit)
        if postings is None:
            return f"Couldn't reach {ORGANIZATION}'s careers board right now. Try again later."
        if not postings:
            return f"No relevant {ORGANIZATION} roles found right now. Try adjusting your keywords."
        return render_postings(
            f"*Latest {ORGANIZATION} AI/ML roles — {{count}} found:*", postings
        )


def search(keywords: str = "", limit: int = DEFAULT_LIMIT) -> list[JobPosting] | None:
    """Acme's current AI/ML openings, or None if the board can't be reached.

    The postings rather than the rendered text, so a caller searching several
    companies at once can merge and count them — see ``jobs/directory.py``.
    """
    limit = clamp_int(limit, DEFAULT_LIMIT, 1, MAX_LIMIT)

    rows = fetch.get_rows(SEARCH_URL, "jobs", {"q": keywords})
    if rows is None:
        return None

    postings = [
        posting for row in rows if (posting := _to_posting(row)) is not None
    ]
    return take_newest(postings, limit)


def _to_posting(row: dict) -> JobPosting | None:
    """Convert one board row, or None if it should be skipped."""
    title = (row.get("title") or "").strip()
    if not is_ai_ml_role(title):
        return None
    return JobPosting(title=title, organization=ORGANIZATION, url=row.get("url", ""))
```

Note the two halves. The registered tool returns Slack text; `search` returns the
postings and matches `Searcher`, which is what lets the Referral Window search
this source alongside seven others. `None` from `search` means *unreachable* and
is never the same answer as `[]`.

Then add the module to an agent's `tool_modules` (`scout/agents/bigtech.py`),
and a test in `tests/test_job_sources.py` using `call_tool` and `FakeRequests`:
one for the happy path, one for filtering, and one for the source being down.
Add it to `SOURCES` at the bottom of that file too — that table is what checks
every source tells "unreachable" from "nothing open".

Two rules worth repeating, because both are about the model's experience:

- **Never raise for an expected failure.** A dead board, no results, a
  malformed page: return a sentence. An exception costs the user the turn.
- **`None` and `[]` are different answers.** "I couldn't reach it" and "there is
  nothing there" lead the model to different next moves.

If the company is hosted on **Greenhouse, Ashby or SmartRecruiters**, you do not
need a module at all — add a line to `BOARDS` in the platform's module. That is
one platform, not one source, and the line reaches `directory.py` on its own.
**Workday** is the same deal with a different shape: add a `WorkdayTenant` to
`TENANTS` in `workday.py`, giving it the host, tenant, site and the id its
country facet uses for the United States. (That facet's *name* is per-tenant —
NVIDIA calls it `locationHierarchy1`, Adobe `locationCountry`, Salesforce a
seventy-character custom field — so read it off the live response rather than
copying another tenant's.)

A *new* multi-company platform is a `HostedBoard`: give it the board URL, the
JSON key its openings sit under, its `BOARDS`, and a function that reads one row
— the slug lookup, the fetch, and the three replies a board tool owes (unknown
company, board down, board empty) are already written.

If the rows are **nested** below the top level, reach for `fetch.get_json` or
`fetch.post_json` and navigate the body yourself, rather than teaching `fetch`
another shape. Do decide deliberately what a *missing* key means: for Oracle it
means the request lost its `expand` and the board returned no postings at all,
which is "unreachable", not "nothing open".

## A tool

Anything the model can call. A module with a `register(reg)` function:

```python
def register(reg: ToolRegistry) -> None:
    @reg.tool
    def days_until(date: str = "") -> str:
        """Days between today and an ISO date.

        Args:
            date: The target date, as YYYY-MM-DD.
        """
```

**The signature and the docstring *are* the schema.** LangChain derives the
argument types from your annotations and the per-parameter descriptions from the
Google-style `Args:` block, and that is exactly what the model reads before
deciding what to pass. So: annotate every parameter, document every parameter,
and write the summary as instructions to a reader who cannot see your code.
`ruff` enforces the documentation half (`D417`).

Argument *types* are LangChain's problem now — it validates against the schema
before your function runs, so `limit="ten"` comes back to the model as a
readable error and costs one hop. `clamp_int` still guards the bounds a schema
cannot express.

**A tool that needs to know who is asking takes `*, config: RunnableConfig`.**
LangChain injects it and hides it from the schema, so the model never sees a
user id and cannot get one wrong:

```python
    @reg.tool
    def list_referrals(*, config: RunnableConfig) -> str:
        """List the companies where the user has a referral connection."""
        owner = referrals.owner_for(config)
```

Keep that annotation exactly as written: widening it to `RunnableConfig | None`
stops the injection and leaks the parameter into the schema.

Finally, add the module to the `tool_modules` of the agents that should have it,
and restart. There is no registration step beyond that — `scout agents` will
show it.

## An agent

One file in `scout/agents/`:

```python
"""Startup Agent: smaller companies, where a referral matters most."""

from __future__ import annotations

from ..core.agent import AgentSpec
from ..tools import clock, referrals_read
from ..tools.jobs import greenhouse

SYSTEM_PROMPT = (
    "You are Startup Agent. You search Greenhouse-hosted startup boards for "
    "roles that fit the candidate profile you are given, newest first. "
    "Put roles at companies on the user's referral list first, and say so."
)

SPEC = AgentSpec(
    key="startup",
    name="Startup Agent",
    system_prompt=SYSTEM_PROMPT,
    tool_modules=[clock, referrals_read, greenhouse],
    tailor_with_resume=True,
    in_digest=True,
)  # default_backend omitted: inherits settings.DEFAULT_BACKEND
```

Then register it in `scout/agents/__init__.py`:

```python
from . import bigtech, edu, referral, resume_parser, startup

AGENTS: dict[str, AgentSpec] = {
    ...
    startup.SPEC.key: startup.SPEC,
}
```

That is the whole change. Three things follow from it automatically:

- `AGENT=startup scout run` (or `scout run --agent startup`) runs it, and
  `scout agents` lists it.
- `tailor_with_resume=True` puts the Resume Parser stage in front of it.
- `in_digest=True` **gives it a daily digest of its own** — the digest derives
  its agents from that flag rather than from a list. On the box it also needs a
  timer instance (`scout-digest-<key>.timer`, copied from an existing one), so
  the report is DM'd by that agent's own Slack app rather than another's.
- Give it `referrals_read`, not `referrals`. Only the Referral Window writes to
  that list — and it can afford to, because the only search tool it holds is
  `search_referral_jobs`, which cannot look outside the list.
- If the source is a company board, add it to `jobs/directory.py` too, so a
  referral there becomes searchable. Companies on a hosted platform — Greenhouse,
  Ashby, SmartRecruiters, Workday — land there on their own. Add an alias when
  the board's slug is not the name anyone types: `doordashusa` is the board,
  "DoorDash" is the company.

Each agent is a separate Slack app, so it needs its own token pair in
`.env.startup` and its own `CHECKPOINT_DB`. See
[getting-started](getting-started.md#2-create-the-slack-app).

## A model provider

One builder in `scout/core/models.py` plus one entry in `_BUILDERS`:

```python
def _gemini() -> BaseChatModel:
    _require_key(settings.GEMINI_API_KEY, "GEMINI_API_KEY", "Add it to .env …")
    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        api_key=settings.GEMINI_API_KEY,
        timeout=settings.MODEL_REQUEST_TIMEOUT_SECONDS,
    )


_BUILDERS = {..., GEMINI: _gemini}
_LABELS = {..., GEMINI: lambda: f"Gemini ({settings.GEMINI_MODEL})"}
```

Add the settings to `settings.py` and `.env.example`, and a command to the table
in `scout/slack/bot.py` if users should be able to switch to it by name.

Models are built on first use and cached, which is what keeps one missing key
from stopping the process from starting. If the provider needs per-request
options that `bind_tools` would discard, add an entry to `_REQUEST_OPTIONS` —
those are applied *after* the tools, deliberately.

## Before you open the pull request

```bash
make check
```

ruff, mypy, and the suite with coverage — the same four commands CI runs. And
see [CONTRIBUTING.md](CONTRIBUTING.md) for the rest.
