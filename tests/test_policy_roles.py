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


#: ops function a batch calls → the tool that does the same thing alone.
_OPS_TOOL = {
    "clone_vm": "clone_vm",
    "reconfigure_vm": "reconfigure_vm",
    "create_snapshot": "create_vm_snapshot",
    "power_on_vm": "power_on_vm",
    "attach_iso": "attach_iso_to_vm",
    "linked_clone": "deploy_linked_clone",
    "deploy_from_template": "deploy_vm_from_template",
    "deploy_ova": "deploy_vm_from_ova",
    "create_vm": "create_vm",
}

#: batch tool → its ops function in vmware_aiops/ops/vm_deploy.py
_BATCH_OPS = {
    "batch_clone_vms": "batch_clone",
    "batch_linked_clone_vms": "batch_linked_clone",
    "batch_deploy_from_spec": "batch_deploy",
}


def _calls_in(func_name: str) -> set[str]:
    import ast
    import inspect

    from vmware_aiops.ops import vm_deploy

    tree = ast.parse(inspect.getsource(getattr(vm_deploy, func_name)))
    return {
        n.func.id if isinstance(n.func, ast.Name) else n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }


@pytest.mark.unit
@pytest.mark.parametrize("tool", sorted(_BATCH_OPS))
def test_batch_reach_covers_every_step_it_runs(tool):
    """Review M1: a batch that also reconfigures/snapshots/powers on reaches those tools."""
    called = _calls_in(_BATCH_OPS[tool])
    reached = {_OPS_TOOL[c] for c in called if c in _OPS_TOOL}
    assert reached, f"{_BATCH_OPS[tool]} calls none of the known ops; update _OPS_TOOL"
    assert reached <= policy_roles.REACHES[tool], sorted(reached - policy_roles.REACHES[tool])


@pytest.mark.unit
def test_the_resolver_uses_the_config_the_connection_manager_holds(monkeypatch):
    """Review H1/H-1: once the server's connection manager exists, a config edit must
    not move the role check off the config it connects with; before, read the file."""
    import importlib
    import types

    from vmware_aiops import policy_roles as pr

    server = importlib.import_module("vmware_aiops.mcp_server._shared")
    assert hasattr(server, "_conn_mgr"), "policy_roles looks up a global the server does not have"
    edited, loads = object(), []
    monkeypatch.setattr(pr, "load_config", lambda: loads.append(1) or edited)

    monkeypatch.setattr(server, "_conn_mgr", None)
    assert pr._connection_config() is edited and pr._connection_config() is edited
    assert len(loads) == 2  # no manager yet: the file as it is now, every time

    held = object()
    monkeypatch.setattr(server, "_conn_mgr", types.SimpleNamespace(_config=held))
    assert pr._connection_config() is held  # the manager's copy, not the edited file
