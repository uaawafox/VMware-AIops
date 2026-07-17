"""Cluster tools: create/delete clusters, add/remove hosts, HA/DRS config, info."""

from typing import Optional

from vmware_policy import vmware_tool

from mcp_server._shared import _get_connection, mcp, tool_errors


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_create(
    name: str,
    datacenter: Optional[str] = None,
    ha: bool = False,
    drs: bool = False,
    drs_behavior: str = "fullyAutomated",
    target: Optional[str] = None,
) -> str:
    """[WRITE] Create a new empty cluster in a datacenter, optionally enabling HA and DRS.

    Fails with a clear error (no partial state) if a cluster with that name already
    exists or drs_behavior is invalid. After creation, add hosts with cluster_add_host;
    change HA/DRS later with cluster_configure; verify with cluster_info.
    Audited to ~/.vmware/audit.db.

    Args:
        name: Name for the new cluster; must be unique in the datacenter.
        datacenter: Datacenter name; omit to use the first datacenter on the target.
        ha: True enables vSphere HA (default False).
        drs: True enables DRS (default False).
        drs_behavior: "fullyAutomated" (default), "partiallyAutomated", or "manual".
            Only takes effect when drs=True.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Status string confirming creation and which features (HA/DRS) were enabled.
    """
    from vmware_aiops.ops.cluster_mgmt import create_cluster
    si = _get_connection(target)
    return create_cluster(
        si, cluster_name=name, datacenter_name=datacenter,
        ha_enabled=ha, drs_enabled=drs, drs_behavior=drs_behavior,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="high")
@tool_errors("str")
def cluster_delete(name: str, target: Optional[str] = None) -> str:
    """[WRITE] Delete an empty cluster (no hosts must remain).

    Args:
        name: Name of the cluster to delete.
        target: Optional vCenter target name from config.
    """
    from vmware_aiops.ops.cluster_mgmt import delete_cluster
    si = _get_connection(target)
    return delete_cluster(si, name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_add_host(
    cluster_name: str,
    host_name: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Move an ESXi host that vCenter already manages into a cluster.

    The host must already be in vCenter inventory (standalone or in another cluster) —
    this tool does NOT register brand-new hosts and takes no host credentials; use the
    vCenter UI for first-time host registration. Idempotent: a host already in the
    cluster returns success without change. Maintenance mode is not required to join
    (it IS required by cluster_remove_host). Check membership with cluster_info.
    Audited to ~/.vmware/audit.db.

    Args:
        cluster_name: Existing destination cluster name (create with cluster_create).
        host_name: Host name exactly as shown in vCenter inventory, typically the
            FQDN, e.g. "esxi-01.lab.local".
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Status string: moved, already-in-cluster, or host/cluster-not-found error.
    """
    from vmware_aiops.ops.cluster_mgmt import add_host_to_cluster
    si = _get_connection(target)
    return add_host_to_cluster(si, cluster_name=cluster_name, host_name=host_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_remove_host(
    cluster_name: str,
    host_name: str,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Remove a host from a cluster (host must be in maintenance mode).

    Args:
        cluster_name: Cluster to remove the host from.
        host_name: ESXi host name to remove.
        target: Optional vCenter target name from config.
    """
    from vmware_aiops.ops.cluster_mgmt import remove_host_from_cluster
    si = _get_connection(target)
    return remove_host_from_cluster(si, cluster_name=cluster_name, host_name=host_name)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("str")
def cluster_configure(
    name: str,
    ha: Optional[bool] = None,
    drs: Optional[bool] = None,
    drs_behavior: Optional[str] = None,
    target: Optional[str] = None,
) -> str:
    """[WRITE] Reconfigure cluster HA/DRS settings.

    Args:
        name: Cluster name.
        ha: Enable (True) or disable (False) HA, or None to leave unchanged.
        drs: Enable (True) or disable (False) DRS, or None to leave unchanged.
        drs_behavior: DRS behavior: "fullyAutomated", "partiallyAutomated", or "manual".
        target: Optional vCenter target name from config.
    """
    from vmware_aiops.ops.cluster_mgmt import configure_cluster
    si = _get_connection(target)
    return configure_cluster(
        si, cluster_name=name,
        ha_enabled=ha, drs_enabled=drs, drs_behavior=drs_behavior,
    )


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def cluster_info(name: str, target: Optional[str] = None) -> dict:
    """[READ] Get detailed cluster information: member hosts, HA/DRS config, resource capacity.

    Read-only, no side effects. Use before cluster_add_host / cluster_remove_host (shows
    membership and per-host maintenance mode) and to verify cluster_configure changes.

    Args:
        name: Exact cluster name.
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict with name, host_count, hosts (each: name, connection_state, power_state,
        maintenance_mode), ha_enabled, ha_admission_control, drs_enabled, drs_behavior,
        total/effective CPU (MHz) and memory (GB). Errors return a dict with "error" + hint.
    """
    from vmware_aiops.ops.cluster_mgmt import get_cluster_info
    si = _get_connection(target)
    return get_cluster_info(si, name)
