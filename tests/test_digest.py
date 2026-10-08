"""The daily digest: which agents have one, whose window it lands in, and what
a failure does."""

from __future__ import annotations

import pytest

from scout import digest
from scout.agents import AGENTS
from scout.core import settings, shared_jobs, tracing
from scout.core.agent import AgentSpec, ConversationalAgent
from scout.slack import notify
from scout.tools.jobs import unsent
from scout.tools.jobs.posting import JobPosting


class FakeAgent(ConversationalAgent):
    """Records the thread it was asked on; can be told to fail."""

    def __init__(self, name: str, reply: str, error: Exception | None = None) -> None:
        self.name = name
        self.reply = reply
        self.error = error
        self.prompts: list[tuple[str, str]] = []
        self.events: list[tuple[str, str]] = []

    def respond(self, user_id: str, prompt: str) -> str:
        self.prompts.append((user_id, prompt))
        self.events.append(("respond", user_id))
        if self.error:
            raise self.error
        return self.reply

    def reset(self, user_id: str) -> None:
        self.events.append(("reset", user_id))

    def tool_names(self) -> list[str]: return []


SEEN = JobPosting("ML Engineer", "Amazon", "https://jobs/1", job_id="amazon:1")
FRESH = JobPosting("AI Engineer", "Amazon", "https://jobs/2", job_id="amazon:2")


class SearchingAgent(FakeAgent):
    """Shows two postings the way a job tool does, then reports only one."""

    def respond(self, user_id: str, prompt: str) -> str:
        super().respond(user_id, prompt)
        config = {"configurable": {"thread_id": user_id}}
        unsent.render_unsent("*{count} found:*", [SEEN, FRESH], config=config)
        return "1. *AI Engineer* — Amazon\n    New this week\n    Oct 07 · https://jobs/2"


@pytest.fixture
def searching_agent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "alpha")
    monkeypatch.setattr(digest, "AGENTS", {"alpha": _spec("alpha", digested=True)})
    monkeypatch.setattr(digest, "build_agent", lambda spec: SearchingAgent(spec.name, ""))


def _spec(key: str, *, digested: bool) -> AgentSpec:
    return AgentSpec(
        key=key,
        name=f"{key.title()} Agent",
        system_prompt="",
        in_digest=digested,
    )


@pytest.fixture
def three_agents(monkeypatch):
    """Two agents with a digest and one without, with the built agents recorded."""
    specs = {
        "alpha": _spec("alpha", digested=True),
        "parser": _spec("parser", digested=False),
        "beta": _spec("beta", digested=True),
    }
    monkeypatch.setattr(digest, "AGENTS", specs)
    built: dict[str, FakeAgent] = {}

    def build(spec: AgentSpec) -> FakeAgent:
        built[spec.key] = FakeAgent(spec.name, f"roles from {spec.key}")
        return built[spec.key]

    monkeypatch.setattr(digest, "build_agent", build)
    return built


