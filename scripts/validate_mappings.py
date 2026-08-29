#!/usr/bin/env python
"""Validate the accumulated mapping files.

Run in CI so a published mapping set cannot drift into something a downstream
consumer cannot read. Checks the things this app is responsible for and that
have actually broken here before:

* every CURIE prefix used in the file is declared in the header's ``curie_map``
  (``author_id`` used ``github:`` with no declaration, so the file did not
  validate);
* no prefix is doubled (``MONDO:MONDO:0014523`` reached the published file once);
* every ``mapping_date`` is ISO-8601 with a timezone;
* no (subject, object, object_source) pair carries two *live* rows — a reversal
  must leave the withdrawn row marked as superseded.

Exits non-zero with one line per problem.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime  # noqa: E402

from app.sssom_service import SUPERSEDED_PREFIX  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SSSOM = ROOT / "mappings" / "ari.sssom.tsv"


def _parse(text):
    curie_map, rows, header = {}, [], None
    for line in text.splitlines():
        if line.startswith("#   ") and ":" in line:
            k, _, v = line[4:].partition(":")
            curie_map[k.strip()] = v.strip()
            continue
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if header is None:
            header = parts
            continue
        rows.append(dict(zip(header, parts)))
    return curie_map, rows


def validate(text) -> list[str]:
    curie_map, rows = _parse(text)
    problems = []
    live: dict = {}
    for n, row in enumerate(rows, start=2):
        where = f"row {n} ({row.get('subject_id', '?')} -> {row.get('object_id', '?')})"

        for col in ("subject_id", "object_id", "author_id"):
            value = row.get(col, "")
            if not value or ":" not in value:
                continue
            prefix, _, local = value.partition(":")
            if prefix not in curie_map:
                problems.append(f"{where}: {col} prefix {prefix!r} is not in the curie_map")
            if local.startswith(prefix + ":"):
                problems.append(f"{where}: {col} has a doubled prefix ({value})")

        date = row.get("mapping_date", "")
        try:
            if datetime.fromisoformat(date).tzinfo is None:
                problems.append(f"{where}: mapping_date {date!r} has no timezone")
        except ValueError:
            problems.append(f"{where}: mapping_date {date!r} is not ISO-8601")

        if not row.get("comment", "").startswith(SUPERSEDED_PREFIX):
            pair = (row.get("subject_id"), row.get("object_id"), row.get("object_source"))
            if pair in live:
                problems.append(
                    f"{where}: contradicts row {live[pair]} for the same pair and "
                    f"neither is marked superseded")
            live[pair] = n
    return problems


def main() -> int:
    if not SSSOM.exists():
        print(f"{SSSOM} not found — nothing to validate.")
        return 0
    problems = validate(SSSOM.read_text(encoding="utf-8"))
    for p in problems:
        print(f"{SSSOM.name}: {p}")
    if problems:
        print(f"\n{len(problems)} problem(s) in the published mapping set.")
        return 1
    print(f"{SSSOM.name} is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
