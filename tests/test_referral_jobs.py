"""Searching by referral: the company directory, and the fan-out over the list.

What is being defended here is a promise rather than a feature — that the reply
covers every company on the list, and names the ones it could not cover. So most
of these tests are about the footer, not the postings.
"""

from __future__ import annotations

import pytest

from scout.agents import get_spec
from scout.core import referrals, settings
from scout.tools import ToolRegistry, referral_jobs
from scout.tools.jobs import ashby, directory, greenhouse
from scout.tools.jobs.posting import JobPosting

from .conftest import registered_tools

ALICE = {"configurable": {"thread_id": "U_ALICE"}}
BOB = {"configurable": {"thread_id": "U_BOB"}}


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    """Point the store at a scratch file. Autouse so no test can touch the real one."""
    monkeypatch.setattr(settings, "REFERRALS_FILE", str(tmp_path / "referrals.json"))


class FakeBoard:
    """A stand-in careers board that records how it was searched."""

    def __init__(self, organization: str, titles: tuple[str, ...] = (),
                 down: bool = False) -> None:
        self.organization = organization
        self.titles = titles
        self.down = down
        self.calls: list[dict] = []

    def search(self, keywords: str = "", limit: int = 5) -> list[JobPosting] | None:
        self.calls.append({"keywords": keywords, "limit": limit})
        if self.down:
            return None
        return [
            JobPosting(title=title, organization=self.organization,
                       url=f"https://jobs/{title}")
            for title in self.titles[:limit]
        ]


def install(monkeypatch, boards: dict[str, FakeBoard]) -> dict[str, FakeBoard]:
    """Replace the company directory with fakes, keyed by normalised name."""
    monkeypatch.setattr(directory, "SOURCES", {
        key: directory.CompanySource(board.organization, board.search)
        for key, board in boards.items()
    })
    return boards


def search(config: dict = ALICE, **args) -> str:
    """Invoke the tool the way the graph does."""
    reg = ToolRegistry()
    referral_jobs.register(reg)
    tool = next(t for t in reg.tools if t.name == "search_referral_jobs")
    return tool.invoke(args, config=config)


# --- The directory --------------------------------------------------------


@pytest.mark.parametrize(("typed", "expected"), [
    ("Stripe", "Stripe"),
    ("  stripe ", "Stripe"),
    ("Stripe, Inc.", "Stripe"),
    ("The Stripe Corporation", "Stripe"),
    ("AWS", "Amazon"),
    ("Alphabet", "Google"),
    ("Lenovo", "Lenovo"),
    ("whoop", "WHOOP"),
    ("Northeastern", "Northeastern University"),
    ("BU", "Boston University"),
])
def test_a_company_resolves_however_it_was_typed(typed: str, expected: str) -> None:
    source = directory.resolve(typed)
    assert source is not None
    assert source.organization == expected


@pytest.mark.parametrize("typed", ["Acme", "some startup", "", "Stripes"])
def test_an_uncovered_company_resolves_to_nothing(typed: str) -> None:
    """None is an answer the caller reports, not an error it swallows."""
    assert directory.resolve(typed) is None


@pytest.mark.parametrize("platform", [greenhouse, ashby],
                         ids=["greenhouse", "ashby"])
def test_every_hosted_company_is_reachable_by_name(platform) -> None:
    """Adding a board to a platform's BOARDS must be all it takes."""
    resolved = {slug: directory.resolve(slug) for slug in platform.BOARDS}
    assert all(source is not None for source in resolved.values())
    assert {slug: source.organization for slug, source in resolved.items()
            if source is not None} == platform.BOARDS


# --- The fan-out ----------------------------------------------------------


def test_one_call_searches_every_company_on_the_list(monkeypatch) -> None:
    boards = install(monkeypatch, {
        "stripe": FakeBoard("Stripe", ("ML Engineer",)),
        "netflix": FakeBoard("Netflix", ("Applied Scientist", "Research Engineer")),
    })
    referrals.add("U_ALICE", "Stripe")
    referrals.add("U_ALICE", "Netflix")

    out = search(keywords="machine learning")

    assert "3 at 2 of your 2 companies" in out
    assert "*ML Engineer*" in out
    assert "*Applied Scientist*" in out
    assert "_Searched: Stripe (1), Netflix (2)._" in out
    # Every board saw the same search, and saw it once.
    for board in boards.values():
        assert board.calls == [{"keywords": "machine learning", "limit": 5}]


def test_an_unreachable_board_is_named_not_read_as_empty(monkeypatch) -> None:
    """The failure this agent exists to prevent: a silently short list."""
    install(monkeypatch, {
        "stripe": FakeBoard("Stripe", ("ML Engineer",)),
        "netflix": FakeBoard("Netflix", down=True),
    })
    referrals.add("U_ALICE", "Stripe")
    referrals.add("U_ALICE", "Netflix")

    out = search()

    assert "1 at 1 of your 2 companies" in out
    assert "_Couldn't reach right now: Netflix — try again later._" in out
    assert "Netflix (0)" not in out


