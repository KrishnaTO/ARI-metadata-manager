"""Coverage for the index builders in scripts/fetch_databases.py.

Each parser is exercised on a tiny inline fixture written to a temp file, so these
tests need no network and no multi-hundred-MB downloads. They pin the contracts the
predicted-match quality depends on: EXACT-only synonyms, NCIt disease filtering +
UMLS-CUI harvesting, MeSH tree/preferred-concept filtering, and Orphanet
exact-mapping-only cross-references.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("fetch_databases", ROOT / "scripts" / "fetch_databases.py")
fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fd)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------------- OBO
def test_parse_obo_keeps_exact_synonyms_and_xrefs(tmp_path):
    obo = _write(tmp_path, "x.obo", '''\
[Term]
id: MONDO:0005147
name: type 1 diabetes mellitus
synonym: "juvenile diabetes" EXACT []
synonym: "T1D" EXACT ABBREVIATION []
synonym: "sugar" RELATED []
xref: SCTID:46635009 {source="MONDO:equivalentTo"}
xref: OMIM:222100 {source="x"}

[Term]
id: MONDO:9999999
name: obsolete thing
is_obsolete: true
''')
    rows = {r["id"]: r for r in fd.parse_obo(obo, "MONDO")}
    assert "MONDO:9999999" not in rows                   # obsolete dropped
    r = rows["MONDO:0005147"]
    assert r["synonyms"] == ["juvenile diabetes"]        # ABBREVIATION + RELATED excluded
    assert r["snomed"] == ["46635009"] and r["omim"] == ["222100"]
    assert r["mondo"] == ["0005147"]                     # own id in own column


def test_parse_obo_disease_only_filters_by_semantic_type_and_harvests_umls(tmp_path):
    obo = _write(tmp_path, "ncit.obo", '''\
[Term]
id: NCIT:C2986
name: Type 1 Diabetes Mellitus
property_value: NCIT:P106 "Disease or Syndrome" xsd:string
property_value: NCIT:P207 "C0011854" xsd:string

[Term]
id: NCIT:C12345
name: Some Gene
property_value: NCIT:P106 "Gene or Genome" xsd:string
''')
    rows = {r["id"]: r for r in fd.parse_obo(obo, "NCIT", disease_only=True)}
    assert set(rows) == {"NCIT:C2986"}                   # non-disease semantic type dropped
    assert rows["NCIT:C2986"]["umls"] == ["C0011854"]    # P207 -> umls xref
    assert rows["NCIT:C2986"]["nci"] == ["C2986"]


# ------------------------------------------------------------------------ MeSH
def test_parse_mesh_xml_disease_tree_and_preferred_concept(tmp_path):
    xml = _write(tmp_path, "mesh.xml", '''\
<?xml version="1.0"?>
<DescriptorRecordSet>
  <DescriptorRecord>
    <DescriptorUI>D003922</DescriptorUI>
    <DescriptorName><String>Diabetes Mellitus, Type 1</String></DescriptorName>
    <TreeNumberList><TreeNumber>C18.452.394.750.124</TreeNumber></TreeNumberList>
    <ConceptList>
      <Concept PreferredConceptYN="Y">
        <TermList>
          <Term><String>Diabetes Mellitus, Type 1</String></Term>
          <Term><String>Type 1 Diabetes</String></Term>
        </TermList>
      </Concept>
      <Concept PreferredConceptYN="N">
        <TermList><Term><String>Brittle Diabetes</String></Term></TermList>
      </Concept>
    </ConceptList>
  </DescriptorRecord>
  <DescriptorRecord>
    <DescriptorUI>D000900</DescriptorUI>
    <DescriptorName><String>Anti-Bacterial Agents</String></DescriptorName>
    <TreeNumberList><TreeNumber>D27.505.954.122</TreeNumber></TreeNumberList>
    <ConceptList><Concept PreferredConceptYN="Y"><TermList>
      <Term><String>Antibiotics</String></Term></TermList></Concept></ConceptList>
  </DescriptorRecord>
</DescriptorRecordSet>
''')
    rows = {r["id"]: r for r in fd.parse_mesh_xml(xml)}
    assert set(rows) == {"MESH:D003922"}                 # D27 (drugs) excluded, C18 kept
    r = rows["MESH:D003922"]
    assert r["mesh"] == ["D003922"]
    assert "Type 1 Diabetes" in r["synonyms"]
    assert "Brittle Diabetes" not in r["synonyms"]       # non-preferred concept excluded


# --------------------------------------------------------------------- Orphanet
def test_parse_orphanet_xml_exact_refs_only(tmp_path):
    xml = _write(tmp_path, "orpha.xml", '''\
<?xml version="1.0"?>
<JDBOR><DisorderList>
  <Disorder id="1">
    <OrphaCode>98757</OrphaCode>
    <Name lang="en">Systemic sclerosis</Name>
    <SynonymList><Synonym lang="en">Scleroderma</Synonym></SynonymList>
    <ExternalReferenceList>
      <ExternalReference><Source>OMIM</Source><Reference>181750</Reference>
        <DisorderMappingRelation><Name lang="en">E (exact mapping)</Name></DisorderMappingRelation></ExternalReference>
      <ExternalReference><Source>ICD-10</Source><Reference>M34.9</Reference>
        <DisorderMappingRelation><Name lang="en">NTBT (narrower)</Name></DisorderMappingRelation></ExternalReference>
      <ExternalReference><Source>UMLS</Source><Reference>C0036421</Reference>
        <DisorderMappingRelation><Name lang="en">E (exact mapping)</Name></DisorderMappingRelation></ExternalReference>
    </ExternalReferenceList>
  </Disorder>
</DisorderList></JDBOR>
''')
    rows = {r["id"]: r for r in fd.parse_orphanet_xml(xml)}
    r = rows["ORPHA:98757"]
    assert r["orphanet"] == ["98757"] and r["synonyms"] == ["Scleroderma"]
    assert r["omim"] == ["181750"] and r["umls"] == ["C0036421"]   # exact refs kept
    assert "icd10" not in r                                          # narrower ref dropped


def test_index_columns_match_predict_service():
    # INDEX_COLS is the shared column *vocabulary*: the writer emits a subset of it
    # (see test_write_index_omits_columns_this_source_never_fills) and the reader
    # looks columns up by name, so the two lists must still agree on names and order.
    from app import predict_service as ps
    assert fd.INDEX_COLS == ps.INDEX_COLS


def test_write_index_omits_columns_this_source_never_fills(tmp_path, monkeypatch):
    # Every source leaves most of the ten target columns empty, and an always-empty
    # column is a tab per row and nothing else. predict_service reads by name, so an
    # absent column is simply a database this source cross-references nothing in.
    monkeypatch.setattr(fd, "DATA_DIR", tmp_path)
    rows = [{"id": "MESH:D1", "label": "a", "synonyms": [], "mesh": ["D1"]},
            {"id": "MESH:D2", "label": "b", "synonyms": [], "mesh": ["D2"]}]
    lines = fd.write_index("mesh", rows).read_text(encoding="utf-8").splitlines()
    assert lines[0].split("\t") == fd.INDEX_COLS[:3] + ["mesh"]
    assert lines[1] == "MESH:D1\ta\t\tD1"


def test_write_index_drops_synonyms_that_only_echo_the_label(tmp_path, monkeypatch):
    # NCIt repeats the term's own name as its first synonym on nearly every row.
    # Both the matcher and the enrichment engine walk [label] + synonyms and keep the
    # first spelling of each key, so an echo can never match anything the label does
    # not already match. Punctuation/accent variants are different names and stay.
    monkeypatch.setattr(fd, "DATA_DIR", tmp_path)
    rows = [{"id": "NCIT:C1", "label": "Vessel Disease", "synonyms": [
        "Vessel Disease", "VESSEL DISEASE", "Sjögren Disease", "Sjogren Disease"]}]
    lines = fd.write_index("ncit", rows).read_text(encoding="utf-8").splitlines()
    assert lines[1].split("\t")[2] == "Sjögren Disease | Sjogren Disease"


# ------------------------------------------------------------------- hierarchy
_HIER_OBO = '''\
[Term]
id: MONDO:0000001
name: root disease

[Term]
id: MONDO:0005147
name: type 1 diabetes mellitus
is_a: MONDO:0000001 ! root disease

[Term]
id: MONDO:0011899
name: type 1 diabetes mellitus 2
is_a: MONDO:0005147 {source="DOID:10493", source="MONDO:Inferred"} ! type 1 diabetes

[Term]
id: MONDO:0099999
name: obsolete thing
is_a: MONDO:0005147 ! type 1 diabetes
is_obsolete: true
'''


def test_parse_obo_keeps_parent_ids_alongside_resolved_labels(tmp_path):
    # ``parents`` is resolved to labels for the human-facing details sidecar, but the
    # raw ids must survive as ``parent_ids`` — a label does not identify a term, so
    # write_subtypes cannot invert the hierarchy without them. The second parent here
    # also carries an OBO trailing qualifier, which must not end up in the id.
    rows = {r["id"]: r for r in fd.parse_obo(_write(tmp_path, "h.obo", _HIER_OBO), "MONDO")}
    assert rows["MONDO:0011899"]["parent_ids"] == ["MONDO:0005147"]   # qualifier stripped
    assert rows["MONDO:0011899"]["parents"] == ["type 1 diabetes mellitus"]
    assert rows["MONDO:0005147"]["parent_ids"] == ["MONDO:0000001"]
    assert "MONDO:0099999" not in rows                                # obsolete dropped


def test_write_subtypes_inverts_edges_and_skips_pruned_parents(tmp_path, monkeypatch):
    monkeypatch.setattr(fd, "DATA_DIR", tmp_path)
    rows = [
        {"id": "MONDO:1", "label": "parent", "parent_ids": []},
        {"id": "MONDO:2", "label": "child a", "parent_ids": ["MONDO:1"]},
        {"id": "MONDO:3", "label": "child b", "parent_ids": ["MONDO:1", "MONDO:404"]},
    ]
    out, n = fd.write_subtypes("mondo", rows)
    assert n == 2                                    # MONDO:404 is not a kept term
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].split("\t") == fd.SUBTYPE_COLS
    assert lines[1:] == ["MONDO:1\tMONDO:2\tchild a", "MONDO:1\tMONDO:3\tchild b"]


def test_subtype_columns_match_enrich_service():
    # The writer must emit exactly the columns the enrichment engine reads.
    from app import enrich_service as es
    assert fd.SUBTYPE_COLS == es.SUBTYPE_COLS
