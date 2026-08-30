"""Coverage for the enrichment engine (synonyms + subtypes from confirmed xrefs).

Uses tiny in-memory indexes / subtype maps so it never depends on the multi-MB
downloaded ontologies.
"""
from app import enrich_service as es
from app import predict_service as ps


def _index(*records):
    """LexicalIndex from ``(id, label, synonyms, {db: [ids]})`` tuples."""
    idx = ps.LexicalIndex("test")
    for ident, label, syns, by_db in records:
        idx.add({"id": ident, "label": label, "synonyms": list(syns), "by_db": by_db},
                [label, *syns])
    return idx


# MONDO hub term for type 1 diabetes, cross-referencing SNOMED + DOID.
HUB = _index(
    ("MONDO:0005147", "type 1 diabetes mellitus", ["juvenile diabetes", "insulin dependent diabetes"],
     {"mondo": ["0005147"], "snomed": ["46635009"], "doid": ["9744"]}),
)

# Two direct children of the hub term in MONDO's is_a hierarchy.
SUBTYPES = {
    "MONDO:0005147": [
        {"id": "MONDO:0011899", "label": "type 1 diabetes mellitus 2"},
        {"id": "MONDO:0012819", "label": "type 1 diabetes mellitus 21"},
    ],
}


def _disease(name="Type 1 diabetes", ari="ARI:0000001", synonyms=None, clinical_subtypes=None):
    return {"iri": "iri:t1d", "ari_id": ari, "name": name,
            "synonyms": synonyms or [], "clinical_subtypes": clinical_subtypes or []}


def _enrich(disease, confirmed, indexes=(HUB,), subtypes=SUBTYPES, blocklist=None):
    return es.enrich(disease, confirmed, es.build_id_index(list(indexes)), subtypes, blocklist)


# ------------------------------------------------------------------- synonyms (req 1)
def test_confirmed_mapping_adds_external_synonyms():
    out = _enrich(_disease(), [{"db": "mondo", "ids": ["0005147"]}])
    # the external term's label + synonyms fold in; the disease's own name does not
    assert "juvenile diabetes" in out["synonyms"]
    assert "insulin dependent diabetes" in out["synonyms"]
    assert "type 1 diabetes mellitus" in out["synonyms"]


def test_existing_synonyms_and_label_are_not_duplicated():
    out = _enrich(_disease(name="juvenile diabetes", synonyms=["Insulin-dependent diabetes"]),
                  [{"db": "mondo", "ids": ["0005147"]}])
    # label match (normalized) and existing synonym (case/punct-normalized) are skipped
    assert "juvenile diabetes" not in out["synonyms"]
    assert not any(ps.normalize(s) == ps.normalize("insulin dependent diabetes")
                   for s in out["synonyms"])


def test_blocklisted_synonyms_are_dropped():
    bl = {"ARI:0000001": {ps.normalize("juvenile diabetes")}}
    out = _enrich(_disease(), [{"db": "mondo", "ids": ["0005147"]}], blocklist=bl)
    assert "juvenile diabetes" not in out["synonyms"]
    assert "insulin dependent diabetes" in out["synonyms"]


def test_synonyms_resolved_via_a_cross_referenced_id():
    # confirming the SNOMED id (which has no index of its own) still reaches the
    # MONDO record that cross-references it, so its synonyms fold in
    out = _enrich(_disease(), [{"db": "snomed", "ids": ["46635009"]}])
    assert "juvenile diabetes" in out["synonyms"]


# ------------------------------------------------------------------- subtypes (req 2)
def test_confirmed_mapping_adds_direct_children_as_subtypes():
    out = _enrich(_disease(), [{"db": "mondo", "ids": ["0005147"]}])
    joined = " ".join(out["subtypes"])
    assert "type 1 diabetes mellitus 2" in joined
    assert "MONDO:0011899" in joined       # provenance CURIE is recorded
    assert len(out["subtypes"]) == 2


def test_existing_subtypes_are_not_duplicated():
    out = _enrich(_disease(clinical_subtypes=["type 1 diabetes mellitus 2 - already here"]),
                  [{"db": "mondo", "ids": ["0005147"]}])
    assert not any("type 1 diabetes mellitus 2 " in s for s in out["subtypes"])
    assert len(out["subtypes"]) == 1       # only the second child remains


