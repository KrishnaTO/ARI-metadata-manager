#!/usr/bin/env python3
"""Seed the id-authorship ledger from the accumulated SSSOM mappings.

``app/id_provenance.py`` only learns who added a cross-reference id from edits
made *through the app*, so every id curated before it existed has no recorded
author and stays confirmable by the curator who added it. The SSSOM file records
an ``author_id`` per mapping, which for this repo's history is the curator who
entered the id — enough to backfill.

Only positive rows count. A negative (``predicate_modifier = Not``) names whoever
*flagged* the mapping, and a ``NoTermFound`` row has no id at all; neither says
who added anything. A row is recorded only when its id is still on file for that
disease and database, so a mapping that has since been edited away leaves no
stale key behind, and only ``github:`` authors are used — the ledger is compared
against a GitHub login, which an ORCID cannot be resolved to.

Re-running is safe: the ledger keeps an id's first author, so nothing is moved.

Usage:
    python scripts/backfill_id_authors.py [--sssom mappings/ari.sssom.tsv]
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import id_provenance  # noqa: E402
from app.ontology_service import OntologyService  # noqa: E402
from app.sssom_service import NO_TERM, PREFIX_TO_DBS  # noqa: E402


def _rows(path: Path) -> list:
    """The SSSOM file's data rows as dicts, keyed by its own header."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []
    cols = lines[0].split("\t")
    return [dict(zip(cols, ln.split("\t"))) for ln in lines[1:]]


def _ari_number(curie: str):
    """The numeric part of an ARI id, so ``ARI:0001008`` and ``ARI:1008`` agree."""
    m = re.search(r"(\d+)", curie or "")
    return int(m.group(1)) if m else None


def backfill(sssom_path: Path, ontology_path: Path, store) -> dict:
    svc = OntologyService(str(ontology_path))
    # ARI number -> (iri, {db: [ids]}), so a row is only recorded against an id
    # the disease still carries.
    diseases = {}
    for row in svc.get_xref_rows():
        n = _ari_number(row.get("ari_id") or "")
        if n is not None:
            diseases[n] = (row["iri"], {k: [str(i) for i in v]
                                        for k, v in svc.get_xrefs(row["iri"]).items()})

    # (iri, login) -> {db: [ids]}
    pending, skipped = {}, {"negative": 0, "no_term": 0, "author": 0, "disease": 0, "id": 0}
    for row in _rows(sssom_path):
        if (row.get("predicate_modifier") or "").strip().lower() == "not":
            skipped["negative"] += 1
            continue
        obj = row.get("object_id", "")
        if obj == NO_TERM:
            skipped["no_term"] += 1
            continue
        author = row.get("author_id", "")
        if not author.startswith("github:"):
            skipped["author"] += 1
            continue
        login = author.split(":", 1)[1].strip()
        n = _ari_number(row.get("subject_id", ""))
        if n is None or n not in diseases:
            skipped["disease"] += 1
            continue
        iri, xrefs = diseases[n]
        prefix, _, ident = obj.partition(":")
        hits = [db for db in PREFIX_TO_DBS.get(prefix, []) if ident in xrefs.get(db, [])]
        if not hits:
            skipped["id"] += 1
            continue
        for db in hits:
            pending.setdefault((iri, login), {}).setdefault(db, []).append(ident)

    recorded = 0
    for (iri, login), by_db in pending.items():
        recorded += store.record(iri, {}, by_db, login)
    return {"recorded": recorded, "skipped": skipped}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sssom", default=str(ROOT / "mappings" / "ari.sssom.tsv"))
    p.add_argument("--ontology", default=str(ROOT / "ontologies" / "ari_t1d.owl"))
    p.add_argument("--ledger", default=str(ROOT / "provenance"),
                   help="Directory holding id-authors.json")
    a = p.parse_args()

    store = id_provenance.IdAuthorStore(a.ledger)
    before = len(store.authors())
    out = backfill(Path(a.sssom), Path(a.ontology), store)
    after = store.authors()
    print(f"Ledger: {before} -> {len(after)} ids ({out['recorded']} newly recorded)")
    s = out["skipped"]
    print(f"Skipped: {s['negative']} negative, {s['no_term']} no-term-found, "
          f"{s['author']} non-github author, {s['disease']} unknown disease, "
          f"{s['id']} id no longer on file")
    by_login = {}
    for login in after.values():
        by_login[login] = by_login.get(login, 0) + 1
    for login, n in sorted(by_login.items(), key=lambda kv: -kv[1]):
        print(f"  @{login}: {n} ids")
