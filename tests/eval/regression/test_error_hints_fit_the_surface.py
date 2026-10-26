"""Error hints must fit the error, and the surface that shows them.

Real hardware, 2026-10-17: every MCP error carried "Run 'vmware-aiops doctor'
to verify connectivity and credentials." -- also for a name that does not
resolve, where connectivity is fine and the message already says what to change.
And messages an agent reads said "Pass --target" / "Poll with: vm task-status
... --target", a CLI flag and a CLI command; the MCP names are the ``target``
parameter and ``get_vm_task_status``.
"""

from __future__ import annotations

import pytest

from vmware_aiops.config import AppConfig
from vmware_aiops.mcp_server import _shared
from vmware_aiops.ops.ova_deploy import DeployError
from vmware_aiops.ops.vm_lifecycle import TaskStillRunning, VMNotFoundError


def _unknown_target():
    from vmware_aiops.config import UnknownTargetError

    return UnknownTargetError("Target 'x' not found.")


@pytest.mark.unit
@pytest.mark.parametrize(
    "exc", [ValueError("bad"), _unknown_target(), VMNotFoundError("x"), DeployError("x")]
)
def test_an_authored_request_error_does_not_send_the_agent_to_doctor(exc) -> None:
    assert "doctor" not in _shared._hint_for(exc)


@pytest.mark.unit
@pytest.mark.parametrize("exc", [ConnectionError("x"), TimeoutError("x"), RuntimeError("x")])
def test_connectivity_and_unplanned_errors_keep_the_doctor_hint(exc) -> None:
    assert _shared._hint_for(exc) == _shared._DOCTOR_HINT


@pytest.mark.unit
def test_unknown_target_names_the_mcp_parameter() -> None:
    with pytest.raises(KeyError) as info:
        AppConfig(targets=()).get_target("nope")
    assert "target=" in str(info.value)


@pytest.mark.unit
def test_still_running_names_the_mcp_poll_tool() -> None:
    assert "get_vm_task_status" in str(TaskStillRunning("task-1", 60))


@pytest.mark.unit
@pytest.mark.parametrize("make", [
    lambda: __import__("vmware_aiops.ops.vm_lifecycle", fromlist=["x"]).TaskFailedError(
        "Task failed: ... | fault=vim.fault.MemoryHotPlugNotSupported"),
    lambda: __import__("vmware_aiops.ops.guest_ops", fromlist=["x"]).GuestOpsError(
        "VMware Tools is not running in the guest"),
    lambda: __import__("vmware_aiops.ops.gate", fromlist=["x"]).GateRefusedError(
        "refused: VMware Tools is not running"),
])
def test_task_guest_and_gate_refusals_do_not_send_the_agent_to_doctor(make) -> None:
    """Write round 2026-10-17: MemoryHotPlugNotSupported and Tools-not-running
    errors said 'Run doctor to verify connectivity and credentials'."""
    assert "doctor" not in _shared._hint_for(make())



@pytest.mark.unit
@pytest.mark.parametrize("exc", [
    __import__("json").JSONDecodeError("Expecting value", "<html>", 0),
    KeyError("summary.capacity"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
])
def test_parse_errors_and_bare_key_errors_keep_the_doctor_hint(exc) -> None:
    """Independent review 2026-10-17: a reverse proxy answering HTML raises
    JSONDecodeError (a ValueError), and a programming slip raises a bare KeyError;
    neither message says what to change, so they must not be told it does."""
    assert _shared._hint_for(exc) == _shared._DOCTOR_HINT


@pytest.mark.unit
def test_an_unknown_target_is_still_a_key_error() -> None:
    """Callers catch KeyError from get_target; the subclass must not break them."""
    assert issubclass(type(_unknown_target()), KeyError)
