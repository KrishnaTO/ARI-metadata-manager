"""Report 9 (the synonym vs subtype review), read from the ARI repo."""
import pytest
from fastapi.testclient import TestClient

import app.config as config
import app.github_service as gh
import app.main as main
from app import sessions, synonym_review, synonym_review_store

client = TestClient(main.app)

HEADER = "\t".join(synonym_review.COLUMNS)
ROW = ("ARI:0001001\tAcquired epidermolysis bullosa\tEBA\tARI_Synonym\tsynonym\tkeep\t"
       "database evidence\texact: MONDO:0018747\t\t\t2026-10-08")


def test_parse_returns_one_dict_per_row():
    rows = synonym_review.parse(HEADER + "\n" + ROW + "\n")
    assert rows == [dict(zip(synonym_review.COLUMNS, ROW.split("\t")))]


def test_parse_rejects_an_unexpected_header():
    with pytest.raises(ValueError):
        synonym_review.parse("ari_id\tdisease\tterm\n")


def test_endpoint_reads_the_report_from_the_base_branch(monkeypatch):
    monkeypatch.setattr(config, "GH_OWNER", "KrishnaTO")
    monkeypatch.setattr(config, "GH_REPO", "ARI")
    seen = {}

    async def _file(token, owner, repo, path, ref):
        seen.update(owner=owner, repo=repo, path=path, ref=ref)
        return (HEADER + "\n" + ROW + "\n").encode()

    monkeypatch.setattr(gh, "get_file_at", _file)
    r = client.get("/api/v2/synonym-review")
    assert r.status_code == 200
    assert r.json()["rows"][0]["term"] == "EBA"
    assert seen == {"owner": "KrishnaTO", "repo": "ARI", "path": config.SYNONYM_REVIEW_PATH,
                    "ref": config.GH_BASE_BRANCH}


def test_endpoint_reports_a_github_failure_as_502(monkeypatch):
    monkeypatch.setattr(config, "GH_OWNER", "KrishnaTO")
    monkeypatch.setattr(config, "GH_REPO", "ARI")

    async def _fail(*a):
        raise ValueError("Could not fetch: 404")

    monkeypatch.setattr(gh, "get_file_at", _fail)
    assert client.get("/api/v2/synonym-review").status_code == 502


def test_endpoint_requires_the_repo_to_be_configured(monkeypatch):
    monkeypatch.setattr(config, "GH_OWNER", "")
    assert client.get("/api/v2/synonym-review").status_code == 503


ROW_DICT = dict(zip(synonym_review.COLUMNS, ROW.split("\t")))


@pytest.fixture
def curated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SYNONYM_REVIEW_DIR", tmp_path / "synonym-review")
    monkeypatch.setattr(sessions, "_login", lambda request: "curator")
    return tmp_path / "synonym-review" / "curation.json"


def test_curation_needs_sign_in():
    r = client.put("/api/v2/synonym-review/curation", json={"row": ROW_DICT, "status": "correct"})
    assert r.status_code == 401


def test_marking_a_row_writes_the_curation_folder(curated):
    r = client.put("/api/v2/synonym-review/curation",
                   json={"row": ROW_DICT, "status": "incorrect", "note": " really a subtype "})
    assert r.status_code == 200
    entry = synonym_review_store.read()["ARI:0001001|EBA"]
    assert curated.exists()
    assert entry["status"] == "incorrect" and entry["note"] == "really a subtype"
    assert entry["by"] == "curator" and entry["verdict"] == "synonym" and entry["action"] == "keep"


def test_clearing_status_and_note_removes_the_entry(curated):
    client.put("/api/v2/synonym-review/curation", json={"row": ROW_DICT, "status": "correct"})
    client.put("/api/v2/synonym-review/curation", json={"row": ROW_DICT, "status": "", "note": ""})
    assert synonym_review_store.read() == {}


def test_a_note_alone_is_kept_without_a_status(curated):
    client.put("/api/v2/synonym-review/curation", json={"row": ROW_DICT, "note": "check MONDO"})
    assert synonym_review_store.read()["ARI:0001001|EBA"]["status"] == ""


def test_curation_rejects_an_unknown_status_and_an_incomplete_row(curated):
    assert client.put("/api/v2/synonym-review/curation",
                      json={"row": ROW_DICT, "status": "maybe"}).status_code == 400
    assert client.put("/api/v2/synonym-review/curation",
                      json={"row": {"ari_id": "ARI:0001001"}, "status": "correct"}).status_code == 400


def test_report_endpoint_returns_the_curation(curated, monkeypatch):
    monkeypatch.setattr(config, "GH_OWNER", "KrishnaTO")
    monkeypatch.setattr(config, "GH_REPO", "ARI")

    async def _file(*a):
        return (HEADER + "\n" + ROW + "\n").encode()

    monkeypatch.setattr(gh, "get_file_at", _file)
    client.put("/api/v2/synonym-review/curation", json={"row": ROW_DICT, "status": "needs-review"})
    d = client.get("/api/v2/synonym-review").json()
    assert d["curation"]["ARI:0001001|EBA"]["status"] == "needs-review"
    assert d["login"] == "curator"
