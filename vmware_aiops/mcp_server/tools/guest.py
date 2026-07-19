"""Guest Operations tools: exec, exec-with-output, upload, download, provision."""

from typing import Optional

from vmware_policy import vmware_tool

from vmware_aiops.mcp_server._shared import _get_connection, mcp, tool_errors
from vmware_aiops.ops.guest_ops import (
    guest_download,
    guest_exec,
    guest_exec_with_output,
    guest_provision,
    guest_upload,
)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_exec(
    vm_name: str,
    command: str,
    arguments: str = "",
    username: str = "root",
    password: str = "",
    working_directory: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Execute a command inside a VM via VMware Tools.

    Requires VMware Tools running in the guest OS.
    Returns exit_code, stdout, stderr, and timed_out flag.

    Note: VMware Guest Ops API does not capture stdout/stderr directly.
    To capture output, redirect to a file and use vm_guest_download:
        command="/bin/bash", arguments="-c 'ls -la /tmp > /tmp/output.txt'"
        Then download /tmp/output.txt.

    Args:
        vm_name: Target VM name.
        command: Full path to program (e.g. "/bin/bash", "C:\\Windows\\System32\\cmd.exe").
        arguments: Command arguments (e.g. "-c 'whoami'").
        username: Guest OS username (default "root").
        password: Guest OS password.
        working_directory: Working directory inside guest (optional).
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return guest_exec(
        si, vm_name, command, username, password,
        arguments=arguments,
        working_directory=working_directory,
    )


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_exec_output(
    vm_name: str,
    command: str,
    username: str = "root",
    password: str = "",
    timeout: int = 300,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Execute a shell command inside a VM and capture stdout + stderr.

    Automatically detects guest OS (Linux/Windows) and selects the correct
    shell. Output is captured by redirecting to a temp file, downloading it,
    then cleaning up — no manual redirection needed.

    Returns exit_code, stdout, stderr, timed_out, os_family.

    Args:
        vm_name: Target VM name.
        command: Shell command (e.g. "df -h", "ls /etc", "ipconfig").
        username: Guest OS username (default "root").
        password: Guest OS password.
        timeout: Max wait seconds (default 300).
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return guest_exec_with_output(si, vm_name, command, username, password, timeout=timeout)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_params=['password'])
@tool_errors("str")
def vm_guest_upload(
    vm_name: str,
    local_path: str,
    guest_path: str,
    username: str = "root",
    password: str = "",
    target: Optional[str] = None,
) -> str:
    """[WRITE] Upload a file from local machine to a VM via VMware Tools.

    Requires VMware Tools running in the guest OS.

    Args:
        vm_name: Target VM name.
        local_path: Local file path to upload.
        guest_path: Destination path inside the guest.
        username: Guest OS username (default "root").
        password: Guest OS password.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return guest_upload(si, vm_name, local_path, guest_path, username, password)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_params=['password'])
@tool_errors("str")
def vm_guest_download(
    vm_name: str,
    guest_path: str,
    local_path: str,
    username: str = "root",
    password: str = "",
    target: Optional[str] = None,
) -> str:
    """[READ] Download a file from a VM to local machine via VMware Tools.

    Requires VMware Tools running in the guest OS.

    Args:
        vm_name: Target VM name.
        guest_path: File path inside the guest to download.
        local_path: Local destination path.
        username: Guest OS username (default "root").
        password: Guest OS password.
        target: Optional vCenter/ESXi target name from config.
    """
    si = _get_connection(target)
    return guest_download(si, vm_name, guest_path, local_path, username, password)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True})
@vmware_tool(risk_level="medium", sensitive_params=['password'])
@tool_errors("dict")
def vm_guest_provision(
    vm_name: str,
    username: str,
    password: str,
    steps: list[dict],
    timeout: int = 300,
    target: Optional[str] = None,
) -> dict:
    """[WRITE] Provision a VM by running a sequence of guest operations (exec / upload / service).

    Combines key injection, software installation, and service startup into a
    single call. Steps execute in order; stops on first failure.

    Step types:
      - exec:    {"type": "exec", "command": "apt-get install -y nginx"}
      - upload:  {"type": "upload", "local_path": "/tmp/id_rsa.pub", "guest_path": "/root/.ssh/authorized_keys"}
      - service: {"type": "service", "name": "nginx", "action": "start"}

    Args:
        vm_name: Target VM name.
        username: Guest OS username.
        password: Guest OS password.
        steps: Ordered list of step dicts.
        timeout: Per-step timeout in seconds (default 300).
        target: Optional vCenter/ESXi target name from config.

    Returns:
        dict with success, completed_steps, total_steps, results, error.

    Example:
        steps = [
            {"type": "upload", "local_path": "~/.ssh/id_rsa.pub", "guest_path": "/root/.ssh/authorized_keys"},
            {"type": "exec", "command": "chmod 600 /root/.ssh/authorized_keys"},
            {"type": "exec", "command": "apt-get install -y nginx"},
            {"type": "service", "name": "nginx", "action": "enable"},
            {"type": "service", "name": "nginx", "action": "start"},
        ]
    """
    si = _get_connection(target)
    return guest_provision(si, vm_name, username, password, steps, timeout=timeout)
