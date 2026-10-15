"""vSphere privileges each MCP tool needs (HLD §17 D-17), read by ``get_access_info``.

Derived from the calls each tool's ops make, using pyVmomi's own metadata: a
method's ``privId``, a property's ``privId``, and — where the method itself
declares none — the ``privId`` pyVmomi attaches to the method's *parameters*
(e.g. ``CloneVM_Task(folder=…)`` → VirtualMachine.Inventory.CreateFromExisting,
``StartProgramInGuest(vm=…)`` → VirtualMachine.GuestOperations.Execute). Every tool
that finds an object by name reads through a ContainerView (System.View) and the
PropertyCollector; entity properties without a privId of their own are checked
as System.Read.

Some calls carry no privilege in pyVmomi's metadata because vSphere checks it inside
the method (``ReconfigVM_Task`` per changed field, ``Destroy_Task`` on a cluster,
``SearchDatastoreSubFolders_Task``, vMotion inside ``RelocateVM_Task``, a template
inside ``CloneVM_Task``). Those entries list vSphere's documented id. A tool whose
deciding permission this check cannot name at all is ``None`` (always
"undetermined"), never its read privileges alone: listing only those would report a
read-only account as granted for a call vCenter refuses.

The family gate ``scripts/lib/access_requirements_cover_tools.py`` checks that
the keys are exactly the registered tools and every id is one vSphere defines.
"""

from __future__ import annotations

_READ = ["System.View", "System.Read"]

_POWER_ON = "VirtualMachine.Interact.PowerOn"
_POWER_OFF = "VirtualMachine.Interact.PowerOff"
_SNAPSHOT = "VirtualMachine.State.CreateSnapshot"
# Placement: CreateVM_Task(pool=…) and CreateImportSpec(datastore=…) parameter privileges,
# which CloneVM_Task (no privId of its own) also needs per vSphere's documentation.
_PLACE = ["Resource.AssignVMToPool", "Datastore.AllocateSpace"]
# CloneVM_Task(folder=…) → CreateFromExisting; Provisioning.Clone is documented (pyVmomi:
# InstantClone_Task).
_CLONE = [
    "VirtualMachine.Provisioning.Clone",
    "VirtualMachine.Inventory.CreateFromExisting",
    *_PLACE,
]
# CreateVM_Task with a new disk also checks VirtualMachine.Config.AddNewDisk.
_CREATE = [
    "VirtualMachine.Inventory.Create",
    "VirtualMachine.Config.AddNewDisk",
    "Network.Assign",
    *_PLACE,
]
# OvfManager.CreateImportSpec(resourcePool, datastore) + ResourcePool.ImportVApp.
_IMPORT = ["VApp.Import", *_PLACE]
# ReconfigVM_Task checks per field: numCPUs → CPUCount, memoryMB → Memory.
_RESIZE = ["VirtualMachine.Config.CPUCount", "VirtualMachine.Config.Memory"]
# CloneVM_Task from a template checks DeployTemplate instead of Provisioning.Clone.
_FROM_TEMPLATE = [
    "VirtualMachine.Provisioning.DeployTemplate",
    "VirtualMachine.Inventory.CreateFromExisting",
    *_PLACE,
]
# Edits the existing CD-ROM's backing, or adds a CD-ROM when the VM has none.
_ATTACH_ISO = ["VirtualMachine.Config.EditDevice", "VirtualMachine.Config.AddRemoveDevice"]
_GUEST_EXEC = "VirtualMachine.GuestOperations.Execute"
_GUEST_QUERY = "VirtualMachine.GuestOperations.Query"
_GUEST_MODIFY = "VirtualMachine.GuestOperations.Modify"
_EDIT_CLUSTER = "Host.Inventory.EditCluster"
_HOST_NETWORK = "Host.Config.Network"


