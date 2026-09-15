"""The read-only view of the referral list, for the job-search agents.

A separate module rather than a flag on ``referrals`` because ``tool_modules``
is a list of modules, and because the split is the point: referrals are managed
in the Referral Window and merely *consulted* while searching. A job agent that
could write would eventually decide, mid-search, to helpfully record something
the user never asked it to.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from .referrals import render_list
from .registry import ToolRegistry


def register(reg: ToolRegistry) -> None:
    @reg.tool
    def list_referrals(*, config: RunnableConfig) -> str:
        """List the companies where the user has a referral connection.

        Call this before presenting job results so roles at those companies can
        be surfaced first — a referral is the strongest signal the user has.
        """
        return render_list(config)
