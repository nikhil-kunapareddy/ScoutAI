"""Approximate location, from IP-based geolocation.

City-level only, and it shares the host's public IP with ip-api.com. That
service's free tier is HTTP-only, hence the URL — nothing sensitive is sent, but
the reply isn't authenticated either, so treat the result as a hint.
"""

from __future__ import annotations

import requests

from ..core import settings
from .registry import ToolRegistry

GEO_API_URL = "http://ip-api.com/json/"


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def get_location() -> str:
        """Return the approximate current location (city, region, country),
        derived from the host's public IP address."""
        try:
            resp = requests.get(GEO_API_URL, timeout=settings.GEO_REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return "Location unavailable (couldn't reach the geolocation service)."

        if data.get("status") != "success":
            return "Location unavailable."
        parts = [data.get("city"), data.get("regionName"), data.get("country")]
        return ", ".join(p for p in parts if p) or "Location unavailable."
