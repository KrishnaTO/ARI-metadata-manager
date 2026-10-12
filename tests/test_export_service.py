"""Coverage for the Excel export builder."""
import io

import openpyxl

from app import export_service as ex


# --------------------------------------------------------------- pure helpers
def test_stringify_scalar_list_and_dicts():
    assert ex._s(None) == ""
    assert ex._s(["a", "b"]) == "a; b"
    assert ex._s([{"name": "x"}, {"label": "y"}, {"iri": "z"}]) == "x; y; z"
    assert ex._s("plain") == "plain"


def test_disease_to_row_marks_missing_codes():
    row = ex.disease_to_row({"name": "X", "iri": "i", "snomed": [], "omop": [], "dxcode": []})
    assert row["Code Status"] == "No code"
    assert row["Preferred Name"] == "X"

    coded = ex.disease_to_row({"name": "Y", "snomed": ["12345"]})
    assert coded["Code Status"] == ""
    assert coded["SNOMED Code(s)"] == "12345"


def test_subtypes_render_with_links():
    d = {"clinical_subtypes_parsed": [
        {"name": "Juvenile", "description": "early onset", "link_name": "Child disease"},
    ]}
    out = ex._subtypes(d)
    assert "Juvenile" in out and "early onset" in out and "Child disease" in out


# --------------------------------------------------------------- integration
def _load(xlsx_bytes):
    return openpyxl.load_workbook(io.BytesIO(xlsx_bytes)).active


# ------------------------------------------------------- whole-catalogue cost
# The sheet carries each disease's own fields, but the export built every
# disease's full record on both sides: its item lists, plus a scan of every other
# disease for its subtypes, 428 times over — ~0.6 s locally per export.
ITEM_KEYS = {"subtypes", "symptoms", "environmental_factors", "antibodies", "genetic",
             "treatments", "etiology", "biomarkers", "pathway", "cytokines", "tcells", "apcs",
             "transcription_factors", "innate_components", "complement", "receptors",
             "netosis", "inflammasome", "acute_phase_reactants", "antigens", "target_cells"}


def test_the_export_never_builds_a_full_disease_record(make_service, ro_service, monkeypatch):
    current = make_service()

    def refuse(iri):
        raise AssertionError("built a full disease record for the export")
    for svc in (current, ro_service):
        monkeypatch.setattr(svc, "get_disease_detail", refuse)
    assert _load(ex.build_report(current, ro_service)).max_row > 1


def test_a_disease_record_is_its_detail_without_the_items(ro_service):
    details = [ro_service.get_disease_detail(d["iri"]) for d in ro_service.get_diseases_list()]
    records = ro_service.get_disease_records()
    assert [r["iri"] for r in records] == [d["iri"] for d in details]
    for record, detail in zip(records, details):
        assert record == {k: v for k, v in detail.items() if k not in ITEM_KEYS}


def test_build_report_without_baseline(ro_service):
    ws = _load(ex.build_report(ro_service))
    headers = [c.value for c in ws[1]]
    assert "Preferred Name" in headers
    assert "Change Status" not in headers          # no baseline -> no status column
    assert ws.max_row > 1                           # at least one disease row


def test_build_report_with_baseline_flags_changes_and_new_diseases(make_service, ro_service):
    """Both statuses in one report. They come out of a single comparison pass, so
    staging them as two tests meant two whole-ontology diffs to assert one cell
    value each — and this way they are also shown to coexist. The pristine side is
    the shared `ro_service`: export_service only reads it.
    """
    current = make_service()
    iri = current.get_diseases_list()[0]["iri"]
    current.update_disease(iri, {"disease_category": "ZZZ-Export-Test"}, editor="t")
    current.create_disease({"label": "Exportable New Disease"}, editor="t")

    ws = _load(ex.build_report(current, ro_service))
    headers = [c.value for c in ws[1]]
    assert headers[0] == "Change Status"
    statuses = {ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)}
    assert {"Modified", "New"} <= statuses
