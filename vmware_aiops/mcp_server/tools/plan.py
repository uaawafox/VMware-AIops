"""Plan → Apply tools: create/apply/rollback multi-step plans, list plans."""

from typing import Any, Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.plan_executor import apply_plan, rollback_plan
from vmware_aiops.ops.planner import create_plan, list_plans


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vm_create_plan(
    operations: list[dict[str, Any]],
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Create an execution plan for multi-step VM operations.

    Auto-triggered when operations involve 2+ steps or 2+ VMs.
    Validates actions, checks target existence in vSphere, and generates
    a plan with rollback info for each step.

    Each operation is a dict with "action" key plus action-specific params.
    Allowed actions: power_on, power_off, reset, suspend, create_vm,
    delete_vm, reconfigure, create_snapshot, delete_snapshot,
    revert_snapshot, clone, migrate, deploy_ova, deploy_template,
    linked_clone, attach_iso, convert_to_template.

    Example:
        operations=[
            {"action": "power_off", "vm_name": "test-1"},
            {"action": "revert_snapshot", "vm_name": "test-1", "snapshot_name": "baseline"},
            {"action": "power_on", "vm_name": "test-1"}
        ]

    Returns plan dict with plan_id, steps, summary (vms_affected,
    irreversible_steps, rollback_available). Show to user for confirmation
    before calling vm_apply_plan.

    Args:
        operations: List of operation dicts, each with "action" + params.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return create_plan(si, operations, target=target)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vm_apply_plan(plan_id: str, target: Optional[str] = None) -> dict:
    """[WRITE] Execute a previously created plan step by step.

    Steps run sequentially. On failure: stops immediately, keeps the plan
    file with per-step results, and returns rollback_available flag.
    On success: deletes the plan file.

    If a step fails and rollback_available is true, ask the user whether
    to rollback, then call vm_rollback_plan if confirmed.

    Args:
        plan_id: The plan ID returned by vm_create_plan.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    result = apply_plan(si, plan_id)

    # If failed with rollback available, hint to the agent
    if result.get("status") == "failed" and result.get("rollback_available"):
        result["hint"] = (
            "Plan failed. Ask the user: 'Do you want to rollback the "
            "already-executed steps?' If yes, call vm_rollback_plan."
        )
    return result


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium")
@tool_errors("dict")
def vm_rollback_plan(plan_id: str, target: Optional[str] = None) -> dict:
    """[WRITE] Rollback executed steps of a failed plan in reverse order.

    Only call this after vm_apply_plan returns status='failed' and the
    user confirms they want to rollback. Irreversible steps (delete_vm,
    revert_snapshot, etc.) are skipped with a warning.

    Args:
        plan_id: The plan ID of the failed plan.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return rollback_plan(si, plan_id)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def vm_list_plans() -> dict:
    """[READ] List all pending/failed plans.

    Returns the list envelope: 'items' holds plan summaries (plan_id,
    created_at, status, steps count, VMs affected), and 'returned'/'total'/
    'truncated' state whether the listing is complete. Every plan file is
    read, so truncated is always false. Listing never deletes: stale plans
    (>24h) are swept by vm_create_plan, not by this tool.
    """
    return list_plans()
