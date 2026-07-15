"""Routing backend selection: each target resolves ITS OWN vCenter's per-role
items - never prod creds against another host. Fakes the uaa_hub_routing lib
so this runs in the no-lib CI/local shape (unlike test_hub_routing, which
importorskips)."""
import types

from vmware_aiops import connection as conn_mod
from vmware_aiops.config import AppConfig, TargetConfig


class _LiveSess:
    currentSession = object()


class FakeSI:
    def __init__(self):
        class _Content:
            sessionManager = _LiveSess()
        self.content = _Content()


def _manager(monkeypatch, backend_calls):
    fake_lib = types.SimpleNamespace(
        routing_item=lambda backend, sel: (backend_calls.append(backend), f"MCP - syseng_elevated - {backend}")[1],
        resolve_fields=lambda item: {"username": "svc", "password": "pw"},
    )
    monkeypatch.setattr(conn_mod, "uaa_hub_routing", fake_lib, raising=False)
    monkeypatch.setattr(conn_mod, "_HUB_ROUTING", True)
    monkeypatch.setattr(conn_mod, "_VCENTER_SELECTOR", object(), raising=False)
    monkeypatch.setattr(
        conn_mod.ConnectionManager, "_create_connection",
        staticmethod(lambda t, *, user=None, pwd=None: FakeSI()),
    )
    prod = TargetConfig(name="uaa-vcenter", host="prod.example", username="u", verify_ssl=False)
    v9 = TargetConfig(name="v9-vcenter", host="v9.example", username="u", verify_ssl=True)
    return conn_mod.ConnectionManager(AppConfig(targets=(prod, v9)))


def test_default_target_uses_prod_backend(monkeypatch):
    calls = []
    cm = _manager(monkeypatch, calls)
    cm.connect()
    assert calls == ["vcenter-prod"]


def test_v9_target_uses_v9_backend(monkeypatch):
    calls = []
    cm = _manager(monkeypatch, calls)
    cm.connect("v9-vcenter")
    assert calls == ["vcenter-v9"]


def test_unmapped_target_falls_back_to_prod_backend(monkeypatch):
    calls = []
    cm = _manager(monkeypatch, calls)
    cm.connect("uaa-vcenter")
    assert calls == ["vcenter-prod"]


def test_backend_cache_keys_stay_per_target_and_account(monkeypatch):
    calls = []
    cm = _manager(monkeypatch, calls)
    a = cm.connect("v9-vcenter")
    b = cm.connect("v9-vcenter")
    assert a is b  # cached per (target, routed account); resolve not repeated
    assert calls == ["vcenter-v9", "vcenter-v9"]
