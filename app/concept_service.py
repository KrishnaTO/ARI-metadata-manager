"""Look up what the free reference indexes actually know about one target-database id.

The reference-review page compares an ARI disease (left) against the candidate target
concept (right). ``predict_service`` can name a concept only when it *predicted* it and
only by its label; there is no way to answer "tell me about ``DOID:2043``". This module
is that lookup: given a database key and an id it returns the concept's label, exact
synonyms, definition and parent terms — with the provenance that makes the answer
honest.

Where the fields come from
--------------------------
Label, synonyms and cross-references come from the prediction index (``<db>.index.tsv``)
that ``predict_service`` already loads. Definitions and parent terms are heavier and are
only needed here, so they live in a sibling ``<db>.details.tsv`` sidecar (id -> definition,
parents) that this module loads lazily — and only for the database actually being looked
up — keeping the prediction hot path lean and the resident cost proportional to use.
A missing sidecar means "details were not built for this database" (``details_available:
false``); a missing id *within* a present sidecar means "this term genuinely has none".

Why provenance is the whole point
----------------------------------
Only five databases have an index of their own (MONDO, DOID, NCIt→``nci``, MeSH,
Orphanet). SNOMED, OMOP, ICD-10 and UMLS appear **only** as cross-reference
columns on those ontologies' terms. So a lookup of ``SNOMEDCT:408335007`` can at best
find "the MONDO term that *claims* this SNOMED id" — which is MONDO's opinion, not
SNOMED's own label. Presenting that as SNOMED's term would let a curator confirm a
wrong ``skos:exactMatch``. Every answer therefore carries:

* ``direct`` — True only when the id came from an index we hold **for that database**
  (an own-index db). For SNOMED/OMOP/ICD-10/UMLS it is always False.
* ``via``    — the hub term(s) a non-direct answer came through (empty when direct).
  Several hubs can claim one id and disagree on its label; that disagreement is a
  signal a curator wants, so all of them are returned rather than one being chosen.
* ``note``   — a plain-language caveat when the answer is not a database's own term,
  when the details sidecar is absent, or when the id is simply not indexed here.

For a non-direct answer only the hub *label(s)* are returned (in ``via`` and as the
top-level ``label`` for the primary hub); synonyms/definition/parents are left empty
because they belong to the hub concept, not the target — filling them would make the
pane look authoritative about a database we have no index for.

Nothing here writes or hits the network; it reads the same ``data/2-databases`` files
``predict_service`` loads, plus their detail sidecars.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from urllib.parse import quote

from .predict_service import LexicalIndex
from .xref_registry import BY_KEY, PREFIX

log = logging.getLogger(__name__)

# Which index ``source`` (file stem) is the *own* index for a target-database key.
# The db key ``nci`` is served by the ``ncit`` index; the rest match by name. A db
# absent here (snomed/omop/icd10/umls) has no own index — only hub xrefs.
SOURCE_DB = {"mondo": "mondo", "doid": "doid", "ncit": "nci",
             "mesh": "mesh", "orphanet": "orphanet"}


def _norm_id(raw: str) -> str:
    """Fold an id to its within-database match key.

    Strips an optional ``PREFIX:`` (the indexes and the ontology disagree on whether
    ids carry one — ``MONDO:0016264`` vs ``0016264``), drops leading zeros from a
    purely numeric remainder, and casefolds the rest so ``ICD10CM:M32.1`` and
    ``M32.1`` agree. Matching is always keyed by ``(db, _norm_id(id))``, so a bare
    ``2043`` is only ever compared **within** one database — ``DOID:2043`` can never
    collide with a MeSH descriptor numbered 2043.
    """
    s = raw.strip()
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s.casefold()


def _split_parents(cell: str) -> list[str]:
    return [p.strip() for p in (cell or "").split(" | ") if p.strip()]


# Reverse ``id -> term`` index, cached per ``indexes`` list. ``get_indexes()`` returns
# the *same* list object while the files are unchanged and a fresh one after they are
# rebuilt, so keying on its identity gives exactly the mtime/size invalidation the
# index cache already provides. The list is held so its id() cannot be reused by a
# later object while the entry is live.
_REVERSE_CACHE: dict[int, tuple[list, dict]] = {}
# Detail sidecars, cached per directory with the same mtime/size signature
# ``get_indexes()`` uses — a sidecar can change without its index changing, so this
# cache watches the ``*.details.tsv`` files independently.
_DETAILS_CACHE: dict[str, tuple[tuple, dict]] = {}


def _build_reverse(indexes: list[LexicalIndex]) -> dict[tuple[str, str], list[tuple]]:
    """``(db, normalized id) -> [(source, record), ...]`` over every term in ``indexes``.

    A term is added once per ``(db, id)`` it supplies — its own id (for own-index
    terms) and every xref it carries — so a SNOMED id lands under every hub term
    that cross-references it. Entries are plain ``(source, record)`` tuples rather
    than dicts: there are hundreds of thousands of them across the five indexes, and
    a dict per entry costs ~27 MB more for the same two fields.
    """
    rev: dict[tuple[str, str], list[tuple]] = {}
    for idx in indexes:
        for rec in idx.records:
            for db, ids in rec["by_db"].items():
                for ident in ids:
                    rev.setdefault((db, _norm_id(ident)), []).append((idx.source, rec))
    return rev


def _reverse_for(indexes: list[LexicalIndex]) -> dict[tuple[str, str], list[tuple]]:
    key = id(indexes)
    cached = _REVERSE_CACHE.get(key)
    if cached and cached[0] is indexes:
        return cached[1]
    rev = _build_reverse(indexes)
    _REVERSE_CACHE[key] = (indexes, rev)
    return rev


def _details_dir(indexes: list[LexicalIndex]) -> Path | None:
    for idx in indexes:
        if idx.path:
            return Path(idx.path).parent
    return None


def _load_details(path: Path) -> dict[str, dict] | None:
    """``term id -> {"definition", "parents"}`` from one sidecar (None if unreadable)."""
    rows: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                ident = (row.get("id") or "").strip()
                if ident:
                    rows[ident] = {"definition": row.get("definition", "") or "",
                                   "parents": _split_parents(row.get("parents", ""))}
    except OSError as e:
        # Don't claim details are available when we could not read them.
        log.warning("Could not read detail sidecar %s: %s", path.name, e)
        return None
    return rows


def _details_for(indexes: list[LexicalIndex], source: str) -> dict[str, dict] | None:
    """Detail rows for one index ``source``; ``None`` when it has no sidecar.

    Only the sidecar of the database actually being looked up is read — a DOID
    lookup has no reason to pull MONDO's and NCIt's definitions into memory, which
    is the difference between ~8 MB and ~52 MB resident. Cached per file with the
    same mtime/size invalidation ``get_indexes()`` uses, so a regenerated sidecar is
    picked up without a restart.
    """
    d = _details_dir(indexes)
    if d is None:
        return None
    path = d / f"{source}.details.tsv"
    try:
        st = path.stat()
        sig = (st.st_mtime, st.st_size)
    except OSError:
        sig = None                       # absent -> "details not built for this db"
    key = (str(d), source)
    cached = _DETAILS_CACHE.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    rows = None
    if sig is not None:
        rows = _load_details(path)
        if rows is None:
            return None                  # transient IO failure — don't cache it
    _DETAILS_CACHE[key] = (sig, rows)
    return rows


def _url(db: str, num: str, raw_id: str) -> str | None:
    """Link-out for one id, from the ``xref_registry`` template (no duplicated URLs)."""
    tmpl = BY_KEY[db].get("link")
    if not tmpl:
        return None
    return tmpl.replace("{num}", quote(num, safe="")).replace("{id}", quote(raw_id, safe=""))


def lookup(db: str, obj_id: str, indexes: list[LexicalIndex]) -> dict:
    """What the free indexes know about ``obj_id`` in database ``db``.

    ``db`` is a review-column key (``doid``, ``snomed``, ...); an unknown key raises
    ``KeyError`` (the API turns that into a 404). ``obj_id`` may be bare or carry a
    ``PREFIX:``. Returns the provenance-bearing dict documented at module top;
    ``found: false`` is a normal 200 answer for a valid-but-unindexed id, not an error.
    """
    meta = BY_KEY[db]                       # KeyError -> 404 for an unknown db
    prefix = PREFIX[db]
    num = obj_id.split(":", 1)[1].strip() if ":" in obj_id else obj_id.strip()
    canonical = f"{prefix}:{num}"
    base = {"db": db, "id": canonical, "prefix": prefix, "url": _url(db, num, obj_id)}

    entries = _reverse_for(indexes).get((db, _norm_id(obj_id)), [])
    direct = [(src, rec) for src, rec in entries if SOURCE_DB.get(src) == db]

    if direct:
        source, rec = direct[0]
        detail = _details_for(indexes, source)      # None when no sidecar for this db
        info = detail.get(rec["id"], {}) if detail is not None else {}
        out = {**base, "found": True, "direct": True,
               "label": rec.get("label", ""), "synonyms": list(rec.get("synonyms", [])),
               "definition": info.get("definition", ""), "parents": list(info.get("parents", [])),
               "details_available": detail is not None, "via": []}
        if detail is None:
            out["note"] = ("Definitions aren't built for this database — run "
                           "scripts/fetch_databases.py to generate its <db>.details.tsv sidecar.")
        return out

    if entries:
        # No own index for this db — the id is only known through hub cross-references.
        via = [{"source": src, "id": rec["id"], "label": rec["label"]}
               for src, rec in entries]
        labels = {v["label"] for v in via if v["label"]}
        sources = sorted({v["source"] for v in via})
        note = (f"{meta['label']} has no index here; "
                f"showing the {', '.join(sources)} term(s) that cross-reference {num}.")
        if len(labels) > 1:
            note += " The hub terms disagree on the label — compare them before confirming."
        return {**base, "found": True, "direct": False,
                "label": via[0]["label"], "synonyms": [], "definition": "", "parents": [],
                "details_available": False, "via": via, "note": note}

    note = f"{meta['label']} {canonical} is not in the free reference indexes."
    if db == "omop":
        note = ("OMOP is not covered by the free indexes; predicting it needs the "
                "licensed OHDSI Athena vocabulary (see data/2-databases/README.md).")
    return {**base, "found": False, "direct": False,
            "label": "", "synonyms": [], "definition": "", "parents": [],
            "details_available": False, "via": [], "note": note}
