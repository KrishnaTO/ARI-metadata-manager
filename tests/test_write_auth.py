"""The write-access boundary on the ontology endpoints.

Every write resolves its ontology through ``service_for(..., write=True)``.
An anonymous caller has no private working copy, so before this gate existed
that helper handed back the shared ``BASE`` service and an unauthenticated
request could edit the published ontology directly.

Where GitHub sign-in exists we demand it. A deployment with the integration
switched off (local/offline use) has no identity to check, so writes there still
land on BASE by design.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)

# One representative request per write endpoint; the bodies only need to be
# well-formed enough to reach service_for(), which runs before any of them.
# `client.request` rather than the verb helpers: DELETE carries a body here, and
# TestClient.delete() takes no `json=`.
WRITES = [
    ("PUT", "/api/v2/disease/http://example.org/d1", {"changes": {"name": "x"}}),
    ("POST", "/api/v2/disease", {"data": {"label": "x"}}),
    ("POST", "/api/v2/disease/http://example.org/d1/item", {"category": "symptoms", "values": {}}),
    ("PUT", "/api/v2/item/http://example.org/i1", {"category": "symptoms", "changes": {}}),
    ("DELETE", "/api/v2/item/http://example.org/i1", {"category": "symptoms", "disease": "d"}),
    ("POST", "/api/v2/releases", {"version": "9.9.9"}),
]


@pytest.fixture
def github_on(monkeypatch):
    """A deployment with GitHub integration configured, nobody signed in.

    `user_service` is replaced with a tripwire: the gate is meant to reject the
    request before any ontology is resolved, so reaching it is the failure. This
    also keeps a regression from writing to the tracked `ontologies/` copy —
    these tests drive the real app, and `create_release` in particular would
    otherwise version the real file and leave a snapshot in `releases/`.
    """
    monkeypatch.setattr(main, "GH_ENABLED", True)
    monkeypatch.setattr(main, "_login", lambda request: None)

    reached = []

    def _tripwire(login, create=False):
        reached.append(login)
        raise AssertionError("write gate let an anonymous request through to user_service()")

    monkeypatch.setattr(main, "user_service", _tripwire)
    return reached


@pytest.mark.parametrize("method,url,body", WRITES)
def test_anonymous_write_is_rejected(github_on, method, url, body):
    r = client.request(method, url, json=body)
    assert r.status_code == 401
    assert "Sign in" in r.json()["detail"]
    # The ontology was never even resolved, let alone written.
    assert github_on == []


def test_reads_stay_open_to_anonymous_callers(github_on):
    assert client.get("/api/v2/overview").status_code == 200
    assert client.get("/api/v2/tree/alphabetical").status_code == 200


def test_writes_are_ungated_when_github_is_off(monkeypatch):
    """Local/offline use has no identity to demand, so the gate stands down."""
    monkeypatch.setattr(main, "GH_ENABLED", False)
    monkeypatch.setattr(main, "_login", lambda request: None)
    sentinel = object()
    monkeypatch.setattr(main, "user_service", lambda login, create=False: sentinel)

    class _Req:
        pass

    assert main.service_for(_Req(), write=True) is sentinel
