"""Dead-session eviction for ROUTED pool keys (Component 6 on the 2.x SessionPool).

Upstream pins the unrouted shapes in
tests/eval/regression/test_session_probe_eviction.py (probe raises, probe
returns None, live session kept). Since the 2.x sync the pool owns liveness for
every key, so what this fork must pin is the routed half: a dead
"<target>#<item>" session is evicted and recreated WITH the routed credentials
(never the startup credential), and an unrouted caller never receives a routed
session. No pyVmomi, 1P, or vCenter required: _create_connection is faked and
uaa_hub_routing is a stub, so this runs in the no-lib CI shape.
"""
import types

import pytest

from vmware_aiops import connection as conn_mod
from vmware_aiops.config import AppConfig, TargetConfig


class _RaisingSess:
    @property
    def currentSession(self):
        raise RuntimeError("NotAuthenticated")


class _NoneSess:
    currentSession = None


class _LiveSess:
    currentSession = object()


class FakeSI:
    def __init__(self, sess, user=None, pwd=None):
        class _Content:
            sessionManager = sess
        self.content = _Content()
        self.user = user
        self.pwd = pwd


class _RoutingError(Exception):
    pass


ITEM = "MCP - syseng_elevated - vcenter-v9"


@pytest.fixture
def rig(monkeypatch):
    """ConnectionManager with a stub routing lib; ``state['sess']`` picks the
    liveness shape of the NEXT created session, ``created`` records creds."""
    state = {"routed": True, "sess": _LiveSess}
    created: list[tuple[str | None, str | None]] = []
    fake_lib = types.SimpleNamespace(
        routing_item=lambda backend, sel: ITEM if state["routed"] else None,
        resolve_fields=lambda item: {"username": "svc-elev", "password": "elev-pw"},
        RoutingError=_RoutingError,
    )
    monkeypatch.setattr(conn_mod, "uaa_hub_routing", fake_lib, raising=False)
    monkeypatch.setattr(conn_mod, "_HUB_ROUTING", True)
    monkeypatch.setattr(conn_mod, "_VCENTER_SELECTOR", object(), raising=False)
    monkeypatch.setattr(conn_mod, "_release_si", lambda si: None)

    def create(t, *, user=None, pwd=None):
        created.append((user, pwd))
        return FakeSI(state["sess"](), user or t.username, pwd)

    monkeypatch.setattr(conn_mod.ConnectionManager, "_create_connection", staticmethod(create))
    target = TargetConfig(name="uaa-vcenter", host="vc.example", config_username="startup", verify_ssl=False)
    cm = conn_mod.ConnectionManager(AppConfig(targets=(target,)))
    return cm, state, created


def test_routed_key_shape(rig):
    cm, _, _ = rig
    cm.connect()
    assert cm.list_connected() == [f"uaa-vcenter#{ITEM}"]


def test_live_routed_session_is_reused(rig):
    cm, _, created = rig
    assert cm.connect() is cm.connect()
    assert created == [("svc-elev", "elev-pw")]


@pytest.mark.parametrize("dead", [_RaisingSess, _NoneSess])
def test_dead_routed_session_evicted_and_recreated_with_routed_creds(rig, dead):
    cm, state, created = rig
    state["sess"] = dead
    corpse = cm.connect()          # pool stores it; the probe only runs on the next get
    state["sess"] = _LiveSess
    fresh = cm.connect()
    assert fresh is not corpse
    assert created == [("svc-elev", "elev-pw"), ("svc-elev", "elev-pw")]  # routed both times
    assert cm.connect() is fresh   # healthy afterwards


def test_unrouted_caller_never_gets_the_routed_session(rig):
    cm, state, created = rig
    routed_si = cm.connect()
    state["routed"] = False
    plain_si = cm.connect()
    assert plain_si is not routed_si
    assert created[-1] == (None, None)            # startup credential path
    assert sorted(cm.list_connected()) == ["uaa-vcenter", f"uaa-vcenter#{ITEM}"]


def test_disconnect_drops_routed_keys_too(rig):
    cm, state, _ = rig
    cm.connect()
    state["routed"] = False
    cm.connect()
    cm.disconnect("uaa-vcenter")
    assert cm.list_connected() == []
