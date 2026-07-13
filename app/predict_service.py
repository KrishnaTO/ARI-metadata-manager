"""Predict database cross-references for diseases by exact name/synonym match.

Issue #42: many ARI diseases already carry an id for some of the target databases
(SNOMED, OMOP, DOID, MONDO, NCI, ICD-10, Orphanet, OMIM, UMLS, MeSH) but leave the
rest blank. For a blank cell we can often *propose* an id automatically: if the
disease's own label or one of its synonyms is an **exact** name/synonym of a term
in a downloaded reference ontology, that term (and the databases it cross-references)
gives a candidate id. These are surfaced as yellow "predicted" cells on the
reference-review page and written to a predicted-SSSOM file with a
``semapv:LexicalMatching`` justification — a curator still confirms each one.

Data source
-----------
Predictions are computed from compact per-database *index* TSVs built by
``scripts/fetch_databases.py`` from freely-redistributable ontologies (MONDO, DOID,
...) and stored under ``data/2-databases/`` for local version control. Each index
row is one ontology term with its label, exact synonyms, and the ids it
cross-references in each target database. MONDO is the primary hub: a single MONDO
term xrefs SNOMED/DOID/NCI/ICD-10/Orphanet/OMIM/UMLS/MeSH, so matching a disease
name to MONDO can fill nine of the ten columns at once. OMOP is OHDSI-specific and
is not carried by these ontologies; predicting it requires the licensed Athena
vocabulary, which the user must supply (see ``data/2-databases/README.md``).

Nothing here writes to the ontology or the network; it only reads the index files
and the disease list and returns candidate mappings.
"""
from __future__ import annotations

import csv
import datetime
import re
import unicodedata
from pathlib import Path

from .xref_registry import PREFIX, XREF_DATABASES

# Target database keys, in registry order. ``omop`` has no free index source, so it
# never gets a prediction unless the user supplies an OMOP index (handled generically
# by the same column loop below).
TARGET_DBS = [d["key"] for d in XREF_DATABASES if d.get("review")]

# Columns of a per-database index TSV (``data/2-databases/<db>.index.tsv``). The
# first three describe the ontology term; the rest hold the ``;``-joined ids that
# term cross-references in each target database (its own column holds its own id).
INDEX_COLS = ["id", "label", "synonyms"] + TARGET_DBS

DEFAULT_INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "2-databases"
# Curated list of ARI synonyms that actually name a *different* disease and must be
# ignored when matching (see scripts/report_synonyms.py). Absent file -> no blocklist.
DEFAULT_BLOCKLIST_PATH = Path(__file__).resolve().parent.parent / "mappings" / "ari.synonym_blocklist.tsv"


