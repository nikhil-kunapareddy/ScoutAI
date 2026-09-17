"""The daily digest: which agents run, on which threads, and what a failure does."""

from __future__ import annotations

import pytest

from scout import digest
from scout.agents import AGENTS
from scout.core import settings, tracing
from scout.core.agent import AgentSpec, ConversationalAgent
from scout.slack import notify


class FakeAgent(ConversationalAgent):
    """Records the thread it was asked on; can be told to fail."""

    def __init__(self, name: str, reply: str, error: Exception | None = None) -> None:
        self.name = name
        self.reply = reply
        self.error = error
        self.prompts: list[tuple[str, str]] = []

    def respond(self, user_id: str, prompt: str) -> str:
        self.prompts.append((user_id, prompt))
        if self.error:
            raise self.error
        return self.reply

    def reset(self, user_id: str) -> None: ...
    def set_backend(self, user_id: str, name: str) -> bool: return True
    def backend_name(self, user_id: str) -> str: return "anthropic"
    def backend_label(self, user_id: str) -> str: return "Claude"
    def last_backend(self, user_id: str) -> str: return "anthropic"
    def tool_names(self) -> list[str]: return []


def _spec(key: str, *, tailored: bool) -> AgentSpec:
    return AgentSpec(
        key=key,
        name=f"{key.title()} Agent",
        system_prompt="",
        tailor_with_resume=tailored,
    )


@pytest.fixture
def two_agents(monkeypatch):
    """Two job agents and one that isn't, with the built agents recorded."""
    specs = {
        "alpha": _spec("alpha", tailored=True),
        "parser": _spec("parser", tailored=False),
        "beta": _spec("beta", tailored=True),
    }
    monkeypatch.setattr(digest, "AGENTS", specs)
    built: dict[str, FakeAgent] = {}

    def build(spec: AgentSpec) -> FakeAgent:
        built[spec.key] = FakeAgent(spec.name, f"roles from {spec.key}")
        return built[spec.key]

    monkeypatch.setattr(digest, "build_agent", build)
    return built


def test_only_job_agents_run(two_agents) -> None:
    digest.run_digest()
    assert sorted(two_agents) == ["alpha", "beta"]


def test_the_real_registry_yields_the_shipped_job_agents() -> None:
    # Guards the derivation itself: the parser must never be digested.
    keys = [spec.key for spec in digest.job_agents()]
    assert "resume_parser" not in keys
    assert set(keys) <= set(AGENTS)
    assert all(AGENTS[key].tailor_with_resume for key in keys)


def test_each_agent_runs_on_its_own_digest_thread(two_agents) -> None:
    digest.run_digest()
    assert two_agents["alpha"].prompts[0][0] == "digest:alpha"
    assert two_agents["beta"].prompts[0][0] == "digest:beta"


def test_digest_threads_cannot_collide_with_a_slack_user(two_agents) -> None:
    # Slack ids are bare alphanumerics, so the prefix keeps the spaces apart.
    digest.run_digest()
    assert all(
        agent.prompts[0][0].startswith("digest:") for agent in two_agents.values()
    )


def test_the_role_cap_reaches_the_prompt(two_agents, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_MAX_ROLES", 3)
    digest.run_digest()
    assert "at most 3" in two_agents["alpha"].prompts[0][1]


def test_report_has_a_section_per_agent(two_agents) -> None:
    report = digest.run_digest()
    assert "*Alpha Agent*" in report
    assert "roles from alpha" in report
    assert "*Beta Agent*" in report
    assert "roles from beta" in report


def test_one_agent_failing_still_delivers_the_others(monkeypatch) -> None:
    monkeypatch.setattr(
        digest, "AGENTS", {k: _spec(k, tailored=True) for k in ("alpha", "beta")}
    )

    def build(spec: AgentSpec) -> FakeAgent:
        if spec.key == "alpha":
            return FakeAgent(spec.name, "", error=RuntimeError("job board down"))
        return FakeAgent(spec.name, "roles from beta")

    monkeypatch.setattr(digest, "build_agent", build)

    report = digest.run_digest()
    assert "job board down" in report
    assert "roles from beta" in report


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


def test_main_checks_the_config_then_dms_the_report(two_agents, monkeypatch) -> None:
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")
    monkeypatch.setattr(digest, "post_dm", lambda user, text: sent.append((user, text)))

    digest.main()

    (user, report) = sent[0]
    assert user == "U1"
    assert "roles from alpha" in report


def test_main_shuts_tracing_down_before_exiting(two_agents, monkeypatch) -> None:
    """Export is batched on a background thread, and this process is about to
    exit — a digest that does not drain it is one nobody can look at after."""
    flushed = []
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U1")
    monkeypatch.setattr(digest, "post_dm", lambda user, text: None)
    monkeypatch.setattr(tracing, "shutdown", lambda: flushed.append(1))

    digest.main()

    assert flushed == [1]


def test_main_refuses_without_somewhere_to_send(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")

    with pytest.raises(SystemExit, match="DIGEST_SLACK_USER"):
        digest.main()
