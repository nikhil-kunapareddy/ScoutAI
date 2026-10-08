"""The resume-parser → job-search hand-off, as one graph::

    START ──▶ (profile cached?) ──yes──▶ job_agent ──▶ END
                    │                    (subgraph)
                    │ no                     ▲
                    ▼                        │
              parse_resume ─── profile ──────┘

``parse_resume`` runs the Resume Parser on its own graph and its own thread, so
the parser's tool calls and JSON reply never land in the job agent's history —
all it contributes to the conversation is ``profile``, the rendered brief.

Because ``profile`` lives in the checkpoint, the router skips the parse on every
later turn: the cache *is* the state, not a dict kept beside it. Job agents never
read the resume themselves; the brief reaches them appended to their instructions
(see ``_instructions`` in ``scout/core/agent.py``).

Opting in is one flag on an ``AgentSpec``: ``tailor_with_resume=True``.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from ..core.agent import AgentSpec, AgentState, agent_tools, build_agent_graph
from ..core.checkpoints import build_checkpointer
from ..core.logging_config import logger
from ..core.runner import GraphRunner
from .resume_parser import SPEC as RESUME_SPEC
from .resume_parser import parse_profile

log = logger()

#: What we ask the parser for; its system prompt does the real work.
_PARSE_REQUEST = "Extract my candidate profile."

#: The graph's node names. They are also span names in Langfuse and appear in
#: the architecture docs, so renaming one is a visible change.
_PARSE_NODE = "parse_resume"
_JOB_NODE = "job_agent"


class ResumeTailoredAgent(GraphRunner):
    """Runs ``job_spec`` with the user's resume profile in its instructions."""

    # This graph's own two nodes, parse_resume and job_agent. The tool loop runs
    # inside the subgraph, which gets the recursion limit afresh.
    _min_steps = 2

    def __init__(self, job_spec: AgentSpec) -> None:
        self._parser_checkpointer = build_checkpointer()
        self._parser = build_agent_graph(RESUME_SPEC, agent_tools(RESUME_SPEC)).compile(
            checkpointer=self._parser_checkpointer
        )

        job_tools = agent_tools(job_spec)
        checkpointer = build_checkpointer()
        super().__init__(
            name=f"{job_spec.name} (resume-tailored)",
            graph=self._build_graph(job_spec, job_tools).compile(checkpointer=checkpointer),
            checkpointer=checkpointer,
            # The parser's only tool is the resume reader; the job tools are the
            # ones worth logging at start-up.
            tools=job_tools,
        )

    def reset(self, user_id: str) -> None:
        """Forget the user's conversation and their parsed resume.

        Clearing the job thread drops the cached profile with it, so the next
        message re-parses.

        Args:
            user_id: The Slack user whose threads to clear.
        """
        super().reset(user_id)
        self._parser_checkpointer.delete_thread(_parser_thread_id(user_id))

    def _build_graph(self, job_spec: AgentSpec, job_tools: list[BaseTool]) -> StateGraph:
        """Wire the parse-once hand-off in front of the job agent."""
        builder = StateGraph(AgentState)
        builder.add_node(_PARSE_NODE, self._parse_resume)
        # Compiled with no checkpointer of its own: as a subgraph it inherits
        # this graph's, so the job conversation lives in the user's thread.
        builder.add_node(_JOB_NODE, build_agent_graph(job_spec, job_tools).compile())
        builder.add_conditional_edges(START, _needs_profile, [_PARSE_NODE, _JOB_NODE])
        builder.add_edge(_PARSE_NODE, _JOB_NODE)
        builder.add_edge(_JOB_NODE, END)
        return builder

    def _parse_resume(self, state: AgentState, config: RunnableConfig) -> dict[str, str]:
        """Run the Resume Parser and hand its brief on as ``profile``."""
        user_id = config["configurable"]["thread_id"]
        request = {"messages": [HumanMessage(_PARSE_REQUEST)]}
        parser_config: RunnableConfig = {
            "configurable": {
                "thread_id": _parser_thread_id(user_id),
                # Start a fresh checkpoint namespace: this is its own graph,
                # not a subgraph of the one calling it.
                "checkpoint_ns": "",
                "checkpoint_id": None,
            },
            "recursion_limit": config["recursion_limit"],
        }

        parser_state = self._parser.invoke(request, parser_config)
        brief = parse_profile(parser_state["messages"][-1].text).to_search_brief()
        log.info("Parsed resume profile for %s", user_id)
        return {"profile": brief}


def _needs_profile(state: AgentState) -> str:
    """Route a thread's first turn to the parser, and every later one straight on.

    The function name is load-bearing: ``tracing.ROUTING_NODES`` drops this
    router's span by it.
    """
    return _JOB_NODE if state.get("profile") else _PARSE_NODE


def _parser_thread_id(user_id: str) -> str:
    """The parser's thread for a user, e.g. ``U123:resume``.

    Kept apart from the user's job-search thread, so the parser's tool calls and
    JSON reply never land in that history.
    """
    return f"{user_id}:resume"
