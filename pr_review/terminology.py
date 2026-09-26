"""A target database's own description of a concept, from public FHIR terminology servers.

The local indexes hold no SNOMED or OMOP terms, only other ontologies' cross-references to
their ids, and those can point from a broader term (MONDO's "paraneoplastic neurologic
syndrome" carries SNOMED 192877007, which SNOMED itself names "Paraneoplastic cerebellar
degeneration"). Two servers answer FHIR ``CodeSystem/$lookup`` without a key:

* SNOMED CT — the HL7 server tx.fhir.org, pinned to the US edition (the International
  release plus the US extension; ARI stores ids from both, e.g. 104461000119104).
* OMOP — OHDSI's own terminology server, which also names the source code an OMOP
  concept was made from (e.g. SNOMED 2772003), so it can be checked against ARI's ids.
  Anonymous use is rate-limited per IP.

Each lookup returns ``{label, synonyms, parents, inactive, facts, xrefs}`` or None when the
server has no such code; any other failure raises.
"""
from __future__ import annotations

import datetime
import re

import httpx

SNOMED_SOURCE = "SNOMED CT (tx.fhir.org, US edition)"
OMOP_SOURCE = "OMOP (fhir-terminology.ohdsi.org)"

_SNOMED_URL = "https://tx.fhir.org/r4/CodeSystem/$lookup"
_SNOMED_SYSTEM = "http://snomed.info/sct"
_SNOMED_US_EDITION = "http://snomed.info/sct/731000124108"
_OMOP_URL = "https://fhir-terminology.ohdsi.org/r4/CodeSystem/$lookup"
_OMOP_SYSTEM = "https://fhir-terminology.ohdsi.org"

# Code systems an OMOP concept's source code can come from -> review db key.
_SOURCE_SYSTEM_DB = {_SNOMED_SYSTEM: "snomed", "http://hl7.org/fhir/sid/icd-10-cm": "icd10"}

# Answers per (system, code) for the life of the process: a release does not change.
_cache: dict[tuple[str, str], dict | None] = {}

_SEMANTIC_TAG = re.compile(r"\s*\([^()]*\)\s*$")


def _value(part: dict):
    return next((v for k, v in part.items() if k.startswith("value")), None)


def _fetch(url: str, params: dict) -> dict | None:
    """The ``$lookup`` answer as ``{display, designations, properties}``, or None (404)."""
    key = (params["system"], params["code"])
    if key not in _cache:
        resp = httpx.get(url, params=params, timeout=30,
                         headers={"Accept": "application/fhir+json"})
        if resp.status_code == 404:
            _cache[key] = None
        else:
            resp.raise_for_status()
            display, designations, properties = "", [], {}
            for p in resp.json()["parameter"]:
                parts = {x["name"]: x for x in p.get("part", [])}
                if p["name"] == "display":
                    display = p["valueString"]
                elif p["name"] == "designation" and _value(parts.get("language", {})) in (None, "en"):
                    use = _value(parts.get("use", {}))
                    designations.append((use.get("display") if isinstance(use, dict) else use,
                                         _value(parts["value"])))
                elif p["name"] == "property":
                    properties.setdefault(_value(parts["code"]), []).append(
                        {"value": _value(parts.get("value", {})),
                         "description": _value(parts.get("description", {}))})
            _cache[key] = {"display": display, "designations": designations,
                           "properties": properties}
    return _cache[key]


def _unique(label: str, names: list[str]) -> list[str]:
    seen = {label.casefold()}
    return [n for n in names if not (n.casefold() in seen or seen.add(n.casefold()))]


def _first(props: dict, code: str):
    return props.get(code, [{}])[0].get("value")


def snomed(code: str) -> dict | None:
    got = _fetch(_SNOMED_URL, {"system": _SNOMED_SYSTEM, "code": code,
                               "version": _SNOMED_US_EDITION})
    if got is None:
        return None
    props = got["properties"]
    # The fully specified name ends in a semantic tag: "... (disorder)".
    names = [_SEMANTIC_TAG.sub("", v) if use == "Fully specified name" else v
             for use, v in got["designations"]]
    return {"label": got["display"], "synonyms": _unique(got["display"], names),
            "parents": [p["description"] or p["value"] for p in props.get("parent", [])],
            "inactive": bool(_first(props, "inactive")), "facts": [], "xrefs": {}}


def omop(code: str) -> dict | None:
    got = _fetch(_OMOP_URL, {"system": _OMOP_SYSTEM, "code": code})
    if got is None:
        return None
    props = got["properties"]
    names = [_SEMANTIC_TAG.sub("", v) for _, v in got["designations"]]
    source = _first(props, "source-concept-code") or {}
    xrefs = {}
    if source.get("system") in _SOURCE_SYSTEM_DB:
        xrefs[_SOURCE_SYSTEM_DB[source["system"]]] = [source["code"]]
    standard = _first(props, "standard-concept")
    # OMOP retires a concept by ending its validity; the server's ``inactive`` stays false.
    ended = (_first(props, "valid-end-date") or "9999") < datetime.date.today().isoformat()
    facts = [f"vocabulary {_first(props, 'vocabulary-id')}",
             f"source code {source.get('code', '?')}",
             f"domain {_first(props, 'domain-id')}",
             f"class {_first(props, 'concept-class-id')}",
             {"S": "standard concept", "C": "classification concept"}.get(standard,
                                                                            "non-standard concept"),
             f"valid {_first(props, 'valid-start-date')} – {_first(props, 'valid-end-date')}"]
    return {"label": got["display"], "synonyms": _unique(got["display"], names), "parents": [],
            "inactive": bool(_first(props, "inactive")) or ended, "standard": standard == "S",
            "facts": facts, "xrefs": xrefs}
