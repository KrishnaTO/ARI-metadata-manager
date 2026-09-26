"""Each target database's current description of a concept, from public, keyless services.

The local ``data/2-databases`` indexes are a dated snapshot, and hold no SNOMED, OMOP,
ICD-10 or UMLS terms at all — only other ontologies' cross-references to those ids, which
can come from a broader term (MONDO's "paraneoplastic neurologic syndrome" carries SNOMED
192877007, which SNOMED itself names "Paraneoplastic cerebellar degeneration"). So every
review database is looked up live at its source:

==========  ==================================================================
snomed      HL7 tx.fhir.org, FHIR ``$lookup``, US edition (International + US
            extension; ARI stores ids from both, e.g. 104461000119104)
icd10       HL7 tx.fhir.org, FHIR ``$lookup``, ICD-10-CM
omop        OHDSI's terminology server, FHIR ``$lookup`` (rate-limited per IP);
            also names the source code the concept was made from
mondo, doid EMBL-EBI OLS4 v2 — synonyms by scope, so only *exact* synonyms are
nci         matched and narrow/broad ones are reported separately (MONDO lists
orphanet    "paraneoplastic cerebellar degeneration" as a *narrow* synonym of
            "paraneoplastic neurologic syndrome")
mesh        NLM MeSH lookup API (descriptors D… and supplementary concepts C…)
umls        NCBI MedGen (E-utilities), which carries UMLS CUIs for diseases;
            UMLS itself needs a licence key. NCBI allows 3 requests/s unkeyed, so a
            PR's CUIs are fetched in one batch (:func:`prefetch`).
==========  ==================================================================

:func:`lookup` returns ``{label, synonyms, narrow, broad, definition, parents, inactive,
standard, facts, xrefs}``, or None when the source has no such code. Any other failure
(network, rate limit, server error) raises: a matrix silently built on partial data
would read as evidence it isn't.
"""
from __future__ import annotations

import datetime
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx

SOURCES = {
    "snomed": "SNOMED CT (tx.fhir.org, US edition)",
    "icd10": "ICD-10-CM (tx.fhir.org)",
    "omop": "OMOP (fhir-terminology.ohdsi.org)",
    "mondo": "MONDO (EBI OLS)",
    "doid": "DOID (EBI OLS)",
    "nci": "NCIt (EBI OLS)",
    "orphanet": "Orphanet ORDO (EBI OLS)",
    "mesh": "MeSH (id.nlm.nih.gov)",
    "umls": "UMLS via NCBI MedGen",
}

_TX = "https://tx.fhir.org/r4/CodeSystem/$lookup"
_OMOP = "https://fhir-terminology.ohdsi.org/r4/CodeSystem/$lookup"
_OMOP_SYSTEM = "https://fhir-terminology.ohdsi.org"
_SNOMED_SYSTEM = "http://snomed.info/sct"
_ICD10CM_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
_SNOMED_US_EDITION = "http://snomed.info/sct/731000124108"
_OLS = "https://www.ebi.ac.uk/ols4/api/v2/ontologies/{onto}/classes/{iri}"
_MESH = "https://id.nlm.nih.gov/mesh/lookup/details"
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

# Code systems an OMOP concept's source code can come from -> review db key.
_SOURCE_SYSTEM_DB = {_SNOMED_SYSTEM: "snomed", _ICD10CM_SYSTEM: "icd10"}

# db key -> (OLS ontology id, class IRI template).
_OLS_ONTOLOGIES = {
    "mondo": ("mondo", "http://purl.obolibrary.org/obo/MONDO_{id}"),
    "doid": ("doid", "http://purl.obolibrary.org/obo/DOID_{id}"),
    "nci": ("ncit", "http://purl.obolibrary.org/obo/NCIT_{id}"),
    "orphanet": ("ordo", "http://www.orpha.net/ORDO/Orphanet_{id}"),
}
_OBO = "http://www.geneontology.org/formats/oboInOwl#"
_EXACT = (_OBO + "hasExactSynonym", "http://www.ebi.ac.uk/efo/alternative_term")
_NARROW, _BROAD = _OBO + "hasNarrowSynonym", _OBO + "hasBroadSynonym"
_REPLACED_BY = "http://purl.obolibrary.org/obo/IAO_0100001"
_COMMENT = "http://www.w3.org/2000/01/rdf-schema#comment"

_SEMANTIC_TAG = re.compile(r"\s*\([^()]*\)\s*$")      # SNOMED FSN "... (disorder)"
_BRACKETED = re.compile(r"\s*\[[^\]]*\]\s*$")          # ICD "... [Marchiafava-Micheli]"

