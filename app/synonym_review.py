"""Report 9 (``data/4-reports/9_Synonym_Review.tsv`` in the ARI repo), parsed.

The report judges every name an ARI disease carries, and every name its
confirmed target terms give it, as a synonym of the disease or something else
(subtype, variant, broader, distinct, non-disease), with the action that verdict
implies for ARI. It is written by ``notebook/synonym-review/write_report.py``
there; this module only reads it.
"""
import csv
import io

COLUMNS = ["ari_id", "disease", "term", "ari_field", "verdict", "action",
           "decided_by", "evidence", "note", "other_names", "review_date"]


def parse(text: str) -> list[dict]:
    """Rows of the report, one dict per name, in the report's own order.

    Raises ``ValueError`` when the header is not the one the writer produces:
    a renamed column would otherwise render as a silently empty field.
    """
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    if reader.fieldnames != COLUMNS:
        raise ValueError(f"Unexpected synonym review columns: {reader.fieldnames}")
    return list(reader)
