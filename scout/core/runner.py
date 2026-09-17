"""Driving a compiled graph for many users.

``build_agent_graph`` (see ``agent.py``) produces the graph; this runs it. Each
user gets their own checkpointer thread — that thread *is* their conversation
history — and their chosen backend rides along in the graph config, so one
compiled graph serves every user on every model. Because history is stored as
provider-agnostic message objects, a user can switch model mid-conversation, and
a failed model call can be retried on ``settings.FALLBACK_BACKEND`` within the
same turn.

``GraphRunner`` implements ``ConversationalAgent`` once, for both ``Agent`` here
and ``ResumeTailoredAgent`` in ``scout/agents/resume_tailored.py``. That is what
keeps ``scout/slack/`` free of graph knowledge: the adapter has one interface and
no special cases.
"""

from __future__ import annotations

import threading
import time

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph

from . import metrics, models, settings, tracing
from .agent import AgentSpec, ConversationalAgent, agent_tools, build_agent_graph
from .checkpoints import build_checkpointer
from .logging_config import logger

log = logger()

#: Reply when a turn used up its tool hops without answering.
STUCK_REPLY = "Sorry, I got stuck calling my tools. Please try rephrasing."

#: Stands in for the result of a tool call abandoned at the hop limit.
STUCK_TOOL_RESULT = "Stopped: this turn ran out of tool calls."

#: Reply when the model returns neither text nor a tool call (a refusal, or a
#: max_tokens cut-off).
EMPTY_REPLY = "The model returned an empty reply. Please try rephrasing."


