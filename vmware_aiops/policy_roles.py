"""What vmware-policy needs to apply roles to this skill (HLD §18 D-21/D-22).

Policy cannot see which tools run other tools, which tools escape a role, or how a
missing ``target`` resolves; this module declares them. Imported by
``policy_environment``, so the MCP server and the CLI both register it. With a
vmware-policy that has no roles module (< 1.24) nothing is declared.
"""

from __future__ import annotations

from vmware_policy import skill_name

from vmware_aiops.policy_environment import _cached_config

#: Each plan action → the tool that does the same thing. ``reset``/``suspend`` have no
#: tool of their own; they change power state like ``power_off_vm``. The storage
#: actions are refused by ``plan_gate.REFUSED_ACTIONS`` and are not listed.
#: ``tests/test_policy_roles.py`` fails if a dispatch action is missing here.
ACTION_TOOLS: dict[str, str] = {
    "attach_iso": "attach_iso_to_vm",
    "clone": "clone_vm",
    "cluster_add_host": "add_cluster_host",
    "cluster_remove_host": "remove_cluster_host",
    "configure_cluster": "configure_cluster",
    "convert_to_template": "convert_vm_to_template",
    "create_cluster": "create_cluster",
    "create_snapshot": "create_vm_snapshot",
    "create_vm": "create_vm",
    "delete_cluster": "delete_cluster",
    "delete_snapshot": "delete_vm_snapshot",
    "delete_vm": "delete_vm",
    "deploy_ova": "deploy_vm_from_ova",
    "deploy_template": "deploy_vm_from_template",
    "guest_download": "download_vm_guest_file",
    "guest_exec": "run_vm_guest_command",
    "guest_upload": "upload_vm_guest_file",
    "linked_clone": "deploy_linked_clone",
    "migrate": "migrate_vm",
    "power_off": "power_off_vm",
    "power_on": "power_on_vm",
    "reconfigure": "reconfigure_vm",
    "reset": "power_off_vm",
    "revert_snapshot": "revert_vm_snapshot",
    "suspend": "power_off_vm",
}

#: A plan's rollback deletes VMs the plan created, so applying or rolling back a plan
#: reaches ``delete_vm`` even when no step names it.
_PLAN_REACHES = frozenset(ACTION_TOOLS.values()) | {"delete_vm"}

REACHES: dict[str, frozenset[str]] = {
    "apply_vm_plan": _PLAN_REACHES,
    "rollback_vm_plan": _PLAN_REACHES,
    "set_vm_ttl": frozenset({"delete_vm"}),  # the scheduler deletes later, with no caller
    "batch_clone_vms": frozenset({"clone_vm"}),
    "batch_linked_clone_vms": frozenset({"deploy_linked_clone"}),
    "batch_deploy_from_spec": frozenset(
        {"deploy_vm_from_template", "clone_vm", "deploy_vm_from_ova", "deploy_linked_clone",
         "create_vm"}
    ),
    "restore_vm_clean_slate": frozenset({"power_off_vm", "revert_vm_snapshot"}),
    "provision_vm_guest": frozenset({"run_vm_guest_command", "upload_vm_guest_file"}),
}

#: Tools that give their holder more than a role: code in a guest, files on the
#: server host. (Host-path tools are added by policy while a file root is set.)
ESCAPES = frozenset(
    {
        "run_vm_guest_command",
        "run_vm_guest_command_with_output",
        "upload_vm_guest_file",
        "download_vm_guest_file",
        "provision_vm_guest",
    }
)


def _target_name(target: str | None) -> str:
    """The target the connection layer would use for ``target`` (or the default)."""
    config = _cached_config()
    return (config.get_target(target) if target else config.default_target).name


try:
    from vmware_policy.roles import declare_role_metadata
except ImportError:  # vmware-policy without roles: nothing to declare
    declare_role_metadata = None

if declare_role_metadata is not None:
    declare_role_metadata(
        skill_name(__name__),
        escapes=ESCAPES,
        reaches=REACHES,
        resolver=_target_name,
    )
