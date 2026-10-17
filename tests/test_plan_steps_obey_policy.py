"""Every plan step is authorized by vmware-policy under its own tool's name.

Real hardware, 2026-10-17 (vmware-aiops 2.4.0, home-vcenter): the lab's
``~/.vmware/rules.yaml`` denies ``delete_vm`` (rule
``no-vm-destruction-in-lab``), yet a plan step ``delete_vm`` would run.
``apply_vm_plan`` / ``rollback_vm_plan`` are checked by the policy only under
their own names; ``plan_gate.check_step`` never asked the policy, and the
executor calls ``delete_vm_acknowledged`` directly. The acknowledgement
(instance UUID, disk count, snapshot count) is readable from read-only tools, so
it is not a barrier. The TTL daemon already asks the policy as ``delete_vm``;
plans now do the same, for every step, forward and rollback, at preview and
just before the step runs, and file a ``denied`` audit row when they refuse.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.plan as plan_tools
from vmware_aiops.ops import inventory, plan_executor, plan_gate, planner, vm_lifecycle
from vmware_aiops.ops.gate import GateRefusedError
from vmware_aiops.policy_roles import ACTION_TOOLS

ON = vim.VirtualMachine.PowerState.poweredOn


def _rules(home, *operations: str) -> None:
    ops = ", ".join(f'"{o}"' for o in operations)
    (home / "rules.yaml").write_text(
        "deny:\n"
        "  - name: no-vm-destruction-in-lab\n"
        f"    operations: [{ops}]\n"
        '    reason: "Not through an agent in this lab."\n',
        encoding="utf-8",
    )
    reset_policy_engine()


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    monkeypatch.setattr(plan_tools, "_get_connection", lambda _t=None: MagicMock(name="si"))
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda _t, **_k: None)
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    yield tmp_path
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def rows(monkeypatch):
    captured: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            captured.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return captured


def _vm(name):
    vm = MagicMock(name=f"vm:{name}")
    vm.name = name
    vm.runtime.powerState = ON
    vm.runtime.host.name = "esx-01"
    vm.guest.toolsRunningStatus = "guestToolsRunning"
    vm.config.instanceUuid = f"uuid-{name}"
    vm.config.hardware.device = []
    vm.datastore = []
    vm.snapshot = None
    return vm


@pytest.fixture
def world(monkeypatch):
    state = {"vms": [_vm("web-01")]}

    def collect(_si, types, _p):
        pool = state["vms"] if vim.VirtualMachine in types else []
        return [(o, {"name": o.name}) for o in pool]

    monkeypatch.setattr(inventory, "_collect", collect)
    return state


@pytest.fixture
def dispatched(monkeypatch):
    calls: list = []
    monkeypatch.setattr(plan_executor, "_dispatch",
                        lambda _si, a, p: calls.append(("do", a)) or "ok")
    monkeypatch.setattr(plan_executor, "_rollback_dispatch",
                        lambda _si, a, p: calls.append(("undo", a)) or "ok")
    monkeypatch.setattr(plan_executor, "_created_instance_uuid", lambda _si, _n: "uuid-new")
    return calls


def _write_plan(steps, status="pending", plan_id="plan-p-0001"):
    planner._PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plan = {"plan_id": plan_id, "created_at": "2026-10-17T00:00:00+00:00",
            "target": "vc01", "status": status, "steps": steps,
            "summary": {"total_steps": len(steps)}}
    (planner._PLANS_DIR / f"{plan_id}.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan_id


def _step(index, action, params, status="pending"):
    rb_action, rb_params = planner._build_rollback(action, params)
    return {"index": index, "action": action, "params": params,
            "rollback_action": rb_action, "rollback_params": rb_params,
            "status": status, "result": None, "executed_at": None}


def _delete_step():
    ack = {"instance_uuid": "uuid-web-01", "disk_count": 0, "snapshot_count": 0}
    return _step(0, "delete_vm", {"vm_name": "web-01", "acknowledge_blast_radius": ack})


# ── forward steps ────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("rule_name", ["delete_vm", "vm_delete"])  # the lab writes the 1.x name
def test_a_denied_delete_vm_step_blocks_the_preview(harness, world, dispatched, rule_name):
    _rules(harness, rule_name)
    pid = _write_plan([_delete_step()])

    br = plan_tools.apply_vm_plan(pid, target="vc01")["blast_radius"]

    assert any("no-vm-destruction-in-lab" in b and "delete_vm" in b for b in br["blockers"])


@pytest.mark.unit
def test_a_denied_delete_vm_step_is_refused_at_apply(harness, world, dispatched, rows):
    _rules(harness, "delete_vm")
    pid = _write_plan([_delete_step()])

    out = plan_tools.apply_vm_plan(pid, confirm=True, target="vc01")

    assert dispatched == []
    assert "error" in out
    assert any(r["tool"] == "delete_vm" and r["status"] == "denied" for r in rows)


@pytest.mark.unit
def test_a_denied_power_off_step_is_refused_at_preview_and_apply(harness, world, dispatched):
    _rules(harness, "power_off_vm")
    pid = _write_plan([_step(0, "power_off", {"vm_name": "web-01"})])

    br = plan_tools.apply_vm_plan(pid, target="vc01")["blast_radius"]
    out = plan_tools.apply_vm_plan(pid, confirm=True, target="vc01")

    assert any("power_off_vm" in b for b in br["blockers"])
    assert dispatched == [] and "error" in out


@pytest.mark.unit
def test_a_non_destructive_step_is_authorized_too(harness, world, dispatched):
    """create_vm runs unmeasured; a deny on create_vm must still stop it."""
    _rules(harness, "create_vm")
    pid = _write_plan([_step(0, "create_vm", {"vm_name": "new-01"})])

    out = plan_tools.apply_vm_plan(pid, confirm=True, target="vc01")

    assert dispatched == [] and "error" in out


@pytest.mark.unit
def test_check_step_asks_the_policy_just_before_the_step_runs(harness, world, rows):
    """A rule added between preview and apply, or a deferred step, still refuses."""
    _rules(harness, "delete_vm")
    with pytest.raises(GateRefusedError, match="no-vm-destruction-in-lab"):
        plan_gate.check_step(MagicMock(), _delete_step(), target="vc01")
    assert any(r["tool"] == "delete_vm" and r["status"] == "denied" for r in rows)


# ── rollback steps ───────────────────────────────────────────────────────────


def _failed_after_create():
    created = _step(0, "create_vm", {"vm_name": "new-01"}, status="success")
    created["rollback_params"] = {**created["rollback_params"], "instance_uuid": "uuid-new"}
    failed = _step(1, "power_on", {"vm_name": "new-01"}, status="failed")
    return _write_plan([created, failed], status="failed")


@pytest.mark.unit
def test_a_denied_rollback_delete_is_refused(harness, world, dispatched):
    _rules(harness, "delete_vm")
    pid = _failed_after_create()

    br = plan_tools.rollback_vm_plan(pid, target="vc01")["blast_radius"]
    out = plan_tools.rollback_vm_plan(pid, confirm=True, target="vc01")

    assert any("delete_vm" in b and "no-vm-destruction-in-lab" in b for b in br["blockers"])
    assert dispatched == [] and "error" in out


# ── controls ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_without_rules_the_same_plan_runs(world, dispatched):
    pid = _write_plan([_step(0, "power_off", {"vm_name": "web-01"})])

    out = plan_tools.apply_vm_plan(pid, confirm=True, target="vc01")

    assert out["status"] == "completed" and dispatched == [("do", "power_off")]


@pytest.mark.unit
def test_a_rule_on_another_tool_does_not_refuse(harness, world, dispatched):
    _rules(harness, "delete_namespace")
    pid = _write_plan([_step(0, "power_off", {"vm_name": "web-01"})])

    out = plan_tools.apply_vm_plan(pid, confirm=True, target="vc01")

    assert out["status"] == "completed"


@pytest.mark.unit
def test_each_step_is_checked_at_its_own_tools_risk_level() -> None:
    """A maintenance window blocks high/critical ops, so the risk must be the tool's."""
    from vmware_aiops.mcp_server.server import mcp

    registered = {t.name for t in asyncio.run(mcp.list_tools())}
    from vmware_aiops.mcp_server import tools as tool_pkg  # noqa: F401 -- registers tools

    for action, tool in ACTION_TOOLS.items():
        assert tool in registered, f"{action} maps to unknown tool {tool}"
        fn = _tool_function(tool)
        assert plan_gate.step_risk_level(action) == fn._risk_level, action


