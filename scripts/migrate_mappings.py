#!/usr/bin/env python
"""Rewrite the accumulated mapping files under the current SSSOM header.

One-off, idempotent. The published `mappings/ari.sssom.tsv` predates three
things and does not validate as a result (issue #116):

* the `github:` prefix its own `author_id` column uses is not in the curie_map;
* `mapping_date` is a bare local date with no timezone;
* there is no `object_source` column, nor the `comment` column that carries a
  supersession marker.

Running every row back through `sssom_service.build()` with no new judgments
re-emits the file under the current header and columns. No judgment changes:
dates widen to midnight UTC, which is exactly what the bare date meant.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import sssom_service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SSSOM = ROOT / "mappings" / "ari.sssom.tsv"
EQUIV = ROOT / "mappings" / "ari.equivalencies.tsv"


def _backfill_supersessions(text: str) -> str:
    """Mark the older of two contradictory rows for the same pair.

    Judgments only ever accumulated, so a mapping that was confirmed and later
    flagged left two live rows asserting that the pair both is and is not an
    exact match, with nothing saying which was withdrawn. The dates say which
    came first; this writes that into the earlier row's ``comment``, the same
    marker `sssom_service` now writes as corrections are made.
    """
    lines = text.splitlines()
    head = [ln for ln in lines if ln.startswith("#")]
    body = [ln for ln in lines if not ln.startswith("#") and ln.strip()]
    cols, rows = body[0].split("\t"), [ln.split("\t") for ln in body[1:]]
    i = {c: n for n, c in enumerate(cols)}

    by_pair: dict = {}
    for row in rows:
        pair = (row[i["subject_id"]], row[i["object_id"]], row[i["object_source"]])
        by_pair.setdefault(pair, []).append(row)

    marked = 0
    for pair, group in by_pair.items():
        if len({r[i["predicate_modifier"]] for r in group}) < 2:
            continue                       # no contradiction to resolve
        group.sort(key=lambda r: r[i["mapping_date"]])
        winner = group[-1]
        for row in group[:-1]:
            if row[i["comment"]]:
                continue
            verdict = "negative" if winner[i["predicate_modifier"]] else "positive"
            row[i["comment"]] = (f"{sssom_service.SUPERSEDED_PREFIX}{verdict} judgment "
                                 f"of {winner[i['author_id']]} on {winner[i['mapping_date']]}.")
            marked += 1
    if marked:
        print(f"Marked {marked} superseded row(s).")
    out_rows = ["\t".join(r) for r in rows]
    header_row = "\t".join(cols)
    return "\n".join(head + [header_row] + out_rows) + "\n"


def main() -> int:
    sssom_text = SSSOM.read_text(encoding="utf-8") if SSSOM.exists() else ""
    equiv_text = EQUIV.read_text(encoding="utf-8") if EQUIV.exists() else ""
    if not sssom_text:
        print("No mapping file to migrate.")
        return 0

    out = sssom_service.build([], "", sssom_text, equiv_text)
    out["sssom"] = _backfill_supersessions(out["sssom"])
    changed = []
    if out["sssom"] != sssom_text:
        SSSOM.write_text(out["sssom"], encoding="utf-8", newline="\n")
        changed.append(SSSOM.name)
    if out["equiv"] != equiv_text:
        EQUIV.write_text(out["equiv"], encoding="utf-8", newline="\n")
        changed.append(EQUIV.name)
    print(f"Rewrote: {', '.join(changed)}" if changed else "Already current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
