-- REVIEW DRAFT: do not apply to Supabase until explicitly approved.
-- Target: cpkgvpuivjzplerzlpxz. Tested only in a disposable PostgreSQL database.
-- Additive, transactional, apply once. A repeated execution fails, not silently adopts an unknown schema.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $roles$
DECLARE name text;
BEGIN
  FOREACH name IN ARRAY ARRAY['ghi_owner', 'github_intelligence_runtime'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = name) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION', name);
    ELSIF EXISTS (
      SELECT 1 FROM pg_catalog.pg_roles
      WHERE rolname = name AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit)
    ) THEN
      RAISE EXCEPTION 'Existing application role is not restricted; review required';
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members am
    JOIN pg_catalog.pg_roles parent ON parent.oid = am.roleid
    JOIN pg_catalog.pg_roles member ON member.oid = am.member
    WHERE parent.rolname IN ('ghi_owner', 'github_intelligence_runtime')
      AND member.rolname IN ('anon', 'authenticated', 'service_role', 'authenticator', 'github_intelligence_runtime')
  ) THEN
    RAISE EXCEPTION 'Unsafe existing role membership; review required';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_db_role_setting settings,
      LATERAL unnest(settings.setconfig) AS setting(value),
      LATERAL regexp_split_to_table(split_part(setting.value, '=', 2), ',') AS exposed(schema_name)
    WHERE setting.value LIKE 'pgrst.db_schemas=%'
      AND settings.setdatabase IN (0, (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()))
      AND btrim(exposed.schema_name) = 'github_intelligence'
  ) THEN
    RAISE EXCEPTION 'Application schema must not be exposed through the Data API';
  END IF;
  -- The migration operator needs SET membership to transfer ownership without superuser.
  -- This is not granted to runtime, browser roles, or any user supplied by the application.
  EXECUTE format('GRANT ghi_owner TO %I WITH SET TRUE', current_user);
END
$roles$;

CREATE SCHEMA github_intelligence;
GRANT USAGE, CREATE ON SCHEMA github_intelligence TO ghi_owner;
REVOKE ALL ON SCHEMA github_intelligence FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA github_intelligence TO github_intelligence_runtime;

CREATE FUNCTION github_intelligence.client_context() RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog AS $fn$
  SELECT nullif(current_setting('ghi.client_id', true), '')::uuid
$fn$;
CREATE FUNCTION github_intelligence.project_context() RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog AS $fn$
  SELECT nullif(current_setting('ghi.project_id', true), '')::uuid
$fn$;

CREATE TABLE github_intelligence.clients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL CHECK (length(btrim(name)) > 0),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE github_intelligence.projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES github_intelligence.clients(id) ON DELETE RESTRICT,
  name text NOT NULL CHECK (length(btrim(name)) > 0),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (client_id, id)
);

CREATE TABLE github_intelligence.project_memberships (
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  auth_user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE RESTRICT,
  role text NOT NULL CHECK (role IN ('admin', 'manager', 'viewer')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (client_id, project_id, auth_user_id),
  FOREIGN KEY (client_id, project_id) REFERENCES github_intelligence.projects(client_id, id) ON DELETE RESTRICT
);

CREATE TABLE github_intelligence.github_installations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES github_intelligence.clients(id) ON DELETE RESTRICT,
  github_app_id bigint NOT NULL CHECK (github_app_id > 0),
  github_installation_id bigint NOT NULL CHECK (github_installation_id > 0),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'revoked', 'unknown')),
  verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (client_id, id),
  UNIQUE (client_id, github_app_id, github_installation_id)
);

