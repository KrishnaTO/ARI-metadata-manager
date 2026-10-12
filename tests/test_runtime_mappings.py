"""Signed-out readers are served the data repo's current mapping files.

``deploy/update-mappings.sh`` fetched ``mappings/ari.sssom.tsv`` and
``ari.equivalencies.tsv`` from KrishnaTO/ARI over this repo's *tracked* copies of
the same paths. Every ten minutes ``deploy/update.sh`` then stashed them and
reset the tracked snapshot back, and the next mapping refresh saw the bytes
"change" and restarted the app. Production restarted every ten minutes, readers
saw the old snapshot in between, and the server's stash list grew by one entry
per tick.

The fetched copies now live in their own gitignored directory,
``config.MAPPINGS_RUNTIME_DIR``. The tracked files under ``mappings/`` stay as the
snapshot the tooling and CI read, and are never served.
"""
from fastapi.testclient import TestClient

import app.config as config
import app.main as main

client = TestClient(main.app)

SSSOM = ("subject_id\tpredicate_id\tpredicate_modifier\tobject_id\tobject_source\t"
         "author_id\tmapping_date\tcomment\n"
         "ARI:0009999\tskos:exactMatch\t\tMONDO:0005147\tMONDO\tgithub:bench\t"
         "2026-10-11T00:00:00+00:00\t\n")


def _judgments():
    return [(j["ari_id"], j["prefix"], j["id"], j["judgment"])
            for j in client.get("/api/v2/mappings").json()]


def test_signed_out_judgments_come_from_the_fetched_copies(tmp_path, monkeypatch):
    (tmp_path / "ari.sssom.tsv").write_text(SSSOM, encoding="utf-8")
    monkeypatch.setattr(config, "MAPPINGS_RUNTIME_DIR", tmp_path, raising=False)
    assert _judgments() == [("ARI:0009999", "MONDO", "0005147", "positive")]


def test_the_tracked_snapshot_is_never_served(tmp_path, monkeypatch):
    # Nothing fetched yet: the tracked mappings/ files must not stand in for them.
    monkeypatch.setattr(config, "MAPPINGS_RUNTIME_DIR", tmp_path, raising=False)
    assert _judgments() == []
