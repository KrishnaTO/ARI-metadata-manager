"""Coverage for the PR change-summary builder."""
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
def test_no_changes_reports_no_differences(make_service):
    baseline = make_service()
    current = make_service()
    summary = ds.build_change_summary(current, baseline)
    assert "No field-level differences" in summary


def test_field_edit_shows_up_in_summary(make_service):
    baseline = make_service()
    current = make_service()
    iri = current.get_diseases_list()[0]["iri"]
    name = current.get_disease_detail(iri)["name"]
    current.update_disease(iri, {"disease_category": "ZZZ-Diff-Test"}, editor="t")

    summary = ds.build_change_summary(current, baseline)
    assert name in summary
    assert "Category" in summary          # FIELDS label for disease_category
    assert "ZZZ-Diff-Test" in summary
    assert "| Field | Previous | New |" in summary


def test_new_disease_flagged_as_new(make_service):
    baseline = make_service()
    current = make_service()
    current.create_disease({"label": "Brand New Test Disease"}, editor="t")
    summary = ds.build_change_summary(current, baseline)
    assert "Brand New Test Disease" in summary
    assert "new disease" in summary.lower()


def test_touched_iris_restricts_summary_to_own_edits(make_service):
    """A curator's working copy can drift from a fresh baseline for diseases
    they never touched (e.g. another curator's merged PR); touched_iris keeps
    those out of this curator's own change summary."""
    baseline = make_service()
    current = make_service()
    diseases = current.get_diseases_list()
    mine, other = diseases[0]["iri"], diseases[1]["iri"]
    current.update_disease(mine, {"disease_category": "ZZZ-Mine"}, editor="t")
    current.update_disease(other, {"disease_category": "ZZZ-Other"}, editor="t")

    summary = ds.build_change_summary(current, baseline, touched_iris={mine})
    assert "ZZZ-Mine" in summary
    assert "ZZZ-Other" not in summary


# ------------------------------------------------------ what a submission carries
# Issue #25: the publish dialog offered a free-text title box and nothing else,
# so a curator could not see which diseases their pull request carried, and the
# default title named whichever record happened to be on screen.
def test_list_changes_names_each_changed_disease_and_its_fields(make_service):
    baseline = make_service()
    current = make_service()
    target = current.get_diseases_list()[0]
    current.update_disease(target["iri"], {"name": "Renamed disease"}, editor="t")

    changes = ds.list_changes(current, baseline)
    assert len(changes) == 1
    c = changes[0]
    assert c["name"] == "Renamed disease" and not c["is_new"]
    assert [f["label"] for f in c["fields"]] == ["Label"]
    assert c["fields"][0]["new"] == "Renamed disease"


def test_a_new_disease_is_marked_new_rather_than_diffed(make_service):
    baseline = make_service()
    current = make_service()
    current.create_disease({"label": "Brand new", "definition": "d"}, editor="t")
    new = [c for c in ds.list_changes(current, baseline) if c["is_new"]]
    assert [c["name"] for c in new] == ["Brand new"]
    assert new[0]["fields"] == []


def test_a_touched_disease_with_nothing_actually_different_is_left_out(make_service):
    """`touched` is every disease the session opened for writing, not every one
    that changed — listing those would describe work the curator did not do."""
    baseline = make_service()
    current = make_service()
    target = current.get_diseases_list()[0]
    assert ds.list_changes(current, baseline, {target["iri"]}) == []


def test_the_summary_renders_exactly_what_the_list_reports(make_service):
    """One comparison behind both, so a pull request cannot describe itself twice."""
    baseline = make_service()
    current = make_service()
    target = current.get_diseases_list()[0]
    current.update_disease(target["iri"], {"name": "Renamed disease"}, editor="t")
    assert ds.build_change_summary(current, baseline) == \
        ds.render_summary(ds.list_changes(current, baseline))


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
