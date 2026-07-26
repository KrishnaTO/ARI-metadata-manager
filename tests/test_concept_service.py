"""Coverage for the concept-detail lookup (reference-review compare pane).

Like ``test_predict_service``, these build tiny index TSVs in ``tmp_path`` — no
network, no ontology fixture — so they exercise the real ``load_index`` -> reverse
index -> ``lookup`` path end to end. The final test also proves the schema change was
*additive*: prediction output over a fixed index is byte-identical before and after.
"""
import time
from pathlib import Path

from app import concept_service as cs
from app import predict_service as ps

# Full new-schema header (13 + definition + parents).
NEW_HEADER = "\t".join(ps.INDEX_COLS)
# The pre-change header: the three descriptor columns + the ten db columns, no
# definition/parents. This is what a pull-without-regenerate leaves on disk.
OLD_HEADER = "\t".join(["id", "label", "synonyms"] + ps.TARGET_DBS)
_COL = {c: i for i, c in enumerate(ps.INDEX_COLS)}


def _row(**cells) -> str:
    out = [""] * len(ps.INDEX_COLS)
    for k, v in cells.items():
        out[_COL[k]] = v
    return "\t".join(out)


def _write(path: Path, header: str, *rows: str) -> Path:
    # An OLD_HEADER file must not carry the trailing new columns, so trim each row
    # to the header's width — exactly a file written before the schema change.
    width = len(header.split("\t"))
    body = ["\t".join(r.split("\t")[:width]) for r in rows]
    path.write_text("\n".join([header, *body]) + "\n", encoding="utf-8")
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
def _doid_file(path):
    return _write(path / "doid.index.tsv", NEW_HEADER,
                  _row(id="DOID:2043", label="autoimmune hepatitis",
                       synonyms="AIH | lupoid hepatitis", doid="2043",
                       definition="A hepatitis that is caused by autoimmunity.",
                       parents="hepatitis | autoimmune disease of gastrointestinal tract"))


def test_direct_hit_returns_full_detail(tmp_path):
    _doid_file(tmp_path)
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
    _write(tmp_path / "mondo.index.tsv", NEW_HEADER,
           _row(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                snomed="408335007", definition="def.", parents="hepatitis"))
    r = cs.lookup("mondo", "MONDO:0016264", _indexes(tmp_path))
    assert r["found"] and r["direct"] and r["label"] == "autoimmune hepatitis"
    assert r["definition"] == "def." and r["parents"] == ["hepatitis"]
    # a bare, unprefixed id resolves identically (id normalization)
    assert cs.lookup("mondo", "0016264", _indexes(tmp_path))["label"] == "autoimmune hepatitis"


# --------------------------------------------------------------- via a hub only
def _mondo_hub(path):
    return _write(path / "mondo.index.tsv", NEW_HEADER,
                  _row(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                       snomed="408335007", umls="C0241910",
                       definition="hub def.", parents="hepatitis"))


def test_snomed_id_resolves_only_via_hub(tmp_path):
    _mondo_hub(tmp_path)
    r = cs.lookup("snomed", "408335007", _indexes(tmp_path))
    assert r["found"] and r["direct"] is False
    assert r["via"] == [{"source": "mondo", "id": "MONDO:0016264",
                         "label": "autoimmune hepatitis"}]
    assert r["label"] == "autoimmune hepatitis"
    # hub content is NOT presented as the target's own
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
    _write(tmp_path / "mondo.index.tsv", NEW_HEADER,
           _row(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                snomed="408335007"))
    _write(tmp_path / "orphanet.index.tsv", NEW_HEADER,
           _row(id="ORPHA:2137", label="autoimmune hepatitis", orphanet="2137",
                snomed="408335007"))
    r = cs.lookup("snomed", "408335007", _indexes(tmp_path))
    assert {v["source"] for v in r["via"]} == {"mondo", "orphanet"}
    assert len(r["via"]) == 2


def test_conflicting_hub_labels_are_both_returned(tmp_path):
    _write(tmp_path / "mondo.index.tsv", NEW_HEADER,
           _row(id="MONDO:0016264", label="autoimmune hepatitis", mondo="0016264",
                snomed="408335007"))
    _write(tmp_path / "doid.index.tsv", NEW_HEADER,
           _row(id="DOID:2043", label="lupoid hepatitis", doid="2043",
                snomed="408335007"))
    r = cs.lookup("snomed", "408335007", _indexes(tmp_path))
    labels = {v["label"] for v in r["via"]}
    assert labels == {"autoimmune hepatitis", "lupoid hepatitis"}   # not collapsed
    assert "disagree on the label" in r["note"]


