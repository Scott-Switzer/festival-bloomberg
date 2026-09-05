# Factor history production rollout

Rollout in progress. No production readiness claim yet.

V2 PR #65 merged normally at `6fc00f4653e5a9b849bae7c67c904c4f564f0a12`.
Exact-head CI and post-merge CI passed. Fresh local baseline: 1147 Python tests,
76 Node tests, TypeScript checks. Browser CI skipped credential-dependent UAT;
it is not hosted acceptance evidence.

## Publication controls

The Factor publisher remains `cloud/factor_history.py`, using conditional PUT
with the version read alongside the parent pointer. Shared S3 request dispatch
now rejects unconditioned writes, copy, delete (including bulk), and multipart
operations against `gold/artist_factor_tape/CURRENT.json` in every bucket.

All four repository S3 factories attach this guard:
- `cloud/r2_lake.py`: batch publisher and generic helpers.
- `lake/r2.py`: lake scripts and bulk processors.
- `evidence_rails/r2_object_store.py`: generic evidence store.
- `scripts/stream_url_to_r2.py`: arbitrary destination uploader.

`write_current_pointer`, `put_bytes`, and uploads cannot bypass this policy.
The validation adapter writes only below its validation namespace. Test fakes
are test-only. An AST regression checks repository S3 factory coverage, and
botocore dispatch tests exercise both rejected operations and accepted CAS.
External credentials remain a separate authority boundary; this guard is a
repository runtime control, not an R2 IAM policy.

## Required rollout corrections

Batch images contain build_identity.json assembled from GitHub's exact SHA and
run identity. Runtime manifests/pointers expose the full commit and build ID.
Factor results also report normalized rows, overlap deduplication, ledger skips,
and the parent ETag. Existing bounds and immutable plan contracts are retained.

The existing serving builder verifies Factor Gold's SHA before folding, records
the consumed Gold generation/hash/key, and uses an optimistic serving CURRENT
write against the version observed before building.

Hosted diagnosis: static assets/bootstrap respond but API requests stall. The
container serves plain HTTP on its private port; internal health and product
forwarding now explicitly use HTTP, while the external surface remains HTTPS.
This fix requires deployed acceptance before being considered effective.
