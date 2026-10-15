"""A login that works but has no vSphere role must say so (AIops#47).

Reported against vCenter 8.0.3, not reproduced here (the lab is down): an SSO
account with no vSphere role logs in, ``sessionManager.currentSession`` reads
``None``, and every inventory call then fails with ``vim.fault.NotAuthenticated``.
``doctor`` passed, the CLI said "verify connectivity and credentials", and
``vmware-aiops summary`` printed a raw traceback for the fault.

Driven through the real ``ConnectionManager`` with only ``SmartConnect`` and
``Disconnect`` replaced, so the production factory, pool and cleanup all run.
The attention roll-up is vmware-monitor's code and is tested there.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import typer
from pyVmomi import vim
from rich.console import Console

from vmware_aiops import connection as conn_mod
from vmware_aiops.config import AppConfig, TargetConfig
from vmware_aiops.connection import ConnectionManager, NoVSphereRoleError

pytestmark = pytest.mark.unit

ROLE = "no vSphere role"


@pytest.fixture(autouse=True)
def _password(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("VC1", "VC2"):
        monkeypatch.setenv(f"VMWARE_{name}_PASSWORD", "test-only")
        monkeypatch.setenv(f"VMWARE_AIOPS_{name}_PASSWORD", "test-only")


def _config(*names: str) -> AppConfig:
    return AppConfig(
        targets=tuple(
            TargetConfig(name=n, host=f"{n}.example.com", config_username="svc") for n in names
        )
    )


def _si(has_session: bool) -> MagicMock:
    si = MagicMock()
    si.content.sessionManager.currentSession = object() if has_session else None
    return si


def test_role_less_login_is_refused_closed_and_not_cached() -> None:
    mgr = ConnectionManager(_config("vc1"))
    si = _si(False)
    with (
        patch("pyVim.connect.SmartConnect", return_value=si),
        patch("pyVim.connect.Disconnect") as disconnect,
        pytest.raises(NoVSphereRoleError) as err,
    ):
        mgr.connect("vc1")

    msg = str(err.value)
    assert ROLE in msg and "Read-Only" in msg and "'vc1'" in msg
    assert "vmware-aiops doctor" in msg
    disconnect.assert_called_once_with(si)  # the login is logged out
    assert id(si) not in conn_mod._SI_ATEXIT  # its atexit cleanup taken back
    assert id(si) not in conn_mod._SI_VERIFY_SSL  # its side store cleared
    assert mgr.list_connected() == []  # and the pool kept nothing


def test_a_normal_login_is_unaffected() -> None:
    mgr = ConnectionManager(_config("vc1"))
    si = _si(True)
    with (
        patch("pyVim.connect.SmartConnect", return_value=si),
        patch("pyVim.connect.Disconnect") as disconnect,
    ):
        assert mgr.connect("vc1") is si
        disconnect.assert_not_called()
        assert mgr.list_connected() == ["vc1"]
        mgr.disconnect_all()


def test_an_unreadable_session_is_not_a_verdict() -> None:
    """Unknown is not "no role": a probe that raises lets the login through."""
    mgr = ConnectionManager(_config("vc1"))
    si = MagicMock()
    type(si.content.sessionManager).currentSession = PropertyMock(side_effect=RuntimeError)
    with patch("pyVim.connect.SmartConnect", return_value=si), patch("pyVim.connect.Disconnect"):
        assert ConnectionManager._create_connection(mgr._config.get_target("vc1")) is si
    conn_mod._close_session(si)


def test_connect_all_names_the_role_case() -> None:
    mgr = ConnectionManager(_config("vc1", "vc2"))
    sis = {"vc1.example.com": _si(True), "vc2.example.com": _si(False)}
    with (
        patch("pyVim.connect.SmartConnect", side_effect=lambda host, **_: sis[host]),
        patch("pyVim.connect.Disconnect"),
    ):
        sessions, unreachable = mgr.connect_all()
        mgr.disconnect_all()
    assert [n for n, _ in sessions] == ["vc1"]
    assert unreachable == [("vc2", "NoVSphereRoleError")]


def test_doctor_auth_row_fails_with_the_role_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from vmware_aiops import doctor as doc

    monkeypatch.setattr(type(doc.resolve_config_path()), "exists", lambda self: True)
    monkeypatch.setattr("vmware_aiops.config.load_config", lambda: _config("vc1"))
    with (
        patch("pyVim.connect.SmartConnect", return_value=_si(False)),
        patch("pyVim.connect.Disconnect"),
    ):
        ok, row = doc._check_auth()
    assert ok is False
    assert ROLE in row and "vc1 ✗" in row


def _cli_output(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> str:
    from vmware_aiops.cli import _common

    buf = io.StringIO()
    monkeypatch.setattr(_common, "console", Console(file=buf, width=2000))

    @_common.cli_errors
    def cmd() -> None:
        raise exc

    with pytest.raises(typer.Exit):
        cmd()
    return buf.getvalue()


def test_cli_prints_the_role_message_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = conn_mod.no_role_message("vc1", "vmware-aiops")
    out = _cli_output(monkeypatch, NoVSphereRoleError(msg))
    assert msg in out


def test_cli_not_authenticated_names_both_causes(monkeypatch: pytest.MonkeyPatch) -> None:
    fault = vim.fault.NotAuthenticated(msg="The session is not authenticated.")
    out = _cli_output(monkeypatch, fault)
    assert "expired" in out and "vSphere role" in out
    assert "verify connectivity and credentials" not in out


def test_cli_any_other_fault_is_one_line_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue item 5: `summary` printed a traceback for a vSphere fault."""
    out = _cli_output(monkeypatch, vim.fault.NoPermission(msg="Permission to perform this"))
    assert "vSphere API fault: Permission to perform this" in out
    assert "vSphere role" in out


def test_summary_goes_through_cli_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command the issue named, end to end through its real decorators."""
    from vmware_aiops.cli import _common, summary

    buf = io.StringIO()
    monkeypatch.setattr(_common, "console", Console(file=buf, width=2000))

    def _raise(*_a, **_kw):
        raise vim.fault.NotAuthenticated(msg="The session is not authenticated.")

    monkeypatch.setattr(summary, "_get_connection", _raise)
    with pytest.raises(typer.Exit):
        summary.cluster_summary_cmd(
            cluster=None, no_vms=False, top=10, html=False, html_path=None, target=None, config=None
        )
    assert "vSphere role" in buf.getvalue()


def _mcp_payload(exc: Exception, shape: str = "dict"):
    from vmware_aiops.mcp_server._shared import tool_errors

    @tool_errors(shape)
    def tool():
        raise exc

    return tool()


def test_mcp_passes_the_whole_role_message() -> None:
    msg = conn_mod.no_role_message("a-rather-long-target-name-in-config", "vmware-aiops")
    payload = _mcp_payload(NoVSphereRoleError(msg))
    assert payload["error"] == msg  # not cut, not reduced to a class name
    assert "credentials" not in payload["hint"]


def test_mcp_not_authenticated_names_both_causes() -> None:
    payload = _mcp_payload(vim.fault.NotAuthenticated(msg="The session is not authenticated."))
    assert "expired" in payload["error"] and "vSphere role" in payload["error"]
    assert "credentials" not in payload["hint"]
    text = _mcp_payload(vim.fault.NotAuthenticated(), shape="str")
    assert "vSphere role" in text and "verify connectivity and credentials" not in text
