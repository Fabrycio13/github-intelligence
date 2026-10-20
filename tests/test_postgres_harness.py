"""Checks against the native, disposable PostgreSQL test engine only."""

import runpy
import stat
import zipfile
from pathlib import Path

import psycopg
import pytest
from conftest import _binary_dir
from psycopg.conninfo import conninfo_to_dict

_PREPARE = runpy.run_path(str(Path(__file__).parents[1] / "scripts/prepare_test_postgres.py"))


def test_native_postgres_17_is_loopback_only(pg_cluster_dsn):
    assert conninfo_to_dict(pg_cluster_dsn)["host"] == "127.0.0.1"
    with psycopg.connect(pg_cluster_dsn) as conn:
        assert conn.execute("SHOW server_version_num").fetchone()[0].startswith("17")
        assert conn.execute("SHOW listen_addresses").fetchone()[0] == "127.0.0.1"
        assert str(conn.execute("SELECT inet_server_addr()").fetchone()[0]) == "127.0.0.1"
        roles = conn.execute(
            "SELECT rolname, rolbypassrls, rolcanlogin FROM pg_roles "
            "WHERE rolname IN ('anon', 'authenticated', 'service_role') ORDER BY rolname"
        ).fetchall()
        assert roles == [
            ("anon", False, False), ("authenticated", False, False), ("service_role", True, False),
        ]


def test_database_is_fresh_and_autocommit(pg_admin):
    assert pg_admin.autocommit is True
    assert pg_admin.info.dbname.startswith("ghi_test_")
    assert pg_admin.execute("SELECT to_regclass('public.harness_marker')").fetchone()[0] is None
    pg_admin.execute("CREATE TABLE public.harness_marker (id integer)")


def test_another_database_is_fresh(pg_admin):
    assert pg_admin.execute("SELECT to_regclass('public.harness_marker')").fetchone()[0] is None


def test_database_is_removed_after_fixture(request, pg_cluster_dsn):
    # Run this finalizer after pg_admin's teardown (registered later).
    name = []

    def verify_removed():
        with psycopg.connect(pg_cluster_dsn, autocommit=True) as conn:
            assert conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name[0],)).fetchone() is None

    request.addfinalizer(verify_removed)
    conn = request.getfixturevalue("pg_admin")
    name.append(conn.info.dbname)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", r"..\escape", "pgsql/bin/file:ads"])
def test_zip_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, b"must not escape")
    with pytest.raises(ValueError, match="Unsafe ZIP member"):
        _PREPARE["safe_extract"](archive, tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_zip_rejects_symlink(tmp_path):
    archive = tmp_path / "symlink.zip"
    entry = zipfile.ZipInfo("pgsql/bin/postgres.exe")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(entry, "../../outside")
    with pytest.raises(ValueError, match="Unsafe ZIP member"):
        _PREPARE["safe_extract"](archive, tmp_path / "out")


def test_zip_extracts_engine_only(tmp_path):
    archive = tmp_path / "safe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("pgsql/bin/example.txt", "engine")
        bundle.writestr("pgsql/pgAdmin 4/example.txt", "not needed")
    destination = tmp_path / "out"
    _PREPARE["safe_extract"](archive, destination)
    assert (destination / "pgsql/bin/example.txt").read_text() == "engine"
    assert not (destination / "pgsql/pgAdmin 4").exists()


def test_explicit_invalid_binary_dir_fails_not_skips(monkeypatch, tmp_path):
    monkeypatch.setenv("GHI_PG_BIN", str(tmp_path / "absent"))
    with pytest.raises(pytest.fail.Exception, match="Invalid GHI_PG_BIN"):
        _binary_dir()
