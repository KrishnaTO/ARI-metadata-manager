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
import os
import re

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
    # author_id defaults to github:<login>. Without this the CURIE cannot be
    # expanded by a consumer and the file does not validate as SSSOM.
    "github": "https://github.com/",
}

# The licence asserted on every mapping file this app writes. It is a rights
# declaration about contributed work, so it is configurable rather than baked in,
# and `GET /api/v2/settings` reports it so the publish dialog can show a curator
# what they are agreeing to before they submit.
MAPPING_LICENSE = os.environ.get(
    "MAPPING_LICENSE", "https://creativecommons.org/publicdomain/zero/1.0/")

ORCID_RE = re.compile(r"^(?:https?://orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$")


def orcid_curie(value: str) -> str:
    """``orcid:0000-0002-1825-0097`` from a bare ORCID or an orcid.org URL.

    Raises on anything else: an unvalidated string went straight into the
    published ``author_id`` column, where a typo is permanent and unattributable.
    """
    m = ORCID_RE.match((value or "").strip())
    if not m:
        raise ValueError(
            f"{value!r} is not an ORCID. Expected 16 digits as 0000-0000-0000-0000 "
            f"(the last character may be X).")
    return "orcid:" + m.group(1)

# SSSOM's sentinel object for "this database has no term for the subject"; the
# target database itself is carried in object_source. The bare local name is what
# the review page keys its per-cell "not in database" judgment on.
NO_TERM = "sssom:NoTermFound"
NO_TERM_ID = "NoTermFound"

# `comment` carries the supersession marker: when a judgment is reversed, the
# row it replaced is annotated in place rather than merely being outvoted by
# whatever the reader's ordering happens to be. It is a standard SSSOM slot, so
# the file still validates.
SSSOM_COLS = ["subject_id", "subject_label", "predicate_id", "predicate_modifier",
              "object_id", "object_source", "mapping_justification", "author_id",
              "mapping_date", "comment"]
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
        f"# license: {MAPPING_LICENSE}",
    ]
    return "\n".join(lines)


def _normalize_stored_row(row: dict) -> dict:
    """Bring a row written under an older header up to the current one.

    ``object_source`` is part of the SSSOM dedup key (two databases share the
    ``NoTermFound`` object), so a stored row missing it would look new and be
    written twice. Every such row names a real object, so its own CURIE prefix
    is the source.

    ``mapping_date`` was a bare local date. Widening it to midnight UTC keeps
    what was actually known — the day — while making the column parseable as
    ISO-8601 with an offset, and orders legacy rows before any judgment made
    since, which is correct.
    """
    if not row.get("object_source"):
        row["object_source"] = row.get("object_id", "").partition(":")[0]
    date = row.get("mapping_date", "")
    if len(date) == 10 and date.count("-") == 2:
        row["mapping_date"] = date + "T00:00:00+00:00"
    # A prefix doubled at write time (`MONDO:MONDO:0014523`) reached the
    # published file before ids were canonicalised. The prefix is prepended on
    # read, so the stored local part must not carry it.
    prefix, _, local = row.get("object_id", "").partition(":")
    if prefix and local.startswith(prefix + ":"):
        row["object_id"] = prefix + ":" + local[len(prefix) + 1:]
    return row


# The supersession marker written into a withdrawn row's `comment`. Reading it
# back is how `load_judgments` knows which of two contradictory rows is live,
# without depending on timestamps that may not separate two judgments made the
# same second.
SUPERSEDED_PREFIX = "Superseded by the "

# SSSOM column indexes used by the supersession pass.
_SUBJ, _MOD, _OBJ, _SRC, _AUTHOR, _DATE, _COMMENT = 0, 3, 4, 5, 7, 8, 9


def _reconcile_judgments(existing_rows, new_rows):
    """Fold ``new_rows`` into ``existing_rows`` in place, honouring reversals.

    A pair (subject, object, object_source) carries at most two rows — the
    positive judgment and the negative one — and whichever was asserted most
    recently is the current state. Merging on the dedup key alone could not
    express a reversal: it appended any row with a new key and marked nothing,
    so judgments only ever accumulated and a re-confirmation after a flag was
    dropped entirely (its key already existed) while the flag kept winning.

    So for each incoming judgment:

    * a row asserting the **same** verdict is refreshed to the new author and
      date, and its supersession note cleared — it is the live judgment again;
    * the row asserting the **opposite** verdict is annotated as superseded, so
      a consumer reading the published file can see which of two contradictory
      rows was withdrawn without reimplementing the ordering.

    Returns the incoming rows that still need appending.
    """
    by_pair: dict = {}
    for row in existing_rows:
        by_pair.setdefault((row[_SUBJ], row[_OBJ], row[_SRC]), []).append(row)

    append = []
    for new in new_rows:
        pair = by_pair.setdefault((new[_SUBJ], new[_OBJ], new[_SRC]), [])
        same = next((r for r in pair if r[_MOD] == new[_MOD]), None)
        if same is None:
            pair.append(new)
            append.append(new)
        else:
            same[_AUTHOR], same[_DATE], same[_COMMENT] = new[_AUTHOR], new[_DATE], ""
        for other in pair:
            if other[_MOD] != new[_MOD]:
                other[_COMMENT] = (
                    f"{SUPERSEDED_PREFIX}{'negative' if new[_MOD] else 'positive'} "
                    f"judgment of {new[_AUTHOR]} on {new[_DATE]}.")
    return append


