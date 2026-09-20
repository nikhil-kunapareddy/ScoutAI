"""What an agent is, and the graph every one of them compiles to.

An ``AgentSpec`` describes one agent — name, system prompt, tool set, default
backend — and ``build_agent_graph`` turns it into the graph they all share::

    START ──▶ model ──tool calls?──▶ tools ──┐
                 │                           │
                 │ none                      │
                 ▼                           │
                END      ◀───────────────────┘

Adding an agent means writing one spec (see ``scout/agents/``), not touching
this graph. Running one is ``scout/core/runner.py``, which drives the compiled
graph for many users; ``ConversationalAgent`` here is the interface between the
two, and the only thing the Slack adapter is allowed to know.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from types import ModuleType
from typing import Protocol

from langchain_core.messages import BaseMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from ..tools import build_registry
from . import models, settings
from .logging_config import logger

log = logger()


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
    # Whether this agent has a daily digest of its own: one timer instance, one
    # process carrying this agent's Slack token, one DM in this agent's window.
    # Separate from tailor_with_resume because the two answer different
    # questions — the Referral Window searches a scope rather than a resume, and
    # still has something to report every morning. See scout/digest.py.
    in_digest: bool = False


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
