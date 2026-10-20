import json

from github_intelligence.__main__ import main


def test_report_uses_the_pinned_installation_for_each_repository(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import github_intelligence.__main__ as cli
    import github_intelligence.collect as collect
    from github_intelligence.models import RepositorySnapshot

    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"repositories": [
        {"full_name": "a/one", "github_id": 1, "github_installation_id": 11},
        {"full_name": "b/two", "github_id": 2, "github_installation_id": 22},
    ]}), encoding="utf-8")
    seen = []

    @contextmanager
    def client(public, *, installation_id=None):
        seen.append(installation_id)
        yield SimpleNamespace(authenticated=True, installation_id=installation_id)

    def snapshot(github, repo, window):
        assert github.installation_id == repo.github_installation_id
        return RepositorySnapshot(
            config=repo, github_id=repo.github_id, observed_at=datetime.now(timezone.utc), authenticated=True,
        )

    monkeypatch.setattr(cli, "_client", client)
    monkeypatch.setattr(collect, "collect_repository", snapshot)
    assert main(["--config", str(config), "report", "--since", "2026-10-01T00:00:00Z",
                 "--until", "2026-10-19T00:00:00Z", "--output", str(tmp_path / "report.md")]) == 0
    assert seen == [11, 22]


def test_cli_lists_selections_without_network(tmp_path, capsys):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"repositories": [{"full_name": "a/b"}]}), encoding="utf-8")
    assert main(["--config", str(config), "repos", "list"]) == 0
    assert "a/b" in capsys.readouterr().out


def test_cli_rejects_future_report_window_before_network(tmp_path, capsys):
    assert (
        main(
            [
                "--public",
                "--config",
                str(tmp_path / "repos.json"),
                "report",
                "--since",
                "2099-01-01T00:00:00Z",
                "--until",
                "2099-01-02T00:00:00Z",
                "--output",
                str(tmp_path / "report.md"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "report.md").exists()
    assert "futuro" in capsys.readouterr().err


def test_cli_does_not_collect_inactive_selected_repository(tmp_path, capsys):
    config = tmp_path / "repos.json"
    config.write_text(
        json.dumps({"repositories": [{"full_name": "a/b", "status": "inactive"}]}), encoding="utf-8"
    )
    assert (
        main(
            [
                "--public",
                "--config",
                str(config),
                "report",
                "--repo",
                "a/b",
                "--since",
                "2026-01-01T00:00:00Z",
                "--until",
                "2026-01-02T00:00:00Z",
                "--output",
                str(tmp_path / "report.md"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "report.md").exists()
    assert "ativo" in capsys.readouterr().err


def test_cli_requires_aware_time_before_network(tmp_path, capsys):
    assert (
        main(
            [
                "--public",
                "report",
                "--since",
                "2026-01-01T00:00:00",
                "--until",
                "2026-01-02T00:00:00Z",
                "--output",
                str(tmp_path / "report.md"),
            ]
        )
        == 2
    )
    assert "timezone" in capsys.readouterr().err


def test_deactivation_only_changes_config_and_keeps_history(tmp_path):
    config = tmp_path / "repos.json"
    config.write_text(json.dumps({"repositories": [{"full_name": "a/b"}]}), encoding="utf-8")
    old_report = tmp_path / "history.md"
    old_report.write_text("previous report", encoding="utf-8")
    assert main(["--config", str(config), "repos", "deactivate", "a/b"]) == 0
    assert json.loads(config.read_text())["repositories"][0]["status"] == "inactive"
    assert old_report.read_text() == "previous report"


def test_explicit_public_mode_never_loads_app_secret(monkeypatch):
    import github_intelligence.__main__ as cli

    def forbidden(*args, **kwargs):
        raise AssertionError("Public mode must not read App credentials")

    monkeypatch.setattr(cli.AppSettings, "from_environment", forbidden)
    with cli._client(True) as client:
        assert not client.authenticated


def test_authenticated_cli_passes_only_local_reference_to_settings(monkeypatch):
    from pathlib import Path

    import github_intelligence.__main__ as cli
    import github_intelligence.auth as auth

    received = []
    settings = cli.AppSettings(app_id="123", installation_id=456, private_key=b"SYNTHETIC TEST KEY")

    def load(*, config_path):
        received.append(config_path)
        return settings

    def exchange(app_id, private_key, installation_id):
        assert (app_id, private_key, installation_id) == ("123", b"SYNTHETIC TEST KEY", 456)
        return "synthetic-installation-token"

    monkeypatch.setattr(cli.AppSettings, "from_environment", load)
    monkeypatch.setattr(auth, "installation_token", exchange)
    with cli._client(False) as client:
        assert client.authenticated
    assert received == [Path("config/app.local.json")]