def test_disease_is_never_proposed_as_its_own_subtype():
    # A confirmed mapping to a broader concept lists the disease itself among that
    # concept's children; it must not come back as a subtype of itself.
    subs = {"MONDO:0005147": [{"id": "MONDO:1", "label": "juvenile diabetes"},
                              {"id": "MONDO:2", "label": "real subtype"}]}
    out = _enrich(_disease(name="Juvenile diabetes"), [{"db": "mondo", "ids": ["0005147"]}],
                  subtypes=subs)
    assert out["subtypes"] == ["real subtype - subtype of Juvenile diabetes (MONDO:2)"]


def test_no_subtypes_when_no_hierarchy_loaded():
    out = _enrich(_disease(), [{"db": "mondo", "ids": ["0005147"]}], subtypes={})
    assert out["subtypes"] == []
    assert out["synonyms"]                 # synonyms still work without a subtype file


# -------------------------------------------------------------------- ambiguity
# A coarse cross-reference (one ICD-10 code shared by many diseases) must not pull
# every one of those diseases' names into a single synonym list. Real data has
# ICD-10 codes xreffed by 100+ DOID terms, so this is the main safety rule.
COARSE = _index(
    ("MONDO:1", "deafness alpha", [], {"mondo": ["1"], "icd10": ["H90.3"]}),
    ("MONDO:2", "deafness beta", [], {"mondo": ["2"], "icd10": ["H90.3"]}),
)


def test_ambiguous_id_within_a_source_contributes_nothing():
    out = _enrich(_disease(), [{"db": "icd10", "ids": ["H90.3"]}], indexes=(COARSE,), subtypes={})
    assert out["synonyms"] == []


def test_unambiguous_id_in_the_same_source_still_resolves():
    out = _enrich(_disease(), [{"db": "mondo", "ids": ["1"]}], indexes=(COARSE,), subtypes={})
    assert out["synonyms"] == ["deafness alpha"]


def test_agreeing_sources_are_both_kept():
    # the same SNOMED id pins exactly one record in each of two sources -> agreement,
    # not ambiguity, so synonyms from both fold in
    other = _index(("DOID:9744", "T1DM", ["sugar diabetes"], {"doid": ["9744"], "snomed": ["46635009"]}))
    out = _enrich(_disease(), [{"db": "snomed", "ids": ["46635009"]}],
                  indexes=(HUB, other), subtypes={})
    assert "juvenile diabetes" in out["synonyms"]     # from MONDO
    assert "sugar diabetes" in out["synonyms"]        # from DOID


# ---------------------------------------------------------------------- batch
def test_enrich_many_skips_diseases_without_confirmations():
    diseases = [_disease(), {"iri": "iri:other", "ari_id": "ARI:0000002",
                             "name": "Other", "synonyms": [], "clinical_subtypes": []}]
    out = es.enrich_many(diseases, {"iri:t1d": [{"db": "mondo", "ids": ["0005147"]}]},
                         indexes=[HUB], subtypes=SUBTYPES, blocklist={})
    assert set(out) == {"iri:t1d"}


# --------------------------------------------------------------- subtype file loading
def test_load_subtypes_resolves_child_labels_from_the_indexes(tmp_path):
    # The edge file carries ids only; the name of each child comes from that child's
    # own index row, so the two can never disagree about a label. A child the indexes
    # do not know resolves to "" -- which enrich() already skips -- rather than
    # silently proposing a subtype under a name nothing else in the app recognises.
    (tmp_path / "mondo.subtypes.tsv").write_text(
        "	".join(es.SUBTYPE_COLS) + "\n"
        "MONDO:0005147	MONDO:0011899\n"
        "MONDO:0005147	MONDO:0099999\n", encoding="utf-8")
    idx = _index(("MONDO:0011899", "type 1 diabetes mellitus 2", [], {"mondo": ["0011899"]}))

    got = es.load_subtypes([idx], tmp_path)
    assert got == {"MONDO:0005147": [{"id": "MONDO:0011899", "label": "type 1 diabetes mellitus 2"},
                                     {"id": "MONDO:0099999", "label": ""}]}

