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
    # The writer must emit exactly the columns predict_service reads.
    from app import predict_service as ps
    assert fd.INDEX_COLS == ps.INDEX_COLS
