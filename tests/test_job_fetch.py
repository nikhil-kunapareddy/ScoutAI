"""The shared HTTP layer under every job source.

What is being pinned here is one distinction: ``None`` means the source was
never reached, an empty list means it answered with nothing. The Referral
Window's promise — that it names the companies it could not check — rests
entirely on sources getting that right, so it is tested once, here, rather than
eight times over.
"""

from __future__ import annotations

import pytest

from scout.tools.jobs import fetch

from .conftest import FakeRequests, FakeResponse

URL = "https://example.test/jobs"


@pytest.fixture
def stub(monkeypatch):
    """Install a fake ``requests`` and hand it back for assertions."""
    def install(fake: FakeRequests) -> FakeRequests:
        monkeypatch.setattr(fetch, "requests", fake)
        return fake

    return install


# --- Reading rows ---------------------------------------------------------


def test_rows_come_back_from_under_their_key(stub) -> None:
    stub(FakeRequests(FakeResponse({"jobs": [{"title": "ML Engineer"}]})))

    assert fetch.get_rows(URL, "jobs") == [{"title": "ML Engineer"}]


def test_an_unreachable_source_is_none_not_empty(stub) -> None:
    stub(FakeRequests(error=OSError("no route to host")))

    assert fetch.get_rows(URL, "jobs") is None
    assert fetch.get_text(URL) is None
    assert fetch.post_rows(URL, "jobs", {}) is None


def test_a_body_that_will_not_decode_reads_as_unreachable(stub) -> None:
    """A board answering with an HTML error page has not told us it is empty."""
    class Undecodable(FakeResponse):
        def json(self) -> dict:
            raise ValueError("Expecting value: line 1 column 1")

    stub(FakeRequests(Undecodable()))

    assert fetch.get_rows(URL, "jobs") is None


def test_a_source_with_nothing_open_is_an_empty_list(stub) -> None:
    stub(FakeRequests(FakeResponse({"jobs": []})))

    assert fetch.get_rows(URL, "jobs") == []


# --- Reading a body that isn't shaped the way it should be ----------------


@pytest.mark.parametrize("payload", [
    ["not", "a", "document"],   # a bare list where an object was promised
    "an error string",
    None,
    {"jobs": "not a list"},     # the key is there, the rows are not
    {"other": [{"title": "x"}]},
])
def test_a_body_of_the_wrong_shape_yields_no_rows(payload: object) -> None:
    """Boards under load answer with an error document often enough to expect
    it; every source's parser is then working on rows only."""
    assert fetch.json_rows(payload, "jobs") == []


def test_rows_that_are_not_objects_are_dropped() -> None:
    assert fetch.json_rows({"jobs": [{"title": "ok"}, "junk", None]}, "jobs") == [
        {"title": "ok"}
    ]


# --- What gets sent -------------------------------------------------------


def test_every_request_carries_the_user_agent_and_a_timeout(stub) -> None:
    """Some boards answer 403 to a bare client, and none of them may hang a turn."""
    fake = stub(FakeRequests(FakeResponse({"jobs": []})))

    fetch.get_rows(URL, "jobs", {"q": "ml"})
    fetch.get_text(URL)
    fetch.post_rows(URL, "jobs", {"searchText": "ml"})

    for call in fake.calls:
        assert "Mozilla" in call["headers"]["User-Agent"]
        assert call["timeout"] > 0
    assert fake.calls[0]["params"] == {"q": "ml"}
    assert fake.calls[2]["json"] == {"searchText": "ml"}


def test_json_endpoints_ask_for_json(stub) -> None:
    fake = stub(FakeRequests(FakeResponse({"jobs": []})))

    fetch.get_rows(URL, "jobs")
    fetch.get_text(URL)

    assert fake.calls[0]["headers"]["Accept"] == "application/json"
    assert "Accept" not in fake.calls[1]["headers"]  # a feed or a page, not JSON


# --- Whole bodies, for the sources whose rows are nested ------------------


class Undecodable(FakeResponse):
    """A response whose body will not parse — a board answering with an error page."""

    def json(self) -> dict:
        raise ValueError("not json")


def test_a_whole_body_comes_back_decoded(stub) -> None:
    """Three sources bury their rows, so they navigate the body themselves."""
    fake = stub(FakeRequests(FakeResponse({"data": {"positions": [{"name": "MLE"}]}})))
    assert fetch.get_json(URL) == {"data": {"positions": [{"name": "MLE"}]}}
    assert fake.calls[0]["url"] == URL


def test_a_posted_body_comes_back_decoded(stub) -> None:
    stub(FakeRequests(FakeResponse({"refineSearch": {"data": {"jobs": []}}})))
    assert fetch.post_json(URL, {"q": "ml"}) == {"refineSearch": {"data": {"jobs": []}}}


@pytest.mark.parametrize("post", [False, True], ids=["get", "post"])
def test_an_unreachable_body_is_none(stub, post: bool) -> None:
    stub(FakeRequests(error=OSError("down")))
    body = fetch.post_json(URL, {}) if post else fetch.get_json(URL)
    assert body is None


@pytest.mark.parametrize("post", [False, True], ids=["get", "post"])
def test_a_body_that_will_not_decode_is_none(stub, post: bool) -> None:
    """Microsoft answers a rate-limited request with plain text, not JSON."""
    stub(FakeRequests(Undecodable()))
    body = fetch.post_json(URL, {}) if post else fetch.get_json(URL)
    assert body is None
