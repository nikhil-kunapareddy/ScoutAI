"""Environment-driven platform configuration, loaded once from the project .env file.

Agent-specific settings (system prompt, tool set, default backend) live on each
``AgentSpec`` in ``scout/agents/`` — this module only holds infrastructure that is
shared across agents.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")

# --- Active agent ---
# Which agent this process runs; see scout/agents/. One process per agent.
ACTIVE_AGENT = os.environ.get("AGENT", "bigtech")

# --- Slack ---
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

# --- Ollama (local backend) ---
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

# --- Meta Llama API (hosted backend) ---
# Only needed when a user switches to the "llama" backend.
LLAMA_API_KEY = os.environ.get("LLAMA_API_KEY", "")
LLAMA_API_URL = os.environ.get("LLAMA_API_URL", "https://api.llama.com/v1/chat/completions")
LLAMA_MODEL = os.environ.get("LLAMA_MODEL", "Llama-4-Maverick-17B-128E-Instruct-FP8")

# --- Conversation ---
MAX_TURNS = 20      # user+assistant message pairs retained per user
MAX_TOOL_HOPS = 5   # tool-call round-trips allowed within a single message

# --- Model HTTP requests ---
MODEL_REQUEST_TIMEOUT_SECONDS = 300  # generation can be slow on local models

# --- Tool HTTP requests ---
TOOL_USER_AGENT = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36"
TOOL_REQUEST_TIMEOUT_SECONDS = 30    # job-search APIs / scraping
GEO_REQUEST_TIMEOUT_SECONDS = 10     # ip-api geolocation
