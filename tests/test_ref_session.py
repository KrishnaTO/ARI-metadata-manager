"""Per-user cross-reference review session persistence.

The ref-edits page saves its in-progress verdicts and PR pointer
to the server so a signed-in user can resume after a page reload. These guard the
GET/PUT endpoints and the auth boundary (anonymous users get no persistence)."""
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions, workspace

client = TestClient(main.app)

# A window sends only what it changed, so the body is a patch. `null` clears a
# key — the one thing a plain merge cannot express.
SAMPLE = {
    "patch": {
        "reviewed": {"iri1|mondo|123": "ok", "iri1|umls|C1": "bad"},
        "edited": {"iri1|mondo|123": True},
    },
    "branch": "edit/tester/mappings-review-1",
    "pr": {"number": 7, "url": "https://example/pr/7", "fork": False},
}

STORED = {
    "reviewed": {"iri1|mondo|123": "ok", "iri1|umls|C1": "bad"},
    "edited": {"iri1|mondo|123": True},
    "published": {},
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
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    monkeypatch.setattr(sessions, "_login", lambda request: "tester")

    # Save, then read it straight back.
    assert client.put("/api/v2/ref-session", json=SAMPLE).status_code == 200
    got = client.get("/api/v2/ref-session")
    assert got.status_code == 200
    assert got.json() == STORED

    # Resetting the user (branch switch / fetch) drops the saved session.
    workspace._reset_user("tester")
    assert client.get("/api/v2/ref-session").json() == {}


def test_forgetting_one_disease_leaves_the_rest_of_the_session(tmp_path, monkeypatch):
    """The narrow way out of a publish conflict.

    Taking the source branch's version of a disease has to take its verdicts and
    its touched marker with it, and nothing else: fetching the branch again —
    which drops the whole session — is what made one collision cost an afternoon
    of unrelated review."""
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    collided, kept = "http://x/collided", "http://x/kept"
    workspace.mark("tester", collided, kept)
    workspace._save_ref_session("tester", {
        "reviewed": {f"{collided}|mondo|1": "ok", f"{kept}|mondo|2": "ok"},
        "edited": {f"{collided}|umls|C1": True},
        "published": {},
        "pr": {"number": 7},
    })

    workspace.forget("tester", collided)

    assert workspace.touched("tester") == {kept}
    session = workspace._load_ref_session("tester")
    assert session["reviewed"] == {f"{kept}|mondo|2": "ok"}
    assert session["edited"] == {}
    assert session["pr"] == {"number": 7}      # the PR this work goes into is unchanged


def test_corrupt_session_file_raises_rather_than_resetting(tmp_path, monkeypatch):
    """A half-written session used to read as empty.

    That is a silent reset, not a recovery: the curator's verdicts appear to
    have vanished, they re-judge, and the next save overwrites the file that
    still held them. Raising keeps the bytes on disk and puts the failure where
    an operator can see it."""
    import pytest

    from app.atomic_store import StoreCorrupt

    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    (tmp_path / "tester.refsession.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreCorrupt):
        workspace._load_ref_session("tester")


def test_absent_session_file_is_an_empty_blob(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    assert workspace._load_ref_session("nobody") == {}


def test_two_windows_for_one_curator_do_not_lose_each_others_verdicts(tmp_path, monkeypatch):
    """The multi-window case (issues #121, #114).

    Comparing content side by side is the product's core loop and forces two
    windows. The page used to PUT the *entire* state blob and the server wrote it
    wholesale, so the last writer replaced the whole document and the other
    window's verdicts were gone on the next reload. Each window now sends only
    what it changed, and the writes interleave.
    """
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    monkeypatch.setattr(sessions, "_login", lambda request: "tester")

    window_a = {"patch": {"reviewed": {"iri1|mondo|1": "ok"}}}
    window_b = {"patch": {"reviewed": {"iri1|snomed|2": "bad"}}}

    assert client.put("/api/v2/ref-session", json=window_a).status_code == 200
    assert client.put("/api/v2/ref-session", json=window_b).status_code == 200

    reviewed = client.get("/api/v2/ref-session").json()["reviewed"]
    assert reviewed == {"iri1|mondo|1": "ok", "iri1|snomed|2": "bad"}


def test_interleaved_writes_from_two_windows_all_survive(tmp_path, monkeypatch):
    """Ten alternating saves, as two windows working the matrix side by side."""
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    monkeypatch.setattr(sessions, "_login", lambda request: "tester")

    expected = {}
    for i in range(5):
        for window, db in (("a", "mondo"), ("b", "snomed")):
            key = f"iri{i}|{db}|{window}"
            expected[key] = "ok"
            assert client.put("/api/v2/ref-session",
                              json={"patch": {"reviewed": {key: "ok"}}}).status_code == 200

    assert client.get("/api/v2/ref-session").json()["reviewed"] == expected


def test_a_cleared_verdict_is_removed_not_merged_back(tmp_path, monkeypatch):
    """Un-judging a cell has to survive the merge, or a verdict can never be undone."""
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    monkeypatch.setattr(sessions, "_login", lambda request: "tester")

    client.put("/api/v2/ref-session", json={"patch": {"reviewed": {"k1": "ok", "k2": "bad"}}})
    client.put("/api/v2/ref-session", json={"patch": {"reviewed": {"k1": None}}})

    assert client.get("/api/v2/ref-session").json()["reviewed"] == {"k2": "bad"}


def test_a_stale_window_cannot_clear_the_pull_request_pointer(tmp_path, monkeypatch):
    """One window publishes; the other still has pr=null and saves a verdict.

    Last-writer-wins on the PR pointer would send the published work's PR number
    back to null, and the header would stop naming the pull request the curator's
    verdicts are in. Publishing sets these; nothing else may unset them.
    """
    monkeypatch.setattr(config, "USER_DIR", tmp_path)
    monkeypatch.setattr(sessions, "_login", lambda request: "tester")

    pr = {"number": 12, "url": "https://example/pr/12", "fork": False}
    client.put("/api/v2/ref-session",
               json={"patch": {"published": {"k1": {"pr": 12}}}, "branch": "edit/tester/x", "pr": pr})
    client.put("/api/v2/ref-session",
               json={"patch": {"reviewed": {"k2": "ok"}}, "branch": None, "pr": None})

    got = client.get("/api/v2/ref-session").json()
    assert got["pr"] == pr
    assert got["branch"] == "edit/tester/x"
    assert got["reviewed"] == {"k2": "ok"}
