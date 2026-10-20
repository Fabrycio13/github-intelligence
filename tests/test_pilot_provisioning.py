"""Pilot provisioning is tested only in disposable PostgreSQL."""
from pathlib import Path

import psycopg
import pytest
from test_storage import prepared_db as prepared_db

SQL = Path(__file__).resolve().parents[1] / "scripts" / "provision_pilot.sql"


def database_rows(db):
    tables = ("clients", "projects", "github_installations", "repositories",
              "project_memberships", "sync_runs", "pr_snapshots", "reports", "audit_events")
    return {
        table: db.execute(f"SELECT * FROM github_intelligence.{table} ORDER BY 1,2").fetchall()
        for table in tables
    }


def test_pilot_seed_reuses_rows_without_changing_history(prepared_db):
    db, _ = prepared_db
    sql = SQL.read_text(encoding="utf-8")
    db.execute(sql, prepare=False)
    db.execute(sql, prepare=False)
    assert db.execute(
        "SELECT count(*) FROM github_intelligence.clients WHERE name='personal'"
    ).fetchone() == (1,)
    assert db.execute(
        "SELECT count(*) FROM github_intelligence.repositories WHERE github_repository_id IN "
        "(1226750215,1410914950)"
    ).fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM github_intelligence.sync_runs").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM github_intelligence.project_memberships").fetchone() == (0,)


def test_pilot_seed_refuses_ambiguous_client_without_partial_writes(prepared_db):
    db, _ = prepared_db
    db.execute("INSERT INTO github_intelligence.clients(name) VALUES ('personal'),('personal')")
    with pytest.raises(psycopg.Error, match="Ambiguous"):
        db.execute(SQL.read_text(encoding="utf-8"), prepare=False)
    db.execute("ROLLBACK")
    assert db.execute(
        "SELECT count(*) FROM github_intelligence.repositories WHERE github_repository_id=1226750215"
    ).fetchone() == (0,)


def test_repeated_seed_preserves_all_existing_values_and_exact_scope(prepared_db):
    db, _ = prepared_db
    before = database_rows(db)
    db.execute(SQL.read_text(encoding="utf-8"), prepare=False)
    after = database_rows(db)
    for table in before:
        assert all(row in after[table] for row in before[table])
        expected_added = 1 if table in {"clients", "projects", "github_installations", "repositories"} else 0
        assert len(after[table]) == len(before[table]) + expected_added
    assert db.execute(
        "SELECT c.name,p.name,i.github_app_id,i.github_installation_id,r.github_repository_id,r.full_name "
        "FROM github_intelligence.repositories r "
        "JOIN github_intelligence.clients c ON c.id=r.client_id "
        "JOIN github_intelligence.projects p ON p.id=r.project_id AND p.client_id=c.id "
        "JOIN github_intelligence.github_installations i ON i.id=r.installation_id AND i.client_id=c.id "
        "WHERE r.github_repository_id=1226750215"
    ).fetchall() == [("personal", "pilot", 5380709, 173020772, 1226750215, "Fabrycio13/project-mds")]
    db.execute(
        "UPDATE github_intelligence.repositories SET expected_approvals=7,staging_branch='custom' "
        "WHERE github_repository_id=1226750215"
    )
    saved = database_rows(db)
    db.execute(SQL.read_text(encoding="utf-8"), prepare=False)
    assert database_rows(db) == saved


def test_installation_conflict_rolls_back_new_client_and_project(prepared_db):
    db, ids = prepared_db
    db.execute(
        "INSERT INTO github_intelligence.github_installations "
        "(client_id,github_app_id,github_installation_id) VALUES (%s,5380709,173020772)",
        (ids["client_a"],),
    )
    before = database_rows(db)
    with pytest.raises(psycopg.Error, match="another client"):
        db.execute(SQL.read_text(encoding="utf-8"), prepare=False)
    db.execute("ROLLBACK")
    assert database_rows(db) == before