# --------------------------------------------------------------------------- text
def normalize(text: str) -> str:
    """Fold a name to its exact-match key.

    Conservative on purpose — "exact name or synonym match" should not collapse
    genuinely different concepts. We only: Unicode-normalize (NFKD, drop combining
    marks so "Sjögren" == "Sjogren"), casefold, turn any run of non-alphanumeric
    characters into a single space, and trim. No stemming, no roman-numeral or
    number folding, no synonym expansion.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold()
    t = re.sub(r"[^0-9a-z]+", " ", t)
    return t.strip()


def _split_ids(cell: str) -> list[str]:
    return [x.strip() for x in (cell or "").split(";") if x.strip()]


# ------------------------------------------------------------------------- indexes
class LexicalIndex:
    """Normalized name/synonym -> reference-ontology records, for one index file.

    A *record* is ``{"id", "label", "by_db": {db_key: [ids]}}``. One normalized
    name can map to several records (rare homonyms across the ontology); callers
    decide how to treat ambiguity.
    """

    def __init__(self, source: str):
        self.source = source          # e.g. "mondo" — which index this came from
        self.by_name: dict[str, list[dict]] = {}
        self.terms = 0

    def add(self, record: dict, names: list[str]) -> None:
        self.terms += 1
        seen = set()
        for n in names:
            key = normalize(n)
            if not key or key in seen:
                continue
            seen.add(key)
            self.by_name.setdefault(key, []).append(record)

    def lookup(self, name: str) -> list[dict]:
        return self.by_name.get(normalize(name), [])


def load_index(path: str | Path, source: str | None = None) -> LexicalIndex:
    """Load one ``<db>.index.tsv`` into a :class:`LexicalIndex`."""
    path = Path(path)
    src = source or path.stem.split(".")[0]
    idx = LexicalIndex(src)
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            by_db: dict[str, list[str]] = {}
            for db in TARGET_DBS:
                ids = _split_ids(row.get(db, ""))
                if ids:
                    by_db[db] = ids
            record = {"id": row.get("id", ""), "label": row.get("label", ""), "by_db": by_db}
            names = [row.get("label", "")] + _split_synonyms(row.get("synonyms", ""))
            idx.add(record, names)
    return idx


def _split_synonyms(cell: str) -> list[str]:
    return [s.strip() for s in (cell or "").split(" | ") if s.strip()]


def load_indexes(index_dir: str | Path = DEFAULT_INDEX_DIR) -> list[LexicalIndex]:
    """Load every ``*.index.tsv`` in ``index_dir`` (missing dir -> no indexes)."""
    index_dir = Path(index_dir)
    if not index_dir.is_dir():
        return []
    out = []
    for p in sorted(index_dir.glob("*.index.tsv")):
        try:
            out.append(load_index(p))
        except (OSError, csv.Error):
            continue
    return out


# The index files are multi-MB, so cache them per directory and only reload when a
# file's mtime/size changes (e.g. after ``fetch_databases.py`` rebuilds them).
_INDEX_CACHE: dict[str, tuple] = {}


def get_indexes(index_dir: str | Path = DEFAULT_INDEX_DIR) -> list[LexicalIndex]:
    """Load (and cache) the indexes in ``index_dir``, refreshing on file change."""
    index_dir = Path(index_dir)
    sig = tuple(sorted((p.name, p.stat().st_mtime, p.stat().st_size)
                       for p in index_dir.glob("*.index.tsv"))) if index_dir.is_dir() else ()
    key = str(index_dir)
    cached = _INDEX_CACHE.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    idxs = load_indexes(index_dir)
    _INDEX_CACHE[key] = (sig, idxs)
    return idxs


def load_synonym_blocklist(path: str | Path = DEFAULT_BLOCKLIST_PATH) -> dict[str, set[str]]:
    """Load the mis-curated-synonym blocklist -> ``{ari_id: {normalized synonym}}``.

    TSV with an ``ari_id``/``synonym`` header (extra columns and ``#`` comments
    ignored). Synonyms here name a *different* disease than their ARI record and are
    skipped during matching. Missing file -> empty blocklist.
    """
    path = Path(path)
    out: dict[str, set[str]] = {}
    if not path.is_file():
        return out
    cols = None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if cols is None:
            cols = parts
            continue
        row = dict(zip(cols, parts))
        ari, syn = row.get("ari_id", "").strip(), row.get("synonym", "")
        if ari and syn.strip():
            out.setdefault(ari, set()).add(normalize(syn))
    return out


# ------------------------------------------------------------------------- predict
def predict_for_disease(disease: dict, indexes: list[LexicalIndex],
                        target_dbs: list[str] | None = None,
                        blocklist: dict[str, set[str]] | None = None) -> list[dict]:
    """Candidate ids for one disease's currently-blank target-database cells.

    ``disease`` = ``{"ari_id", "name", "synonyms": [...], "existing": {db: [ids]}}``.
    A cell is only predicted when ``existing[db]`` is empty. Returns a list of
    prediction dicts (see :func:`predict_matches`), deduplicated by ``(db, id)``,
    each carrying the provenance of every index/name that produced it and a
    ``confidence`` of ``"high"`` (label-anchored) or ``"low"`` (synonym-only).

    The disease's **label is its identity anchor**: if the label exactly matches any
    reference term, only those term(s) — and the databases they cross-reference —
    are used. Synonyms are trusted only when the label matches nothing (a disease
    known here solely by a synonym, e.g. "Kawasaki disease"). This is deliberate: ARI
    synonym lists sometimes contain *associated* conditions rather than true
    name-variants (e.g. "Megaloblastic anemia" under "Autoimmune gastritis"), and
    matching those would link the disease to an unrelated concept. ``blocklist``
    (``{ari_id: {normalized synonym}}`` from :func:`load_synonym_blocklist`) drops
    known mis-curated synonyms outright before matching.
    """
    target_dbs = target_dbs or TARGET_DBS
    label = disease.get("name", "")
    label_norm = normalize(label)
    existing = disease.get("existing") or {}
    blank = [db for db in target_dbs if not (existing.get(db))]
    if not blank:
        return []
    blocked = (blocklist or {}).get(disease.get("ari_id") or "", set())

    # Anchor on the label; fall back to synonyms only when the label matches nothing.
    matches = []   # (record, matched_text, field, source)
    for idx in indexes:
        for rec in idx.lookup(label):
            matches.append((rec, label, "label", idx.source))
    anchored = bool(matches)
    if not anchored:
        for syn in (disease.get("synonyms") or []):
            if not syn or normalize(syn) == label_norm or normalize(syn) in blocked:
                continue
            for idx in indexes:
                for rec in idx.lookup(syn):
                    matches.append((rec, syn, "synonym", idx.source))

    # (db, id) -> prediction, so several matches for the same id merge their evidence.
    found: dict[tuple[str, str], dict] = {}
    for rec, matched, field, source in matches:
        for db in blank:
            # A record supplies ``db`` either as its own id (same ontology) or
            # through a cross-reference it carries to ``db``.
            for ident in rec["by_db"].get(db, []):
                key = (db, ident)
                pred = found.get(key)
                if pred is None:
                    pred = found[key] = {
                        "ari_id": disease.get("ari_id"),
                        "subject_label": disease.get("name", ""),
                        "db": db,
                        "prefix": PREFIX.get(db, db),
                        "id": ident,
                        # The concept label from the remote source, so the curator
                        # needn't open the resource (issue #42).
                        "object_label": rec["label"],
                        "match_field": field,
                        "confidence": "high" if anchored else "low",
                        "evidence": [],
                    }
                pred["evidence"].append({
                    "via": rec["id"], "source": source,
                    "matched": matched, "match_field": field,
                })
    return sorted(found.values(), key=lambda p: (target_dbs.index(p["db"]), p["id"]))


def predict_matches(diseases: list[dict], indexes: list[LexicalIndex] | None = None,
                    index_dir: str | Path = DEFAULT_INDEX_DIR,
                    target_dbs: list[str] | None = None,
                    blocklist: dict[str, set[str]] | None = None,
                    blocklist_path: str | Path = DEFAULT_BLOCKLIST_PATH) -> list[dict]:
    """Predicted cross-references for a list of diseases (only blank cells).

    ``diseases`` items are the shape :func:`predict_for_disease` accepts, or the
    review-grid rows from ``OntologyService.get_xref_rows`` (name + ``ari_id`` +
    a list per db key); :func:`from_xref_rows` adapts the latter. Loads the index
    files from ``index_dir`` and the mis-curated-synonym blocklist from
    ``blocklist_path`` when not passed in. Returns a flat list of prediction dicts::

        {ari_id, subject_label, db, prefix, id, object_label, match_field,
         confidence, evidence}
    """
    if indexes is None:
        indexes = get_indexes(index_dir)
    if blocklist is None:
        blocklist = load_synonym_blocklist(blocklist_path)
    out = []
    for d in diseases:
        out.extend(predict_for_disease(d, indexes, target_dbs, blocklist))
    return out


def to_cells(predictions: list[dict]) -> list[dict]:
    """Compact predictions for the review grid (one row per predicted id).

    ``{ari_id, prefix, id, dbs, object_label, match_field}`` — the same shape the
    ``/api/v2/mappings`` endpoint returns, so the frontend handles predictions and
    curated judgments the same way. ``dbs`` lists every review column a prefix backs.
    """
    from .sssom_service import PREFIX_TO_DBS
    out = []
    for p in predictions:
        out.append({"ari_id": p.get("ari_id"), "prefix": p.get("prefix"), "id": p["id"],
                    "dbs": PREFIX_TO_DBS.get(p.get("prefix"), [p["db"]]),
                    "object_label": p.get("object_label", ""),
                    "match_field": p.get("match_field", ""),
                    "confidence": p.get("confidence", "high")})
    return out


def from_xref_rows(rows: list[dict], synonyms_by_iri: dict | None = None) -> list[dict]:
    """Adapt ``get_xref_rows`` output to :func:`predict_matches` input.

    The review grid rows carry one list per db key but not synonyms; pass
    ``synonyms_by_iri`` (``iri -> [synonym, ...]``) to match on synonyms too.
    """
    synonyms_by_iri = synonyms_by_iri or {}
    out = []
    for r in rows:
        out.append({
            "ari_id": r.get("ari_id"),
            "iri": r.get("iri"),
            "name": r.get("name", ""),
            "synonyms": synonyms_by_iri.get(r.get("iri"), []),
            "existing": {db: (r.get(db) or []) for db in TARGET_DBS},
        })
    return out


# ----------------------------------------------------------------------- SSSOM I/O
_SSSOM_COLS = ["subject_id", "subject_label", "predicate_id", "object_id",
               "object_label", "mapping_justification", "subject_match_field",
               "match_string", "confidence", "mapping_provider", "author_id", "mapping_date"]


def _sssom_header() -> str:
    # Mirror app/sssom_service.py's curie_map so the predicted file resolves the
    # same object prefixes; add the semapv justification vocabulary.
    from .sssom_service import CURIE_MAP
    lines = ["# curie_map:"]
    for k, v in CURIE_MAP.items():
        lines.append(f"#   {k}: {v}")
    lines += [
        "# mapping_set_id: https://diseases.autoimmuneregistry.org/mappings/ari.predicted.sssom.tsv",
        "# mapping_provider: https://www.autoimmuneregistry.org",
        "# mapping_set_title: ARI predicted disease cross-reference mappings (lexical)",
        "# mapping_tool: ARI-metadata-manager predict_service (exact name/synonym match)",
        "# license: https://creativecommons.org/publicdomain/zero/1.0/",
    ]
    return "\n".join(lines)


def build_predicted_sssom(predictions: list[dict], author: str = "ari:predict_service") -> str:
    """Render predictions as a predicted-SSSOM TSV (``semapv:LexicalMatching``)."""
    today = datetime.date.today().isoformat()
    lines = [_sssom_header(), "\t".join(_SSSOM_COLS)]
    for p in sorted(predictions, key=lambda x: (str(x.get("ari_id")), x["db"], x["id"])):
        subj = p.get("ari_id") or ""
        obj = f"{p.get('prefix', PREFIX.get(p['db'], p['db']))}:{p['id']}"
        ev = (p.get("evidence") or [{}])[0]
        row = [subj, p.get("subject_label", ""), "skos:exactMatch", obj,
               p.get("object_label", ""), "semapv:LexicalMatching",
               p.get("match_field", ""), ev.get("matched", ""),
               p.get("confidence", "high"), ev.get("via", ""), author, today]
        lines.append("\t".join(str(x) for x in row))
    return "\n".join(lines) + "\n"


def load_predictions(sssom_text: str = "") -> list[dict]:
    """Parse a predicted-SSSOM file into per-cell rows for the review grid.

    Returns ``{ari_id, prefix, dbs, id, object_label, match_field}`` so the
    frontend can pre-highlight blank cells yellow and show the concept name. ``dbs``
    lists every review column a prefix backs (SNOMED's prefix backs snomed+dxcode).
    """
    from .sssom_service import PREFIX_TO_DBS
    out, seen = [], set()
    if not (sssom_text and sssom_text.strip()):
        return out
    cols = None
    for line in sssom_text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if cols is None:
            cols = parts
            continue
        row = dict(zip(cols, parts))
        ari = row.get("subject_id", "")
        prefix, _, ident = row.get("object_id", "").partition(":")
        key = (ari, prefix, ident)
        if not prefix or not ident or key in seen:
            continue
        seen.add(key)
        out.append({"ari_id": ari, "prefix": prefix, "id": ident,
                    "dbs": PREFIX_TO_DBS.get(prefix, []),
                    "object_label": row.get("object_label", ""),
                    "match_field": row.get("subject_match_field", ""),
                    "confidence": row.get("confidence", "high")})
    return out
