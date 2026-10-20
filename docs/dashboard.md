# Local MVP dashboard

Start from the project root:

```bash
uv run python -m github_intelligence.dashboard
```

Or double-click `start-dashboard.cmd` on Windows. The screen is served only at `http://127.0.0.1:8765`; keep its process running while using it. It is a local trusted-operator tool, not a hosted or multi-user web application. Do not expose it on a network or reverse proxy.

`config/dashboard.local.json` contains the provisioned client/project UUIDs plus the local registry aliases `client` and `project`. It contains no credential. The server uses the existing DPAPI-backed database configuration. No new database migration or permissions are required.

## Included

- Sidebar navigation: `#/dashboard`, `#/repositorios`, and `#/analises`.
- Dashboard with active repository connections, saved/partial report counts, daily report generation over 30 UTC days, repository distribution, and recent reports. Global report counts use distinct reports, not source rows or the current history page.
- Provisioned repositories for the configured project, excluding removed entries from the operational list. Open a repository to access last execution status, collection window, saved report, and explicit manual refresh with UTC interval and confirmation before writing.
- Styled modal for the existing GitHub App inspection/authorization/binding flow. Opening or cancelling it does not bind or collect. After confirmation succeeds, it opens the repository details without starting a collection.
- Paginated report history, independently of the latest collection, with repository filtering. Historical repository options include removed repositories with saved reports; removal does not erase those reports.
- Direct report links at `#/analises/<report-uuid>`, with reload/back/forward and return to the originating list/filter. Content is opened as text, never inserted as raw HTML.
- Light/dark theme adaptation and compact navigation on narrow screens. The daily chart includes a textual data table.

An “analysis” is the existing deterministic evidence report, not a new AI/code audit. A complete report is complete only within its declared collection coverage; it does not prove deploy or functional validation. Event intervals and observation times are distinct.

Opening an empty project suggests adding a repository; connected repositories without reports suggest an explicit first collection. Read failures show an error, not fabricated zero totals. Repository connection counts describe database/installation status, not a fresh access check against GitHub or reconciliation with local CLI selection.

Opening the screen, reloading data, and opening a report are read-only. Only the explicit update action invokes the existing authenticated CLI import, under the configured project and the selected repository identity. It uses no shell interpolation, applies the standard CLI health gate, and does not overwrite history. Concurrent refreshes in this process are rejected; database constraints remain the final concurrency gate.

## Initial loading and page-lifetime cache

- On opening the app, an initial progress screen waits for repositories, Dashboard and the first history page in parallel, plus a directly selected history filter/page or report detail when applicable. Progress counts completed read stages, not elapsed time or estimated bytes. Failed reads never count toward 100%.
- The initial error state offers an explicit retry or opening the available data. No perpetual fake progress or automatic retry loop is used.
- Successful and pending reads are reused by URL in this browser page only. Navigation to already-loaded areas does not query the database again. A previously unopened filter, history page or report detail is read once on first access and then reused. Reloading the browser starts a new initial load; nothing is stored in localStorage.
- After a confirmed binding or collection attempt, summary/repository/history reads are invalidated and refreshed without blocking the whole app. Already-read immutable report details remain cached. The explicit Recarregar dados action refreshes all main reads and the current selection, invalidating other cached reads for their next visit.
- Transient update failures keep the last valid data with an actionable warning. A server denial/missing resource (401/403/404) discards the corresponding previous result instead of serving it as an offline fallback. Responses from reads invalidated by a newer refresh cannot overwrite the new cache.
- No polling, Realtime integration, new connection pool, backend behavior change or automatic collection was added. Changes made outside this browser page require Recarregar dados.

Verification for this increment: **14 Node checks passed**, **781 Python tests passed**, JavaScript syntax/Ruff/mypy passed. Browser tests covered staged progress at 67% while the third read was held, release at 100%, loading/error recovery, cached detail reuse, invalidation after synthetic writes, and failed refresh preserving data. All writes were against the isolated browser fixture, not the configured remote database.

Measured on the real configured app: initial availability at **2066 ms**; five subsequent main-page transitions took **17, 68, 68, 30 and 33 ms**, with **zero new API requests** during those transitions and no JavaScript errors. This is one local browser sample, not a general latency guarantee. The first load still includes remote-database latency.

## Boundaries

No PDF export, email sending, scheduling, AI, user signup/login, administration, membership changes or deployments are included in this increment. Gmail with a PDF attachment is planned separately, with Resend deferred. The browser never receives the database password or GitHub key. Mutation requires the exact loopback Host, matching Origin and a per-process request token; reports are scoped server-side. This is not end-user authentication.

Refresh has a five-minute limit. On timeout, inspect the stored execution before retrying: a terminated process can leave an unfinished run requiring operator attention. No automatic retries or silent recovery writes occur.

## Sidebar increment verification

- Full Python suite: **781 passed**. Dashboard-focused suite: **31 passed**, including HTTP/database integration on disposable PostgreSQL. The initial baseline hit a harness connection timeout; the isolated test and the final full run passed without changing the harness.
- Node navigation checks: **5 passed** (`node --test tests/dashboard_routes.test.cjs`). JavaScript syntax, Ruff and mypy passed.
- Browser smoke against an isolated synthetic fixture covered explicit update/bind confirmation, duplicate inspection blocking, pending modal close, Escape/focus restoration, global historical filter options, report reload/back/forward, safe text content, empty/error recovery, and 390px layout in light/dark themes.
- Browser smoke against the actual application and configured database verified Dashboard, history and report detail/return with GET requests only and no JavaScript errors. At the check, counts were 3 active repository connections, 5 saved reports and 0 partial reports; these are observed data, not fixed expected production totals.
- A temporary actual application instance was opened at `http://127.0.0.1:8877/` for preview. An already-running instance at port 8765 still had the old Python handler and returned 404 for the new dashboard endpoint; restart that instance normally before using the new interface on the default port. It was not terminated automatically.
- No remote collection, repository binding, email send, migration, commit or push was performed for this validation. PDF/Gmail and the separately documented collection/lock-recovery proposals remain unimplemented.

## Earlier pilot verification (before the sidebar increment)

- Full suite: 527 tests passed. Ruff passed; mypy passed for 19 source files.
- Dashboard-focused tests: 14 passed, including scoped queries on disposable native PostgreSQL and an HTTP update that commits a synthetic report and reads it back through the dashboard.
- Live server `http://127.0.0.1:8765` returned the existing `Fabrycio13/project-mds` repository and report `627498ad-cea9-4d9f-8848-664cb4bbebf8` from Supabase.
- Browser DOM smoke: populated repository, report opened/closed successfully, report content inserted as text, update control enabled. At 390px viewport the document remained 390px wide with the report open.
- Opened in the desktop preview. No new remote collection was triggered by this smoke; scoped run count and report count remained 1 each.
- Review was scoped/manual because this workspace has no Git history; no Git repository was initialized.
- This validates local synthetic update behavior and live remote reads. A new remote update through the button remains an explicit operator action, not a hidden verification write.
