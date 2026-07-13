#!/usr/bin/env python3
"""Report ARI disease synonyms that are really the name of a *different* disease,
and emit a blocklist the predictor uses to ignore them (issue #42 follow-up).

Some ARI records list associated conditions or related disorders in their synonym
field (e.g. "Megaloblastic anemia" under "Autoimmune gastritis"). Exact
synonym-matching then links the disease to an unrelated database concept. This
script finds every such synonym by checking whether it is the *canonical label* of
some reference-ontology term, and classifies it:

  conflicts_with_label  The disease's own label confidently matches a concept
                        (its identity), and the synonym names a *different* one.
                        High-confidence mis-curation -> written to the blocklist so
                        the predictor never matches on it. (The label-anchor rule in
                        predict_service already ignores these, but the blocklist
                        makes the exclusion explicit, auditable, and extendable.)
  synonym_only_review   The disease label matches no concept, and the synonym is a
                        distinct disease's canonical name. Usually legitimate — the
                        ARI label is just a verbose phrasing (e.g. "Immunoglobulin
                        G4 related ophthalmic disease" -> "IgG4-related ophthalmic
                        disease") — so these are listed for human review, NOT
                        auto-blocklisted.

Outputs (under mappings/):
  ari.mis_curated_synonyms.tsv   the full report (both verdicts, with context)
  ari.synonym_blocklist.tsv      ari_id + synonym pairs the predictor must ignore
                                 (the conflicts_with_label rows; curator-editable)

Usage:
    python scripts/report_synonyms.py [--ontology ontologies/ari_t1d.owl]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Import the app package whether run as a module or a bare script.
try:
    from app import predict_service as ps
    from app.ontology_service import OntologyService
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import predict_service as ps
    from app.ontology_service import OntologyService

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "mappings" / "ari.mis_curated_synonyms.tsv"
BLOCKLIST_PATH = ROOT / "mappings" / "ari.synonym_blocklist.tsv"

REPORT_COLS = ["ari_id", "disease_label", "synonym", "matched_curie", "matched_label",
               "sources", "disease_anchor_curie", "disease_anchor_label", "verdict"]


def _canonical_matches(indexes, name):
    """Records whose *canonical label* equals ``name`` (i.e. ``name`` is that
    concept's primary name, not merely one of its synonyms), with the source."""
    out = []
    n = ps.normalize(name)
    for idx in indexes:
        for rec in idx.lookup(name):
            if ps.normalize(rec["label"]) == n:
                out.append((idx.source, rec))
    return out


def _anchor(indexes, label):
    """Concepts the disease *label* matches, and the (db, id) set they provide."""
    recs = [(idx.source, rec) for idx in indexes for rec in idx.lookup(label)]
    ids = {(db, i) for _, rec in recs for db, vals in rec["by_db"].items() for i in vals}
    return recs, ids


def analyse(ontology_path):
    svc = OntologyService(str(ontology_path))
    base = svc.base
    indexes = ps.load_indexes(ROOT / "data" / "2-databases")
    if not indexes:
        print("No indexes in data/2-databases — run scripts/fetch_databases.py first.",
              file=sys.stderr)
    rows = []
    for ind in svc._all_diseases():
        ari = svc._get_annotation(ind, base + "ARI_ID")
        ari = ari[0] if ari else ""
        label = svc._get_label(ind)
        label_n = ps.normalize(label)
        anchor_recs, anchor_ids = _anchor(indexes, label)
        anchored = bool(anchor_recs)
        anchor_curie = anchor_recs[0][1]["id"] if anchor_recs else ""
        anchor_label = anchor_recs[0][1]["label"] if anchor_recs else ""
        for syn in svc._get_annotation(ind, base + "ARI_Synonym"):
            if not syn or ps.normalize(syn) == label_n:
                continue
            canon = _canonical_matches(indexes, syn)
            if not canon:
                continue    # synonym isn't the canonical name of any disease -> fine
            # Does the synonym's concept corroborate the disease's own identity?
            agrees = any((db, i) in anchor_ids
                         for _, rec in canon
                         for db, vals in rec["by_db"].items() for i in vals)
            if anchored and agrees:
                continue    # synonym points at the same disease -> a true alias
            verdict = "conflicts_with_label" if anchored else "synonym_only_review"
            rec = canon[0][1]
            sources = ",".join(sorted({s for s, _ in canon}))
            rows.append([ari, label, syn, rec["id"], rec["label"], sources,
                         anchor_curie, anchor_label, verdict])
    rows.sort(key=lambda r: (r[8], r[1].lower(), r[2].lower()))
    return rows


def write_report(rows):
    with open(REPORT_PATH, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(REPORT_COLS) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    return REPORT_PATH


def write_blocklist(rows):
    blocked = [r for r in rows if r[8] == "conflicts_with_label"]
    with open(BLOCKLIST_PATH, "w", encoding="utf-8", newline="") as f:
        f.write("# Synonyms the cross-reference predictor must ignore: each names a\n"
                "# different disease than the ARI record it is attached to (see\n"
                "# ari.mis_curated_synonyms.tsv). Generated by scripts/report_synonyms.py;\n"
                "# curator-editable — add rows to exclude more, delete rows to re-allow.\n")
        f.write("ari_id\tsynonym\tnames_instead\n")
        for r in blocked:
            f.write(f"{r[0]}\t{r[2]}\t{r[3]} {r[4]}\n")
    return BLOCKLIST_PATH, len(blocked)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ontology", default=str(ROOT / "ontologies" / "ari_t1d.owl"))
    args = ap.parse_args(argv)
    rows = analyse(args.ontology)
    write_report(rows)
    _, n_block = write_blocklist(rows)
    n_conflict = sum(1 for r in rows if r[8] == "conflicts_with_label")
    n_review = sum(1 for r in rows if r[8] == "synonym_only_review")
    print(f"wrote {REPORT_PATH.relative_to(ROOT)} ({len(rows)} flagged synonyms: "
          f"{n_conflict} conflicts_with_label, {n_review} synonym_only_review)")
    print(f"wrote {BLOCKLIST_PATH.relative_to(ROOT)} ({n_block} blocklisted synonyms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
