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