# Answers per (db, code) for the life of the process, shared by the prefetch threads
# (which are handed distinct codes, so none is fetched twice).
_cache: dict[tuple[str, str], dict | None] = {}
_cache_lock = threading.Lock()

# NCBI allows three unkeyed requests per second per IP. Calls are made one at a time
# (the lock is held through the request) and at least 0.4 s apart.
_ncbi_lock = threading.Lock()
_ncbi_last = 0.0
_NCBI_BATCH = 200


def _get(url: str, **kw) -> httpx.Response:
    return httpx.get(url, timeout=30, follow_redirects=True, **kw)


def _concept(label: str, names=(), **extra) -> dict:
    seen = {label.casefold()}
    synonyms = [n for n in names if n and not (n.casefold() in seen or seen.add(n.casefold()))]
    out = {"label": label, "synonyms": synonyms, "narrow": [], "broad": [], "definition": "",
           "parents": [], "inactive": False, "standard": True, "facts": [], "xrefs": {}}
    out.update(extra)
    return out


# --------------------------------------------------------------------- FHIR $lookup
def _value(part: dict):
    return next((v for k, v in part.items() if k.startswith("value")), None)


def _fhir(url: str, params: dict) -> dict | None:
    """A ``$lookup`` answer as ``{display, designations, properties}``, or None (404)."""
    resp = _get(url, params=params, headers={"Accept": "application/fhir+json"})
    if resp.status_code == 404:
        return None
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
    return {"display": display, "designations": designations, "properties": properties}


def _first(props: dict, code: str):
    return props.get(code, [{}])[0].get("value")


def _snomed(code: str) -> dict | None:
    got = _fhir(_TX, {"system": _SNOMED_SYSTEM, "code": code, "version": _SNOMED_US_EDITION})
    if got is None:
        return None
    props = got["properties"]
    names = [_SEMANTIC_TAG.sub("", v) if use == "Fully specified name" else v
             for use, v in got["designations"]]
    return _concept(got["display"], names,
                    parents=[p["description"] or p["value"] for p in props.get("parent", [])],
                    inactive=bool(_first(props, "inactive")))


def _icd10(code: str) -> dict | None:
    got = _fhir(_TX, {"system": _ICD10CM_SYSTEM, "code": code})
    if got is None:
        return None
    props = got["properties"]
    # "Paroxysmal nocturnal hemoglobinuria [Marchiafava-Micheli]": the bracket is an
    # eponym, so the name without it is matched too.
    names = [v for _, v in got["designations"]]
    names += [_BRACKETED.sub("", n) for n in [got["display"], *names]]
    return _concept(got["display"], names,
                    parents=[p["description"] or p["value"] for p in props.get("parent", [])],
                    inactive=bool(_first(props, "inactive")),
                    facts=["category, not a billable code"] if _first(props, "notSelectable")
                    else [])


def _omop(code: str) -> dict | None:
    got = _fhir(_OMOP, {"system": _OMOP_SYSTEM, "code": code})
    if got is None:
        return None
    props = got["properties"]
    source = _first(props, "source-concept-code") or {}
    standard = _first(props, "standard-concept")
    # OMOP retires a concept by ending its validity; the server's ``inactive`` stays false.
    ended = (_first(props, "valid-end-date") or "9999") < datetime.date.today().isoformat()
    xrefs = {}
    if source.get("system") in _SOURCE_SYSTEM_DB:
        xrefs[_SOURCE_SYSTEM_DB[source["system"]]] = [source["code"]]
    facts = [f"vocabulary {_first(props, 'vocabulary-id')}",
             f"source code {source.get('code', '?')}",
             f"domain {_first(props, 'domain-id')}",
             f"class {_first(props, 'concept-class-id')}",
             {"S": "standard concept", "C": "classification concept"}.get(standard,
                                                                            "non-standard concept"),
             f"valid {_first(props, 'valid-start-date')} – {_first(props, 'valid-end-date')}"]
    return _concept(got["display"], [_SEMANTIC_TAG.sub("", v) for _, v in got["designations"]],
                    inactive=bool(_first(props, "inactive")) or ended,
                    standard=standard == "S", facts=facts, xrefs=xrefs)


# ------------------------------------------------------------------------- EBI OLS
def _texts(value) -> list[str]:
    """OLS v2 values are a string, a reified ``{value: ...}``, or a list of either."""
    items = value if isinstance(value, list) else [] if value is None else [value]
    return [i["value"] if isinstance(i, dict) else i for i in items]


