"""Authorize work a tool does on behalf of another tool, under that tool's name.

A ``rules.yaml`` deny is checked by ``@vmware_tool`` under the name of the tool
that was called. Some tools run other tools' operations: a plan step
``delete_vm``, the rollback that deletes a VM a plan created, ``batch_clone_vms``
cloning, ``restore_vm_clean_slate`` powering off and reverting,
``provision_vm_guest`` running guest commands. Until 2026-10-17 none of that work
was checked under the tool that normally does it, so a deny on ``delete_vm`` or
``revert_vm_snapshot`` did not stop it (real hardware, then an independent
review). Everything here asks the policy under the inner tool's own name and
risk level, the way the TTL daemon already did for ``delete_vm``.
"""

from __future__ import annotations

from typing import Any

from vmware_policy import sanitize
from vmware_policy.guard import audit_call, guard
from vmware_policy.policy import PolicyDenied
from vmware_policy.skills import skill_name

from vmware_aiops.ops.gate import GateRefusedError
from vmware_aiops.policy_roles import ACTION_TOOLS, REACHES

SKILL = skill_name(__name__)

#: Each plan action's tool risk: the tool's own ``@vmware_tool`` risk, so a
#: maintenance window that blocks high/critical work blocks it here too.
#: ``tests/test_plan_steps_obey_policy.py`` compares it with the tools.
STEP_RISK: dict[str, str] = {
    "attach_iso": "medium", "clone": "high", "cluster_add_host": "medium",
    "cluster_remove_host": "medium", "configure_cluster": "medium",
    "convert_to_template": "medium", "create_cluster": "medium",
    "create_snapshot": "medium", "create_vm": "medium", "delete_cluster": "high",
    "delete_snapshot": "high", "delete_vm": "critical", "deploy_ova": "medium",
    "deploy_template": "medium", "guest_download": "high", "guest_exec": "critical",
    "guest_upload": "high", "linked_clone": "medium", "migrate": "high",
    "power_off": "medium", "power_on": "medium", "reconfigure": "medium",
    "reset": "medium", "revert_snapshot": "high", "suspend": "medium",
}

#: The same risks keyed by tool name (every REACHES tool is a plan action's tool).
TOOL_RISK: dict[str, str] = {ACTION_TOOLS[a]: r for a, r in STEP_RISK.items()}


def _role() -> str:
    """The network caller's role name for the audit row, or ``""`` (review L4)."""
    try:
        from vmware_policy.roles import caller_role
    except ImportError:  # vmware-policy without roles
        return ""
    try:
        return caller_role()[1] or ""
    except Exception:  # noqa: BLE001 - an audit enrichment must not fail the check
        return ""


def tool_refusal(
    tool: str,
    params: dict[str, Any],
    target: str | None,
    *,
    label: str,
    record: bool,
    audit_params: dict[str, Any] | None = None,
) -> str | None:
    """Ask vmware-policy about ``tool``; the refusal message, or ``None``.

    ``record`` files a ``denied`` (or ``error``) row under ``tool``, as the tool
    itself would: set when the work is about to run, not on a preview. A policy
    that cannot be read refuses: an unreadable rule set must not turn into an
    unchecked operation (the daemon's rule). A tool with no known risk is
    checked at the highest.
    """
    risk = TOOL_RISK.get(tool, "critical")
    try:
        guard(SKILL, tool, params, risk_level=risk, target=target or "")
        return None
    except PolicyDenied as exc:
        rule = sanitize(exc.result.rule or "", 100)
        reason = sanitize(exc.result.reason or "", 300).rstrip(". ")
        if rule == "maintenance_window":
            remedy = "run it again inside the maintenance window"
        else:
            remedy = "leave that part out, or do it outside the agent"
        message = (
            f"{label} refused by policy: rule '{rule}' denies {tool}"
            f"{f' -- {reason}' if reason else ''}. It is authorized as {tool} itself; "
            f"{remedy}."
        )
        status, detail = "denied", {"error": message, "rule": rule}
    except Exception as exc:  # noqa: BLE001 - refuse, never run unchecked
        message = (
            f"{label} refused: the policy check for {tool} failed "
            f"({type(exc).__name__}), so it is not run unchecked. Check "
            "~/.vmware/rules.yaml, then retry."
        )
        status, detail = "error", {"error": message}
    if record:
        audit_call(SKILL, tool, params=audit_params if audit_params is not None else params,
                   result=detail, status=status, risk_level=risk, role=_role())
    return message


def authorize_reached(tool: str, params: dict[str, Any], target: str | None) -> None:
    """Refuse ``tool`` if the policy denies any tool it runs (``REACHES``).

    Every reached tool is checked, not only the ones these arguments use: fail
    closed, and the same answer whatever the arguments say. Call it just before
    the tool acts.
    """
    for inner in sorted(REACHES.get(tool, ())):
        refused = tool_refusal(inner, params, target, label=f"{tool} (runs {inner})",
                               record=True)
        if refused:
            raise GateRefusedError(refused)
