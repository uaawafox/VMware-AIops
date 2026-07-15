"""Dead-session eviction tests for ConnectionManager.connect().

The 2026-07-15 vmware-monitor v9 defect: the liveness probe's except clause
named vmodl.fault.NotAuthenticated, which does not exist (the real fault is
vim.fault.NotAuthenticated) - and Python evaluates except-tuples at catch
time, so the handler itself raised AttributeError, skipped the cache
eviction, and permafailed the target until a service restart. These tests
pin the fixed behavior for BOTH dead-session shapes: probe raises, and
currentSession None without raising. No pyVmomi, 1P, or vCenter required;
runs with routing absent (_HUB_ROUTING False), which is also the CI shape.
"""
import pytest

from vmware_aiops import connection as conn_mod
from vmware_aiops.config import AppConfig, TargetConfig


class _RaisingSess:
    """Expired-session shape 1: property access raises (as NotAuthenticated does)."""
    @property
    def currentSession(self):
        raise RuntimeError("NotAuthenticated")


class _NoneSess:
    """Expired-session shape 2: returns None without raising."""
    currentSession = None


class _LiveSess:
    currentSession = object()


class FakeSI:
    def __init__(self, sess):
        class _Content:
            sessionManager = sess
        self.content = _Content()


@pytest.fixture
def cm(monkeypatch):
    monkeypatch.setattr(conn_mod, "_HUB_ROUTING", False)
    target = TargetConfig(name="prod", host="vc.example", username="u", verify_ssl=False)
    cfg = AppConfig(targets=(target,))

    def fake_create(t, *, user=None, pwd=None):
        return FakeSI(_LiveSess())

    monkeypatch.setattr(conn_mod.ConnectionManager, "_create_connection", staticmethod(fake_create))
    return conn_mod.ConnectionManager(cfg)


def test_live_session_returned_from_cache(cm):
    a = cm.connect()
    b = cm.connect()
    assert a is b


def test_raising_probe_evicts_and_reconnects(cm):
    corpse = FakeSI(_RaisingSess())
    cm._connections["prod"] = corpse
    si = cm.connect()
    assert si is not corpse                      # never hand back the corpse
    assert cm._connections["prod"] is si         # fresh session re-cached
    assert cm.connect() is si                    # and healthy afterwards


def test_none_current_session_evicts_and_reconnects(cm):
    corpse = FakeSI(_NoneSess())
    cm._connections["prod"] = corpse
    si = cm.connect()
    assert si is not corpse
    assert cm._connections["prod"] is si
