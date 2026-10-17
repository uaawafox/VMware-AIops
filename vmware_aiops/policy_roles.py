"""What vmware-policy needs to apply roles to this skill (HLD §18 D-21/D-22).

Policy cannot see which tools run other tools, which tools escape a role, or how a
missing ``target`` resolves; this module declares them. Imported by
``policy_environment``, so the MCP server and the CLI both register it. With a
vmware-policy that has no roles module (< 1.24) nothing is declared.
"""

from __future__ import annotations

import sys

from vmware_policy import skill_name

from vmware_aiops.config import load_config

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
    # The scheduler deletes later, with no caller, powering a running VM off first.
    "set_vm_ttl": frozenset({"delete_vm", "power_off_vm"}),
    # The batch tools run the follow-up steps their arguments ask for (review M1).
    "batch_clone_vms": frozenset(
        {"clone_vm", "reconfigure_vm", "create_vm_snapshot", "power_on_vm"}
    ),
    "batch_linked_clone_vms": frozenset(
        {"deploy_linked_clone", "reconfigure_vm", "create_vm_snapshot", "power_on_vm"}
    ),
    # Single deploys reconfigure, snapshot and power on when asked (review 2026-10-17).
    "deploy_vm_from_ova": frozenset({"power_on_vm", "create_vm_snapshot"}),
    "deploy_vm_from_template": frozenset(
        {"reconfigure_vm", "create_vm_snapshot", "power_on_vm"}
    ),
    "deploy_linked_clone": frozenset({"reconfigure_vm", "create_vm_snapshot", "power_on_vm"}),
    "batch_deploy_from_spec": frozenset(
        {
            "deploy_vm_from_template",
            "clone_vm",
            "deploy_vm_from_ova",
            "deploy_linked_clone",
            "create_vm",
            "reconfigure_vm",
            "attach_iso_to_vm",
            "create_vm_snapshot",
            "power_on_vm",
        }
    ),
    "restore_vm_clean_slate": frozenset({"power_off_vm", "revert_vm_snapshot"}),
    # Runs a guest command and reads its output back (review 2026-10-17).
    "run_vm_guest_command_with_output": frozenset(
        {"run_vm_guest_command", "download_vm_guest_file"}
    ),
    # Exec steps run through guest_exec_with_output (review 2026-10-17).
    "provision_vm_guest": frozenset(
        {"run_vm_guest_command", "run_vm_guest_command_with_output", "upload_vm_guest_file"}
    ),
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


def _connection_config():
    """The config the connection layer uses for this call (review H1, then H-1).

    The server's connection manager loads the config once, on its first connection,
    and keeps it. Once it exists, the role is checked against that same object, so
    an edit to config.yaml cannot move the check off the target the call connects
    to. Before it exists, the file as it is now is what the manager is about to load.
    Looked up in ``sys.modules`` so the CLI never imports the MCP server.
    """
    server = sys.modules.get("vmware_aiops.mcp_server._shared")
    manager = getattr(server, "_conn_mgr", None) if server is not None else None
    return manager._config if manager is not None else load_config()


def _target_name(target: str | None) -> str:
    """The target the connection layer would use for ``target`` (or the default)."""
    config = _connection_config()
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
