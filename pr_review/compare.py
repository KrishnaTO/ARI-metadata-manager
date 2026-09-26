"""The comparison matrix for one pull request's equivalency changes.

A PR from the metadata manager adds rows to ``mappings/ari.equivalencies.tsv``: one
curator judgment per (ARI disease, target-database id), ``manual`` for a confirmed
``skos:exactMatch`` and ``manual-negative`` for a rejected one. This module diffs that
file between the PR's merge base and its head, then puts each changed row's ARI disease
(read from the PR's own ontology) beside what the local reference indexes know about the
target id, and measures how the two agree:

* **name match** — label/synonym equality on both sides, else word overlap;
* **definition overlap** — shared content words between the two definitions;
* **cross-reference support** — other ids the target term cross-references that ARI
  already maps to (or conflicts with, or has rejected);
* **name collisions** — the target's names equal an ARI clinical subtype (a narrower
  concept) or a *different* ARI disease.

SNOMED, OMOP, ICD-10 and UMLS have no index of their own here; their ids are only known
through the MONDO/DOID/NCIt/MeSH/Orphanet terms that cross-reference them, so those rows
are compared against the hub terms and marked ``via``. SNOMED additionally gets its own
term from the public terminology server (``snomed.py``), compared first; the hub terms
stay alongside it for their cross-references. Nothing here writes anything.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile

from app.concept_service import lookup
from app.ontology_service import OntologyService
from app.predict_service import get_indexes, match_key, normalize, token_similarity
from app.xref_registry import BY_KEY, SOURCE_DB, XREF_DATABASES

from . import github, snomed

# SSSOM/equivalencies prefix -> review db key. DXCODE shares SNOMEDCT's prefix but is
# not a mapping target, so SNOMEDCT resolves to ``snomed``.
PREFIX_TO_DB = {d["prefix"].casefold(): d["key"] for d in XREF_DATABASES if d["key"] != "dxcode"}

CONFIRMED, REJECTED = "manual", "manual-negative"

# GitHub names a file's block in a PR's "Files changed" view by the SHA-256 of its path;
# appending ``R<n>``/``L<n>`` targets one line, where a review comment can be added.
DIFF_ANCHOR = "diff-" + hashlib.sha256(github.EQUIV_PATH.encode()).hexdigest()

# Name-match kinds, strongest first, with the points each adds to a row's evidence.
NAME_KINDS = {
    "exact_label": ("Label = label", 3),
    "label_synonym": ("ARI label = target synonym", 3),
    "synonym_label": ("ARI synonym = target label", 2),
    "synonym_synonym": ("Synonym = synonym", 2),
    "partial": ("Partial word overlap", 1),
    "weak": ("Weak word overlap", 0),
    "none": ("No name overlap", 0),
    "no_data": ("Target not in local indexes", 0),
}
PARTIAL_THRESHOLD = 0.5
DEFINITION_THRESHOLD = 0.4

_STOPWORDS = frozenset("""a an and are as at be been by can characterized disease diseases
disorder disorders for from has have in into is it its may of on or other that the their
there these this to which with without caused condition conditions""".split())


def _ari_num(value: str) -> int:
    return int(str(value).split(":")[-1].split("_")[-1])


def _row_key(ari_num: int, prefix: str, target_id: str) -> tuple:
    return (ari_num, prefix.casefold(), match_key(target_id))


# ----------------------------------------------------------------------- TSV inputs
def parse_equivalencies(text: str) -> dict[tuple, dict]:
    """Row key -> row, each row carrying its 1-based file line number as ``line``."""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    rows = {}
    for r in reader:
        r["line"] = reader.line_num
        rows[_row_key(_ari_num(r["source_id"]), r["target_prefix"], r["target_id"])] = r
    return rows


def parse_sssom_comments(text: str) -> dict[tuple, dict]:
    """``row key -> {comment, author, date}`` from an SSSOM file's data rows."""
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    out = {}
    for r in csv.DictReader(lines, delimiter="\t"):
        prefix, _, ident = r["object_id"].partition(":")
        out[_row_key(_ari_num(r["subject_id"]), prefix, ident)] = {
            "comment": r.get("comment", ""), "author": r.get("author_id", ""),
            "date": r.get("mapping_date", "")}
    return out


