from pathlib import Path


def test_initial_migration_is_transactional_and_never_alters_managed_schema():
    path = Path(__file__).resolve().parents[1] / "migrations" / "0001_initial.sql"
    sql = path.read_text(encoding="utf-8")
    statements = sql.lower()
    assert statements.lstrip().startswith("--")
    assert "begin;" in statements and statements.rstrip().endswith("commit;")
    assert "create schema github_intelligence" in statements
    assert "create schema auth" not in statements
    assert "create table auth." not in statements
    assert "drop table" not in statements
    assert "truncate " not in statements
    assert "using (true)" not in statements
    assert "force row level security" in statements
    assert "nologin" in statements and "nobypassrls" in statements
    for table in (
        "clients",
        "projects",
        "project_memberships",
        "github_installations",
        "repositories",
        "sync_runs",
        "pr_snapshots",
        "reports",
        "report_sources",
        "audit_events",
    ):
        assert f"create table github_intelligence.{table}" in statements