def _all(*groups: list[str]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        seen.extend(p for p in group if p not in seen)
    return seen


# Every step type a plan can hold (plan_executor's dispatch table). A given plan
# needs only its own steps' privileges, so "not granted" here may still apply it.
_PLAN_STEPS = _all(
    _READ,
    [_POWER_ON, _POWER_OFF, "VirtualMachine.Interact.Reset", "VirtualMachine.Interact.Suspend"],
    _CREATE,
    ["VirtualMachine.Inventory.Delete"],
    [_SNAPSHOT, "VirtualMachine.State.RemoveSnapshot", "VirtualMachine.State.RevertToSnapshot"],
    _CLONE,
    ["Resource.ColdMigrate", "Resource.HotMigrate"],
    _RESIZE,
    _FROM_TEMPLATE,
    ["VirtualMachine.Config.AddRemoveDevice", "VirtualMachine.Config.EditDevice"],
    ["Host.Inventory.DeleteCluster"],
    _IMPORT,
    ["VirtualMachine.Provisioning.MarkAsTemplate"],
    [_GUEST_EXEC, _GUEST_QUERY, _GUEST_MODIFY],
    ["Host.Inventory.CreateCluster", _EDIT_CLUSTER, "Host.Inventory.MoveHost"],
    ["Host.Inventory.RemoveHostFromCluster", "Host.Config.Storage"],
)

REQUIREMENTS: dict[str, list[str] | None] = {
    # HasPrivilegeOnEntities / RetrieveAllPermissions / roleList are all System.View.
    "get_access_info": ["System.View"],
    # --- reads -------------------------------------------------------------
    "get_cluster_health_summary": _READ,
    "get_cross_vcenter_attention": _READ,
    "investigate_vm": _READ,
    "investigate_host": _READ,
    "investigate_datastore": _READ,
    "get_cluster_info": _READ,
    "list_drs_rules": _READ,
    "list_dvs_portgroups": _READ,
    "list_host_vmks": _READ,
    "list_vcenter_alarms": _READ,
    "list_vm_snapshots": _READ,
    "get_vm_task_status": _READ,
    # SearchDatastoreSubFolders_Task: no privId in pyVmomi; vSphere checks Datastore.Browse.
    "browse_datastore": [*_READ, "Datastore.Browse"],
    "scan_all_datastore_images": [*_READ, "Datastore.Browse"],
    # esxcli "network diag ping" through the host's internal method executer (raw SOAP);
    # which privilege the host checks for it is not documented.
    "ping_from_vmk": None,
    # Local files only (~/.vmware-aiops); no platform call.
    "list_vm_plans": [],
    "list_vm_ttls": [],
    "cancel_vm_ttl": [],
    # --- alarms ------------------------------------------------------------
    # AcknowledgeAlarm(alarm=…) parameter privilege.
    "acknowledge_vcenter_alarm": [*_READ, "Alarm.Acknowledge"],
    "reset_vcenter_alarm": [*_READ, "Alarm.SetStatus"],
    # --- VM lifecycle ------------------------------------------------------
    "power_on_vm": [*_READ, _POWER_ON],
    # PowerOffVM_Task and ShutdownGuest are both VirtualMachine.Interact.PowerOff.
    "power_off_vm": [*_READ, _POWER_OFF],
    "create_vm": _all(_READ, _CREATE),
    # Destroy_Task has no privId; VirtualMachine.Inventory.Delete is vSphere's documented
    # one (pyVmomi names it only inside a vSAN parameter's "Create Delete" pair).
    "delete_vm": [*_READ, _POWER_OFF, "VirtualMachine.Inventory.Delete"],
    "reconfigure_vm": _all(_READ, _RESIZE),
    "attach_iso_to_vm": [*_READ, *_ATTACH_ISO],
    "create_vm_snapshot": [*_READ, _SNAPSHOT],
    "revert_vm_snapshot": [*_READ, "VirtualMachine.State.RevertToSnapshot"],
    "delete_vm_snapshot": [*_READ, "VirtualMachine.State.RemoveSnapshot"],
    "restore_vm_clean_slate": [*_READ, _POWER_OFF, "VirtualMachine.State.RevertToSnapshot"],
    # power_on=True sends CloneSpec(powerOn=True), which needs PowerOn too.
    "clone_vm": _all(_READ, _CLONE, [_POWER_ON]),
    # RelocateVM_Task: ColdMigrate (metadata); a powered-on move checks Resource.HotMigrate,
    # and a storage move allocates on the target datastore.
    "migrate_vm": [
        *_READ,
        "Resource.ColdMigrate",
        "Resource.HotMigrate",
        "Resource.AssignVMToPool",  # RelocateSpec.pool
        "Datastore.AllocateSpace",
    ],
    "convert_vm_to_template": [*_READ, "VirtualMachine.Provisioning.MarkAsTemplate"],
    # set_vm_ttl reads the VM's instance UUID now; the deletion it schedules runs later
    # in the daemon as this same account, so it needs what delete_vm needs.
    "set_vm_ttl": [*_READ, _POWER_OFF, "VirtualMachine.Inventory.Delete"],
    # --- deployment (optional baseline snapshot and power-on included) ---
    "deploy_vm_from_template": _all(_READ, _FROM_TEMPLATE, _RESIZE, [_SNAPSHOT, _POWER_ON]),
    "deploy_linked_clone": _all(_READ, _CLONE, _RESIZE, [_SNAPSHOT, _POWER_ON]),
    # The imported VM's NICs attach to a network (the OVF's default mapping).
    "deploy_vm_from_ova": _all(_READ, _IMPORT, ["Network.Assign", _SNAPSHOT, _POWER_ON]),
    "batch_clone_vms": _all(_READ, _CLONE, _RESIZE, [_SNAPSHOT, _POWER_ON]),
    "batch_linked_clone_vms": _all(_READ, _CLONE, _RESIZE, [_SNAPSHOT, _POWER_ON]),
    # A spec can clone, deploy from a template, import an OVA, create from scratch
    # (no source) and attach an ISO, so it needs the union of those paths.
    "batch_deploy_from_spec": _all(
        _READ,
        _CLONE,
        _FROM_TEMPLATE,
        _IMPORT,
        _CREATE,
        _RESIZE,
        _ATTACH_ISO,
        [_SNAPSHOT, _POWER_ON],
    ),
    # --- guest operations (parameter privileges of the GuestOperations managers) ---
    "run_vm_guest_command": [*_READ, _GUEST_EXEC, _GUEST_QUERY],
    "run_vm_guest_command_with_output": [*_READ, _GUEST_EXEC, _GUEST_QUERY],
    "upload_vm_guest_file": [*_READ, _GUEST_MODIFY],
    "download_vm_guest_file": [*_READ, _GUEST_QUERY],
    "provision_vm_guest": [*_READ, _GUEST_EXEC, _GUEST_QUERY, _GUEST_MODIFY],
    # --- clusters ----------------------------------------------------------
    "create_cluster": [*_READ, "Host.Inventory.CreateCluster"],
    # ClusterComputeResource.Destroy_Task: no privId in pyVmomi; vSphere checks DeleteCluster.
    "delete_cluster": [*_READ, "Host.Inventory.DeleteCluster"],
    "configure_cluster": [*_READ, _EDIT_CLUSTER],
    "create_drs_rule": [*_READ, _EDIT_CLUSTER],
    "delete_drs_rule": [*_READ, _EDIT_CLUSTER],
    "set_drs_rule_enabled": [*_READ, _EDIT_CLUSTER],
    # MoveInto_Task: EditCluster on the cluster, MoveHost on the host parameter.
    "add_cluster_host": [*_READ, _EDIT_CLUSTER, "Host.Inventory.MoveHost"],
    # Folder.MoveIntoFolder_Task has no privId; vSphere documents RemoveHostFromCluster
    # (pyVmomi names it as a vSAN witness-removal parameter privilege) and MoveHost.
    "remove_cluster_host": [
        *_READ,
        "Host.Inventory.RemoveHostFromCluster",
        "Host.Inventory.MoveHost",
    ],
    # --- networking --------------------------------------------------------
    "create_dvs_portgroup": [*_READ, "DVPortgroup.Create"],
    # AddVirtualNic has no privId (vSphere: Host.Config.Network); RemoveVirtualNic and the
    # VirtualNicManager calls are Host.Config.Network in pyVmomi's metadata.
    "add_host_vmk": [*_READ, _HOST_NETWORK],
    "remove_host_vmk": [*_READ, _HOST_NETWORK],
    "set_vmk_service": [*_READ, _HOST_NETWORK],
    # --- plans -------------------------------------------------------------
    "create_vm_plan": _READ,  # validates the steps against inventory; changes nothing
    "apply_vm_plan": _PLAN_STEPS,
    "rollback_vm_plan": _PLAN_STEPS,
}
