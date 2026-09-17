"""Job-search tools: one module per source, over a set of shared parts.

Each source gets its own module because the platforms differ too much to share
an implementation: JSON search APIs (Amazon, Netflix), scraped HTML (Google),
RSS (Lenovo, Boston University), Workday (Northeastern), and the hosted board
platforms (Greenhouse, Ashby) — which are one implementation over many
companies, because there the platform *is* the source.

What every source shares is split by the job it does, and imported from there
rather than re-exported here, so each name has one home:

===================  =======================================================
``fetch``            talking to a source, and what "unreachable" means
``feeds``            reading an RSS feed
``posting``          the ``JobPosting`` record, merging queries, rendering
``relevance``        which titles count, and what to search for by default
``hosted_board``     the Greenhouse/Ashby shape, filled in twice
``directory``        the other index: company name -> the board to search
===================  =======================================================
"""

from __future__ import annotations
