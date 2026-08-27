"""The agent runtime: per-user conversation memory plus the tool-call loop.

An ``AgentSpec`` describes one agent — name, system prompt, tool set, default
backend — and ``Agent`` runs it. Adding an agent means writing one spec (see
``scout/agents/``), not touching this loop.

Each user has their own history and chosen backend, switchable at runtime.
History holds only plain user/assistant text, which keeps it backend-agnostic: a
user can switch mid-conversation, and a failed turn can be replayed on
``settings.FALLBACK_BACKEND``.

``ConversationalAgent`` is the interface the Slack adapter talks to. ``Agent``
implements it for a single model; ``scout/agents/resume_tailored.py`` chains two
of them behind the same interface, so the adapter needs no special cases.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import ModuleType

from ..tools import ToolRegistry, build_registry
from . import settings
from .backends import ChatBackend, build_backends
from .backends.base import ToolCall

log = logging.getLogger("scout")

#: One history entry: plain text, no provider-specific content blocks.
Message = dict[str, str]


@dataclass
class AgentSpec:
    """Declarative description of one agent. Add an agent = add one of these."""

    key: str                        # id used to select the agent (e.g. "bigtech")
    name: str                       # display name (e.g. "BigTech Agent")
    system_prompt: str
    tool_modules: list[ModuleType] = field(default_factory=list)  # each has register(reg)
    default_backend: str = settings.DEFAULT_BACKEND
    # Run behind the resume-parser hand-off, prefixing every turn with a profile
    # distilled from the user's resume. See scout/agents/resume_tailored.py.
    tailor_with_resume: bool = False


class ConversationalAgent(ABC):
    """What the Slack adapter needs from an agent, however it's built.

    Implemented by ``Agent`` and by ``ResumeTailoredAgent``. Coding the adapter
    against this is what keeps ``scout/slack/`` free of pipeline knowledge.
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


class Agent(ConversationalAgent):
    """Runs one ``AgentSpec``: holds conversation state and drives the tool loop."""

    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self.name = spec.name
        self.tools: ToolRegistry = build_registry(spec.tool_modules)
        self._backends = build_backends()
        # Per-user state. These grow with the number of distinct users (bounded by
        # the workspace) and are never evicted: dropping someone's history behind
        # their back would be surprising.
        self._histories: dict[str, deque[Message]] = defaultdict(
            lambda: deque(maxlen=settings.MAX_TURNS * 2)
        )
        self._chosen_backend: dict[str, str] = {}
        self._answering_backend: dict[str, str] = {}  # who actually answered last

        # slack-bolt dispatches events on a thread pool. One lock per user
        # serializes that user's turns while other users run concurrently;
        # _locks_guard covers creating them.
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def tool_names(self) -> list[str]:
        return self.tools.names()

    # --- Per-user state -------------------------------------------------

    def _lock_for(self, user_id: str) -> threading.RLock:
        """The lock for this user, created on first use."""
        with self._locks_guard:
            lock = self._locks.get(user_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[user_id] = lock
            return lock

    def reset(self, user_id: str) -> None:
        with self._lock_for(user_id):
            self._histories.pop(user_id, None)

    def backend_name(self, user_id: str) -> str:
        # Lock-free: dict.get is atomic under the GIL, and a stale read against a
        # concurrent set_backend is harmless.
        return self._chosen_backend.get(user_id, self.spec.default_backend)

    def backend_label(self, user_id: str) -> str:
        return self._backends[self.backend_name(user_id)].label

    def last_backend(self, user_id: str) -> str:
        """Differs from ``backend_name`` only when the chosen backend failed and
        the fallback answered."""
        return self._answering_backend.get(user_id, self.backend_name(user_id))

    def set_backend(self, user_id: str, name: str) -> bool:
        if name not in self._backends:
            return False
        with self._lock_for(user_id):
            self._chosen_backend[user_id] = name
        return True

    # --- Answering a message --------------------------------------------

    def respond(self, user_id: str, prompt: str) -> str:
        """Run ``prompt`` through this user's backend and tool loop.

        If that backend fails (outage, missing key, rate limit) the whole turn is
        replayed once on ``settings.FALLBACK_BACKEND``. History holds plain text
        only, so nothing from the failed attempt leaks in, and the user's choice
        is left alone so the next turn tries it again.

        Holds the user's lock for the whole turn, serializing their messages;
        other users run in parallel.
        """
        with self._lock_for(user_id):
            history = self._histories[user_id]
            history.append({"role": "user", "content": prompt})

            chosen = self.backend_name(user_id)
            try:
                reply = self._run_turn(self._backends[chosen], history)
                self._answering_backend[user_id] = chosen
            except Exception:
                fallback = self._fallback_for(chosen)
                if fallback is None:
                    raise
                log.exception("Backend %s failed; replaying the turn on %s",
                              chosen, fallback.name)
                reply = self._run_turn(fallback, history)
                self._answering_backend[user_id] = fallback.name

            history.append({"role": "assistant", "content": reply})
            return reply

    def _fallback_for(self, chosen: str) -> ChatBackend | None:
        """The backend to retry a failed turn on, or None if there isn't one."""
        name = settings.FALLBACK_BACKEND
        return None if name == chosen else self._backends.get(name)

    def _run_turn(self, backend: ChatBackend, history: deque[Message]) -> str:
        """One pass of the tool loop against ``backend``; returns the reply text.

        Tool traffic stays in the local ``messages`` list — each backend has its
        own wire format for it — which is what keeps ``history`` replayable
        elsewhere.
        """
        messages: list[dict] = [
            {"role": "system", "content": self.spec.system_prompt},
            *_messages_from(history),
        ]

        for _ in range(settings.MAX_TOOL_HOPS):
            result = backend.chat(messages, self.tools.schemas)

            if not result.tool_calls:
                return result.text

            # Record the request, run each tool, feed the results back in this
            # backend's format, then loop so the model can answer.
            messages.append(backend.assistant_message(result))
            for call in result.tool_calls:
                messages.append(backend.tool_result_message(call, self._run_tool(call)))

        log.warning("Hit the %s-hop tool limit without a final answer",
                    settings.MAX_TOOL_HOPS)
        return "Sorry, I got stuck calling my tools. Please try rephrasing."

    def _run_tool(self, call: ToolCall) -> str:
        """Run one tool call, turning a failure into text the model can read."""
        try:
            result = self.tools.call(call.name, call.args)
        except Exception as e:
            log.exception("Tool %s failed", call.name)
            result = f"Error running tool: {e}"
        log.info("tool %s(%s) -> %s", call.name, call.args, str(result)[:120])
        return result


def _messages_from(history: deque[Message]) -> list[Message]:
    """The history to send, guaranteed to start with a user message.

    ``history`` is bounded, so a full deque evicts its oldest entry on append,
    which can leave an assistant reply at the front. Providers expect a
    conversation to open on the user's side.
    """
    messages = list(history)
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages
