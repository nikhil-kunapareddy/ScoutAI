"""Job search scoped to the referral list — the Referral Window's one search tool.

The job agents search a *source* and consult the referral list to rank what they
find. This goes the other way: the list is the scope, so every posting in the
reply is one the user could ask someone for. That is the whole promise, and it
only holds if the reply also says what it could not see — a board that was down,
a company Scout has no board for. Both are named in the footer rather than
quietly reading as "nothing open".

One tool over the whole list, not one call per company: a model that has to
remember to call six tools eventually forgets one, and a silently short list is
exactly the failure this agent exists to prevent.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from langchain_core.runnables import RunnableConfig

from ..core import referrals
from ..core.referrals import Referral, ReferralStoreError
from .jobs.directory import CompanySource, resolve
from .jobs.posting import JobPosting, clamp_int, render_postings
from .registry import ToolRegistry

DEFAULT_PER_COMPANY = 5
MAX_PER_COMPANY = 15

#: Boards are fetched in parallel: a list of eight companies is eight or more
#: round trips, each allowed 30s, and a Slack turn that takes a minute reads as
#: broken. Kept small — this is a handful of careers pages, not a crawl.
MAX_PARALLEL = 4


class Outcome(NamedTuple):
    """What one company's board gave back.

    ``postings`` is None when the board could not be reached — which the reply
    must distinguish from an empty list, since one means "nothing open" and the
    other means "I don't know".
    """

    company: str  # the user's own wording, since the reply is for them
    postings: list[JobPosting] | None


def register(reg: ToolRegistry) -> None:
    @reg.tool
    # No Args: entry for `config`: LangChain injects it and keeps it out of the
    # schema, so documenting it would describe a parameter the model never sees.
    def search_referral_jobs(  # noqa: D417
        keywords: str = "", limit_per_company: int = DEFAULT_PER_COMPANY,
        *, config: RunnableConfig,
    ) -> str:
        """Search every company on the user's referral list for open roles.

        This covers the whole list in one call — call it once, not once per
        company. The reply ends with which companies were searched, which boards
        were unreachable, and which ones have no board available: pass those
        gaps on to the user rather than implying the list is complete.

        Args:
            keywords: Optional phrase to narrow titles, e.g. "machine learning".
                If empty, each board's own AI/ML filter applies.
            limit_per_company: Most roles to return per company.
        """
        try:
            listed = referrals.list_for(referrals.owner_for(config))
        except ReferralStoreError as e:
            return f"Couldn't read the referral list: {e}"
        if not listed:
            return ("No referrals recorded yet, so there is nothing to search. "
                    "Add one by naming the company — for example, "
                    "\"I have a referral at Stripe\".")

        limit = clamp_int(limit_per_company, DEFAULT_PER_COMPANY, 1, MAX_PER_COMPANY)
        searched, uncovered = _scan(listed, keywords, limit)
        return _render(searched, uncovered)


def _scan(
    listed: list[Referral], keywords: str, limit: int
) -> tuple[list[Outcome], list[str]]:
    """Search every listed company that has a board, and name the ones that don't.

    Returns the outcomes in referral-list order — the user's own ordering is as
    good as any, and a stable one makes the reply comparable day to day.
    """
    targets: list[tuple[str, CompanySource]] = []
    uncovered: list[str] = []
    for referral in listed:
        source = resolve(referral.company)
        if source is None:
            uncovered.append(referral.company)
        else:
            targets.append((referral.company, source))

    if not targets:
        return [], uncovered

    def run(target: tuple[str, CompanySource]) -> Outcome:
        company, source = target
        return Outcome(company, source.search(keywords, limit))

    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(targets))) as pool:
        return list(pool.map(run, targets)), uncovered


def _render(searched: list[Outcome], uncovered: list[str]) -> str:
    """One merged list, plus the coverage the user needs to trust it."""
    reached = [outcome for outcome in searched if outcome.postings is not None]
    postings = [posting for outcome in reached for posting in outcome.postings or []]

    notes = []
    if reached:
        counts = ", ".join(
            f"{outcome.company} ({len(outcome.postings or [])})" for outcome in reached
        )
        notes.append(f"_Searched: {counts}._")
    if unreachable := [out.company for out in searched if out.postings is None]:
        notes.append(f"_Couldn't reach right now: {', '.join(unreachable)} — "
                     "try again later._")
    if uncovered:
        notes.append(f"_No careers board available for: {', '.join(uncovered)} — "
                     "you'll have to check those yourself._")
    footer = "\n".join(notes)

    if not postings:
        # Three different answers, and saying the wrong one is the whole problem:
        # "nothing open" where the truth is "I couldn't look" is what sends
        # someone away from a company they should have asked.
        if not searched:
            opening = "I have no careers board for any company on your list."
        elif reached:
            opening = "Nothing open right now at the companies I could check."
        else:
            opening = "Couldn't reach any of your referral companies' boards."
        return f"{opening}\n\n{footer}" if footer else opening

    total = len(searched) + len(uncovered)
    return render_postings(
        f"*Openings where you have a referral — {{count}} at {len(reached)} "
        f"of your {total} companies:*",
        postings,
        footer=footer,
    )
