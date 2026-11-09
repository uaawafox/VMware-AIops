"""Output-capturing guest commands keep the measured VM identity end to end.

``guest_exec_with_output`` checks the expected instance UUID once, then runs the
command, downloads its output and cleans up through nested calls that each look
the VM up by name again. Without the UUID on those nested calls, a VM recreated
under the same name between the check and the run would execute the command
(fork fix on top of upstream #49, found by the reviewer pass 2026-11-09).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyVmomi import vim

from vmware_aiops.ops import guest_ops
from vmware_aiops.ops.inventory import VMChangedError

ON = vim.VirtualMachine.PowerState.poweredOn


def _vm(uuid: str) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(instanceUuid=uuid),
        runtime=SimpleNamespace(powerState=ON),
        guest=SimpleNamespace(toolsRunningStatus="guestToolsRunning", guestFamily="linuxGuest"),
    )


@pytest.fixture
def swapped_after_first_lookup(monkeypatch):
    """First lookup finds VM A; every later lookup finds a same-name VM B."""
    calls = {"n": 0}

    def find(_si, _name):
        calls["n"] += 1
        return _vm("uuid-A") if calls["n"] == 1 else _vm("uuid-B")

    monkeypatch.setattr(guest_ops, "find_vm_by_name", find)
    return calls


def test_command_refused_when_vm_replaced_after_the_check(swapped_after_first_lookup, monkeypatch):
    ran: list = []
    monkeypatch.setattr(guest_ops, "_guest_auth", lambda *a, **k: ran.append("auth") or object())
    with pytest.raises(VMChangedError):
        guest_ops.guest_exec_with_output(
            object(), "web-01", "id", "ops", "pw", expect_instance_uuid="uuid-A")
    assert ran == [], "no guest process may start on the replacement VM"


def test_every_nested_call_carries_the_expected_uuid(monkeypatch):
    monkeypatch.setattr(guest_ops, "_require_vm_with_tools", lambda *a, **k: _vm("uuid-A"))
    seen: list = []

    def spy_exec(*a, **k):
        seen.append(("exec", k.get("expect_instance_uuid")))
        return {"exit_code": 0, "timed_out": False}

    def spy_download(*a, **k):
        seen.append(("download", k.get("expect_instance_uuid")))
        raise OSError("no file")

    monkeypatch.setattr(guest_ops, "guest_exec", spy_exec)
    monkeypatch.setattr(guest_ops, "guest_download", spy_download)
    guest_ops.guest_exec_with_output(object(), "web-01", "id", "ops", "pw", expect_instance_uuid="uuid-A")
    assert [kind for kind, _ in seen] == ["exec", "download", "exec"]
    assert all(u == "uuid-A" for _, u in seen), seen


def test_cli_path_without_a_measured_uuid_is_unchanged(monkeypatch):
    monkeypatch.setattr(guest_ops, "_require_vm_with_tools", lambda *a, **k: _vm("uuid-A"))
    seen: list = []
    monkeypatch.setattr(guest_ops, "guest_exec",
                        lambda *a, **k: seen.append(k.get("expect_instance_uuid"))
                        or {"exit_code": 0, "timed_out": False})
    monkeypatch.setattr(guest_ops, "guest_download", lambda *a, **k: seen.append(k.get("expect_instance_uuid")))
    guest_ops.guest_exec_with_output(object(), "web-01", "id", "ops", "pw")
    assert seen == [None, None, None]
