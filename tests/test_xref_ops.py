"""One cross-reference id at a time, applied against what is on file.

The review page used to compute the cell's whole new contents from the ids the
window happened to be holding and PUT that back as the field's complete value.
Anything a second window (or another curator) had added to the same cell since
page load was erased, with no conflict and no message — issue #114, and the
reason comparing two records side by side was unsafe.
"""
import pytest

from app.errors import Invalid


def _first_disease_with_no_omim(service):
    """A disease whose OMIM cell is empty, so each test starts from a known state."""
    for d in service.get_diseases_list():
        if not service.get_xrefs(d["iri"]).get("omim"):
            return d["iri"]
    pytest.skip("no disease with an empty OMIM cell in the fixture")


def test_add_appends_without_touching_what_is_there(service):
    iri = _first_disease_with_no_omim(service)
    service.apply_xref_op(iri, "omim", "add", value="111111", editor="a")
    service.apply_xref_op(iri, "omim", "add", value="222222", editor="b")
    assert service.get_xrefs(iri)["omim"] == ["111111", "222222"]


def test_a_second_window_does_not_erase_the_first_windows_id(service):
    """The defect itself: two windows, each holding a stale view of the cell.

    Window A loads, window B loads, A adds an id, then B adds one. B is sending
    the id alone, so the list is rebuilt from what is actually stored and both
    survive — where sending "the cell's new contents" would have written B's
    view of the cell and dropped A's.
    """
    iri = _first_disease_with_no_omim(service)
    window_a_view = service.get_xrefs(iri)["omim"]
    window_b_view = service.get_xrefs(iri)["omim"]
    assert window_a_view == window_b_view == []

    service.apply_xref_op(iri, "omim", "add", value="100001", editor="window-a")
    service.apply_xref_op(iri, "omim", "add", value="200002", editor="window-b")

    assert service.get_xrefs(iri)["omim"] == ["100001", "200002"]


def test_add_is_idempotent(service):
    iri = _first_disease_with_no_omim(service)
    service.apply_xref_op(iri, "omim", "add", value="333333")
    service.apply_xref_op(iri, "omim", "add", value="333333")
    assert service.get_xrefs(iri)["omim"] == ["333333"]


def test_replace_swaps_only_its_own_id(service):
    iri = _first_disease_with_no_omim(service)
    service.apply_xref_op(iri, "omim", "add", value="111111")
    service.apply_xref_op(iri, "omim", "add", value="222222")
    service.apply_xref_op(iri, "omim", "replace", value="999999", replaces="111111")
    assert service.get_xrefs(iri)["omim"] == ["999999", "222222"]


def test_replacing_an_id_someone_else_changed_is_refused(service):
    """A collision has to be visible.

    Quietly adding the new value instead would hide exactly the case this whole
    endpoint exists to surface, and leave two ids where the curator meant one.
    """
    iri = _first_disease_with_no_omim(service)
    service.apply_xref_op(iri, "omim", "add", value="111111")
    service.apply_xref_op(iri, "omim", "replace", value="444444", replaces="111111")

    with pytest.raises(Invalid, match="no longer on file"):
        service.apply_xref_op(iri, "omim", "replace", value="555555", replaces="111111")
    assert service.get_xrefs(iri)["omim"] == ["444444"]


def test_remove_drops_one_id_and_is_idempotent(service):
    iri = _first_disease_with_no_omim(service)
    service.apply_xref_op(iri, "omim", "add", value="111111")
    service.apply_xref_op(iri, "omim", "add", value="222222")
    service.apply_xref_op(iri, "omim", "remove", value="111111")
    service.apply_xref_op(iri, "omim", "remove", value="111111")
    assert service.get_xrefs(iri)["omim"] == ["222222"]


def test_unknown_database_and_operation_are_refused(service):
    iri = _first_disease_with_no_omim(service)
    with pytest.raises(Invalid, match="not a cross-reference database"):
        service.apply_xref_op(iri, "definition", "add", value="x")
    with pytest.raises(Invalid, match="not one of add, replace, remove"):
        service.apply_xref_op(iri, "omim", "clobber", value="x")
    with pytest.raises(Invalid, match="add needs an id"):
        service.apply_xref_op(iri, "omim", "add", value="   ")
