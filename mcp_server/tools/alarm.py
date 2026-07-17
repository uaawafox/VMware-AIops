"""Alarm management tools: list, acknowledge, reset vCenter alarms."""

from typing import Optional

from vmware_policy import vmware_tool

from mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.alarm_mgmt import acknowledge_alarm, list_alarms, reset_alarm


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("list")
def list_vcenter_alarms(
    target: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """[READ] List active/triggered alarms across the vCenter inventory.

    Returns alarms with severity (critical/warning/info), entity name and type,
    alarm name, acknowledged flag, and trigger time.

    Args:
        target: Optional vCenter target name from config. Uses default if omitted.
        limit: Max number of alarms to return (None = all). Use when many alarms are active.
    """
    si = _get_connection(target)
    results = list_alarms(si)
    if limit is not None:
        results = results[:limit]
    return results


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def acknowledge_vcenter_alarm(
    entity_name: str,
    alarm_name: str,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Acknowledge a triggered vCenter alarm — marks it as seen WITHOUT clearing it.

    The alarm stays in the active list with acknowledged=true until its condition clears
    or it is reset. To remove the alarm entirely after fixing the root cause, use
    reset_vcenter_alarm instead. Get exact entity_name and alarm_name values from
    list_vcenter_alarms first; an unknown pair returns a not-found error.
    Audited to ~/.vmware/audit.db.

    Args:
        entity_name: Name of the VM, ESXi host, or cluster the alarm fired on
            (from list_vcenter_alarms output).
        alarm_name: Exact alarm definition name, e.g. "Virtual machine CPU usage".
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict: entity_name, alarm_name, action ("acknowledged"), acknowledged (true).
    """
    si = _get_connection(target)
    return acknowledge_alarm(si, entity_name, alarm_name, target_name=target or "default")


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def reset_vcenter_alarm(
    entity_name: str,
    alarm_name: str,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Clear triggered vCenter alarms back to normal state.

    Uses AlarmManager.ClearTriggeredAlarms. The named alarm no longer appears in
    the active alarm list. Use this after resolving the underlying issue. Use
    list_vcenter_alarms to find entity_name and alarm_name values.

    Gotcha: vSphere has no per-alarm clear — this clears ALL triggered alarms
    matching the named alarm's entity type (host/VM/all) and current status
    (red/yellow). The response's 'scope' field states exactly what was cleared.

    Args:
        entity_name: Name of the entity with the alarm (VM name, host name, or cluster name).
        alarm_name: Exact alarm definition name from list_vcenter_alarms output.
        target: Optional vCenter target name from config.
    """
    si = _get_connection(target)
    return reset_alarm(si, entity_name, alarm_name, target_name=target or "default")
