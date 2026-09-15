"""The referral list: the store, the tools over it, and who may write to it."""

from __future__ import annotations

import json

import pytest

from scout.agents import AGENTS, get_spec
from scout.core import referrals, settings
from scout.core.referrals import Referral, ReferralStoreError
from scout.digest import job_agents
from scout.tools import ToolRegistry, referrals_read
from scout.tools import referrals as referral_tools

ALICE = {"configurable": {"thread_id": "U_ALICE"}}
BOB = {"configurable": {"thread_id": "U_BOB"}}


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    """Point the store at a scratch file. Autouse so no test can touch the real one."""
    path = tmp_path / "referrals.json"
    monkeypatch.setattr(settings, "REFERRALS_FILE", str(path))
    return path


def tools(module) -> dict:
    reg = ToolRegistry()
    module.register(reg)
    return {t.name: t for t in reg.tools}


# --- The store ------------------------------------------------------------


def test_add_then_list_round_trips() -> None:
    referrals.add("u", "Stripe", contact="ex-teammate", note="team A")
    (saved,) = referrals.list_for("u")

    assert saved.company == "Stripe"
    assert saved.contact == "ex-teammate"
    assert saved.note == "team A"
    assert saved.added  # stamped with the date it was recorded


def test_missing_file_reads_as_empty(store) -> None:
    assert not store.exists()
    assert referrals.list_for("u") == []


def test_adding_a_listed_company_updates_instead_of_duplicating() -> None:
    referrals.add("u", "Stripe", contact="ex-teammate")
    referral, created = referrals.add("u", "  stripe ", note="team A")

    assert created is False
    assert len(referrals.list_for("u")) == 1
    # The note lands without blanking the contact this turn never mentioned.
    assert referral.contact == "ex-teammate"
    assert referral.note == "team A"


def test_each_owner_has_their_own_list() -> None:
    referrals.add("alice", "Stripe")
    referrals.add("bob", "Databricks")

    assert [r.company for r in referrals.list_for("alice")] == ["Stripe"]
    assert [r.company for r in referrals.list_for("bob")] == ["Databricks"]


def test_remove_is_case_insensitive_and_returns_what_it_dropped() -> None:
    referrals.add("u", "Stripe")
    referrals.add("u", "Databricks")

    dropped = referrals.remove("u", "STRIPE")

    assert dropped is not None
    assert dropped.company == "Stripe"
    assert [r.company for r in referrals.list_for("u")] == ["Databricks"]


def test_removing_something_unlisted_changes_nothing() -> None:
    referrals.add("u", "Stripe")

    assert referrals.remove("u", "Netflix") is None
    assert len(referrals.list_for("u")) == 1


def test_order_added_is_preserved() -> None:
    for company in ("Stripe", "Databricks", "Airbnb"):
        referrals.add("u", company)

    assert [r.company for r in referrals.list_for("u")] == ["Stripe", "Databricks", "Airbnb"]


def test_a_corrupt_store_refuses_rather_than_starting_over(store) -> None:
    """Silently resetting would throw away a list the user typed by hand."""
    store.write_text("{not json")

    with pytest.raises(ReferralStoreError):
        referrals.list_for("u")


def test_writes_are_atomic(store) -> None:
    """Rename-into-place, so a crash mid-write cannot truncate the list."""
    referrals.add("u", "Stripe")

    assert json.loads(store.read_text())["u"][0]["company"] == "Stripe"
    assert list(store.parent.glob("*.tmp")) == []


def test_emptied_owners_are_dropped_from_the_file(store) -> None:
    referrals.add("u", "Stripe")
    referrals.remove("u", "Stripe")

    assert json.loads(store.read_text()) == {}


# --- Whose list a turn acts on --------------------------------------------


def test_owner_is_the_slack_user_id() -> None:
    assert referrals.owner_for(ALICE) == "U_ALICE"


def test_digest_threads_resolve_to_the_digest_recipient(monkeypatch) -> None:
    """The digest runs on digest:<agent>, not a person.

    Without this the daily report would look up the referrals of a thread id
    that is not a user, find none, and quietly stop ranking by referral.
    """
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U_ALICE")
    config = {"configurable": {"thread_id": "digest:bigtech"}}

    assert referrals.owner_for(config) == "U_ALICE"


def test_digest_thread_falls_back_to_itself_when_no_recipient_is_set(monkeypatch) -> None:
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "")
    config = {"configurable": {"thread_id": "digest:bigtech"}}

    assert referrals.owner_for(config) == "digest:bigtech"


def test_a_missing_config_does_not_explode() -> None:
    assert referrals.owner_for(None) == ""
    assert referrals.owner_for({}) == ""


# --- The tools ------------------------------------------------------------


def test_tools_act_on_the_calling_user_only() -> None:
    t = tools(referral_tools)
    t["add_referral"].invoke({"company": "Stripe"}, config=ALICE)

    assert "Stripe" in t["list_referrals"].invoke({}, config=ALICE)
    assert "No referrals recorded yet" in t["list_referrals"].invoke({}, config=BOB)


def test_the_user_id_is_not_in_the_schema_the_model_sees() -> None:
    """The injected config is what keeps the model from naming a user at all."""
    t = tools(referral_tools)

    assert set(t["add_referral"].args) == {"company", "contact", "note"}
    assert t["list_referrals"].args == {}


def test_removing_something_unlisted_says_what_is_listed() -> None:
    t = tools(referral_tools)
    t["add_referral"].invoke({"company": "Stripe"}, config=ALICE)

    reply = t["remove_referral"].invoke({"company": "Netflix"}, config=ALICE)

    assert "isn't on the list" in reply
    assert "Stripe" in reply  # so the model can correct itself in one hop


def test_a_broken_store_is_reported_not_raised(store) -> None:
    """Tools answer with a sentence; raising would abandon the tool call."""
    store.write_text("{not json")
    t = tools(referral_tools)

    for name, args in [("list_referrals", {}), ("add_referral", {"company": "X"}),
                       ("remove_referral", {"company": "X"})]:
        reply = t[name].invoke(args, config=ALICE)
        assert "Couldn't" in reply, name


# --- Who may write --------------------------------------------------------


def test_job_agents_get_the_read_only_view() -> None:
    """A searching agent must not decide to record a referral on its own."""
    assert set(tools(referrals_read)) == {"list_referrals"}

    for key in ("bigtech", "edu"):
        names = set()
        reg = ToolRegistry()
        for module in get_spec(key).tool_modules:
            module.register(reg)
            names = set(reg.names())
        assert "list_referrals" in names, key
        assert "add_referral" not in names, key
        assert "remove_referral" not in names, key


def test_the_referral_window_owns_the_writes() -> None:
    reg = ToolRegistry()
    for module in get_spec("referral").tool_modules:
        module.register(reg)

    assert {"add_referral", "remove_referral", "list_referrals"} <= set(reg.names())


def test_both_views_render_a_list_identically() -> None:
    referrals.add("U_ALICE", "Stripe", contact="ex-teammate")

    assert (tools(referral_tools)["list_referrals"].invoke({}, config=ALICE)
            == tools(referrals_read)["list_referrals"].invoke({}, config=ALICE))


# --- Where it does and does not belong ------------------------------------


def test_the_referral_window_is_not_a_digest_agent() -> None:
    """tailor_with_resume is what puts an agent in the digest; a CRUD window
    has nothing to report daily."""
    assert AGENTS["referral"].tailor_with_resume is False
    assert "referral" not in {spec.key for spec in job_agents()}


def test_referral_renders_without_optional_fields() -> None:
    assert Referral(company="Stripe").render() == "*Stripe*"
