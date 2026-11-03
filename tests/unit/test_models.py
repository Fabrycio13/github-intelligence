from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from github_intelligence.models import Evidence, RepositoryConfig, RepositorySnapshot


def test_evidence_requires_timezone_and_source():
    with pytest.raises(ValidationError):
        Evidence(kind="pull", url="", observed_at=datetime(2026, 10, 19))
    evidence = Evidence(
        kind="pull", url="https://github.com/a/b/pull/1", observed_at=datetime.now(timezone.utc)
    )
    assert evidence.kind == "pull"


def test_repository_identifier_is_not_silently_repaired():
    for name in ["https://github.com/a/b", "a/b/", "a/b/c", " a/b", "a/..", "a/b?token=x"]:
        with pytest.raises(ValidationError):
            RepositoryConfig(full_name=name)
    assert RepositoryConfig(full_name="Fabrycio13/project-mds").full_name == "Fabrycio13/project-mds"


def test_empty_snapshot_is_not_marked_authenticated():
    snapshot = RepositorySnapshot(
        config=RepositoryConfig(full_name="a/b"), observed_at=datetime.now(timezone.utc)
    )
    assert snapshot.authenticated is False
    assert snapshot.pulls == []


def test_repository_can_pin_a_verified_installation_without_coercing_ids():
    repo = RepositoryConfig(full_name="a/b", github_installation_id=123)
    assert repo.github_installation_id == 123
    assert RepositoryConfig(full_name="a/b").github_installation_id is None
    for value in (True, 0, -1, "123"):
        with pytest.raises(ValidationError):
            RepositoryConfig(full_name="a/b", github_installation_id=value)
