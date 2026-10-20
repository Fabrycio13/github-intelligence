# Add a repository from the local dashboard

## Intended flow

1. Click **Adicionar repositório** and enter an exact HTTPS GitHub repository URL or `OWNER/REPO`.
2. **Verificar acesso** checks the configured App, the repository installation and its read-only repository access. It does not provision database rows or start a collection.
3. If access is missing, **Autorizar no GitHub** opens the App's installation page. Install/approve there, return and click **Já autorizei — verificar novamente**. This MVP uses explicit recheck, not an OAuth callback pretending to prove installation ownership.
4. If access is verified, **Confirmar vínculo** repeats verification server-side and compares repository/App/installation identity with what was displayed for confirmation. Browser IDs are comparison-only, never authority. If identity changed, the request is rejected and requires a new inspection/confirmation instead of silently binding a replacement repository.
5. The repository appears without a sync/report. Only **Atualizar agora** initiates collection, with a separate confirmation.

GitHub credentials never enter the local frontend. The same App key can identify installations, but repository collection must use the actual installation for that repository, not the first installation in local configuration. A private App or an organization's approval policy can prevent installation in a third-party account; entering a URL does not grant access.

## Database approval gate

The current worker has no INSERT privilege on `github_installations` or `repositories`; read-only preflight confirmed both were false. The draft `migrations/0003_repository_onboarding.sql` is the one-time permission change required for frontend linking. It must be reviewed and explicitly approved before remote application. No new permission, user, membership or repository has been written remotely as part of implementing this flow.

The draft also adds a fixed `SECURITY DEFINER` status-lock helper owned by the existing NOLOGIN `ghi_owner`, with a pinned search path and no dynamic SQL. Three owner-only RLS policies retain the same client/project predicates so the helper can take row locks under forced RLS. Only runtime receives EXECUTE; PUBLIC, browser roles and the direct worker login do not. Runtime receives no UPDATE privilege or owner membership. Active client/project and any existing matching installation remain locked until binding commits, so an administrative deactivation/revocation cannot interleave after validation.

Existing clients/projects are required. Onboarding does not create new tenants, change permissions from the browser, reactivate removed/inactive records or replace a conflicting repository identity. Existing monitoring settings and historical reports remain intact. The local registry is synchronized after database commit; a local-file failure is reported distinctly and can be retried without creating duplicate database rows.

## Local-only scope

This is an operator-local MVP, not a hosted multi-user authorization system. Host/Origin/request-token protections remain in force. No signup, user OAuth, public callbacks, scheduler or automatic first collection is introduced.

## Provider references

GitHub REST Apps documentation: authenticated App metadata; repository installation lookup authenticated by App JWT; read-only installation-token exchange. GitHub App sharing documentation governs private/public installation eligibility. The implementation does not change the App's visibility or repository selection automatically.

## Verification

- Current complete suite: 768 tests passed; Ruff passed; mypy passed for 22 source files.
- A failing regression where a metadata-only token was reported ready was reproduced and fixed: onboarding now requires every read permission used by the collector, without exposing the issued token.
- Live read-only check verified `Fabrycio13/skills`, repository ID `1410914950`, installation `173020772`; the browser correctly disabled confirmation because the remote INSERT capability is still absent.
- Live inspection of `octocat/Hello-World` returned authorization required and displayed the validated App installation link with an explicit recheck action. No installation or authorization was submitted for that repository.
- Real disposable PostgreSQL tests exercise inspection without writes, permission-gated binding, repeated confirmation without duplicate rows, local registry synchronization and no automatic collection.
- Six two-connection status-race regressions failed on the original binder and passed with the helper, using the actual restricted worker and bounded administrative lock timeouts. Deactivation committed before binding is rejected; an administrative status update can proceed after binding commits. Helper permission/search-path/owner-policy drift checks remain fail-closed.
- Confirmation regressions reject changed repository/App/installation IDs and require the identity shown in the UI, reverified against the provider. Browser comparison values are never used as source authority.
- Final review found two diagnostic gaps, reproduced and fixed locally: effective UPDATE grants on protected status tables/direct worker helper execution now fail health checks; owner LOGIN/SUPERUSER/BYPASSRLS drift is rejected before migration and during health checks. Thirteen new negative regressions passed after failing before the correction. This was local fault injection, not evidence of remote privilege drift or a demonstrated cross-scope exploit.
- A separate public `db check` still passed against Supabase after the final diagnostic changes, with TLS verify-full and 10 tables; this read-only check did not apply the onboarding migration.
- No remote migration, bind or import was executed during this implementation. Multi-installation routing is tested synthetically; no claim of a completed third-party installation is made.
