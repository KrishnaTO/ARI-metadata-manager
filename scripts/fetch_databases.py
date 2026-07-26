#!/usr/bin/env python3
"""Download reference databases and build the compact cross-reference indexes
used by ``app/predict_service`` (issue #42).

For each freely-redistributable source this downloads the raw release into
``data/2-databases/raw/`` (git-ignored — large) and distils it into two committed
files: ``data/2-databases/<db>.index.tsv`` (one lean row per term — label, exact
synonyms, cross-reference ids — read on every prediction request) and a
``<db>.details.tsv`` sidecar (id -> definition, parents) read on demand by the
concept-detail lookup. Splitting details out keeps the prediction index ~half the
size it would otherwise be; see ``data/2-databases/README.md``. The raw dumps are not
committed.

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


def _parse_def(line: str) -> str:
    """Return the text of a ``def: "text" [refs]`` line (quotes + refs stripped)."""
    body = line[len("def:"):].strip()
    if not body.startswith('"'):
        return ""
    end = body.find('"', 1)
    return body[1:end] if end > 1 else ""


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

    Each row: ``{"id", "label", "synonyms": [...], "definition", "parents": [ids],
    "<db>": [ids...]}``. The term's own id also fills its owning-database column, so
    a MONDO term carries its MONDO id plus every xref it declares. ``disease_only``
    keeps only NCIt terms whose semantic type (NCIT:P106) is a disease/disorder —
    the NCIt release is otherwise ~180k mostly-non-disease concepts. NCIt's UMLS CUI
    (NCIT:P207) is harvested as a umls cross-reference.

    ``definition`` is the ``def:`` text; ``parents`` starts as the ``is_a:`` target
    ids and is resolved to the parents' **labels** by :func:`_resolve_parents` in a
    second pass over the returned rows (a target dropped from a disease-only index
    keeps its id — a curator reading "is_a NCIT:C2991" is better served by the id
    than by nothing).
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
                cur = {"synonyms": [], "definition": "", "parents": []}
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
            elif line.startswith("def: "):
                if not cur["definition"]:
                    cur["definition"] = _parse_def(line)
            elif line.startswith("is_a: "):
                # ``is_a: TARGET {qualifiers} ! label`` — TARGET is the first token
                # (drop any OBO trailing ``{...}`` qualifier and the ``! label``
                # comment); the second pass turns the id into the parent's label.
                rest = line[len("is_a:"):].split()
                if rest:
                    cur["parents"].append(rest[0])
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
    _resolve_parents(rows)
    return rows


def _resolve_parents(rows: list[dict]) -> None:
    """Rewrite each row's ``parents`` from ``is_a`` ids to the parents' labels.

    A single pass over ``rows`` builds an id->label map, then every ``parents``
    entry that names a kept term is replaced by that term's label. Targets absent
    from the map (obsolete, or filtered out of a disease-only index) keep their id.
    """
    id_to_label = {r["id"]: r["label"] for r in rows if r.get("id") and r.get("label")}
    for r in rows:
        r["parents"] = [id_to_label.get(pid, pid) for pid in r.get("parents", [])]


def _local_tag(elem) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def parse_mesh_xml(path: Path, keep_tree_prefixes: tuple[str, ...] = ("C", "F03")) -> list[dict]:
    """Distil NLM MeSH descriptor XML into index rows (Diseases + Mental Disorders).

    Keeps descriptors with a tree number under ``keep_tree_prefixes`` (C = Diseases,
    F03 = Mental Disorders). Label = DescriptorName; synonyms = the terms of the
    *preferred* concept only (non-preferred concepts are narrower and would make an
    unsafe exact match); ``definition`` = the preferred concept's ScopeNote (MeSH's
    definition equivalent). No cross-references (MeSH descriptors carry none to the
    other target databases); only the ``mesh`` column is filled. ``parents`` is left
    empty — MeSH hierarchy comes from tree numbers, a different mechanism that would
    need the whole tree resolved to be a curator-readable label (noted in the README).
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
            synonyms, definition = [], ""
            for concept in elem.iterfind("ConceptList/Concept"):
                if concept.get("PreferredConceptYN") != "Y":
                    continue
                definition = concept.findtext("ScopeNote") or ""
                for term in concept.iterfind("TermList/Term/String"):
                    if term.text and term.text != name:
                        synonyms.append(term.text)
            rows.append({"id": f"MESH:{ui}", "label": name, "synonyms": synonyms,
                         "definition": definition, "parents": [], "mesh": [ui]})
        elem.clear()
    return rows


# Orphanet en_product1 external-reference source label -> target-database key.
_ORPHA_SOURCE_DB = {"ICD-10": "icd10", "OMIM": "omim", "UMLS": "umls",
                    "MeSH": "mesh", "SNOMED CT": "snomed"}


def parse_orphanet_xml(path: Path) -> list[dict]:
    """Distil the Orphanet nomenclature XML (en_product1) into index rows.

    Own id = OrphaCode; label = Name; synonyms = SynonymList; ``definition`` = the
    disorder's SummaryInformation text section (en_product1 carries the Orphanet
    definition). Only *exact* external references (DisorderMappingRelation ``E``)
    become cross-references, mapped to ICD-10 / OMIM / UMLS / MeSH / SNOMED so a
    broader/narrower Orphanet mapping is never emitted as a skos:exactMatch
    prediction. ``parents`` is left empty — the Orphanet classification hierarchy
    lives in a different product file (en_product3) that this script does not
    download (noted in the README).
    """
    import xml.etree.ElementTree as ET

    rows: list[dict] = []
    for _event, elem in ET.iterparse(path, events=("end",)):
        if _local_tag(elem) != "Disorder":
            continue
        code = elem.findtext("OrphaCode") or ""
        name = elem.findtext("Name") or ""
        if code and name:
            definition = elem.findtext(
                "SummaryInformationList/SummaryInformation/TextSectionList/TextSection/Contents") or ""
            row = {"id": f"ORPHA:{code}", "label": name, "synonyms": [],
                   "definition": definition, "parents": [], "orphanet": [code]}
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


def _clean_cell(text: str) -> str:
    """Collapse tabs/newlines so a free-text field stays inside one TSV cell."""
    return " ".join((text or "").split())


# Columns of a ``<db>.details.tsv`` sidecar: the term's own id, its definition, and
# its ` | `-joined parent labels. Kept out of the prediction index because they
# roughly double its size (see README) yet are only read by the concept-detail
# lookup — see ``app/concept_service`` and ``predict_service.INDEX_COLS``.
DETAILS_COLS = ["id", "definition", "parents"]


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


def write_details(db: str, rows: list[dict]) -> Path:
    """Write the ``<db>.details.tsv`` sidecar (id -> definition, parents).

    Rows with neither a definition nor parents are omitted so the sidecar stays as
    small as the data allows; ``concept_service`` treats a missing id as "this term
    has no details", and a missing whole file as "details not built for this db".
    """
    out = DATA_DIR / f"{db}.details.tsv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(DETAILS_COLS) + "\n")
        for r in rows:
            definition = _clean_cell(r.get("definition", ""))
            parents = " | ".join(_clean_cell(p) for p in r.get("parents", []) if p)
            if not definition and not parents:
                continue
            f.write("\t".join([r.get("id", ""), definition, parents]) + "\n")
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
        det = write_details(db, rows)
        root = DATA_DIR.parent.parent
        print(f"[{db}] wrote {out.relative_to(root)} ({len(rows):,} terms, {out.stat().st_size:,} bytes)"
              f" + {det.relative_to(root)} ({det.stat().st_size:,} bytes)")


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
