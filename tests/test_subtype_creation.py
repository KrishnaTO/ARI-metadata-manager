"""Creating a clinical subtype leaves a trace on both records (issue #24).

The issue asks three questions about creating a subtype from the review page.
The answers, before this: it does **not** open a pull request (it writes to the
curator's working copy, which the next submission carries); the changelog said
only "Created: <label>" and named no parent; and the new disease was in the
session but nothing said so. The first is by design and is now stated in the UI;
the other two are fixed here.
"""
from app import diff_service as ds


def _entry(service, iri):
    return service.get_disease_detail(iri)["changelog"][-1]


def test_the_child_changelog_names_the_disease_it_was_split_from(service):
    parent = service.get_diseases_list()[0]
    child = service.create_disease(
        {"label": "Test subtype", "definition": "d", "parent_iri": parent["iri"]},
        editor="tester")
    assert f"clinical subtype of {parent['name']}" in _entry(service, child["iri"])


def test_the_parent_changelog_records_the_subtype_that_was_added(service):
    """A curator opening the parent could not see a subtype had been split out."""
    parent = service.get_diseases_list()[0]
    child = service.create_disease(
        {"label": "Test subtype", "definition": "d", "parent_iri": parent["iri"]},
        editor="tester")
    entry = _entry(service, parent["iri"])
    assert "Added clinical subtype: Test subtype" in entry
    assert child["ari_id"][0] in entry


def test_a_disease_created_without_a_parent_says_only_that_it_was_created(service):
    d = service.create_disease({"label": "Standalone", "definition": "d"}, editor="tester")
    entry = _entry(service, d["iri"])
    assert entry.endswith("Created: Standalone")


def test_both_records_show_up_in_what_the_submission_carries(make_service):
    """The parent changed too, so a pull request must describe both."""
    baseline = make_service()
    current = make_service()
    parent = current.get_diseases_list()[0]
    child = current.create_disease(
        {"label": "Test subtype", "definition": "d", "parent_iri": parent["iri"]},
        editor="tester")

    changes = ds.list_changes(current, baseline, {child["iri"], parent["iri"]})
    sub = {c["name"]: c for c in changes}["Test subtype"]
    assert sub["is_new"] is True
    # The relationship is reported on the child, where it lives. The parent's own
    # record changed only in its changelog, and no field diff reports those.
    assert sub["parent"] == parent["name"]
    assert f"new clinical subtype of {parent['name']}" in ds.render_summary(changes)
    assert ds.title_for(changes) == "Add Test subtype"
