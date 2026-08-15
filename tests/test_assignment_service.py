"""Service-layer tests for per-curator disease assignments."""
import pytest

from app import assignment_service as asv


@pytest.fixture
def store(tmp_path):
    return asv.AssignmentStore(tmp_path / "assignments")


# ---------------------------------------------------------------- assignments
def test_assign_is_additive_and_deduplicates(store):
    store.assign("ktokey", ["a", "b"])
    rec = store.assign("ktokey", ["b", "c"])
    assert rec["iris"] == ["a", "b", "c"]


def test_replace_drops_previous_and_prunes_done(store):
    store.assign("ktokey", ["a", "b"])
    store.set_done("ktokey", "a")
    rec = store.assign("ktokey", ["c"], replace=True)
    assert rec["iris"] == ["c"]
    assert rec["done"] == []


def test_assign_requires_a_login(store):
    with pytest.raises(ValueError):
        store.assign("", ["a"])


def test_unassign_removes_and_unknown_login_raises(store):
    store.assign("ktokey", ["a", "b"])
    assert store.unassign("ktokey", ["a"])["iris"] == ["b"]
    with pytest.raises(KeyError):
        store.unassign("nobody", ["a"])


def test_assign_refuses_a_disease_another_curator_holds(store):
    store.assign("ktokey", ["a", "b"])
    with pytest.raises(ValueError, match="another curator"):
        store.assign("someone", ["b", "c"])
    assert store.assigned_to("ktokey")["iris"] == ["a", "b"]
    assert store.owner_of("c") is None       # the whole call is refused, not part of it


def test_reassign_moves_the_disease_and_drops_the_old_done_flag(store):
    store.assign("ktokey", ["a", "b"])
    store.set_done("ktokey", "b")
    rec = store.assign("someone", ["b"], reassign=True)
    assert rec["iris"] == ["b"]
    assert store.owner_of("b") == "someone"
    assert store.assigned_to("ktokey")["iris"] == ["a"]
    assert store.assigned_to("ktokey")["done"] == []


def test_owner_of(store):
    store.assign("ktokey", ["a"])
    assert store.owner_of("a") == "ktokey"
    assert store.owner_of("z") is None


def test_done_toggles(store):
    store.assign("ktokey", ["a"])
    assert store.set_done("ktokey", "a")["done"] == ["a"]
    assert store.set_done("ktokey", "a", done=False)["done"] == []


def test_state_survives_a_new_store_instance(store, tmp_path):
    store.assign("ktokey", ["a"])
    store.set_done("ktokey", "a")
    fresh = asv.AssignmentStore(tmp_path / "assignments")
    assert fresh.assigned_to("ktokey")["iris"] == ["a"]
    assert fresh.assigned_to("ktokey")["done"] == ["a"]
