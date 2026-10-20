# Local MVP dashboard

Start from the project root:

```bash
uv run python -m github_intelligence.dashboard
```

Or double-click `start-dashboard.cmd` on Windows. The screen is served only at `http://127.0.0.1:8765`; keep its process running while using it. It is a local trusted-operator tool, not a hosted or multi-user web application. Do not expose it on a network or reverse proxy.

`config/dashboard.local.json` contains the provisioned client/project UUIDs plus the local registry aliases `client` and `project`. It contains no credential. The server uses the existing DPAPI-backed database configuration. No new database migration or permissions are required.

## Included

- Provisioned repositories for the configured project, excluding removed entries.
- Last execution status, start/end, and collection window.
- The saved report associated with that execution, opened as text; repository content is never inserted as HTML.
- Explicit manual refresh with UTC interval and confirmation before writing.

Opening the screen, reloading data, and opening a report are read-only. Only the explicit update action invokes the existing authenticated CLI import, under the configured project and the selected repository identity. It uses no shell interpolation, applies the standard CLI health gate, and does not overwrite history. Concurrent refreshes in this process are rejected; database constraints remain the final concurrency gate.

## Boundaries

No scheduling, AI, user signup/login, administration, membership changes, extra repository provisioning or deployments are included. The browser never receives the database password or GitHub key. Mutation requires the exact loopback Host, matching Origin and a per-process request token; reports are scoped server-side. This is not end-user authentication.

Refresh has a five-minute limit. On timeout, inspect the stored execution before retrying: a terminated process can leave an unfinished run requiring operator attention. No automatic retries or silent recovery writes occur.

## Verified delivery

- Full suite: 527 tests passed. Ruff passed; mypy passed for 19 source files.
- Dashboard-focused tests: 14 passed, including scoped queries on disposable native PostgreSQL and an HTTP update that commits a synthetic report and reads it back through the dashboard.
- Live server `http://127.0.0.1:8765` returned the existing `Fabrycio13/project-mds` repository and report `627498ad-cea9-4d9f-8848-664cb4bbebf8` from Supabase.
- Browser DOM smoke: populated repository, report opened/closed successfully, report content inserted as text, update control enabled. At 390px viewport the document remained 390px wide with the report open.
- Opened in the desktop preview. No new remote collection was triggered by this smoke; scoped run count and report count remained 1 each.
- Review was scoped/manual because this workspace has no Git history; no Git repository was initialized.
- This validates local synthetic update behavior and live remote reads. A new remote update through the button remains an explicit operator action, not a hidden verification write.
