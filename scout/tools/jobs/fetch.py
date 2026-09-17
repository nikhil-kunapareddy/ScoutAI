"""Talking to a job source over HTTP.

Every source repeated the same request: the browser user-agent the boards
expect, the timeout from settings, and ``None`` for any failure at all. That
``None`` is the source saying "I don't know", which callers must keep apart from
an empty result — a company whose board was down is not a company with nothing
open, and the Referral Window's whole promise rests on the difference.

Stubbing this one module is also how the suite runs without a network; see
``call_tool`` in ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import requests
from requests import Response

from ...core import settings

_JSON_ACCEPT = {"Accept": "application/json"}

#: Query string or JSON body. Values are whatever a board's API wants.
Params = Mapping[str, Any]


def get_text(url: str, params: Params | None = None) -> str | None:
    """A page or feed as text, or None if the source can't be reached."""
    response = _get(url, params)
    return None if response is None else response.text


def get_rows(url: str, key: str, params: Params | None = None) -> list[dict] | None:
    """The rows under ``key`` from a JSON endpoint, or None if unreachable."""
    return _rows(_get(url, params, _JSON_ACCEPT), key)


def post_rows(url: str, key: str, payload: Params) -> list[dict] | None:
    """As ``get_rows``, for an endpoint that takes its query in the body."""
    return _rows(_post(url, payload), key)


def json_rows(payload: object, key: str) -> list[dict]:
    """The rows under ``key`` in a decoded JSON body, or [] if it isn't shaped
    that way.

    A decoded body is whatever the source sent, and a board under load answers
    with an error document often enough to be worth expecting: checking the
    shape here is what keeps every source's parser working on rows only.
    """
    if not isinstance(payload, dict):
        return []
    found = payload.get(key)
    if not isinstance(found, list):
        return []
    return [row for row in found if isinstance(row, dict)]


def _get(url: str, params: Params | None, accept: Params | None = None) -> Response | None:
    """One GET, or None if the source could not be reached."""
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": settings.TOOL_USER_AGENT, **(accept or {})},
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response
    except Exception:
        return None


def _post(url: str, payload: Params) -> Response | None:
    """One JSON POST, or None if the source could not be reached."""
    try:
        response = requests.post(
            url,
            json=payload,
            headers={
                "User-Agent": settings.TOOL_USER_AGENT,
                "Content-Type": "application/json",
                **_JSON_ACCEPT,
            },
            timeout=settings.TOOL_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response
    except Exception:
        return None


def _rows(response: Response | None, key: str) -> list[dict] | None:
    """Decode a JSON body into rows, keeping None as "never reached".

    A body that will not decode is treated as unreachable too: the source
    answered with something that isn't its API, which is a failure to look
    rather than a report of nothing.
    """
    if response is None:
        return None
    try:
        return json_rows(response.json(), key)
    except Exception:
        return None
