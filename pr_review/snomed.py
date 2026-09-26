"""SNOMED CT's own description of a concept, from the public HL7 terminology server.

The local indexes hold no SNOMED terms, only other ontologies' cross-references to
SNOMED ids, and those can point from a broader term (MONDO's "paraneoplastic neurologic
syndrome" carries 192877007, which SNOMED itself names "Paraneoplastic cerebellar
degeneration"). tx.fhir.org answers FHIR ``CodeSystem/$lookup`` without a key, so the
reviewer can compare against SNOMED's own label, synonyms and parents instead.
"""
from __future__ import annotations

import re

import httpx

LOOKUP_URL = "https://tx.fhir.org/r4/CodeSystem/$lookup"
SYSTEM = "http://snomed.info/sct"
# The US edition is the International release plus the US extension; ARI stores ids from
# both (e.g. 104461000119104), and the International edition alone lacks the latter.
US_EDITION = "http://snomed.info/sct/731000124108"
SOURCE = "SNOMED CT (tx.fhir.org, US edition)"

# Answers per code for the life of the process: a released edition does not change.
_cache: dict[str, dict | None] = {}

_SEMANTIC_TAG = re.compile(r"\s*\([^()]*\)\s*$")


def _part_value(part: dict):
    for key in ("valueString", "valueCode", "valueBoolean"):
        if key in part:
            return part[key]
    return part.get("valueCoding", {}).get("display")


def _parse(params: list[dict]) -> dict:
    label, synonyms, parents, inactive = "", [], [], False
    for p in params:
        if p["name"] == "display":
            label = p["valueString"]
        parts = {x["name"]: x for x in p.get("part", [])}
        if p["name"] == "designation" and _part_value(parts.get("language", {})) in (None, "en"):
            value = _part_value(parts["value"])
            if _part_value(parts.get("use", {})) == "Fully specified name":
                value = _SEMANTIC_TAG.sub("", value)     # "... (disorder)" -> "..."
            synonyms.append(value)
        if p["name"] == "property":
            code = _part_value(parts["code"])
            if code == "parent":
                parents.append(_part_value(parts["description"]) or _part_value(parts["value"]))
            elif code == "inactive":
                inactive = bool(_part_value(parts["value"]))
    seen = {label.casefold()}
    unique = [s for s in synonyms if not (s.casefold() in seen or seen.add(s.casefold()))]
    return {"label": label, "synonyms": unique, "parents": parents, "inactive": inactive}


def lookup(code: str) -> dict | None:
    """``{label, synonyms, parents, inactive}`` for a SNOMED code, or None when the
    edition has no such code. Any other server or network failure raises."""
    if code not in _cache:
        resp = httpx.get(LOOKUP_URL, timeout=30,
                         params={"system": SYSTEM, "code": code, "version": US_EDITION},
                         headers={"Accept": "application/fhir+json"})
        if resp.status_code == 404:
            _cache[code] = None
        else:
            resp.raise_for_status()
            _cache[code] = _parse(resp.json()["parameter"])
    return _cache[code]
