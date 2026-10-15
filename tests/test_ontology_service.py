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


def test_each_symptom_names_its_diseases_by_iri(ro_service):
    # The symptom search opens a disease from here. It matched names against the
    # whole disease list instead, which cost a second request.
    rows = ro_service.get_symptoms_index()
    assert rows and all("iri" not in r for r in rows)
    owner = rows[0]["diseases"][0]
    assert ro_service.get_disease_detail(owner["iri"])["name"] == owner["name"]


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


def test_search_finds_by_ari_id(ro_service):
    # The id as the record shows it ("ARI:0001170"), not only the IRI's local name.
    d = next(d for d in ro_service.get_xref_rows() if d["ari_id"])
    results = ro_service.search(d["ari_id"].lower())
    assert results[0]["iri"] == d["iri"]
    assert results[0]["match"] == d["ari_id"]


def test_search_caps_results_at_100(ro_service):
    # Single most-common vowel-ish token shouldn't blow past the documented cap.
    assert len(ro_service.search("a")) <= 100


def test_schema_is_category_map(ro_service):
    schema = ro_service.get_schema()
    assert "symptoms" in schema
    assert "fields" in schema["symptoms"]


# --------------------------------------------------------------- write API
def test_create_disease_assigns_sequential_ari_id(service):
    # _highest_ari_number() peeks; _next_ari_number() *reserves*, so the peek is
    # what a test may call without consuming a number.
    n0 = service._highest_ari_number()
    detail = service.create_disease(
        {"label": "Testology disease", "definition": "A synthetic test disease."},
        editor="tester",
    )
    assert detail["name"] == "Testology disease"
    assert detail["ari_id"], "new disease should carry an ARI id"
    assert re.fullmatch(r"ARI:\d{7}", detail["ari_id"][0])
    assert detail["ari_id"][0] == f"ARI:{n0 + 1:07d}"
    assert service._highest_ari_number() == n0 + 1
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


def test_update_disease_stores_xref_ids_without_their_prefix(service):
    # A curator pasting "MONDO:0012345" must be stored as the bare local part.
    # Storing the prefixed form is what let the SSSOM exporter prepend MONDO a
    # second time and publish MONDO:MONDO:0014523.
    iri = service.get_diseases_list()[0]["iri"]
    detail = service.update_disease(iri, {"mondo": "MONDO:0012345, 0067890"}, editor="tester")
    assert detail["mondo"] == ["0012345", "0067890"]


def test_update_disease_keeps_free_text_multi_fields_verbatim(service):
    # Only cross-reference columns are canonicalised; a synonym that happens to
    # contain a colon must survive untouched.
    iri = service.get_diseases_list()[0]["iri"]
    detail = service.update_disease(iri, {"synonyms": "Type 1: juvenile onset"}, editor="tester")
    assert detail["synonyms"] == ["Type 1: juvenile onset"]


def _t1d(svc):
    return next(d for d in svc.get_diseases_list() if d["name"] == "Type 1 diabetes mellitus")["iri"]


FIGURE = {"epiMeasure": "Prevalence", "epiRegion": "US", "epiYear": "2019",
          "epiSex": "Female", "epiRatePer100k": "18.5", "sourcePMID": "https://a.example/us"}


def test_t1d_prevalence_is_a_figure(ro_service):
    # The fixture's flat prevalencePer100k was moved to a figure (#229).
    [fig] = ro_service.get_disease_detail(_t1d(ro_service))["epidemiology"]
    assert (fig["name"], fig["rate"]) == ("Prevalence · US · All", [500.0])


def test_epidemiology_figure_round_trip(service):
    iri = _t1d(service)
    detail = service.add_item(iri, "prevalence", FIGURE, editor="t")
    fig = next(f for f in detail["epidemiology"] if f["sex"] == ["Female"])
    assert fig["name"] == "Prevalence · US · 2019 · Female"
    assert (fig["year"], fig["rate"], fig["source"]) == ([2019], [18.5], ["https://a.example/us"])

    # The label follows the fields it is made from.
    detail = service.update_item(fig["iri"], "prevalence", {"epiSex": "Male"}, disease_iri=iri, editor="t")
    assert any(f["name"] == "Prevalence · US · 2019 · Male" for f in detail["epidemiology"])

    detail = service.delete_item(fig["iri"], "prevalence", iri, editor="t")
    assert len(detail["epidemiology"]) == 1


@pytest.mark.parametrize("bad", [
    {"epiRatePer100k": "about 30"},     # not a number
    {"epiYear": "circa 2020"},          # not a whole number
    {"epiSex": "Other"},                # not one of the options
    {"epiRegion": ""},                  # required
])
def test_epidemiology_figure_refuses_bad_values(service, bad):
    from app.errors import Invalid
    iri = _t1d(service)
    with pytest.raises(Invalid):
        service.add_item(iri, "prevalence", {**FIGURE, **bad}, editor="t")
    # Refused before anything was written.
    assert len(service.get_disease_detail(iri)["epidemiology"]) == 1


def test_migration_moves_flat_rates_to_figures(tmp_path):
    from owlready2 import AnnotationProperty, DataProperty, Thing, World

    from scripts.migrate_epidemiology import migrate
    world = World()
    onto = world.get_ontology("http://example.org/ari#")
    with onto:
        class AutoimmuneDisease(Thing): pass
        class ARI_ChangeLog(AnnotationProperty): pass
        class prevalencePer100k(DataProperty): range = [float]
        class incidenceWorldPer100k(DataProperty): range = [float]
        class incidenceWorldYear(DataProperty): range = [int]
        class incidenceWorldSource(DataProperty): range = [str]
    d = AutoimmuneDisease("ARI_0000001")
    prevalencePer100k[d] = [12.5]
    incidenceWorldPer100k[d] = [1.1]
    incidenceWorldYear[d] = [2018]
    incidenceWorldSource[d] = ["https://w.example/x"]
    path = tmp_path / "ontologies" / "ari.owl"
    path.parent.mkdir()
    onto.save(file=str(path), format="rdfxml")

    assert migrate(path, "tester") == 2
    svc = OntologyService(str(path))
    detail = svc.get_disease_detail(d.iri)
    figs = {f["name"]: f for f in detail["epidemiology"]}
    assert figs["Prevalence · US · All"]["rate"] == [12.5]
    inc = figs["Incidence · Worldwide · 2018 · All"]
    assert (inc["rate"], inc["source"]) == ([1.1], ["https://w.example/x"])
    assert "Moved to prevalence & incidence figures" in detail["changelog"][-1]
    assert svc.world[svc.base + "prevalencePer100k"] is None     # flat fields are gone


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
