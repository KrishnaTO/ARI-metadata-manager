#!/usr/bin/env python3
"""Re-derive the fuzzy-match threshold from the confirmed mappings.

``predict_service`` falls back to word-overlap matching when no exact route finds
anything, gated by ``FUZZY_THRESHOLD``. That constant is not a guess: it is picked
by replaying every curator-confirmed mapping in ``mappings/ari.sssom.tsv`` at a
range of thresholds and reading off where recall stops paying for the candidates
it costs a curator to read.

For each threshold the script reports, against the exact-routes-only baseline:

  recovered   confirmed (db, id) cells the predictor proposes
  offered     candidates it proposes in total (the curator's reading pile)
  yield       share of the *additional* candidates that are confirmed cells

``yield`` is a floor, not a true precision: a candidate absent from the confirmed
set may simply be unreviewed rather than wrong, and only a fraction of the
catalogue has been curated. It is comparable *across* thresholds, which is what
choosing one needs.

Each disease is replayed from its label alone with every cell blank, so the
numbers isolate the label routes — exactly what the fuzzy fallback changes.

Re-run this when the confirmed corpus grows; if the shape of the table moves,
update ``FUZZY_THRESHOLD`` and the note beside it.

Usage:
    python scripts/eval_fuzzy.py
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

# Import the app package whether run as a module or a bare script.
try:
    from app import predict_service as ps
    from app.sssom_service import PREFIX_TO_DBS
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app import predict_service as ps
    from app.sssom_service import PREFIX_TO_DBS

ROOT = Path(__file__).resolve().parent.parent
SSSOM_PATH = ROOT / "mappings" / "ari.sssom.tsv"
THRESHOLDS = (0.7, 0.6, 0.5, 0.4)
# A threshold high enough that no pair can reach it, i.e. the fuzzy route off.
FUZZY_OFF = 1.01


def load_gold(path: Path) -> tuple[dict[str, str], dict[str, set]]:
    """``ari_id -> label`` and ``ari_id -> {(db, match_key)}`` from a SSSOM file."""
    labels: dict[str, str] = {}
    gold: dict[str, set] = collections.defaultdict(set)
    cols = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if cols is None:
            cols = parts
            continue
        row = dict(zip(cols, parts))
        ari = row.get("subject_id", "")
        if not ari:
            continue
        labels[ari] = row.get("subject_label", "")
        prefix, _, ident = row.get("object_id", "").partition(":")
        for db in PREFIX_TO_DBS.get(prefix, []):
            gold[ari].add((db, ps.match_key(ident)))
    return labels, gold


def replay(labels: dict[str, str], gold: dict[str, set], indexes: list,
           threshold: float) -> tuple[int, int, list[tuple[str, int]]]:
    """Predict every disease from its label at ``threshold``.

    Returns ``(confirmed cells recovered, candidates offered, per-disease fuzzy
    counts)``. Mutates the module constant for the duration of the call — this is
    an offline report, nothing else is running.
    """
    previous = ps.FUZZY_THRESHOLD
    ps.FUZZY_THRESHOLD = threshold
    try:
        recovered = offered = 0
        fuzzy_counts = []
        for ari, label in labels.items():
            disease = {"ari_id": ari, "name": label, "synonyms": [], "existing": {}}
            preds = ps.predict_for_disease(disease, indexes)
            offered += len(preds)
            n_fuzzy = sum(1 for p in preds if p["match_field"] == "fuzzy")
            if n_fuzzy:
                fuzzy_counts.append((label, n_fuzzy))
            got = {(p["db"], ps.match_key(p["id"])) for p in preds}
            recovered += sum(1 for cell in gold[ari] if cell in got)
        return recovered, offered, fuzzy_counts
    finally:
        ps.FUZZY_THRESHOLD = previous


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sssom", default=str(SSSOM_PATH),
                    help="confirmed mappings to replay (default: mappings/ari.sssom.tsv)")
    args = ap.parse_args(argv)

    indexes = ps.get_indexes()
    if not indexes:
        print("no indexes under data/2-databases — run scripts/fetch_databases.py first")
        return 1
    labels, gold = load_gold(Path(args.sssom))
    cells = sum(len(v) for v in gold.values())
    print(f"{len(labels)} diseases, {cells} confirmed cells, "
          f"{len(indexes)} indexes ({', '.join(i.source for i in indexes)})\n")

    base_recovered, base_offered, _ = replay(labels, gold, indexes, FUZZY_OFF)
    print(f"{'threshold':>10}  {'recovered':>9}  {'offered':>7}  {'yield':>6}  busiest disease")
    print(f"{'exact only':>10}  {base_recovered:>9}  {base_offered:>7}  {'—':>6}  —")
    for threshold in THRESHOLDS:
        recovered, offered, fuzzy_counts = replay(labels, gold, indexes, threshold)
        extra_cells = recovered - base_recovered
        extra_cands = offered - base_offered
        share = f"{extra_cells / extra_cands * 100:.0f}%" if extra_cands else "—"
        busiest = max(fuzzy_counts, key=lambda x: x[1], default=("—", 0))
        print(f"{threshold:>10}  {recovered:>4} (+{extra_cells:<3}) {offered:>7}  {share:>6}  "
              f"{busiest[0]} ({busiest[1]})")
    print("\nrecovered/offered counts include the exact routes; (+n) is the fuzzy gain.\n"
          "yield = share of the additional candidates that are confirmed cells (a floor).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
