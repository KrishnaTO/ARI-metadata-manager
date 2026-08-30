"""The write boundary on the feedback API.

These four routes had no authentication of any kind and took `author` straight
from the request body, so anyone could post under a named curator's identity
and delete anyone's comments (issue #102).
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions, workspace
from app.feedback_service import MAX_MESSAGE_CHARS, FeedbackStore

client = TestClient(main.app)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A throwaway feedback store on the real app, nobody signed in."""
    fs = FeedbackStore(tmp_path)
    monkeypatch.setattr(workspace.BASE, "feedback", fs)
    monkeypatch.setattr(sessions, "_login", lambda request: None)
    return fs


@pytest.fixture
def as_alice(store, monkeypatch):
    monkeypatch.setattr(sessions, "_login", lambda request: "alice")
    return store


ANON_WRITES = [
    ("POST", "/api/v2/feedback", {"disease": "d", "term": "t", "message": "hi"}),
    ("PUT", "/api/v2/feedback/fb_1", {"message": "rewritten"}),
    ("DELETE", "/api/v2/feedback/fb_1", None),
]


@pytest.mark.parametrize("method,url,body", ANON_WRITES)
def test_anonymous_writes_are_rejected(store, method, url, body):
    r = client.request(method, url, json=body)
    assert r.status_code == 401
    assert store.list() == []


def test_reading_feedback_stays_open(store):
    assert client.get("/api/v2/feedback").status_code == 200


def test_author_comes_from_the_session_not_the_body(as_alice):
    r = client.post("/api/v2/feedback",
                    json={"disease": "d", "term": "t", "message": "hi",
                          "author": "someone-elses-name"})
    assert r.status_code == 200
    assert r.json()["author"] == "alice"


def test_another_curators_entry_cannot_be_edited_or_deleted(as_alice, monkeypatch):
    entry = as_alice.add("d", "t", "alice's note", author="alice")
    monkeypatch.setattr(sessions, "_login", lambda request: "mallory")
    monkeypatch.setattr(config, "ASSIGN_ADMINS", ["admin"])   # mallory is not an admin

    assert client.put(f"/api/v2/feedback/{entry['id']}",
                      json={"message": "rewritten"}).status_code == 400
    assert client.request("DELETE", f"/api/v2/feedback/{entry['id']}").status_code == 400
    assert as_alice.list()[0]["message"] == "alice's note"


def test_your_own_entry_is_editable(as_alice):
    entry = as_alice.add("d", "t", "note", author="alice")
    r = client.put(f"/api/v2/feedback/{entry['id']}", json={"message": "revised"})
    assert r.status_code == 200
    assert as_alice.list()[0]["message"] == "revised"


@pytest.mark.parametrize("method,body", [
    ("PUT", {"message": "rewritten"}),
    ("DELETE", None),
])
def test_an_entry_that_does_not_exist_is_a_404(as_alice, method, body):
    """Signed in, unknown id — a 404, not a 500.

    `_own_feedback` raised a bare `KeyError`, which was fine while every
    `KeyError` mapped to a 404. #130 removed that mapping deliberately, so an
    incidental dictionary bug would 500 with a traceback instead of posing as a
    404 — and took this genuine missing-entity case down with it. Only the
    anonymous 401 path was covered, so nothing caught the change.
    """
    r = client.request(method, "/api/v2/feedback/fb_does_not_exist", json=body)
    assert r.status_code == 404
    assert "fb_does_not_exist" in r.json()["detail"]


def test_message_length_is_capped(as_alice):
    r = client.post("/api/v2/feedback",
                    json={"disease": "d", "term": "t", "message": "x" * (MAX_MESSAGE_CHARS + 1)})
    assert r.status_code == 400
    assert "too long" in r.json()["detail"]


def test_a_delete_that_matches_nothing_does_not_rewrite_the_file(tmp_path):
    fs = FeedbackStore(tmp_path)
    fs.add("d", "t", "keep me", author="alice")
    before = fs.path.stat().st_mtime_ns
    assert fs.delete("fb_nope") == {"ok": True, "deleted": False}
    assert fs.path.stat().st_mtime_ns == before
    assert len(fs.list()) == 1