@pytest.fixture
def deliverable(monkeypatch):
    """Enough config for ``main`` to get as far as sending, and the messages sent."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")
    monkeypatch.setattr(digest, "post_dm", lambda user, text: sent.append((user, text)))
    return sent


# --- Which agents have a digest -------------------------------------------


def test_the_real_registry_yields_the_shipped_digest_agents() -> None:
    """Guards the derivation itself: the parser must never be digested."""
    keys = {spec.key for spec in digest.digest_agents()}

    assert keys == {"bigtech", "edu", "referral"}
    assert "resume" not in keys
    assert all(AGENTS[key].in_digest for key in keys)


def test_the_referral_window_has_a_digest_without_being_resume_tailored() -> None:
    """The two flags answer different questions. The Referral Window searches a
    scope rather than a résumé, and still has something to report each morning."""
    assert AGENTS["referral"].in_digest is True
    assert AGENTS["referral"].tailor_with_resume is False


# --- One agent, one window -------------------------------------------------


def test_main_digests_only_the_active_agent(three_agents, deliverable, monkeypatch) -> None:
    """The whole point of the per-agent split: a process carries one bot's Slack
    token, so it must report for that bot and no other."""
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "beta")

    digest.main()

    assert list(three_agents) == ["beta"]
    (user, report) = deliverable[0]
    assert user == "U1"
    assert "roles from beta" in report
    assert "alpha" not in report


def test_main_sends_exactly_one_message(three_agents, deliverable, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "alpha")

    digest.main()

    assert len(deliverable) == 1


def test_main_refuses_an_agent_with_no_digest(three_agents, deliverable, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "parser")

    with pytest.raises(SystemExit, match="has no digest"):
        digest.main()

    assert deliverable == []


def test_main_refuses_an_unregistered_agent(three_agents, deliverable, monkeypatch) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "nope")

    with pytest.raises(SystemExit, match="has no digest"):
        digest.main()

    assert deliverable == []


def test_the_refusal_names_the_agents_that_do_have_one(
    three_agents, deliverable, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "parser")

    with pytest.raises(SystemExit, match="alpha, beta"):
        digest.main()


# --- The run itself --------------------------------------------------------


def test_the_agent_runs_on_its_own_digest_thread(three_agents) -> None:
    digest.run_digest(_spec("alpha", digested=True))

    assert three_agents["alpha"].prompts[0][0] == "digest:alpha"


def test_the_digest_thread_cannot_collide_with_a_slack_user(three_agents) -> None:
    # Slack ids are bare alphanumerics, so the prefix keeps the spaces apart.
    digest.run_digest(_spec("alpha", digested=True))

    assert three_agents["alpha"].prompts[0][0].startswith("digest:")


def test_the_role_cap_reaches_the_prompt(three_agents, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_MAX_ROLES", 3)

    digest.run_digest(_spec("alpha", digested=True))

    assert "at most 3" in three_agents["alpha"].prompts[0][1]


def test_the_prompt_asks_for_what_could_not_be_checked(three_agents) -> None:
    """The Referral Window's answer is a completeness claim, so a digest that
    drops the gaps turns a short list into a silent lie."""
    digest.run_digest(_spec("alpha", digested=True))

    assert "could not check" in three_agents["alpha"].prompts[0][1]


def test_the_prompt_pins_the_layout(three_agents) -> None:
    """The report is prose by the time it exists, so the only place the shape
    can be fixed is the request. Without this it drifts between days."""
    digest.run_digest(_spec("alpha", digested=True))
    asked = three_agents["alpha"].prompts[0][1]

    assert "*<role title>* — <company>" in asked
    assert "<one line on why it is worth a look>" in asked
    assert "<the posted date, exactly as the tool gave it> · <link>" in asked


def test_the_prompt_forbids_inventing_a_date(three_agents) -> None:
    """Two sources cannot give one — Workday reports only "5 Days Ago", and
    Bloomberg publishes nothing. A fixed layout must not push the model into
    filling the field anyway."""
    digest.run_digest(_spec("alpha", digested=True))
    asked = three_agents["alpha"].prompts[0][1]

    assert "word for word" in asked
    assert "no date given" in asked


def test_the_prompt_leaves_the_header_to_the_code(three_agents) -> None:
    """``run_digest`` writes the agent name and the date, so asking the model
    for them again is how a report grows two titles."""
    digest.run_digest(_spec("alpha", digested=True))

    assert "already carries both" in three_agents["alpha"].prompts[0][1]


def test_the_report_is_headed_by_the_agent_that_wrote_it(three_agents) -> None:
    report = digest.run_digest(_spec("alpha", digested=True))

    assert report.startswith("*Alpha Agent — ")
    assert "roles from alpha" in report


def test_the_thread_starts_clean_every_morning(three_agents) -> None:
    """What was sent lives in the shared store, so yesterday's turn is only
    weight — and a stale résumé profile."""
    digest.run_digest(_spec("alpha", digested=True))

    assert three_agents["alpha"].events == [("reset", "digest:alpha"), ("respond", "digest:alpha")]


def test_the_prompt_leaves_repeats_to_the_code(three_agents) -> None:
    """The model no longer has to remember yesterday; the tools already hid it."""
    digest.run_digest(_spec("alpha", digested=True))
    asked = three_agents["alpha"].prompts[0][1]

    assert "left out of what your tools return" in asked
    assert "earlier in this conversation" not in asked


def test_main_records_what_the_dm_linked_to(searching_agent, deliverable) -> None:
    """Only the model's picks are recorded: the role it left out is still new."""
    digest.main()

    assert shared_jobs.recently_shared("alpha", 30) == {"amazon:2"}


def test_a_dm_that_fails_to_post_records_nothing(searching_agent, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")

    def refuse(user: str, text: str) -> None:
        raise RuntimeError("channel_not_found")

    monkeypatch.setattr(digest, "post_dm", refuse)

    with pytest.raises(RuntimeError):
        digest.main()

    assert shared_jobs.recently_shared("alpha", 30) == set()


def test_a_store_that_cannot_be_written_still_sends_the_digest(
    searching_agent, deliverable, monkeypatch
) -> None:
    """Tomorrow may repeat a role; today's DM has already gone."""
    def broken(agent: str, postings: object) -> int:
        raise shared_jobs.SharedJobsError("state/shared.sqlite: readonly database")

    monkeypatch.setattr(shared_jobs, "record", broken)

    digest.main()

    assert "AI Engineer" in deliverable[0][1]


def test_a_failed_run_still_delivers_a_message(monkeypatch) -> None:
    """A morning with nothing in the window is indistinguishable from a bot that
    died, so the failure is reported rather than raised."""
    monkeypatch.setattr(
        digest,
        "build_agent",
        lambda spec: FakeAgent(spec.name, "", error=RuntimeError("job board down")),
    )

    report = digest.run_digest(_spec("alpha", digested=True))

    assert "job board down" in report
    assert "*Alpha Agent — " in report


# --- Configuration and shutdown -------------------------------------------


def test_digest_config_is_checked(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")
    with pytest.raises(SystemExit, match="DIGEST_SLACK_USER"):
        settings.require_digest_config()


def test_digest_does_not_need_the_socket_mode_token(monkeypatch) -> None:
    # The digest posts over the Web API, so an app token is not required.
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "SLACK_APP_TOKEN", "")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")
    settings.require_digest_config()


def test_main_refuses_without_somewhere_to_send(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")

    with pytest.raises(SystemExit, match="DIGEST_SLACK_USER"):
        digest.main()


def test_main_shuts_tracing_down_before_exiting(
    three_agents, deliverable, monkeypatch
) -> None:
    """Export is batched on a background thread, and this process is about to
    exit — a digest that does not drain it is one nobody can look at after."""
    flushed = []
    monkeypatch.setattr(settings, "ACTIVE_AGENT", "alpha")
    monkeypatch.setattr(tracing, "shutdown", lambda: flushed.append(1))

    digest.main()

    assert flushed == [1]


def test_long_digest_is_split_across_messages(monkeypatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, token: str) -> None: ...
        # Named for the Slack SDK method it stands in for.
        def chat_postMessage(self, channel: str, text: str) -> None:  # noqa: N802
            sent.append((channel, text))

    monkeypatch.setattr(notify, "WebClient", FakeClient)
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")

    notify.post_dm("U1", "\n".join(f"line {i}" for i in range(1200)))

    assert len(sent) > 1
    assert all(channel == "U1" for channel, _ in sent)
