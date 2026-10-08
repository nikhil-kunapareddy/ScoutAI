"""What an agent is, and the graph every one of them compiles to.

An ``AgentSpec`` describes one agent — name, system prompt, tool set — and
``build_agent_graph`` turns it into the graph they all share::

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

#: The graph's node names. ``tools_condition`` routes to ``"tools"`` by that
#: literal, and both are span names in Langfuse, so neither is free to change.
_MODEL_NODE = "model"
_TOOLS_NODE = "tools"


@dataclass
class AgentSpec:
    """Declarative description of one agent. Add an agent = add one of these."""

    #: Registry key, used to select the agent (e.g. ``"bigtech"``).
    key: str
    #: Display name (e.g. ``"BigTech Agent"``).
    name: str
    #: The agent's instructions, sent as the system message on every model call.
    system_prompt: str
    #: Tool modules, each defining ``register(reg)``; see ``scout/tools/``.
    tool_modules: list[ModuleType] = field(default_factory=list)
    #: Run behind the resume-parser stage, which appends a profile distilled from
    #: the user's resume to this agent's instructions. See ``resume_tailored.py``.
    tailor_with_resume: bool = False
    #: Whether this agent has a daily digest of its own: one timer instance, one
    #: process carrying this agent's Slack token, one DM in this agent's window.
    #: Separate from ``tailor_with_resume`` because the two answer different
    #: questions — the Referral Window searches a scope rather than a resume, and
    #: still has something to report every morning. See ``scout/digest.py``.
    in_digest: bool = False


class AgentState(MessagesState):
    """Graph state: the conversation, plus the resume brief when there is one.

    ``MessagesState`` supplies ``messages`` with the ``add_messages`` reducer, so
    nodes return the messages they add rather than the whole list.
    """

    #: Candidate brief from the resume parser; "" for agents that don't use one.
    profile: str


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
    builder.add_node(_MODEL_NODE, _build_model_node(spec, tools))
    builder.add_node(_TOOLS_NODE, ToolNode(tools, handle_tool_errors=_tool_error_message))
    builder.add_edge(START, _MODEL_NODE)
    # tools_condition routes to "tools" when the reply asked for one, else END.
    builder.add_conditional_edges(_MODEL_NODE, tools_condition)
    builder.add_edge(_TOOLS_NODE, _MODEL_NODE)
    return builder


def _build_model_node(spec: AgentSpec, tools: list[BaseTool]) -> AgentNode:
    """The node that calls the model.

    A call that fails raises out of the turn, after ``ChatAnthropic``'s own
    retries: a node that raises commits nothing, so the thread is left as it
    was before the failed call.
    """

    def call_model(state: AgentState, config: RunnableConfig) -> dict:
        messages = [
            SystemMessage(_instructions(spec, state.get("profile", ""))),
            *_within_window(state["messages"]),
        ]
        return {"messages": [models.with_tools(tools).invoke(messages, config)]}

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
        max_tokens=settings.MAX_TURNS * 2,  # MAX_TURNS counts question/reply pairs
        start_on="human",
        include_system=False,
        allow_partial=False,
    )


def _tool_error_message(exc: Exception) -> str:
    """Turn a tool failure into text the model can read and act on."""
    log.warning("Tool failed: %s", exc)
    return f"Error running tool: {exc}"
