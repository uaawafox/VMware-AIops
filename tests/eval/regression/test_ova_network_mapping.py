"""Regression: deploy_vm_from_ova ignored ``network_name`` (found in review, 2026-10-16).

The import spec was built with ``entityName`` only, so every NIC landed on
whatever network the OVF descriptor named (or none), whatever the caller asked
for. Now each network the OVF declares is mapped to the named vSphere network,
and a name that does not resolve to exactly one network is refused before
anything is imported.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pyVmomi import vim

from vmware_aiops.ops import ova_deploy
from vmware_aiops.ops.inventory import InventoryError


def _network(name: str) -> MagicMock:
    net = MagicMock(spec=vim.Network)
    net.name = name
    return net


class Fakes:
    """A datacenter with ``networks`` and an OVF declaring ``ovf_networks``."""

    def __init__(self, networks_in_dc, ovf_networks, parse_errors=None):
        lease = MagicMock()
        lease.state = vim.HttpNfcLease.State.ready
        lease.info.deviceUrl = []
        self.resource_pool = MagicMock()
        self.resource_pool.ImportVApp.return_value = lease
        self.ovf_manager = MagicMock()
        self.ovf_manager.CreateImportSpec.return_value = SimpleNamespace(
            error=None, warning=None, fileItem=[], importSpec=MagicMock()
        )
        self.ovf_manager.ParseDescriptor.return_value = SimpleNamespace(
            error=parse_errors,
            warning=None,
            network=[SimpleNamespace(name=n) for n in ovf_networks],
        )
        content = MagicMock()
        content.ovfManager = self.ovf_manager
        self.si = MagicMock()
        self.si.RetrieveContent.return_value = content
        self.dc = MagicMock(spec=vim.Datacenter)
        self.dc.network = networks_in_dc
        self.cr = MagicMock(spec=vim.ComputeResource)
        self.cr.resourcePool = self.resource_pool

    def deploy(self, network_name="Prod-PG"):
        with (
            patch.object(ova_deploy, "find_datastore_by_name", return_value=MagicMock()),
            patch.object(ova_deploy, "resolve_datacenter", return_value=self.dc),
            patch.object(ova_deploy, "find_compute_resource", return_value=self.cr),
            patch.object(ova_deploy, "_read_ovf_from_ova", return_value=("<ovf/>", {})),
            patch.object(ova_deploy, "get_verify_ssl", return_value=False),
        ):
            return ova_deploy.deploy_ova(
                self.si, "/tmp/x.ova", "vm1", "ds1", network_name=network_name
            )


def test_every_ovf_network_is_mapped_to_the_named_network():
    prod = _network("Prod-PG")
    f = Fakes([_network("VM Network"), prod], ["nat", "bridged"])
    f.deploy()

    cisp = f.ovf_manager.CreateImportSpec.call_args.kwargs["cisp"]
    assert {m.name: m.network for m in cisp.networkMapping} == {"nat": prod, "bridged": prod}


def test_an_unknown_network_is_refused_with_the_choices_and_nothing_is_imported():
    f = Fakes([_network("VM Network"), _network("Mgmt")], ["nat"])
    with pytest.raises(InventoryError) as exc:
        f.deploy(network_name="Prod-PG")
    msg = str(exc.value)
    assert "Prod-PG" in msg and "VM Network" in msg and "Mgmt" in msg
    f.ovf_manager.CreateImportSpec.assert_not_called()
    f.resource_pool.ImportVApp.assert_not_called()


def test_an_ambiguous_network_name_is_refused_and_nothing_is_imported():
    # A standard port group and a distributed port group can share a name.
    f = Fakes([_network("Prod-PG"), _network("Prod-PG")], ["nat"])
    with pytest.raises(InventoryError, match="more than one"):
        f.deploy()
    f.resource_pool.ImportVApp.assert_not_called()


def test_an_ovf_without_networks_needs_no_mapping():
    f = Fakes([], [])
    f.deploy()
    cisp = f.ovf_manager.CreateImportSpec.call_args.kwargs["cisp"]
    assert not cisp.networkMapping
    f.resource_pool.ImportVApp.assert_called_once()


def test_a_descriptor_the_server_cannot_parse_is_refused():
    f = Fakes([_network("Prod-PG")], ["nat"], parse_errors=[SimpleNamespace(msg="bad descriptor")])
    with pytest.raises(InventoryError, match="bad descriptor"):
        f.deploy()
    f.resource_pool.ImportVApp.assert_not_called()
