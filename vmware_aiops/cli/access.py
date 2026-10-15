"""``access-info``: which account the CLI uses on a target, and what it may do (HLD §17 D-17)."""

from __future__ import annotations

import json
from typing import Annotated

import typer
from vmware_policy import audited

from vmware_aiops.cli._common import (
    ConfigOption,
    TargetOption,
    _get_connection,
    cli_errors,
    console,
)

ToolOption = Annotated[
    str | None,
    typer.Option("--tool", help="One MCP tool name, e.g. clone_vm (default: every tool)"),
]


@cli_errors
@audited("get_access_info")
def access_info_cmd(
    tool: ToolOption = None,
    target: TargetOption = None,
    config: ConfigOption = None,
) -> None:
    """Show the account in use and whether it holds each tool's privileges (root folder)."""
    from vmware_policy.access import vsphere_access_info

    from vmware_aiops.access_requirements import REQUIREMENTS

    if tool is not None and tool not in REQUIREMENTS:
        try:  # the helper words the unknown-name error (with close matches)
            vsphere_access_info(None, target="", requirements=REQUIREMENTS, tool=tool)
        except ValueError as e:
            console.print(f"[red]Error: {e}[/]")
            raise typer.Exit(1) from e
    si, cfg = _get_connection(target, config)
    name = target or cfg.default_target.name
    result = vsphere_access_info(si, target=name, requirements=REQUIREMENTS, tool=tool)
    console.print_json(json.dumps(result, default=str))
