# Quantitative Research Specification

Compares official festival primary Face Value tiers with contemporaneous SeatGeek secondary listings. Primary data comes from the authorized official ticketing source; secondary data comes from the SeatGeek API or authorized export. The adapter normalizes payloads only and records immutable identifiers, URLs, retrieval time, provenance, SHA-256 content hash, and quality flags.

Schema: core.festival_ticket_tiers stores edition, tier identity/rank/type, access, face value, fees, total and currency. core.secondary_ticket_observations stores immutable listing snapshots and buyer-price components. metrics.ticket_price_spreads stores calculated outputs. PostgreSQL migration and DuckDB mirror are under schema/.

Amounts are integer minor units. absolute spread = secondary total buyer price - primary total price. percentage spread = absolute spread / primary total price. buyer margin = absolute spread / secondary total buyer price. FX uses the exact UTC retrieval date; missing rates use a flagged fallback and cannot produce an arbitrage candidate.

Timestamp tolerances are 24 hours historical, 6 hours active, and 1 hour real-time. Missing fees, missing prices/currencies, fallback FX, missing timestamps, and stale observations disqualify arbitrage. The conservative flag additionally requires a positive spread, 15% buyer margin, and a 10% safety buffer. This is a screening metric, not an execution or investment recommendation.

Acceptance criteria: both DDL files are idempotent; repository writes are parameterized and transactional; calculations are deterministic; tests cover currencies, FX, unknown fees, timestamps, missing fields, and changed-listing immutability; old snapshots are never updated in place. Caveats include API limits/licensing, dynamic inventory, taxes, delivery fees, quantity semantics, and disappearing listings.
