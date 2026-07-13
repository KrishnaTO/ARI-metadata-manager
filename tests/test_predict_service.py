"""Coverage for the cross-reference prediction service (issue #42).

The unit tests build a tiny in-memory index so they don't depend on the multi-MB
downloaded ontologies. The integration tests run only when the real
``data/2-databases`` indexes are present (built by ``scripts/fetch_databases.py``).
"""
from pathlib import Path

import pytest

from app import predict_service as ps

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "data" / "2-databases"


# --------------------------------------------------------------- normalization
def test_normalize_folds_case_accents_and_punctuation():
    assert ps.normalize("Sjögren's Syndrome") == "sjogren s syndrome"
    assert ps.normalize("  Type 1  Diabetes ") == "type 1 diabetes"
    assert ps.normalize("CREST-syndrome") == ps.normalize("CREST syndrome")


def test_normalize_does_not_fold_numbers_or_roman_numerals():
    # "exact" must not collapse genuinely different concepts.
    assert ps.normalize("type 1 diabetes") != ps.normalize("type i diabetes")


# ------------------------------------------------------------------- indexing
def _index(*records):
    """Build a LexicalIndex from ``(id, label, synonyms, {db: [ids]})`` tuples."""
    idx = ps.LexicalIndex("test")
    for ident, label, syns, by_db in records:
        idx.add({"id": ident, "label": label, "by_db": by_db}, [label, *syns])
    return idx


def _disease(name, ari="ARI:0000001", synonyms=None, existing=None):
    return {"ari_id": ari, "name": name, "synonyms": synonyms or [], "existing": existing or {}}


HUB = _index(
    ("MONDO:0005147", "type 1 diabetes mellitus", ["juvenile diabetes"],
     {"mondo": ["0005147"], "snomed": ["46635009"], "doid": ["9744"],
      "omim": ["222100"], "orphanet": ["243377"], "umls": ["C0011854"]}),
)


def test_predicts_all_xreffed_dbs_for_a_label_match():
    preds = ps.predict_for_disease(_disease("Type 1 diabetes mellitus"), [HUB])
    got = {p["db"]: p["id"] for p in preds}
    assert got["mondo"] == "0005147"
    assert got["snomed"] == "46635009"
    assert got["omim"] == "222100"
    assert got["orphanet"] == "243377"
    # prefix comes from the shared registry, not invented here
    snomed = next(p for p in preds if p["db"] == "snomed")
    assert snomed["prefix"] == "SNOMEDCT"
    assert snomed["object_label"] == "type 1 diabetes mellitus"


def test_synonym_match_is_flagged_as_synonym_and_low_confidence():
    # Label ("Sugar sickness") misses; only a disease synonym hits the hub term, so
    # the match falls back to synonyms and is flagged low-confidence.
    d = _disease("Sugar sickness", synonyms=["Juvenile diabetes"])
    preds = ps.predict_for_disease(d, [HUB])
    assert preds and all(p["match_field"] == "synonym" for p in preds)
    assert all(p["confidence"] == "low" for p in preds)


def test_label_match_is_high_confidence():
    p = ps.predict_for_disease(_disease("type 1 diabetes mellitus"), [HUB])[0]
    assert p["match_field"] == "label" and p["confidence"] == "high"


# Two distinct concepts: a label match (gastritis) and a separate concept that a
# mis-curated synonym (an associated condition) would otherwise pull in (anemia).
ANCHOR = _index(
    ("MONDO:0031014", "autoimmune gastritis", [], {"mondo": ["0031014"], "snomed": ["111"]}),
    ("MONDO:0001700", "megaloblastic anemia", [], {"mondo": ["0001700"], "snomed": ["222"]}),
)


def test_label_anchor_suppresses_conflicting_synonym():
    # Reproduces issue: "Autoimmune gastritis" with an associated-condition synonym.
    d = _disease("Autoimmune gastritis", synonyms=["Megaloblastic anemia"])
    preds = ps.predict_for_disease(d, [ANCHOR])
    ids = {(p["db"], p["id"]) for p in preds}
    assert ("mondo", "0031014") in ids and ("snomed", "111") in ids   # the disease itself
    assert ("mondo", "0001700") not in ids                            # associated condition dropped
    assert ("snomed", "222") not in ids
    assert all(p["confidence"] == "high" for p in preds)


def test_synonym_used_only_when_label_matches_nothing():
    # No label match anywhere -> the synonym is the disease's only handle (Kawasaki
    # pattern); it is kept but as a low-confidence candidate.
    d = _disease("Some descriptive ARI label", synonyms=["Megaloblastic anemia"])
    preds = ps.predict_for_disease(d, [ANCHOR])
    ids = {(p["db"], p["id"]) for p in preds}
    assert ("mondo", "0001700") in ids
    assert all(p["confidence"] == "low" for p in preds)


def test_blocklist_removes_a_synonym_fallback_prediction():
    # A blocklisted (mis-curated) synonym is dropped even in the synonym-only path.
    d = _disease("Some descriptive ARI label", ari="ARI:0009999",
                 synonyms=["Megaloblastic anemia"])
    block = {"ARI:0009999": {ps.normalize("Megaloblastic anemia")}}
    preds = ps.predict_for_disease(d, [ANCHOR], blocklist=block)
    assert preds == []                              # its only handle was blocklisted


