"""The GitHub calls a publish makes (EFFICIENCY.md §7).

A publish uploads one blob per file (the ontology and the two mapping files),
and those uploads are independent of each other. It also created each PR label
on every publish, although labels almost always exist already.
"""
import asyncio
import json

import httpx
import pytest

from app import github_service as gh


@pytest.fixture
def github(monkeypatch):
    """A fake GitHub for publish_file, recording calls and blob-upload overlap."""
    seen = {"labels_created": 0, "blobs_in_flight": 0, "most_blobs_at_once": 0}
    monkeypatch.setattr(gh, "_LABELS_KNOWN", set())

    async def handler(request: httpx.Request):
        path, method = request.url.path, request.method
        if method == "POST" and path.endswith("/git/blobs"):
            seen["blobs_in_flight"] += 1
            seen["most_blobs_at_once"] = max(seen["most_blobs_at_once"], seen["blobs_in_flight"])
            await asyncio.sleep(0.05)
            seen["blobs_in_flight"] -= 1
            name = json.loads(request.content)["content"][:8]
            return httpx.Response(201, json={"sha": "blob-" + name})
        if method == "POST" and path == "/repos/o/r/labels":
            seen["labels_created"] += 1
            return httpx.Response(201, json={})
        routes = {
            ("GET", "/repos/o/r"): {"permissions": {"push": True}},
            ("GET", "/repos/o/r/git/ref/heads/main"): {"object": {"sha": "base"}},
            ("POST", "/repos/o/r/git/refs"): {},
            ("GET", "/repos/o/r/git/commits/base"): {"tree": {"sha": "tree-0"}},
            ("POST", "/repos/o/r/git/trees"): {"sha": "tree-1"},
            ("POST", "/repos/o/r/git/commits"): {"sha": "commit-1"},
            ("GET", "/repos/o/r/pulls"): [],
            ("POST", "/repos/o/r/pulls"): {"number": 7, "html_url": "https://example.test/7"},
            ("POST", "/repos/o/r/issues/7/labels"): [],
        }
        if path.startswith("/repos/o/r/git/ref/heads/edit/"):
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if method == "PATCH" and path.startswith("/repos/o/r/git/refs/heads/"):
            return httpx.Response(200, json={})
        return httpx.Response(200 if method == "GET" else 201, json=routes[(method, path)])

    real = httpx.AsyncClient
    monkeypatch.setattr(gh.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return seen


def _publish():
    return asyncio.run(gh.publish_file(
        token="t", owner="o", repo="r", base_branch="main", path="ontology.owl",
        content_bytes=b"ontology", extra_files={"a.tsv": b"sssom", "b.tsv": b"equiv"},
        disease_name="Addison", message="Update Addison",
        identity={"login": "ada", "name": "Ada", "email": "a@x"},
        labels=["edit term", "sssom"]))


def test_the_blob_uploads_run_together(github):
    assert _publish()["pr_number"] == 7
    assert github["most_blobs_at_once"] == 3


def test_labels_are_created_once_per_process(github):
    _publish()
    _publish()
    assert github["labels_created"] == 2          # each label once, not once per publish
