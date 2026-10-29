"""The concept view behind a biomarker row's expanded detail.

tests/fixtures/ontologies/ari_biomarkers.owl holds "Anti-GAD65 antibody"
(ARIB_0000001, targets GAD65, expressed in the islet of Langerhans), its child
"Anti-GAD65 IgG" (ARIB_0000005), the antigen "GAD65" (ARIB_0000002, PR and
HGNC links), "Glycated hemoglobin" (ARIB_0000003) and "Anti-IA-2 antibody"
(ARIB_0000004). In ari_disease.owl, Type 1 diabetes's GADA, IA-2A, GAD65 and
HbA1c entries and LADA's GADA entry are linked; Fulminant T1D's GADA is not.
"""
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.biomarker_concepts import concept_detail
from app.errors import NotFound

D = "http://www.aurint.org/ontologies/ari-disease#"
ARIB = "http://www.aurint.org/ontologies/ari-biomarkers#"
T1D = "https://diseases.autoimmuneregistry.org/disease/ARI_0001080"
LADA = D + "T1D_0002"
FULMINANT = D + "T1D_0003"


def _link(svc, item, *concepts):
    """Give ``item`` a sharedConcept triple per concept, as Protégé would."""
    prop = svc._annotation_property("sharedConcept")
    for concept in concepts:
        svc.onto._add_obj_triple_spo(
            svc._entity(item).storid, prop.storid, svc.world._abbreviate(ARIB + concept))


def test_detail_lists_target_and_gene(ro_service):
    detail = concept_detail(ro_service, D + "Ab_GAD65")
    assert (detail["entry"], detail["status"], detail["reason"]) == (D + "Ab_GAD65", "linked", None)
    [c] = detail["concepts"]
    assert (c["iri"], c["id"], c["label"], c["kind"]) == (
        ARIB + "ARIB_0000001", "ARIB:0000001", "Anti-GAD65 antibody", "antibodies")
    assert c["alt_labels"] == ["GADA"]
    # HPO has no slice: the curie, no label.
    assert c["external"] == [{"predicate": "skos:exactMatch",
                              "iri": "http://purl.obolibrary.org/obo/HP_5000011",
                              "curie": "HP:5000011", "label": None}]
    assert c["targets"] == [{
        "iri": ARIB + "ARIB_0000002", "label": "GAD65",
        "external": [
            {"predicate": "skos:exactMatch", "iri": "http://purl.obolibrary.org/obo/PR_Q05329",
             "curie": "PR:Q05329", "label": "glutamate decarboxylase 2 (human)"},
            {"predicate": "skos:relatedMatch", "iri": "http://identifiers.org/hgnc/4093",
             "curie": "HGNC:4093", "label": "GAD2"}]}]
    assert c["parents"] == []                  # its parent is the core kind, not a concept
    assert c["children"] == [{"iri": ARIB + "ARIB_0000005", "label": "Anti-GAD65 IgG"}]
    # The child inherits GAD65 but is family, listed under children only.
    assert c["same_target"] == []
    assert c["targeted_by"] == []
    assert c["body_parts"] == [{"iri": "http://purl.obolibrary.org/obo/UBERON_0000006",
                                "label": "islet of Langerhans"}]
    assert c["symptoms"] == []

    [antigen] = concept_detail(ro_service, D + "Antigen_GAD65")["concepts"]
    assert antigen["kind"] == "antigens"
    assert antigen["targets"] == [] and antigen["same_target"] == []
    assert antigen["targeted_by"] == [{"iri": ARIB + "ARIB_0000001", "label": "Anti-GAD65 antibody"},
                                      {"iri": ARIB + "ARIB_0000005", "label": "Anti-GAD65 IgG"}]

    [test] = concept_detail(ro_service, D + "Mark_HbA1c")["concepts"]
    assert test["kind"] == "tests"


def test_a_child_inherits_its_parents_target(service):
    detail = service.add_item(LADA, "antibodies", {"name": "Anti-GAD65 IgG"}, editor="t")
    item = next(i["iri"] for i in detail["antibodies"] if i["name"] == "Anti-GAD65 IgG")
    [c] = concept_detail(service, item)["concepts"]
    assert c["parents"] == [{"iri": ARIB + "ARIB_0000001", "label": "Anti-GAD65 antibody"}]
    assert [t["iri"] for t in c["targets"]] == [ARIB + "ARIB_0000002"]
    assert c["same_target"] == []                # its parent is family


def test_same_target_lists_other_antibodies_to_the_antigen(service):
    # Anti-IA-2 antibody, outside the GAD65 antibody's family, is given GAD65 too.
    prop = service._annotation_property("targetsAntigen")
    service.onto._add_obj_triple_spo(service.world._abbreviate(ARIB + "ARIB_0000004"), prop.storid,
                                     service.world._abbreviate(ARIB + "ARIB_0000002"))
    [c] = concept_detail(service, D + "Ab_GAD65")["concepts"]
    assert c["same_target"] == [{"iri": ARIB + "ARIB_0000004", "label": "Anti-IA-2 antibody"}]
    [c] = concept_detail(service, D + "Ab_IA2")["concepts"]
    assert c["same_target"] == [{"iri": ARIB + "ARIB_0000001", "label": "Anti-GAD65 antibody"},
                                {"iri": ARIB + "ARIB_0000005", "label": "Anti-GAD65 IgG"}]


