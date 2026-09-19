"""Workspace-scoped external tool servers."""

from cptr.utils.tools import server_allowed_in_workspace


def test_missing_scope_is_global():
    server = {"id": "maps", "enabled": True}
    assert server_allowed_in_workspace(server, None) is True
    assert server_allowed_in_workspace(server, []) is True
    assert server_allowed_in_workspace(server, ["other"]) is True


def test_workspace_scope_requires_attachment():
    server = {"id": "household", "enabled": True, "scope": "workspace"}
    assert server_allowed_in_workspace(server, None) is False
    assert server_allowed_in_workspace(server, []) is False
    assert server_allowed_in_workspace(server, ["maps"]) is False
    assert server_allowed_in_workspace(server, ["household"]) is True


def test_disabled_server_is_hidden():
    server = {"id": "household", "enabled": False, "scope": "global"}
    assert server_allowed_in_workspace(server, None) is False
