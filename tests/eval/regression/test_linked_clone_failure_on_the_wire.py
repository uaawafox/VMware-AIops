"""deploy_linked_clone failures must arrive as failures on the MCP wire.

Write round 2026-10-17 (2.4.0 wheel): "Source VM 'x' not found." and
"Snapshot 'current' not found. Available: base" came back with isError false and
an audit row of ok, so vmware-pilot -- which reads isError / a top-level
"error" -- recorded the clone step as success, and a workflow whose last step
was that clone ended "completed". The ops layer now raises DeployError; this
pins what a client sees through FastMCP's real request handler.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import mcp.types as mcp_types
import pytest
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.deploy as deploy_tools
from vmware_aiops.mcp_server.server import mcp
from vmware_aiops.ops import vm_deploy, vm_lifecycle


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    monkeypatch.setattr(deploy_tools, "_get_connection", lambda target=None: MagicMock())
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


def _call(**arguments) -> mcp_types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name="deploy_linked_clone", arguments=arguments),
    )
    return asyncio.run(handler(request)).root


def _text(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, mcp_types.TextContent))


@pytest.mark.unit
def test_a_missing_source_vm_is_an_error_frame(monkeypatch) -> None:
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: None)

    result = _call(source_vm_name="x", snapshot_name="base", new_name="c1", target="t")

    assert result.isError is True
    assert "Source VM 'x' not found" in _text(result)


@pytest.mark.unit
def test_a_missing_snapshot_is_an_error_frame(monkeypatch) -> None:
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: MagicMock())
    monkeypatch.setattr(vm_lifecycle, "list_snapshots", lambda si, n: [{"name": "base"}])

    result = _call(source_vm_name="src", snapshot_name="current", new_name="c1", target="t")

    assert result.isError is True
    assert "Available: base" in _text(result)
