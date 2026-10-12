"""Loading the reference indexes does not hold the event loop (EFFICIENCY.md §3).

The app runs one worker with one event loop. After ``data/2-databases`` changes,
the next prediction or enrichment request reloaded the indexes inside its
handler, seconds in production, and nothing else was served meanwhile, not even
``/healthz``. The load reads no ontology, so it runs in a thread.
"""
import asyncio
import time

import httpx
import pytest

import app.main as main
from app import enrich_service, predict_service, workspace


@pytest.fixture
def slow_indexes(monkeypatch, make_service):
    """A first index load that takes a while; later ones are cached, as in life."""
    loaded = []

    def _get_indexes(*a, **k):
        if not loaded:
            time.sleep(0.4)
            loaded.append(True)
        return []
    monkeypatch.setattr(predict_service, "get_indexes", _get_indexes)
    monkeypatch.setattr(enrich_service, "get_indexes", _get_indexes)
    svc = make_service()
    monkeypatch.setattr(workspace, "service_for", lambda request, write=False: svc)
    return svc


def _healthz_while(method, path, **kw):
    """The order the two finish in: a blocked loop answers /healthz only after."""
    async def scenario():
        done = []
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            async def slow():
                await c.request(method, path, **kw)
                done.append("slow")

            async def health():
                await asyncio.sleep(0.05)                # once the slow one is loading
                assert (await c.get("/healthz")).status_code == 200
                done.append("healthz")

            await asyncio.gather(slow(), health())
        return done
    return asyncio.run(scenario())


def test_healthz_answers_while_predictions_load_the_indexes(slow_indexes):
    assert _healthz_while("GET", "/api/v2/predictions") == ["healthz", "slow"]


def test_healthz_answers_while_an_enrichment_preview_loads_them(slow_indexes):
    iri = slow_indexes.get_diseases_list()[0]["iri"]
    assert _healthz_while("POST", "/api/v2/enrichment-preview", json={
        "confirmed": [{"iri": iri, "db": "mondo", "ids": ["1"]}]}) == ["healthz", "slow"]
