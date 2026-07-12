#!/usr/bin/env python3
"""Download reference ontologies and build the compact cross-reference indexes
used by ``app/predict_service`` (issue #42).

For each freely-redistributable source (MONDO, DOID, ...) this downloads the raw
OBO into ``data/2-databases/raw/`` (git-ignored — large) and distills it into
``data/2-databases/<db>.index.tsv``: one row per ontology term with its label,
exact synonyms, and the ids it cross-references in each target database. The index
files are small and are committed for local version control; the raw dumps are not.

MONDO is the hub — a single MONDO term xrefs SNOMED/DOID/NCI/ICD-10/Orphanet/OMIM/
UMLS/MeSH — so its index alone can predict nine of the ten target columns. OMOP is
OHDSI-specific and is not carried by these ontologies; see the README.

Usage:
    python scripts/fetch_databases.py                # download (if missing) + build
    python scripts/fetch_databases.py --offline      # build from already-downloaded raw
    python scripts/fetch_databases.py --only mondo doid
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "2-databases"
RAW_DIR = DATA_DIR / "raw"

# Freely-redistributable OBO sources. ``db`` is the owning target-database key
# (its own id lands in that column). Add more OBO ontologies here as needed.
SOURCES = {
    "mondo": {"url": "https://purl.obolibrary.org/obo/mondo.obo",
              "raw": "mondo.obo", "id_prefix": "MONDO"},
    "doid": {"url": "https://purl.obolibrary.org/obo/doid.obo",
             "raw": "doid.obo", "id_prefix": "DOID"},
}

# Import the shared column contract / normalization from the app so the builder and
# the reader can never drift. Falls back to a sys.path tweak when run as a script.
try:
    from app.predict_service import INDEX_COLS, TARGET_DBS
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.predict_service import INDEX_COLS, TARGET_DBS

# OBO xref / id prefix (upper-cased, before the first colon or date suffix) ->
# target-database key. Ontologies spell the same database several ways
# (SCTID / SNOMEDCT_US_2020_09_01, UMLS_CUI, NCIT / NCI), so match by startswith.
_PREFIX_RULES = [
    ("SCTID", "snomed"), ("SNOMEDCT", "snomed"), ("SNOMED", "snomed"),
    ("DOID", "doid"),
    ("MONDO", "mondo"),
    ("NCIT", "nci"), ("NCI", "nci"),
    ("ICD10CM", "icd10"),
    ("ORPHANET", "orphanet"), ("ORPHA", "orphanet"), ("ORDO", "orphanet"),
    ("OMIMPS", None), ("OMIM", "omim"),          # skip OMIM phenotypic-series ids
    ("UMLS", "umls"),
    ("MESH", "mesh"), ("MSH", "mesh"),
]


def _db_for_prefix(prefix: str) -> str | None:
    up = prefix.upper()
    for pat, db in _PREFIX_RULES:
        if up.startswith(pat):
            return db
    return None


def download(only: list[str] | None) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for db, cfg in SOURCES.items():
        if only and db not in only:
            continue
        dest = RAW_DIR / cfg["raw"]
        print(f"[{db}] downloading {cfg['url']} -> {dest}")
        req = urllib.request.Request(cfg["url"], headers={"User-Agent": "ARI-metadata-manager"})
        with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:  # noqa: S310 (trusted OBO PURLs)
            f.write(resp.read())
        print(f"[{db}] {dest.stat().st_size:,} bytes")


def _parse_synonym(line: str) -> str | None:
    """Return the text of an EXACT, non-deprecated, non-abbreviation synonym."""
    body = line[len("synonym:"):].strip()
    if not body.startswith('"'):
        return None
    end = body.find('"', 1)
    if end < 1:
        return None
    text = body[1:end]
    rest = body[end + 1:].strip().split("[")[0].split()
    scope = rest[0] if rest else ""
    stype = rest[1] if len(rest) > 1 else ""
    if scope != "EXACT" or stype in {"DEPRECATED", "ABBREVIATION"}:
        return None
    return text


def parse_obo(path: Path, id_prefix: str) -> list[dict]:
    """Distil one OBO file into index rows for terms of ``id_prefix``.

    Each row: ``{"id", "label", "synonyms": [...], "<db>": [ids...]}``. The term's
    own id also fills its owning-database column, so a MONDO term carries its MONDO
    id plus every xref it declares.
    """
    rows: list[dict] = []
    cur: dict | None = None
    obsolete = False

    def flush():
        nonlocal cur, obsolete
        if cur and not obsolete and cur.get("id", "").startswith(id_prefix + ":") and cur.get("label"):
            rows.append(cur)
        cur, obsolete = None, False

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line == "[Term]":
                flush()
                cur = {"synonyms": []}
                obsolete = False
                continue
            if line.startswith("[") and line.endswith("]"):
                flush()          # [Typedef] etc. — leave term context
                continue
            if cur is None:
                continue
            if line.startswith("id: "):
                ident = line[4:].strip()
                cur["id"] = ident
                db = _db_for_prefix(ident.split(":", 1)[0])
                if db:
                    cur.setdefault(db, []).append(ident.split(":", 1)[1])
            elif line.startswith("name: "):
                cur["label"] = line[6:].strip()
            elif line.startswith("is_obsolete: true"):
                obsolete = True
            elif line.startswith("synonym: "):
                syn = _parse_synonym(line)
                if syn:
                    cur["synonyms"].append(syn)
            elif line.startswith("xref: "):
                x = line[6:].strip().split()[0].rstrip(",")
                prefix, _, ident = x.partition(":")
                db = _db_for_prefix(prefix)
                if db and ident:
                    cur.setdefault(db, []).append(ident)
    flush()
    return rows


def write_index(db: str, rows: list[dict]) -> Path:
    out = DATA_DIR / f"{db}.index.tsv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(INDEX_COLS) + "\n")
        for r in rows:
            cells = [r.get("id", ""), r.get("label", ""), " | ".join(r.get("synonyms", []))]
            for col in TARGET_DBS:
                vals = r.get(col, [])
                # de-dupe preserving order
                seen, uniq = set(), []
                for v in vals:
                    if v not in seen:
                        seen.add(v)
                        uniq.append(v)
                cells.append(";".join(uniq))
            f.write("\t".join(cells) + "\n")
    return out


def build(only: list[str] | None) -> None:
    for db, cfg in SOURCES.items():
        if only and db not in only:
            continue
        raw = RAW_DIR / cfg["raw"]
        if not raw.exists():
            print(f"[{db}] raw file missing ({raw}); run without --offline first", file=sys.stderr)
            continue
        print(f"[{db}] parsing {raw.name} ...")
        rows = parse_obo(raw, cfg["id_prefix"])
        out = write_index(db, rows)
        rel = out.relative_to(DATA_DIR.parent.parent)
        print(f"[{db}] wrote {rel} ({len(rows):,} terms, {out.stat().st_size:,} bytes)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="build from already-downloaded raw files")
    ap.add_argument("--only", nargs="*", metavar="DB", help="restrict to these sources (e.g. mondo doid)")
    args = ap.parse_args(argv)
    only = args.only or None
    if only:
        unknown = [d for d in only if d not in SOURCES]
        if unknown:
            ap.error(f"unknown source(s): {', '.join(unknown)}; known: {', '.join(SOURCES)}")
    if not args.offline:
        download(only)
    build(only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
