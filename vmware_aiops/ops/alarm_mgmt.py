"""vCenter alarm management: list, acknowledge, reset.

Acknowledge marks an alarm as seen without clearing it.
Reset clears triggered alarms back to normal via
AlarmManager.ClearTriggeredAlarms (the vSphere API has no per-alarm
status setter).
Both write operations are audit-logged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyVmomi import vim, vmodl
from vmware_policy import paginated, sanitize

from vmware_aiops.ops.health import get_active_alarms
from vmware_aiops.ops.inventory import _collect, _collect_object

if TYPE_CHECKING:
    from pyVmomi.vim import ServiceInstance

    from vmware_aiops.notify.audit import AuditLogger


# ---------------------------------------------------------------------------
# list_alarms — thin wrapper around health.get_active_alarms
# ---------------------------------------------------------------------------


def list_alarms(si: ServiceInstance, limit: int | None = None) -> dict:
    """List all active/triggered alarms across the vCenter inventory.

    Args:
        si: pyVmomi ServiceInstance.
        limit: Max alarms to return. ``None`` returns every active alarm.

    Returns:
        The family list envelope; ``items`` holds alarm dicts with severity,
        alarm_name, entity_name, entity_type, time, and acknowledged flag.
        Every active alarm is collected before the limit is applied, so
        ``total`` is the real alarm count — which is what lets a full page be
        recognised as complete instead of flagged as possibly-truncated.
    """
    alarms = get_active_alarms(si)
    rows = alarms[:limit] if limit is not None else alarms
    return paginated(rows, limit=limit, total=len(alarms))


# ---------------------------------------------------------------------------
# Internal: find a specific triggered alarm state
# ---------------------------------------------------------------------------


def _find_triggered_alarm(
    si: ServiceInstance,
    entity_name: str,
    alarm_name: str,
) -> tuple[Any, Any]:
    """Locate an entity and its triggered alarm state by name.

    Searches VMs, hosts, clusters, and datacenters.

    Returns:
        (entity, alarm_state) tuple.

    Raises:
        ValueError: If no matching alarm is found.
    """
    search_types = [
        vim.VirtualMachine,
        vim.HostSystem,
        vim.ClusterComputeResource,
        vim.Datacenter,
        vim.Datastore,
    ]
    # Fetch name + triggeredAlarmState for every entity of each type in one
    # batched PropertyCollector call per type, instead of touching those lazy
    # properties per object (N+1 SOAP round-trips on large inventories).
    for obj_type in search_types:
        for entity, props in _collect(si, [obj_type], ["name", "triggeredAlarmState"]):
            if props.get("name") != entity_name:
                continue
            for alarm_state in props.get("triggeredAlarmState") or []:
                if alarm_state.alarm.info.name == alarm_name:
                    return entity, alarm_state

    raise ValueError(
        f"Triggered alarm '{alarm_name}' on entity '{entity_name}' not found. "
        "Use list_vcenter_alarms to see current active alarms."
    )


# ---------------------------------------------------------------------------
# acknowledge_alarm
# ---------------------------------------------------------------------------


def acknowledge_alarm(
    si: ServiceInstance,
    entity_name: str,
    alarm_name: str,
    audit_logger: AuditLogger | None = None,
    target_name: str = "default",
) -> dict:
    """Acknowledge a triggered vCenter alarm.

    Marks the alarm as acknowledged without clearing it. The alarm
    remains visible but is flagged as seen by an operator.

    Args:
        si: pyVmomi ServiceInstance.
        entity_name: Name of the entity with the alarm (VM/host/cluster name).
        alarm_name: Exact alarm definition name.
        audit_logger: Optional audit logger.
        target_name: Target name for audit log.

    Returns:
        Dict with entity_name, alarm_name, action, acknowledged.
    """
    entity, alarm_state = _find_triggered_alarm(si, entity_name, alarm_name)
    content = si.RetrieveContent()
    content.alarmManager.AcknowledgeAlarm(
        alarm=alarm_state.alarm,
        entity=entity,
    )

    result = {
        "entity_name": sanitize(entity_name),
        "alarm_name": sanitize(alarm_name),
        "action": "acknowledged",
        "acknowledged": True,
    }

    if audit_logger:
        audit_logger.log(
            target=target_name,
            operation="acknowledge_alarm",
            resource=f"alarm/{entity_name}/{alarm_name}",
            parameters={"entity_name": entity_name, "alarm_name": alarm_name},
            result="ok",
        )

    return result


# ---------------------------------------------------------------------------
# reset_alarm
# ---------------------------------------------------------------------------

#: How many of the alarms a reset would clear the preview lists by name.
RESET_PREVIEW_LIMIT = 50

_READ_ERRORS = (vmodl.MethodFault, AttributeError, TypeError)


def _clear_filter(entity: Any, alarm_state: Any) -> tuple[str, Any]:
    """The filter ClearTriggeredAlarms gets for this alarm: its entity type + status."""
    entity_types = vim.alarm.AlarmFilterSpec.AlarmTypeByEntity
    if isinstance(entity, vim.HostSystem):
        type_entity = entity_types.entityTypeHost
    elif isinstance(entity, vim.VirtualMachine):
        type_entity = entity_types.entityTypeVm
    else:
        type_entity = entity_types.entityTypeAll
    filter_spec = vim.alarm.AlarmFilterSpec(
        status=[alarm_state.overallStatus],
        typeEntity=type_entity,
        typeTrigger=vim.alarm.AlarmFilterSpec.AlarmTypeByTrigger.triggerTypeAll,
    )
    return type_entity, filter_spec


def _matches_filter(state: Any, status: str, type_entity: str) -> bool:
    if str(state.overallStatus) != status:
        return False
    if type_entity == "entityTypeHost":
        return isinstance(state.entity, vim.HostSystem)
    if type_entity == "entityTypeVm":
        return isinstance(state.entity, vim.VirtualMachine)
    return True


def _read_name(obj: Any) -> str:
    try:
        return sanitize(obj.name)
    except _READ_ERRORS:
        return "<unreadable>"


def reset_alarm_radius(
    si: ServiceInstance, entity_name: str, alarm_name: str
) -> dict[str, Any]:
    """What reset_alarm would clear: every triggered alarm its filter matches.

    vSphere has no per-alarm clear, so this is usually more than the named
    alarm. The root folder's triggeredAlarmState aggregates every descendant
    alarm; it is read once, filtered the way ClearTriggeredAlarms filters, and
    de-duplicated by alarm-state key. If it cannot be read, the alarms are
    reported unmeasured and a confirmed call refuses.
    """
    entity, alarm_state = _find_triggered_alarm(si, entity_name, alarm_name)
    type_entity, _ = _clear_filter(entity, alarm_state)
    status = str(alarm_state.overallStatus)
    radius: dict[str, Any] = {
        "entity": sanitize(entity_name),
        "alarm": sanitize(alarm_name),
        "status": status,
        "entity_type_filter": str(type_entity),
        "effect": (
            "vSphere has no per-alarm clear: this clears EVERY triggered alarm with "
            f"status={status} on {type_entity} entities, not only the named one."
        ),
        "blockers": [],
        "unmeasured": [],
    }
    try:
        root = _collect_object(
            si, si.RetrieveContent().rootFolder, vim.Folder, ["triggeredAlarmState"]
        )
        states = root.get("triggeredAlarmState")
    except _READ_ERRORS:
        states = None
    if states is None:
        radius["unmeasured"].append("alarms_that_would_be_cleared")
        return radius

    seen: set[str] = set()
    cleared: list[dict[str, str]] = []
    for state in states:
        key = str(getattr(state, "key", "")) or str(id(state))
        if key in seen or not _matches_filter(state, status, str(type_entity)):
            continue
        seen.add(key)
        cleared.append({
            "entity_name": _read_name(state.entity),
            "alarm_name": _read_name(state.alarm.info),
            "entity_type": type(state.entity).__name__,
        })
    radius["will_clear_count"] = len(cleared)
    radius["will_clear"] = cleared[:RESET_PREVIEW_LIMIT]
    radius["will_clear_truncated"] = len(cleared) > RESET_PREVIEW_LIMIT
    return radius


def reset_alarm(
    si: ServiceInstance,
    entity_name: str,
    alarm_name: str,
    audit_logger: AuditLogger | None = None,
    target_name: str = "default",
) -> dict:
    """Clear triggered vCenter alarms back to normal state.

    Uses AlarmManager.ClearTriggeredAlarms with a vim.alarm.AlarmFilterSpec.
    The vSphere API has no per-alarm clear: the filter can only scope by
    entity type (host/VM/all) and alarm status, so this clears ALL triggered
    alarms matching the named alarm's entity type and current status —
    including the one requested. The named alarm is looked up first, so a
    typo'd entity/alarm name fails fast instead of clearing anything.

    Args:
        si: pyVmomi ServiceInstance.
        entity_name: Name of the entity with the alarm.
        alarm_name: Exact alarm definition name.
        audit_logger: Optional audit logger.
        target_name: Target name for audit log.

    Returns:
        Dict with entity_name, alarm_name, action, status, scope.
    """
    entity, alarm_state = _find_triggered_alarm(si, entity_name, alarm_name)
    content = si.RetrieveContent()
    type_entity, filter_spec = _clear_filter(entity, alarm_state)
    content.alarmManager.ClearTriggeredAlarms(filter=filter_spec)

    result = {
        "entity_name": sanitize(entity_name),
        "alarm_name": sanitize(alarm_name),
        "action": "reset",
        "status": "cleared",
        "scope": (
            f"all triggered alarms with status={alarm_state.overallStatus} "
            f"on {type_entity} entities (vSphere has no per-alarm clear)"
        ),
    }

    if audit_logger:
        audit_logger.log(
            target=target_name,
            operation="reset_alarm",
            resource=f"alarm/{entity_name}/{alarm_name}",
            parameters={"entity_name": entity_name, "alarm_name": alarm_name},
            result="ok",
        )

    return result
