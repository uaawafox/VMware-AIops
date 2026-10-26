"""Local state that MCP calls running side by side share (HLD §16 D-13(2)).

Once calls run in worker threads at once, a load-modify-save of a JSON file and
a check-then-set of a plan's status are races. Each test widens the race window
with a sleep inside the real code path, so the unlocked version loses.
"""

from __future__ import annotations

import json
import stat
import threading
import time

import pytest

from vmware_aiops._fsutil import atomic_write_text
from vmware_aiops.ops import plan_executor, planner, ttl

pytestmark = pytest.mark.unit


def _run(threads: list[threading.Thread]) -> None:
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
        assert not t.is_alive()


def test_concurrent_ttl_sets_keep_every_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(ttl, "_TTL_FILE", tmp_path / "ttl.json")
    real_load = ttl._load_ttl_store

    def slow_load():
        store = real_load()
        time.sleep(0.05)  # the window between reading and writing the store
        return store

    monkeypatch.setattr(ttl, "_load_ttl_store", slow_load)
    names = [f"vm-{i}" for i in range(5)]
    _run([threading.Thread(target=ttl.set_ttl, args=(n, 60)) for n in names])
    stored = json.loads((tmp_path / "ttl.json").read_text(encoding="utf-8"))
    assert sorted(stored) == names, "a concurrent set_ttl dropped another call's entry"


def test_one_plan_is_never_applied_twice_at_once(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    planner._PLANS_DIR.mkdir(parents=True)
    plan = {
        "plan_id": "plan-race",
        "created_at": "2026-10-15T00:00:00+00:00",
        "target": "vc01",
        "status": "pending",
        "summary": {"total_steps": 1},
        "steps": [
            {
                "index": 1,
                "action": "power_on",
                "params": {"vm_name": "web-01"},
                "rollback_action": None,
                "rollback_params": None,
                "status": "pending",
                "result": None,
                "executed_at": None,
            }
        ],
    }
    (planner._PLANS_DIR / "plan-race.json").write_text(json.dumps(plan), encoding="utf-8")

    real_load = plan_executor.load_plan

    def slow_load(plan_id):
        loaded = real_load(plan_id)
        time.sleep(0.2)  # both calls would read "pending" before either saves
        return loaded

    ran: list[str] = []
    monkeypatch.setattr(plan_executor, "load_plan", slow_load)
    monkeypatch.setattr(plan_executor, "_dispatch", lambda si, action, params: ran.append(action))

    results: list[dict] = []
    _run(
        [
            threading.Thread(
                target=lambda: results.append(plan_executor.apply_plan(None, "plan-race"))
            )
            for _ in range(2)
        ]
    )
    assert ran == ["power_on"], f"the plan's steps ran {len(ran)} times"
    assert sum(1 for r in results if "being applied or rolled back" in r.get("error", "")) == 1


def test_a_finished_apply_releases_the_plan(monkeypatch, tmp_path):
    with plan_executor._plan_claimed("p1") as first:
        assert first
        with plan_executor._plan_claimed("p1") as second:
            assert not second
        with plan_executor._plan_claimed("p2") as other:
            assert other, "a different plan must not be blocked"
    with plan_executor._plan_claimed("p1") as again:
        assert again


def test_atomic_write_is_owner_only_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "ttl.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["ttl.json"]


def test_daemon_jobs_each_hold_their_own_sessions(monkeypatch, tmp_path):
    """The scan and TTL jobs share one manager on APScheduler's thread pool."""
    from unittest.mock import MagicMock

    from vmware_policy.pool import in_call_scope

    from vmware_aiops.scanner import scheduler

    seen: dict[str, bool] = {}
    jobs: list = []

    class _FakeScheduler:
        def add_job(self, func, **kw):
            jobs.append((kw["id"], func, kw.get("args", [])))

        def start(self):
            for _id, func, args in jobs:
                func(*args)

        def shutdown(self, wait=False):
            pass

    cfg = MagicMock()
    cfg.scanner.enabled = True
    cfg.scanner.interval_minutes = 15
    monkeypatch.setattr(scheduler, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(scheduler, "ConnectionManager", lambda c: MagicMock())
    monkeypatch.setattr(scheduler, "BlockingScheduler", _FakeScheduler)
    monkeypatch.setattr(scheduler, "PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr(scheduler.signal, "signal", lambda *a: None)
    monkeypatch.setattr(
        scheduler, "_run_scan", lambda *a: seen.__setitem__("scan", in_call_scope())
    )
    monkeypatch.setattr(
        scheduler, "_run_ttl_check", lambda *a: seen.__setitem__("ttl", in_call_scope())
    )
    scheduler.start_scheduler()
    assert {j[0] for j in jobs} == {"vmware_scan", "vmware_ttl"}
    assert seen == {"scan": True, "ttl": True}


def test_a_plan_being_saved_is_never_read_half_written(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    planner._PLANS_DIR.mkdir(parents=True)
    plan = {"plan_id": "plan-rw", "status": "running", "steps": [{"n": i} for i in range(400)]}
    planner.save_plan(plan)
    stop = threading.Event()
    errors: list = []

    def writer() -> None:
        i = 0
        while not stop.is_set():
            i += 1
            planner.save_plan({**plan, "status": f"step-{i}"})

    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(800):
            try:
                assert planner.load_plan("plan-rw") is not None
            except json.JSONDecodeError as exc:
                errors.append(exc)
    finally:
        stop.set()
        t.join(5)
    assert not errors
