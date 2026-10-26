"""Access self-diagnosis: the account in use and the privileges each tool needs (HLD §17 D-17)."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.access_requirements import REQUIREMENTS
from vmware_aiops.mcp_server._shared import _ensure_conn_mgr, _get_connection, mcp, tool_errors


@mcp.tool(
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
@vmware_tool(risk_level="low")
@tool_errors("dict")
def get_access_info(
    tool: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] The account this server uses on a target, and whether it holds a tool's privileges.

    When to use: a tool failed with NoPermission, or before a write you want to know
    the configured account may run. Ask about one ``tool`` (e.g. clone_vm), or omit
    it to sort every tool of this server into granted / not granted / undetermined.

    What it returns: {target, account, checked_on, privileges {id: state}, and either
    ``tool`` {name, requires, verdict, meaning, missing_at_root} or the tools_* lists},
    plus the account's direct permissions (role, on, propagate).

    Gotchas: privileges are checked on the vCenter/ESXi **root folder** only.
    ``not_granted_at_root`` does not mean no access — a permission on a cluster,
    folder or VM still lets the tool work there. ``undetermined`` means the platform
    did not answer, or the tool's deciding permission is not a vSphere privilege
    (ping_from_vmk); it is never to be read as granted. Changes nothing.

    Args:
        tool: A tool name on this server, e.g. clone_vm (None = all tools).
        target: Optional vCenter/ESXi target name from config. Uses default if omitted.
    """
    from vmware_policy.access import unknown_tool_message, vsphere_access_info

    unknown = unknown_tool_message(tool, REQUIREMENTS)  # no login for a misspelled name
    if unknown:
        return {"error": unknown}
    si = _get_connection(target)
    cfg = _ensure_conn_mgr()._config
    name = (cfg.get_target(target) if target else cfg.default_target).name
    return vsphere_access_info(si, target=name, requirements=REQUIREMENTS, tool=tool)
