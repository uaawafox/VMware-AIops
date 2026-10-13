"""HLD §7 gate on reset_vcenter_alarm.

vSphere has no per-alarm clear: ClearTriggeredAlarms clears every triggered
alarm with the named alarm's status on its entity type. Until 1.12.1 the MCP
tool cleared them on the first call (destructiveHint False, so outside the
gate). Now:

* L2 — a bare call previews and never calls ClearTriggeredAlarms.
* L1 — preview and acting response carry ``blast_radius`` listing exactly what
  the filter matches (same status, same entity type, de-duplicated).
* L3 — ``confirm=True`` refuses when that list cannot be read.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine

import vmware_aiops.mcp_server.tools.alarm as alarm_tools
from vmware_aiops.ops import alarm_mgmt


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    yield
    reset_policy_engine()
    reset_budget()


def _entity(kind, name):
    e = MagicMock(spec=kind)
    e.name = name
    return e


def _state(entity, alarm_name, status, key):
    s = MagicMock()
    s.entity = entity
    s.alarm.info.name = alarm_name
    s.overallStatus = status
    s.key = key
    return s


ESX1 = _entity(vim.HostSystem, "esx-01")
ESX2 = _entity(vim.HostSystem, "esx-02")
VM1 = _entity(vim.VirtualMachine, "web-01")

NAMED = _state(ESX1, "Host memory usage", "red", "alarm-1.host-1")
SAME_FILTER = _state(ESX2, "Host CPU usage", "red", "alarm-2.host-2")
OTHER_STATUS = _state(ESX2, "Host connection", "yellow", "alarm-3.host-2")
OTHER_TYPE = _state(VM1, "VM CPU usage", "red", "alarm-4.vm-1")


@pytest.fixture
def vcenter(monkeypatch):
    """A vCenter whose root aggregates the given alarm states (or cannot be read)."""
    si = MagicMock()

    def install(root_states, *, root_readable=True):
        def collect(_si, types, _props):
            out = []
            for t in types:
                for ent in (ESX1, ESX2, VM1):
                    if isinstance(ent, t):
                        mine = [s for s in root_states if s.entity is ent]
                        out.append((ent, {"name": ent.name, "triggeredAlarmState": mine}))
            return out

        def collect_object(_si, _obj, _type, _props):
            if not root_readable:
                raise vim.fault.NoPermission()
            return {"triggeredAlarmState": list(root_states)}

        monkeypatch.setattr(alarm_mgmt, "_collect", collect)
        monkeypatch.setattr(alarm_mgmt, "_collect_object", collect_object)
        monkeypatch.setattr(alarm_tools, "_get_connection", lambda _t=None: si)
        return si.RetrieveContent.return_value.alarmManager

    return install


def test_bare_call_previews_exactly_what_the_filter_clears(vcenter):
    manager = vcenter([NAMED, SAME_FILTER, OTHER_STATUS, OTHER_TYPE, NAMED])
    out = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage")

    manager.ClearTriggeredAlarms.assert_not_called()
    assert out["action"] == "preview"
    br = out["blast_radius"]
    assert br["status"] == "red"
    assert br["entity_type_filter"] == "entityTypeHost"
    assert br["will_clear_count"] == 2
    assert {(a["entity_name"], a["alarm_name"]) for a in br["will_clear"]} == {
        ("esx-01", "Host memory usage"),
        ("esx-02", "Host CPU usage"),
    }
    assert br["will_clear_truncated"] is False
    assert br["blockers"] == [] and br["unmeasured"] == []


def test_confirmed_call_clears_once_and_reports_the_radius(vcenter):
    manager = vcenter([NAMED, SAME_FILTER])
    out = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage", confirm=True)

    manager.ClearTriggeredAlarms.assert_called_once()
    assert out["action"] == "reset"
    assert out["blast_radius"]["will_clear_count"] == 2


def test_unreadable_alarm_list_is_unmeasured_and_refused(vcenter):
    manager = vcenter([NAMED, SAME_FILTER], root_readable=False)

    preview = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage")
    assert preview["blast_radius"]["unmeasured"] == ["alarms_that_would_be_cleared"]

    out = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage", confirm=True)
    manager.ClearTriggeredAlarms.assert_not_called()
    assert "reset_vcenter_alarm refused" in out["error"]


@pytest.mark.parametrize("confirm", [False, True])
def test_unknown_alarm_clears_nothing(vcenter, confirm):
    manager = vcenter([NAMED])
    out = alarm_tools.reset_vcenter_alarm("esx-01", "Hots memory usage", confirm=confirm)
    manager.ClearTriggeredAlarms.assert_not_called()
    assert "error" in out


def test_preview_list_is_capped_but_the_count_is_not(vcenter):
    many = [NAMED] + [
        _state(ESX2, f"Host alarm {i}", "red", f"alarm-x{i}.host-2")
        for i in range(alarm_mgmt.RESET_PREVIEW_LIMIT + 5)
    ]
    vcenter(many)
    br = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage")["blast_radius"]
    assert br["will_clear_count"] == alarm_mgmt.RESET_PREVIEW_LIMIT + 6
    assert len(br["will_clear"]) == alarm_mgmt.RESET_PREVIEW_LIMIT
    assert br["will_clear_truncated"] is True


def test_schema_and_annotations_put_it_inside_the_gate():
    from vmware_aiops.mcp_server.server import mcp

    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "reset_vcenter_alarm")
    assert tool.inputSchema["properties"]["confirm"]["default"] is False
    assert "confirm" not in tool.inputSchema.get("required", [])
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.idempotentHint is not True


# ═══ review fixes ═════════════════════════════════════════════════════════════


def test_the_clear_uses_exactly_the_filter_the_preview_measured(vcenter):
    manager = vcenter([NAMED, SAME_FILTER])
    out = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage", confirm=True)
    filt = manager.ClearTriggeredAlarms.call_args.kwargs["filter"]
    br = out["blast_radius"]
    assert [str(s) for s in filt.status] == [br["status"]]
    assert str(filt.typeEntity) == br["entity_type_filter"]


def test_confirmed_call_looks_the_alarm_up_once(vcenter, monkeypatch):
    vcenter([NAMED, SAME_FILTER])
    calls = []
    real = alarm_mgmt._find_triggered_alarm
    monkeypatch.setattr(
        alarm_mgmt, "_find_triggered_alarm", lambda *a: calls.append(a) or real(*a)
    )
    alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage", confirm=True)
    assert len(calls) == 1, "the clear must not re-derive its filter from a second lookup"


def test_root_returning_nothing_is_unmeasured_not_empty(vcenter, monkeypatch):
    manager = vcenter([NAMED])
    monkeypatch.setattr(alarm_mgmt, "_collect_object", lambda *_a: {})
    br = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage")["blast_radius"]
    assert br["unmeasured"] == ["alarms_that_would_be_cleared"]
    assert "will_clear_count" not in br
    out = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage", confirm=True)
    manager.ClearTriggeredAlarms.assert_not_called()
    assert "error" in out


def test_one_unreadable_alarm_definition_does_not_sink_the_preview(vcenter):
    broken = _state(ESX2, "x", "red", "alarm-9.host-2")
    type(broken.alarm).info = property(lambda _self: (_ for _ in ()).throw(TypeError("gone")))
    vcenter([NAMED, broken])
    br = alarm_tools.reset_vcenter_alarm("esx-01", "Host memory usage")["blast_radius"]
    assert br["will_clear_count"] == 2
    assert "<unreadable>" in {a["alarm_name"] for a in br["will_clear"]}


@pytest.fixture
def cli_vcenter(vcenter, monkeypatch):
    import vmware_aiops.cli.alarm as cli_alarm

    def install(states, **kw):
        manager = vcenter(states, **kw)
        si = alarm_tools._get_connection()
        monkeypatch.setattr(cli_alarm, "_get_connection", lambda *_a, **_k: (si, None))
        return manager

    return install


def _cli(argv, answers=""):
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    return CliRunner().invoke(app, argv, input=answers)


def test_cli_dry_run_lists_what_it_would_clear_and_clears_nothing(cli_vcenter):
    manager = cli_vcenter([NAMED, SAME_FILTER, OTHER_STATUS])
    out = _cli(["alarm", "reset", "esx-01", "Host memory usage", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "Would clear 2 triggered alarm(s)" in out.output
    assert "Host CPU usage" in out.output
    manager.ClearTriggeredAlarms.assert_not_called()


def test_cli_refuses_when_it_cannot_read_what_it_would_clear(cli_vcenter):
    manager = cli_vcenter([NAMED], root_readable=False)
    out = _cli(["alarm", "reset", "esx-01", "Host memory usage"], "y\ny\n")
    assert out.exit_code != 0
    manager.ClearTriggeredAlarms.assert_not_called()


def test_cli_confirmed_reset_clears_with_the_measured_filter(cli_vcenter):
    manager = cli_vcenter([NAMED, SAME_FILTER])
    out = _cli(["alarm", "reset", "esx-01", "Host memory usage"], "y\ny\n")
    assert out.exit_code == 0, out.output
    filt = manager.ClearTriggeredAlarms.call_args.kwargs["filter"]
    assert [str(s) for s in filt.status] == ["red"]
    assert str(filt.typeEntity) == "entityTypeHost"
    assert "Cleared 2 triggered alarm(s)" in out.output
