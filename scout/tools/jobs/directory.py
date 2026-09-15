"""The other index into the job sources: by company rather than by platform.

The source modules are organised the way an implementation has to be — one per
platform, because a Workday tenant and an RSS feed share nothing. The referral
list is organised the way the user thinks: a list of *companies* where they know
someone. Answering "what is open where I could ask for a referral" means going
the other way, company to board, so that mapping lives here.

It is a table rather than a prompt on purpose. A model asked to route "Stripe"
to ``search_greenhouse_jobs`` gets it right most of the time, and most of the
time is exactly what the Referral Window cannot promise: the value of that agent
is that its answer is complete, with the companies it *couldn't* check named
rather than silently missing. Routing in code is what makes naming them possible.

Adding coverage is adding a line — or, for a Greenhouse company, adding one to
``greenhouse.BOARDS``, which lands here on its own.
"""

from __future__ import annotations

import re
from functools import partial
from typing import NamedTuple

from . import (
    Searcher,
    amazon,
    ashby,
    boston_university,
    google,
    greenhouse,
    lenovo,
    netflix,
    northeastern,
)

#: How far back the by-company search looks at sources that take a date window.
#: The Amazon tool defaults to the last 24h, which is right for "what is new
#: today" and wrong here: a referral is worth spending on anything still open.
WINDOW_DAYS = 30


class CompanySource(NamedTuple):
    """One company's careers board: what to call it, and how to search it."""

    organization: str
    search: Searcher


#: Normalised company name -> its board. The multi-company platforms contribute
#: everyone they host, so a line in ``greenhouse.BOARDS`` or ``ashby.BOARDS`` is
#: also a line here.
SOURCES: dict[str, CompanySource] = {
    "amazon": CompanySource("Amazon", partial(amazon.search, days=WINDOW_DAYS)),
    "google": CompanySource("Google", google.search),
    "netflix": CompanySource("Netflix", netflix.search),
    "lenovo": CompanySource("Lenovo", lenovo.search),
    "northeastern university": CompanySource(
        northeastern.ORGANIZATION, northeastern.search
    ),
    "boston university": CompanySource(
        boston_university.ORGANIZATION, boston_university.search
    ),
    **{
        slug: CompanySource(name, partial(greenhouse.search, slug))
        for slug, name in greenhouse.BOARDS.items()
    },
    **{
        slug: CompanySource(name, partial(ashby.search, slug))
        for slug, name in ashby.BOARDS.items()
    },
}

#: What people write, mapped onto the keys above. Worth keeping short: a wrong
#: alias silently searches the wrong company, which is worse than admitting the
#: name isn't covered.
ALIASES = {
    "aws": "amazon",
    "amazon web services": "amazon",
    "alphabet": "google",
    "google deepmind": "google",
    "deepmind": "google",
    "northeastern": "northeastern university",
    "neu": "northeastern university",
    "bu": "boston university",
}

#: Dropped before matching — nobody's board is registered under "Inc".
_NOISE = {"inc", "llc", "ltd", "corp", "corporation", "co", "the"}


def normalise(company: str) -> str:
    """The lookup key for a company name as someone would actually type it.

    "Stripe, Inc." and "  STRIPE " are both the Stripe on the referral list.
    """
    words = re.sub(r"[^a-z0-9 ]+", " ", company.casefold()).split()
    return " ".join(word for word in words if word not in _NOISE)


def resolve(company: str) -> CompanySource | None:
    """The board to search for ``company``, or None if Scout has none for it.

    None is a real answer, not a failure: the caller reports it as a gap in
    coverage rather than dropping the company from the reply.
    """
    key = normalise(company)
    return SOURCES.get(ALIASES.get(key, key))
