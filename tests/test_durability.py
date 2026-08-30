"""Durability of the working copies, the stores and the ARI id sequence.

Covers issues #103 (a restart discarded then overwrote unpublished work),
#104 (parallel creation minted duplicate ids), #107 (source branch was global),
#108 (non-atomic writes, silent resets) and #120 (idle work deleted).
"""
import json
import shutil
import time
from collections import OrderedDict

import pytest

from app import atomic_store, config, workspace
from app.id_allocator import IdAllocator
from app.ontology_service import OntologyService


# ------------------------------------------------------- atomic_store basics
def test_a_write_is_all_or_nothing(tmp_path):
    f = tmp_path / "store.json"
    atomic_store.write_json(f, {"a": 1})
    assert json.loads(f.read_text()) == {"a": 1}
    # No temp files left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["store.json"]


def test_a_missing_store_is_the_default(tmp_path):
    assert atomic_store.read_json(tmp_path / "absent.json", {"x": 1}) == {"x": 1}


def test_a_corrupt_store_is_not_read_as_empty(tmp_path):
    f = tmp_path / "store.json"
    f.write_text("{ half a wri")
    with pytest.raises(atomic_store.StoreCorrupt):
        atomic_store.read_json(f, {})
    assert f.read_text() == "{ half a wri"     # the bytes are still there


# ----------------------------------------------------- #103 working copies
@pytest.fixture
def user_dir(tmp_path, monkeypatch, base_owl):
    onto = tmp_path / "ari.owl"
    shutil.copy2(base_owl, onto)
    udir = tmp_path / ".user-data"
    udir.mkdir()
    monkeypatch.setattr(config, "ONTOLOGY_FILE", str(onto))
    monkeypatch.setattr(config, "USER_DIR", udir)
    monkeypatch.setattr(config, "USER_ARCHIVE_DIR", udir / "archive")
    monkeypatch.setattr(workspace, "USER_SVC", OrderedDict())
    monkeypatch.setattr(workspace, "USER_DIRTY", set())
    return udir


def test_a_restart_neither_hides_nor_overwrites_a_working_copy(user_dir):
    """The bug: USER_SVC is in-memory only and nothing rehydrated it, so after a
    restart reads fell through to BASE and the next write copied the pristine
    base over the curator's file."""
    svc = workspace.user_service("ada", create=True)
    iri = svc.get_diseases_list()[0]["iri"]
    svc.update_disease(iri, {"disease_category": "EDITED-BEFORE-RESTART"}, editor="ada")
    copy = user_dir / "ada.owl"
    size_before = copy.stat().st_size

    workspace.USER_SVC.clear()                       # the restart

    # A read finds the working copy, not BASE.
    after = workspace.user_service("ada")
    assert after.get_disease_detail(iri)["disease_category"] == ["EDITED-BEFORE-RESTART"]
    # And the write path does not copy over it.
    workspace.USER_SVC.clear()
    again = workspace.user_service("ada", create=True)
    assert again.get_disease_detail(iri)["disease_category"] == ["EDITED-BEFORE-RESTART"]
    assert copy.stat().st_size == size_before


def test_in_memory_worlds_are_bounded(user_dir, monkeypatch):
    monkeypatch.setattr(config, "MAX_LOADED_WORLDS", 2)
    for login in ("a", "b", "c"):
        workspace.user_service(login, create=True)
    assert len(workspace.USER_SVC) <= 2
    # Every working copy is still on disk; only the in-memory world was evicted.
    assert {p.stem for p in user_dir.glob("*.owl")} == {"a", "b", "c"}


# --------------------------------------------------------------- #120 sweep
def test_idle_work_with_unpublished_changes_is_archived_not_deleted(user_dir, monkeypatch):
    monkeypatch.setattr(config, "USER_DATA_TTL_DAYS", 14)
    svc = workspace.user_service("ada", create=True)
    iri = svc.get_diseases_list()[0]["iri"]
    svc.update_disease(iri, {"disease_category": "UNPUBLISHED"}, editor="ada")
    workspace.USER_SVC.clear()
    workspace.USER_DIRTY.clear()                     # the restart forgot she was dirty
    copy = user_dir / "ada.owl"
    old = time.time() - 30 * 86400
    import os
    os.utime(copy, (old, old))

    workspace._sweep_user_data()

    assert not copy.exists()
    archived = list((user_dir / "archive").glob("ada.*.owl"))
    assert len(archived) == 1, "unpublished work must be archived, never deleted"
    assert b"UNPUBLISHED" in archived[0].read_bytes()


def test_an_untouched_idle_copy_is_removed(user_dir, monkeypatch):
    monkeypatch.setattr(config, "USER_DATA_TTL_DAYS", 14)
    workspace.user_service("bob", create=True)       # created, never edited
    workspace.USER_SVC.clear()
    copy = user_dir / "bob.owl"
    import os
    old = time.time() - 30 * 86400
    os.utime(copy, (old, old))

    workspace._sweep_user_data()

    assert not copy.exists()
    assert not list((user_dir / "archive").glob("bob.*.owl"))


def test_expiry_is_reportable_while_the_copy_is_still_recoverable(user_dir, monkeypatch):
    monkeypatch.setattr(config, "USER_DATA_TTL_DAYS", 14)
    assert workspace.working_copy_expiry("nobody") is None
    workspace.user_service("ada", create=True)
    info = workspace.working_copy_expiry("ada")
    assert info["ttl_days"] == 14
    assert 12 <= info["days_left"] <= 14


# ------------------------------------------------------------ #104 id minting
def test_two_curators_cannot_mint_the_same_number(tmp_path, base_owl):
    """Two working copies, each unaware of the other, must not agree."""
    minted = []
    for name in ("a", "b"):
        d = tmp_path / name / "ontologies"
        d.mkdir(parents=True)
        shutil.copy2(base_owl, d / "ari.owl")
        svc = OntologyService(str(d / "ari.owl"))
        # Both share the one counter, as they do in the deployed app.
        svc.ids = IdAllocator(tmp_path / "provenance")
        minted.append(svc.create_disease({"label": f"Test {name}"}, editor=name)["ari_id"][0])
    assert minted[0] != minted[1], f"both curators minted {minted[0]}"


def test_the_counter_never_moves_backwards(tmp_path):
    alloc = IdAllocator(tmp_path)
    assert alloc.allocate(1200, editor="a") == 1201
    # A stale working copy sees an older maximum; it must not reissue 1201.
    assert alloc.allocate(900, editor="b") == 1202
    # A copy ahead of the counter (a number that arrived via a merge) pushes it on.
    assert alloc.allocate(5000, editor="c") == 5001


def test_allocation_is_audited(tmp_path):
    alloc = IdAllocator(tmp_path)
    alloc.allocate(10, editor="ada")
    entry = alloc._read()["log"][-1]
    assert entry["n"] == 11 and entry["by"] == "ada"


# ---------------------------------------------------------- #107 source branch
def test_source_branch_is_per_curator(user_dir):
    assert workspace._branch_state("ada")["source_branch"] == config.GH_BASE_BRANCH
    workspace._set_branch_state("ada", source_branch="edit/ada/thing", pr_base="edit/ada/thing")
    assert workspace._branch_state("ada")["source_branch"] == "edit/ada/thing"
    # Bob is untouched, and so is the anonymous default.
    assert workspace._branch_state("bob")["source_branch"] == config.GH_BASE_BRANCH
    assert workspace._branch_state(None)["source_branch"] == config.GH_BASE_BRANCH
