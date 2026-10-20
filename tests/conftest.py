"""Native disposable PostgreSQL only: never reads project/remote database URLs."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

_HERMES = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "hermes"
_CACHE_BIN = _HERMES / "tools/postgresql-17.11-5/pgsql/bin"
_SCRATCH = _HERMES / "cache/scratch"


def _binary_dir() -> Path:
    configured = os.environ.get("GHI_PG_BIN")
    path = Path(configured) if configured is not None else _CACHE_BIN
    missing = [name for name in ("postgres.exe", "initdb.exe", "pg_ctl.exe") if not (path / name).is_file()]
    if missing:
        reason = f"Native PostgreSQL 17 missing at {path}: {', '.join(missing)}"
        if configured is not None:
            pytest.fail(f"Invalid GHI_PG_BIN: {reason}")
        pytest.skip(f"{reason}; run uv run python scripts/prepare_test_postgres.py")
    try:
        version = subprocess.run(
            [str(path / "postgres.exe"), "--version"],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.fail(f"Cannot execute native PostgreSQL at {path}: {exc}")
    if not version.startswith("postgres (PostgreSQL) 17."):
        pytest.fail(f"Native PostgreSQL 17 required, got {version!r}")
    return path.resolve()


@pytest.fixture(scope="session")
def pg_cluster_dsn() -> Iterator[str]:
    """Start one session cluster on IPv4 loopback, with trust only for local tests."""
    binaries = _binary_dir()
    _SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="ghi-pg-cluster-", dir=_SCRATCH))
    data = root / "data"
    logfile = root / "postgres.log"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    process = None
    with logfile.open("wb") as log:
        try:
            subprocess.run(
                [str(binaries / "initdb.exe"), "-D", str(data), "-U", "postgres",
                 "--locale=C", "--encoding=UTF8", "--auth=trust", "--no-instructions"],
                check=True, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=60,
            )
            # Even though listening is IPv4 only, restrict HBA to that interface.
            (data / "pg_hba.conf").write_text(
                "host all all 127.0.0.1/32 trust\n", encoding="ascii",
            )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            dsn = make_conninfo(
                host="127.0.0.1", hostaddr="127.0.0.1", port=port, user="postgres",
                dbname="postgres", password="", sslmode="disable", gssencmode="disable",
                options="", connect_timeout=1,
            )
            process = subprocess.Popen(
                [str(binaries / "postgres.exe"), "-D", str(data), "-h", "127.0.0.1",
                 "-p", str(port), "-c", "unix_socket_directories=", "-c", "logging_collector=off"],
                stdout=log, stderr=subprocess.STDOUT, env=env,
            )
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"PostgreSQL exited with {process.returncode}")
                try:
                    with psycopg.connect(dsn, autocommit=True) as admin:
                        for name in ("anon", "authenticated", "service_role"):
                            exists = admin.execute(
                                "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,),
                            ).fetchone()
                            if not exists:
                                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(name)))
                        admin.execute("ALTER ROLE service_role BYPASSRLS")
                    break
                except psycopg.OperationalError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("PostgreSQL did not become ready within 30 seconds") from None
                    time.sleep(0.1)
            yield dsn
        except (OSError, subprocess.SubprocessError, RuntimeError, TimeoutError) as exc:
            log.flush()
            tail = logfile.read_text(encoding="utf-8", errors="replace")[-6000:]
            pytest.fail(f"Local PostgreSQL harness failed: {exc}\n{tail}")
        finally:
            try:
                if process is not None and process.poll() is None:
                    try:
                        subprocess.run(
                            [str(binaries / "pg_ctl.exe"), "stop", "-D", str(data),
                             "-m", "immediate", "-w", "-t", "15"],
                            stdout=log, stderr=subprocess.STDOUT, env=env, timeout=20, check=True,
                        )
                    finally:
                        if process.poll() is None:
                            # Restrict forced cleanup to the exact process tree we spawned.
                            subprocess.run(
                                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                stdout=log, stderr=subprocess.STDOUT, timeout=15, check=False,
                            )
                        process.wait(timeout=15)
            finally:
                log.close()
                shutil.rmtree(root)


@pytest.fixture
def pg_admin(pg_cluster_dsn: str) -> Iterator[psycopg.Connection]:
    """Each test owns a new database; cluster roles intentionally persist."""
    name = f"ghi_test_{uuid.uuid4().hex}"
    with psycopg.connect(pg_cluster_dsn, autocommit=True) as cluster:
        cluster.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        try:
            with psycopg.connect(pg_cluster_dsn, dbname=name, autocommit=True) as connection:
                yield connection
        finally:
            cluster.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
