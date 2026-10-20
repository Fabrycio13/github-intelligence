-- REVIEW DRAFT: do not apply to Supabase until explicitly approved.
-- Target: cpkgvpuivjzplerzlpxz. Additive role provisioning only; no application data changes.
-- Creates a login with NO password. Set its password separately through a secure psql `\password` prompt.
-- Apply once, after confirming 0001 and reviewing the remote role/catalog state.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $preflight$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
    WHERE rolname = 'github_intelligence_runtime'
      AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
      AND NOT rolinherit
  ) THEN
    RAISE EXCEPTION 'Expected restricted NOLOGIN runtime group is missing or has drifted';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'github_intelligence_worker'
  ) THEN
    RAISE EXCEPTION 'Worker role already exists; inspect it instead of adopting it';
  END IF;
END
$preflight$;

CREATE ROLE github_intelligence_worker
  LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT github_intelligence_runtime TO github_intelligence_worker
  WITH ADMIN FALSE, INHERIT FALSE, SET TRUE;

COMMIT;
