-- REVIEW REQUIRED. Target: Supabase project cpkgvpuivjzplerzlpxz.
-- Business-data seed, NOT a schema migration. Do not run as the worker.
-- First pilot: personal / pilot / Fabrycio13/project-mds ONLY.
-- Does not import snapshots, change permissions, create users or grant membership.
-- Existing ambiguous, inactive or conflicting records cause rollback, not overwrite.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
LOCK TABLE github_intelligence.clients, github_intelligence.projects,
  github_intelligence.github_installations, github_intelligence.repositories
  IN SHARE ROW EXCLUSIVE MODE;
DO $pilot$
DECLARE
  cid uuid;
  pid uuid;
  iid uuid;
  n integer;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles
    WHERE rolname=current_user AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'Administrative visibility required; do not run as worker';
  END IF;

  SELECT count(*) INTO n FROM github_intelligence.clients WHERE name='personal';
  IF n > 1 THEN RAISE EXCEPTION 'Ambiguous personal client'; END IF;
  SELECT id INTO cid FROM github_intelligence.clients WHERE name='personal';
  IF cid IS NULL THEN
    INSERT INTO github_intelligence.clients(name,status)
      VALUES ('personal','active') RETURNING id INTO cid;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM github_intelligence.clients WHERE id=cid AND status='active') THEN
    RAISE EXCEPTION 'Client inactive; explicit review required';
  END IF;

  SELECT count(*) INTO n FROM github_intelligence.projects WHERE client_id=cid AND name='pilot';
  IF n > 1 THEN RAISE EXCEPTION 'Ambiguous pilot project'; END IF;
  SELECT id INTO pid FROM github_intelligence.projects WHERE client_id=cid AND name='pilot';
  IF pid IS NULL THEN
    INSERT INTO github_intelligence.projects(client_id,name,status)
      VALUES (cid,'pilot','active') RETURNING id INTO pid;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM github_intelligence.projects WHERE id=pid AND status='active') THEN
    RAISE EXCEPTION 'Project inactive; explicit review required';
  END IF;

  IF EXISTS (SELECT 1 FROM github_intelligence.github_installations
    WHERE github_app_id=5380709 AND github_installation_id=173020772 AND client_id<>cid) THEN
    RAISE EXCEPTION 'Installation already bound to another client';
  END IF;
  SELECT id INTO iid FROM github_intelligence.github_installations
    WHERE client_id=cid AND github_app_id=5380709 AND github_installation_id=173020772;
  IF iid IS NULL THEN
    INSERT INTO github_intelligence.github_installations
      (client_id,github_app_id,github_installation_id,status,verified_at)
      VALUES (cid,5380709,173020772,'active',NULL) RETURNING id INTO iid;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM github_intelligence.github_installations
    WHERE id=iid AND status='active') THEN
    RAISE EXCEPTION 'Installation not active; explicit review required';
  END IF;

  IF EXISTS (SELECT 1 FROM github_intelligence.repositories
    WHERE (github_repository_id=1226750215 OR lower(full_name)='fabrycio13/project-mds')
      AND NOT (client_id=cid AND project_id=pid AND installation_id=iid
        AND github_repository_id=1226750215 AND full_name='Fabrycio13/project-mds'
        AND status='active')) THEN
    RAISE EXCEPTION 'Repository identity, scope or status conflict; review required';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM github_intelligence.repositories
    WHERE client_id=cid AND project_id=pid AND github_repository_id=1226750215) THEN
    INSERT INTO github_intelligence.repositories
      (client_id,project_id,installation_id,github_repository_id,full_name,status,
       main_branch,staging_branch,production_branch,expected_approvals)
      VALUES (cid,pid,iid,1226750215,'Fabrycio13/project-mds','active','main',NULL,NULL,2);
  END IF;
END
$pilot$;
COMMIT;
-- Read back actual UUIDs for the import plan; do not invent them locally.
SELECT c.id AS client_id,p.id AS project_id,r.id AS repository_id,
  i.github_app_id,i.github_installation_id,r.github_repository_id,r.full_name,r.status
FROM github_intelligence.clients c
JOIN github_intelligence.projects p ON p.client_id=c.id
JOIN github_intelligence.repositories r ON r.client_id=c.id AND r.project_id=p.id
JOIN github_intelligence.github_installations i ON i.client_id=c.id AND i.id=r.installation_id
WHERE c.name='personal' AND p.name='pilot' AND r.github_repository_id=1226750215;
