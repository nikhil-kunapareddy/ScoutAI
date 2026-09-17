"""Langfuse tracing: off until configured, attached per turn, never fatal.

Nothing here sends a trace. The handler is scripted the way the chat models are
— installed into ``scout.core.tracing`` so the graph reaches it exactly as it
would reach Langfuse's — and the one test that does build the real thing builds
it with fake keys and creates no span, so there is nothing to export.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

from scout.core import settings, tracing
from scout.core.runner import Agent

from .conftest import calls_tool


@pytest.fixture(autouse=True)
def forget_the_handler():
    """Each test builds its own, so a change of keys is picked up."""
    tracing.reset_cache()
    yield
    tracing.reset_cache()


@pytest.fixture
def keys(monkeypatch):
    """Both Langfuse keys — which is what turns tracing on — and a fake host."""
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(settings, "LANGFUSE_HOST", "https://langfuse.test")
    monkeypatch.setattr(settings, "LANGFUSE_ENVIRONMENT", "")
    monkeypatch.setattr(settings, "LANGFUSE_HIDE_CONTENT", False)


class RecordingHandler(BaseCallbackHandler):
    """Stands in for Langfuse's handler, recording what LangChain hands it.

    Records the metadata of each model and tool call separately, because the
    claim worth testing is that *both* land in the right session: a tool result
    the model never sees traced is the gap the ``turn`` line already has.
    """

    def __init__(self) -> None:
        self.model_calls: list[dict] = []
        self.tool_calls: list[dict] = []

    def on_chat_model_start(self, serialized, messages, **kwargs: Any) -> None:
        self.model_calls.append(kwargs.get("metadata") or {})

    def on_tool_start(self, serialized, input_str, **kwargs: Any) -> None:
        self.tool_calls.append(kwargs.get("metadata") or {})


class RecordingSpan:
    """Stands in for Langfuse's root span, recording what the turn reports."""

    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)


@pytest.fixture
def root_span(monkeypatch) -> RecordingSpan:
    """Installs a fake root span, and records the input it was opened with."""
    import langfuse

    span = RecordingSpan()
    span.opened_with = []

    @contextmanager
    def start(**kwargs):
        span.opened_with.append(kwargs)
        yield span

    monkeypatch.setattr(
        langfuse, "get_client", lambda: SimpleNamespace(start_as_current_observation=start)
    )
    return span


@pytest.fixture
def attributes(monkeypatch) -> list[dict]:
    """Records the trace-level attributes ``traced`` propagates.

    Patched on ``langfuse`` itself, because that is where the module imports
    ``propagate_attributes`` from at call time — the same seam the job sources
    are stubbed at.
    """
    import langfuse

    seen: list[dict] = []

    @contextmanager
    def record(**kwargs):
        seen.append(kwargs)
        yield

    monkeypatch.setattr(langfuse, "propagate_attributes", record)
    return seen


@pytest.fixture
def scripted_handler(keys, monkeypatch, root_span, attributes) -> RecordingHandler:
    """Tracing on, with every Langfuse touchpoint scripted.

    It takes ``root_span`` and ``attributes`` so that no test can reach the real
    SDK by forgetting one: a real client here builds a real span and queues it
    for export, which is exactly what the suite must never do.
    """
    recorder = RecordingHandler()
    monkeypatch.setattr(tracing, "_build_handler", lambda: recorder)
    return recorder


# --- The switch -----------------------------------------------------------


@pytest.mark.parametrize(("public", "secret", "on"), [
    ("", "", False),
    ("pk-lf-x", "", False),   # half a pair traces nothing
    ("", "sk-lf-x", False),
    ("pk-lf-x", "sk-lf-x", True),
])
def test_tracing_needs_both_keys(monkeypatch, public: str, secret: str, on: bool) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", public)
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", secret)

    assert tracing.enabled() is on


def test_an_untraced_turn_runs_on_the_config_it_came_with() -> None:
    """With no keys there is no callback in the config — not an inert one."""
    config = {"configurable": {"thread_id": "U1"}}

    assert tracing.handler() is None
    with tracing.traced(config, agent="Test Agent", thread="U1", prompt="hi") as turn:
        assert turn.config is config
        assert turn.root is None
        assert turn.answered("a reply nobody records") is None


# --- What a traced turn carries -------------------------------------------


