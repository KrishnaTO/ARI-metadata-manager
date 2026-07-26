"""Look up what the free reference indexes actually know about one target-database id.

The reference-review page compares an ARI disease (left) against the candidate target
concept (right). ``predict_service`` can name a concept only when it *predicted* it and
only by its label; there is no way to answer "tell me about ``DOID:2043``". This module
is that lookup: given a database key and an id it returns the concept's label, exact
synonyms, definition and parent terms — with the provenance that makes the answer
honest.

Why provenance is the whole point
----------------------------------
Only five databases have an index of their own (MONDO, DOID, NCIt→``nci``, MeSH,
Orphanet). SNOMED, OMOP, ICD-10, OMIM and UMLS appear **only** as cross-reference
columns on those ontologies' terms. So a lookup of ``SNOMEDCT:408335007`` can at best
find "the MONDO term that *claims* this SNOMED id" — which is MONDO's opinion, not
SNOMED's own label. Presenting that as SNOMED's term would let a curator confirm a
wrong ``skos:exactMatch``. Every answer therefore carries:

* ``direct`` — True only when the id came from an index we hold **for that database**
  (an own-index db). For SNOMED/OMOP/ICD-10/OMIM/UMLS it is always False.
* ``via``    — the hub term(s) a non-direct answer came through (empty when direct).
  Several hubs can claim one id and disagree on its label; that disagreement is a
  signal a curator wants, so all of them are returned rather than one being chosen.
* ``note``   — a plain-language caveat when the answer is not a database's own term,
  when the index predates definitions, or when the id is simply not indexed here.

For a non-direct answer only the hub *label(s)* are returned (in ``via`` and as the
top-level ``label`` for the primary hub); synonyms/definition/parents are left empty
because they belong to the hub concept, not the target — filling them would make the
pane look authoritative about a database we have no index for.

Nothing here writes or hits the network; it reads the same ``data/2-databases`` index
files ``predict_service`` loads, and builds a reverse ``id -> term`` index over them.
"""
from __future__ import annotations

from urllib.parse import quote

from .predict_service import LexicalIndex
from .xref_registry import BY_KEY, PREFIX

# Which index ``source`` (file stem) is the *own* index for a target-database key.
# The db key ``nci`` is served by the ``ncit`` index; the rest match by name. A db
# absent here (snomed/omop/icd10/omim/umls) has no own index — only hub xrefs.
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


# Reverse ``id -> term`` index, cached per ``indexes`` list. ``get_indexes()`` returns
# the *same* list object while the files are unchanged and a fresh one after they are
# rebuilt, so keying on its identity gives exactly the mtime/size invalidation the
# index cache already provides — no separate stat() needed. The list is held so its
# id() cannot be reused by a later object while the entry is live.
_REVERSE_CACHE: dict[int, tuple[list, dict]] = {}


def _build_reverse(indexes: list[LexicalIndex]) -> dict[tuple[str, str], list[dict]]:
    """``(db, normalized id) -> [entry, ...]`` over every term in ``indexes``.

    An *entry* is ``{"source", "has_details", "rec"}``. A term is added once per
    ``(db, id)`` it supplies — its own id (for own-index terms) and every xref it
    carries — so a SNOMED id lands under every hub term that cross-references it.
    """
    rev: dict[tuple[str, str], list[dict]] = {}
    for idx in indexes:
        for rec in idx.records:
            for db, ids in rec["by_db"].items():
                for ident in ids:
                    rev.setdefault((db, _norm_id(ident)), []).append(
                        {"source": idx.source, "has_details": idx.has_details, "rec": rec})
    return rev


def _reverse_for(indexes: list[LexicalIndex]) -> dict[tuple[str, str], list[dict]]:
    key = id(indexes)
    cached = _REVERSE_CACHE.get(key)
    if cached and cached[0] is indexes:
        return cached[1]
    rev = _build_reverse(indexes)
    _REVERSE_CACHE[key] = (indexes, rev)
    return rev


def _url(db: str, prefix: str, num: str, raw_id: str) -> str | None:
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
    base = {"db": db, "id": canonical, "prefix": prefix, "url": _url(db, prefix, num, obj_id)}

    entries = _reverse_for(indexes).get((db, _norm_id(obj_id)), [])
    direct = [e for e in entries if SOURCE_DB.get(e["source"]) == db]

    if direct:
        e = direct[0]
        rec = e["rec"]
        out = {**base, "found": True, "direct": True,
               "label": rec.get("label", ""), "synonyms": list(rec.get("synonyms", [])),
               "definition": rec.get("definition", ""), "parents": list(rec.get("parents", [])),
               "details_available": e["has_details"], "via": []}
        if not e["has_details"]:
            out["note"] = ("This index predates the definition/parents columns; "
                           "regenerate it with scripts/fetch_databases.py to populate them.")
        return out

    if entries:
        # No own index for this db — the id is only known through hub cross-references.
        via = [{"source": e["source"], "id": e["rec"]["id"], "label": e["rec"]["label"]}
               for e in entries]
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
