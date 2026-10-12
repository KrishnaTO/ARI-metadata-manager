"""Publishing some of the diseases a curator changed, not all of them (issue #25).

A publish grafts only its scope onto the source branch, so the diseases a
curator leaves out of a pull request stay in their working copy, still touched,
for a later one.
"""
import shutil
from collections import OrderedDict

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import config, sessions, workspace
from app import github_service as gh
from app.ontology_service import OntologyService


@pytest.fixture
def signed_in(tmp_path, monkeypatch, base_owl, make_service):
    """Signed in as ada; GitHub serves the fixture ontology and captures the commit."""
    base = tmp_path / "base.owl"
    shutil.copy2(base_owl, base)
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "user")
    monkeypatch.setattr(config, "ONTOLOGY_FILE", str(base))
    monkeypatch.setattr(workspace, "BASE", OntologyService(str(base)))
    monkeypatch.setattr(workspace, "USER_SVC", OrderedDict())
    monkeypatch.setattr(config, "GH_ENABLED", True)
    monkeypatch.setattr(sessions, "_user", lambda r: {
        "token": "t", "identity": {"login": "ada", "name": "Ada", "email": "a@x"}})
    monkeypatch.setattr(sessions, "_login", lambda r: "ada")
    branch = make_service()
    committed = {}

    async def _sha(*a):
        return "sha-1"

    async def _file(token, owner, repo, path, ref):
        branch._save()                    # as the branch stores it, not the checkout's line endings
        return branch.path.read_bytes()

    async def _publish_file(**kw):
        committed.update(kw)
        return {"pr_url": "https://example.test/pr/1", "pr_number": 1}

    monkeypatch.setattr(gh, "branch_sha", _sha)
    monkeypatch.setattr(gh, "get_file_at", _file)
    monkeypatch.setattr(gh, "publish_file", _publish_file)
    client = TestClient(main.app)
    iris = [d["iri"] for d in branch.get_diseases_list()[:2]]
    for iri in iris:
        r = client.put(f"/api/v2/disease/{iri}", json={"changes": {"definition": "edited " + iri}})
        assert r.status_code == 200
    return client, committed, iris, tmp_path


def _committed_definition(committed, tmp_path, iri):
    path = tmp_path / "committed.owl"
    path.write_bytes(committed["content_bytes"])
    return OntologyService(str(path)).get_disease_detail(iri)["definition"]


def test_a_disease_left_out_stays_for_a_later_pull_request(signed_in):
    client, committed, (keep, leave), tmp_path = signed_in

    r = client.post("/api/v2/publish", json={"exclude": [leave]})

    assert r.status_code == 200, r.text
    assert _committed_definition(committed, tmp_path, keep) == "edited " + keep
    assert _committed_definition(committed, tmp_path, leave) != "edited " + leave
    assert workspace.touched("ada") == {leave}


def test_publishing_everything_clears_the_touched_set(signed_in):
    client, _committed, _iris, _tmp = signed_in

    assert client.post("/api/v2/publish", json={}).status_code == 200
    assert workspace.touched("ada") == set()


def test_leaving_out_a_disease_that_was_not_changed_is_refused(signed_in):
    client, committed, _iris, _tmp = signed_in

    r = client.post("/api/v2/publish", json={"exclude": ["http://example.org/not-touched"]})

    assert r.status_code == 400 and not committed


def test_leaving_out_every_disease_is_refused(signed_in):
    client, committed, iris, _tmp = signed_in

    r = client.post("/api/v2/publish", json={"exclude": iris})

    assert r.status_code == 400 and not committed