@pytest.mark.parametrize(("thread", "trace_name", "source"), [
    # A Slack user, and the digest — which is not a person and is not dressed
    # up as one. The two are worth telling apart in the UI, so they are named
    # and tagged apart.
    ("U123", tracing.SLACK_TRACE, tracing.SLACK_SOURCE),
    ("digest:bigtech", tracing.DIGEST_TRACE, tracing.DIGEST_SOURCE),
])
def test_the_turn_is_named_and_attributed(
    scripted_handler, attributes, root_span, thread: str, trace_name: str, source: str
) -> None:
    config = {"configurable": {"thread_id": thread, "backend": "anthropic"}}

    with tracing.traced(
        config, agent="BigTech Agent", thread=thread, prompt="any AI roles?"
    ) as turn:
        assert turn.config["callbacks"] == [scripted_handler]

    assert attributes == [{
        "trace_name": trace_name,
        "session_id": thread,
        "user_id": thread,
        "tags": ["BigTech Agent", source],
        "metadata": {"scout_backend": "anthropic"},
    }]


def test_a_trace_name_carries_nothing_per_run(attributes) -> None:
    """Langfuse targets dashboards, saved views and judges by name, so a name
    that carried the user or the model would fragment all three."""
    for name in (tracing.SLACK_TRACE, tracing.DIGEST_TRACE):
        assert name.islower()
        assert " " not in name
        # Verb-first, and nothing that changes between runs.
        assert name.split("-")[0] in {"answer", "run"}


def test_the_rest_of_the_config_is_carried_through(scripted_handler) -> None:
    config = {
        "configurable": {"thread_id": "U1", "backend": "anthropic"},
        "recursion_limit": 9,
        "metadata": {"already": "here"},
    }

    with tracing.traced(config, agent="A", thread="U1", prompt="hi") as turn:
        assert turn.config["configurable"] == config["configurable"]
        assert turn.config["recursion_limit"] == 9
        assert turn.config["metadata"] == {"already": "here"}


def test_the_config_it_was_given_is_left_alone(scripted_handler, attributes, root_span) -> None:
    """``GraphRunner`` keeps using it for its checkpointer reads, which are not
    turns and must not open a trace of their own."""
    config = {"configurable": {"thread_id": "U1"}}

    with tracing.traced(config, agent="A", thread="U1", prompt="hi"):
        pass

    assert "callbacks" not in config


def test_the_trace_shows_the_question_and_the_answer(
    scripted_handler, attributes, root_span
) -> None:
    """What the tracing table and an evaluator read. The graph's own output is
    the whole message list, which answers a different question."""
    config = {"configurable": {"thread_id": "U1"}}

    with tracing.traced(config, agent="A", thread="U1", prompt="any AI roles?") as turn:
        turn.answered("Three worth a look.")

    assert root_span.opened_with == [
        {"as_type": "span", "name": tracing.SLACK_TRACE, "input": "any AI roles?"}
    ]
    assert root_span.updates == [{"output": "Three worth a look."}]


def test_a_traced_turn_answers_and_traces_every_step(
    spec, chat_models, scripted_handler
) -> None:
    """The whole point, end to end: a real turn with the handler in place.

    Both model calls and the tool hop between them have to reach it. A trace
    that aggregated the loop into one step would hide what the agent decided
    after the tool returned, which is the thing worth looking at.
    """
    chat_models["primary"].replies = [calls_tool("echo", {"text": "hi"}), AIMessage("done")]

    assert Agent(spec).respond("U1", "use the tool") == "done"

    assert len(scripted_handler.model_calls) == 2
    assert len(scripted_handler.tool_calls) == 1
    # LangGraph's own per-node metadata rides along, which is what nests the
    # tool call under the step that asked for it.
    assert scripted_handler.tool_calls[0]["langgraph_node"] == "tools"


# --- Building it once, and surviving not being able to --------------------


def test_the_handler_is_built_once(keys, monkeypatch) -> None:
    """It owns an exporter thread and a queue; one per process is the point."""
    built = []

    def build():
        built.append(RecordingHandler())
        return built[-1]

    monkeypatch.setattr(tracing, "_build_handler", build)

    assert tracing.handler() is tracing.handler()
    assert len(built) == 1


def test_a_handler_that_cannot_be_built_leaves_turns_untraced(
    keys, monkeypatch, caplog
) -> None:
    """Regression guard for the rule: monitoring must never break a turn."""
    attempts = []

    def explode():
        attempts.append(1)
        raise RuntimeError("no langfuse here")

    monkeypatch.setattr(tracing, "_build_handler", explode)
    config = {"configurable": {}}

    assert tracing.handler() is None
    with tracing.traced(config, agent="A", thread="U1", prompt="hi") as turn:
        assert turn.config is config
    # Tried once, then left alone: a broken exporter must not be retried on
    # every turn for the life of the process.
    assert tracing.handler() is None
    assert attempts == [1]
    assert "could not start" in caplog.text


def test_a_turn_still_answers_when_tracing_will_not_start(
    spec, chat_models, keys, monkeypatch
) -> None:
    monkeypatch.setattr(tracing, "_build_handler", lambda: 1 / 0)
    chat_models["primary"].replies = [AIMessage("answered anyway")]

    assert Agent(spec).respond("U1", "hi") == "answered anyway"


# --- How the client is configured -----------------------------------------


def test_the_client_takes_its_credentials_and_host_from_settings(keys) -> None:
    options = tracing._client_options()

    assert options["public_key"] == "pk-lf-test"
    assert options["secret_key"] == "sk-lf-test"
    assert options["host"] == "https://langfuse.test"
    assert options["release"]  # the version that produced the trace
    # Left out rather than passed empty, so the SDK's own defaults apply.
    assert "environment" not in options
    assert "mask" not in options


def test_the_environment_is_passed_when_one_is_named(keys, monkeypatch) -> None:
    monkeypatch.setattr(settings, "LANGFUSE_ENVIRONMENT", "production")
    assert tracing._client_options()["environment"] == "production"


def test_hiding_content_installs_a_mask(keys, monkeypatch) -> None:
    """The counterpart of LANGSMITH_HIDE_INPUTS: the call tree without the
    résumé profile that rides in every system prompt."""
    monkeypatch.setattr(settings, "LANGFUSE_HIDE_CONTENT", True)

    mask = tracing._client_options()["mask"]

    assert mask(data="Nikhil's résumé, in full") == tracing.REDACTED


def test_routing_nodes_are_not_exported(keys) -> None:
    """A conditional edge holds no model call, no tool result, and nothing to
    act on — and Langfuse bills what it stores."""
    worth_exporting = tracing._client_options()["should_export_span"]

    assert worth_exporting(_span("tools_condition")) is False
    assert worth_exporting(_span("_needs_profile")) is False


def test_the_default_filter_is_composed_with_not_replaced(keys) -> None:
    """It is what keeps other libraries' HTTP and database spans out of a
    trace; dropping routing nodes must not cost us that."""
    worth_exporting = tracing._client_options()["should_export_span"]

    # A span from an unrelated library: not ours, so not exported.
    assert worth_exporting(_span("GET /api/jobs")) is False
    # One of ours, under a name that is not routing.
    assert worth_exporting(_span("model", langfuse=True)) is True


def _span(name: str, *, langfuse: bool = False):
    """A finished OpenTelemetry span, as the exporter would see it."""
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope

    scope = InstrumentationScope(name="langfuse-sdk" if langfuse else "urllib3")
    return ReadableSpan(name=name, instrumentation_scope=scope)


def test_the_handler_it_builds_is_langfuses_own(keys) -> None:
    """The one test that reaches the SDK.

    Constructing the client starts its exporter thread but sends nothing: no
    span is created here, and the keys are fake. Shut down again so the suite
    does not carry the thread around.
    """
    from langfuse import get_client

    handler = tracing._build_handler()
    try:
        assert isinstance(handler, BaseCallbackHandler)
        assert type(handler).__module__.startswith("langfuse")
    finally:
        get_client().shutdown()


# --- Flushing -------------------------------------------------------------


def test_shutting_down_an_untraced_process_does_nothing(monkeypatch) -> None:
    """``scout digest`` calls this whether or not tracing is on."""
    import langfuse

    monkeypatch.setattr(langfuse, "get_client", lambda: 1 / 0)

    assert tracing.shutdown() is None


def test_shutting_down_drains_the_queue(scripted_handler, monkeypatch) -> None:
    """What Langfuse asks a short-lived application to do: drain, then stop."""
    import langfuse

    stopped = []
    monkeypatch.setattr(
        langfuse, "get_client",
        lambda: SimpleNamespace(shutdown=lambda: stopped.append(1)),
    )
    tracing.handler()  # something was traced, so there is something queued

    tracing.shutdown()

    assert stopped == [1]


def test_a_shutdown_that_fails_is_logged_not_raised(
    scripted_handler, monkeypatch, caplog
) -> None:
    """The digest was delivered; failing to file the paperwork is not its problem."""
    import langfuse

    monkeypatch.setattr(langfuse, "get_client", lambda: SimpleNamespace(shutdown=_explode))
    tracing.handler()

    assert tracing.shutdown() is None
    assert "Could not shut Langfuse down" in caplog.text


def _explode() -> None:
    raise RuntimeError("langfuse is down")
