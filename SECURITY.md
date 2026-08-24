# Security

## Secret handling

- `.env` files are git-ignored (`.env`, `.env.*` except `.env.example`).
- Credentials are read from the environment only — never hard-coded, never
  committed, never logged. Provider clients (Monid, Apify, YouTube, Scrapling)
  take credentials from environment variables.
- CI runs a gitleaks secret scan over the working tree on every push/PR
  (`.github/workflows/ci.yml` → `security` job). Historical findings are
  tracked here rather than silently rewritten.

## Historical exposure — ROTATION REQUIRED

A root `.env` containing a **Hetzner vLLM API key** (`HETZNER_VLLM_API_KEY`)
was committed to this repository's history (introduced in the
festival-intelligence consolidation, removed by the canonical foundation
branch's first commit). Deleting the file did not remove the value from
history.

- **Credential type affected:** Hetzner vLLM API key.
- **Repository status:** `EXTERNAL_ROTATION_VERIFICATION_REQUIRED` — repository
  evidence cannot establish that the credential was revoked externally. Rotate
  any key that may have been live and verify the Hetzner account for unauthorized
  usage before treating this incident as closed.
- The value is intentionally not reproduced here and was not printed during
  the audit.

Historical secret-scan findings also include a previously committed
`gitleaks-report.json` (an old scan artifact, now git-ignored) and API-key
shaped strings inside test fixtures (false positives from test data).

## Operational rules

- No test or CI job makes a paid provider call.
- The default acquisition budget is exactly `$0.00`; paid provider calls
  require an explicit `ALLOW_PAID_PROVIDER_SMOKE=1` opt-in.
- Scraped web content is untrusted data and never becomes instructions for
  an agent (see the prompt-injection boundary tests).
