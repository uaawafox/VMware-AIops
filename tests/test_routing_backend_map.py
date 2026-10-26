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


def test_routed_item_for_another_backend_is_denied_before_login(monkeypatch):
    """Legacy single-role header: a pre-resolved title minted for a different
    vCenter must not put that vCenter's credentials on this host."""
    calls, created = [], []
    cm = _manager(monkeypatch, calls, created)
    monkeypatch.setattr(
        conn_mod.uaa_hub_routing, "routing_item",
        lambda backend, sel: "MCP - syseng_elevated - vcenter-prod",
    )
    with pytest.raises(_RoutingError, match="not for backend 'vcenter-v9'"):
        cm.connect("uaa-vcenter")
    assert created == []


def test_target_name_containing_the_key_separator_still_works(monkeypatch):
    calls, created = [], []
    routed = {"on": False}

    def routing_item(backend, sel):
        return f"MCP - syseng_elevated - {backend}" if routed["on"] else None

    fake_lib = types.SimpleNamespace(
        routing_item=routing_item,
        resolve_fields=lambda item: {"username": "svc", "password": "pw"},
        RoutingError=_RoutingError,
    )
    monkeypatch.setattr(conn_mod, "uaa_hub_routing", fake_lib, raising=False)
    monkeypatch.setattr(conn_mod, "_HUB_ROUTING", True)
    monkeypatch.setattr(conn_mod, "_VCENTER_SELECTOR", object(), raising=False)
    monkeypatch.setitem(conn_mod._ROUTING_BACKENDS, "site#1", "site-backend")
    monkeypatch.setattr(
        conn_mod.ConnectionManager, "_create_connection",
        staticmethod(lambda t, *, user=None, pwd=None: (created.append(t.name), FakeSI(user or t.username))[1]),
    )
    target = TargetConfig(name="site#1", host="vc.example", config_username="startup", verify_ssl=True)
    cm = conn_mod.ConnectionManager(AppConfig(targets=(target,)))
    assert cm.connect("site#1").user == "startup"          # unrouted: whole name kept
    routed["on"] = True
    assert cm.connect("site#1").user == "svc"              # routed: same target, routed creds
    assert created == ["site#1", "site#1"]
    cm.disconnect("site#1")
    assert cm.list_connected() == []


def test_target_named_like_a_routed_key_cannot_alias_it(monkeypatch):
    """A target whose NAME equals another target's routed key text must get
    its own session, never the elevated one (and vice versa)."""
    item = "MCP - syseng_elevated - vcenter-v9"
    evil = f"uaa-vcenter#{item}"
    created = []
    routed = {"on": True}
    fake_lib = types.SimpleNamespace(
        routing_item=lambda backend, sel: item if routed["on"] else None,
        resolve_fields=lambda i: {"username": "svc-elev", "password": "pw"},
        RoutingError=_RoutingError,
    )
    monkeypatch.setattr(conn_mod, "uaa_hub_routing", fake_lib, raising=False)
    monkeypatch.setattr(conn_mod, "_HUB_ROUTING", True)
    monkeypatch.setattr(conn_mod, "_VCENTER_SELECTOR", object(), raising=False)
    monkeypatch.setattr(
        conn_mod.ConnectionManager, "_create_connection",
        staticmethod(lambda t, *, user=None, pwd=None: (created.append((t.name, user)), FakeSI(user or t.username))[1]),
    )
    live = TargetConfig(name="uaa-vcenter", host="vc.example", config_username="startup", verify_ssl=True)
    other = TargetConfig(name=evil, host="vc2.example", config_username="other-startup", verify_ssl=True)
    cm = conn_mod.ConnectionManager(AppConfig(targets=(live, other)))
    elevated = cm.connect("uaa-vcenter")           # routed session on the live host
    routed["on"] = False
    plain = cm.connect(evil)                       # unrouted session on the other host
    assert plain is not elevated
    assert plain.user == "other-startup"
    assert elevated.user == "svc-elev"
    assert created == [("uaa-vcenter", "svc-elev"), (evil, None)]
    assert sorted(cm.list_connected()) == sorted(["uaa-vcenter", evil])
