#!/usr/bin/env python3
"""Seed the id-authorship ledger from the curated mapping files.

``app/id_provenance.py`` only learns who added a cross-reference id from edits
made *through the app*, so every id curated before it existed has no recorded
author and stays confirmable by the curator who added it. Both mapping files
name a curator per mapping — SSSOM in ``author_id``, equivalencies in ``source``
— which for this repo's history is whoever entered the id.

Read from the **source ARI repo** by default (``KrishnaTO/ARI``), where
``POST /api/v2/publish`` actually accumulates the mappings — the ontology too, so
the ids are matched against the same snapshot the mappings were written from.
This repo's tracked copies lag behind (they resolve 245 of 322 curated mappings
against 310 for the source repo), and ``--local`` reads those instead.

Disease IRIs are stable across ontology versions, so a ledger built from a newer
ontology than the one being served is still correct: a key for an id that server
does not carry simply never matches anything it renders.

Only positive rows count. A negative (SSSOM ``predicate_modifier = Not``,
equivalencies ``type = manual-negative``) names whoever *flagged* the mapping,
and a ``NoTermFound`` row has no id; neither says who added anything. A claim is
recorded only when its id is still on file for that disease and database, so a
mapping whose id has since been rewritten (the files hold a few, e.g. ICD-10
``362.5`` against ``362.50``) contributes only in the form the ontology carries.
Only ``github:`` authors are used — the ledger is compared against a GitHub
login, which an ORCID cannot be resolved to.

Re-running is safe: the ledger keeps an id's first author, so nothing is moved.

Usage:
    python scripts/backfill_id_authors.py                 # from KrishnaTO/ARI@main
    python scripts/backfill_id_authors.py --local         # from this working tree
"""
import argparse
import os
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import id_provenance  # noqa: E402
from app.ontology_service import OntologyService  # noqa: E402
from app.sssom_service import NO_TERM, NO_TERM_ID, PREFIX_TO_DBS  # noqa: E402

RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"


def _fetch(repo: str, ref: str, path: str) -> bytes:
    url = RAW.format(repo=repo, ref=ref, path=path)
    print(f"  fetching {url}")
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def _rows(text: str) -> list:
    """A TSV's data rows as dicts, keyed by its own header."""
    lines = [ln for ln in (text or "").splitlines()
             if ln.strip() and not ln.startswith("#")]
    if not lines:
        return []
    cols = lines[0].split("\t")
    return [dict(zip(cols, ln.split("\t"))) for ln in lines[1:]]


def _ari_number(curie: str):
    """The numeric part of an ARI id, so ``ARI:0001008`` and ``1008`` agree."""
    m = re.search(r"(\d+)", curie or "")
    return int(m.group(1)) if m else None


def _claims(sssom_text: str, equiv_text: str, skipped: dict):
    """``(ari number, object prefix, id, login)`` for every positive mapping.

    The two files describe the same judgments in different shapes and neither is
    a superset of the other, so both are read; duplicate claims are harmless
    because the ledger keeps an id's first author.
    """
    for row in _rows(sssom_text):
        if (row.get("predicate_modifier") or "").strip().lower() == "not":
            skipped["negative"] += 1
            continue
        obj = row.get("object_id", "")
        if obj == NO_TERM:
            skipped["no_term"] += 1
            continue
        prefix, _, ident = obj.partition(":")
        yield row.get("subject_id", ""), prefix, ident, row.get("author_id", "")

    for row in _rows(equiv_text):
        if "negative" in (row.get("type") or "").lower():
            skipped["negative"] += 1
            continue
        ident = row.get("target_id", "")
        if ident == NO_TERM_ID:
            skipped["no_term"] += 1
            continue
        yield row.get("source_id", ""), row.get("target_prefix", ""), ident, row.get("source", "")


def backfill(sssom_text: str, equiv_text: str, ontology_path, store) -> dict:
    svc = OntologyService(str(ontology_path))
    # ARI number -> (iri, {db: [ids]}), so a claim is only recorded against an id
    # the disease still carries.
    diseases = {}
    for row in svc.get_xref_rows():
        n = _ari_number(row.get("ari_id") or "")
        if n is not None:
            diseases[n] = (row["iri"], {k: [str(i) for i in v]
                                        for k, v in svc.get_xrefs(row["iri"]).items()})

    # (iri, login) -> {db: [ids]}
    pending = {}
    skipped = {"negative": 0, "no_term": 0, "author": 0, "disease": 0, "id": 0}
    seen = set()
    for subject, prefix, ident, author in _claims(sssom_text, equiv_text, skipped):
        if not author.startswith("github:"):
            skipped["author"] += 1
            continue
        login = author.split(":", 1)[1].strip()
        n = _ari_number(subject)
        if n is None or n not in diseases:
            skipped["disease"] += 1
            continue
        iri, xrefs = diseases[n]
        hits = [db for db in PREFIX_TO_DBS.get(prefix, []) if ident in xrefs.get(db, [])]
        if not hits:
            skipped["id"] += 1
            continue
        for db in hits:
            if (iri, db, ident) in seen:      # the same mapping in both files
                continue
            seen.add((iri, db, ident))
            pending.setdefault((iri, login), {}).setdefault(db, []).append(ident)

    recorded = 0
    for (iri, login), by_db in pending.items():
        recorded += store.record(iri, {}, by_db, login)
    return {"recorded": recorded, "skipped": skipped}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--local", action="store_true",
                   help="Read this repo's mappings/ instead of the source ARI repo")
    p.add_argument("--repo", default="{}/{}".format(os.environ.get("GITHUB_OWNER", "KrishnaTO"),
                                                    os.environ.get("GITHUB_REPO", "ARI")))
    p.add_argument("--ref", default=os.environ.get("GITHUB_BASE_BRANCH", "main"))
    p.add_argument("--ledger", default=str(ROOT / "provenance"),
                   help="Directory holding id-authors.json")
    a = p.parse_args()

    sssom_path = os.environ.get("GITHUB_SSSOM_PATH", "mappings/ari.sssom.tsv")
    equiv_path = os.environ.get("GITHUB_EQUIV_PATH", "mappings/ari.equivalencies.tsv")
    onto_path = os.environ.get("GITHUB_ONTOLOGY_PATH", "ontologies/ari_t1d.owl")

    tmp_onto = None
    try:
        if a.local:
            print(f"Reading {ROOT}")
            sssom_text = (ROOT / sssom_path).read_text(encoding="utf-8")
            equiv_text = (ROOT / equiv_path).read_text(encoding="utf-8")
            ontology = ROOT / onto_path
        else:
            print(f"Reading {a.repo}@{a.ref}")
            sssom_text = _fetch(a.repo, a.ref, sssom_path).decode("utf-8")
            equiv_text = _fetch(a.repo, a.ref, equiv_path).decode("utf-8")
            tf = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
            tf.write(_fetch(a.repo, a.ref, onto_path))
            tf.close()
            ontology = tmp_onto = Path(tf.name)

        store = id_provenance.IdAuthorStore(a.ledger)
        before = len(store.authors())
        out = backfill(sssom_text, equiv_text, ontology, store)
    finally:
        if tmp_onto:
            tmp_onto.unlink(missing_ok=True)

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
