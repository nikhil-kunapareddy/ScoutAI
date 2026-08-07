"""
The Agent runtime: per-user conversation memory plus the tool-call loop, over a
pluggable model backend.

An ``AgentSpec`` declaratively describes a single agent — its name, system
prompt, tool set, and default backend — and ``Agent`` runs it. Adding a new
agent to the platform means writing one ``AgentSpec`` (see ``scout/agents/``),
not touching this loop.

Each user has their own conversation history and chosen backend (local Ollama or
the hosted Meta Llama API), switchable at runtime. History stores only plain
user/assistant text, so it stays backend-agnostic and a user can switch
mid-conversation.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from types import ModuleType

from . import settings
from .backends import build_backends
from .backends.base import ToolCall
from ..tools import build_registry

log = logging.getLogger("scout")


@dataclass
class AgentSpec:
    """Declarative description of one agent. Add an agent = add one of these."""

    key: str                        # short id used to select the agent (e.g. "bigtech")
    name: str                       # display name (e.g. "BigTech Agent")
    system_prompt: str
    tool_modules: list[ModuleType]  # tool modules, each exposing register(reg)
    default_backend: str = "ollama"


class Agent:
    """Runs an ``AgentSpec``: holds conversation state and drives the tool loop."""

    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self.tools = build_registry(spec.tool_modules)
        self._backends = build_backends()
        self._histories: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=settings.MAX_TURNS * 2)
        )
        self._user_backend: dict[str, str] = {}  # user_id -> backend name

        # slack-bolt dispatches events on a thread pool. A per-user lock serializes
        # a single user's turns (so their shared history/backend stays consistent)
        # while different users run concurrently. _registry_lock guards creating them.
        self._registry_lock = threading.Lock()
        self._user_locks: dict[str, threading.RLock] = {}

    def _user_lock(self, user_id: str) -> threading.RLock:
        """Return the lock for ``user_id``, creating it on first use."""
        with self._registry_lock:
            lock = self._user_locks.get(user_id)
            if lock is None:
                lock = threading.RLock()
                self._user_locks[user_id] = lock
            return lock

    def reset(self, user_id: str) -> None:
        with self._user_lock(user_id):
            self._histories.pop(user_id, None)

    def backend_name(self, user_id: str) -> str:
        # Lock-free read: dict.get is atomic under the GIL and a stale read here
        # (vs. a concurrent set_backend) is harmless.
        return self._user_backend.get(user_id, self.spec.default_backend)

    def backend_label(self, user_id: str) -> str:
        return self._backends[self.backend_name(user_id)].label

    def set_backend(self, user_id: str, name: str) -> bool:
        """Switch a user's backend. Returns False if the name is unknown."""
        if name not in self._backends:
            return False
        with self._user_lock(user_id):
            self._user_backend[user_id] = name
        return True

    def respond(self, user_id: str, prompt: str) -> str:
        """Run ``prompt`` through the user's backend and tool loop, return the reply.

        Holds the user's lock for the whole turn, so concurrent messages from the
        same user are serialized (their shared history stays consistent); other
        users run in parallel.
        """
        with self._user_lock(user_id):
            backend = self._backends[self.backend_name(user_id)]
            history = self._histories[user_id]
            history.append({"role": "user", "content": prompt})
            messages = [{"role": "system", "content": self.spec.system_prompt}, *history]

            for _ in range(settings.MAX_TOOL_HOPS):
                result = backend.chat(messages, self.tools.tools)

                if not result.tool_calls:
                    reply = result.text
                    history.append({"role": "assistant", "content": reply})
                    return reply

                # The model wants to call tools: record the request, run each tool, feed the
                # results back (in this backend's wire format), then loop so it can answer.
                messages.append(backend.assistant_message(result))
                for call in result.tool_calls:
                    messages.append(backend.tool_result_message(call, self._run_tool(call)))

            reply = "Sorry, I got stuck calling my tools. Please try rephrasing."
            history.append({"role": "assistant", "content": reply})
            return reply

    def _run_tool(self, call: ToolCall) -> str:
        try:
            result = self.tools.call_tool(call.name, call.args)
        except Exception as e:
            log.exception("Tool %s failed", call.name)
            result = f"Error running tool: {e}"
        log.info("tool %s(%s) -> %s", call.name, call.args, str(result)[:120])
        return result
