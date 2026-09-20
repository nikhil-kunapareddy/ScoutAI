"""Langfuse tracing: what a turn looked like from the inside.

``metrics.py`` records one line per turn — cheap, always on, no network. This is
the other half of the same question, for when that line is not enough: every
node, model call and tool call of a turn, with the prompts, the tool output, the
token counts and the latencies attached, grouped in Langfuse by conversation.

This module is the only place in the package that knows Langfuse exists.
``GraphRunner.respond`` hands it a run config and gets back either an
instrumented one or the one it already had, so a turn's code path is the same
either way. Four rules hold that in place:

- **Off unless both keys are set.** The key pair *is* the switch: this module
  reads it, so there is no separate on/off flag to disagree with it. Half a
  pair is a typo rather than a choice, and ``scout doctor`` says so.
- **Observability never breaks a turn.** The import, the client and the handler
  are built inside one ``try`` that logs and leaves tracing off for the life of
  the process. Export itself is batched on a background thread, so an
  unreachable Langfuse slows nothing and fails no turn.
- **Built once, lazily.** The client owns that thread and its queue, so it is
  built on the first traced turn — never at import, which would start a thread
  inside ``scout doctor``, the test suite, and every ``scout stats``.
- **A thread is a session.** Scout's checkpointer thread *is* the conversation,
  so it is the Langfuse session id: a user's turns line up in the UI in the same
  order the checkpointer replays them. Tool traffic belongs to that trace for
  the same reason it is checkpointed — it is part of the turn.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from .. import __version__
from . import settings
from .logging_config import logger
from .referrals import DIGEST_THREAD_PREFIX

if TYPE_CHECKING:  # the OpenTelemetry types, without importing them to get here
    from opentelemetry.sdk.trace import ReadableSpan

log = logger()

#: Both are needed to trace; see ``enabled``. Shared with ``scout doctor``, so
#: the report and the runtime cannot disagree about what turns this on.
REQUIRES = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

#: What a prompt or completion becomes under ``LANGFUSE_HIDE_CONTENT``.
REDACTED = "[redacted by LANGFUSE_HIDE_CONTENT]"

#: Trace names, by what asked for the turn. Verb-first and free of anything
#: per-run: Langfuse dashboards, saved views and judges target names, so a name
#: that carries a user id or a model would fragment every one of them. Which
#: agent ran, and on which backend, are a tag and a metadata field instead.
SLACK_TRACE = "answer-slack-dm"
DIGEST_TRACE = "run-job-digest"

#: Tag for where a turn came from — the dimension worth comparing cost and
#: latency across, since a digest turn sweeps every source on a raised hop limit.
SLACK_SOURCE = "slack"
DIGEST_SOURCE = "digest"

#: Graph nodes that are pure routing: a conditional edge choosing what runs
#: next. They hold no model call, no tool result, and nothing a reader of the
#: trace can act on, and Langfuse bills what it stores — so they are dropped on
#: the way out. Only ever leaves, so dropping them orphans nothing.
ROUTING_NODES = frozenset({"tools_condition", "_needs_profile"})

_handler: BaseCallbackHandler | None = None
_failed = False
_guard = threading.Lock()


def enabled() -> bool:
    """Whether turns are traced — which is to say, whether both keys are set."""
    return not settings.missing_for(REQUIRES)


def handler() -> BaseCallbackHandler | None:
    """The Langfuse callback handler, built on first use; None when tracing is off.

    One handler serves every user and every thread: what a run is *about*
    travels per turn in the config (see ``observed``), not in this object.

    A handler that cannot be built is not a reason to fail a turn, so the
    failure is logged once and tracing stays off until the process restarts.
    """
    global _handler, _failed
    if not enabled():
        return None
    with _guard:
        if _handler is None and not _failed:
            try:
                _handler = _build_handler()
            except Exception:
                _failed = True
                log.exception("Langfuse tracing could not start; turns run untraced")
        return _handler


@dataclass
class Turn:
    """One turn in progress: the config to run it with, and where the answer goes.

    Handed out by ``traced`` in both states, so ``GraphRunner.respond`` reads the
    same whether or not anything is being recorded — an untraced turn gets a
    ``Turn`` whose ``answered`` does nothing.
    """

    #: The config to invoke the graph with: instrumented, or the original.
    config: RunnableConfig
    #: The trace's root span, when there is one.
    root: Any | None = field(default=None, repr=False)

    def answered(self, reply: str) -> None:
        """Record what the user was actually told, as the trace's output.

        The trace-level output is read off this root, and it is what the
        tracing table shows and an evaluator reads. It has to be the reply —
        the graph's own output is the whole message list, which answers "what
        is in the thread", not "what did it say".
        """
        if self.root is not None:
            self.root.update(output=reply)


@contextmanager
def traced(
    config: RunnableConfig, *, agent: str, thread: str, prompt: str
) -> Iterator[Turn]:
    """Run one turn as one trace, yielding the ``Turn`` to run it through.

    With tracing off, yields a ``Turn`` carrying ``config`` itself: a turn with
    no keys configured runs through exactly the config it always did, with no
    callback in it and no context entered.

    A context manager because a trace is a scope, not a value. It owns the root
    span — which is what lets the trace show the user's question and the bot's
    reply rather than a graph state dump — and the trace-level attributes, which
    ``propagate_attributes`` sets on that span and every span under it. One
    trace per turn is the scope Langfuse asks for; the session groups the
    conversation back together. An exception on the way out is recorded on the
    root by the SDK, so a failed turn is visible as a failed trace.

    Args:
        config: The run config for one turn, from ``GraphRunner._config``.
        agent: Display name of the agent running the turn; becomes a trace tag.
        thread: The checkpointer thread — a Slack user id, or ``digest:<key>``.
            It is both the session and the user: digest threads are not people,
            and dressing one up as a person would be a worse lie than the
            ``digest:`` prefix showing in the UI.
        prompt: What the user asked, recorded as the trace's input.
    """
    tracer = handler()
    if tracer is None:
        yield Turn(config)
        return

    from langfuse import get_client, propagate_attributes

    digest = thread.startswith(DIGEST_THREAD_PREFIX)
    name = DIGEST_TRACE if digest else SLACK_TRACE
    with (
        get_client().start_as_current_observation(
            as_type="span", name=name, input=prompt
        ) as root,
        propagate_attributes(
            trace_name=name,
            session_id=thread,
            user_id=thread,
            tags=[agent, DIGEST_SOURCE if digest else SLACK_SOURCE],
            # Request context, for the question a generation cannot answer on
            # its own: which backend the turn was *asked* for. It differs from
            # the model that replied exactly when the fallback rescued the turn.
            metadata={"scout_backend": config["configurable"].get("backend") or ""},
        ),
    ):
        # A new dict rather than a mutated one: the caller keeps an untraced
        # config for its checkpointer reads, which are not part of the turn.
        yield Turn({**config, "callbacks": [tracer]}, root)


def shutdown() -> None:
    """Send whatever is queued, then stop. For a process that is about to exit.

    What Langfuse asks a short-lived application to do, rather than ``flush``:
    it drains the queue *and* stops the exporter, so the digest cannot exit with
    spans still in flight. Best-effort by the same rule as the rest of this
    module — failing to file the traces must not fail a digest that was
    otherwise delivered.
    """
    if _handler is None:
        return  # nothing was traced, so there is nothing queued
    try:
        from langfuse import get_client

        get_client().shutdown()
    except Exception:
        log.exception("Could not shut Langfuse down cleanly")


def reset_cache() -> None:
    """Drop the built handler, so changed settings are picked up. For tests."""
    global _handler, _failed
    with _guard:
        _handler = None
        _failed = False


def _build_handler() -> BaseCallbackHandler:
    """Configure the Langfuse client and return its LangChain handler.

    Constructing ``Langfuse`` is what installs the credentials, the host and the
    mask; ``CallbackHandler`` then finds that client itself. Imported here and
    not at module scope so a process that never traces never pays for the
    import — ``langfuse`` brings the OpenTelemetry SDK with it.
    """
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    Langfuse(**_client_options())
    log.info("Langfuse tracing on (%s)", settings.LANGFUSE_HOST)
    return CallbackHandler()


def _client_options() -> dict[str, Any]:
    """How the client is configured, from settings.

    ``environment`` and ``mask`` are left out rather than passed empty, so the
    SDK's own defaults apply to anything not configured here.
    """
    options: dict[str, Any] = {
        "public_key": settings.LANGFUSE_PUBLIC_KEY,
        "secret_key": settings.LANGFUSE_SECRET_KEY,
        "host": settings.LANGFUSE_HOST,
        # Groups traces by the version that produced them, which is the one
        # thing a log line cannot tell you after a redeploy.
        "release": __version__,
    }
    if settings.LANGFUSE_ENVIRONMENT:
        options["environment"] = settings.LANGFUSE_ENVIRONMENT
    if settings.LANGFUSE_HIDE_CONTENT:
        options["mask"] = _redact
    options["should_export_span"] = _worth_exporting
    return options


def _worth_exporting(span: ReadableSpan) -> bool:
    """Whether one finished span is worth storing.

    Composed with the SDK's own default filter rather than replacing it: that
    default is what keeps other libraries' HTTP and database spans out of the
    trace, and this only drops the routing nodes on top of it.
    """
    from langfuse import is_default_export_span

    return is_default_export_span(span) and span.name not in ROUTING_NODES


def _redact(*, data: object, **_kwargs: object) -> str:
    """Replace one traced input or output with a placeholder.

    Matches Langfuse's ``MaskFunction``: the call tree, the latencies and the
    token counts still arrive, the résumé profile in the system prompt does not.

    Args:
        data: The input or output Langfuse is about to record. Annotated
            ``object`` rather than ``Any`` because nothing here reads it — the
            keyword-only shape is what Langfuse's ``MaskFunction`` requires.
    """
    return REDACTED