# --------------------------------------------------------------------- unknown
def test_unknown_id_is_found_false_not_an_exception(tmp_path):
    _doid_file(tmp_path)
    r = cs.lookup("doid", "DOID:99999999", _indexes(tmp_path))
    assert r["found"] is False and r["direct"] is False
    assert r["label"] == "" and r["via"] == []
    assert "not in the free reference indexes" in r["note"]


def test_omop_is_found_false_with_athena_note(tmp_path):
    _doid_file(tmp_path)
    r = cs.lookup("omop", "4239860", _indexes(tmp_path))
    assert r["found"] is False and "Athena" in r["note"]


def test_doid_number_does_not_match_a_mesh_term_of_the_same_number(tmp_path):
    # Same bare number 2043 in two databases must never cross-match.
    _write(tmp_path / "doid.index.tsv", NEW_HEADER,
           _row(id="DOID:2043", label="autoimmune hepatitis", doid="2043"))
    _write(tmp_path / "mesh.index.tsv", NEW_HEADER,
           _row(id="MESH:D002043", label="something else", mesh="2043"))
    assert cs.lookup("doid", "DOID:2043", _indexes(tmp_path))["label"] == "autoimmune hepatitis"
    # a mesh lookup of 2043 finds the mesh term, not the DOID one
    assert cs.lookup("mesh", "2043", _indexes(tmp_path))["label"] == "something else"


# ------------------------------------------ regression: index predating columns
def test_old_schema_file_loads_and_reports_no_details(tmp_path):
    # A file written before definition/parents existed must load, and the answer must
    # say details are unavailable (not "this term has no definition").
    _write(tmp_path / "doid.index.tsv", OLD_HEADER,
           _row(id="DOID:2043", label="autoimmune hepatitis",
                synonyms="AIH", doid="2043"))
    r = cs.lookup("doid", "DOID:2043", _indexes(tmp_path))
    assert r["found"] and r["direct"]
    assert r["label"] == "autoimmune hepatitis" and r["synonyms"] == ["AIH"]
    assert r["details_available"] is False
    assert r["definition"] == "" and r["parents"] == []
    assert "note" in r and "predates" in r["note"]


# --------------------------------------------------- reverse-index cache
def test_reverse_cache_invalidates_on_file_change(tmp_path):
    f = _write(tmp_path / "doid.index.tsv", NEW_HEADER,
               _row(id="DOID:2043", label="autoimmune hepatitis", doid="2043"))
    idx1 = _indexes(tmp_path)
    rev1 = cs._reverse_for(idx1)
    assert cs._reverse_for(idx1) is rev1                 # same list -> cached
    time.sleep(0.01)
    _write(f, NEW_HEADER,
           _row(id="DOID:2043", label="autoimmune hepatitis", doid="2043"),
           _row(id="DOID:9744", label="type 1 diabetes mellitus", doid="9744"))
    idx2 = _indexes(tmp_path)                             # get_indexes rebuilds on mtime/size change
    assert idx2 is not idx1
    rev2 = cs._reverse_for(idx2)
    assert rev2 is not rev1
    assert ("doid", "9744") in rev2 and ("doid", "9744") not in rev1


# ------------------------------------------- prediction output is unchanged
def test_schema_change_is_additive_to_predictions(tmp_path):
    # A fixed small index parsed under the new schema must yield byte-identical
    # predictions to the same rows under the old (definition-less) schema.
    args = dict(id="MONDO:0005147", label="type 1 diabetes mellitus",
                synonyms="juvenile diabetes", mondo="0005147", snomed="46635009",
                doid="9744", omim="222100")
    new_dir, old_dir = tmp_path / "new", tmp_path / "old"
    new_dir.mkdir()
    old_dir.mkdir()
    _write(new_dir / "mondo.index.tsv", NEW_HEADER,
           _row(**args, definition="A diabetes.", parents="diabetes mellitus"))
    _write(old_dir / "mondo.index.tsv", OLD_HEADER, _row(**args))
    disease = {"ari_id": "ARI:1", "name": "Type 1 diabetes mellitus",
               "synonyms": [], "existing": {}}
    p_new = ps.build_predicted_sssom(ps.predict_for_disease(disease, ps.load_indexes(new_dir)))
    p_old = ps.build_predicted_sssom(ps.predict_for_disease(disease, ps.load_indexes(old_dir)))
    assert p_new == p_old
