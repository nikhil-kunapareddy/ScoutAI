"""The agent runtime: one LangGraph state graph per agent, driven per user.

An ``AgentSpec`` describes one agent — name, system prompt, tool set, default
backend — and ``build_agent_graph`` turns it into the graph every agent shares::

    START ──▶ model ──tool calls?──▶ tools ──┐
                 │                           │
                 │ none                      │
                 ▼                           │
                END      ◀───────────────────┘

Adding an agent means writing one spec (see ``scout/agents/``), not touching
this graph.

``GraphRunner`` drives a compiled graph for many users: each gets their own
checkpointer thread — that thread *is* their conversation history — and their
chosen backend rides along in the graph config, so one compiled graph serves
every user on every model. Because history is stored as provider-agnostic
message objects, a user can switch model mid-conversation and a failed model
call can be retried on ``settings.FALLBACK_BACKEND`` inside the same turn.

``ConversationalAgent`` is the interface the Slack adapter codes against.
``Agent`` implements it for a single graph; ``scout/agents/resume_tailored.py``
puts a resume-parsing stage in front of one behind the same interface.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Protocol

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    trim_messages,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ..tools import build_registry
from . import metrics, models, settings
from .checkpoints import build_checkpointer

log = logging.getLogger("scout")

#: Reply when a turn used up its tool hops without answering.
STUCK_REPLY = "Sorry, I got stuck calling my tools. Please try rephrasing."

#: Stands in for the result of a tool call abandoned at the hop limit.
STUCK_TOOL_RESULT = "Stopped: this turn ran out of tool calls."

#: Reply when the model returns neither text nor a tool call (a refusal, or a
#: max_tokens cut-off).
EMPTY_REPLY = "The model returned an empty reply. Please try rephrasing."


@dataclass
class AgentSpec:
    """Declarative description of one agent. Add an agent = add one of these."""

    key: str                        # id used to select the agent (e.g. "bigtech")
    name: str                       # display name (e.g. "BigTech Agent")
    system_prompt: str
    tool_modules: list[ModuleType] = field(default_factory=list)  # each has register(reg)
    default_backend: str = settings.DEFAULT_BACKEND
    # Run behind the resume-parser stage, which appends a profile distilled from
    # the user's resume to this agent's instructions. See resume_tailored.py.
    tailor_with_resume: bool = False


class AgentState(MessagesState):
    """Graph state: the conversation plus what the run needs to report itself.

    ``MessagesState`` supplies ``messages`` with the ``add_messages`` reducer, so
    nodes return the messages they add rather than the whole list.
    """

    #: Candidate brief from the resume parser; "" for agents that don't use one.
    profile: str
    #: Backend that produced the last reply — the fallback, if the chosen one failed.
    answered_by: str


class AgentNode(Protocol):
    """One node of the graph: given the state, return the state it adds.

    A Protocol and not a ``Callable[...]`` alias because LangGraph matches node
    signatures by parameter *name* — a Callable type carries none, so it would
    not satisfy ``add_node``.
    """

    def __call__(self, state: AgentState, config: RunnableConfig) -> dict: ...


class ConversationalAgent(ABC):
    """What the Slack adapter needs from an agent, however it's built.

    Implemented by ``GraphRunner``, and so by both ``Agent`` and
    ``ResumeTailoredAgent``. Coding the adapter against this is what keeps
    ``scout/slack/`` free of graph knowledge.
    """

    #: Display name, used in start-up logs.
    name: str

    @abstractmethod
    def respond(self, user_id: str, prompt: str) -> str:
        """Answer ``prompt``, running tools as needed."""

    @abstractmethod
    def reset(self, user_id: str) -> None:
        """Forget everything remembered about this user."""

    @abstractmethod
    def set_backend(self, user_id: str, name: str) -> bool:
        """Switch this user's backend; False if the name is unknown."""

    @abstractmethod
    def backend_name(self, user_id: str) -> str:
        """Name of the backend this user has chosen."""

    @abstractmethod
    def backend_label(self, user_id: str) -> str:
        """Human-readable label of the backend this user has chosen."""

    @abstractmethod
    def last_backend(self, user_id: str) -> str:
        """Backend that actually answered this user's last turn."""

    @abstractmethod
    def tool_names(self) -> list[str]:
        """Tools this agent can call, for start-up diagnostics."""


