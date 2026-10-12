"""The source branch's ontology is downloaded once per commit (EFFICIENCY.md §10).

The publish dialog, the publish, sync, resolve and the export each downloaded
the branch's ontology (~1.7 MB); a routine publish fetched the same bytes three
or four times.
"""
import asyncio
import os
import shutil
from collections import OrderedDict

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import baselines, config, sessions, workspace
from app import github_service as gh
from app.ontology_service import OntologyService

SHA = "a" * 40


@pytest.fixture
def downloads(tmp_path, monkeypatch, make_service):
    """GitHub serving the fixture ontology at any commit; counts ontology downloads."""
    monkeypatch.setattr(config, "USER_DIR", tmp_path / "user")
    branch = make_service()
    seen = []

    async def _file(token, owner, repo, path, ref):
        if path != config.GH_ONTOLOGY_PATH:
            raise FileNotFoundError(path)
        seen.append(ref)
        branch._save()                     # as the branch stores it, not the checkout's line endings
        return branch.path.read_bytes()

    monkeypatch.setattr(gh, "get_file_at", _file)
    return seen


def test_a_commit_is_downloaded_once(downloads):
    first = asyncio.run(baselines.ontology_at("t", SHA))
    second = asyncio.run(baselines.ontology_at("t", SHA))
    assert first == second and downloads == [SHA]


def test_only_the_most_recently_used_commits_are_kept(downloads, monkeypatch):
    monkeypatch.setattr(baselines, "KEEP", 2)
    for age, c in ((30, "a"), (20, "b"), (0, "c")):     # used 30 s ago, 20 s ago, now
        asyncio.run(baselines.ontology_at("t", c * 40))
        path = baselines._dir() / f"{c * 40}.owl"
        when = path.stat().st_mtime - age
        os.utime(path, (when, when))
    assert sorted(p.stem[0] for p in baselines._dir().glob("*.owl")) == ["b", "c"]

    asyncio.run(baselines.ontology_at("t", "b" * 40))    # reading one makes it recent
    asyncio.run(baselines.ontology_at("t", "d" * 40))
    assert sorted(p.stem[0] for p in baselines._dir().glob("*.owl")) == ["b", "d"]


def test_a_value_that_is_not_a_commit_sha_is_refused(downloads):
    """Resolve takes the sha from the request body, and it names a file on disk."""
    with pytest.raises(ValueError):
        asyncio.run(baselines.ontology_at("t", "../../etc/passwd"))
    assert downloads == []


def test_the_publish_dialog_and_the_publish_share_one_download(downloads, tmp_path, monkeypatch,
                                                               base_owl):
    base = tmp_path / "base.owl"
    shutil.copy2(base_owl, base)
    monkeypatch.setattr(config, "ONTOLOGY_FILE", str(base))
    monkeypatch.setattr(workspace, "BASE", OntologyService(str(base)))
    monkeypatch.setattr(workspace, "USER_SVC", OrderedDict())
    monkeypatch.setattr(config, "GH_ENABLED", True)
    monkeypatch.setattr(sessions, "_user", lambda r: {
        "token": "t", "identity": {"login": "ada", "name": "Ada", "email": "a@x"}})
    monkeypatch.setattr(sessions, "_login", lambda r: "ada")

    async def _sha(*a):
        return SHA

    async def _publish_file(**kw):
        return {"pr_url": "https://example.test/pr/1", "pr_number": 1}

    monkeypatch.setattr(gh, "branch_sha", _sha)
    monkeypatch.setattr(gh, "publish_file", _publish_file)
    client = TestClient(main.app)
    iri = workspace.BASE.get_diseases_list()[0]["iri"]
    assert client.put(f"/api/v2/disease/{iri}", json={"changes": {"definition": "x"}}).status_code == 200

    assert client.get("/api/v2/pending-changes").json()["changes"]
    assert client.post("/api/v2/publish", json={}).status_code == 200
    assert downloads == [SHA]
