"""Build SSSOM + biomappings-style equivalency files from confirmed cross-references.

When a curator marks a disease's database cross-reference as correct in the
reference-review page, those become exact-match mappings (ARI disease -> external
id). When they flag one as "needs change", that is recorded as a *negative*
mapping (exactMatch with a "Not" predicate modifier, biomappings-style). When
they judge that the database has no term for the disease at all, the mapping is
recorded against ``sssom:NoTermFound`` with the database named in
``object_source``. This module renders/accumulates an SSSOM TSV and a simpler
equivalencies TSV, and can read them back so the review page can pre-highlight
already-judged cells.
"""
import datetime

from .xref_registry import CURIE_BASES, PREFIX, normalize_id  # db key -> object/target prefix

# CURIE map for the SSSOM header: object-database prefixes come from the shared
# xref registry (so they can't drift from the review page / ontology); the subject
# (ARI) and SSSOM-vocabulary prefixes are specific to this file.
CURIE_MAP = {
    "ARI": "https://diseases.autoimmuneregistry.org/disease/ARI_",
    **CURIE_BASES,
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "semapv": "https://w3id.org/semapv/vocab/",
    "sssom": "https://w3id.org/sssom/",
    "orcid": "https://orcid.org/",
}

# SSSOM's sentinel object for "this database has no term for the subject"; the
# target database itself is carried in object_source. The bare local name is what
# the review page keys its per-cell "not in database" judgment on.
NO_TERM = "sssom:NoTermFound"
NO_TERM_ID = "NoTermFound"

SSSOM_COLS = ["subject_id", "subject_label", "predicate_id", "predicate_modifier",
              "object_id", "object_source", "mapping_justification", "author_id", "mapping_date"]
EQUIV_COLS = ["source_prefix", "source_id", "source_name", "relation",
              "target_prefix", "target_id", "type", "source"]

# prefix (as written in the object curie) -> review-page database key(s). One
# prefix can back more than one column (SNOMED is used for both snomed + dxcode),
# so the loader emits a judgment for every candidate key.
PREFIX_TO_DBS: dict[str, list[str]] = {}
for _db, _prefix in PREFIX.items():
    PREFIX_TO_DBS.setdefault(_prefix, []).append(_db)


def _object_curie(db, ident):
    return f"{PREFIX.get(db, db)}:{ident}"


def _sssom_header():
    lines = ["# curie_map:"]
    for k, v in CURIE_MAP.items():
        lines.append(f"#   {k}: {v}")
    lines += [
        "# mapping_set_id: https://diseases.autoimmuneregistry.org/mappings/ari.sssom.tsv",
        "# mapping_provider: https://www.autoimmuneregistry.org",
        "# mapping_set_title: ARI disease cross-reference mappings",
        "# license: https://creativecommons.org/publicdomain/zero/1.0/",
    ]
    return "\n".join(lines)


def _backfill_object_source(row: dict) -> dict:
    """object_source for rows written before that column existed.

    It is part of the SSSOM dedup key (two databases share the ``NoTermFound``
    object), so a stored row missing it would look new and be written twice. Every
    such row names a real object, so its own CURIE prefix is the source."""
    if not row.get("object_source"):
        row["object_source"] = row.get("object_id", "").partition(":")[0]
    return row