def diff_equivalencies(base: dict, head: dict) -> list[dict]:
    """Rows the PR added, re-judged (type changed) or removed, relative to ``base``."""
    changes = []
    for key, row in head.items():
        old = base.get(key)
        if old is None:
            changes.append({"key": key, "row": row, "status": "added", "previous_type": ""})
        elif old["type"] != row["type"]:
            changes.append({"key": key, "row": row, "status": "changed",
                            "previous_type": old["type"]})
    for key, row in base.items():
        if key not in head:
            changes.append({"key": key, "row": row, "status": "removed", "previous_type": ""})
    return changes


# ------------------------------------------------------------------------ ARI side
def load_ari(owl_bytes: bytes) -> dict[int, dict]:
    """ARI number -> the disease's names, definition, xrefs and clinical subtypes."""
    fd, path = tempfile.mkstemp(suffix=".owl")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(owl_bytes)
        svc = OntologyService(path)
        out = {}
        for row in svc.get_xref_rows():
            if not row["ari_id"]:
                continue
            raw = svc._get_annotation(svc.world[row["iri"]], svc.base + "ARI_ClinicalSubtype")
            row["subtypes"] = [svc._parse_subtype(s)["name"] for s in raw]
            out[_ari_num(row["ari_id"])] = row
        return out
    finally:
        os.unlink(path)


def _name_owners(ari: dict[int, dict]) -> dict[str, set[int]]:
    """Normalized ARI label/synonym -> the ARI numbers that use it."""
    owners: dict[str, set[int]] = {}
    for num, d in ari.items():
        for n in [d["name"], *d["synonyms"]]:
            if normalize(n):
                owners.setdefault(normalize(n), set()).add(num)
    return owners


# --------------------------------------------------------------------- target side
def target_views(db: str, ident: str, indexes) -> list[dict]:
    """What is known about ``db:ident``: its own term, else the hub terms that
    cross-reference it. Each view carries names, definition and xrefs. SNOMED's own
    term comes from the terminology server and carries no xrefs, so its hub terms are
    kept after it as the cross-reference evidence."""
    views = []
    for idx in indexes:
        for rec in idx.records_for(db, ident):
            own_db = SOURCE_DB.get(idx.source)
            info = lookup(own_db, rec["id"], indexes)
            views.append({"source": idx.source, "direct": own_db == db, "id": rec["id"],
                          "label": rec["label"], "synonyms": rec["synonyms"],
                          "definition": info["definition"], "parents": info["parents"],
                          "url": info["url"], "by_db": rec["by_db"], "inactive": False})
    if db == "snomed":
        concept = snomed.lookup(ident)
        own = [] if concept is None else [{
            "source": snomed.SOURCE, "direct": True, "id": f"SNOMEDCT:{ident}",
            "label": concept["label"], "synonyms": concept["synonyms"], "definition": "",
            "parents": concept["parents"], "url": BY_KEY["snomed"]["link"].replace("{num}", ident),
            "by_db": {}, "inactive": concept["inactive"]}]
        return own + views
    direct = [v for v in views if v["direct"]]
    return direct or views


def name_match(ari_label: str, ari_synonyms: list[str], view: dict) -> dict:
    al = normalize(ari_label)
    asyn = {normalize(s): s for s in ari_synonyms if normalize(s)}
    tl = normalize(view["label"])
    tsyn = {normalize(s): s for s in view["synonyms"] if normalize(s)}

    def hit(kind, ari_name, target_name, overlap=1.0):
        # A hub term's names are the hub's opinion, not the target database's own
        # label, so a match through one counts a point less.
        points = NAME_KINDS[kind][1] - (0 if view["direct"] else 1)
        return {"kind": kind, "label": NAME_KINDS[kind][0], "points": max(points, 0),
                "ari": ari_name, "target": target_name, "overlap": round(overlap, 2),
                "via": "" if view["direct"] else f"{view['source']} {view['id']}"}

    if al and al == tl:
        return hit("exact_label", ari_label, view["label"])
    if al in tsyn:
        return hit("label_synonym", ari_label, tsyn[al])
    if tl in asyn:
        return hit("synonym_label", asyn[tl], view["label"])
    shared = set(asyn) & set(tsyn)
    if shared:
        s = sorted(shared)[0]
        return hit("synonym_synonym", asyn[s], tsyn[s])
    best = (0.0, ari_label, view["label"])
    for a in [ari_label, *ari_synonyms]:
        for t in [view["label"], *view["synonyms"]]:
            sim = token_similarity(a, t)
            if sim > best[0]:
                best = (sim, a, t)
    kind = "partial" if best[0] >= PARTIAL_THRESHOLD else "weak" if best[0] > 0 else "none"
    return hit(kind, best[1], best[2], best[0])


def _content_words(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) > 2 and w not in _STOPWORDS}


