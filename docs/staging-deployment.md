# Festival Bloomberg — Terminal Staging Deployment

## Purpose

The terminal is a separate read-only Cloudflare Worker/Container project. It is
not `fi-acquisition-runtime` and it does not run acquisition, provider calls,
Gold builds, or workspace/customer jobs.

The staging deployment is intentionally a **public-data-only demo** so that a
missing Cloudflare Access configuration or an optional provider credential does
not prevent the terminal from being viewable from another device.

## Deployment modes

| Mode | Worker | Access | Data boundary |
|---|---|---|---|
| `STAGING_PUBLIC_DEMO` | `festival-bloomberg-terminal-staging` | Public HTTPS | Compact published serving artifact; ephemeral in-memory buyer inputs only |
| `PRODUCTION_PRIVATE` | `festival-bloomberg-terminal-production` | Cloudflare Access or deployment secret path required | Same read-only serving contract; no customer/admin routes |

The staging badge is visible in the terminal as `STAGING / PUBLIC DATA DEMO`.
Production remains fail-closed unless its Access/path boundary is configured.

## Staging terminal contract

1. Start the product Container.
2. Read `serving/artist_security_terminal_v1/CURRENT.json` from the lake R2
   binding.
3. Fetch the compact `terminal.duckdb` for the object key in CURRENT.
4. Stream SHA-256 and verify it against CURRENT before opening the database.
5. Open DuckDB `READ_ONLY` and serve the terminal API/static assets.
6. Never copy the raw warehouse, rebuild Gold, query external providers from a
   browser request, expose acquisition/admin routes, or persist public-demo
   workspace data.

The Worker bootstrap endpoints are narrow: only CURRENT metadata and the
CURRENT terminal DuckDB can be fetched. Arbitrary R2 keys and bucket listings
are not exposed.

## Release workflow

The deployment workflow is:

```text
.github/workflows/terminal-deploy.yml
```

It runs from `terminal-runtime/`, assembles the image context in CI, and uses
the existing repository secrets:

```text
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
```

It does not require local Docker, local R2 credentials, Apify/Soundcharts
credentials, or Cloudflare Access service-token secrets for public staging.
Cloudflare Container image build and deployment occur on the GitHub-hosted
runner. The workflow captures the URL printed by `wrangler deploy`, waits for
Container cold start, then runs `scripts/uat_hosted_terminal.py` against that
actual HTTPS URL.

Run manually for a branch with:

```bash
gh workflow run terminal-deploy.yml --ref feat/artist-terminal-production-v1
```

The workflow must report a real `STAGING_URL`; it must not substitute a guessed
hostname. Hosted UAT asserts CURRENT metadata, health, status, Search, Artist
Security, factor tape, YouTube provenance, sentiment, Markets, Compare,
Underwrite, Portfolio, no page/console errors, and blocked admin/private paths.

## Public staging boundary

Allowed public product reads/actions:

- Home, Search, Artist Security, Markets, Compare, and Underwrite.
- Portfolio's empty/read surface backed by ephemeral in-memory state.
- User-entered assumptions used to calculate an underwrite.

Blocked at the Worker before the Container:

- `/admin`, `/batch`, `/dispatch`, `/governor`, `/ops`, acquisition/test paths.
- `/private`, `/customer`, `/workspace`.
- Durable shortlist/monitor/decision/backtest/vault/readiness/pace APIs.
- Portfolio lineup writes and underwrite save endpoints.

No provider credentials, raw usernames/user IDs, raw warehouse objects, private
customer outcomes, or acquisition controls are included in the terminal image.

## Current published serving artifact

The staging terminal always reads the R2 CURRENT pointer rather than a local
cache. At the time of this release checkpoint the known published artifact is:

- Generation: `terminal_v1_20260903T190113Z`
- Factor observations: `66,684` across `725` artists
- Sentiment observations/artists: `111`

A newer verified CURRENT generation takes precedence. Hosted UAT records the
actual generation and SHA returned by the deployed Worker.

## Optional provider follow-ups

These do not gate a public staging URL:

- Soundcharts: requires an authorized trial credential for licensed historical
  Spotify/social backfill; otherwise record `SOUNDCHARTS_ACCOUNT_REQUIRED`.
- Monid/Apify: requires a usable cloud rail/account for a bounded live bakeoff;
  do not claim a provider winner without actual runs.
- YouTube: refresh every verified identity first and report the observed ceiling
  rather than spending quota on ambiguous search results.
- Wikimedia and ListenBrainz remain eligible for canonical factor-tape
  unification without buying another provider.
