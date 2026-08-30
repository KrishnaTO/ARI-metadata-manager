"""Enrich a disease from its *confirmed* database cross-references.

When a curator confirms that an ARI disease maps to an external term (e.g.
``MONDO:0005147``) on the reference-review page, that term is an exact match for
the disease. Two facts of the external term can then be folded back into the ARI
record automatically:

1. **Synonyms.** The external term's label + exact synonyms are name-variants of
   the same disease, so they extend the ARI disease's own synonym list (requirement
   1). Synonyms already known to name a *different* disease (the predictor's
   :func:`~app.predict_service.load_synonym_blocklist`) are dropped, and anything
   equal to the disease's label or an existing synonym is de-duplicated away.

2. **Clinical subtypes.** The external term's *direct children* in its source
   ontology are subtypes of the disease, so they become proposed clinical subtypes
   (requirement 2). Only children of a **confirmed** mapping are proposed — flagged
   (negative) mappings never feed the engine.

This module is pure: it reads the reference indexes (from
:mod:`app.predict_service`) and the per-database subtype files, and returns the
proposed additions. Writing them onto the ontology is
:meth:`app.ontology_service.OntologyService.apply_enrichment`.

Data source
-----------
Synonyms come from the same ``data/2-databases/<db>.index.tsv`` files the predictor
uses. Subtypes come from companion ``data/2-databases/<db>.subtypes.tsv`` files
(direct ``is_a`` parent->child edges) written by ``scripts/fetch_databases.py`` for
the OBO sources (MONDO, DOID, NCIt). Missing subtype files simply yield no subtype
proposals — the same graceful-empty behaviour the predictor has for a missing index.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .predict_service import (
    DEFAULT_BLOCKLIST_PATH,
    DEFAULT_INDEX_DIR,
    LexicalIndex,
    get_indexes,
    load_synonym_blocklist,
    normalize,
)

# Columns of a per-database subtype TSV (must match scripts/fetch_databases.py). The
# child's label is not among them: it is already in that child's index row, and
# repeating it here cost 3.7 MB across the three OBO sources.
SUBTYPE_COLS = ["parent_id", "child_id"]


# ------------------------------------------------------------------ subtype index
def load_subtypes(indexes: list[LexicalIndex],
                  index_dir: str | Path = DEFAULT_INDEX_DIR) -> dict[str, list[dict]]:
    """Load every ``*.subtypes.tsv`` into ``{parent_curie: [{"id", "label"}]}``.

    Parent/child ids are full CURIEs (``MONDO:0005147``), which are globally unique
    across sources, so one flat map serves every database. Missing directory or
    files -> empty map.

    A child's **label is not in the file** — it is already in that child's own index
    row, and ``write_subtypes`` only emits edges whose endpoints are kept terms, so
    ``indexes`` always has it. Resolving it here rather than storing it twice keeps
    the edge files 3.7 MB smaller and makes a relabelled term impossible to
    disagree with itself. A child the loaded indexes do not know resolves to "",
    which :func:`enrich` already skips.
    """
    index_dir = Path(index_dir)
    out: dict[str, list[dict]] = {}
    if not index_dir.is_dir():
        return out
    label_of = {rec["id"]: rec.get("label", "") for idx in indexes for rec in idx.records}
    seen: set[tuple[str, str]] = set()
    for path in sorted(index_dir.glob("*.subtypes.tsv")):
        try:
            with open(path, encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f, delimiter="	"):
                    parent, child = row.get("parent_id", ""), row.get("child_id", "")
                    if not parent or not child or (parent, child) in seen:
                        continue
                    seen.add((parent, child))
                    out.setdefault(parent, []).append(
                        {"id": child, "label": label_of.get(child, "")})
        except (OSError, csv.Error):
            continue
    return out


# ------------------------------------------------------------------ reverse lookup
def build_id_index(indexes: list[LexicalIndex]) -> dict[tuple[str, str], list[dict]]:
    """``(db_key, bare_id) -> [records]`` across every loaded reference index.

    A record is registered under every ``(db, id)`` it carries in ``by_db`` — its
    own id in its owning column *and* every cross-reference it declares. So a
    confirmed ``(mondo, 0005147)`` finds the MONDO record directly, and a confirmed
    ``(snomed, 44054006)`` finds whatever record cross-references that SNOMED id.

    **Ambiguous ids are dropped per source.** Many cross-references are coarser than
    a disease concept — a single ICD-10 code such as ``H90.3`` is xreffed by 134
    different DOID terms — so an id that matches more than one record *within one
    source ontology* does not identify a concept there and would otherwise pour 134
    unrelated diseases' names into one synonym list. Such an id contributes nothing
    from that source. An id that pins exactly one record in each of several sources
    (e.g. a SNOMED id naming the same disease in both MONDO and DOID) is kept from
    all of them: that is agreement, not ambiguity.
    """
    out: dict[tuple[str, str], list[dict]] = {}
    for idx in indexes:
        local: dict[tuple[str, str], list[dict]] = {}
        for rec in idx.records:
            for db, ids in rec.get("by_db", {}).items():
                for ident in ids:
                    local.setdefault((db, str(ident)), []).append(rec)
        for key, recs in local.items():
            if len({r["id"] for r in recs}) == 1:      # unique within this source
                out.setdefault(key, []).append(recs[0])
    return out


def _resolve(db: str, ident: str, id_index: dict[tuple[str, str], list[dict]]) -> list[dict]:
    """Reference records exactly matching a confirmed ``(db, id)`` cross-reference."""
    return id_index.get((db, str(ident)), [])


# ------------------------------------------------------------------------- enrich
def enrich(disease: dict, confirmed: list[dict],
           id_index: dict[tuple[str, str], list[dict]],
           subtypes: dict[str, list[dict]],
           blocklist: dict[str, set[str]] | None = None) -> dict:
    """Proposed synonym + clinical-subtype additions for one disease.

    ``disease`` = ``{"ari_id", "name", "synonyms": [...], "clinical_subtypes": [...]}``
    (the disease's *current* values). ``confirmed`` is that disease's confirmed
    cross-references, each ``{"db", "ids": [...]}`` (flagged/negative mappings must
    not be passed in). Returns ``{"synonyms": [...], "subtypes": [...]}``
    of *new* values only — nothing already present, blocklisted, or duplicated.

    Each entry is ``{"value": str, "source": "<PREFIX>:<id>"}``: this tool's
    output feeds clinical vocabularies, and per-value lineage is the difference
    between a curated record and an aggregated one. Six months on it must be
    possible to say whether a synonym came from MONDO, from DOID, or from a
    human (issue #117).
    """
    name = disease.get("name", "")
    name_norm = normalize(name)
    blocked = (blocklist or {}).get(disease.get("ari_id") or "", set())

    # Existing keys to de-duplicate against.
    have_syn = {name_norm} | {normalize(s) for s in (disease.get("synonyms") or [])}
    have_sub = {normalize(_subtype_name(s)) for s in (disease.get("clinical_subtypes") or [])}

    # Resolve every confirmed mapping to its reference record(s) once.
    records: list[dict] = []
    seen_rec: set[str] = set()
    for c in confirmed:
        db = str(c.get("db", "")).strip()
        for ident in (c.get("ids") or []):
            for rec in _resolve(db, str(ident).strip(), id_index):
                if rec["id"] not in seen_rec:
                    seen_rec.add(rec["id"])
                    records.append(rec)

    new_syn: list[dict] = []
    for rec in records:
        for cand in [rec.get("label", "")] + list(rec.get("synonyms") or []):
            key = normalize(cand)
            if not key or key in have_syn or key in blocked:
                continue
            have_syn.add(key)
            new_syn.append({"value": cand.strip(), "source": rec["id"]})

    # A disease is never its own subtype. When a confirmed mapping points at a
    # broader concept, that concept's children include the disease itself (and its
    # siblings), so skip any child that names the disease or one of its synonyms.
    identity = {name_norm} | {normalize(s) for s in (disease.get("synonyms") or [])} \
        | {normalize(s["value"]) for s in new_syn}

    new_sub: list[dict] = []
    for rec in records:
        for child in subtypes.get(rec["id"], []):
            child_label = (child.get("label") or "").strip()
            key = normalize(child_label)
            if not child_label or key in have_sub or key in identity:
                continue
            have_sub.add(key)
            new_sub.append({"value": f"{child_label} - subtype of {name} ({child['id']})",
                            "source": rec["id"]})

    return {"synonyms": new_syn, "subtypes": new_sub}


def _subtype_name(raw: str) -> str:
    """The display name of a stored clinical-subtype string (before ``" - "``/``" | "``)."""
    s = str(raw)
    if " | " in s:
        s = s.rsplit(" | ", 1)[0]
    return s.partition(" - ")[0].strip()


def enrich_many(diseases: list[dict], confirmed_by_iri: dict[str, list[dict]],
                index_dir: str | Path = DEFAULT_INDEX_DIR,
                blocklist_path: str | Path = DEFAULT_BLOCKLIST_PATH,
                indexes: list[LexicalIndex] | None = None,
                subtypes: dict[str, list[dict]] | None = None,
                blocklist: dict[str, set[str]] | None = None) -> dict[str, dict]:
    """Run :func:`enrich` for several diseases, loading shared data once.

    ``diseases`` items carry an ``iri`` plus the shape :func:`enrich` needs;
    ``confirmed_by_iri`` maps a disease iri to its confirmed cross-references.
    Returns ``{iri: {"synonyms": [...], "subtypes": [...]}}`` for diseases with any
    proposed addition.
    """
    if indexes is None:
        indexes = get_indexes(index_dir)
    if subtypes is None:
        subtypes = load_subtypes(indexes, index_dir)
    if blocklist is None:
        blocklist = load_synonym_blocklist(blocklist_path)
    id_index = build_id_index(indexes)
    out: dict[str, dict] = {}
    for d in diseases:
        iri = d.get("iri")
        conf = confirmed_by_iri.get(iri, [])
        if not conf:
            continue
        result = enrich(d, conf, id_index, subtypes, blocklist)
        if result["synonyms"] or result["subtypes"]:
            out[iri] = result
    return out
