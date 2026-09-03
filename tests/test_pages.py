"""The server-rendered HTML page routes (index, ref-edits).

These routes are registered *before* the catch-all StaticFiles mount so they can
inject the per-deploy asset token; this guards that wiring."""
import os

from fastapi.testclient import TestClient

import app.config as config
import app.main as main

client = TestClient(main.app)


def test_ref_edits_page_still_renders():
    # Same shared _render_html path — guard the sibling page too.
    r = client.get("/ref-edits/")
    assert r.status_code == 200
    assert "__ASSETV__" not in r.text


def test_asset_token_follows_the_newest_asset(tmp_path, monkeypatch):
    """The token is what stops a browser serving last deploy's immutable JS.

    It is derived per render from the newest mtime under ``static/``, so a deploy
    that writes an asset moves it even if the process is not restarted — the
    window in which `deploy/update.sh` has pulled but is still deferring the
    restart around a working curator.
    """
    monkeypatch.setattr(config, "STATIC_DIR", tmp_path)
    asset = tmp_path / "app.js"
    asset.write_bytes(b"one")
    os.utime(asset, (1_000_000, 1_000_000))
    before = config.asset_version()

    asset.write_bytes(b"two")
    os.utime(asset, (2_000_000, 2_000_000))
    assert config.asset_version() != before
