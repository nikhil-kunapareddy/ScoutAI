"""Job-search tools: one module per source, over a set of shared parts.

Each source gets its own module because the platforms differ too much to share
an implementation: JSON search APIs (Amazon, Netflix, Microsoft, Uber, Oracle,
Cisco), scraped HTML (Google, Bloomberg, Apple), RSS (Lenovo, Boston
University), and the platforms that host many companies — Greenhouse, Ashby and
SmartRecruiters behind one ``HostedBoard``, and Workday behind one
``WorkdayTenant`` — which are one implementation over many companies, because
there the platform *is* the source. (Northeastern predates the Workday shape and
still has its own module; it is a different site with different fields.)

What every source shares is split by the job it does, and imported from there
rather than re-exported here, so each name has one home:

===================  =======================================================
``fetch``            talking to a source, and what "unreachable" means
``feeds``            reading an RSS feed
``posting``          the ``JobPosting`` record, merging queries, rendering
``relevance``        which titles count, and what to search for by default
``hosted_board``     the Greenhouse/Ashby/SmartRecruiters shape, filled in thrice
``workday``          the Workday CXS shape, filled in per tenant
``directory``        the other index: company name -> the board to search
===================  =======================================================
"""

from __future__ import annotations
