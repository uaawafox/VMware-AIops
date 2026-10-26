"""migrate_vm's preview asks vCenter whether the move would be accepted.

Real hardware, 2026-10-17: a live vMotion was refused by vCenter at execution
with a CPU-feature fault (FeatureRequirementsNotMet) while the preview had shown
no blocker. The measurement now runs ``ProvisioningChecker.CheckMigrate_Task``
(read-only) and:

* turns every ``vim.vm.check.Result.error`` into a blocker (with the CPU remedy
  when the fault is a CPU fault), so ``confirm=True`` refuses before Relocate;
* lists ``warning`` entries without blocking;
* says visibly "not checked" — without blocking — when there is no checker
  (standalone ESXi), or the check raises, fails or outlives its wait budget.
  vCenter re-runs these checks when the Relocate task starts, so an unchecked
  preview cannot let an incompatible move through; it only loses the early
  warning. Refusing would block every migration on a target without a checker.

The same measurement serves the plan gate and the CLI ``vm migrate`` command.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pyVmomi import vim
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.vm as vm_tools
from vmware_aiops.ops import inventory, vm_gate, vm_lifecycle

ON = vim.VirtualMachine.PowerState.poweredOn
ALL_TESTS = {"sourceTests", "hostTests", "resourcePoolTests", "datastoreTests",
             "networkTests"}
CPU_MSG = "The target host does not support the virtual machine's current hardware requirements."


# ─── pyVmomi-shaped fakes ────────────────────────────────────────────────────


def _task(results, moid="task-77"):
    return SimpleNamespace(
        _moId=moid, info=SimpleNamespace(state=vim.TaskInfo.State.success, result=results))


class _Checker:
    """A ProvisioningChecker that records its calls and answers with ``results``."""

    def __init__(self, results=(), raises=None):
        self.results = list(results)
        self.raises = raises
        self.calls: list[dict] = []

    def CheckMigrate_Task(self, **kwargs):  # noqa: N802 — the vSphere method name
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return _task(self.results)


def _result(errors=(), warnings=()):
    return vim.vm.check.Result(error=list(errors), warning=list(warnings))


def _cpu_fault(msg=CPU_MSG):
    return vim.fault.FeatureRequirementsNotMet(msg=msg)


def _host(name, pool=True):
    h = MagicMock(name=f"host:{name}")
    h.name = name
    h.runtime.connectionState = "connected"
    h.runtime.inMaintenanceMode = False
    h.parent.resourcePool = MagicMock(name=f"pool:{name}") if pool else None
    return h


def _world():
    src, dst = _host("esx-01"), _host("esx-02")
    ds1 = MagicMock(name="ds:ds-shared")
    ds1.name = "ds-shared"
    ds1.host = [MagicMock(key=src), MagicMock(key=dst)]
    ds2 = MagicMock(name="ds:ds-other")
    ds2.name = "ds-other"
    ds2.host = [MagicMock(key=dst)]
    vm = MagicMock(name="vm:web-01")
    vm.name = "web-01"
    vm.runtime.powerState = ON
    vm.runtime.host = src
    vm.config.instanceUuid = "5012-aaaa"
    vm.datastore = [ds1]
    return vm, src, dst, ds1, ds2


def _si(checker):
    si = MagicMock(name="si")
    si.RetrieveContent.return_value.vmProvisioningChecker = checker
    return si


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    real_wait = vm_lifecycle._wait_for_task

    def wait(task, **kw):
        # The check task is a fake with a real TaskInfo state; Relocate's is a mock.
        return real_wait(task, **kw) if isinstance(task, SimpleNamespace) else None

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", wait)
    monkeypatch.setattr(vm_lifecycle.time, "sleep", lambda _s: None)
    # RelocateSpec type-checks its fields; the hosts here are mocks.
    monkeypatch.setattr(vim.vm, "RelocateSpec", lambda **kw: SimpleNamespace(**kw))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def world(monkeypatch):
    """Install a world and a checker; returns (vm, dst, si) for the given checker."""
    def install(checker):
        vm, src, dst, ds1, ds2 = _world()
        by_type = {vim.VirtualMachine: [vm], vim.HostSystem: [src, dst],
                   vim.Datastore: [ds1, ds2]}

        def collect(_si, types, _props):
            return [(o, {"name": o.name}) for t in types for o in by_type.get(t, ())]

        si = _si(checker)
        monkeypatch.setattr(inventory, "_collect", collect)
        monkeypatch.setattr(vm_tools, "_get_connection", lambda _t=None: si)
        return vm, dst, si
    return install


# ═══ errors block ════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_a_cpu_fault_from_the_checker_is_a_blocker_with_the_remedy(world):
    checker = _Checker([_result(errors=[_cpu_fault()])])
    vm, dst, _si_ = world(checker)
    br = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]
    assert len(br["blockers"]) == 1
    blocker = br["blockers"][0]
    assert "FeatureRequirementsNotMet" in blocker
    assert "does not support" in blocker
    assert "Power the VM off" in blocker and "EVC" in blocker
    assert br["compatibility"]["checked"] is True
    assert br["compatibility"]["errors"] and br["compatibility"]["warnings"] == []
    assert br["unmeasured"] == []
    # The checker was asked about this VM, this host and its pool, as it runs now.
    (call,) = checker.calls
    assert call["vm"] is vm and call["host"] is dst
    assert call["pool"] is dst.parent.resourcePool
    assert call["state"] is None
    assert set(call["testType"]) == ALL_TESTS


@pytest.mark.unit
def test_a_checker_error_refuses_confirm_before_relocate(world):
    vm, dst, _si_ = world(_Checker([_result(errors=[_cpu_fault()])]))
    out = vm_tools.migrate_vm("web-01", "esx-02", confirm=True)
    vm.Relocate.assert_not_called()
    assert "FeatureRequirementsNotMet" in out["error"]
    assert "Power the VM off" in out["error"]


@pytest.mark.unit
def test_a_non_cpu_error_blocks_without_the_cpu_remedy(world):
    fault = vim.fault.DisallowedMigrationDeviceAttached(
        msg="A CD-ROM backed by a host device is attached.")
    vm, dst, _si_ = world(_Checker([_result(errors=[fault])]))
    br = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]
    (blocker,) = br["blockers"]
    assert "DisallowedMigrationDeviceAttached" in blocker and "CD-ROM" in blocker
    assert "EVC" not in blocker


@pytest.mark.unit
def test_errors_across_several_results_are_all_blockers(world):
    vm, dst, _si_ = world(_Checker([
        _result(errors=[_cpu_fault()]),
        _result(errors=[vim.fault.MigrationFault(msg="network 'VM Net' is not on the host")]),
    ]))
    br = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]
    assert len(br["blockers"]) == 2
    assert any("VM Net" in b for b in br["blockers"])


@pytest.mark.unit
def test_fault_text_falls_back_to_fault_message_then_class_name(world):
    with_fm = vim.fault.MigrationFault(
        msg="", faultMessage=[vim.LocalizableMessage(key="k", message="from faultMessage")])
    bare = vim.fault.MigrationFault()
    vm, dst, _si_ = world(_Checker([_result(errors=[with_fm, bare])]))
    blockers = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]["blockers"]
    assert any("from faultMessage" in b for b in blockers)
    assert any("MigrationFault" in b for b in blockers)


@pytest.mark.unit
def test_fault_text_is_sanitized_and_short(world):
    noisy = _cpu_fault("bad\x00\x1b[31m" + "x" * 5000)
    vm, dst, _si_ = world(_Checker([_result(errors=[noisy])]))
    (blocker,) = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]["blockers"]
    assert "\x00" not in blocker and "\x1b" not in blocker
    assert len(blocker) < 1000
    assert "Power the VM off" in blocker  # the remedy survives the cut


# ═══ warnings do not block ═══════════════════════════════════════════════════


@pytest.mark.unit
def test_warnings_are_listed_but_do_not_block(world):
    warn = vim.fault.MigrationFault(
        msg="Network interface 'eth1' uses network 'iso', which is not accessible.")
    vm, dst, _si_ = world(_Checker([_result(warnings=[warn])]))
    br = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]
    assert br["blockers"] == []
    assert br["compatibility"]["checked"] is True
    assert any("eth1" in w for w in br["compatibility"]["warnings"])
    out = vm_tools.migrate_vm("web-01", "esx-02", confirm=True)
    vm.Relocate.assert_called_once()
    assert out["action"] == "migrated"


@pytest.mark.unit
def test_a_clean_check_says_checked_and_migrates(world):
    vm, dst, _si_ = world(_Checker([_result()]))
    br = vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"]
    assert br["compatibility"] == {"checked": True, "errors": [], "warnings": [],
                                   "tests": sorted(ALL_TESTS)}
    assert vm_tools.migrate_vm("web-01", "esx-02", confirm=True)["action"] == "migrated"


# ═══ not checked: visible, never blocking ════════════════════════════════════


def _assert_not_checked(br, *needles):
    comp = br["compatibility"]
    assert comp["checked"] is False
    for n in needles:
        assert n in comp["reason"], comp["reason"]
    assert br["blockers"] == [] and br["unmeasured"] == []


@pytest.mark.unit
def test_no_checker_on_standalone_esxi_is_not_checked_and_does_not_block(world):
    vm, dst, _si_ = world(None)
    _assert_not_checked(vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"], "ESXi")
    assert vm_tools.migrate_vm("web-01", "esx-02", confirm=True)["action"] == "migrated"


@pytest.mark.unit
def test_a_check_call_that_raises_is_not_checked(world):
    denied = vim.fault.NoPermission(msg="no", privilegeId="System.View")
    vm, dst, _si_ = world(_Checker(raises=denied))
    _assert_not_checked(vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"], "NoPermission")
    assert vm_tools.migrate_vm("web-01", "esx-02", confirm=True)["action"] == "migrated"


@pytest.mark.unit
def test_a_check_that_outlives_its_budget_is_not_checked(world, monkeypatch):
    seen: dict = {}

    def wait(task, timeout=300):
        if isinstance(task, SimpleNamespace):
            seen["timeout"] = timeout
            raise vm_lifecycle.TaskStillRunning(task._moId, timeout)
        return None

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", wait)
    vm, dst, _si_ = world(_Checker([_result(errors=[_cpu_fault()])]))
    _assert_not_checked(vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"], "60")
    assert 0 < seen["timeout"] <= 60


@pytest.mark.unit
def test_a_failed_check_task_is_not_checked(world, monkeypatch):
    def wait(task, timeout=300):
        if isinstance(task, SimpleNamespace):
            raise vm_lifecycle.TaskFailedError("Task failed: boom")
        return None

    monkeypatch.setattr(vm_lifecycle, "_wait_for_task", wait)
    vm, dst, _si_ = world(_Checker([_result()]))
    _assert_not_checked(vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"], "boom")


@pytest.mark.unit
def test_a_check_with_no_result_list_is_not_checked(world):
    checker = _Checker()
    checker.CheckMigrate_Task = lambda **kw: _task(None)  # type: ignore[method-assign]
    vm, dst, _si_ = world(checker)
    _assert_not_checked(vm_tools.migrate_vm("web-01", "esx-02")["blast_radius"], "no result")


# ═══ what is checked ═════════════════════════════════════════════════════════


@pytest.mark.unit
def test_with_a_target_datastore_the_current_datastore_is_not_tested(world):
    checker = _Checker([_result()])
    vm, dst, _si_ = world(checker)
    br = vm_tools.migrate_vm("web-01", "esx-02", to_datastore="ds-other")["blast_radius"]
    (call,) = checker.calls
    assert "datastoreTests" not in call["testType"]
    assert set(call["testType"]) == ALL_TESTS - {"datastoreTests"}
    assert br["compatibility"]["tests"] == sorted(ALL_TESTS - {"datastoreTests"})


@pytest.mark.unit
def test_a_noop_or_unknown_host_does_not_run_the_check(world):
    checker = _Checker([_result(errors=[_cpu_fault()])])
    vm, dst, _si_ = world(checker)
    assert vm_tools.migrate_vm("web-01", "esx-01")["action"] == "noop"
    br = vm_tools.migrate_vm("web-01", "esx-99")["blast_radius"]
    assert checker.calls == []
    assert br["compatibility"]["checked"] is False


# ═══ the other two callers ═══════════════════════════════════════════════════


@pytest.mark.unit
def test_the_plan_gate_migrate_measurement_carries_the_blocker(world):
    from vmware_aiops.ops import plan_gate

    vm, dst, si = world(_Checker([_result(errors=[_cpu_fault()])]))
    radius = plan_gate._MEASURES["migrate"](si, {"vm_name": "web-01", "target_host": "esx-02"})
    assert any("FeatureRequirementsNotMet" in b for b in radius["blockers"])


def _cli(world, monkeypatch, checker, *extra):
    from typer.testing import CliRunner

    from vmware_aiops.cli import app

    vm, dst, si = world(checker)
    asked: list = []
    monkeypatch.setattr("vmware_aiops.cli.vm._get_connection", lambda *a, **k: (si, MagicMock()))
    monkeypatch.setattr(vm_lifecycle, "get_vm_info", lambda _si, _n: {"host": "esx-01"})
    monkeypatch.setattr("vmware_aiops.cli.vm._double_confirm", lambda *a, **k: asked.append(a))
    result = CliRunner().invoke(
        app, ["vm", "migrate", "web-01", "--to-host", "esx-02", "--target", "t", *extra])
    return vm, result, asked


@pytest.mark.unit
def test_cli_migrate_refuses_on_a_checker_error_before_asking(world, monkeypatch):
    vm, result, asked = _cli(world, monkeypatch, _Checker([_result(errors=[_cpu_fault()])]))
    assert result.exit_code == 1, result.output
    assert "FeatureRequirementsNotMet" in result.output
    assert asked == []
    vm.Relocate.assert_not_called()


@pytest.mark.unit
def test_cli_migrate_dry_run_shows_the_check(world, monkeypatch):
    vm, result, asked = _cli(world, monkeypatch, _Checker([_result(errors=[_cpu_fault()])]),
                             "--dry-run")
    assert result.exit_code == 0, result.output
    assert "FeatureRequirementsNotMet" in result.output
    vm.Relocate.assert_not_called()


@pytest.mark.unit
def test_cli_migrate_without_a_checker_says_not_checked_and_proceeds(world, monkeypatch):
    vm, result, asked = _cli(world, monkeypatch, None)
    assert result.exit_code == 0, result.output
    assert "not checked" in result.output
    assert len(asked) == 1
    vm.Relocate.assert_called_once()


@pytest.mark.unit
def test_compatibility_helper_is_exported_for_the_cli():
    assert callable(vm_gate.migration_compatibility)


@pytest.mark.unit
def test_cli_check_skips_a_move_to_the_host_the_vm_is_on(monkeypatch):
    """Review 2026-10-18: the CLI ran the check for a no-op that migrate_radius skips."""
    from types import SimpleNamespace

    from vmware_aiops.ops import vm_gate, vm_lifecycle

    host = object()
    vm = SimpleNamespace(runtime=SimpleNamespace(host=host))
    monkeypatch.setattr(vm_lifecycle, "_require_vm", lambda si, n: vm)
    monkeypatch.setattr(vm_gate, "find_host_by_name", lambda si, n: host)
    monkeypatch.setattr(vm_gate, "migration_compatibility",
                        lambda *a, **k: pytest.fail("checked a no-op"))

    out = vm_gate.migration_compatibility_by_name(object(), "web-01", "esx-1", None)
    assert out["checked"] is False and "already on that host" in out["reason"]
