"""The write-access boundary on every /api/v2 endpoint.

Every ontology write resolves through ``service_for(..., write=True)``. An
anonymous caller has no private working copy, so before this gate existed that
helper handed back the shared ``BASE`` service and an unauthenticated request
could edit the published ontology directly.

Where GitHub sign-in exists we demand it. A deployment with the integration
switched off (local/offline use) has no identity to check, so writes there still
land on BASE by design.

The route table is derived from the app's own OpenAPI schema rather than typed
out here. Five files used to keep their own list of "this endpoint 401s when
nobody is signed in" -- and between them they covered 13 of the 20 write routes,
so ``/api/v2/publish``, ``/api/v2/fetch``, ``/api/v2/source``, ``/api/v2/pr-base``
and ``/api/v2/assignments/done`` were gated by code nothing exercised. Deriving
the list means a write endpoint added without a gate fails here on the commit
that adds it, with no test to remember to write.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions, workspace

client = TestClient(main.app)

WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# Write routes deliberately open to anonymous callers. Anything listed here is a
# hole in the gate, so each one carries the reason it is not one.
OPEN_TO_ANONYMOUS = {
    # A read that happens to take a body: it computes enrichment proposals from
    # the ids it is given and writes nothing. Reads are open throughout.
    ("POST", "/api/v2/enrichment-preview"),
    # Clearing a session you may not have is not a write anyone needs to own.
    ("POST", "/api/v2/logout"),
}

# Stand-ins for the path parameters. The values only have to be well-formed; the
# gate runs before the handler ever looks one up.
PATH_PARAMS = {"iri": "http://example.org/d1", "fid": "fb_1"}


def _write_routes() -> set[tuple[str, str]]:
    """(method, path) for every /api/v2 write route the app actually serves."""
    found = set()
    for path, operations in main.app.openapi()["paths"].items():
        if not path.startswith("/api/v2/"):
            continue
        found |= {(m.upper(), path) for m in operations if m.upper() in WRITE_METHODS}
    return found


GATED = sorted(_write_routes() - OPEN_TO_ANONYMOUS)


def _url(path: str) -> str:
    for name, value in PATH_PARAMS.items():
        path = path.replace("{" + name + "}", value)
    assert "{" not in path, f"add a PATH_PARAMS stand-in for the parameter in {path}"
    return path


@pytest.fixture
def github_on(monkeypatch):
    """A deployment with GitHub integration configured, nobody signed in.

    `user_service` is replaced with a tripwire on its *create* path -- the one a
    write takes. The gate is meant to reject the request before any working copy
    is resolved, so reaching it is the failure. This also keeps a regression from
    writing to the tracked `ontologies/` copy -- these tests drive the real app,
    and `create_release` in particular would otherwise version the real file and
    leave a snapshot in `releases/`. Reads resolve through the same helper
    without `create`, and land on BASE for an anonymous caller.
    """
    monkeypatch.setattr(config, "GH_ENABLED", True)
    monkeypatch.setattr(sessions, "_login", lambda request: None)

    real = workspace.user_service
    reached = []

    def _tripwire(login, create=False):
        if not create:
            return real(login)
        reached.append(login)
        raise AssertionError("write gate let an anonymous request through to user_service()")

    monkeypatch.setattr(workspace, "user_service", _tripwire)
    return reached


@pytest.mark.parametrize("method,path", GATED, ids=[f"{m} {p}" for m, p in GATED])
def test_anonymous_write_is_rejected(github_on, method, path):
    # An empty body is enough: every gate runs before the handler validates what
    # it was sent, so a 422 here would mean the gate had moved behind it.
    r = client.request(method, _url(path), json={})
    assert r.status_code == 401, f"{method} {path} answered {r.status_code}: {r.text}"
    assert "Sign in" in r.json()["detail"]
    # The ontology was never even resolved, let alone written.
    assert github_on == []


def test_the_route_table_is_not_empty():
    """A schema change that stopped yielding routes would turn the parametrized
    test above into a silent no-op rather than a failure."""
    assert len(GATED) >= 15


def test_reads_stay_open_to_anonymous_callers(github_on):
    assert client.get("/api/v2/overview").status_code == 200
    assert client.get("/api/v2/tree/alphabetical").status_code == 200


def test_writes_are_ungated_when_github_is_off(monkeypatch):
    """Local/offline use has no identity to demand, so the gate stands down."""
    monkeypatch.setattr(config, "GH_ENABLED", False)
    monkeypatch.setattr(sessions, "_login", lambda request: None)
    sentinel = object()
    monkeypatch.setattr(workspace, "user_service", lambda login, create=False: sentinel)

    class _Req:
        pass

    assert workspace.service_for(_Req(), write=True) is sentinel
