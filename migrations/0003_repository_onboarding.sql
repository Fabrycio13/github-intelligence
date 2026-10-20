-- REVIEW DRAFT: local disposable PostgreSQL validation only.
-- Do not apply remotely without explicit approval of this exact migration.
-- Requires reviewed 0001 and 0002. Insert-only onboarding; no seed or credentials.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
SET LOCAL search_path = pg_catalog;

DO $preflight$
DECLARE
  name text;
  expected text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'ghi_owner'
      AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
  ) THEN
    RAISE EXCEPTION 'Expected restricted NOLOGIN owner is missing or has drifted';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner
    WHERE n.nspname = 'github_intelligence' AND r.rolname = 'ghi_owner'
  ) THEN
    RAISE EXCEPTION 'Expected private schema ownership is missing or has drifted';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'github_intelligence_runtime'
      AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
  ) OR pg_has_role('github_intelligence_runtime', 'ghi_owner', 'MEMBER') THEN
    RAISE EXCEPTION 'Expected restricted runtime role is missing or has drifted';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'github_intelligence_worker'
      AND rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_auth_members m
    JOIN pg_roles parent ON parent.oid = m.roleid
    JOIN pg_roles member ON member.oid = m.member
    WHERE parent.rolname = 'github_intelligence_runtime'
      AND member.rolname = 'github_intelligence_worker'
      AND NOT m.admin_option AND NOT m.inherit_option AND m.set_option
  ) OR pg_has_role('github_intelligence_worker', 'ghi_owner', 'MEMBER') THEN
    RAISE EXCEPTION 'Expected NOINHERIT worker membership is missing or has drifted';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon', 'authenticated', 'service_role')
      AND (pg_has_role(r.oid, 'github_intelligence_runtime', 'MEMBER')
        OR pg_has_role(r.oid, 'ghi_owner', 'MEMBER')
        OR has_schema_privilege(r.oid, 'github_intelligence', 'USAGE,CREATE'))
  ) THEN
    RAISE EXCEPTION 'Private schema must not be available to browser or service roles';
  END IF;
  FOREACH name IN ARRAY ARRAY[
    'clients','projects','project_memberships','github_installations','repositories',
    'sync_runs','pr_snapshots','reports','report_sources','audit_events'
  ] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_roles r ON r.oid = c.relowner
      WHERE n.nspname = 'github_intelligence' AND c.relname = name
        AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity
        AND r.rolname = 'ghi_owner'
    ) THEN
      RAISE EXCEPTION 'Required table ownership or forced RLS has drifted';
    END IF;
    expected := CASE WHEN name = 'clients' THEN 'id' ELSE 'client_id' END
      || '=github_intelligence.client_context';
    IF name NOT IN ('clients', 'github_installations') THEN
      expected := expected || 'AND'
        || CASE WHEN name = 'projects' THEN 'id' ELSE 'project_id' END
        || '=github_intelligence.project_context';
    END IF;
    IF (SELECT count(*) FROM pg_policies
        WHERE schemaname = 'github_intelligence' AND tablename = name) <> 1
      OR NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'github_intelligence' AND tablename = name
          AND policyname = 'worker_scope' AND permissive = 'PERMISSIVE' AND cmd = 'ALL'
          AND roles = ARRAY['github_intelligence_runtime']::name[]
          AND regexp_replace(qual, '[[:space:]()]', '', 'g') = expected
          AND regexp_replace(with_check, '[[:space:]()]', '', 'g') = expected
      ) THEN
      RAISE EXCEPTION 'Required runtime scope policy has drifted';
    END IF;
  END LOOP;
END
$preflight$;

SET LOCAL ROLE ghi_owner;
-- The existing NOLOGIN table owner performs fixed status locks only through this
-- helper. Runtime receives no UPDATE privilege and cannot SET ROLE ghi_owner.
-- Owner policies use the same trusted-worker context; no unscoped access.
CREATE POLICY onboarding_owner_lock ON github_intelligence.clients
  FOR ALL TO ghi_owner
  USING (id = github_intelligence.client_context())
  WITH CHECK (id = github_intelligence.client_context());
CREATE POLICY onboarding_owner_lock ON github_intelligence.projects
  FOR ALL TO ghi_owner
  USING (client_id = github_intelligence.client_context() AND id = github_intelligence.project_context())
  WITH CHECK (client_id = github_intelligence.client_context() AND id = github_intelligence.project_context());
CREATE POLICY onboarding_owner_lock ON github_intelligence.github_installations
  FOR ALL TO ghi_owner
  USING (client_id = github_intelligence.client_context())
  WITH CHECK (client_id = github_intelligence.client_context());

CREATE FUNCTION github_intelligence.lock_repository_binding_scope(app_id bigint, installation_id bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $guard$
BEGIN
  IF app_id IS NULL OR app_id <= 0 OR installation_id IS NULL OR installation_id <= 0 THEN
    RETURN false;
  END IF;
  PERFORM 1 FROM github_intelligence.clients c
    WHERE c.id = github_intelligence.client_context() AND c.status = 'active'
    FOR SHARE;
  IF NOT FOUND THEN RETURN false; END IF;
  PERFORM 1 FROM github_intelligence.projects p
    WHERE p.client_id = github_intelligence.client_context()
      AND p.id = github_intelligence.project_context() AND p.status = 'active'
    FOR SHARE;
  IF NOT FOUND THEN RETURN false; END IF;
  -- Lock even an inactive existing installation: the caller rejects its status
  -- after this call, while a concurrent admin cannot revoke an active one mid-bind.
  PERFORM 1 FROM github_intelligence.github_installations i
    WHERE i.client_id = github_intelligence.client_context()
      AND i.github_app_id = app_id AND i.github_installation_id = installation_id
    FOR SHARE;
  RETURN true;
END
$guard$;
REVOKE ALL ON FUNCTION github_intelligence.lock_repository_binding_scope(bigint,bigint)
  FROM PUBLIC, anon, authenticated, service_role, github_intelligence_worker;
GRANT EXECUTE ON FUNCTION github_intelligence.lock_repository_binding_scope(bigint,bigint)
  TO github_intelligence_runtime;
GRANT INSERT (client_id, github_app_id, github_installation_id, status, verified_at)
  ON github_intelligence.github_installations TO github_intelligence_runtime;
GRANT INSERT (client_id, project_id, installation_id, github_repository_id,
              full_name, status, main_branch, expected_approvals)
  ON github_intelligence.repositories TO github_intelligence_runtime;
COMMIT;
