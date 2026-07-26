"""Coverage for the concept-detail lookup (reference-review compare pane).

Like ``test_predict_service``, these build tiny ``<db>.index.tsv`` files (and their
``<db>.details.tsv`` sidecars) in ``tmp_path`` — no network, no ontology fixture — so
they exercise the real ``load_index`` -> reverse index -> ``lookup`` path end to end.
Definitions/parents live in the sidecar; an index with no sidecar must still answer,
reporting ``details_available: false``.
"""
import time
from pathlib import Path

from app import concept_service as cs
from app import predict_service as ps

INDEX_HEADER = "\t".join(ps.INDEX_COLS)      # id label synonyms + the ten db columns
_COL = {c: i for i, c in enumerate(ps.INDEX_COLS)}


def _irow(**cells) -> str:
    out = [""] * len(ps.INDEX_COLS)
    for k, v in cells.items():
        out[_COL[k]] = v
    return "\t".join(out)


def _write_index(path: Path, *rows: str) -> Path:
    path.write_text("\n".join([INDEX_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def _write_details(path: Path, *rows: tuple) -> Path:
    lines = ["id\tdefinition\tparents"] + ["\t".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _indexes(tmp_path: Path) -> list[ps.LexicalIndex]:
    return ps.get_indexes(tmp_path)


# ------------------------------------------------------------- id normalization
def test_norm_id_strips_prefix_and_leading_zeros():
    assert cs._norm_id("MONDO:0016264") == cs._norm_id("0016264") == "16264"
    assert cs._norm_id("DOID:2043") == "2043"
    assert cs._norm_id("ICD10CM:M32.1") == cs._norm_id("M32.1") == "m32.1"
    assert cs._norm_id("umls:C0004364") == "c0004364"


# ------------------------------------------------------------------ direct hits
def _doid_files(tmp, with_details=True):
    _write_index(tmp / "doid.index.tsv",
                 _irow(id="DOID:2043", label="autoimmune hepatitis",
                       synonyms="AIH | lupoid hepatitis", doid="2043"))
    if with_details:
        _write_details(tmp / "doid.details.tsv",
                       ("DOID:2043", "A hepatitis that is caused by autoimmunity.",
                        "hepatitis | autoimmune disease of gastrointestinal tract"))


def test_direct_hit_returns_full_detail(tmp_path):
    _doid_files(tmp_path)
    r = cs.lookup("doid", "DOID:2043", _indexes(tmp_path))
    assert r["found"] and r["direct"]
    assert r["id"] == "DOID:2043" and r["prefix"] == "DOID"
    assert r["label"] == "autoimmune hepatitis"
    assert r["synonyms"] == ["AIH", "lupoid hepatitis"]
    assert r["definition"].startswith("A hepatitis")
    assert r["parents"] == ["hepatitis", "autoimmune disease of gastrointestinal tract"]
    assert r["details_available"] and r["via"] == []
    assert r["url"] == "https://disease-ontology.org/?id=DOID:2043"
    assert "note" not in r


def test_direct_hit_in_mondo_own_index(tmp_path):
    _write_index(tmp_path / "mondo.index.tsv",
                 _irow(id="MONDO:0016264", label="autoimmune hepatitis",
                       mondo="0016264", snomed="408335007"))
    _write_details(tmp_path / "mondo.details.tsv",
                   ("MONDO:0016264", "def.", "hepatitis"))
    r = cs.lookup("mondo", "MONDO:0016264", _indexes(tmp_path))
    assert r["found"] and r["direct"] and r["label"] == "autoimmune hepatitis"
    assert r["definition"] == "def." and r["parents"] == ["hepatitis"]
    # a bare, unprefixed id resolves identically (id normalization)
    assert cs.lookup("mondo", "0016264", _indexes(tmp_path))["label"] == "autoimmune hepatitis"


# --------------------------------------------------------------- via a hub only
def _mondo_hub(tmp):
    _write_index(tmp / "mondo.index.tsv",
                 _irow(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                       snomed="408335007", umls="C0241910"))
    _write_details(tmp / "mondo.details.tsv",
                   ("MONDO:0016264", "hub def.", "hepatitis"))


def test_snomed_id_resolves_only_via_hub(tmp_path):
    _mondo_hub(tmp_path)
    r = cs.lookup("snomed", "408335007", _indexes(tmp_path))
    assert r["found"] and r["direct"] is False
    assert r["via"] == [{"source": "mondo", "id": "MONDO:0016264",
                         "label": "autoimmune hepatitis"}]
    assert r["label"] == "autoimmune hepatitis"
    # hub content is NOT presented as the target's own, even though a sidecar exists
    assert r["synonyms"] == [] and r["definition"] == "" and r["parents"] == []
    assert r["details_available"] is False
    assert "note" in r and "SNOMED has no index" in r["note"] and "mondo" in r["note"]


def test_umls_id_resolves_only_via_hub(tmp_path):
    _mondo_hub(tmp_path)
    r = cs.lookup("umls", "C0241910", _indexes(tmp_path))
    assert r["found"] and r["direct"] is False and r["via"]
    assert r["via"][0]["source"] == "mondo"
    assert "UMLS has no index" in r["note"]


# ------------------------------------------------- several hubs claim one id
def test_two_hubs_claiming_one_id_both_appear_in_via(tmp_path):
    _write_index(tmp_path / "mondo.index.tsv",
                 _irow(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                       snomed="408335007"))
    _write_index(tmp_path / "orphanet.index.tsv",
                 _irow(id="ORPHA:2137", label="autoimmune hepatitis", orphanet="2137",
                       snomed="408335007"))
    r = cs.lookup("snomed", "408335007", _indexes(tmp_path))
    assert {v["source"] for v in r["via"]} == {"mondo", "orphanet"}
    assert len(r["via"]) == 2


def test_conflicting_hub_labels_are_both_returned(tmp_path):
    _write_index(tmp_path / "mondo.index.tsv",
                 _irow(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                       snomed="408335007"))
    _write_index(tmp_path / "doid.index.tsv",
                 _irow(id="DOID:2043", label="lupoid hepatitis", doid="2043",
                       snomed="408335007"))
    r = cs.lookup("snomed", "408335007", _indexes(tmp_path))
    labels = {v["label"] for v in r["via"]}
    assert labels == {"autoimmune hepatitis", "lupoid hepatitis"}   # not collapsed
    assert "disagree on the label" in r["note"]


# --------------------------------------------------------------------- unknown
def test_unknown_id_is_found_false_not_an_exception(tmp_path):
    _doid_files(tmp_path)
    r = cs.lookup("doid", "DOID:99999999", _indexes(tmp_path))
    assert r["found"] is False and r["direct"] is False
    assert r["label"] == "" and r["via"] == []
    assert "not in the free reference indexes" in r["note"]


def test_omop_is_found_false_with_athena_note(tmp_path):
    _doid_files(tmp_path)
    r = cs.lookup("omop", "4239860", _indexes(tmp_path))
    assert r["found"] is False and "Athena" in r["note"]


def test_doid_number_does_not_match_a_mesh_term_of_the_same_number(tmp_path):
    # Same bare number 2043 in two databases must never cross-match.
    _write_index(tmp_path / "doid.index.tsv",
                 _irow(id="DOID:2043", label="autoimmune hepatitis", doid="2043"))
    _write_index(tmp_path / "mesh.index.tsv",
                 _irow(id="MESH:D002043", label="something else", mesh="2043"))
    assert cs.lookup("doid", "DOID:2043", _indexes(tmp_path))["label"] == "autoimmune hepatitis"
    # a mesh lookup of 2043 finds the mesh term, not the DOID one
    assert cs.lookup("mesh", "2043", _indexes(tmp_path))["label"] == "something else"


# ------------------------------------------ regression: index without a sidecar
def test_index_without_details_sidecar_reports_no_details(tmp_path):
    # An index present but its <db>.details.tsv absent (e.g. a checkout that never
    # regenerated) must still answer, reporting that details are unavailable — not
    # "this term has no definition".
    _doid_files(tmp_path, with_details=False)
    r = cs.lookup("doid", "DOID:2043", _indexes(tmp_path))
    assert r["found"] and r["direct"]
    assert r["label"] == "autoimmune hepatitis" and r["synonyms"] == ["AIH", "lupoid hepatitis"]
    assert r["details_available"] is False
    assert r["definition"] == "" and r["parents"] == []
    assert "note" in r and "aren't built" in r["note"]


def test_present_sidecar_but_term_absent_is_details_available_no_definition(tmp_path):
    # Sidecar exists but this term has no row in it (it genuinely has no definition):
    # details_available stays True, definition empty, and no "not built" note.
    _write_index(tmp_path / "doid.index.tsv",
                 _irow(id="DOID:2043", label="autoimmune hepatitis", doid="2043"))
    _write_details(tmp_path / "doid.details.tsv", ("DOID:9744", "some other def", ""))
    r = cs.lookup("doid", "DOID:2043", _indexes(tmp_path))
    assert r["details_available"] is True and r["definition"] == "" and "note" not in r


# --------------------------------------------------- caches
def test_reverse_cache_invalidates_on_index_change(tmp_path):
    f = _write_index(tmp_path / "doid.index.tsv",
                     _irow(id="DOID:2043", label="autoimmune hepatitis", doid="2043"))
    idx1 = _indexes(tmp_path)
    rev1 = cs._reverse_for(idx1)
    assert cs._reverse_for(idx1) is rev1                 # same list -> cached
    time.sleep(0.01)
    _write_index(f, _irow(id="DOID:2043", label="autoimmune hepatitis", doid="2043"),
                 _irow(id="DOID:9744", label="type 1 diabetes mellitus", doid="9744"))
    idx2 = _indexes(tmp_path)                             # get_indexes rebuilds on mtime/size change
    assert idx2 is not idx1
    rev2 = cs._reverse_for(idx2)
    assert rev2 is not rev1
    assert ("doid", "9744") in rev2 and ("doid", "9744") not in rev1


def test_details_cache_invalidates_on_sidecar_change(tmp_path):
    _write_index(tmp_path / "doid.index.tsv",
                 _irow(id="DOID:2043", label="autoimmune hepatitis", doid="2043"))
    _write_details(tmp_path / "doid.details.tsv", ("DOID:2043", "first def", ""))
    assert cs.lookup("doid", "DOID:2043", _indexes(tmp_path))["definition"] == "first def"
    time.sleep(0.01)
    _write_details(tmp_path / "doid.details.tsv", ("DOID:2043", "second def", "parent x"))
    r = cs.lookup("doid", "DOID:2043", _indexes(tmp_path))
    assert r["definition"] == "second def" and r["parents"] == ["parent x"]


# ------------------------------------------- prediction output is unaffected
def test_details_sidecar_does_not_affect_predictions(tmp_path):
    _write_index(tmp_path / "mondo.index.tsv",
                 _irow(id="MONDO:0005147", label="type 1 diabetes mellitus",
                       synonyms="juvenile diabetes", mondo="0005147", snomed="46635009",
                       doid="9744", omim="222100"))
    disease = {"ari_id": "ARI:1", "name": "Type 1 diabetes mellitus",
               "synonyms": [], "existing": {}}
    before = ps.build_predicted_sssom(ps.predict_for_disease(disease, ps.load_indexes(tmp_path)))
    _write_details(tmp_path / "mondo.details.tsv",
                   ("MONDO:0005147", "A diabetes.", "diabetes mellitus"))
    after = ps.build_predicted_sssom(ps.predict_for_disease(disease, ps.load_indexes(tmp_path)))
    assert before == after
