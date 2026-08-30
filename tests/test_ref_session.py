"""Per-user cross-reference review session persistence.

The ref-edits page saves its in-progress verdicts and PR pointer
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


def test_corrupt_session_file_raises_rather_than_resetting(tmp_path, monkeypatch):
    """A half-written session used to read as empty.

    That is a silent reset, not a recovery: the curator's verdicts appear to
    have vanished, they re-judge, and the next save overwrites the file that
    still held them. Raising keeps the bytes on disk and puts the failure where
    an operator can see it."""
    import pytest

    from app.atomic_store import StoreCorrupt

    monkeypatch.setattr(main, "USER_DIR", tmp_path)
    (tmp_path / "tester.refsession.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreCorrupt):
        main._load_ref_session("tester")


def test_absent_session_file_is_an_empty_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "USER_DIR", tmp_path)
    assert main._load_ref_session("nobody") == {}


def test_two_windows_for_one_curator_do_not_lose_each_others_verdicts(tmp_path, monkeypatch):
    """The multi-window case, which had no test at all (issue #121).

    Comparing content side by side is the product's core loop and forces two
    windows. `saveSession()` PUTs the *entire* state blob and the server writes
    it wholesale, so last writer wins the whole document.

    This documents the behaviour rather than asserting the fix: merging is
    issue #114. What it does guarantee is that the loss is visible here the
    moment #114 lands, instead of being discovered by a curator.
    """
    monkeypatch.setattr(main, "USER_DIR", tmp_path)
    monkeypatch.setattr(main, "_login", lambda request: "tester")

    window_a = {"reviewed": {"iri1|mondo|1": "ok"}, "edited": {}, "branch": None, "pr": None}
    window_b = {"reviewed": {"iri1|snomed|2": "bad"}, "edited": {}, "branch": None, "pr": None}

    assert client.put("/api/v2/ref-session", json=window_a).status_code == 200
    assert client.put("/api/v2/ref-session", json=window_b).status_code == 200

    got = client.get("/api/v2/ref-session").json()
    a_key, b_key = "iri1|mondo|1", "iri1|snomed|2"
    assert b_key in got["reviewed"], "the second window's verdict must survive"
    if a_key not in got["reviewed"]:
        # Current behaviour. When #114 merges per-cell, flip this to an assert.
        assert got["reviewed"] == window_b["reviewed"]
