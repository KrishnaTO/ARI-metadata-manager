"""Review-queue assignment endpoints and their auth boundary.

Filling your *own* queue is never gated — that is what the ref-edits matrix's
"Add to my queue" does. Queueing work for *another* curator is what the
``ASSIGN_ADMINS`` allow-list restricts.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import assignment_service as asv
from app import config, sessions, stores

client = TestClient(main.app)


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    """A signed-in curator writing to a throwaway assignment store."""
    monkeypatch.setattr(stores, "ASSIGNMENTS", asv.AssignmentStore(tmp_path / "assignments"))
    monkeypatch.setattr(config, "ASSIGN_ADMINS", [])
    monkeypatch.setattr(sessions, "_login", lambda request: "tester")
    return stores.ASSIGNMENTS


def test_anonymous_cannot_assign():
    r = client.post("/api/v2/assignments", json={"iris": ["a"]})
    assert r.status_code == 401
    assert "Sign in" in r.json()["detail"]


def test_self_assign_defaults_to_the_caller(signed_in):
    r = client.post("/api/v2/assignments", json={"iris": ["a", "b"]})
    assert r.status_code == 200
    assert r.json()["iris"] == ["a", "b"]
    assert signed_in.owner_of("a") == "tester"


def test_self_assign_works_even_when_an_allow_list_is_set(signed_in, monkeypatch):
    monkeypatch.setattr(config, "ASSIGN_ADMINS", ["lead"])
    assert client.post("/api/v2/assignments", json={"iris": ["a"]}).status_code == 200
    assert client.post("/api/v2/assignments",
                       json={"login": "tester", "iris": ["b"]}).status_code == 200


def test_assigning_to_another_curator_needs_the_allow_list(signed_in, monkeypatch):
    monkeypatch.setattr(config, "ASSIGN_ADMINS", ["lead"])
    r = client.post("/api/v2/assignments", json={"login": "someone", "iris": ["a"]})
    assert r.status_code == 400
    assert "only change their own" in r.json()["detail"]
    assert signed_in.owner_of("a") is None

    monkeypatch.setattr(config, "ASSIGN_ADMINS", ["tester"])
    assert client.post("/api/v2/assignments",
                       json={"login": "someone", "iris": ["a"]}).status_code == 200
    assert signed_in.owner_of("a") == "someone"


def test_a_disease_another_curator_holds_is_refused_then_moved(signed_in):
    signed_in.assign("someone", ["a"])
    r = client.post("/api/v2/assignments", json={"iris": ["a"]})
    assert r.status_code == 400
    assert "@someone holds 1" in r.json()["detail"]
    assert signed_in.owner_of("a") == "someone"

    ok = client.post("/api/v2/assignments", json={"iris": ["a"], "reassign": True})
    assert ok.status_code == 200
    assert signed_in.owner_of("a") == "tester"


def test_unassign_is_scoped_to_your_own_queue(signed_in, monkeypatch):
    signed_in.assign("tester", ["a", "b"])
    signed_in.assign("someone", ["c"])
    assert client.request("DELETE", "/api/v2/assignments",
                          json={"iris": ["a"]}).json()["iris"] == ["b"]

    monkeypatch.setattr(config, "ASSIGN_ADMINS", ["lead"])
    r = client.request("DELETE", "/api/v2/assignments",
                       json={"login": "someone", "iris": ["c"]})
    assert r.status_code == 400
    assert signed_in.owner_of("c") == "someone"
