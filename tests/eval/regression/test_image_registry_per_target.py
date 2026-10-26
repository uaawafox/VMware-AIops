"""scan_all_datastore_images keeps each target's images (review 2026-10-17).

Each scan replaced the whole registry, so scanning home-esxi erased what a
home-vcenter scan had found ("datastore1" exists on both), and a corrupt file
made list_cached_images fail with a bare JSONDecodeError.
"""

from __future__ import annotations

import pytest

from vmware_aiops.ops import datastore_browser as dsb


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "image_registry.json"
    monkeypatch.setattr(dsb, "IMAGE_REGISTRY_FILE", path)
    monkeypatch.setattr(dsb, "CONFIG_DIR", tmp_path)
    return path


def _found(monkeypatch, name):
    img = {"name": name, "ds_path": f"[datastore1] {name}", "size_mb": 1.0,
           "type": "File", "modified": ""}
    monkeypatch.setattr(dsb, "scan_all_datastores", lambda si: {"datastore1": [img]})


@pytest.mark.unit
def test_two_targets_keep_their_own_images(registry, monkeypatch) -> None:
    _found(monkeypatch, "a.iso")
    dsb.update_registry(object(), target="vc")
    _found(monkeypatch, "b.iso")
    out = dsb.update_registry(object(), target="esx")

    assert [i["name"] for i in out["images"]] == ["b.iso"]
    assert sorted((i["target"], i["name"]) for i in dsb.list_images()) == [
        ("esx", "b.iso"), ("vc", "a.iso")]


@pytest.mark.unit
def test_a_rescan_replaces_that_targets_images_and_legacy_rows(registry, monkeypatch) -> None:
    registry.write_text('{"images": [{"datastore": "datastore1", "name": "old.iso", '
                        '"ds_path": "[datastore1] old.iso"}], "last_scan": null}',
                        encoding="utf-8")
    _found(monkeypatch, "new.iso")
    dsb.update_registry(object(), target="vc")

    assert [i["name"] for i in dsb.list_images()] == ["new.iso"]


@pytest.mark.unit
def test_a_corrupt_registry_says_how_to_recover(registry) -> None:
    registry.write_text("{not json", encoding="utf-8")
    with pytest.raises(dsb.DatastoreBrowseError, match="scan_all_datastore_images"):
        dsb.list_images()


@pytest.mark.unit
def test_a_corrupt_registry_is_rebuilt_by_a_scan(registry, monkeypatch) -> None:
    registry.write_text("{not json", encoding="utf-8")
    _found(monkeypatch, "x.iso")
    dsb.update_registry(object(), target="vc")
    assert [i["name"] for i in dsb.list_images()] == ["x.iso"]
