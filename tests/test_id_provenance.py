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


def test_id_authors_endpoint_serves_the_ledger(ledger):
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "ada")
    r = client.get("/api/v2/id-authors")
    assert r.status_code == 200
    assert r.json() == {"iri:1|snomed|1": "ada"}


def test_publish_refuses_confirming_your_own_id(ledger, monkeypatch):
    ledger.record("iri:1", {}, {"snomed": ["1"]}, "ada")
    monkeypatch.setattr(main, "GH_ENABLED", True)
    monkeypatch.setattr(main, "_user", lambda request: {"token": "t", "identity": {"login": "ada"}})
    r = client.post("/api/v2/publish", json={
        "confirmed": [{"ari_id": "ARI:1", "iri": "iri:1", "name": "D", "db": "snomed", "ids": ["1"]}]})
    assert r.status_code == 400
    assert "another curator must confirm it" in r.json()["detail"]


def test_backfill_reads_authors_out_of_the_sssom_file(tmp_path, base_owl, ledger):
    """The curated SSSOM names who entered each mapping, which is what seeds the
    ledger for ids that predate it (scripts/backfill_id_authors.py)."""
    from scripts.backfill_id_authors import backfill

    sssom = tmp_path / "ari.sssom.tsv"
    sssom.write_text(
        "subject_id\tsubject_label\tpredicate_id\tpredicate_modifier\tobject_id\t"
        "mapping_justification\tauthor_id\tmapping_date\n"
        # Adult onset Still's disease, an id the curated ontology really carries.
        "ARI:0001008\tD\tskos:exactMatch\t\tumls:C0085253\tsemapv:ManualMappingCuration\t"
        "github:KrishnaTO\t2026-06-21\n"
        # A negative names who flagged it, not who added it — skipped.
        "ARI:0001008\tD\tskos:exactMatch\tNot\tDOID:99999\tsemapv:ManualMappingCuration\t"
        "github:someone\t2026-06-21\n"
        # An ORCID cannot be matched against a GitHub login — skipped.
        "ARI:0001008\tD\tskos:exactMatch\t\tDOID:14256\tsemapv:ManualMappingCuration\t"
        "orcid:0000-0000-0000-0000\t2026-06-21\n", encoding="utf-8")

    out = backfill(sssom, base_owl, ledger)
    assert out["recorded"] == 1
    assert out["skipped"]["negative"] == 1 and out["skipped"]["author"] == 1
    assert list(ledger.authors().values()) == ["KrishnaTO"]
    assert list(ledger.authors())[0].endswith("|umls|C0085253")

    # Re-running keeps the first author rather than rewriting the ledger.
    assert backfill(sssom, base_owl, ledger)["recorded"] == 0
