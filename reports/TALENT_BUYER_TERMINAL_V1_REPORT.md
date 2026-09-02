# TALENT_BUYER_TERMINAL_V1_REPORT

Status: **PASS**

This is an underwriting-research terminal. It does not issue booking, buy/sell, pass, go/no-go, attendance, gross, or guarantee recommendations.

## Delivered acceptance

- Cohort: 25 real artists; tiers: {'HOT_1000': 9, 'CORE_5000': 8, 'COVERAGE_25000': 8}; evidence profiles: {'medium': 7, 'deep': 17, 'sparse': 1}
- Capability results: 252 PASS / 0 FAIL
- Honest UNKNOWN panels accepted: 60

## Performance

| Operation | P50 ms | P95 ms | N |
|---|---:|---:|---:|
| artist_search | 14.969 | 16.913 | 25 |
| artist_security | 19.619 | 21.474 | 25 |
| market_panel | 23.149 | 26.486 | 25 |
| peer_panel | 24.945 | 27.067 | 25 |
| compare | 53.518 | 65.634 | 25 |

## Data and limits

- Product database: `/Users/scottthomasswitzer/CascadeProjects/festival-bloomberg/serving/artist_security_terminal_v1/CURRENT.duckdb`
- Compact artifact: 203436032 bytes; counts: {'artists': 25000, 'markets': 27322, 'peers': 135468, 'events': 153749, 'festivals': 14721, 'future_events': 6229}
- Cohort source: `artists`
- Ticket semantics: advertised structured ranges only; no resale, transaction, attendance, or sales inference.
- Missing evidence remains UNKNOWN and is never encoded as zero.
- Browser acceptance: PASS — SEARCH -> ARTIST SECURITY -> COMPARE; eight artist panels, strict advertised-range and no-current-ticket states, nine compare dimensions, and no-winner boundary verified with zero console errors
  - `reports/talent_buyer_terminal_v1/metallica_artist_security.png` — ba83b258d7fe3d35d760815d53dc6a156d21ccfb03dc1b5cb10b306e253eb74c
  - `reports/talent_buyer_terminal_v1/jamie_xx_artist_security.png` — d118b0110dfb776dbdf380c24ac9801dd474c394c432e838e27569ae8c2d52d0
  - `reports/talent_buyer_terminal_v1/unknown_mortal_orchestra_artist_security.png` — 99be2897f0520d6b43504a3b483ebb42600ad643e1f312540440ffcfe89990fc
  - `reports/talent_buyer_terminal_v1/unknown_mortal_orchestra_vs_jamie_xx_compare.png` — c226b0cce5840cb8345ad1cff17d8157d2a3d0188de3f278eeb3a605052df6d7
  - `reports/talent_buyer_terminal_v1/robert_glasper_ticket_evidence.png` — a63883e9ea9673960045c67c44c66059aaf969975d6e6084492c95b57b58b02e
