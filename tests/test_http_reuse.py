"""Connections are reused across calls (EFFICIENCY.md §8)."""
import asyncio

import httpx

from app import github_service as gh
from app.pr_review import terminology


def test_github_calls_in_one_loop_share_one_client(monkeypatch):
    made = []
    real = httpx.AsyncClient

    def _client(**kw):
        made.append(kw)
        return real(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"commit": {"sha": "abc"}, "sha": "blob"})), **kw)

    monkeypatch.setattr(gh.httpx, "AsyncClient", _client)

    async def three_calls():
        await gh.branch_sha("t", "o", "r", "main")
        await gh.file_blob_sha("t", "o", "r", "x.owl", "main")
        await gh.branch_sha(None, "o", "r", "main")

    asyncio.run(three_calls())
    assert len(made) == 1


def test_each_call_still_sends_its_own_headers(monkeypatch):
    seen = []
    real = httpx.AsyncClient

    def handler(request):
        seen.append((request.headers.get("authorization"), request.headers.get("accept")))
        return httpx.Response(200, json={"commit": {"sha": "abc"}, "sha": "blob"})

    monkeypatch.setattr(gh.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))

    async def calls():
        await gh.branch_sha("t", "o", "r", "main")
        await gh.file_blob_sha(None, "o", "r", "x.owl", "main")

    asyncio.run(calls())
    assert seen == [("Bearer t", "application/vnd.github+json"),
                    (None, "application/vnd.github.object")]


def test_terminology_lookups_share_one_client(monkeypatch):
    calls = []

    class _Client:
        def get(self, url, **kw):
            calls.append(url)
            return httpx.Response(200, json={})

    monkeypatch.setattr(terminology, "_HTTP", _Client())
    terminology._get("https://a.example/1")
    terminology._get("https://a.example/2")
    assert calls == ["https://a.example/1", "https://a.example/2"]