def _merge_tsv(existing, cols, new_rows, key_idx, header_block="", normalize=None,
               reconcile=None):
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
    if reconcile:
        # The judgment files are an event log with a current-state projection,
        # so reversals are resolved here rather than by the dedup key alone.
        new_rows = reconcile(existing_data, new_rows)
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
    # UTC with an offset, to the second. Day-granular local dates could not
    # order two judgments made the same afternoon, which is exactly when a
    # correction happens, and they could not be correlated with the provenance
    # ledger without knowing the server's timezone.
    today = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
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
                                   "semapv:ManualMappingCuration", author, today, ""])
                equiv_rows.append(["ARI", (subj.split(":")[-1] if subj else ""), name,
                                   "skos:exactMatch", prefix, ident_s, eq_type, author])
    for c in (absent or []):
        subj = c.get("ari_id") or ""
        name = c.get("name") or ""
        prefix = PREFIX.get(c["db"], c["db"])
        sssom_rows.append([subj, name, "skos:exactMatch", "", NO_TERM, prefix,
                           "semapv:ManualMappingCuration", author, today, ""])
        equiv_rows.append(["ARI", (subj.split(":")[-1] if subj else ""), name,
                           "skos:exactMatch", prefix, NO_TERM_ID, "manual-absent", author])
    # Dedup on (subject, predicate, modifier, object, object_source) so a positive
    # and a later negative for the same triple don't silently collapse into one
    # another — and so two databases' NoTermFound rows stay distinct.
    sssom, n1 = _merge_tsv(existing_sssom, SSSOM_COLS, sssom_rows, (0, 2, 3, 4, 5),
                           _sssom_header(), normalize=_normalize_stored_row,
                           reconcile=_reconcile_judgments)
    equiv, n2 = _merge_tsv(existing_equiv, EQUIV_COLS, equiv_rows, (0, 1, 4, 5, 6))
    # `added` counts the SSSOM rows, which is the file the judgments live in.
    # It used to be max(n1, n2) — an estimate presented as a fact in the PR body.
    return {"sssom": sssom, "equiv": equiv, "added": n1}


def load_judgments(sssom_text="", equiv_text=""):
    """Parse stored mapping files into per-cell judgments for pre-highlighting.

    Returns a list of ``{ari_id, prefix, dbs, id, judgment, author, date}`` where
    ``judgment`` is ``"positive"``, ``"negative"`` or ``"absent"`` (the whole
    database has no term, carried under the ``NoTermFound`` id) and ``dbs`` lists
    the review-page column keys the prefix maps to. SSSOM is canonical; the
    equivalencies file is used only as a fallback when no SSSOM is present.

    The mapping set is an event log: a judgment and its later correction differ
    in ``predicate_modifier``, so both rows are stored. This is the projection
    to current state, and **the most recent judgment wins**. It used to key on
    ``(ari_id, prefix, id)`` and keep whichever row came first in the file —
    which is append order, so the *older* judgment always won and a correction
    was silently discarded: the review page showed a withdrawn mapping as
    confirmed forever, and the next curator might confirm it again.
    """
    latest: dict = {}

    def _add(ari_id, prefix, ident, judgment, author="", date="", order=0,
             superseded=False):
        key = (ari_id, prefix, ident)
        if not prefix or not ident:
            return
        # Ranked on (not superseded, mapping_date, file position). An explicit
        # supersession marker beats any timestamp: two judgments made the same
        # second carry the same date, and file position alone cannot say which
        # verdict is live once a row has been refreshed in place. Date then
        # position covers rows written before the marker existed.
        rank = (0 if superseded else 1, date, order)
        prev = latest.get(key)
        if prev is not None and prev["_rank"] > rank:
            return
        latest[key] = {"ari_id": ari_id, "prefix": prefix, "id": ident,
                       "dbs": PREFIX_TO_DBS.get(prefix, []), "judgment": judgment,
                       "author": author, "date": date, "_rank": rank}

    def _finish():
        return [{k: v for k, v in r.items() if k != "_rank"} for r in latest.values()]

    if sssom_text and sssom_text.strip():
        cols = None
        for order, line in enumerate(sssom_text.splitlines()):
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if cols is None:
                cols = parts
                continue
            row = dict(zip(cols, parts))
            obj = row.get("object_id", "")
            author = row.get("author_id", "")
            date = row.get("mapping_date", "")
            withdrawn = row.get("comment", "").startswith(SUPERSEDED_PREFIX)
            if obj == NO_TERM:
                _add(row.get("subject_id", ""), row.get("object_source", ""),
                     NO_TERM_ID, "absent", author, date, order, withdrawn)
                continue
            prefix, _, ident = obj.partition(":")
            judgment = ("negative"
                        if row.get("predicate_modifier", "").strip().lower() == "not"
                        else "positive")
            _add(row.get("subject_id", ""), prefix, ident, judgment, author, date,
                 order, withdrawn)
        return _finish()

    if equiv_text and equiv_text.strip():
        cols = None
        for order, line in enumerate(equiv_text.splitlines()):
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if cols is None:
                cols = parts
                continue
            row = dict(zip(cols, parts))
            kind = row.get("type", "").lower()
            judgment = ("absent" if "absent" in kind
                        else "negative" if "negative" in kind else "positive")
            ari = row.get("source_id", "")
            # No date column here, so file position alone orders these.
            _add(("ARI:" + ari) if ari and not ari.startswith("ARI:") else ari,
                 row.get("target_prefix", ""), row.get("target_id", ""), judgment,
                 row.get("source", ""), "", order)
    return _finish()
