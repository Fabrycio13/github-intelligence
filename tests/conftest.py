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
        pytest.skip(f"{reason}; run uv run python scripts/db/prepare_test_postgres.py")
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
    ctl_log = root / "pg_ctl.log"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    server_pid: int | None = None
    pid_file = data / "postmaster.pid"
    try:
        with ctl_log.open("wb") as clog:
            subprocess.run(
                [str(binaries / "initdb.exe"), "-D", str(data), "-U", "postgres",
                 "--locale=C", "--encoding=UTF8", "--auth=trust", "--no-instructions"],
                check=True, stdout=clog, stderr=subprocess.STDOUT, env=env, timeout=60,
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
        opts = f"-h 127.0.0.1 -p {port} -c unix_socket_directories= -c logging_collector=off"
        with ctl_log.open("ab") as clog:
            subprocess.run(
                [str(binaries / "pg_ctl.exe"), "start",
                 "-D", str(data),
                 "-l", str(logfile),
                 "-o", opts,
                 "-w", "-t", "30"],
                stdout=clog, stderr=subprocess.STDOUT, env=env, timeout=35, check=True,
            )
        if pid_file.is_file():
            try:
                server_pid = int(pid_file.read_text(encoding="ascii").splitlines()[0].strip())
            except (ValueError, IndexError):
                server_pid = None

        deadline = time.monotonic() + 30
        while True:
            if not pid_file.is_file():
                raise RuntimeError("PostgreSQL postmaster exited unexpectedly (postmaster.pid missing)")
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
        diagnostics: list[str] = []
        if logfile.is_file():
            diagnostics.append(
                f"--- postgres.log ---\n{logfile.read_text(encoding='utf-8', errors='replace')[-4000:]}"
            )
        if ctl_log.is_file():
            diagnostics.append(
                f"--- pg_ctl.log ---\n{ctl_log.read_text(encoding='utf-8', errors='replace')[-2000:]}"
            )
        tail = "\n".join(diagnostics)
        pytest.fail(f"Local PostgreSQL harness failed: {exc}\n{tail}")
    finally:
        try:
            with ctl_log.open("ab") as clog:
                # 1. Standard controlled shutdown via pg_ctl stop
                subprocess.run(
                    [str(binaries / "pg_ctl.exe"), "stop", "-D", str(data),
                     "-m", "immediate", "-w", "-t", "15"],
                    stdout=clog, stderr=subprocess.STDOUT, env=env, timeout=20, check=False,
                )
                # 2. Only if postmaster.pid persists, verify PID and data directory ownership before taskkill
                if pid_file.is_file() and server_pid is not None:
                    try:
                        lines = pid_file.read_text(encoding="ascii").splitlines()
                        live_pid = int(lines[0].strip()) if lines else None
                        live_data = Path(lines[1].strip()).resolve() if len(lines) > 1 else None
                    except (OSError, ValueError):
                        live_pid = None
                        live_data = None
                    if live_pid == server_pid and live_data == data.resolve():
                        subprocess.run(
                            ["taskkill.exe", "/PID", str(server_pid), "/T", "/F"],
                            stdout=clog, stderr=subprocess.STDOUT, timeout=15, check=False,
                        )
        finally:
            last_err: OSError | None = None
            for _ in range(5):
                try:
                    shutil.rmtree(root)
                    last_err = None
                    break
                except OSError as err:
                    last_err = err
                    time.sleep(0.2)
            if last_err is not None:
                raise RuntimeError(
                    f"Failed to remove disposable PostgreSQL directory {root}: {last_err}"
                )


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


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail CI if any integration test is skipped or if zero integration tests run when GHI_PG_BIN is set."""
    if os.environ.get("GHI_PG_BIN"):
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            skips = [
                r for r in reporter.getreports("skipped")
                if "tests/integration" in str(getattr(r, "fspath", "")).replace("\\", "/")
            ]
            if skips:
                reporter.write_sep(
                    "=", "FAILED: SKIPPED INTEGRATION TESTS NOT PERMITTED", red=True, bold=True,
                )
                for r in skips:
                    reporter.write_line(f"Skipped: {r.nodeid} - {r.longrepr}")
                session.exitstatus = 1
            passed = [
                r for r in reporter.getreports("passed")
                if "tests/integration" in str(getattr(r, "fspath", "")).replace("\\", "/")
            ]
            targeted_integration = any(
                "tests/integration" in str(arg).replace("\\", "/") for arg in session.config.args
            )
            if targeted_integration and not passed:
                reporter.write_sep("=", "FAILED: NO INTEGRATION TESTS PASSED", red=True, bold=True)
                session.exitstatus = 1
