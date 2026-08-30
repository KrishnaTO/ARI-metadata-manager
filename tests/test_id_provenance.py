"""Separation of duties on the reference-review page.

Whoever adds a cross-reference id may not also confirm the mapping it stands
for, so the server records who added each id and refuses a self-confirmation at
the publish boundary.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import id_provenance

client = TestClient(main.app)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    store = id_provenance.IdAuthorStore(tmp_path / "provenance")
    monkeypatch.setattr(main, "ID_AUTHORS", store)
    return store


def test_records_only_the_ids_an_edit_introduced(ledger):
    ledger.record("iri:1", {"snomed": ["1"]}, {"snomed": ["1", "2"], "mondo": ["9"]}, "ada")
    assert ledger.authors() == {"iri:1|snomed|2": "ada", "iri:1|mondo|9": "ada"}


def test_first_author_keeps_the_id(ledger):
    """Re-adding an id someone else added must not move authorship — otherwise a
    curator could unlock their own ✓ by removing and re-entering the id."""
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "ada")
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "bo")
    assert ledger.authors()["iri:1|snomed|1"] == "ada"


def test_anonymous_edits_record_nothing(ledger):
    assert ledger.record("iri:1", {}, {"snomed": ["1"]}, None) == 0
    assert ledger.authors() == {}


def test_id_authors_endpoint_serves_the_ledger(ledger, monkeypatch):
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "ada")
    monkeypatch.setattr(main, "_login", lambda request: "ada")
    r = client.get("/api/v2/id-authors")
    assert r.status_code == 200
    assert r.json() == {"iri:1|snomed|1": "ada"}


def test_id_authors_endpoint_needs_a_session(ledger):
    """The ledger is the evidence base for separation of duties, so it is not
    handed to anonymous callers."""
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "ada")
    assert client.get("/api/v2/id-authors").status_code == 401


def test_publish_refuses_confirming_your_own_id(ledger, monkeypatch):
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "ada")
    monkeypatch.setattr(main, "GH_ENABLED", True)
    monkeypatch.setattr(main, "_user", lambda request: {"token": "t", "identity": {"login": "ada"}})
    r = client.post("/api/v2/publish", json={
        "confirmed": [{"ari_id": "ARI:1", "iri": "iri:1", "name": "D", "db": "snomed", "ids": ["1"]}]})
    assert r.status_code == 400
    assert "another curator must confirm it" in r.json()["detail"]


# Both mapping files name a curator per mapping; ids are ones the curated
# ontology really carries for Adult onset Still's disease (ARI:0001008).
SSSOM = ("subject_id\tsubject_label\tpredicate_id\tpredicate_modifier\tobject_id\t"
         "mapping_justification\tauthor_id\tmapping_date\n"
         "ARI:0001008\tD\tskos:exactMatch\t\tumls:C0085253\tsemapv:ManualMappingCuration\t"
         "github:KrishnaTO\t2026-06-21\n"
         # A negative names who flagged it, not who added it — skipped.
         "ARI:0001008\tD\tskos:exactMatch\tNot\tDOID:99999\tsemapv:ManualMappingCuration\t"
         "github:someone\t2026-06-21\n"
         # An ORCID cannot be matched against a GitHub login — skipped.
         "ARI:0001008\tD\tskos:exactMatch\t\tmesh:D016706\tsemapv:ManualMappingCuration\t"
         "orcid:0000-0000-0000-0000\t2026-06-21\n")

EQUIV = ("source_prefix\tsource_id\tsource_name\trelation\ttarget_prefix\ttarget_id\ttype\tsource\n"
         # The same mapping the SSSOM carries — must not be double-counted.
         "ARI\t1008\tD\tskos:exactMatch\tumls\tC0085253\tmanual\tgithub:KrishnaTO\n"
         # Only in the equivalencies file, by a different curator.
         "ARI\t1008\tD\tskos:exactMatch\tDOID\t14256\tmanual\tgithub:AnjaliRH\n"
         "ARI\t1008\tD\tskos:exactMatch\ticd10cm\tM06.1\tmanual-negative\tgithub:someone\n")


def test_backfill_reads_authors_out_of_both_mapping_files(base_owl, ledger):
    """Both curated files name who entered each mapping, and neither is a superset
    of the other, so both seed the ledger (scripts/backfill_id_authors.py)."""
    from scripts.backfill_id_authors import backfill

    out = backfill(SSSOM, EQUIV, base_owl, ledger)
    authors = ledger.authors()
    assert out["recorded"] == 2                       # one per file, deduped across them
    assert out["skipped"]["negative"] == 2 and out["skipped"]["author"] == 1
    assert authors == {
        "https://diseases.autoimmuneregistry.org/disease/ARI_0001008|umls|C0085253": "KrishnaTO",
        "https://diseases.autoimmuneregistry.org/disease/ARI_0001008|doid|14256": "AnjaliRH",
    }

    # Re-running keeps each id's first author rather than rewriting the ledger.
    assert backfill(SSSOM, EQUIV, base_owl, ledger)["recorded"] == 0


def test_backfill_skips_ids_no_longer_on_the_disease(base_owl, ledger):
    """A mapping whose id has since been edited away must not leave a stale key —
    nobody can confirm an id the disease does not carry."""
    from scripts.backfill_id_authors import backfill

    gone = ("subject_id\tsubject_label\tpredicate_id\tpredicate_modifier\tobject_id\t"
            "mapping_justification\tauthor_id\tmapping_date\n"
            "ARI:0001008\tD\tskos:exactMatch\t\tumls:C9999999\tsemapv:ManualMappingCuration\t"
            "github:KrishnaTO\t2026-06-21\n")
    out = backfill(gone, "", base_owl, ledger)
    assert out["recorded"] == 0 and out["skipped"]["id"] == 1
    assert ledger.authors() == {}


def test_ontology_service_over_a_temp_file_creates_no_sibling_dirs(tmp_path, base_owl):
    """A read-only service built over a downloaded ontology must not touch the disk.

    OntologyService derives its feedback store from the ontology file's *grandparent*
    directory. The publish change summary, the export baseline and the id-authorship
    backfill all build one over a file in the system temp directory, whose grandparent
    is the filesystem root — so creating that store eagerly meant `mkdir /feedback`
    and a PermissionError on Linux (silently swallowed in the two endpoints, which is
    why every production PR body read "Change summary unavailable").
    """
    import shutil

    from app.ontology_service import OntologyService

    # Mimic the temp-file layout: the ontology sits directly in a directory whose
    # own parent must not be written to.
    root = tmp_path / "readonly"
    holder = root / "holder"
    holder.mkdir(parents=True)
    owl = holder / "ari_t1d.owl"
    shutil.copyfile(base_owl, owl)

    svc = OntologyService(str(owl))
    assert svc.get_xref_rows()                       # usable for reading
    assert not (root / "feedback").exists()          # nothing created alongside it
