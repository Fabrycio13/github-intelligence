# Pilot provisioning — applied and verified

## Authorized scope

Business seed `scripts/provision_pilot.sql`, explicitly approved by the user. Applied through the user's existing Chrome session, Supabase SQL Editor for project `cpkgvpuivjzplerzlpxz`. No schema migration, permissions change, user creation, membership grant or PR import was included.

SQL Editor query handle: `728c7adf-10ff-495a-9ab8-bce097823ec3`.

## Read-back verification

A separate PostgreSQL connection using the saved worker configuration and `project_transaction` confirmed:

- Client `personal`: `c3587c2e-e471-4a80-962d-b4ce5c13bd4b`, active.
- Project `pilot`: `2ef6f640-57ca-459d-b8e8-882f80697720`, active.
- GitHub App `5380709`, installation `173020772`, active.
- Repository `Fabrycio13/project-mds`, GitHub ID `1226750215`, active.
- Repository UUID: `69f9280b-2109-4a6b-b4d7-ad318cfcec2e`.
- Scoped sync runs: 0; scoped PR snapshots: 0.

The CLI plan without `--apply` completed with exit 0 for the actual UUIDs and interval `[2026-10-01T00:00:00Z, 2026-10-19T00:00:00Z)`.

## Remaining gate

First remote import requires separate approval of repository and interval. It was not executed. No user membership was fabricated; this remains a trusted-worker CLI pilot, not end-user authorization.

## Local verification

Four provisioning tests passed in native disposable PostgreSQL, including re-execution, preservation of existing row values, exact one-repository scope, and rollback after a cross-client installation conflict. Ruff passed; mypy passed for 17 source files. Independent static review reported no SQL blockers; additional preservation/rollback tests were added after review.