# --- Building the graph ----------------------------------------------------


def agent_tools(spec: AgentSpec) -> list[BaseTool]:
    """The tools ``spec`` asked for, as LangChain tools."""
    return build_registry(spec.tool_modules).tools


def build_agent_graph(spec: AgentSpec, tools: list[BaseTool]) -> StateGraph:
    """Build — but don't compile — the model/tools graph for ``spec``.

    Left uncompiled so the caller owns the checkpointer: a top-level agent gets
    its own, while a graph embedded as a subgraph inherits its parent's.
    """
    builder = StateGraph(AgentState)
    builder.add_node("model", _model_node(spec, tools))
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=_tool_error))
    builder.add_edge(START, "model")
    # tools_condition routes to "tools" when the reply asked for one, else END.
    builder.add_conditional_edges("model", tools_condition)
    builder.add_edge("tools", "model")
    return builder


def _model_node(spec: AgentSpec, tools: list[BaseTool]) -> AgentNode:
    """The node that calls the model, retrying once on the fallback backend."""

    def call_model(state: AgentState, config: RunnableConfig) -> dict:
        chosen = config["configurable"].get("backend") or spec.default_backend
        messages = [
            SystemMessage(_instructions(spec, state.get("profile", ""))),
            *_within_window(state["messages"]),
        ]

        try:
            reply = models.with_tools(chosen, tools).invoke(messages, config)
            answered = chosen
        except Exception:
            fallback = _fallback_for(chosen)
            if fallback is None:
                raise
            # Retried here rather than around the whole turn so tool results
            # already fetched are kept, and nothing from the failed call is
            # recorded: a node that raises commits no messages.
            log.exception("Backend %s failed; retrying on %s", chosen, fallback)
            reply = models.with_tools(fallback, tools).invoke(messages, config)
            answered = fallback

        return {"messages": [reply], "answered_by": answered}

    return call_model


def _instructions(spec: AgentSpec, profile: str) -> str:
    """The system prompt, with the candidate brief appended when there is one."""
    return f"{spec.system_prompt}\n\n{profile}" if profile else spec.system_prompt


def _within_window(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The tail of the conversation to send, bounded by ``MAX_TURNS``.

    ``start_on="human"`` is what keeps the request valid wherever the window
    falls: providers expect a conversation to open on the user's side, and a
    tool result must never lead it or be separated from the call it answers.

    Tool traffic is checkpointed now, so it counts against the window — the
    model remembers what it already looked up, at the cost of a shorter reach
    back through a tool-heavy conversation.
    """
    return trim_messages(
        messages,
        strategy="last",
        token_counter=len,  # count messages, not tokens
        max_tokens=settings.MAX_TURNS * 2,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )


def _fallback_for(chosen: str) -> str | None:
    """The backend to retry a failed model call on, or None if there isn't one.

    An empty ``FALLBACK_BACKEND`` disables the retry, which is what the
    container image wants: there is no Ollama in it, so retrying would only make
    every Claude failure fail twice.
    """
    name = settings.FALLBACK_BACKEND
    if not name or name == chosen or not models.exists(name):
        return None
    return name


def _tool_error(exc: Exception) -> str:
    """Turn a tool failure into text the model can read and act on."""
    log.warning("Tool failed: %s", exc)
    return f"Error running tool: {exc}"


# --- Driving the graph ----------------------------------------------------


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
        """The graph config for one user: their thread and their model."""
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
            try:
                state = self._graph.invoke(
                    {"messages": [HumanMessage(prompt)]}, config
                )
            except GraphRecursionError:
                log.warning(
                    "Hit the %s-hop tool limit without a final answer",
                    settings.MAX_TOOL_HOPS,
                )
                reply = self._give_up(config)
                self._measure(user_id, config, started, metrics.STUCK)
                return reply
            except Exception:
                self._measure(user_id, config, started, metrics.ERROR)
                raise

            self._measure(user_id, config, started, metrics.OK, state)
            return _reply_text(state["messages"][-1])

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
        self._graph.update_state(
            config, {"messages": [*answers, AIMessage(STUCK_REPLY)]}
        )
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