CREATE TABLE github_intelligence.repositories (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  installation_id uuid NOT NULL,
  github_repository_id bigint NOT NULL CHECK (github_repository_id > 0),
  full_name text NOT NULL CHECK (full_name ~ '^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+$' AND split_part(full_name, '/', 2) NOT IN ('.','..')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive', 'removed')),
  main_branch text,
  staging_branch text,
  production_branch text,
  expected_approvals integer NOT NULL DEFAULT 2 CHECK (expected_approvals >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (client_id, project_id, id),
  UNIQUE (client_id, project_id, github_repository_id),
  FOREIGN KEY (client_id, project_id) REFERENCES github_intelligence.projects(client_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (client_id, installation_id) REFERENCES github_intelligence.github_installations(client_id, id) ON DELETE RESTRICT
);

CREATE TABLE github_intelligence.sync_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  repository_id uuid NOT NULL,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','partial','failed')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  started_at timestamptz,
  finished_at timestamptz,
  error_code text CHECK (error_code ~ '^[A-Z0-9_]{1,64}$'),
  CHECK (window_start < window_end),
  CHECK (
    (status = 'pending' AND started_at IS NULL AND finished_at IS NULL) OR
    (status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL) OR
    (status IN ('succeeded','partial','failed') AND started_at IS NOT NULL AND finished_at IS NOT NULL AND finished_at >= started_at)
  ),
  UNIQUE (client_id, project_id, id),
  UNIQUE (client_id, project_id, repository_id, id),
  FOREIGN KEY (client_id, project_id, repository_id)
    REFERENCES github_intelligence.repositories(client_id, project_id, id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX one_active_sync_per_repository ON github_intelligence.sync_runs(repository_id)
  WHERE status IN ('pending','running');
CREATE INDEX sync_runs_scope_time ON github_intelligence.sync_runs(client_id, project_id, created_at DESC);

CREATE TABLE github_intelligence.pr_snapshots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  repository_id uuid NOT NULL,
  sync_run_id uuid NOT NULL,
  pr_number integer NOT NULL CHECK (pr_number > 0),
  head_sha text NOT NULL CHECK (head_sha ~ '^[a-fA-F0-9]{7,64}$'),
  observed_at timestamptz NOT NULL,
  evaluator_version text NOT NULL CHECK (length(evaluator_version) > 0),
  payload jsonb NOT NULL CHECK ((jsonb_typeof(payload) = 'object'
    AND payload->'schema_version' = '1'::jsonb
    AND jsonb_typeof(payload->'snapshot') = 'object') IS TRUE),
  fingerprint text NOT NULL CHECK (fingerprint ~ '^[a-f0-9]{64}$'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (client_id, project_id, sync_run_id, pr_number),
  UNIQUE (client_id, project_id, sync_run_id, id),
  FOREIGN KEY (client_id, project_id, repository_id, sync_run_id)
    REFERENCES github_intelligence.sync_runs(client_id, project_id, repository_id, id) ON DELETE RESTRICT
);
CREATE INDEX pr_snapshots_lookup ON github_intelligence.pr_snapshots(client_id, project_id, repository_id, pr_number, observed_at DESC);

CREATE TABLE github_intelligence.reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  window_start timestamptz NOT NULL,
  window_end timestamptz NOT NULL,
  format text NOT NULL DEFAULT 'markdown' CHECK (format = 'markdown'),
  content text NOT NULL CHECK (length(content) > 0),
  complete boolean NOT NULL,
  generated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (window_start < window_end),
  UNIQUE (client_id, project_id, id),
  FOREIGN KEY (client_id, project_id) REFERENCES github_intelligence.projects(client_id, id) ON DELETE RESTRICT
);
CREATE INDEX reports_scope_time ON github_intelligence.reports(client_id, project_id, generated_at DESC);

CREATE TABLE github_intelligence.report_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  report_id uuid NOT NULL,
  sync_run_id uuid NOT NULL,
  snapshot_id uuid,
  UNIQUE NULLS NOT DISTINCT (report_id, sync_run_id, snapshot_id),
  FOREIGN KEY (client_id, project_id, report_id)
    REFERENCES github_intelligence.reports(client_id, project_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (client_id, project_id, sync_run_id)
    REFERENCES github_intelligence.sync_runs(client_id, project_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (client_id, project_id, sync_run_id, snapshot_id)
    REFERENCES github_intelligence.pr_snapshots(client_id, project_id, sync_run_id, id) ON DELETE RESTRICT
);

CREATE TABLE github_intelligence.audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL,
  project_id uuid NOT NULL,
  action text NOT NULL CHECK (action IN (
    'repository_status_changed','sync_queued','sync_started','sync_finished','snapshot_saved','report_created'
  )),
  entity_id uuid NOT NULL,
  actor_kind text NOT NULL CHECK (actor_kind IN ('system','manual_cli','user')),
  auth_user_id uuid REFERENCES auth.users(id) ON DELETE RESTRICT,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((actor_kind = 'user' AND auth_user_id IS NOT NULL) OR (actor_kind <> 'user' AND auth_user_id IS NULL)),
  FOREIGN KEY (client_id, project_id) REFERENCES github_intelligence.projects(client_id, id) ON DELETE RESTRICT
);
CREATE INDEX audit_events_scope_time ON github_intelligence.audit_events(client_id, project_id, occurred_at DESC);

CREATE FUNCTION github_intelligence.reject_immutable_change() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
  RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Historical record is immutable';
END
$fn$;
CREATE FUNCTION github_intelligence.validate_run_transition() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
  IF OLD.status IN ('succeeded','partial','failed') OR
     (NEW.id,NEW.client_id,NEW.project_id,NEW.repository_id,NEW.window_start,NEW.window_end) IS DISTINCT FROM
     (OLD.id,OLD.client_id,OLD.project_id,OLD.repository_id,OLD.window_start,OLD.window_end) OR
     (NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'pending' AND NEW.status IN ('running','failed')) OR
       (OLD.status = 'running' AND NEW.status IN ('succeeded','partial','failed'))
     )) THEN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Invalid execution transition';
  END IF;
  RETURN NEW;
END
$fn$;
CREATE TRIGGER validate_sync_transition BEFORE UPDATE ON github_intelligence.sync_runs
  FOR EACH ROW EXECUTE FUNCTION github_intelligence.validate_run_transition();

CREATE FUNCTION github_intelligence.require_running_snapshot() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM github_intelligence.sync_runs r WHERE r.id = NEW.sync_run_id
      AND r.client_id = NEW.client_id AND r.project_id = NEW.project_id
      AND r.repository_id = NEW.repository_id AND r.status = 'running'
      FOR SHARE
  ) THEN
    RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'Snapshot requires a running execution';
  END IF;
  RETURN NEW;
