"""Read + write coverage for the OntologyService — the core, riskiest surface."""
import re

import pytest

from app.ontology_service import OntologyService, _split_csv


# --------------------------------------------------------------- pure helpers
def test_split_csv_splits_dedups_and_preserves_order():
    assert _split_csv(["12345, 67890", "12345"]) == ["12345", "67890"]
    assert _split_csv(["  a ", "b", "a"]) == ["a", "b"]
    assert _split_csv([]) == []
    assert _split_csv(None) == []


# --------------------------------------------------------------- read API
def test_overview_shape(ro_service):
    ov = ro_service.overview()
    assert ov["disease_count"] > 0
    assert ov["individuals"] > ov["disease_count"]
    assert ov["classes"] > 0
    assert ov["iri"].startswith("http")


def test_diseases_list_sorted_and_shaped(ro_service):
    diseases = ro_service.get_diseases_list()
    assert diseases
    names = [d["name"] for d in diseases]
    assert names == sorted(names)
    for key in ("iri", "name", "local_name", "obsolete", "synonyms"):
        assert key in diseases[0]


def test_xref_rows_expose_every_configured_database(ro_service):
    rows = ro_service.get_xref_rows()
    assert rows
    row = rows[0]
    assert "iri" in row and "name" in row and "ari_id" in row
    for key in OntologyService.XREF_SUFFIXES:
        assert key in row, f"xref column {key!r} missing"


def test_alphabetical_tree_nodes_have_children(ro_service):
    tree = ro_service.get_alphabetical_tree()
    assert isinstance(tree, list) and tree
    assert "children" in tree[0] and "name" in tree[0]


def test_search_finds_by_name(ro_service):
    results = ro_service.search("diabetes")
    assert results
    assert all("is_disease" in r and "iri" in r for r in results)
    assert any("diabetes" in r["name"].lower() for r in results)


def test_search_caps_results_at_100(ro_service):
    # Single most-common vowel-ish token shouldn't blow past the documented cap.
    assert len(ro_service.search("a")) <= 100


def test_schema_is_category_map(ro_service):
    schema = ro_service.get_schema()
    assert "symptoms" in schema
    assert "fields" in schema["symptoms"]


# --------------------------------------------------------------- write API
def test_create_disease_assigns_sequential_ari_id(service):
    n0 = service._next_ari_number()
    detail = service.create_disease(
        {"label": "Testology disease", "definition": "A synthetic test disease."},
        editor="tester",
    )
    assert detail["name"] == "Testology disease"
    assert detail["ari_id"], "new disease should carry an ARI id"
    assert re.fullmatch(r"ARI:\d{7}", detail["ari_id"][0])
    assert detail["ari_id"][0] == f"ARI:{n0:07d}"
    assert service._next_ari_number() == n0 + 1
    # It should now appear in the flat list.
    assert any(d["iri"] == detail["iri"] for d in service.get_diseases_list())


def test_create_disease_requires_label(service):
    with pytest.raises(ValueError):
        service.create_disease({"label": "   "})


def test_update_disease_changes_field_and_appends_changelog(service):
    iri = service.get_diseases_list()[0]["iri"]
    before = len(service.get_disease_detail(iri)["changelog"])
    detail = service.update_disease(iri, {"disease_category": "ZZZ-Test-Category"}, editor="tester")
    assert detail["disease_category"] == ["ZZZ-Test-Category"]
    assert len(detail["changelog"]) == before + 1
    assert "tester" in detail["changelog"][-1]


def test_update_disease_ignores_unknown_field(service):
    iri = service.get_diseases_list()[0]["iri"]
    before = len(service.get_disease_detail(iri)["changelog"])
    # Only an unknown key -> nothing changed, so no changelog entry is appended.
    detail = service.update_disease(iri, {"totally_unknown_field": "x"}, editor="tester")
    assert len(detail["changelog"]) == before


def test_item_crud_round_trip(service):
    iri = service.get_diseases_list()[0]["iri"]
    before = len(service.get_disease_detail(iri)["symptoms"])

    # add
    detail = service.add_item(iri, "symptoms", {"name": "Testitis", "likelihood": "Common"}, editor="t")
    syms = detail["symptoms"]
    assert len(syms) == before + 1
    new = next(s for s in syms if s["name"] == "Testitis")
    assert new["likelihood"] == ["Common"]

    # update
    detail = service.update_item(new["iri"], "symptoms", {"likelihood": "Rare"}, disease_iri=iri, editor="t")
    updated = next(s for s in detail["symptoms"] if s["iri"] == new["iri"])
    assert updated["likelihood"] == ["Rare"]

    # delete
    detail = service.delete_item(new["iri"], "symptoms", iri, editor="t")
    assert new["iri"] not in [s["iri"] for s in detail["symptoms"]]


def test_add_item_rejects_unknown_category(service):
    iri = service.get_diseases_list()[0]["iri"]
    with pytest.raises(KeyError):
        service.add_item(iri, "not-a-category", {"name": "x"})


def test_get_disease_detail_unknown_iri_raises(ro_service):
    with pytest.raises(KeyError):
        ro_service.get_disease_detail("https://example.org/nope")
