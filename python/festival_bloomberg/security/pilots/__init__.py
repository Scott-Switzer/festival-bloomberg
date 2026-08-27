"""Open-source pilot evaluations for OPEN_ARTIST_MARKET_DATA_V1.

Each pilot is an isolated, disposable evaluation (no production wiring):
- voyager_pilot      — KNN nearest-neighbor retrieval over factor vectors
- feast_pilot        — PIT historical retrieval equivalence test
- perspective_pilot  — artist-monitor table prototype (data export + semantics)
- memray_pilot       — dev-only memory profiler wrapper

Verdicts feed docs/open_source_adoption_registry.yaml.
"""
