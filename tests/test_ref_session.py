"""Per-user cross-reference review session persistence.

The ref-edits / ref-curate pages save their in-progress verdicts and PR pointer
to the server so a signed-in user can resume after a page reload. These guard the
GET/PUT endpoints and the auth boundary (anonymous users get no persistence)."""
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)

SAMPLE = {
    "reviewed": {"iri1|mondo|123": "ok", "iri1|umls|C1": "bad"},
    "edited": {"iri1|mondo|123": True},
    "branch": "edit/tester/mappings-review-1",
    "pr": {"number": 7, "url": "https://example/pr/7", "fork": False},
}


def test_anonymous_get_is_empty():
    # No signed-in user -> nothing persisted, empty blob (not a 401).
    r = client.get("/api/v2/ref-session")
    assert r.status_code == 200
    assert r.json() == {}


def test_anonymous_put_is_rejected():
    r = client.put("/api/v2/ref-session", json=SAMPLE)
    assert r.status_code == 401


def test_signed_in_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "USER_DIR", tmp_path)
    monkeypatch.setattr(main, "_login", lambda request: "tester")

    # Save, then read it straight back.
    assert client.put("/api/v2/ref-session", json=SAMPLE).status_code == 200
    got = client.get("/api/v2/ref-session")
    assert got.status_code == 200
    assert got.json() == SAMPLE

    # Resetting the user (branch switch / fetch) drops the saved session.
    main._reset_user("tester")
    assert client.get("/api/v2/ref-session").json() == {}


def test_corrupt_session_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "USER_DIR", tmp_path)
    (tmp_path / "tester.refsession.json").write_text("{not json", encoding="utf-8")
    assert main._load_ref_session("tester") == {}
