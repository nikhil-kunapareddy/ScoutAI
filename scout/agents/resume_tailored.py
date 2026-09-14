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

import logging

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from ..core.agent import (
    AgentSpec,
    AgentState,
    GraphRunner,
    agent_tools,
    build_agent_graph,
)
from ..core.checkpoints import build_checkpointer
from .resume_parser import SPEC as RESUME_SPEC
from .resume_parser import parse_profile

log = logging.getLogger("scout")

#: What we ask the parser for; its system prompt does the real work.
_PARSE_REQUEST = "Extract my candidate profile."

#: The parser's thread for a user, kept apart from their job-search thread.
_PARSER_THREAD = "{user_id}:resume"


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
        builder = StateGraph(AgentState)
        builder.add_node("parse_resume", self._parse_resume)
        # Compiled with no checkpointer of its own: as a subgraph it inherits
        # this graph's, so the job conversation lives in the user's thread.
        builder.add_node("job_agent", build_agent_graph(job_spec, job_tools).compile())
        builder.add_conditional_edges(
            START,
            _needs_profile,
            {"parse_resume": "parse_resume", "job_agent": "job_agent"},
        )
        builder.add_edge("parse_resume", "job_agent")
        builder.add_edge("job_agent", END)

        super().__init__(
            name=f"{job_spec.name} (resume-tailored)",
            graph=builder.compile(checkpointer=checkpointer),
            checkpointer=checkpointer,
            default_backend=job_spec.default_backend,
            # The parser's only tool is the resume reader; the job tools are the
            # ones worth logging at start-up.
            tools=job_tools,
        )

    def _parse_resume(self, state: AgentState, config: RunnableConfig) -> dict:
        """Run the Resume Parser and hand its brief on as ``profile``."""
        user_id = config["configurable"]["thread_id"]
        reply = self._parser.invoke(
            {"messages": [HumanMessage(_PARSE_REQUEST)]},
            {
                "configurable": {
                    "thread_id": _PARSER_THREAD.format(user_id=user_id),
                    # Both stages move together, so a mid-conversation switch
                    # can't leave the pipeline half on one model.
                    "backend": config["configurable"].get("backend"),
                    # Start a fresh checkpoint namespace: this is its own graph,
                    # not a subgraph of the one calling it.
                    "checkpoint_ns": "",
                    "checkpoint_id": None,
                },
                "recursion_limit": config["recursion_limit"],
            },
        )
        brief = parse_profile(reply["messages"][-1].text).to_search_brief()
        log.info("Parsed resume profile for %s", user_id)
        return {"profile": brief}

    def reset(self, user_id: str) -> None:
        # Clearing the thread drops the cached profile with it, so the next
        # message re-parses.
        super().reset(user_id)
        self._parser_checkpointer.delete_thread(
            _PARSER_THREAD.format(user_id=user_id)
        )


def _needs_profile(state: AgentState) -> str:
    """Parse the resume once per thread; later turns go straight to the job agent."""
    return "job_agent" if state.get("profile") else "parse_resume"