def _tool_function(name: str):
    import importlib
    import pkgutil

    import vmware_aiops.mcp_server.tools as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
        fn = getattr(mod, name, None)
        if fn is not None and hasattr(fn, "_risk_level"):
            return fn
    raise AssertionError(f"no decorated tool function named {name}")


# ── a plan that failed says so, to the agent and to the audit trail ─────────


@pytest.mark.unit
def test_a_failed_apply_is_audited_as_an_error(world, monkeypatch, rows):
    def boom(_si, action, _params):
        raise RuntimeError("Task failed")

    monkeypatch.setattr(plan_executor, "_dispatch", boom)
    pid = _write_plan([_step(0, "power_on", {"vm_name": "web-01"})])

    out = plan_tools.apply_vm_plan(pid, confirm=True, target="vc01")

    assert out["status"] == "failed" and out.get("error")
    assert rows[-1]["tool"] == "apply_vm_plan" and rows[-1]["status"] == "error"


@pytest.mark.unit
def test_an_invalid_plan_is_audited_as_an_error(rows):
    out = plan_tools.create_vm_plan([{"action": "no_such_action"}], target="vc01")

    assert out.get("error") and out.get("errors")
    assert rows[-1]["tool"] == "create_vm_plan" and rows[-1]["status"] == "error"


@pytest.mark.unit
def test_a_rollback_with_a_failed_undo_is_an_error(world, monkeypatch, rows):
    def undo(_si, action, _params):
        raise RuntimeError("Task failed")

    monkeypatch.setattr(plan_executor, "_rollback_dispatch", undo)
    created = _step(0, "power_on", {"vm_name": "web-01"}, status="success")
    pid = _write_plan([created, _step(1, "power_on", {"vm_name": "x"}, status="failed")],
                      status="failed")

    out = plan_tools.rollback_vm_plan(pid, confirm=True, target="vc01")

    assert out.get("error")
    assert rows[-1]["tool"] == "rollback_vm_plan" and rows[-1]["status"] == "error"
