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


def test_update_disease_logs_and_writes_only_fields_that_changed(service):
    # The editor form sends every field on each save (collectDiseaseFields). A
    # one-synonym save on staging logged "Edited:" with all 13 fields and wrote
    # an ARI_IsGrouping "false" the record never had.
    iri = service.get_diseases_list()[0]["iri"]
    d = service.get_disease_detail(iri)
    first = lambda vals: str(vals[0]) if vals else ""  # noqa: E731
    form = {
        "name": d["name"], "definition": d["definition"],
        "synonyms": d["synonyms"] + ["Staging test synonym"],
        "clinical_subtypes": d["clinical_subtypes"], "def_source": d["def_source"],
        "disease_category": first(d["disease_category"]),
        "evidence_quality": first(d["evidence_quality"]),
        "prevalence_desc": first(d["prevalence_desc"]),
        "obsolete": "true" if d["obsolete"] else "false",
        "is_grouping": "true" if d["is_grouping"] else "false",
    }
    e = service._entity(iri)
    grouping_before = service._get_annotation(e, service.base + "ARI_IsGrouping")

    detail = service.update_disease(iri, form, editor="tester")

    assert detail["changelog"][-1].endswith("| tester | Edited: synonyms")
    assert service._get_annotation(e, service.base + "ARI_IsGrouping") == grouping_before


def test_update_disease_with_nothing_changed_logs_nothing(service):
    iri = service.get_diseases_list()[0]["iri"]
    d = service.get_disease_detail(iri)
    detail = service.update_disease(iri, {"synonyms": list(reversed(d["synonyms"])),
                                          "obsolete": "false"}, editor="tester")
    assert detail["changelog"] == d["changelog"]


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
    # The service refuses to load an ontology that does not declare skos:altLabel
    # (ari_core.owl does), so this one declares it too.
    from owlready2.base import owl_annotation_property, rdf_type
    onto._add_obj_triple_spo(world._abbreviate("http://www.w3.org/2004/02/skos/core#altLabel"),
                             rdf_type, owl_annotation_property)
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


def test_a_new_biomarker_is_a_clinical_biomarker(service):
    iri = service.get_diseases_list()[0]["iri"]
    detail = service.add_item(iri, "biomarkers", {
        "name": "Skin biopsy", "markerType": "Histopathological", "markerSensitivity": "90–100%"}, editor="t")
    new = next(m for m in detail["biomarkers"] if m["name"] == "Skin biopsy")
    assert (new["type"], new["sensitivity"]) == (["Histopathological"], ["90–100%"])
    assert service.world[new["iri"]].is_a == [service.world[service.base + "ClinicalBiomarker"]]


def test_add_item_rejects_unknown_category(service):
    iri = service.get_diseases_list()[0]["iri"]
    with pytest.raises(KeyError):
        service.add_item(iri, "not-a-category", {"name": "x"})


def test_get_disease_detail_unknown_iri_raises(ro_service):
    with pytest.raises(KeyError):
        ro_service.get_disease_detail("https://example.org/nope")


ARIS = "http://www.aurint.org/ontologies/ari-symptoms#"


def test_a_symptom_links_the_shared_symptom_its_label_names(service):
    # tests/fixtures/ontologies/ari_symptoms.owl: Polyuria is ARIS_0000390;
    # Bloating is ARIS_0000034, with altLabel "Abdominal bloating".
    iri = service.get_diseases_list()[0]["iri"]
    service.add_item(iri, "symptoms", {"name": " Polyuria "}, editor="t")
    item = next(s["iri"] for s in service.get_disease_detail(iri)["symptoms"] if s["name"] == " Polyuria ")

    def links(svc):
        """The item's sharedConcept lines as saved: resources, not literals."""
        text = svc.path.read_text(encoding="utf-8")
        block = text[text.index(f'rdf:about="#{item.rsplit("#", 1)[1]}"'):]
        return [line.strip() for line in block[:block.index("\n</")].splitlines()
                if "sharedConcept" in line]

    assert links(service) == [f'<sharedConcept rdf:resource="{ARIS}ARIS_0000390"/>']
    # Each step on a fresh load, where owlready2 reads the link back from the file.
    for name, want in [("Abdominal bloating", "ARIS_0000034"),
                       ("Absence of orthostatic hypotension", None),
                       ("Bloating", "ARIS_0000034"),
                       ("polyuria", None),
                       ("Polyuria", "ARIS_0000390")]:
        svc = OntologyService(str(service.path))
        svc.update_item(item, "symptoms", {"name": name}, disease_iri=iri, editor="t")
        assert links(svc) == ([f'<sharedConcept rdf:resource="{ARIS}{want}"/>'] if want else []), name
    # Edits that leave the label alone leave the link alone.
    svc = OntologyService(str(service.path))
    svc.update_item(item, "symptoms", {"likelihood": "Rare"}, disease_iri=iri, editor="t")
    assert links(svc) == [f'<sharedConcept rdf:resource="{ARIS}ARIS_0000390"/>']