def _merge_tsv(existing, cols, new_rows, key_idx, header_block="", normalize=None):
    existing_data = []
    old_cols = None
    for line in (existing or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if old_cols is None:
            old_cols = parts                    # the file's own header row
            continue
        # Re-map every stored row through the header it was written under. A file
        # written before a column existed carries fewer fields, and appending it
        # raw under the current header would shift each value into the wrong column.
        row = dict(zip(old_cols, parts))
        if normalize:
            row = normalize(row)
        existing_data.append([row.get(c, "") for c in cols])
    keys = set(tuple(r[i] for i in key_idx) for r in existing_data)
    merged = list(existing_data)
    added = 0
    for r in new_rows:
        k = tuple(str(r[i]) for i in key_idx)
        if k in keys:
            continue
        keys.add(k); merged.append(r); added += 1
    out = []
    if header_block:
        out.append(header_block.rstrip("\n"))
    out.append("\t".join(cols))
    for r in merged:
        out.append("\t".join(str(x) for x in r))
    return "\n".join(out) + "\n", added


def build(confirmed, author, existing_sssom="", existing_equiv="", flagged=None, absent=None):
    """Accumulate confirmed (positive), flagged (negative) and absent cross-references.

    ``confirmed`` and ``flagged`` are lists of ``{ari_id, name, db, ids}``;
    ``absent`` is ``{ari_id, name, db}`` — the curator's judgment that ``db`` has
    no term for the disease at all. Negatives are written with a ``Not`` predicate
    modifier (SSSOM) and a ``skos:exactMatch`` relation tagged ``negative`` in the
    ``type`` column (equivalencies), matching the biomappings convention for
    incorrect mappings. Absences are written against SSSOM's ``NoTermFound``
    sentinel object, with the database in ``object_source``.
    """
    today = datetime.date.today().isoformat()
    sssom_rows, equiv_rows = [], []
    for items, modifier, eq_type in ((confirmed or [], "", "manual"),
                                     (flagged or [], "Not", "manual-negative")):
        for c in items:
            subj = c.get("ari_id") or ""
            name = c.get("name") or ""
            prefix = PREFIX.get(c["db"], c["db"])
            for ident in c.get("ids", []):
                # Guard the client-supplied id boundary. A stray placeholder must
                # never become a literal "PREFIX:null" row, and an id that already
                # carries its own prefix must not have it prepended a second time.
                ident_s = normalize_id(c["db"], ident)
                if not ident_s:
                    continue
                obj = _object_curie(c["db"], ident_s)
                sssom_rows.append([subj, name, "skos:exactMatch", modifier, obj, prefix,
                                   "semapv:ManualMappingCuration", author, today])
                equiv_rows.append(["ARI", (subj.split(":")[-1] if subj else ""), name,
                                   "skos:exactMatch", prefix, ident_s, eq_type, author])
    for c in (absent or []):
        subj = c.get("ari_id") or ""
        name = c.get("name") or ""
        prefix = PREFIX.get(c["db"], c["db"])
        sssom_rows.append([subj, name, "skos:exactMatch", "", NO_TERM, prefix,
                           "semapv:ManualMappingCuration", author, today])
        equiv_rows.append(["ARI", (subj.split(":")[-1] if subj else ""), name,
                           "skos:exactMatch", prefix, NO_TERM_ID, "manual-absent", author])
    # Dedup on (subject, predicate, modifier, object, object_source) so a positive
    # and a later negative for the same triple don't silently collapse into one
    # another — and so two databases' NoTermFound rows stay distinct.
    sssom, n1 = _merge_tsv(existing_sssom, SSSOM_COLS, sssom_rows, (0, 2, 3, 4, 5),
                           _sssom_header(), normalize=_backfill_object_source)
    equiv, n2 = _merge_tsv(existing_equiv, EQUIV_COLS, equiv_rows, (0, 1, 4, 5, 6))
    return {"sssom": sssom, "equiv": equiv, "added": max(n1, n2)}


def load_judgments(sssom_text="", equiv_text=""):
    """Parse stored mapping files into per-cell judgments for pre-highlighting.

    Returns a list of ``{ari_id, prefix, dbs, id, judgment}`` where ``judgment``
    is ``"positive"``, ``"negative"`` or ``"absent"`` (the whole database has no
    term, carried under the ``NoTermFound`` id) and ``dbs`` lists the review-page
    column keys the prefix maps to. SSSOM is canonical; the equivalencies file is
    used only as a fallback when no SSSOM is present.
    """
    out, seen = [], set()

    def _add(ari_id, prefix, ident, judgment):
        key = (ari_id, prefix, ident)
        if not prefix or not ident or key in seen:
            return
        seen.add(key)
        out.append({"ari_id": ari_id, "prefix": prefix, "id": ident,
                    "dbs": PREFIX_TO_DBS.get(prefix, []), "judgment": judgment})

    if sssom_text and sssom_text.strip():
        cols = None
        for line in sssom_text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if cols is None:
                cols = parts
                continue
            row = dict(zip(cols, parts))
            obj = row.get("object_id", "")
            if obj == NO_TERM:
                _add(row.get("subject_id", ""), row.get("object_source", ""), NO_TERM_ID, "absent")
                continue
            prefix, _, ident = obj.partition(":")
            judgment = "negative" if (row.get("predicate_modifier", "").strip().lower() == "not") else "positive"
            _add(row.get("subject_id", ""), prefix, ident, judgment)
        return out

    if equiv_text and equiv_text.strip():
        cols = None
        for line in equiv_text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if cols is None:
                cols = parts
                continue
            row = dict(zip(cols, parts))
            kind = row.get("type", "").lower()
            judgment = "absent" if "absent" in kind else "negative" if "negative" in kind else "positive"
            ari = row.get("source_id", "")
            _add(("ARI:" + ari) if ari and not ari.startswith("ARI:") else ari,
                 row.get("target_prefix", ""), row.get("target_id", ""), judgment)
    return out
