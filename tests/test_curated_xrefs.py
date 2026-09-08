"""The MONDO/DOID ids on five diseases that had stored a broader term's id.

Enrichment treats a confirmed cross-reference as an *exact* match and folds that
term's label and synonyms into the disease (``enrich_service``). An id naming the
disease's parent therefore hands it the parent's names and the parent's children,
which is what ``ARI_DOID 9744`` (*type 1 diabetes mellitus*) did to **LADA**.

These pin the five records the parent/sibling sweep found, against the real
reference indexes rather than a fixture: the defect is in the curated data, so a
stub index could not have caught it and cannot guard it.
"""
import pytest

from app import xref_registry
from app.predict_service import normalize

# disease label -> the term each stored id must resolve to, per database.
CORRECTED = {
    "Latent autoimmune diabetes in adults (LADA)": {
        "doid": "0080846", "mondo": "0850306"},
    "Autoimmune thyroiditis": {"mondo": "0007699"},
    "Cryptogenic organizing pneumonia": {"doid": "0050157"},
    "Uveitis": {"mondo": "0020283"},
    # DOID has no term for the acquired (autoimmune) disease at all, so the record
    # carries none: 12134 is *factor VIII deficiency*, congenital hemophilia A.
    "Acquired hemophilia": {"doid": None, "mondo": "0019139"},
}


def _detail(ro_service, label):
    for d in ro_service.get_diseases_list():
        if d["name"] == label:
            return ro_service.get_disease_detail(d["iri"])
    raise AssertionError(f"no disease labelled {label!r}")


@pytest.mark.parametrize("label,expected", sorted(CORRECTED.items()))
def test_the_stored_id_is_the_one_the_registry_curated(ro_service, label, expected):
    detail = _detail(ro_service, label)
    for db, want in expected.items():
        stored = [i for i in (xref_registry.normalize_id(db, v) for v in detail[db]) if i]
        assert stored == ([] if want is None else [want])


# Names each record used to be handed, and the term that handed them over. Asserting
# on the enrichment output rather than on the ids is what makes this a test of the
# defect: a future id naming any of these terms fails here whatever its number.
FOREIGN_NAMES = {
    "Latent autoimmune diabetes in adults (LADA)":
        ["type 1 diabetes mellitus", "IDDM", "insulin-dependent diabetes mellitus",
         "immune mediated diabetes", "diabetes mellitus, noninsulin-dependent, 1"],
    "Autoimmune thyroiditis": ["autoimmune thyroid disease", "Graves disease"],
    "Cryptogenic organizing pneumonia":
        ["Idiopathic fibrosing alveolitis", "idiopathic pulmonary fibrosis"],
    "Uveitis": ["endocervical adenocarcinoma"],
    "Acquired hemophilia": ["Hemophilia A", "Congenital factor VIII disorder",
                            "severe hemophilia A"],
}


@pytest.mark.parametrize("label,foreign", sorted(FOREIGN_NAMES.items()))
def test_enrichment_no_longer_offers_another_diseases_names(ro_service, label, foreign):
    """Nothing these records' cross-references propose belongs to a neighbour.

    The parent's *synonyms* and the parent's *children* arrive together — LADA was
    offered "insulin-dependent diabetes mellitus" as a name and thirteen numbered
    type 1 diabetes forms as subtypes — so both halves are checked.
    """
    detail = _detail(ro_service, label)
    confirmed = [{"iri": detail["iri"], "db": db,
                  "ids": [i for i in (xref_registry.normalize_id(db, v)
                                      for v in detail[db]) if i]}
                 for db in ("mondo", "doid") if detail[db]]
    proposed = ro_service.enrichment_preview(confirmed).get(detail["iri"], {})
    offered = {normalize(p["value"]) for p in proposed.get("synonyms", [])}
    offered |= {normalize(p["value"].partition(" - subtype")[0])
                for p in proposed.get("subtypes", [])}
    for name in foreign:
        assert normalize(name) not in offered, f"{label} is still offered {name!r}"
