"""Festival Intelligence — the read-only information product layer.

The terminal (search / tape / entity pages / DATA / ASK) reads the canonical
warehouse through the read models in :mod:`festival_bloomberg.intelligence.readmodels`.
The activity tape in :mod:`festival_bloomberg.intelligence.tape` is the single
append-only ledger of "what changed". No module here invents empirical facts;
every displayed value traces to a source row.
"""
