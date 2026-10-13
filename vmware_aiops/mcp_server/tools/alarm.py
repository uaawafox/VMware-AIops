"""Alarm management tools: list, acknowledge, reset vCenter alarms."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.alarm_mgmt import (
    acknowledge_alarm,
    list_alarms,
    measure_reset,
    reset_alarm,
)
from vmware_aiops.ops.gate import preview, refuse_on


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def list_vcenter_alarms(
    target: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """[READ] List active/triggered alarms across the vCenter inventory.

    Start here for alarm work: it supplies the exact entity_name/alarm_name pair
    that acknowledge_vcenter_alarm and reset_vcenter_alarm require.

    Returns the list envelope: 'items' holds severity (critical/warning/info),
    entity name and type, alarm name, acknowledged flag, and trigger time;
    'returned'/'limit'/'total'/'truncated'/'hint' state completeness, so a
    limited page is never mistaken for the whole picture. 'total' is the real
    active-alarm count — every alarm is collected before the limit is applied.

    Args:
        target: Optional vCenter target name from config. Uses default if omitted.
        limit: Max number of alarms to return (None = all). Use when many alarms are active.
    """
    si = _get_connection(target)
    return list_alarms(si, limit=limit)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def acknowledge_vcenter_alarm(
    entity_name: str,
    alarm_name: str,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Acknowledge a triggered vCenter alarm — marks it as seen WITHOUT clearing it.

    This only marks the alarm; it does not clear it. The alarm stays in the active
    list with acknowledged=true until its condition clears or it is reset. To remove
    it entirely after fixing the root cause use reset_vcenter_alarm instead. Get
    exact entity_name and alarm_name from list_vcenter_alarms first; an unknown pair
    returns a not-found error.

    Args:
        entity_name: VM, ESXi host, or cluster the alarm fired on
            (from list_vcenter_alarms output).
        alarm_name: Exact alarm definition name, e.g. "Virtual machine CPU usage".
        target: vCenter target name from config.yaml; omit to use the default target.

    Returns:
        Dict: entity_name, alarm_name, action ("acknowledged"), acknowledged (true).
    """
    si = _get_connection(target)
    return acknowledge_alarm(si, entity_name, alarm_name, target_name=target or "default")


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def reset_vcenter_alarm(
    entity_name: str,
    alarm_name: str,
    confirm: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Clear triggered vCenter alarms back to normal state.

    Use this after resolving the underlying issue; to merely mark an alarm seen
    use acknowledge_vcenter_alarm instead. Get entity_name and alarm_name from
    list_vcenter_alarms first.

    vSphere has no per-alarm clear: this clears EVERY triggered alarm with the
    named alarm's status (red/yellow) on its entity type (host/VM/all), usually
    more than the one named. Without confirm=True this only previews: it returns
    blast_radius with will_clear_count and the alarms it would clear, and changes
    nothing. Show it to the user and get their decision; do not set confirm=True
    on your own. Refused when the list of alarms cannot be read.

    Args:
        entity_name: Name of the entity with the alarm (VM name, host name, or cluster name).
        alarm_name: Exact alarm definition name from list_vcenter_alarms output.
        confirm: False (default) returns the blast radius and changes nothing. True clears.
        target: Optional vCenter target name from config.

    Returns:
        Dict with action (preview, reset), blast_radius, and on reset the scope cleared.
    """
    si = _get_connection(target)
    radius, filter_spec = measure_reset(si, entity_name, alarm_name)
    if not confirm:
        return preview(radius)
    refuse_on(radius, "reset_vcenter_alarm")
    result = reset_alarm(
        si, entity_name, alarm_name, target_name=target or "default", filter_spec=filter_spec
    )
    return {**result, "blast_radius": radius}
