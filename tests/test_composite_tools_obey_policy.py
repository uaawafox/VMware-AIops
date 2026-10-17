"""Tools that run other tools' operations are authorized under those tools too.

Independent review, 2026-10-17 (proved): with rules.yaml denying
revert_vm_snapshot and power_off_vm, restore_vm_clean_slate(confirm=True) still
reverted the VM and was audited ok. The same held for the batch tools (a deny on
clone_vm did not stop batch_clone_vms) and provision_vm_guest (a deny on
run_vm_guest_command did not stop it). Each now asks the policy under every tool
it can run (policy_roles.REACHES) just before it acts.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.deploy as deploy_tools
import vmware_aiops.mcp_server.tools.guest as guest_tools
import vmware_aiops.mcp_server.tools.ttl as ttl_tools
from vmware_aiops.ops import step_policy, vm_deploy
from vmware_aiops.policy_roles import REACHES


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
    for mod in (deploy_tools, guest_tools, ttl_tools):
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


@pytest.mark.unit
def test_clean_slate_is_refused_when_revert_is_denied(harness, monkeypatch, rows):
    _rules(harness, "revert_vm_snapshot")
    monkeypatch.setattr("vmware_aiops.ops.ttl.measure_clean_slate",
                        lambda si, n, s: {"blockers": [], "unmeasured": []})
    reverted = []
    monkeypatch.setattr("vmware_aiops.ops.vm_lifecycle.clean_slate",
                        lambda *a, **k: reverted.append(1) or "x reverted to snapshot y")

    out = ttl_tools.restore_vm_clean_slate("web-01", confirm=True, target="t")

    assert reverted == [] and "error" in out
    assert any(r["tool"] == "revert_vm_snapshot" and r["status"] == "denied" for r in rows)
    assert rows[-1]["tool"] == "restore_vm_clean_slate" and rows[-1]["status"] == "error"


@pytest.mark.unit
def test_batch_clone_is_refused_when_clone_is_denied(harness, monkeypatch):
    _rules(harness, "clone_vm")
    ran = []
    monkeypatch.setattr(vm_deploy, "batch_clone", lambda *a, **k: ran.append(1) or [])

    out = deploy_tools.batch_clone_vms("gold", ["a", "b"], target="t")

    assert ran == [] and out and out[0].get("error")


@pytest.mark.unit
def test_provision_is_refused_when_guest_commands_are_denied(harness, monkeypatch, rows):
    _rules(harness, "run_vm_guest_command")
    monkeypatch.setattr(guest_tools, "measure_guest_provision",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})
    ran = []
    monkeypatch.setattr(guest_tools, "guest_provision", lambda *a, **k: ran.append(1) or {})

    out = guest_tools.provision_vm_guest("web-01", "root", "s3cret", [{"run": "id"}],
                                         confirm=True, target="t")

    assert ran == [] and "error" in out
    assert all("s3cret" not in str(r) for r in rows)


@pytest.mark.unit
def test_without_rules_the_batch_runs(monkeypatch):
    monkeypatch.setattr(vm_deploy, "batch_clone", lambda *a, **k: [{"name": "a", "status": "ok"}])
    out = deploy_tools.batch_clone_vms("gold", ["a"], target="t")
    assert out == [{"name": "a", "status": "ok"}]


@pytest.mark.unit
def test_every_reached_tool_has_a_known_risk():
    for tool, inner in REACHES.items():
        if tool in ("apply_vm_plan", "rollback_vm_plan", "set_vm_ttl"):
            continue  # plans check per step; the TTL daemon checks delete_vm itself
        for name in inner:
            assert name in step_policy.TOOL_RISK, f"{tool} reaches {name} with no risk"


# ── narrow review, 2026-10-17 ────────────────────────────────────────────────


@pytest.mark.unit
def test_a_follow_up_is_checked_only_when_asked_for(harness, monkeypatch):
    """A deny on power_on_vm refuses a batch that powers on, not one that does not."""
    _rules(harness, "power_on_vm")
    monkeypatch.setattr(vm_deploy, "batch_clone", lambda *a, **k: [{"name": "a", "status": "ok"}])

    quiet = deploy_tools.batch_clone_vms("gold", ["a"], target="t")
    loud = deploy_tools.batch_clone_vms("gold", ["a"], power_on=True, target="t")

    assert quiet == [{"name": "a", "status": "ok"}]
    assert loud and loud[0].get("error")


@pytest.mark.unit
@pytest.mark.parametrize("call", [
    lambda: deploy_tools.deploy_vm_from_ova("/x.ova", "v", "ds", power_on=True, target="t"),
    lambda: deploy_tools.deploy_vm_from_template("tmpl", "v", power_on=True, target="t"),
    lambda: deploy_tools.deploy_linked_clone("src", "s", "v", power_on=True, target="t"),
    lambda: deploy_tools.batch_linked_clone_vms("src", "s", ["v"], power_on=True, target="t"),
])
def test_every_deploy_path_checks_power_on(harness, monkeypatch, call):
    _rules(harness, "power_on_vm")
    for name in ("deploy_ova", "deploy_from_template", "linked_clone", "batch_linked_clone"):
        monkeypatch.setattr(vm_deploy, name, lambda *a, **k: pytest.fail("ran despite the deny"))

    out = call()

    assert "Error" in str(out) or "error" in str(out)


@pytest.mark.unit
def test_provision_checks_the_exec_with_output_tool(harness, monkeypatch):
    _rules(harness, "run_vm_guest_command_with_output")
    monkeypatch.setattr(guest_tools, "measure_guest_provision",
                        lambda *a, **k: {"blockers": [], "unmeasured": []})
    monkeypatch.setattr(guest_tools, "guest_provision", lambda *a, **k: pytest.fail("ran"))

    out = guest_tools.provision_vm_guest("web-01", "root", "pw", [{"run": "id"}],
                                         confirm=True, target="t")

    assert "error" in out


@pytest.mark.unit
def test_cli_clean_slate_is_refused_when_revert_is_denied(harness, monkeypatch):
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    _rules(harness, "revert_vm_snapshot")
    monkeypatch.setattr("vmware_aiops.cli.vm._get_connection",
                        lambda *a, **k: pytest.fail("connected despite the deny"))
    result = CliRunner().invoke(app, ["vm", "clean-slate", "web-01", "--target", "t"])

    assert result.exit_code != 0
    assert "revert_vm_snapshot" in result.output


@pytest.mark.unit
def test_a_plan_deploy_step_that_powers_on_is_checked_as_power_on(harness):
    from vmware_aiops.ops import plan_gate

    _rules(harness, "power_on_vm")
    step = {"index": 0, "action": "deploy_template",
            "params": {"template_name": "t", "new_name": "v", "power_on": True}}
    assert plan_gate.policy_refusal(step, "t", record=False)
    step["params"]["power_on"] = False
    assert plan_gate.policy_refusal(step, "t", record=False) is None