def definition_overlap(a: str, b: str) -> dict | None:
    """Shared content words over the shorter definition's (overlap coefficient)."""
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return None
    shared = wa & wb
    return {"score": round(len(shared) / min(len(wa), len(wb)), 2),
            "shared": sorted(shared)[:15]}


# ----------------------------------------------------------------- xref evidence
def _ari_ids(ari_row: dict | None, head_equiv: dict, ari_num: int, pr_keys: set) -> tuple:
    """(known, rejected) id maps for one disease: ``(db, match_key) -> origin``."""
    known, rejected = {}, {}
    if ari_row:
        for d in XREF_DATABASES:
            for i in ari_row.get(d["key"], []):
                known[(d["key"], match_key(i))] = "existing"
    for key, row in head_equiv.items():
        if key[0] != ari_num or key[1] not in PREFIX_TO_DB:
            continue
        db_key = (PREFIX_TO_DB[key[1]], key[2])
        target = known if row["type"] == CONFIRMED else rejected
        target.setdefault(db_key, "existing")
    for key in pr_keys:
        if key[0] == ari_num and key[1] in PREFIX_TO_DB:
            db_key = (PREFIX_TO_DB[key[1]], key[2])
            for m in (known, rejected):
                if db_key in m:
                    m[db_key] = "this PR"
    return known, rejected


def xref_evidence(views: list[dict], db: str, known: dict, rejected: dict) -> dict:
    """Other ids the target term(s) cross-reference, set against ARI's own ids.

    Several hub terms can carry the same id, so entries are merged per id (support,
    against) or per database (conflicts), listing every hub they came through in ``via``.
    """
    support, against, conflicts = {}, {}, {}
    for v in views:
        for xdb, ids in v["by_db"].items():
            if xdb == db:
                continue
            keys = {(xdb, match_key(i)): i for i in ids}
            for k, raw in keys.items():
                bucket = support if k in known else against if k in rejected else None
                if bucket is not None:
                    origin = known.get(k) or rejected[k]
                    entry = bucket.setdefault(k, {"db": BY_KEY[xdb]["label"], "id": raw,
                                                  "origin": origin, "via": []})
                    entry["via"].append(v["id"])
            ari_in_db = sorted(k[1] for k in known if k[0] == xdb)
            if ari_in_db and not set(ari_in_db) & {k[1] for k in keys}:
                entry = conflicts.setdefault(xdb, {"db": BY_KEY[xdb]["label"], "target_ids": [],
                                                   "ari_ids": ari_in_db, "via": []})
                entry["target_ids"] = sorted(set(entry["target_ids"]) | set(ids))
                entry["via"].append(v["id"])
    return {"support": list(support.values()), "against": list(against.values()),
            "conflicts": list(conflicts.values())}


# ---------------------------------------------------------------------- verdict
def hint(status: str, judgment: str, found: bool, score: int, flags: list[str]) -> str:
    if status == "removed":
        return "Removed in PR"
    if not found:
        return "No local data"
    if judgment == REJECTED:
        return "Check rejection" if score >= 3 else "Rejection plausible"
    if score >= 3 and not flags:
        return "Supported"
    return "Review" if score >= 1 else "Weak evidence"


# ------------------------------------------------------------------------ matrix
def build_matrix(refs: dict) -> dict:
    """The matrix for the PR described by ``refs`` (from :func:`github.pr_refs`)."""
    head_text = github.file_at(github.EQUIV_PATH, refs["head_sha"]).decode("utf-8")
    base_equiv = parse_equivalencies(
        github.file_at(github.EQUIV_PATH, refs["merge_base"]).decode("utf-8"))
    head_equiv = parse_equivalencies(head_text)
    main_equiv = parse_equivalencies(
        github.file_at(github.EQUIV_PATH, refs["base_ref"]).decode("utf-8"))
    comments = parse_sssom_comments(
        github.file_at(github.SSSOM_PATH, refs["head_sha"]).decode("utf-8"))
    ari = load_ari(github.file_at(github.ONTOLOGY_PATH, refs["head_sha"]))
    owners = _name_owners(ari)
    indexes = get_indexes()

    changes = diff_equivalencies(base_equiv, head_equiv)
    pr_keys = {c["key"] for c in changes if c["status"] != "removed"}
    rows = [_compare_row(c, refs, ari, owners, head_equiv, main_equiv, comments, pr_keys,
                         indexes) for c in changes]
    # File order; removed rows (numbered in the merge-base file) go last.
    rows.sort(key=lambda r: (r["status"] == "removed", r["line"]))
    return {"pr": refs, "rows": rows}


