import json

import pytest

from github_intelligence.models import RepositoryConfig
from github_intelligence.repositories import RegistryError, RepositoryRegistry


def test_selection_can_contain_multiple_repositories_and_reload(tmp_path):
    path = tmp_path / "repositories.json"
    registry = RepositoryRegistry(path)
    registry.add(RepositoryConfig(full_name="a/one", github_id=1))
    registry.add(RepositoryConfig(full_name="a/two", github_id=2))
    assert [repo.full_name for repo in RepositoryRegistry(path).active()] == ["a/one", "a/two"]


def test_deactivate_remove_and_reactivate_keep_selection_record(tmp_path):
    registry = RepositoryRegistry(tmp_path / "repositories.json")
    registry.add(RepositoryConfig(full_name="a/one", github_id=1))
    registry.set_status("a/one", "inactive", client_id="personal", project_id="pilot")
    assert registry.active() == []
    registry.set_status("a/one", "removed", client_id="personal", project_id="pilot")
    assert registry.entries()[0].status == "removed"
    registry.add(RepositoryConfig(full_name="a/one", github_id=1))
    assert len(registry.entries()) == 1
    assert registry.active()[0].full_name == "a/one"


def test_repository_cannot_leak_into_other_project_scope(tmp_path):
    registry = RepositoryRegistry(tmp_path / "repositories.json")
    registry.add(RepositoryConfig(full_name="a/one", client_id="client-a", project_id="p1"))
    with pytest.raises(RegistryError):
        registry.set_status("a/one", "removed", client_id="client-b", project_id="p1")
    assert registry.active()[0].status == "active"


def test_rename_uses_github_id_and_readding_does_not_duplicate(tmp_path):
    registry = RepositoryRegistry(tmp_path / "repositories.json")
    registry.add(RepositoryConfig(full_name="a/old", github_id=100))
    registry.add(RepositoryConfig(full_name="a/new", github_id=100))
    assert len(registry.entries()) == 1
    assert registry.active()[0].full_name == "a/new"


def test_corrupt_registry_is_not_empty_success(tmp_path):
    path = tmp_path / "repositories.json"
    path.write_text('{"repositories":"invalid"}', encoding="utf-8")
    with pytest.raises(RegistryError):
        RepositoryRegistry(path).entries()
    assert json.loads(path.read_text())["repositories"] == "invalid"


def test_linking_verified_repository_preserves_existing_monitoring_settings(tmp_path):
    registry = RepositoryRegistry(tmp_path / "repositories.json")
    registry.add(RepositoryConfig(full_name="a/one", github_id=1, staging_branch="qa", required_approvals=7))
    registry.link_verified(RepositoryConfig(full_name="a/one", github_id=1, github_installation_id=123))
    existing = registry.active()[0]
    assert existing.staging_branch == "qa"
    assert existing.required_approvals == 7
    assert existing.github_installation_id == 123
    registry.set_status("a/one", "removed", client_id="personal", project_id="pilot")
    before = registry.path.read_bytes()
    with pytest.raises(RegistryError):
        registry.link_verified(RepositoryConfig(full_name="a/one", github_id=1, github_installation_id=123))
    assert registry.path.read_bytes() == before


def test_cli_style_readding_cannot_erase_an_existing_installation(tmp_path):
    registry = RepositoryRegistry(tmp_path / "repositories.json")
    registry.add(RepositoryConfig(full_name="a/one", github_id=1, github_installation_id=123))
    registry.add(RepositoryConfig(full_name="a/one", github_id=1))
    assert registry.entries()[0].github_installation_id == 123