def test_appears_with_counts_shared_diseases(service):
    # LADA gains an IA-2A entry, which links ARIB_0000004 by its altLabel.
    service.add_item(LADA, "antibodies", {"name": "IA-2A"}, editor="t")
    [c] = concept_detail(service, D + "Ab_GAD65")["concepts"]
    assert c["appears_with"] == [
        {"iri": ARIB + "ARIB_0000004", "label": "Anti-IA-2 antibody", "shared": 2},
        {"iri": ARIB + "ARIB_0000002", "label": "GAD65", "shared": 1},
        {"iri": ARIB + "ARIB_0000003", "label": "Glycated hemoglobin", "shared": 1}]
    # An obsolete entry counts for nothing.
    service.update_item(D + "Ab_IA2", "antibodies", {"obsolete": True}, disease_iri=T1D, editor="t")
    [c] = concept_detail(service, D + "Ab_GAD65")["concepts"]
    assert [(r["label"], r["shared"]) for r in c["appears_with"]] == [
        ("Anti-IA-2 antibody", 1), ("GAD65", 1), ("Glycated hemoglobin", 1)]


def test_also_in_excludes_the_entrys_disease(ro_service):
    [c] = concept_detail(ro_service, D + "Ab_GAD65")["concepts"]
    assert c["also_in"] == [{"iri": LADA, "label": "Latent autoimmune diabetes in adults (LADA)"}]
    [c] = concept_detail(ro_service, D + "Ab_LADA_GAD65")["concepts"]
    assert c["also_in"] == [{"iri": T1D, "label": "Type 1 diabetes mellitus"}]


def test_an_obsolete_disease_counts_nowhere(service):
    service.add_item(LADA, "antibodies", {"name": "IA-2A"}, editor="t")
    service.update_disease(LADA, {"obsolete": True}, editor="t")
    [c] = concept_detail(service, D + "Ab_GAD65")["concepts"]
    assert c["also_in"] == []
    assert [(r["label"], r["shared"]) for r in c["appears_with"]] == [
        ("Anti-IA-2 antibody", 1), ("GAD65", 1), ("Glycated hemoglobin", 1)]


def test_an_unlinked_entry_reports_unlinked(service):
    assert concept_detail(service, D + "Ab_Ful_GAD65") == {
        "entry": D + "Ab_Ful_GAD65", "status": "unlinked", "reason": None, "concepts": []}
    no_concept = service._annotation_property("ARI_NoConcept")
    no_concept[service._entity(D + "Ab_Ful_GAD65")].append("no term")
    assert concept_detail(service, D + "Ab_Ful_GAD65") == {
        "entry": D + "Ab_Ful_GAD65", "status": "no_concept", "reason": "no term", "concepts": []}


def test_a_bundle_returns_each_concept(service):
    _link(service, D + "Ab_Ful_GAD65", "ARIB_0000004", "ARIB_0000001")
    detail = concept_detail(service, D + "Ab_Ful_GAD65")
    assert detail["status"] == "bundle"
    assert [c["label"] for c in detail["concepts"]] == ["Anti-GAD65 antibody", "Anti-IA-2 antibody"]
    gad, ia2 = detail["concepts"]
    assert [d["iri"] for d in gad["also_in"]] == [LADA, T1D]
    assert [d["iri"] for d in ia2["also_in"]] == [T1D]


def test_a_non_biomarker_iri_is_not_found(ro_service):
    for iri in (T1D,                                   # a disease
                D + "Sym_WeightLoss",                  # a symptom entry
                ARIB + "ARIB_0000001",                 # the concept itself, not an entry
                "http://example.org/nope"):
        with pytest.raises(NotFound):
            concept_detail(ro_service, iri)


def test_the_route_serves_the_detail_and_404s_the_rest():
    client = TestClient(main.app)
    r = client.get("/api/v2/biomarker-concept/" + quote(D + "Ab_GAD65", safe=""))
    assert r.status_code == 200
    assert r.json()["status"] == "linked"
    r = client.get("/api/v2/biomarker-concept/" + quote(D + "Sym_WeightLoss", safe=""))
    assert r.status_code == 404


def test_objects_returns_distinct_values_sorted():
    """One link asserted in two loaded ontologies is one link, not a bundle."""
    from app.biomarker_concepts import _objects

    class World:
        _abbreviate = staticmethod(lambda iri, _: iri)
        _unabbreviate = staticmethod(lambda o: o)
        _get_obj_triples_sp_o = staticmethod(lambda s, p: ["b", "a", "b"])

    assert _objects(World, "s", "p") == ["a", "b"]