class GraphRunner(ConversationalAgent):
    """Runs one compiled graph for many Slack users.

    Owns everything that is per-user rather than per-graph: the checkpointer
    thread, the chosen backend, and the lock that serializes a user's turns.
    """

    #: Floor on the recursion limit: the super-steps this graph needs outside the
    #: model/tools loop. Subclasses that wrap the loop in more nodes raise it.
    #: It is a floor and not an addition because a subgraph is given the limit
    #: afresh, so adding to it would hand the inner loop extra tool hops.
    _min_steps = 1

    def __init__(
        self,
        name: str,
        graph: CompiledStateGraph,
        checkpointer: BaseCheckpointSaver,
        default_backend: str,
        tools: list[BaseTool],
    ) -> None:
        self.name = name
        self._graph = graph
        self._checkpointer = checkpointer
        self._default_backend = default_backend
        self._tools = tools
        self._chosen_backend: dict[str, str] = {}

        # slack-bolt dispatches events on a thread pool. One lock per user
        # serializes that user's turns while other users run concurrently;
        # _locks_guard covers creating them.
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def tool_names(self) -> list[str]:
        return [tool.name for tool in self._tools]

    # --- Per-user state -------------------------------------------------

    def _lock_for(self, user_id: str) -> threading.RLock:
        """The lock for this user, created on first use."""
        with self._locks_guard:
            lock = self._locks.get(user_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[user_id] = lock
            return lock

    def _config(self, user_id: str) -> RunnableConfig:
        """The graph config for one user: their thread and their model.

        Untraced on purpose: this config is also what the checkpointer reads and
        repairs are written with, which are not turns. ``respond`` makes the
        traced copy for the one call that is.
        """
        return {
            "configurable": {
                "thread_id": user_id,
                "backend": self.backend_name(user_id),
            },
            # A tool hop is two super-steps (model, then tools) and a turn ends
            # on a model step, so n model calls is 2n-1 steps.
            "recursion_limit": max(2 * settings.MAX_TOOL_HOPS - 1, self._min_steps),
        }

    def reset(self, user_id: str) -> None:
        with self._lock_for(user_id):
            self._checkpointer.delete_thread(user_id)

    def backend_name(self, user_id: str) -> str:
        # Lock-free: dict.get is atomic under the GIL, and a stale read against a
        # concurrent set_backend is harmless.
        return self._chosen_backend.get(user_id, self._default_backend)

    def backend_label(self, user_id: str) -> str:
        return models.label(self.backend_name(user_id))

    def last_backend(self, user_id: str) -> str:
        """Differs from ``backend_name`` only when the chosen backend failed and
        the fallback answered."""
        state = self._graph.get_state(self._config(user_id))
        return state.values.get("answered_by") or self.backend_name(user_id)

    def set_backend(self, user_id: str, name: str) -> bool:
        if not models.exists(name):
            return False
        with self._lock_for(user_id):
            self._chosen_backend[user_id] = name
        return True

    # --- Answering a message --------------------------------------------

    def respond(self, user_id: str, prompt: str) -> str:
        """Run ``prompt`` through this user's thread and return the reply.

        Holds the user's lock for the whole turn, serializing their messages;
        other users run in parallel.
        """
        with self._lock_for(user_id):
            config = self._config(user_id)
            started = time.monotonic()
            # One turn, one trace (a no-op when tracing is off). The reply is
            # handed back to it so the trace records what the user was told,
            # whichever way the turn ended.
            with tracing.traced(
                config, agent=self.name, thread=user_id, prompt=prompt
            ) as turn:
                try:
                    state = self._graph.invoke(
                        {"messages": [HumanMessage(prompt)]}, turn.config
                    )
                except GraphRecursionError:
                    log.warning(
                        "Hit the %s-hop tool limit without a final answer",
                        settings.MAX_TOOL_HOPS,
                    )
                    reply = self._give_up(config)
                    self._measure(user_id, config, started, metrics.STUCK)
                    turn.answered(reply)
                    return reply
                except Exception:
                    self._measure(user_id, config, started, metrics.ERROR)
                    raise

                self._measure(user_id, config, started, metrics.OK, state)
                reply = _reply_text(state["messages"][-1])
                turn.answered(reply)
                return reply

    def _measure(
        self,
        user_id: str,
        config: RunnableConfig,
        started: float,
        outcome: str,
        state: dict | None = None,
    ) -> None:
        """Log what the turn cost.

        ``state`` is passed on the happy path because the caller already has it;
        the other paths read it back, which also picks up the repair ``_give_up``
        just wrote. Measuring must never be what breaks a reply, hence the catch.
        """
        try:
            values = state if state is not None else self._graph.get_state(config).values
            metrics.record(
                metrics.measure(
                    agent=self.name,
                    thread=user_id,
                    backend=self.backend_name(user_id),
                    answered_by=values.get("answered_by") or self.backend_name(user_id),
                    outcome=outcome,
                    seconds=time.monotonic() - started,
                    messages=values.get("messages", []),
                )
            )
        except Exception:
            log.exception("Could not record turn metrics")

    def _give_up(self, config: RunnableConfig) -> str:
        """End a turn that ran out of tool hops, leaving a reusable thread.

        The run stopped with tool calls still unanswered, and providers reject a
        history where a tool request has no result — so the abandoned calls are
        answered before the apology is recorded. Skipping this would break the
        user's *next* message, not just this one.
        """
        messages = self._graph.get_state(config).values.get("messages", [])
        abandoned = getattr(messages[-1], "tool_calls", None) if messages else None
        answers = [
            ToolMessage(
                content=STUCK_TOOL_RESULT, tool_call_id=call["id"], name=call["name"]
            )
            for call in abandoned or []
        ]
        self._graph.update_state(config, {"messages": [*answers, AIMessage(STUCK_REPLY)]})
        return STUCK_REPLY


class Agent(GraphRunner):
    """Runs one ``AgentSpec`` as a graph of its own."""

    def __init__(self, spec: AgentSpec) -> None:
        tools = agent_tools(spec)
        checkpointer = build_checkpointer()
        super().__init__(
            name=spec.name,
            graph=build_agent_graph(spec, tools).compile(checkpointer=checkpointer),
            checkpointer=checkpointer,
            default_backend=spec.default_backend,
            tools=tools,
        )


def _reply_text(message: BaseMessage) -> str:
    """The text of a reply, however the provider shaped its content."""
    return message.text.strip() or EMPTY_REPLY
