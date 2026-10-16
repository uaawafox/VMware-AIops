"""HLD §18 D-22: the role metadata this skill declares matches its real code."""

from __future__ import annotations

import asyncio

import pytest

from vmware_aiops import policy_roles
from vmware_aiops.ops.plan_executor import dispatch_actions
from vmware_aiops.ops.plan_gate import REFUSED_ACTIONS


def _registry() -> set[str]:
    from vmware_aiops.mcp_server.server import mcp

    return {t.name for t in asyncio.run(mcp.list_tools())}


@pytest.mark.unit
def test_every_plan_action_is_mapped_or_refused():
    """A new plan action must name the tool it amounts to, or a role could reach it unlisted."""
    missing = dispatch_actions() - set(policy_roles.ACTION_TOOLS) - set(REFUSED_ACTIONS)
    assert not missing, f"map these plan actions in policy_roles.ACTION_TOOLS: {sorted(missing)}"


@pytest.mark.unit
def test_every_declared_name_is_a_real_tool():
    tools = _registry()
    named = set(policy_roles.ESCAPES) | set(policy_roles.REACHES)
    for reached in policy_roles.REACHES.values():
        named |= reached
    named |= set(policy_roles.ACTION_TOOLS.values())
    assert named - tools == set(), sorted(named - tools)


@pytest.mark.unit
def test_plans_reach_delete_through_rollback():
    assert "delete_vm" in policy_roles.REACHES["apply_vm_plan"]
    assert "delete_vm" in policy_roles.REACHES["set_vm_ttl"]


@pytest.mark.unit
def test_metadata_is_declared_when_policy_has_roles():
    roles = pytest.importorskip("vmware_policy.roles")
    meta = roles.metadata("aiops")
    assert meta is not None and meta.version == 1
    assert meta.reaches["apply_vm_plan"] == policy_roles.REACHES["apply_vm_plan"]
    assert "run_vm_guest_command" in meta.escapes
