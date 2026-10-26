"""A scan replaces only the legacy rows it can attribute (review 2026-10-18).

Legacy rows are the ones written before rows carried ``target``. A scan of one
target deleted every legacy row in the file, including rows for datastores that
target does not have — the cache lost images no scan had looked at again.
VMware-Storage's ``_record_scan`` replaces a legacy row only when the scan
covered its datastore (and folder); this follows the same rule. A whole-target
scan covers each datastore it browsed successfully, whether or not it found
images there.
"""

from __future__ import annotations

import json

import pytest

from vmware_aiops.ops import datastore_browser as dsb
from vmware_aiops.ops import inventory


def _legacy(ds: str, name: str) -> dict:
    return {"datastore": ds, "name": name, "ds_path": f"[{ds}] {name}",
            "size_mb": 1.0, "type": "File", "modified": ""}


def _img(ds: str, name: str) -> dict:
    return {"name": name, "ds_path": f"[{ds}] {name}", "size_mb": 1.0,
            "type": "File", "modified": ""}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "image_registry.json"
    monkeypatch.setattr(dsb, "IMAGE_REGISTRY_FILE", path)
    monkeypatch.setattr(dsb, "CONFIG_DIR", tmp_path)
    return path


def _seed(path, rows: list[dict]) -> None:
    path.write_text(json.dumps({"images": rows, "last_scan": None}), encoding="utf-8")


def _names() -> list[str]:
    return sorted(i["name"] for i in dsb.list_images())


@pytest.mark.unit
def test_legacy_rows_on_datastores_the_scan_did_not_cover_survive(registry, monkeypatch) -> None:
    _seed(registry, [_legacy("datastore1", "old.iso"), _legacy("nfs-b", "keep.iso")])
    monkeypatch.setattr(dsb, "scan_all_datastores",
                        lambda si: {"datastore1": [_img("datastore1", "new.iso")]})

    dsb.update_registry(object(), target="vc")

    assert _names() == ["keep.iso", "new.iso"]


@pytest.mark.unit
def test_other_targets_rows_survive(registry, monkeypatch) -> None:
    other = dict(_legacy("datastore1", "b.iso"), target="esx")
    _seed(registry, [other])
    monkeypatch.setattr(dsb, "scan_all_datastores",
                        lambda si: {"datastore1": [_img("datastore1", "a.iso")]})

    dsb.update_registry(object(), target="vc")

    assert _names() == ["a.iso", "b.iso"]


def _datastores(monkeypatch, names: list[str], found: dict[str, list[dict] | Exception]) -> None:
    monkeypatch.setattr(inventory, "list_datastores",
                        lambda si: [{"name": n, "accessible": True} for n in names])

    def fake_scan(si, ds_name, path=""):
        res = found.get(ds_name, [])
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr(dsb, "scan_images", fake_scan)


@pytest.mark.unit
def test_a_datastore_scanned_empty_still_replaces_its_legacy_rows(registry, monkeypatch) -> None:
    _seed(registry, [_legacy("empty-ds", "gone.iso")])
    _datastores(monkeypatch, ["empty-ds"], {})

    dsb.update_registry(object(), target="vc")

    assert _names() == []


@pytest.mark.unit
def test_a_datastore_whose_scan_failed_keeps_its_legacy_rows(registry, monkeypatch) -> None:
    _seed(registry, [_legacy("flaky-ds", "keep.iso")])
    _datastores(monkeypatch, ["flaky-ds", "ok-ds"],
                {"flaky-ds": RuntimeError("browse failed"), "ok-ds": [_img("ok-ds", "n.iso")]})

    dsb.update_registry(object(), target="vc")

    assert _names() == ["keep.iso", "n.iso"]


@pytest.mark.unit
def test_this_targets_rows_on_a_datastore_it_did_not_browse_survive(registry, monkeypatch) -> None:
    """Review 2026-10-18: a failed or skipped browse is not "no images there"."""
    _seed(registry, [dict(_legacy("ds2", "mine.iso"), target="vc"),
                     dict(_legacy("datastore1", "stale.iso"), target="vc")])
    monkeypatch.setattr(dsb, "scan_all_datastores",
                        lambda si: {"datastore1": [_img("datastore1", "new.iso")], "ds2": None})

    dsb.update_registry(object(), target="vc")

    assert _names() == ["mine.iso", "new.iso"]


@pytest.mark.unit
def test_this_targets_rows_on_a_datastore_that_is_gone_are_dropped(registry, monkeypatch) -> None:
    """Narrow review 2026-10-18: not listed at all is not "failed to browse"."""
    _seed(registry, [dict(_legacy("removed-ds", "ghost.iso"), target="vc"),
                     dict(_legacy("flaky-ds", "kept.iso"), target="vc")])
    _datastores(monkeypatch, ["ds1", "flaky-ds"],
                {"ds1": [_img("ds1", "new.iso")], "flaky-ds": RuntimeError("browse failed")})

    dsb.update_registry(object(), target="vc")

    assert _names() == ["kept.iso", "new.iso"]


@pytest.mark.unit
def test_a_scan_whose_cache_cannot_be_locked_still_returns_its_images(registry, monkeypatch) -> None:
    """Narrow review 2026-10-18: Storage returns the scan with a cache_note; so does AIops."""
    import errno

    from vmware_aiops import _filelock

    monkeypatch.setattr(dsb, "scan_all_datastores",
                        lambda si: {"ds1": [_img("ds1", "new.iso")]})
    if _filelock.fcntl is None:
        pytest.skip("POSIX branch")

    def no_locks(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(_filelock.fcntl, "flock", no_locks)
    out = dsb.update_registry(object(), target="vc")

    assert [i["name"] for i in out["images"]] == ["new.iso"]
    assert "cache" in out["cache_note"].lower()
