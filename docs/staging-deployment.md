# Festival Bloomberg — Terminal Staging Deployment

## Status (2026-09-03, RELEASE #62 + DATA SCALE V1)

**ACCESS_SETUP_REQUIRED** — no dedicated terminal staging project is deployed yet.

The cloud pipeline is fully operational on Cloudflare (`fi-acquisition-runtime`):
worker acquisition → R2 staging → cloud Gold containers → compact serving DuckDB →
`/terminal/bootstrap/current` (admin-gated). The terminal UI + query server
(`mvp_server` over the serving artifact) currently runs the atomic UAT harness
locally against the exact published generation.

The remaining step is a **separate** Cloudflare deployment
(`festival-bloomberg-terminal-staging`) that serves the terminal artifact
browser-reachable over HTTPS, protected by Cloudflare Access.

## Staging terminal contract

1. Start the product container (reads R2 `CURRENT.json` metadata).
2. Fetch the compact `terminal.duckdb` for the pinned generation.
3. Verify the SHA-256 against `CURRENT.json`.
4. Open READ_ONLY and serve the static terminal assets + API.
5. Never: copy the raw warehouse, rebuild Gold, query external providers
   interactively, or expose admin acquisition endpoints.

## What exists and what is required

| Item | State |
|---|---|
| Acquisition worker (`fi-acquisition-runtime`) | Deployed, admin-gated, healthy |
| Cloud Gold (factor tape, sentiment) | PUBLISHED in R2 (lake) |
| Serving artifact + CURRENT | PUBLISHED (`terminal_v1_20260903T190113Z`) |
| Atomic browser UAT (`scripts/uat_current_serving.py`) | PASS on exact generation; CI gate `browser-uat.yml` |
| Terminal staging worker (`festival-bloomberg-terminal-staging`) | **Not created — ACCESS_SETUP_REQUIRED** |
| Cloudflare Access (email/OTP + service token) | **Not provisioned** |
| GitHub secrets for staging deploy | **Not configured** |

## Steps to complete staging (human/CF-admin action)

1. Create `festival-bloomberg-terminal-staging` via
   `wrangler deploy --env staging` with its own Worker/Container config that
   binds the lake R2 bucket read-only and serves the artifact (model on
   `scripts/run_terminal.sh` bootstrap + SHA verify + READ_ONLY open).
2. Configure Cloudflare Access on the staging hostname: allow approved
   emails/OTP for humans; create a service token for CI.
3. Add GitHub secrets: `CF_TERMINAL_STAGING_API_TOKEN`,
   `CF_TERMINAL_STAGING_ACCOUNT_ID`, `CF_ACCESS_CLIENT_ID`,
   `CF_ACCESS_CLIENT_SECRET`.
4. Run `.github/workflows/terminal-staging.yml` (workflow_dispatch) — it
   currently exits 0 with `ACCESS_SETUP_REQUIRED` until the secrets exist.
5. Run the hosted browser UAT against the staging URL (same assertions as
   `scripts/uat_current_serving.py`, pointed at the staging hostname).

## Serving artifact (current)

- Generation: `terminal_v1_20260903T190113Z`
- SHA-256: `8c22ddb35f6a95bfa57856c32f00dd7e9dc854efaeb09c92a0d448cb0d0ed614`
- Rows: `artist_factor_observations` = 66,684 (725 artists),
  `artist_sentiment_observations` = 111 (111 artists)