# Acquisition Provider Benchmarks

Before any provider earns a production role it must be benchmarked against
the same test corpus. This document records the framework and the current
integration status. Do not add a provider to the canonical set just because
it exists.

## Benchmark dimensions

| Dimension | What it measures |
|---|---|
| success % | fraction of requests with a genuine `SUCCESS`/`NO_RESULTS` outcome |
| field completeness | fraction of expected normalized fields populated (missing stays null, never zero) |
| latency | end-to-end ms per request |
| cost | USD per useful record; unknown costs stay unknown |
| memory | peak memory of the provider path |
| breakage rate | schema/API drift over time |
| duplicate rate | duplicate records after canonicalization |
| policy compatibility | source-policy gate result for research vs commercial |
| maintenance activity | project release cadence and issue responsiveness |

## Implemented providers (canonical, offline-tested)

| Provider | Status | Live configured? | Paid? | Test coverage |
|---|---|---|---|---|
| `http` | implemented | no (env-driven) | no (free endpoints, $0.00 budget) | router + provider tests |
| `monid` | implemented | no key by default (`NOT_CONFIGURED`) | no (`$0.00` default budget) | fixture contract tests (discover/inspect/run/poll, cost, rate limit) |
| `apify` | implemented | no token by default (`NOT_CONFIGURED`) | no | fixture contract tests (run + dataset items) |
| `youtube` | implemented | no key by default (`NOT_CONFIGURED`) | no (free tier, quota recorded) | fixture tests (search/videos/commentThreads, quota-exceeded) |
| `scrapling` | implemented, optional | dependency not installed by default (`NOT_CONFIGURED`) | no | `NOT_CONFIGURED` path + policy gate |

Live provider smoke tests are opt-in and gated behind
`ALLOW_PAID_PROVIDER_SMOKE=1`. CI never runs them.

## Benchmarked but not implemented

| Provider | Use case | License | Strengths | Weaknesses | Integration cost | Operational risk | Recommended role | Status |
|---|---|---|---|---|---|---|---|---|
| Crawlee Python | larger crawl queues | Apache-2.0 | state, retries, proxies | ops overhead | medium | medium | self-hosted bulk crawls | not integrated |
| Scrapy | stable bulk crawling | BSD-3-Clause | mature, structured | anti-bot weak | medium | medium | predictable sites | not integrated |
| Playwright | JS-only pages | Apache-2.0 | full browser | expensive, detectable | medium | medium | fallback only | not integrated |
| Crawl4AI | page → LLM documents | Apache-2.0 | clean extraction | security history | medium | high (isolate) | unstructured docs | not integrated |
| yt-dlp | metadata/subtitles | Unlicense | rich metadata | ToS risk per site | low | medium | policy-gated media | not integrated |

## How to propose a new provider

1. Implement it behind `AcquisitionProvider` (`acquisition/base.py`).
2. Add fixture-based contract tests (no network, no paid calls).
3. Add the benchmark rows above with measured numbers.
4. Gate it with a source-policy profile; `UNKNOWN` fails closed.
5. Only then add it to `default_providers()`.
