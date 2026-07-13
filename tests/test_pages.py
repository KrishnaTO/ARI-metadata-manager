"""The server-rendered HTML page routes (index, ref-edits, ref-curate).

These routes are registered *before* the catch-all StaticFiles mount so they can
inject the per-deploy asset token; this guards that wiring (and that a new page
like ref-curate is actually reachable and token-substituted)."""
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)


def test_ref_curate_page_renders_with_asset_token():
    r = client.get("/ref-curate/")
    assert r.status_code == 200
    body = r.text
    # The template placeholder must be substituted with the real asset version…
    assert "__ASSETV__" not in body
    assert f"ref-curate.js?v={main.ASSET_VERSION}" in body
    # …and the page must be marked no-cache so it always carries a fresh token.
    assert "no-cache" in r.headers.get("cache-control", "")


def test_ref_curate_route_aliases_all_serve():
    for path in ("/ref-curate", "/ref-curate/", "/ref-curate/index.html"):
        assert client.get(path).status_code == 200, path


def test_ref_curate_static_assets_served():
    # The JS is served by the StaticFiles mount (no token substitution there).
    js = client.get("/ref-curate/ref-curate.js")
    assert js.status_code == 200
    assert "Disease curator" in js.text


def test_ref_edits_page_still_renders():
    # Same shared _render_html path — guard the sibling page too.
    r = client.get("/ref-edits/")
    assert r.status_code == 200
    assert "__ASSETV__" not in r.text