END
$fn$;
CREATE TRIGGER snapshot_requires_running BEFORE INSERT ON github_intelligence.pr_snapshots
  FOR EACH ROW EXECUTE FUNCTION github_intelligence.require_running_snapshot();

-- These policies are for trusted server workers, NOT browser identity authorization.
-- SET LOCAL context must be set after server-side authorization, per transaction.
DO $security$
DECLARE name text;
BEGIN
  FOREACH name IN ARRAY ARRAY[
    'clients','projects','project_memberships','github_installations','repositories',
    'sync_runs','pr_snapshots','reports','report_sources','audit_events'
  ] LOOP
    EXECUTE format('ALTER TABLE github_intelligence.%I ENABLE ROW LEVEL SECURITY', name);
    EXECUTE format('ALTER TABLE github_intelligence.%I FORCE ROW LEVEL SECURITY', name);
    EXECUTE format('REVOKE ALL ON TABLE github_intelligence.%I FROM PUBLIC, anon, authenticated, service_role', name);
    IF name = 'clients' THEN
      EXECUTE 'CREATE POLICY worker_scope ON github_intelligence.clients TO github_intelligence_runtime USING (id = github_intelligence.client_context()) WITH CHECK (id = github_intelligence.client_context())';
    ELSIF name = 'github_installations' THEN
      EXECUTE 'CREATE POLICY worker_scope ON github_intelligence.github_installations TO github_intelligence_runtime USING (client_id = github_intelligence.client_context()) WITH CHECK (client_id = github_intelligence.client_context())';
    ELSIF name = 'projects' THEN
      EXECUTE 'CREATE POLICY worker_scope ON github_intelligence.projects TO github_intelligence_runtime USING (client_id = github_intelligence.client_context() AND id = github_intelligence.project_context()) WITH CHECK (client_id = github_intelligence.client_context() AND id = github_intelligence.project_context())';
    ELSE
      EXECUTE format('CREATE POLICY worker_scope ON github_intelligence.%I TO github_intelligence_runtime USING (client_id = github_intelligence.client_context() AND project_id = github_intelligence.project_context()) WITH CHECK (client_id = github_intelligence.client_context() AND project_id = github_intelligence.project_context())', name);
    END IF;
    IF name IN ('pr_snapshots','reports','report_sources','audit_events') THEN
      EXECUTE format('CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON github_intelligence.%I FOR EACH ROW EXECUTE FUNCTION github_intelligence.reject_immutable_change()', name);
    END IF;
  END LOOP;
END
$security$;

REVOKE ALL ON ALL FUNCTIONS IN SCHEMA github_intelligence FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION github_intelligence.client_context(), github_intelligence.project_context()
  TO github_intelligence_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA github_intelligence TO github_intelligence_runtime;
GRANT INSERT ON github_intelligence.sync_runs, github_intelligence.pr_snapshots,
  github_intelligence.reports, github_intelligence.report_sources, github_intelligence.audit_events
  TO github_intelligence_runtime;
GRANT UPDATE (status, started_at, finished_at, error_code) ON github_intelligence.sync_runs
  TO github_intelligence_runtime;
GRANT UPDATE (status, updated_at) ON github_intelligence.repositories TO github_intelligence_runtime;

-- Own application objects with a NOLOGIN role, separate from workers and browser roles.
DO $ownership$
DECLARE object record;
BEGIN
  FOR object IN SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'github_intelligence' LOOP
    EXECUTE format('ALTER TABLE github_intelligence.%I OWNER TO ghi_owner', object.tablename);
  END LOOP;
END
$ownership$;
ALTER FUNCTION github_intelligence.client_context() OWNER TO ghi_owner;
ALTER FUNCTION github_intelligence.project_context() OWNER TO ghi_owner;
ALTER FUNCTION github_intelligence.reject_immutable_change() OWNER TO ghi_owner;
ALTER FUNCTION github_intelligence.validate_run_transition() OWNER TO ghi_owner;
ALTER FUNCTION github_intelligence.require_running_snapshot() OWNER TO ghi_owner;
ALTER SCHEMA github_intelligence OWNER TO ghi_owner;
COMMIT;
