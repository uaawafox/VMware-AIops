"""Deploy tools: OVA/template/linked-clone/batch provisioning, ISO attach,
template conversion."""

from typing import Optional

from vmware_policy import vmware_tool

from mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops import vm_deploy


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def deploy_vm_from_ova(
    ova_path: str,
    vm_name: str,
    datastore_name: str,
    network_name: str = "VM Network",
    folder_path: Optional[str] = None,
    power_on: bool = False,
    snapshot_name: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Create a new VM by importing a local .ova file (OVF parse + VMDK upload).

    Use for OVA appliance files on the local machine. For vSphere templates use
    deploy_vm_from_template; to copy an existing VM use vm_clone or deploy_linked_clone.
    Upload time scales with OVA size. Fails before creating anything if the datastore
    is not found. Audited to ~/.vmware/audit.db.

    Args:
        ova_path: Local filesystem path to the .ova file (must be readable by this server).
        vm_name: Name for the new VM; must not already exist.
        datastore_name: Target datastore name; discover with browse_datastore.
        network_name: Port group for the VM's NICs (default "VM Network").
        folder_path: vCenter VM folder path; omit to use the datacenter's root VM folder.
        power_on: True powers the VM on after import (default False).
        snapshot_name: If set, creates a baseline snapshot with this name after deploy.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        Status string with the deployed VM name, or an error naming the missing resource.
    """
    si = _get_connection(target)
    return vm_deploy.deploy_ova(
        si, ova_path=ova_path, vm_name=vm_name,
        datastore_name=datastore_name, network_name=network_name,
        folder_path=folder_path, power_on=power_on,
        snapshot_name=snapshot_name,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def deploy_vm_from_template(
    template_name: str,
    new_name: str,
    datastore_name: Optional[str] = None,
    cpu: Optional[int] = None,
    memory_mb: Optional[int] = None,
    power_on: bool = False,
    snapshot_name: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Deploy a new VM by cloning from a vSphere template.

    Args:
        template_name: Name of the source vSphere template.
        new_name: Name for the new VM.
        datastore_name: Target datastore (uses template's datastore if omitted).
        cpu: Override CPU count (optional).
        memory_mb: Override memory in MB (optional).
        power_on: Power on after deployment.
        snapshot_name: Create a baseline snapshot with this name (optional).
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return vm_deploy.deploy_from_template(
        si, template_name=template_name, new_name=new_name,
        datastore_name=datastore_name, cpu=cpu, memory_mb=memory_mb,
        power_on=power_on, snapshot_name=snapshot_name,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def deploy_linked_clone(
    source_vm_name: str,
    snapshot_name: str,
    new_name: str,
    cpu: Optional[int] = None,
    memory_mb: Optional[int] = None,
    power_on: bool = False,
    baseline_snapshot: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Create a linked clone from a VM snapshot — near-instant, minimal disk usage.

    The clone shares the source's base disk and writes changes to a copy-on-write delta
    disk, so it depends on the source VM staying intact. Fastest provisioning method for
    test/dev fleets; use vm_clone or deploy_vm_from_template for fully independent copies.
    Requires the source VM to have the named snapshot — run vm_list_snapshots first;
    unknown names return the available list. Audited to ~/.vmware/audit.db.

    Args:
        source_vm_name: Exact name of the source VM (must have at least one snapshot).
        snapshot_name: Snapshot on the source to use as the clone base (from vm_list_snapshots).
        new_name: Name for the new linked clone; must not already exist.
        cpu: Override vCPU count; omit to keep the source's value.
        memory_mb: Override memory in MB; omit to keep the source's value.
        power_on: True powers the clone on after creation (default False).
        baseline_snapshot: If set, creates a snapshot with this name on the new clone.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        Status string with the new clone name, or a snapshot/VM-not-found error.
    """
    si = _get_connection(target)
    return vm_deploy.linked_clone(
        si, source_vm_name=source_vm_name, new_name=new_name,
        snapshot_name=snapshot_name, cpu=cpu, memory_mb=memory_mb,
        power_on=power_on, baseline_snapshot=baseline_snapshot,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def attach_iso_to_vm(
    vm_name: str,
    iso_ds_path: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Mount a datastore ISO into a VM's virtual CD-ROM drive.

    Reconfigures the existing CD-ROM (replacing any currently mounted ISO) or adds a
    new CD-ROM on the VM's IDE controller if none exists; fails with a clear message
    if the VM has no IDE controller. Works whether the VM is powered on or off — the
    device is set connected and start-connected. Find ISO files first with
    browse_datastore using pattern "*.iso". Audited to ~/.vmware/audit.db.

    Args:
        vm_name: Exact VM name as shown in vCenter inventory.
        iso_ds_path: Full datastore path in bracket format, e.g.
            "[datastore1] iso/ubuntu-22.04.iso" (datastore name in brackets, then
            the path relative to the datastore root).
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        Status string confirming attachment, or a VM-not-found / no-IDE-controller error.
    """
    si = _get_connection(target)
    return vm_deploy.attach_iso(si, vm_name, iso_ds_path)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def convert_vm_to_template(
    vm_name: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Convert a powered-off VM to a vSphere template.

    After conversion the VM cannot be powered on — it serves as a
    clone source for deploy_vm_from_template.

    Args:
        vm_name: Name of the VM to convert (must be powered off).
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return vm_deploy.convert_to_template(si, vm_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("list")
def batch_clone_vms(
    source_vm_name: str,
    vm_names: list[str],
    cpu: Optional[int] = None,
    memory_mb: Optional[int] = None,
    snapshot_name: Optional[str] = None,
    power_on: bool = False,
    target: Optional[str] = None,
) -> list[dict]:
    """[WRITE] Batch clone multiple VMs from a source VM (gold image).

    Each clone: full copy → optional reconfigure → optional snapshot → optional power on.

    Args:
        source_vm_name: Source VM to clone from.
        vm_names: List of names for the new VMs.
        cpu: Override CPU count for all clones (optional).
        memory_mb: Override memory for all clones (optional).
        snapshot_name: Create a baseline snapshot on each clone (optional).
        power_on: Power on each clone after creation.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return vm_deploy.batch_clone(
        si, source_vm_name=source_vm_name, vm_names=vm_names,
        cpu=cpu, memory_mb=memory_mb,
        snapshot_name=snapshot_name, power_on=power_on,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("list")
def batch_linked_clone_vms(
    source_vm_name: str,
    snapshot_name: str,
    vm_names: list[str],
    cpu: Optional[int] = None,
    memory_mb: Optional[int] = None,
    power_on: bool = False,
    baseline_snapshot: Optional[str] = None,
    target: Optional[str] = None,
) -> list[dict]:
    """[WRITE] Batch create linked clones from a VM snapshot (fastest batch provisioning).

    Each clone shares the source disk via copy-on-write.

    Args:
        source_vm_name: Source VM to clone from.
        snapshot_name: Snapshot to use as clone base.
        vm_names: List of names for the new linked clones.
        cpu: Override CPU count (optional).
        memory_mb: Override memory (optional).
        power_on: Power on each clone.
        baseline_snapshot: Create a new snapshot on each clone (optional).
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return vm_deploy.batch_linked_clone(
        si, source_vm_name=source_vm_name, snapshot_name=snapshot_name,
        vm_names=vm_names, cpu=cpu, memory_mb=memory_mb,
        power_on=power_on, baseline_snapshot=baseline_snapshot,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("list")
def batch_deploy_from_spec(
    spec_path: str,
    target: Optional[str] = None,
) -> list[dict]:
    """[WRITE] Deploy multiple VMs in one call from a declarative YAML spec file.

    Use for fleet provisioning (several VMs, shared defaults); for a single VM prefer
    deploy_vm_from_template, vm_clone, deploy_vm_from_ova, or deploy_linked_clone.
    The provisioning channel is chosen by spec keys: top-level "source" (full clone),
    "template", "linked_clone: {source, snapshot}", per-VM "ova", else empty-VM creation
    (optionally with "iso"). A "defaults" block sets cpu/memory_mb/disk_gb/network/
    datastore/snapshot/power_on, overridable per VM. VMs deploy sequentially; one VM's
    failure is recorded and the rest continue. Audited to ~/.vmware/audit.db.

    Args:
        spec_path: Local filesystem path to the deploy.yaml specification file.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        One dict per VM: name, status ("ok" or "error"), and messages with per-step results.
    """
    si = _get_connection(target)
    return vm_deploy.batch_deploy(si, spec_path)
