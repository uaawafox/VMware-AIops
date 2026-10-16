"""Regression: tools could read or write this server's own control files (HLD §18 step 0).

Over HTTPS any token holder could call ``download_vm_guest_file(local_path=
"~/.vmware/mcp-tokens", overwrite=True)`` and replace the token file, or upload
a skill's ``.env`` into a VM. Every place that opens a caller-supplied host path
now refuses one that resolves into ``$OPS_HOME`` or a ``~/.vmware-*`` directory,
before anything is read, written or transferred.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from vmware_policy.hostpaths import HostPathRefusedError

from vmware_aiops.ops import guest_ops, ova_deploy, vm_deploy


@pytest.fixture
def ops_home() -> Path:
    home = Path(os.environ["OPS_HOME"])  # the sandbox conftest set
    home.mkdir(parents=True, exist_ok=True)
    tokens = home / "mcp-tokens"
    tokens.write_text("alice:abc\n", encoding="utf-8")
    return home


def test_download_cannot_overwrite_the_token_file(ops_home):
    si = MagicMock()
    with pytest.raises(HostPathRefusedError):
        guest_ops.guest_download(
            si, "vm1", "/etc/hosts", str(ops_home / "mcp-tokens"), "u", "p", overwrite=True
        )
    assert (ops_home / "mcp-tokens").read_text(encoding="utf-8") == "alice:abc\n"
    si.RetrieveContent.assert_not_called()


def test_upload_cannot_send_a_control_file_into_a_vm(ops_home):
    si = MagicMock()
    with patch.object(guest_ops, "_require_vm_with_tools", return_value=MagicMock()), \
         patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(HostPathRefusedError):
            guest_ops.guest_upload(
                si, "vm1", str(ops_home / "mcp-tokens"), "/tmp/x", "u", "p"
            )
        urlopen.assert_not_called()
    fm = si.RetrieveContent.return_value.guestOperationsManager.fileManager
    fm.InitiateFileTransferToGuest.assert_not_called()


def test_a_deploy_spec_inside_ops_home_is_not_read(ops_home):
    with pytest.raises(HostPathRefusedError):
        vm_deploy.load_deploy_spec(str(ops_home / "rules.yaml"))


def test_an_ova_inside_ops_home_is_not_read(ops_home):
    with pytest.raises(HostPathRefusedError):
        ova_deploy._read_ovf_from_ova(str(ops_home / "audit.db"))


def test_the_mcp_tool_refuses_before_running(ops_home):
    from vmware_aiops.mcp_server.tools import guest as guest_tools

    with patch.object(guest_tools, "_get_connection") as connect:
        with pytest.raises(HostPathRefusedError):
            guest_tools.download_vm_guest_file(
                vm_name="vm1",
                guest_path="/etc/hosts",
                local_path=str(ops_home / "mcp-tokens"),
                username="u",
                password="p",
                overwrite=True,
            )
        connect.assert_not_called()


def test_ordinary_paths_still_work(tmp_path):
    spec = tmp_path / "spec.yaml"
    spec.write_text("vms: []\n", encoding="utf-8")
    assert vm_deploy.load_deploy_spec(str(spec)) is not None