def test_renaming_a_symptom_drops_its_no_concept_decision(service):
    iri = service.get_diseases_list()[0]["iri"]
    service.add_item(iri, "symptoms", {"name": "Rare:"}, editor="t")
    item = next(s["iri"] for s in service.get_disease_detail(iri)["symptoms"] if s["name"] == "Rare:")
    no_concept = service.world[service.base + "ARI_NoConcept"]
    no_concept[service._entity(item)] = ["not a symptom"]
    service._save()
    svc = OntologyService(str(service.path))
    svc.update_item(item, "symptoms", {"likelihood": "Rare"}, disease_iri=iri, editor="t")
    assert svc._get_annotation(svc._entity(item), svc.base + "ARI_NoConcept") == ["not a symptom"]
    svc.update_item(item, "symptoms", {"name": "Polyuria"}, disease_iri=iri, editor="t")
    svc = OntologyService(str(service.path))
    assert svc._get_annotation(svc._entity(item), svc.base + "ARI_NoConcept") == []
    linked = svc._get_objects(svc._entity(item), svc.base + "sharedConcept")
    assert [c.iri for c in linked] == [ARIS + "ARIS_0000390"]     # the class loads from the symptoms module


def test_saving_a_symptom_with_its_name_unchanged_keeps_its_no_concept_decision(service):
    # The editor PUTs every form field on every save, so "name" is always present.
    iri = service.get_diseases_list()[0]["iri"]
    service.add_item(iri, "symptoms", {"name": "Rare:"}, editor="t")
    item = next(s["iri"] for s in service.get_disease_detail(iri)["symptoms"] if s["name"] == "Rare:")
    service.world[service.base + "ARI_NoConcept"][service._entity(item)] = ["not a symptom"]
    service._save()
    svc = OntologyService(str(service.path))
    svc.update_item(item, "symptoms", {"name": "Rare:", "likelihood": "Rare"}, disease_iri=iri, editor="t")
    svc = OntologyService(str(service.path))
    assert svc._get_annotation(svc._entity(item), svc.base + "ARI_NoConcept") == ["not a symptom"]
    assert svc._get_objects(svc._entity(item), svc.base + "sharedConcept") == []


def test_a_string_naming_two_concepts_is_refused():
    from owlready2 import AnnotationProperty, Thing, World
    from app.concepts import ARIS_BASE, concept_index
    world = World()
    skos = world.get_ontology("http://www.w3.org/2004/02/skos/core#")
    onto = world.get_ontology(ARIS_BASE)
    with skos:
        type("altLabel", (AnnotationProperty,), {})
    with onto:
        type("ARIS_1", (Thing,), {}).label = ["Fatigue"]
        type("ARIS_2", (Thing,), {}).label = ["Fatigue"]
    with pytest.raises(ValueError, match="Fatigue"):
        concept_index(world, ARIS_BASE)


def test_tissues_are_read_from_the_anatomy_module(ro_service):
    names = {t["local_name"] for t in ro_service.get_tissues()}
    assert len(names) == 23 and {"Tissue_Pancreas", "Tissue_Systemic"} <= names


def test_tissue_hierarchy_holds_every_tissue_class(ro_service):
    seen = set()

    def walk(n):
        seen.add(n["local_name"])
        for c in n["children"]:
            walk(c)

    walk(ro_service.get_tissue_hierarchy()[0])
    assert len(seen) == 26 and {"Pancreas", "IsletOfLangerhans", "BetaCell"} <= seen


def test_search_finds_a_tissue(ro_service):
    assert any(r["local_name"] == "Tissue_Pancreas" for r in ro_service.search("pancreas"))
