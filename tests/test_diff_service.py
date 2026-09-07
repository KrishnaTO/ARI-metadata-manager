"""Coverage for the PR change-summary builder."""
import shutil
from types import SimpleNamespace

import pytest

from app import diff_service as ds


# --------------------------------------------------------------- pure helpers
def test_fmt_scalar_bool_and_list():
    assert ds._fmt(None) == ""
    assert ds._fmt(True) == "yes"
    assert ds._fmt(False) == "no"
    assert ds._fmt(["a", "b"]) == "a, b"
    assert ds._fmt("x") == "x"


def test_cell_escapes_truncates_and_marks_empty():
    assert ds._cell("") == "_(empty)_"
    assert ds._cell("a | b") == "a \\| b"        # pipes escaped for markdown tables
    long = ds._cell("x" * 400)
    assert long.endswith("…") and len(long) <= 301


# --------------------------------------------------------------- integration
# Every comparison here walks the whole ontology, which is the bulk of this
# module's runtime. The scenarios used to be staged one per test -- two fresh
# copies and a fresh diff to assert one facet of a result the neighbouring test
# had just computed too. The module now stages one working copy carrying each
# kind of change and diffs it once; the tests assert facets of that.
@pytest.fixture(scope="module")
def edited(tmp_path_factory, base_owl):
    """One working copy: a disease renamed *and* recategorised, a second disease
    edited (so `touched_iris` has something to exclude), a third left alone, and
    a disease created. `ro_service` is the pristine side of every comparison --
    diff_service and export_service only read, so the shared copy is safe."""
    from app.ontology_service import OntologyService

    onto_dir = tmp_path_factory.mktemp("diff") / "ontologies"
    onto_dir.mkdir(parents=True)
    dest = onto_dir / "ari_t1d.owl"
    shutil.copy2(base_owl, dest)
    current = OntologyService(str(dest))

    renamed, other, untouched = (d["iri"] for d in current.get_diseases_list()[:3])
    current.update_disease(renamed, {"name": "Renamed disease",
                                     "disease_category": "ZZZ-Diff-Test"}, editor="t")
    current.update_disease(other, {"disease_category": "ZZZ-Other"}, editor="t")
    current.create_disease({"label": "Brand New Test Disease", "definition": "d"}, editor="t")
    return SimpleNamespace(svc=current, renamed=renamed, other=other, untouched=untouched)


@pytest.fixture(scope="module")
def changes(edited, ro_service):
    return ds.list_changes(edited.svc, ro_service)


@pytest.fixture(scope="module")
def summary(changes):
    return ds.render_summary(changes)


def test_no_changes_reports_no_differences(make_service, ro_service):
    """Its own pair on purpose: two independent loads of the same file have to
    compare equal, which a service diffed against itself would not show."""
    assert "No field-level differences" in ds.build_change_summary(make_service(), ro_service)


def test_field_edit_shows_up_in_summary(summary):
    assert "Renamed disease" in summary
    assert "Category" in summary          # FIELDS label for disease_category
    assert "ZZZ-Diff-Test" in summary
    assert "| Field | Previous | New |" in summary


def test_new_disease_flagged_as_new(summary):
    assert "Brand New Test Disease" in summary
    assert "new disease" in summary.lower()


def test_touched_iris_restricts_summary_to_own_edits(edited, ro_service):
    """A curator's working copy can drift from a fresh baseline for diseases
    they never touched (e.g. another curator's merged PR); touched_iris keeps
    those out of this curator's own change summary."""
    scoped = ds.build_change_summary(edited.svc, ro_service, touched_iris={edited.renamed})
    assert "ZZZ-Diff-Test" in scoped
    assert "ZZZ-Other" not in scoped


# ------------------------------------------------------ what a submission carries
# Issue #25: the publish dialog offered a free-text title box and nothing else,
# so a curator could not see which diseases their pull request carried, and the
# default title named whichever record happened to be on screen.
def test_list_changes_names_each_changed_disease_and_its_fields(changes):
    c = {x["name"]: x for x in changes}["Renamed disease"]
    assert not c["is_new"]
    assert [f["label"] for f in c["fields"]] == ["Label", "Category"]
    assert next(f for f in c["fields"] if f["label"] == "Label")["new"] == "Renamed disease"


def test_a_new_disease_is_marked_new_rather_than_diffed(changes):
    new = [c for c in changes if c["is_new"]]
    assert [c["name"] for c in new] == ["Brand New Test Disease"]
    assert new[0]["fields"] == []


def test_a_touched_disease_with_nothing_actually_different_is_left_out(edited, ro_service):
    """`touched` is every disease the session opened for writing, not every one
    that changed — listing those would describe work the curator did not do."""
    assert ds.list_changes(edited.svc, ro_service, {edited.untouched}) == []


def test_the_summary_renders_exactly_what_the_list_reports(edited, ro_service, summary):
    """One comparison behind both, so a pull request cannot describe itself twice."""
    assert ds.build_change_summary(edited.svc, ro_service) == summary


# ------------------------------------------------------------------- PR titles
def test_a_title_says_what_the_submission_does():
    upd = [{"name": "Addison's disease", "is_new": False}]
    add = [{"name": "Brand new", "is_new": True}]
    assert ds.title_for(upd) == "Update Addison's disease"
    assert ds.title_for(add) == "Add Brand new"
    assert ds.title_for([]) == "Update ontology"


def test_two_diseases_are_named_and_three_are_counted():
    two = [{"name": "A", "is_new": False}, {"name": "B", "is_new": False}]
    three = two + [{"name": "C", "is_new": False}]
    assert ds.title_for(two) == "Update A and B"
    assert ds.title_for(three) == "Update 3 diseases"


def test_added_and_updated_are_reported_separately():
    mixed = [{"name": "New one", "is_new": True},
             {"name": "A", "is_new": False}, {"name": "B", "is_new": False}]
    assert ds.title_for(mixed) == "Add New one; update A and B"


def test_a_disease_only_in_the_baseline_is_reported_as_removed(make_service):
    """The summary listed removals before the structured list replaced it, and it
    must keep doing so — a record present on the source branch and absent here
    would otherwise publish with nothing in the body saying it had gone."""
    baseline = make_service()
    current = make_service()
    only_on_the_branch = baseline.create_disease(
        {"label": "Only on the branch", "definition": "d"}, editor="t")

    removed = [c for c in ds.list_changes(current, baseline) if c["removed"]]
    assert [c["name"] for c in removed] == [only_on_the_branch["name"]]
    assert "**removed**" in ds.render_summary(removed)
