"""Datastore tools: browse files, scan for deployable images."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops import datastore_browser


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def browse_datastore(
    datastore_name: str,
    path: str = "",
    pattern: str = "*",
    target: Optional[str] = None,
) -> dict:
    """[READ] Browse files in a vSphere datastore directory.

    Use this to discover OVA, ISO, VMDK, and other files on datastores
    before deploying VMs.

    Returns the list envelope: 'items' holds one row per file, and
    'returned'/'total'/'truncated' state whether the listing is complete.
    Every match in the searched folders is returned, so truncated is
    always false.

    Args:
        datastore_name: Name of the datastore to browse.
        path: Subdirectory path (empty string for root).
        pattern: Glob pattern to filter files (e.g. "*.ova", "*.iso", "*").
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return datastore_browser.browse_datastore(si, datastore_name, path=path, pattern=pattern)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="low")
@tool_errors("dict")
def scan_datastore_images(target: Optional[str] = None) -> dict:
    """[READ] Scan all accessible datastores for deployable images (OVA/ISO/OVF/VMDK).

    Results are cached locally in ~/.vmware-aiops/image_registry.json for
    fast lookup via list_cached_images. Run this to refresh the cache.

    Args:
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return datastore_browser.update_registry(si)
