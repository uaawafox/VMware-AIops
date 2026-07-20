"""Deploy tools: OVA/template/linked-clone/batch provisioning, ISO attach,
template conversion."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
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

    Use for local OVA files; for vSphere templates use deploy_vm_from_template, to
    copy an existing VM vm_clone. Returns a status string naming the new VM. Upload
    time scales with OVA size; fails before creating anything if the datastore is
    not found.

    Args:
        ova_path: Local path to the .ova file (must be readable by this server).
        vm_name: Name for the new VM; must not already exist.
        datastore_name: Target datastore (see browse_datastore).
        network_name: Port group for the NICs (default "VM Network").
        folder_path: vCenter folder path; omit for the datacenter root.
        power_on: Power the VM on after import (default False).
        snapshot_name: If set, snapshots the new VM with this name.
        target: vCenter/ESXi target from config.yaml; omit for the default.
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

    Returns a status string. Use for VMs marked as templates; for a running
    source VM use vm_clone. Requires an existing template —
    convert_vm_to_template makes one.

    Args:
        template_name: Source vSphere template name.
        new_name: Name for the new VM.
        datastore_name: Target datastore (template's own if omitted).
        cpu: Override CPU count (optional).
        memory_mb: Override memory in MB (optional).
        power_on: Power on after deployment.
        snapshot_name: Snapshot the new VM with this name.
        target: Optional vCenter/ESXi target from config.
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

    The clone writes to a copy-on-write delta over the source's base disk, so it
    depends on the source staying intact. Fastest provisioning for test/dev fleets;
    use vm_clone for fully independent copies. Requires the named snapshot — run
    vm_list_snapshots first. Returns a status string with the new clone name.

    Args:
        source_vm_name: Source VM name (must have at least one snapshot).
        snapshot_name: Clone base (from vm_list_snapshots).
        new_name: Name for the new linked clone; must not already exist.
        cpu: Override vCPU count; omit to keep the source's value.
        memory_mb: Override memory in MB; omit to keep the source's value.
        power_on: Power the clone on after creation (default False).
        baseline_snapshot: If set, snapshots the new clone with this name.
        target: vCenter/ESXi target from config.yaml; omit for the default.
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

    Reconfigures the existing CD-ROM (replacing any mounted ISO) or adds one on the
    VM's IDE controller; fails with a clear message if the VM has no IDE controller.
    Works whether the VM is powered on or off. Find ISO paths first with
    browse_datastore using pattern "*.iso". Returns a status string confirming
    attachment, or a VM-not-found / no-IDE-controller error.

    Args:
        vm_name: Exact VM name as shown in vCenter inventory.
        iso_ds_path: Datastore path in bracket format, e.g.
            "[datastore1] iso/ubuntu-22.04.iso".
        target: vCenter/ESXi target from config.yaml; omit for the default target.
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

    Returns a status string. Use this to freeze a golden image: afterwards the VM
    cannot be powered on and serves only as a clone source for
    deploy_vm_from_template.

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

    Each clone: full copy → optional reconfigure → optional snapshot → optional power
    on. Returns one dict per VM with its status. Clones run sequentially, so a long
    vm_names list may take a while — prefer batch_linked_clone_vms for disposable
    test copies.

    Args:
        source_vm_name: Source VM to clone from.
        vm_names: Names for the new VMs.
        cpu: Override CPU count for all clones (optional).
        memory_mb: Override memory for all clones (optional).
        snapshot_name: Snapshot each clone with this name (optional).
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

    Clones share the source disk via copy-on-write, so the source must stay intact.
    Returns one dict per clone. Prefer batch_clone_vms for independent copies.

    Args:
        source_vm_name: Source VM to clone from.
        snapshot_name: Clone base (from vm_list_snapshots).
        vm_names: Names for the new linked clones.
        cpu: Override CPU count (optional).
        memory_mb: Override memory (optional).
        power_on: Power on each clone.
        baseline_snapshot: Snapshot each clone with this name (optional).
        target: Optional vCenter/ESXi target from config.
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
    The channel is chosen by spec keys: "source" (full clone), "template",
    "linked_clone: {source, snapshot}", per-VM "ova", else empty-VM creation
    (optionally "iso"). A "defaults" block sets cpu/memory_mb/disk_gb/network/
    datastore/snapshot/power_on, overridable per VM. VMs deploy sequentially and
    one VM's failure does not stop the rest.

    Args:
        spec_path: Local filesystem path to the deploy.yaml specification file.
        target: vCenter/ESXi target name from config.yaml; omit to use the default target.

    Returns:
        One dict per VM: name, status ("ok" or "error"), and messages with per-step results.
    """
    si = _get_connection(target)
    return vm_deploy.batch_deploy(si, spec_path)