def _ols(db: str, code: str) -> dict | None:
    onto, template = _OLS_ONTOLOGIES[db]
    iri = template.format(id=code)
    resp = _get(_OLS.format(onto=onto, iri=quote(quote(iri, safe=""), safe="")))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    d = resp.json()
    label = _texts(d.get("label"))[0]
    exact = [s for prop in _EXACT for s in _texts(d.get(prop))]
    replaced = _texts(d.get(_REPLACED_BY))
    # OLS folds rdfs:comment into ``definition`` for some ontologies (DOID: "NT MGI.").
    comments = set(_texts(d.get(_COMMENT)))
    definitions = [t for t in _texts(d.get("definition")) if t not in comments]
    return _concept(label, exact, narrow=_texts(d.get(_NARROW)), broad=_texts(d.get(_BROAD)),
                    definition=(definitions or [""])[0],
                    inactive=bool(d.get("isObsolete")),
                    facts=[f"obsolete, replaced by {', '.join(replaced)}"] if replaced else [])


# ---------------------------------------------------------------------------- MeSH
def _mesh(code: str) -> dict | None:
    resp = _get(_MESH, params={"descriptor": code})
    resp.raise_for_status()
    terms = resp.json()["terms"]
    if not terms:
        return None
    preferred = next(t["label"] for t in terms if t["preferred"])
    return _concept(preferred, [t["label"] for t in terms],
                    facts=["supplementary concept" if code.startswith("C") else "descriptor"])


# ------------------------------------------------------------------ UMLS via MedGen
def _ncbi(path: str, params: dict) -> dict:
    global _ncbi_last
    with _ncbi_lock:
        wait = _ncbi_last + 0.4 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        resp = _get(_EUTILS + path, params={**params, "retmode": "json"})
        _ncbi_last = time.monotonic()
    resp.raise_for_status()
    return resp.json()


def _medgen(cuis: list[str]) -> dict[str, dict | None]:
    """CUI -> concept (None when MedGen lacks it), in one search + one summary call."""
    found = {}
    uids = _ncbi("esearch.fcgi", {"db": "medgen", "retmax": len(cuis) * 5,
                                  "term": " OR ".join(f"{c}[ConceptID]" for c in cuis)})
    uids = uids["esearchresult"]["idlist"]
    if uids:
        result = _ncbi("esummary.fcgi", {"db": "medgen", "id": ",".join(uids)})["result"]
        found = {result[u]["conceptid"]: _medgen_concept(result[u]) for u in result["uids"]}
    return {c: found.get(c) for c in cuis}


def _umls(code: str) -> dict | None:
    return _medgen([code])[code]


def _medgen_concept(rec: dict) -> dict:
    names = re.findall(r"<Name [^>]*>([^<]+)</Name>", rec.get("conceptmeta", ""))
    definition = rec.get("definition") or {}
    semantic = rec.get("semantictype") or {}
    return _concept(rec["title"], names,
                    definition=definition.get("value", "") if isinstance(definition, dict)
                    else str(definition),
                    facts=[f"semantic type {semantic.get('value', '?')}"]
                    if isinstance(semantic, dict) else [])


_LOOKUPS = {"snomed": _snomed, "icd10": _icd10, "omop": _omop, "mesh": _mesh, "umls": _umls,
            **{db: (lambda code, db=db: _ols(db, code)) for db in _OLS_ONTOLOGIES}}


def _bare(code: str) -> str:
    return code.split(":", 1)[1] if ":" in code else code


def prefetch(codes: set[tuple[str, str]]) -> None:
    """Fetch every ``(db, code)`` into the cache: UMLS in MedGen batches, the rest in
    parallel. Raises the first failure."""
    todo = {(db, _bare(c)) for db, c in codes}
    with _cache_lock:
        todo -= _cache.keys()
    cuis = sorted(c for db, c in todo if db == "umls")
    for i in range(0, len(cuis), _NCBI_BATCH):
        answers = _medgen(cuis[i:i + _NCBI_BATCH])
        with _cache_lock:
            _cache.update({("umls", c): concept for c, concept in answers.items()})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda key: lookup(*key), [k for k in todo if k[0] != "umls"]))


def lookup(db: str, code: str) -> dict | None:
    """The live concept for ``db:code``, cached; None when the source has no such code."""
    code = _bare(code)
    key = (db, code)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    concept = _LOOKUPS[db](code)
    with _cache_lock:
        _cache[key] = concept
    return concept
