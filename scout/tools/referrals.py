"""Managing the referral list: the tools behind the Referral Window agent.

``config: RunnableConfig`` is keyword-only and annotated exactly that way on
purpose. LangChain injects it and leaves it out of the schema the model sees, so
each tool knows whose list it is acting on without the model being told — or
being able to get it wrong. Widening the annotation to ``RunnableConfig | None``
breaks the detection and leaks the parameter into the schema.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from ..core import referrals
from ..core.referrals import ReferralStoreError
from .registry import ToolRegistry


def register(reg: ToolRegistry) -> None:
    @reg.tool
    # No Args: entry for `config`: LangChain injects it and keeps it out of the
    # schema, so documenting it would describe a parameter the model never sees.
    def add_referral(company: str, contact: str = "", note: str = "",  # noqa: D417
                     *, config: RunnableConfig) -> str:
        """Record a company where the user has a referral connection.

        Use this when the user says they know someone at a company, or asks to
        add one. Adding a company that is already on the list updates it instead
        of duplicating it, so it is safe to call again.

        Args:
            company: Name of the company, e.g. "Stripe".
            contact: Who the connection is, if the user names them.
            note: Anything else worth remembering, e.g. "former manager".
        """
        try:
            referral, created = referrals.add(
                referrals.owner_for(config), company, contact, note)
        except ReferralStoreError as e:
            return f"Couldn't save that referral: {e}"
        verb = "Added" if created else "Updated"
        return f"{verb} {referral.render()}"

    @reg.tool
    # As above: `config` is injected, so it is not in the documented Args.
    def remove_referral(company: str, *, config: RunnableConfig) -> str:  # noqa: D417
        """Remove a company from the user's referral list.

        Args:
            company: Name of the company to drop. Case does not matter.
        """
        try:
            owner = referrals.owner_for(config)
            removed = referrals.remove(owner, company)
        except ReferralStoreError as e:
            return f"Couldn't update the referral list: {e}"
        if removed is None:
            listed = referrals.list_for(owner)
            if not listed:
                return f"'{company}' isn't on the list — it's empty."
            names = ", ".join(referral.company for referral in listed)
            return f"'{company}' isn't on the list. Currently listed: {names}."
        return f"Removed *{removed.company}* from the referral list."

    @reg.tool
    def list_referrals(*, config: RunnableConfig) -> str:
        """List every company the user has a referral connection at.

        Use this before answering questions about who the user knows, and
        before removing something, so the reply reflects what is actually
        stored rather than what was said earlier in the conversation.
        """
        return render_list(config)


def render_list(config: RunnableConfig) -> str:
    """The shared rendering, so the read-only view matches this one exactly."""
    try:
        listed = referrals.list_for(referrals.owner_for(config))
    except ReferralStoreError as e:
        return f"Couldn't read the referral list: {e}"
    if not listed:
        return ("No referrals recorded yet. Add one by naming the company — "
                "for example, \"I have a referral at Stripe\".")
    lines = "\n".join(f"• {referral.render()}" for referral in listed)
    return f"*Referral connections — {len(listed)}:*\n{lines}"
