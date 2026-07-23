"""A failure that is *returned* rather than raised must still be audited as one.

``@vmware_tool`` marks a call failed when an exception reaches it, or when the
returned payload is the family's ``{"error": ...}`` envelope. Twenty-five of
this skill's tools use ``@tool_errors("str")``, which catches the exception and
hands back ``"Error: ..."`` — a plain string, indistinguishable from a
successful one. So the wrapper saw an ordinary return and recorded ``ok``.

Three things were wrong at once, and the undo one is the worst:

1. the audit row said ``ok`` for an operation that failed;
2. ``_record_undo`` wrote an inverse token for a change that never landed — a
   failed ``vm_power_on`` filed "power it back off", so vmware-pilot could
   offer to reverse a power-on that never happened;
3. the circuit breaker was told ``success=True``, so repeated failures never
   tripped it.

``report_tool_failure`` (vmware-policy 1.8.4) exists for exactly this and
nothing called it. ``@tool_errors`` now calls it before returning any error
payload, inside the ``@vmware_tool`` call still in flight.

These tests assert the *audited status* and the *undo store*, never the
returned string — the string was always right, which is why the defect was
invisible. The success case is pinned alongside so the "no undo token" claim
cannot pass by undo being broken outright.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import get_undo_store, reset_undo_store

import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.ops import vm_lifecycle

#: What a connection failure looks like on the way into a tool body.
DROPPED = "Connection to vcenter-prod dropped. Run 'vmware-aiops doctor'."


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    """Point policy, budget and the undo store at a tmp dir, not the real ~/.vmware."""
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def audit_rows(monkeypatch):
    """Capture the rows ``@vmware_tool`` writes, in place of the real engine."""
    rows: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    # v1.8.7 extracted the single audit sink into vmware_policy.guard.audit_call,
    # which resolves get_engine in the guard module's namespace; patch it there
    # (the decorators module no longer looks get_engine up itself).
    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return rows


def _connection_fails(*_args, **_kwargs):
    raise ConnectionError(DROPPED)


def _connection_works(_target=None):
    return MagicMock()


# ── the audit row ────────────────────────────────────────────────────────────


def test_failed_string_tool_is_audited_as_a_failure(audit_rows, monkeypatch) -> None:
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_fails)

    returned = vm_tools.vm_power_on("web-01", target="vcenter-prod")

    # The string was never the bug — it says "Error:" both before and after.
    assert returned.startswith("Error:")
    assert audit_rows, "the call produced no audit row at all"
    assert audit_rows[0]["status"] == "error"


def test_successful_string_tool_is_still_audited_ok(audit_rows, monkeypatch) -> None:
    """The other direction: declaring failure must not mark working calls failed."""
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_works)
    monkeypatch.setattr(vm_tools, "power_on_vm", lambda si, name: f"VM '{name}' powered on.")

    vm_tools.vm_power_on("web-01", target="vcenter-prod")

    assert audit_rows[0]["status"] == "ok"


# ── the undo token ───────────────────────────────────────────────────────────


def test_failed_power_on_records_no_undo_token(audit_rows, monkeypatch) -> None:
    """The consequence that reaches a human: pilot offering to reverse a no-op.

    ``_record_undo`` is gated on ``status == "ok"``, so wiring the failure
    signal is what closes this — there is no separate undo fix.
    """
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_fails)

    vm_tools.vm_power_on("web-01", target="vcenter-prod")

    assert get_undo_store().list() == [], "filed an undo token for a power-on that never ran"


def test_successful_power_on_does_record_its_undo_token(audit_rows, monkeypatch) -> None:
    """Control for the test above — otherwise "no token" passes if undo is dead."""
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_works)
    monkeypatch.setattr(vm_tools, "power_on_vm", lambda si, name: f"VM '{name}' powered on.")

    vm_tools.vm_power_on("web-01", target="vcenter-prod")

    tokens = get_undo_store().list()
    assert len(tokens) == 1
    assert tokens[0]["tool"] == "vm_power_on"


# ── coverage of the shapes, not just the one tool ────────────────────────────


@pytest.mark.parametrize("shape", ["str", "dict", "list"])
def test_every_error_payload_shape_declares_the_failure(audit_rows, shape) -> None:
    """``@tool_errors`` calls report_tool_failure once, before shape selection.

    The dict and list shapes are additionally detected by policy's own envelope
    reading; the str shape is not, and it is the one covering every undo-bearing
    write in this skill.
    """
    from vmware_policy import vmware_tool

    from vmware_aiops.mcp_server._shared import tool_errors

    @vmware_tool(risk_level="low")
    @tool_errors(shape)
    def probe_tool():
        raise ConnectionError(DROPPED)

    probe_tool()

    assert audit_rows[-1]["status"] == "error"


# ── the other direction: a failed *task* is not a failed *call* ──────────────


def _failed_task() -> MagicMock:
    """A vSphere task that ran and failed. Reading its status still succeeds."""
    task = MagicMock()
    task.info.state = "error"
    task.info.error.msg = "The attempted operation cannot be performed in the current state."
    task.info.progress = 100
    task.info.descriptionId = "VirtualMachine.removeSnapshot"
    task.info.entityName = "web-01"
    return task


def test_polling_a_failed_task_is_not_a_failed_call(audit_rows, monkeypatch) -> None:
    """vm_task_status reported the task's fault under a top-level ``error`` key.

    That key is the family's envelope for "this call failed", so policy read a
    working read as a failure: an agent polling one failed snapshot-delete three
    times booked three phantom failures against the circuit breaker and three
    wrong audit rows. It was ambiguous to a model too — nothing in the payload
    distinguished "the poll broke" from "the task broke". The fault now travels
    as ``task_error``.
    """
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_works)

    with patch.object(vm_lifecycle.vim, "Task", return_value=_failed_task()):
        payload = vm_tools.vm_task_status("task-42", target="vcenter-prod")

    assert payload["state"] == "error"
    assert payload["task_error"], "the fault text must still reach the agent"
    assert "error" not in payload, "top-level 'error' means the CALL failed; this one worked"
    assert audit_rows[0]["status"] == "ok"


def test_polling_a_succeeding_task_carries_no_task_error(audit_rows, monkeypatch) -> None:
    """Control: the key appears only for a task that actually failed."""
    monkeypatch.setattr(vm_tools, "_get_connection", _connection_works)
    task = _failed_task()
    task.info.state = "success"
    task.info.error = None

    with patch.object(vm_lifecycle.vim, "Task", return_value=task):
        payload = vm_tools.vm_task_status("task-42", target="vcenter-prod")

    assert payload["state"] == "success"
    assert "task_error" not in payload
    assert audit_rows[0]["status"] == "ok"
