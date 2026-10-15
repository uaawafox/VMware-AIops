"""D-17: get_access_info answers who the server acts as and whether a tool's privileges are held."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from vmware_aiops.access_requirements import REQUIREMENTS
from vmware_aiops.cli import access as cli_access
from vmware_aiops.cli import app
from vmware_aiops.mcp_server import server  # noqa: F401 — registers every tool
from vmware_aiops.mcp_server._shared import mcp
from vmware_aiops.mcp_server.tools import access

pytestmark = pytest.mark.unit

_READ = {"System.View", "System.Read"}


def _fake_si(granted: set[str]) -> MagicMock:
    si = MagicMock()
    si.content.sessionManager.currentSession = SimpleNamespace(
        key="s-1", userName="VSPHERE.LOCAL\\svc"
    )

    def _has(entity, sessionId, privId):  # noqa: N803 — pyVmomi's keyword names
        avail = [SimpleNamespace(privId=p, isGranted=p in granted) for p in privId]
        return [SimpleNamespace(privAvailability=avail)]

    am = si.content.authorizationManager
    am.HasPrivilegeOnEntities.side_effect = _has
    am.roleList = [SimpleNamespace(roleId=-2, name="ReadOnly")]
    am.RetrieveAllPermissions.return_value = []
    return si


def test_requirements_cover_exactly_the_registered_tools() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert len(names) > 50
    assert set(REQUIREMENTS) == names


def test_get_access_info_is_read_only() -> None:
    tool = {t.name: t for t in asyncio.run(mcp.list_tools())}["get_access_info"]
    assert tool.annotations.readOnlyHint is True


def test_privilege_ids_are_single_ids() -> None:
    """vCenter rejects an unknown id in the batch, so no "A B" pairs or blanks."""
    for name, privs in REQUIREMENTS.items():
        for p in privs or []:
            assert p and " " not in p and p.count(".") >= 1, (name, p)


@pytest.fixture
def _wired(monkeypatch):
    target = SimpleNamespace(name="vc1", type="vcenter")
    cfg = SimpleNamespace(get_target=lambda n: target, default_target=target)

    def wire(si: MagicMock) -> None:
        monkeypatch.setattr(access, "_get_connection", lambda target=None: si)
        monkeypatch.setattr(access, "_ensure_conn_mgr", lambda: SimpleNamespace(_config=cfg))

    return wire


def test_read_only_account_cannot_clone(_wired) -> None:
    _wired(_fake_si(_READ))
    out = access.get_access_info(tool="clone_vm")
    assert out["tool"]["verdict"] == "not_granted_at_root"
    assert "VirtualMachine.Inventory.CreateFromExisting" in out["tool"]["missing_at_root"]
    assert access.get_access_info(tool="list_vcenter_alarms")["tool"]["verdict"] == (
        "granted_at_root"
    )


def test_local_only_tool_needs_no_privilege(_wired) -> None:
    _wired(_fake_si(set()))
    out = access.get_access_info(tool="list_vm_plans")
    assert out["tool"]["verdict"] == "no_platform_privilege_needed"


def test_summary_sorts_every_tool(_wired) -> None:
    _wired(_fake_si(_READ))
    out = access.get_access_info()
    sorted_tools = (
        out["tools_granted_at_root"]
        + out["tools_not_granted_at_root"]
        + out["tools_undetermined"]
        + out["tools_no_privilege_needed"]
    )
    assert sorted(sorted_tools) == sorted(REQUIREMENTS)
    assert "delete_vm" in out["tools_not_granted_at_root"]


def test_failed_lookup_is_undetermined(_wired) -> None:
    si = _fake_si(set())
    si.content.authorizationManager.HasPrivilegeOnEntities.side_effect = RuntimeError("x")
    _wired(si)
    assert access.get_access_info(tool="power_on_vm")["tool"]["verdict"] == "undetermined"


def test_unknown_tool_is_a_teaching_error(_wired) -> None:
    _wired(_fake_si(set()))
    out = access.get_access_info(tool="vm_clone")
    assert "clone_vm" in out["error"]


def test_cli_access_info_prints_the_verdict(monkeypatch) -> None:
    si = _fake_si(_READ)
    cfg = SimpleNamespace(default_target=SimpleNamespace(name="vc1"))
    monkeypatch.setattr(cli_access, "_get_connection", lambda target, config: (si, cfg))
    result = CliRunner().invoke(app, ["access-info", "--tool", "power_on_vm"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["target"] == "vc1"
    assert out["tool"]["missing_at_root"] == ["VirtualMachine.Interact.PowerOn"]


def test_cli_unknown_tool_exits_without_connecting(monkeypatch) -> None:
    monkeypatch.setattr(cli_access, "_get_connection", lambda *a: pytest.fail("must not connect"))
    result = CliRunner().invoke(app, ["access-info", "--tool", "vm_clone"])
    assert result.exit_code == 1
    assert "clone_vm" in result.output


def test_unnameable_permission_is_undetermined_not_granted(_wired) -> None:
    """ping_from_vmk's deciding permission has no privilege id: never report it granted."""
    assert REQUIREMENTS["ping_from_vmk"] is None
    _wired(_fake_si(_READ))
    assert access.get_access_info(tool="ping_from_vmk")["tool"]["verdict"] == "undetermined"


def test_no_write_tool_is_granted_to_a_read_only_account(_wired) -> None:
    """Every write tool needs something beyond System.View/System.Read, or is undetermined."""
    writes = {
        t.name
        for t in asyncio.run(mcp.list_tools())
        if not (t.annotations and t.annotations.readOnlyHint)
    }
    local = {n for n, privs in REQUIREMENTS.items() if privs == []}
    # Writes local plan files only; on vSphere it reads the inventory it validates against.
    local.add("create_vm_plan")
    _wired(_fake_si(_READ))
    granted = set(access.get_access_info()["tools_granted_at_root"])
    assert not (granted & (writes - local)), sorted(granted & writes)
