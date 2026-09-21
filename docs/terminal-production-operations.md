# Production Terminal Operations

This runbook is non-secret. Do not add the production access path, Cloudflare
tokens, GitHub secrets, or provider credentials.

## Runtime

- Worker: `festival-bloomberg-terminal-production`
- Origin: `https://festival-bloomberg-terminal-production.scswitzer.workers.dev`
- Deploy workflow: `.github/workflows/terminal-production.yml`
- Mode: `PRODUCTION_PRIVATE`
- Serving pointer: `serving/artist_security_terminal_v1/CURRENT.json`
- Hosted promotion state: `control/terminal/hosted_promotion/state.json`

Production is reached through a deployment secret named
`TERMINAL_ACCESS_PATH`. Naked root, `/health`, and `/api/status` requests are
expected to return `401` with `TERMINAL_AUTH_REQUIRED`. Authorized product-path
requests must return the real terminal shell and product APIs.

## Deploy

Run the existing review-gated production workflow from the intended git SHA:

```bash
gh workflow run terminal-production.yml --ref main
```

If the GitHub `production` environment asks for approval, approve that run in
GitHub Actions. Do not create a parallel deploy path.

The production workflow:

- deploys `terminal-runtime/` with `--containers-rollout=immediate`;
- proves unauthorized `/`, `/health`, and `/api/status` fail closed;
- proves authorized `/health` and `/api/status` return HTTP 200;
- runs hosted browser UAT through `scripts/uat_hosted_terminal.py`;
- uploads evidence as `production-hosted-terminal-uat`.

## Health And Generation

Resolve Serving CURRENT without printing secrets:

```bash
npx wrangler r2 object get \
  festival-intelligence-lake/serving/artist_security_terminal_v1/CURRENT.json \
  --file /tmp/festival-terminal-current.json
node -e 'const c=require("/tmp/festival-terminal-current.json"); console.log(JSON.stringify({generation:c.generation, sha256:c.sha256, published_at:c.published_at || c.publication_time || null, source_generations:c.source_generations || null}, null, 2))'
```

Authorized production `/health` must report the same `generation` and `sha256`
as CURRENT. A mismatch means the hosted container is stale or failed to
bootstrap the current serving artifact.

## Hosted Promotion

Production and staging both run the hosted-promotion cron every five minutes.
The cron watches Serving CURRENT and restarts the terminal container when the
generation advances, then verifies freshness from `/health` before recording
`HOSTED_FRESH`.

Common states:

- `IDLE`: no serving generation has been promoted yet.
- `IN_FLIGHT`: restart was requested; the next tick should verify.
- `HOSTED_FRESH`: hosted `/health` matches Serving CURRENT.
- `FAILED`: repeated verification failures; inspect container startup,
  bootstrap access, R2 binding, and the promotion state object.

## Rollback

Prefer reverting or redeploying a known-good git SHA through
`.github/workflows/terminal-production.yml`. Do not edit R2 CURRENT merely to
make hosted metadata match unless the data-publication process itself is being
rolled back under its own acceptance rules.

## Boundaries

The production terminal runtime is read-only. It serves static assets, proxies
product `/health` and `/api/*` to the terminal container, and bootstraps only
the current read-only serving artifact. It must not expose acquisition admin
routes, provider credentials, arbitrary R2 keys, batch triggers, Gold
materialization, or canonical warehouse mutation.

Current known data limitations remain product limitations, not runtime
failures: missing evidence is `UNKNOWN`, fan attention is not ticket demand,
and the baseline research verdict remains `COMPS_SIGNAL_ONLY`.
