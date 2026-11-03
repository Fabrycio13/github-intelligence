"""Onboarding public seam; synthetic provider/binder, real local registry."""
import pytest

from github_intelligence.models import RepositoryConfig
from tests.helpers import confirmation, service


def test_inspect_does_not_bind_and_confirm_revalidates_before_writing(tmp_path):
    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    result = onboarding.inspect({"link": "https://github.com/owner/repo"})
    assert result["status"] == "ready"
    assert result["can_bind"] is True
    assert binder.writes == []
    assert registry.entries() == []
    result = onboarding.bind(confirmation("https://github.com/owner/repo"))
    assert result["status"] == "bound"
    assert len(access.checked) == 2
    assert len(binder.writes) == 1
    entry = registry.entries()[0]
    assert entry.github_id == 10
    assert entry.github_installation_id == 30


def test_missing_authorization_or_database_permission_never_binds(tmp_path):
    from github_intelligence.onboarding import OnboardingError

    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    access.ready = False
    assert onboarding.bind(confirmation())["status"] == "authorization_required"
    assert binder.writes == []
    access.ready = True
    binder.enabled = False
    assert onboarding.inspect({"link": "owner/repo"})["can_bind"] is False
    with pytest.raises(OnboardingError, match="ONBOARDING_PERMISSION_REQUIRED"):
        onboarding.bind(confirmation())
    assert binder.writes == []
    assert registry.entries() == []


def test_existing_local_settings_preserved_and_inactive_not_reactivated(tmp_path):
    from github_intelligence.onboarding import OnboardingError

    onboarding, _, binder, registry = service(tmp_path / "repos.json")
    registry.add(RepositoryConfig(
        full_name="owner/repo", github_id=10, required_approvals=9, staging_branch="qa",
    ))
    onboarding.bind(confirmation())
    entry = registry.entries()[0]
    assert entry.required_approvals == 9
    assert entry.staging_branch == "qa"
    registry.set_status("owner/repo", "inactive", client_id="personal", project_id="pilot")
    with pytest.raises(OnboardingError):
        onboarding.bind(confirmation())
    assert len(binder.writes) == 1


def test_local_write_failure_reports_committed_binding_without_claiming_full_success(tmp_path, monkeypatch):
    from github_intelligence.onboarding import OnboardingError
    from github_intelligence.repositories import RegistryError

    onboarding, _, binder, registry = service(tmp_path / "repos.json")

    def fail(entry):
        raise RegistryError("synthetic local lock conflict")

    monkeypatch.setattr(registry, "link_verified", fail)
    with pytest.raises(OnboardingError, match="Vínculo salvo no banco"):
        onboarding.bind(confirmation())
    assert len(binder.writes) == 1
    assert registry.entries() == []


@pytest.mark.parametrize("changed", ["github_id", "app_id", "installation_id"])
def test_confirmation_refuses_identity_changed_since_inspection(tmp_path, monkeypatch, changed):
    from github_intelligence.onboarding import OnboardingError

    onboarding, access, binder, registry = service(tmp_path / "repos.json")
    inspected = onboarding.inspect({"link": "owner/repo"})
    expected = {key: inspected[key] for key in ("full_name", "github_id", "app_id", "installation_id")}
    current = {key: value for key, value in inspected.items() if key != "can_bind"}
    current[changed] += 1
    monkeypatch.setattr(access, "inspect", lambda link: current)
    with pytest.raises(OnboardingError, match="mudou"):
        onboarding.bind({"link": "owner/repo", "expected": expected})
    assert binder.writes == []
    assert registry.entries() == []


def test_binding_without_the_displayed_identity_is_rejected(tmp_path):
    from github_intelligence.onboarding import OnboardingError

    onboarding, _, binder, registry = service(tmp_path / "repos.json")
    with pytest.raises(OnboardingError) as error:
        onboarding.bind({"link": "owner/repo"})
    assert error.value.status == 400
    assert binder.writes == []
    assert registry.entries() == []
