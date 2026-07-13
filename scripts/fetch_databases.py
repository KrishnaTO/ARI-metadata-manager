#!/usr/bin/env python3
"""Download reference databases and build the compact cross-reference indexes
used by ``app/predict_service`` (issue #42).

For each freely-redistributable source this downloads the raw release into
``data/2-databases/raw/`` (git-ignored — large) and distils it into
``data/2-databases/<db>.index.tsv``: one row per term with its label, exact
synonyms, and the ids it cross-references in each target database. The index files
are small and are committed for local version control; the raw dumps are not.

Sources and formats:
  mondo, doid  OBO ontologies. MONDO is the hub — one term xrefs SNOMED/DOID/NCI/
               ICD-10/Orphanet/OMIM/UMLS/MeSH, so it alone predicts nine columns.
  ncit         NCI Thesaurus OBO, filtered to disease semantic types; also carries
               its UMLS CUI (P207) as a umls xref.
  mesh         NLM MeSH descriptor XML, filtered to the Diseases (C*) and Mental
               Disorders (F03*) tree categories.
  orphanet     Orphanet nomenclature XML (en_product1), with its exact-mapped
               ICD-10 / OMIM / UMLS / MeSH / SNOMED cross-references.

MONDO/DOID/NCI/MeSH/Orphanet all match a disease directly on their own labels and
synonyms — independent lexical sources, not just MONDO's xref view. OMOP is
OHDSI-specific and is carried by none of them; see the README.

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

# Freely-redistributable sources. The dict key is the owning target-database key
# (its own id lands in that column). ``format`` selects the parser.
SOURCES = {
    "mondo": {"url": "https://purl.obolibrary.org/obo/mondo.obo",
              "raw": "mondo.obo", "format": "obo", "id_prefix": "MONDO"},
    "doid": {"url": "https://purl.obolibrary.org/obo/doid.obo",
             "raw": "doid.obo", "format": "obo", "id_prefix": "DOID"},
    "ncit": {"url": "https://purl.obolibrary.org/obo/ncit.obo",
             "raw": "ncit.obo", "format": "obo", "id_prefix": "NCIT", "disease_only": True},
    "mesh": {"url": "https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.xml",
             "raw": "mesh_desc2026.xml", "format": "mesh-xml"},
    "orphanet": {"url": "https://www.orphadata.com/data/xml/en_product1.xml",
                 "raw": "orphanet_product1.xml", "format": "orphanet-xml"},
}

# NCI Thesaurus semantic types (property NCIT:P106) that denote a disease/disorder,
# used to keep the ncit index disease-focused rather than all ~180k NCIt concepts.
NCIT_DISEASE_SEMANTIC_TYPES = {
    "Disease or Syndrome", "Neoplastic Process", "Mental or Behavioral Dysfunction",
    "Congenital Abnormality", "Acquired Abnormality", "Anatomical Abnormality",
    "Experimental Model of Disease",
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


def _property_value(line: str) -> tuple[str, str]:
    """Parse ``property_value: PROP "value" type`` -> ``(PROP, value)`` (``("","")`` if not)."""
    body = line[len("property_value:"):].strip()
    parts = body.split(None, 1)
    if len(parts) != 2 or not parts[1].startswith('"'):
        return "", ""
    end = parts[1].find('"', 1)
    if end < 1:
        return "", ""
    return parts[0], parts[1][1:end]


def parse_obo(path: Path, id_prefix: str, disease_only: bool = False) -> list[dict]:
    """Distil one OBO file into index rows for terms of ``id_prefix``.

    Each row: ``{"id", "label", "synonyms": [...], "<db>": [ids...]}``. The term's
    own id also fills its owning-database column, so a MONDO term carries its MONDO
    id plus every xref it declares. ``disease_only`` keeps only NCIt terms whose
    semantic type (NCIT:P106) is a disease/disorder — the NCIt release is otherwise
    ~180k mostly-non-disease concepts. NCIt's UMLS CUI (NCIT:P207) is harvested as a
    umls cross-reference.
    """
    rows: list[dict] = []
    cur: dict | None = None
    obsolete = False
    sem_types: set[str] = set()

    def flush():
        nonlocal cur, obsolete, sem_types
        keep = cur and not obsolete and cur.get("id", "").startswith(id_prefix + ":") and cur.get("label")
        if keep and disease_only:
            keep = bool(sem_types & NCIT_DISEASE_SEMANTIC_TYPES)
        if keep:
            rows.append(cur)
        cur, obsolete, sem_types = None, False, set()

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if line == "[Term]":
                flush()
                cur = {"synonyms": []}
                obsolete = False
                sem_types = set()
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
            elif line.startswith("property_value: NCIT:P106 "):
                _, val = _property_value(line)
                if val:
                    sem_types.add(val)
            elif line.startswith("property_value: NCIT:P207 "):
                _, val = _property_value(line)       # UMLS CUI
                if val:
                    cur.setdefault("umls", []).append(val)
    flush()
    return rows


def _local_tag(elem) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def parse_mesh_xml(path: Path, keep_tree_prefixes: tuple[str, ...] = ("C", "F03")) -> list[dict]:
    """Distil NLM MeSH descriptor XML into index rows (Diseases + Mental Disorders).

    Keeps descriptors with a tree number under ``keep_tree_prefixes`` (C = Diseases,
    F03 = Mental Disorders). Label = DescriptorName; synonyms = the terms of the
    *preferred* concept only (non-preferred concepts are narrower and would make an
    unsafe exact match). No cross-references (MeSH descriptors carry none to the
    other target databases); only the ``mesh`` column is filled.
    """
    import xml.etree.ElementTree as ET

    rows: list[dict] = []
    for _event, elem in ET.iterparse(path, events=("end",)):
        if _local_tag(elem) != "DescriptorRecord":
            continue
        ui = elem.findtext("DescriptorUI") or ""
        name = elem.findtext("DescriptorName/String") or ""
        trees = [t.text or "" for t in elem.iterfind("TreeNumberList/TreeNumber")]
        if ui and name and any(t.startswith(keep_tree_prefixes) for t in trees):
            synonyms = []
            for concept in elem.iterfind("ConceptList/Concept"):
                if concept.get("PreferredConceptYN") != "Y":
                    continue
                for term in concept.iterfind("TermList/Term/String"):
                    if term.text and term.text != name:
                        synonyms.append(term.text)
            rows.append({"id": f"MESH:{ui}", "label": name, "synonyms": synonyms, "mesh": [ui]})
        elem.clear()
    return rows


# Orphanet en_product1 external-reference source label -> target-database key.
_ORPHA_SOURCE_DB = {"ICD-10": "icd10", "OMIM": "omim", "UMLS": "umls",
                    "MeSH": "mesh", "SNOMED CT": "snomed"}


def parse_orphanet_xml(path: Path) -> list[dict]:
    """Distil the Orphanet nomenclature XML (en_product1) into index rows.

    Own id = OrphaCode; label = Name; synonyms = SynonymList. Only *exact* external
    references (DisorderMappingRelation ``E``) become cross-references, mapped to
    ICD-10 / OMIM / UMLS / MeSH / SNOMED so a broader/narrower Orphanet mapping is
    never emitted as a skos:exactMatch prediction.
    """
    import xml.etree.ElementTree as ET

    rows: list[dict] = []
    for _event, elem in ET.iterparse(path, events=("end",)):
        if _local_tag(elem) != "Disorder":
            continue
        code = elem.findtext("OrphaCode") or ""
        name = elem.findtext("Name") or ""
        if code and name:
            row = {"id": f"ORPHA:{code}", "label": name, "synonyms": [], "orphanet": [code]}
            for syn in elem.iterfind("SynonymList/Synonym"):
                if syn.text:
                    row["synonyms"].append(syn.text)
            for ref in elem.iterfind("ExternalReferenceList/ExternalReference"):
                db = _ORPHA_SOURCE_DB.get(ref.findtext("Source") or "")
                ident = ref.findtext("Reference") or ""
                relation = ref.findtext("DisorderMappingRelation/Name") or ""
                if db and ident and relation.strip().startswith("E "):
                    row.setdefault(db, []).append(ident)
            rows.append(row)
        elem.clear()
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
        fmt = cfg.get("format", "obo")
        if fmt == "obo":
            rows = parse_obo(raw, cfg["id_prefix"], disease_only=cfg.get("disease_only", False))
        elif fmt == "mesh-xml":
            rows = parse_mesh_xml(raw)
        elif fmt == "orphanet-xml":
            rows = parse_orphanet_xml(raw)
        else:
            print(f"[{db}] unknown format {fmt!r}; skipping", file=sys.stderr)
            continue
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
