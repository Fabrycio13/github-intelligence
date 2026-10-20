"""CLI database diagnostic does not require any GitHub credentials or registry."""

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from test_database_health import runtime_database as runtime_database
from test_storage import prepared_db as prepared_db

from github_intelligence.__main__ import main


def test_database_help_is_available_without_github_configuration(capsys):
    with pytest.raises(SystemExit) as result:
        main(["db", "--help"])
    assert result.value.code == 0
    assert "check" in capsys.readouterr().out


def test_cli_database_check_uses_environment_without_optional_config_file(
    runtime_database, tmp_path, monkeypatch, capsys
):
    from github_intelligence import database

    _, runtime, _ = runtime_database
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GHI_DATABASE_HOST", "db.cpkgvpuivjzplerzlpxz.supabase.co")
    monkeypatch.setenv("GHI_DATABASE_PROJECT_REF", "cpkgvpuivjzplerzlpxz")
    monkeypatch.setenv("GHI_DATABASE_USER", "github_intelligence_worker")
    monkeypatch.setenv("GHI_DATABASE_PASSWORD", "SYNTHETIC_TEST_PASSWORD")

    @contextmanager
    def local_connection(settings):
        # Network/TLS parameter enforcement is covered by database.py tests.
        # This test runs the CLI and its real permission diagnostic on local PG.
        assert settings.user == "github_intelligence_worker"
        yield runtime

    monkeypatch.setattr(database, "connect_database", local_connection)
    assert main(["db", "check"]) == 0
    captured = capsys.readouterr()
    assert "10 tabelas conferidas" in captured.out
    assert "SYNTHETIC_TEST_PASSWORD" not in captured.out + captured.err


def test_database_import_defaults_to_explicit_no_write_plan(tmp_path, monkeypatch, capsys):
    import json
    from uuid import uuid4

    import github_intelligence.__main__ as cli

    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "full_name": "owner/repo-a",
                        "github_id": 1001,
                        "client_id": "personal",
                        "project_id": "pilot",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Plan mode must not connect to GitHub or PostgreSQL")

    monkeypatch.setattr(cli, "_client", forbidden)
    assert (
        main(
            [
                "--config",
                str(config),
                "db",
                "import",
                "--repo",
                "owner/repo-a",
                "--client-id",
                str(uuid4()),
                "--project-id",
                str(uuid4()),
                "--since",
                "2026-10-01T00:00:00Z",
                "--until",
                "2026-10-19T00:00:00Z",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Plano" in output
    assert "owner/repo-a" in output
    assert "nenhum dado" in output.lower()


@pytest.mark.parametrize("selected_installation", [None, 2])
def test_apply_import_collects_then_persists_through_cli_on_local_postgres(
    runtime_database, tmp_path, monkeypatch, capsys, selected_installation
):
    import json

    import github_intelligence.__main__ as cli
    import github_intelligence.collect as collect
    import github_intelligence.database as database
    from github_intelligence.config import AppSettings
    from github_intelligence.models import (
        Evidence,
        PullRequestSnapshot,
        RepositoryConfig,
        RepositorySnapshot,
    )

    admin, runtime, ids = runtime_database
    if selected_installation is not None:
        admin.execute(
            "UPDATE github_intelligence.github_installations SET github_installation_id=%s WHERE id=%s",
            (selected_installation, ids["install_a"]),
        )
    config_path = tmp_path / "repos.json"
    config_path.write_text(
        json.dumps(
            {
                "repositories": [
                    {
                        "full_name": "owner/repo-a",
                        "github_id": 1001,
                        "client_id": "personal",
                        "project_id": "pilot",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    if selected_installation is not None:
        doc = json.loads(config_path.read_text(encoding="utf-8"))
        doc["repositories"][0]["github_installation_id"] = selected_installation
        config_path.write_text(json.dumps(doc), encoding="utf-8")
    observed = datetime(2026, 10, 19, tzinfo=timezone.utc)
    config = RepositoryConfig(full_name="owner/repo-a", github_id=1001)
    snapshot = RepositorySnapshot(
        config=config,
        observed_at=observed,
        authenticated=True,
        github_id=1001,
        pulls=[
            PullRequestSnapshot(
                repository=config.full_name,
                observed_at=observed,
                pull={
                    "number": 1,
                    "title": "SYNTHETIC CLI import",
                    "state": "closed",
                    "updated_at": "2026-10-19T00:00:00Z",
                    "merged_at": "2026-10-19T00:00:00Z",
                    "merged": True,
                    "draft": False,
                    "commits": 1,
                    "head": {"sha": "a" * 40, "ref": "feature"},
                    "base": {"sha": "b" * 40, "ref": "main"},
                    "user": {"id": 1, "login": "author"},
                },
                evidence=[
                    Evidence(
                        kind="pull",
                        url="https://github.com/owner/repo-a/pull/1",
                        observed_at=observed,
                    )
                ],
            )
        ],
    )

    @contextmanager
    def fake_client(public, *, installation_id=None):
        assert public is False
        assert installation_id == selected_installation
        yield object()

    @contextmanager
    def local_connection(settings):
        assert settings.user == "github_intelligence_worker"
        yield runtime

    monkeypatch.setattr(cli, "_client", fake_client)
    monkeypatch.setattr(
        cli.AppSettings,
        "from_environment",
        lambda *args, **kwargs: AppSettings(app_id="1", installation_id=1, private_key=b"synthetic"),
    )
    monkeypatch.setattr(collect, "collect_repository", lambda client, repo, window: snapshot)
    monkeypatch.setattr(database, "connect_database", local_connection)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GHI_DATABASE_HOST", "db.cpkgvpuivjzplerzlpxz.supabase.co")
    monkeypatch.setenv("GHI_DATABASE_PROJECT_REF", "cpkgvpuivjzplerzlpxz")
    monkeypatch.setenv("GHI_DATABASE_USER", "github_intelligence_worker")
    monkeypatch.setenv("GHI_DATABASE_PASSWORD", "SYNTHETIC_TEST_PASSWORD")

    assert (
        main(
            [
                "--config",
                str(config_path),
                "db",
                "import",
                "--repo",
                "owner/repo-a",
                "--client-id",
                str(ids["client_a"]),
                "--project-id",
                str(ids["project_a"]),
                "--since",
                "2026-10-01T00:00:00Z",
                "--until",
                "2026-10-20T00:00:00Z",
                "--apply",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Importação succeeded" in output
    assert "SYNTHETIC_TEST_PASSWORD" not in output
    assert admin.execute("SELECT count(*) FROM github_intelligence.pr_snapshots").fetchone() == (1,)
    assert admin.execute("SELECT count(*) FROM github_intelligence.reports").fetchone() == (1,)
