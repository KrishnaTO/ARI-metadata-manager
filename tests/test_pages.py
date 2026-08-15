"""The server-rendered HTML page routes (index, ref-edits).

These routes are registered *before* the catch-all StaticFiles mount so they can
inject the per-deploy asset token; this guards that wiring."""
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)


def test_ref_edits_page_still_renders():
    # Same shared _render_html path — guard the sibling page too.
    r = client.get("/ref-edits/")
    assert r.status_code == 200
    assert "__ASSETV__" not in r.text
