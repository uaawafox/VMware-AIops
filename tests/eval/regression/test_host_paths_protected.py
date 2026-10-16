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


# ── review 2026-10-16: hard links, symlink swapped in after the check, preview ──


def _download_with_fake_transfer(local: str, overwrite: bool, before_write=None):
    """Run guest_download with the guest transfer faked; call before_write after the check."""
    si = MagicMock()
    vm = MagicMock()
    resp = MagicMock()
    resp.__enter__.return_value.read.return_value = b"payload"

    def urlopen(*a, **k):
        if before_write:
            before_write()
        return resp

    with patch.object(guest_ops, "_require_vm_with_tools", return_value=vm), \
         patch("urllib.request.urlopen", side_effect=urlopen), \
         patch.object(guest_ops, "get_verify_ssl", return_value=True):
        return guest_ops.guest_download(
            si, "vm1", "/etc/motd", local, "u", "p", overwrite=overwrite
        )


def test_a_hard_link_to_a_protected_file_is_not_written_through(ops_home, tmp_path):
    outside = tmp_path / "innocent.txt"
    os.link(ops_home / "mcp-tokens", outside)
    with pytest.raises(ValueError, match="hard link"):
        _download_with_fake_transfer(str(outside), overwrite=True)
    assert (ops_home / "mcp-tokens").read_text(encoding="utf-8") == "alice:abc\n"


def test_a_symlink_swapped_in_during_the_transfer_is_not_followed(ops_home, tmp_path):
    dest = tmp_path / "out.txt"

    def swap():
        dest.symlink_to(ops_home / "mcp-tokens")

    with pytest.raises((HostPathRefusedError, OSError)):
        _download_with_fake_transfer(str(dest), overwrite=True, before_write=swap)
    assert (ops_home / "mcp-tokens").read_text(encoding="utf-8") == "alice:abc\n"


def test_an_ordinary_download_is_written(tmp_path):
    dest = tmp_path / "out.txt"
    _download_with_fake_transfer(str(dest), overwrite=False)
    assert dest.read_bytes() == b"payload"


def test_the_provision_preview_does_not_reveal_a_protected_file(ops_home):
    from vmware_aiops.ops import guest_gate

    info, blocker = guest_gate._local_file(str(ops_home / "mcp-tokens"))
    assert info["local_size_bytes"] is None
    assert blocker and "configuration, tokens and audit log" in blocker


def test_a_parent_dir_swapped_for_a_link_during_the_transfer_is_caught(ops_home, tmp_path):
    # O_NOFOLLOW guards only the last component; the re-check after the transfer
    # catches a parent directory replaced by a link into OPS_HOME.
    sub = tmp_path / "sub"
    sub.mkdir()

    def swap():
        sub.rmdir()
        sub.symlink_to(ops_home, target_is_directory=True)

    with pytest.raises(HostPathRefusedError):
        _download_with_fake_transfer(str(sub / "rules.yaml"), overwrite=False, before_write=swap)
    assert not (ops_home / "rules.yaml").exists()


# ── second review 2026-10-16: plan_id was an unchecked host path ──────────────


@pytest.mark.parametrize(
    "bad",
    ["/tmp/evil", "../../.cursor/mcp", "plan-x/../../evil", "plan-..", "", "plan-a.b", "x" * 300],
)
def test_a_plan_id_cannot_name_a_path(bad, tmp_path, monkeypatch):
    from vmware_aiops.ops import planner

    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    with pytest.raises(ValueError, match="plan id"):
        planner.load_plan(bad)
    with pytest.raises(ValueError, match="plan id"):
        planner.delete_plan(bad)
    with pytest.raises(ValueError, match="plan id"):
        planner.save_plan({"plan_id": bad, "steps": []})


def test_a_plan_file_naming_another_plan_id_is_refused(tmp_path, monkeypatch):
    import json

    from vmware_aiops.ops import planner

    plans = tmp_path / "plans"
    plans.mkdir()
    monkeypatch.setattr(planner, "_PLANS_DIR", plans)
    (plans / "plan-20261016-000000-abcd.json").write_text(
        json.dumps({"plan_id": "../../.cursor/mcp", "steps": []}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="plan id"):
        planner.load_plan("plan-20261016-000000-abcd")


def test_a_generated_plan_id_round_trips(tmp_path, monkeypatch):
    from vmware_aiops.ops import planner

    monkeypatch.setattr(planner, "_PLANS_DIR", tmp_path / "plans")
    (tmp_path / "plans").mkdir()
    pid = planner._generate_plan_id()
    planner.save_plan({"plan_id": pid, "steps": []})
    assert planner.load_plan(pid)["plan_id"] == pid
    planner.delete_plan(pid)
    assert planner.load_plan(pid) is None


def test_download_onto_a_fifo_does_not_hang(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises((ValueError, OSError)):
        _download_with_fake_transfer(str(fifo), overwrite=True)


def test_upload_reads_a_regular_file_without_following_a_swapped_link(ops_home, tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("data", encoding="utf-8")
    assert guest_ops._read_upload_source(src) == b"data"
    link = tmp_path / "link.txt"
    link.symlink_to(ops_home / "mcp-tokens")
    with pytest.raises(OSError):
        guest_ops._read_upload_source(link)


def test_upload_source_swapped_for_a_fifo_is_refused_not_hung(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        guest_ops._read_upload_source(fifo)
