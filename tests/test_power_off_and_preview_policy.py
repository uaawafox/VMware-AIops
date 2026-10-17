"""Follow-ups to the 2026-10-17 composite-tool review.

1. A preview tells the truth about the policy. restore_vm_clean_slate,
   provision_vm_guest and run_vm_guest_command_with_output were refused by
   rules.yaml only at confirm=True; their previews showed no blocker, so the
   user agreed to something that was then refused.
2. A delete that powers the VM off first is checked as power_off_vm too: the CLI
   ``vm delete``, the TTL daemon and a plan rollback deleting the VM it created
   all powered off inline, and a deny on power_off_vm did not stop them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.guest as guest_tools
import vmware_aiops.mcp_server.tools.ttl as ttl_tools
from vmware_aiops.ops import plan_gate, ttl, vm_lifecycle
from vmware_aiops.ops.gate import GateRefusedError
from vmware_aiops.scanner import scheduler

ON = vim.VirtualMachine.PowerState.poweredOn
OFF = vim.VirtualMachine.PowerState.poweredOff


def _rules(home, *operations):
    ops = ", ".join(f'"{o}"' for o in operations)
    (home / "rules.yaml").write_text(
        f"deny:\n  - name: lab-rule\n    operations: [{ops}]\n    reason: \"Not here.\"\n",
        encoding="utf-8")
    reset_policy_engine()


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    for mod in (guest_tools, ttl_tools):
        monkeypatch.setattr(mod, "_get_connection", lambda target=None: MagicMock())
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


def _vm(state):
    calls: list[str] = []
    vm = SimpleNamespace(
        runtime=SimpleNamespace(powerState=state),
        config=SimpleNamespace(instanceUuid="uuid-1"),
        PowerOff=lambda: calls.append("off") or "task-off",
        Destroy_Task=lambda: calls.append("destroy") or "task-destroy",
    )
    return vm, calls


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)


# ── 1. previews show policy refusals ─────────────────────────────────────────


@pytest.mark.unit
def test_clean_slate_preview_shows_the_revert_deny(harness, monkeypatch, rows):
    _rules(harness, "revert_vm_snapshot")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"vm": n, "blockers": [], "unmeasured": []})

    out = ttl_tools.restore_vm_clean_slate("web-01", target="t")

    assert out["action"] == "preview"
    blockers = out["blast_radius"]["blockers"]
    assert any("revert_vm_snapshot" in b for b in blockers), blockers
    # A preview asks, it does not record a denial: nothing was attempted.
    assert not any(r.get("status") == "denied" for r in rows)


@pytest.mark.unit
def test_clean_slate_preview_keeps_its_own_blockers(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"blockers": ["no snapshot named baseline"],
                                          "unmeasured": []})

    blockers = ttl_tools.restore_vm_clean_slate("web-01", target="t")["blast_radius"]["blockers"]

    assert blockers[0] == "no snapshot named baseline"
    assert any("power_off_vm" in b for b in blockers[1:])


@pytest.mark.unit
def test_clean_slate_preview_without_rules_has_no_policy_blocker(monkeypatch):
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"blockers": [], "unmeasured": []})
    assert ttl_tools.restore_vm_clean_slate("web-01", target="t")["blast_radius"]["blockers"] == []


@pytest.mark.unit
def test_provision_preview_shows_the_guest_command_deny(harness, monkeypatch):
    _rules(harness, "run_vm_guest_command")
    monkeypatch.setattr(guest_tools, "measure_guest_provision",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})

    out = guest_tools.provision_vm_guest("web-01", "root", "s3cret", [{"type": "exec"}],
                                         target="t")

    assert out["action"] == "preview"
    assert any("run_vm_guest_command" in b for b in out["blast_radius"]["blockers"])
    assert "s3cret" not in str(out)


@pytest.mark.unit
def test_with_output_preview_shows_the_download_deny(harness, monkeypatch):
    _rules(harness, "download_vm_guest_file")
    monkeypatch.setattr(guest_tools, "measure_guest_exec_output",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})

    out = guest_tools.run_vm_guest_command_with_output("web-01", "/bin/id", username="root",
                                                       password="pw", target="t")

    assert out["action"] == "preview"
    assert any("download_vm_guest_file" in b for b in out["blast_radius"]["blockers"])


# ── 2. an inline power-off is checked as power_off_vm ────────────────────────


@pytest.mark.unit
def test_ops_delete_asks_before_powering_off(monkeypatch, no_wait):
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)

    def refuse():
        raise GateRefusedError("power_off_vm denied")

    with pytest.raises(GateRefusedError):
        vm_lifecycle.delete_vm(MagicMock(), "web-01", before_power_off=refuse)
    assert calls == []


@pytest.mark.unit
def test_ops_delete_of_a_powered_off_vm_does_not_ask(monkeypatch, no_wait):
    vm, calls = _vm(OFF)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)

    vm_lifecycle.delete_vm(MagicMock(), "web-01",
                           before_power_off=lambda: pytest.fail("asked for an off VM"))
    assert calls == ["destroy"]


def _cli_delete(monkeypatch, state):
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    vm, calls = _vm(state)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    monkeypatch.setattr("vmware_aiops.cli.vm._get_connection",
                        lambda *a, **k: (MagicMock(), MagicMock()))
    monkeypatch.setattr(vm_lifecycle, "get_vm_info", lambda si, n: {"power_state": str(state)})
    monkeypatch.setattr("vmware_aiops.ops.vm_delete_gate.vm_delete_blast_radius",
                        lambda si, n: {"unmeasured": ["x"]})
    monkeypatch.setattr("vmware_aiops.cli.vm._double_confirm", lambda *a, **k: None)
    monkeypatch.setattr("vmware_aiops.cli.vm._show_state_preview", lambda *a, **k: None)
    result = CliRunner().invoke(app, ["vm", "delete", "web-01", "--target", "t"])
    return result, calls


@pytest.mark.unit
def test_cli_delete_of_a_running_vm_is_refused_when_power_off_is_denied(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    result, calls = _cli_delete(monkeypatch, ON)
    assert result.exit_code != 0
    assert "power_off_vm" in result.output
    assert calls == []


@pytest.mark.unit
def test_cli_delete_of_an_off_vm_is_not_affected_by_a_power_off_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    result, calls = _cli_delete(monkeypatch, OFF)
    assert result.exit_code == 0, result.output
    assert calls == ["destroy"]


def _expired():
    return ttl.TTLEntry(vm_name="ttl-vm", expires_at="2026-01-01T00:00:00+00:00",
                        target="t", instance_uuid="uuid-1")


@pytest.mark.unit
def test_ttl_daemon_does_not_power_off_when_power_off_is_denied(harness, monkeypatch, rows):
    _rules(harness, "power_off_vm")
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", lambda task: None)
    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_expired()])
    monkeypatch.setattr(scheduler, "verify_ttl_identity", lambda si, e: None)
    removed: list[str] = []
    monkeypatch.setattr(scheduler, "remove_entry", removed.append)
    conn = MagicMock()

    for _ in range(3):  # the job runs every minute
        scheduler._run_ttl_check(conn)

    assert calls == []
    assert removed == []  # kept: the deny may be lifted, then it expires as set
    denied = [r for r in rows if r.get("status") == "denied"]
    # One row for the unchanged outcome, not one per cycle (review 2026-10-18).
    assert len(denied) == 1 and denied[0]["tool"] == "delete_vm", denied
    assert "power" in str(denied[0]).lower()


@pytest.mark.unit
def test_ttl_daemon_power_off_check_that_fails_is_an_error_not_a_deny(monkeypatch, rows):
    def broken(*a, **k):
        raise OSError("rules.yaml unreadable")

    monkeypatch.setattr(scheduler, "guard", lambda skill, tool, *a, **k:
                        broken() if tool == "power_off_vm" else None)
    vm, calls = _vm(ON)
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(scheduler, "_last_ttl_outcome", {}, raising=False)
    monkeypatch.setattr(scheduler, "get_expired_entries", lambda: [_expired()])
    monkeypatch.setattr(scheduler, "verify_ttl_identity", lambda si, e: None)
    monkeypatch.setattr(scheduler, "remove_entry", lambda n: pytest.fail("entry dropped"))

    scheduler._run_ttl_check(MagicMock())

    assert calls == []
    assert [r["status"] for r in rows if r.get("tool") == "delete_vm"] == ["error"]


def _rollback_step():
    return {"index": 1, "action": "delete_vm", "rollback": True,
            "params": {"vm_name": "made-by-plan", "instance_uuid": "uuid-1"}}


@pytest.mark.unit
def test_rollback_delete_of_a_running_vm_checks_power_off(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    vm, _ = _vm(ON)
    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", lambda si, n: vm)

    with pytest.raises(GateRefusedError, match="power_off_vm"):
        plan_gate.check_step(MagicMock(), _rollback_step(), target="t")


@pytest.mark.unit
def test_rollback_delete_of_an_off_vm_is_not_affected(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    vm, _ = _vm(OFF)
    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", lambda si, n: vm)

    plan_gate.check_step(MagicMock(), _rollback_step(), target="t")


@pytest.mark.unit
def test_rollback_delete_whose_power_state_cannot_be_read_checks_power_off(harness, monkeypatch):
    _rules(harness, "power_off_vm")

    def boom(si, n):
        raise RuntimeError("lost connection")

    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", boom)
    with pytest.raises(GateRefusedError, match="power_off_vm"):
        plan_gate.check_step(MagicMock(), _rollback_step(), target="t")


@pytest.mark.unit
def test_set_vm_ttl_reaches_power_off():
    from vmware_aiops.policy_roles import REACHES

    assert "power_off_vm" in REACHES["set_vm_ttl"]


@pytest.mark.unit
def test_rollback_preview_shows_the_power_off_deny(harness, monkeypatch):
    _rules(harness, "power_off_vm")
    vm, _ = _vm(ON)
    monkeypatch.setattr("vmware_aiops.ops.inventory.find_vm_by_name", lambda si, n: vm)

    fields, blockers, _ = plan_gate._gate([_rollback_step()], 0, MagicMock(), "t", False)

    assert fields == {"check": "refused by policy"}
    assert any("power_off_vm" in b for b in blockers)
