"""Routing backend selection: each target resolves ITS OWN vCenter's per-role
items - never another vCenter's creds against this host. Fakes the
uaa_hub_routing lib so this runs in the no-lib CI/local shape (unlike
test_hub_routing, which importorskips).

Contract (2026-10-01): the live target is uaa-vcenter (renamed from v9-vcenter on
2026-08-03; its 1P items kept the "vcenter-v9" title). A routed request for a
target with no backend row is denied before any login; the old fallback to the
"vcenter-prod" items pointed at the apps vCenter decommissioned 2026-08-24.
Unrouted calls are unaffected.
"""
import types

import pytest

from vmware_aiops import connection as conn_mod
from vmware_aiops.config import AppConfig, TargetConfig


class _LiveSess:
    currentSession = object()


class FakeSI:
    def __init__(self, user=None):
        class _Content:
            sessionManager = _LiveSess()
        self.content = _Content()
        self.user = user


class _RoutingError(Exception):
    pass


def _manager(monkeypatch, backend_calls, created, routed=True):
    def routing_item(backend, sel):
        backend_calls.append(backend)
        return f"MCP - syseng_elevated - {backend}" if routed else None

    fake_lib = types.SimpleNamespace(
        routing_item=routing_item,
        resolve_fields=lambda item: {"username": "svc", "password": "pw"},
        RoutingError=_RoutingError,
    )
    monkeypatch.setattr(conn_mod, "uaa_hub_routing", fake_lib, raising=False)
    monkeypatch.setattr(conn_mod, "_HUB_ROUTING", True)
    monkeypatch.setattr(conn_mod, "_VCENTER_SELECTOR", object(), raising=False)
    def create(t, *, user=None, pwd=None):
        created.append(user or t.username)
        return FakeSI(user or t.username)

    monkeypatch.setattr(conn_mod.ConnectionManager, "_create_connection", staticmethod(create))
    live = TargetConfig(
        name="uaa-vcenter", host="vc.example", config_username="startup", verify_ssl=True
    )
    other = TargetConfig(
        name="new-vcenter", host="vc2.example", config_username="startup", verify_ssl=True
    )
    return conn_mod.ConnectionManager(AppConfig(targets=(live, other)))


def test_default_target_uses_its_own_backend(monkeypatch):
    calls, created = [], []
    _manager(monkeypatch, calls, created).connect()
    assert calls == ["vcenter-v9"]
    assert created == ["svc"]


def test_live_target_name_maps_to_vcenter_v9(monkeypatch):
    calls, created = [], []
    _manager(monkeypatch, calls, created).connect("uaa-vcenter")
    assert calls == ["vcenter-v9"]


def test_routed_request_for_unmapped_target_is_denied_before_login(monkeypatch):
    calls, created = [], []
    cm = _manager(monkeypatch, calls, created)
    with pytest.raises(_RoutingError, match="no hub-routing backend"):
        cm.connect("new-vcenter")
    assert created == []
    assert "vcenter-prod" not in calls


def test_unrouted_call_for_unmapped_target_keeps_startup_cred(monkeypatch):
    calls, created = [], []
    si = _manager(monkeypatch, calls, created, routed=False).connect("new-vcenter")
    assert si.user == "startup"


def test_backend_cache_keys_stay_per_target_and_account(monkeypatch):
    calls, created = [], []
    cm = _manager(monkeypatch, calls, created)
    a = cm.connect("uaa-vcenter")
    b = cm.connect("uaa-vcenter")
    assert a is b  # cached per (target, routed account); login not repeated
    assert created == ["svc"]