def test_a_company_with_no_board_is_named_too(monkeypatch) -> None:
    install(monkeypatch, {"stripe": FakeBoard("Stripe", ("ML Engineer",))})
    referrals.add("U_ALICE", "Stripe")
    referrals.add("U_ALICE", "Acme Robotics")

    out = search()

    assert "1 at 1 of your 2 companies" in out
    assert "No careers board available for: Acme Robotics" in out


def test_nothing_open_still_reports_what_was_covered(monkeypatch) -> None:
    install(monkeypatch, {"stripe": FakeBoard("Stripe")})
    referrals.add("U_ALICE", "Stripe")
    referrals.add("U_ALICE", "Acme Robotics")

    out = search()

    assert "Nothing open right now at the companies I could check." in out
    assert "_Searched: Stripe (0)._" in out
    assert "No careers board available for: Acme Robotics" in out


def test_every_board_being_down_is_not_reported_as_nothing_open(monkeypatch) -> None:
    install(monkeypatch, {"stripe": FakeBoard("Stripe", down=True)})
    referrals.add("U_ALICE", "Stripe")

    out = search()

    assert "Couldn't reach any of your referral companies' boards." in out
    assert "Couldn't reach right now: Stripe" in out


def test_a_list_of_companies_with_no_boards_says_exactly_that(monkeypatch) -> None:
    """Not "nothing open" — the user has to know nothing was looked at."""
    install(monkeypatch, {"stripe": FakeBoard("Stripe", ("ML Engineer",))})
    referrals.add("U_ALICE", "Acme Robotics")

    out = search()

    assert "I have no careers board for any company on your list." in out
    assert "No careers board available for: Acme Robotics" in out


def test_an_empty_list_asks_for_a_company_rather_than_searching(monkeypatch) -> None:
    boards = install(monkeypatch, {"stripe": FakeBoard("Stripe", ("ML Engineer",))})

    out = search()

    assert "No referrals recorded yet" in out
    assert boards["stripe"].calls == []


def test_an_unreadable_store_answers_in_words(monkeypatch, tmp_path) -> None:
    """A tool must not raise into the loop over a corrupt file."""
    path = tmp_path / "referrals.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(settings, "REFERRALS_FILE", str(path))

    assert "Couldn't read the referral list" in search()


def test_each_user_searches_their_own_list(monkeypatch) -> None:
    install(monkeypatch, {
        "stripe": FakeBoard("Stripe", ("ML Engineer",)),
        "netflix": FakeBoard("Netflix", ("Applied Scientist",)),
    })
    referrals.add("U_ALICE", "Stripe")
    referrals.add("U_BOB", "Netflix")

    assert "ML Engineer" in search(ALICE)
    assert "Applied Scientist" in search(BOB)
    assert "Applied Scientist" not in search(ALICE)


def test_the_digest_searches_the_referrals_of_whoever_it_is_sent_to(monkeypatch) -> None:
    install(monkeypatch, {"stripe": FakeBoard("Stripe", ("ML Engineer",))})
    monkeypatch.setattr(settings, "DIGEST_SLACK_USER", "U_ALICE")
    referrals.add("U_ALICE", "Stripe")

    out = search({"configurable": {"thread_id": "digest:bigtech"}})
    assert "ML Engineer" in out


@pytest.mark.parametrize(("asked", "used"), [(2, 2), (0, 1), (999, 15)])
def test_the_limit_is_per_company_and_bounded(monkeypatch, asked: int, used: int) -> None:
    boards = install(monkeypatch, {"stripe": FakeBoard("Stripe")})
    referrals.add("U_ALICE", "Stripe")

    search(limit_per_company=asked)
    assert boards["stripe"].calls[0]["limit"] == used


def test_the_user_id_is_not_in_the_schema_the_model_sees() -> None:
    """As everywhere else: `config` is injected, never a parameter to fill."""
    reg = ToolRegistry()
    referral_jobs.register(reg)
    tool = next(t for t in reg.tools if t.name == "search_referral_jobs")

    assert set(tool.args) == {"keywords", "limit_per_company"}


# --- The agent ------------------------------------------------------------


def test_the_referral_window_can_only_search_within_the_list() -> None:
    """It writes the list, so it must not hold a tool that searches off it.

    The mirror of the rule that keeps `add_referral` away from the job agents:
    an agent that can search anywhere *and* write the list eventually records a
    company it merely found.
    """
    names = set(registered_tools(*get_spec("referral").tool_modules))

    assert "search_referral_jobs" in names
    assert not {name for name in names if name.startswith("search_")} - {
        "search_referral_jobs"
    }
