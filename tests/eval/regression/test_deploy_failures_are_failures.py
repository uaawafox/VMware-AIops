"""A deploy that did not happen must not be reported, or audited, as success.

Real hardware, 2026-10-17: ``deploy_vm_from_ova`` with a datastore name that
does not exist returned the string ``"Datastore 'x' not found."``. The ops layer
*returned* it instead of raising, so ``@tool_errors`` never saw an exception:
the MCP frame said success, the audit row said ``status=ok``, and the plan
executor and batch deploy counted the step as done. The same shape ran through
every early exit in ``vm_deploy`` / ``ova_deploy``.

These tests assert the audit status and the raised type -- the returned text
was never the problem.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from vmware_policy.budget import reset_budget
from vmware_policy.policy import reset_policy_engine
from vmware_policy.undo import reset_undo_store

import vmware_aiops.mcp_server.tools.deploy as deploy_tools
from vmware_aiops.ops import ova_deploy, vm_deploy


@pytest.fixture(autouse=True)
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_HOME", str(tmp_path))
    reset_policy_engine()
    reset_budget()
    reset_undo_store()
    yield
    reset_policy_engine()
    reset_budget()
    reset_undo_store()


@pytest.fixture
def audit_rows(monkeypatch):
    rows: list[dict] = []

    class _Recorder:
        def log(self, **kw):
            rows.append(kw)

    monkeypatch.setattr("vmware_policy.guard.get_engine", lambda: _Recorder())
    return rows


@pytest.mark.unit
def test_ova_deploy_to_a_missing_datastore_raises(monkeypatch) -> None:
    monkeypatch.setattr(ova_deploy, "find_datastore_by_name", lambda si, n: None)
    with pytest.raises(vm_deploy.DeployError, match="list_all_datastores"):
        vm_deploy.deploy_ova(MagicMock(), "/x.ova", "vm1", datastore_name="nope")


@pytest.mark.unit
def test_template_deploy_of_a_missing_template_raises(monkeypatch) -> None:
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: None)
    with pytest.raises(vm_deploy.DeployError, match="not found"):
        vm_deploy.deploy_from_template(MagicMock(), "tmpl", "vm1")


@pytest.mark.unit
def test_template_deploy_to_a_missing_datastore_raises(monkeypatch) -> None:
    tmpl = MagicMock()
    tmpl.config.template = True
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: tmpl)
    monkeypatch.setattr(vm_deploy, "find_datastore_by_name", lambda si, n: None)
    with pytest.raises(vm_deploy.DeployError, match="list_all_datastores"):
        vm_deploy.deploy_from_template(MagicMock(), "tmpl", "vm1", datastore_name="nope")


@pytest.mark.unit
def test_linked_clone_of_a_missing_source_raises(monkeypatch) -> None:
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: None)
    with pytest.raises(vm_deploy.DeployError):
        vm_deploy.linked_clone(MagicMock(), "src", "vm1", snapshot_name="s")


@pytest.mark.unit
def test_ova_tool_failure_is_audited_as_error(audit_rows, monkeypatch) -> None:
    monkeypatch.setattr(deploy_tools, "_get_connection", lambda target=None: MagicMock())
    monkeypatch.setattr(ova_deploy, "find_datastore_by_name", lambda si, n: None)

    returned = deploy_tools.deploy_vm_from_ova("/x.ova", "vm1", "nope", target="t")

    assert returned.startswith("Error:")
    assert "nope" in returned
    assert audit_rows and audit_rows[0]["status"] == "error"


@pytest.mark.unit
def test_template_tool_failure_is_audited_as_error(audit_rows, monkeypatch) -> None:
    monkeypatch.setattr(deploy_tools, "_get_connection", lambda target=None: MagicMock())
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: None)

    returned = deploy_tools.deploy_vm_from_template("tmpl", "vm1", target="t")

    assert returned.startswith("Error:")
    assert audit_rows and audit_rows[0]["status"] == "error"


@pytest.mark.unit
def test_batch_deploy_marks_a_failed_vm_as_error(monkeypatch) -> None:
    """A failed template deploy used to land in 'messages' with status ok."""
    monkeypatch.setattr(vm_deploy, "find_vm_by_name", lambda si, n: None)
    spec = {"template": "tmpl", "vms": [{"name": "vm1"}]}
    monkeypatch.setattr(vm_deploy, "load_deploy_spec", lambda p: spec)

    results = vm_deploy.batch_deploy(MagicMock(), "spec.yaml")

    assert results[0]["status"] == "error"


@pytest.mark.unit
def test_create_vm_on_a_missing_datastore_raises(monkeypatch) -> None:
    """The same shape one module over: create_vm returned this string too."""
    from vmware_aiops.ops import inventory, vm_lifecycle

    monkeypatch.setattr(vm_lifecycle, "resolve_datacenter", lambda si, *a, **k: MagicMock())
    monkeypatch.setattr(vm_lifecycle, "find_compute_resource", lambda *a, **k: MagicMock())
    monkeypatch.setattr(vm_lifecycle, "find_datastore_by_name", lambda si, n: None)
    with pytest.raises(inventory.InventoryError, match="list_all_datastores"):
        vm_lifecycle.create_vm(MagicMock(), "vm1", datastore_name="nope")