def test_load_synonym_blocklist(tmp_path):
    p = tmp_path / "block.tsv"
    p.write_text("# comment\nari_id\tsynonym\tnames_instead\n"
                 "ARI:0001031\tMegaloblastic Anemia\tDOID:13382 megaloblastic anemia\n",
                 encoding="utf-8")
    bl = ps.load_synonym_blocklist(p)
    assert bl == {"ARI:0001031": {ps.normalize("Megaloblastic Anemia")}}
    assert ps.load_synonym_blocklist(tmp_path / "missing.tsv") == {}


def test_blank_cells_only_no_prediction_when_already_filled():
    d = _disease("Type 1 diabetes mellitus", existing={"snomed": ["46635009"], "omim": ["222100"]})
    dbs = {p["db"] for p in ps.predict_for_disease(d, [HUB])}
    assert "snomed" not in dbs and "omim" not in dbs   # already present
    assert "orphanet" in dbs                            # still blank -> predicted


def test_no_match_yields_nothing():
    assert ps.predict_for_disease(_disease("A disease that matches nothing"), [HUB]) == []


def test_omop_is_never_predicted_from_ontology_hub():
    # OMOP is OHDSI-specific; MONDO carries no OMOP xref, so it must stay blank.
    dbs = {p["db"] for p in ps.predict_for_disease(_disease("Type 1 diabetes mellitus"), [HUB])}
    assert "omop" not in dbs


def test_evidence_records_source_and_via():
    p = ps.predict_for_disease(_disease("Type 1 diabetes mellitus"), [HUB])[0]
    ev = p["evidence"][0]
    assert ev["source"] == "test" and ev["via"] == "MONDO:0005147"


# ------------------------------------------------------------------ SSSOM I/O
def test_build_and_reload_predicted_sssom_round_trips():
    preds = ps.predict_for_disease(_disease("Type 1 diabetes mellitus"), [HUB])
    tsv = ps.build_predicted_sssom(preds)
    assert "semapv:LexicalMatching" in tsv
    assert "SNOMEDCT:46635009" in tsv
    reloaded = ps.load_predictions(tsv)
    assert {r["prefix"] + ":" + r["id"] for r in reloaded} == {
        p["prefix"] + ":" + p["id"] for p in preds}
    # object_label + confidence survive the round-trip (issue #42: name from source)
    assert all(r["object_label"] == "type 1 diabetes mellitus" for r in reloaded)
    assert all(r["confidence"] == "high" for r in reloaded)


def test_to_cells_shape_matches_mappings_endpoint():
    cells = ps.to_cells(ps.predict_for_disease(_disease("Type 1 diabetes mellitus"), [HUB]))
    c = cells[0]
    assert set(c) == {"ari_id", "prefix", "id", "dbs", "object_label", "match_field", "confidence"}
    # SNOMED's prefix backs both snomed and dxcode review columns
    snomed = next(x for x in cells if x["prefix"] == "SNOMEDCT")
    assert set(snomed["dbs"]) == {"snomed", "dxcode"}


def test_load_predictions_empty_input():
    assert ps.load_predictions("") == []
    assert ps.load_predictions("   ") == []


# --------------------------------------------------------------- integration
_have_indexes = any(INDEX_DIR.glob("*.index.tsv")) if INDEX_DIR.is_dir() else False
needs_indexes = pytest.mark.skipif(not _have_indexes,
    reason="run scripts/fetch_databases.py to build data/2-databases indexes")


@needs_indexes
def test_real_indexes_predict_type1_diabetes_across_databases():
    indexes = ps.load_indexes(INDEX_DIR)
    preds = ps.predict_for_disease(_disease("Type 1 diabetes mellitus"), indexes)
    got = {p["db"] for p in preds}
    # MONDO term MONDO:0005147 cross-references these, so all should be predicted.
    assert {"mondo", "snomed", "doid", "nci", "omim", "orphanet", "umls", "mesh"} <= got


@needs_indexes
def test_real_indexes_predictions_are_well_formed():
    indexes = ps.load_indexes(INDEX_DIR)
    preds = ps.predict_matches([_disease("Multiple sclerosis")], indexes=indexes)
    for p in preds:
        assert p["id"] and p["prefix"] and p["db"] in ps.TARGET_DBS
        assert p["object_label"]            # a name from the source is always attached


@needs_indexes
def test_service_predict_xrefs_only_fills_blanks(ro_service):
    cells = ro_service.predict_xrefs()
    rows = {r["iri"]: r for r in ro_service.get_xref_rows()}
    by_ari = {r["ari_id"]: r for r in rows.values()}
    for c in cells:
        row = by_ari.get(c["ari_id"])
        if not row:
            continue
        for db in c["dbs"]:
            if db in row:
                assert not row[db], f"predicted {db} for a non-blank cell of {c['ari_id']}"


@needs_indexes
def test_report_flags_the_reported_mis_curated_synonym(base_owl):
    import importlib.util
    spec = importlib.util.spec_from_file_location("report_synonyms", ROOT / "scripts" / "report_synonyms.py")
    rep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rep)
    rows = rep.analyse(base_owl)
    # The reported case: "Autoimmune gastritis" wrongly lists "Megaloblastic anemia".
    hit = [r for r in rows if r[1] == "Autoimmune gastritis"
           and ps.normalize(r[2]) == ps.normalize("Megaloblastic anemia")]
    assert hit and hit[0][8] == "conflicts_with_label"
    # A legitimate verbose-label case must NOT be a hard conflict (label matches
    # nothing, so it is a review item, never auto-blocklisted).
    review = [r for r in rows if r[8] == "synonym_only_review"]
    assert all(r[8] != "conflicts_with_label" for r in review)
