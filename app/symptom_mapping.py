"""Which shared ARI symptom (ARIS) class a per-disease Symptom's label names.

The data repo's ``notebook/symptom-mapping/symptom_mapping.tsv`` (KrishnaTO/ARI)
owns that decision, keyed by the symptom string, and each Symptom individual
carries it as ``symptomConcept`` (ARI#152). The data repo's build script writes
those links in bulk; this keeps one current when a curator adds or renames a
symptom. ``deploy/update-mappings.sh`` keeps the table in
``config.MAPPINGS_RUNTIME_DIR``.
"""
from pathlib import Path

from . import config

ARIS_BASE = "http://www.aurint.org/ontologies/ari-symptoms#"
# Row kinds that name an ARIS class of their own. A `merged` row borrows the
# class of the row in its Merged Into; a `not a symptom` row names none.
MAPPED = {"exact", "broader"}


def path() -> Path:
    """Read at call time so a refresh is picked up."""
    return config.MAPPINGS_RUNTIME_DIR / "symptom_mapping.tsv"


def _rows() -> dict[str, dict]:
    """``{symptom string: row}``. A missing file is an error, not an empty table:
    every symptom would lose its link."""
    p = path()
    if not p.is_file():
        raise FileNotFoundError(
            f"symptom mapping {p} is missing: fetch the data repo's "
            "notebook/symptom-mapping/symptom_mapping.tsv (deploy/update-mappings.sh does on the server)")
    lines = p.read_text(encoding="utf-8").splitlines()
    cols = lines[0].split("\t")
    rows = (dict(zip(cols, line.split("\t"))) for line in lines[1:] if line.strip())
    return {r["Symptom"]: r for r in rows}


def concept_for(name: str) -> str | None:
    """The ARIS class IRI the symptom string ``name`` maps to, or None for a
    ``not a symptom`` row or a string with no row yet."""
    rows = _rows()
    row = rows.get(name.strip())
    if row is None:
        return None
    if row["Mapping"] == "merged":
        target = row["Merged Into"]
        if target not in rows:
            raise ValueError(f"symptom mapping: {row['Symptom']!r} is merged into {target!r}, which has no row")
        row = rows[target]
    return ARIS_BASE + row["Symptom ID"].replace(":", "_") if row["Mapping"] in MAPPED else None
