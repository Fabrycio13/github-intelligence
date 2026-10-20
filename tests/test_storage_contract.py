from uuid import uuid4

import pytest
from pydantic import ValidationError

from github_intelligence.storage import ProjectScope


def test_database_scope_requires_explicit_uuid_not_legacy_cli_alias():
    with pytest.raises(ValidationError):
        ProjectScope(client_id="personal", project_id="pilot")
    client, project = uuid4(), uuid4()
    scope = ProjectScope(client_id=client, project_id=project)
    assert scope.client_id == client
    assert scope.project_id == project
    with pytest.raises(ValidationError):
        scope.client_id = uuid4()
