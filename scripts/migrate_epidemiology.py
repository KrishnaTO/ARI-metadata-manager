#!/usr/bin/env python3
"""Move each disease's flat prevalence and incidence fields into figures (#229).

A disease held one rate, year and source list per measure and region in twelve
data properties (``prevalencePer100k``, ``prevalenceUSYear`` ... ``incidenceWorldSource``).
The editor now keeps every figure as an ``EpidemiologyProfile`` item linked by
``hasEpidemiologyProfile`` (measure, region, year, sex, rate, source), so a
disease can hold several years and a figure per sex.

For every flat rate this creates one figure with that measure and region, sex
"All", and the year and sources stored beside it, and notes the move in the
disease's change log. The flat values and the twelve property declarations are
then removed, and the new class and properties are declared. A year or source
with no rate beside it stops the script, since there is no figure to put it on.

The file is rewritten in the data repo's canonical layout (``app.owl_format``),
so the diff is the moved values and the new figures.

Usage:
    python scripts/migrate_epidemiology.py <ari_disease.owl> [--editor NAME]

Dependencies: owlready2
"""
import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from owlready2 import AnnotationProperty, DataProperty, ObjectProperty, Thing, World, destroy_entity, label

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import owl_format  # noqa: E402

# (measure, region, rate property, year property, source property)
FLAT = [
    ("Prevalence", "US", "prevalencePer100k", "prevalenceUSYear", "prevalenceUSSource"),
    ("Prevalence", "Worldwide", "prevalenceWorldPer100k", "prevalenceWorldYear", "prevalenceWorldSource"),
    ("Incidence", "US", "incidenceUSPer100k", "incidenceUSYear", "incidenceUSSource"),
    ("Incidence", "Worldwide", "incidenceWorldPer100k", "incidenceWorldYear", "incidenceWorldSource"),
]


def _declare(onto):
    """The figure class and its properties, declared once."""
    def get(name, base, rng=None):
        found = onto.world[onto.base_iri + name]
        if found is not None:
            return found
        with onto:
            cls = type(name, (base,), {"namespace": onto})
            if rng is not None:
                cls.range = [rng]
            return cls

    return {
        "cls": get("EpidemiologyProfile", Thing),
        "link": get("hasEpidemiologyProfile", ObjectProperty),
        "measure": get("epiMeasure", DataProperty, str),
        "region": get("epiRegion", DataProperty, str),
        "year": get("epiYear", DataProperty, int),
        "sex": get("epiSex", DataProperty, str),
        "rate": get("epiRatePer100k", DataProperty, float),
        "source": get("sourcePMID", DataProperty, str),
    }


def migrate(path: Path, editor: str) -> int:
    """Rewrite ``path`` in place; returns the number of figures created."""
    world = World()
    # Loaded through a file object, as the app does: owlready2's file:// handling
    # breaks on Windows paths, and the real base IRI is read from the file.
    with open(path, "rb") as f:
        onto = world.get_ontology("http://ari.local/ontology").load(fileobj=f)
    base = onto.base_iri
    new = _declare(onto)
    changelog = world[base + "ARI_ChangeLog"]
    if changelog is None:
        with onto:
            changelog = type("ARI_ChangeLog", (AnnotationProperty,), {"namespace": onto})
    flat = [(m, r, world[base + rate], world[base + year], world[base + src])
            for m, r, rate, year, src in FLAT]

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    created = 0
    for e in list(world.individuals()):
        moved = []
        for measure, region, rate_p, year_p, src_p in flat:
            rates = list(rate_p[e]) if rate_p is not None else []
            years = list(year_p[e]) if year_p is not None else []
            sources = list(src_p[e]) if src_p is not None else []
            if not rates:
                if years or sources:
                    raise SystemExit(f"{e.iri}: {measure} {region} has a year or source but no rate")
                continue
            if len(rates) > 1 or len(years) > 1:
                raise SystemExit(f"{e.iri}: {measure} {region} has more than one rate or year")
            with onto:
                fig = new["cls"](f"Epi_{uuid.uuid4().hex[:8]}")
            new["measure"][fig] = [measure]
            new["region"][fig] = [region]
            new["sex"][fig] = ["All"]
            new["rate"][fig] = [float(rates[0])]
            if years:
                new["year"][fig] = [int(years[0])]
            if sources:
                new["source"][fig] = [str(s) for s in sources]
            label[fig] = [" · ".join([measure, region] + [str(y) for y in years] + ["All"])]
            new["link"][e].append(fig)
            moved.append(f"{measure} {region} {rates[0]}/100k")
            created += 1
        if moved:
            changelog[e] = list(changelog[e]) + [
                f"{ts} | {editor} | Moved to prevalence & incidence figures: {'; '.join(moved)}"]

    for _, _, *props in flat:
        for p in props:
            if p is not None:
                destroy_entity(p)

    tmp = path.with_name(path.name + ".migrate.tmp")
    try:
        onto.save(file=str(tmp), format="rdfxml")
        path.write_bytes(owl_format.format_owl(tmp.read_bytes()))
    finally:
        tmp.unlink(missing_ok=True)
    return created


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("owl", type=Path)
    ap.add_argument("--editor", default="migration (#229)")
    args = ap.parse_args()
    print(f"{migrate(args.owl, args.editor)} figures created in {args.owl}")


if __name__ == "__main__":
    main()
