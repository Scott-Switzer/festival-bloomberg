"""MusicBrainz bulk core-data ingestion (CC0 local reference graph).

The public MusicBrainz web service is intentionally ~1 request/sec; bulk
entity resolution belongs on the downloadable CC0 database dumps, not the web
API. This package downloads/parses those dumps and turns them into provider
observations with source/checksum lineage — it never overwrites the canonical
entity master directly.
"""