def _compare_row(change, refs, ari, owners, head_equiv, main_equiv, comments, pr_keys, indexes):
    row, key = change["row"], change["key"]
    num = key[0]
    db = PREFIX_TO_DB.get(key[1])
    ident = row["target_id"]
    ari_row = ari.get(num)
    ari_label = ari_row["name"] if ari_row else row["source_name"]
    ari_syns = ari_row["synonyms"] if ari_row else []
    ari_def = ari_row["definition"] if ari_row else ""

    views = target_views(db, ident, indexes) if db else []
    matches = [name_match(ari_label, ari_syns, v) for v in views]
    best = max(matches, key=lambda m: (m["points"], m["overlap"]), default=None)
    if best is None:
        best = {"kind": "no_data", "label": NAME_KINDS["no_data"][0], "points": 0,
                "ari": "", "target": "", "overlap": 0, "via": ""}

    defs = [(v, definition_overlap(ari_def, v["definition"])) for v in views]
    defs = [(v, o) for v, o in defs if o]
    best_def = max(defs, key=lambda p: p[1]["score"], default=(None, None))[1]

    known, rejected = _ari_ids(ari_row, head_equiv, num, pr_keys)
    evidence = xref_evidence(views, db, known, rejected) if db else \
        {"support": [], "against": [], "conflicts": []}

    subtypes = {normalize(s): s for s in (ari_row or {}).get("subtypes", []) if normalize(s)}
    subtype_hits, other_diseases = set(), set()
    for v in views:
        for n in [v["label"], *v["synonyms"]]:
            if normalize(n) in subtypes:
                subtype_hits.add(subtypes[normalize(n)])
            other_diseases |= owners.get(normalize(n), set()) - {num}
    flags = [f"Target is named like ARI clinical subtype '{s}' (narrower?)"
             for s in sorted(subtype_hits)]
    for other in sorted(other_diseases):
        flags.append(f"Target name matches another ARI disease: "
                     f"ARI:{other:07d} {ari[other]['name']}")
    if db == "snomed" and not any(v["direct"] for v in views):
        flags.append("SNOMED has no such concept (tx.fhir.org, US edition)")
    if any(v["inactive"] for v in views):
        flags.append("SNOMED concept is inactive")
    if evidence["against"]:
        flags.append("Target cross-references an id already rejected for this disease")
    if evidence["conflicts"]:
        flags.append("Target cross-references disagree with ARI's ids in "
                     + ", ".join(sorted({c["db"] for c in evidence["conflicts"]})))

    score = best["points"]
    score += 2 if evidence["support"] else 0
    score += 1 if best_def and best_def["score"] >= DEFINITION_THRESHOLD else 0
    score -= 2 if other_diseases or subtype_hits else 0
    score -= 1 if evidence["against"] or evidence["conflicts"] else 0

    main_row = main_equiv.get(key)
    if main_row is None:
        on_main = "not on main"
    elif main_row["type"] == row["type"]:
        on_main = "same on main"
    else:
        on_main = f"main says {main_row['type']} ({main_row['source']})"

    sssom = comments.get(key, {})
    meta = BY_KEY.get(db, {})
    return {
        "ari_id": f"ARI:{num:07d}", "ari_label": ari_label, "ari_synonyms": ari_syns,
        "ari_definition": ari_def, "ari_subtypes": (ari_row or {}).get("subtypes", []),
        "db": meta.get("label", key[1]), "db_key": db or "", "target_id": ident,
        "target_url": (meta.get("link") or "").replace("{num}", ident).replace("{id}", ident)
        or None,
        # Removed rows only exist in the merge-base file, so their line is from there
        # and sits on the diff's left side (L); every other row is on the right (R).
        "line": row["line"],
        "line_url": f"{refs['url']}/files#{DIFF_ANCHOR}"
                    f"{'L' if change['status'] == 'removed' else 'R'}{row['line']}",
        "status": change["status"], "judgment": row["type"],
        "previous_type": change["previous_type"], "curator": row["source"],
        "comment": sssom.get("comment", ""),
        "found": bool(views), "direct": any(v["direct"] for v in views),
        "views": [{k: v[k] for k in ("source", "direct", "id", "label", "synonyms",
                                      "definition", "parents", "url", "inactive")}
                  for v in views],
        "target_label": views[0]["label"] if views else "",
        "name_match": best, "definition_overlap": best_def, "evidence": evidence,
        "flags": flags, "score": score, "on_main": on_main,
        "hint": hint(change["status"], row["type"], bool(views), score, flags),
    }
