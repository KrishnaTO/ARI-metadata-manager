"""Single source of truth for the cross-reference databases the ARI ontology
links diseases to.

The same contract used to be hand-maintained in four places — ``sssom_service``
(object-CURIE prefixes + curie map), ``ontology_service`` (annotation-property
suffixes), and both frontend pages (``static/js/core.js`` link builders and
``static/ref-edits/ref-edits.js`` DBS + PREFIX). They had to be kept in lock-step
by hand; the review page's PREFIX map even carried a "mirrors app/sssom_service.py"
comment. Everything now derives from this one list, and the frontends fetch it from
``GET /api/v2/xref-databases``, so a database is added or changed in exactly one place.

Per-entry fields:
  key         review-page / API column key (also the disease-detail field name)
  label       human display label
  prefix      object CURIE / bioregistry prefix used in SSSOM mappings
  curie_base  IRI namespace ``prefix`` expands to (for the SSSOM curie_map)
  ari_suffix  the ARI_* annotation-property suffix the id is stored under
  link        URL template for one id: ``{num}`` = id without a leading ``PREFIX:``,
              ``{id}`` = the raw id (both URL-encoded by the caller). None if the
              database has no standalone link-out.
  search      URL template to search the source by disease name (``{name}``)
  noframe     True when the target site refuses to be embedded in an <iframe>
  review      shown as a column on the reference-review page
  main_app    shown as a cross-reference chip on the main disease page
"""

XREF_DATABASES = [
    {"key": "snomed", "label": "SNOMED", "prefix": "SNOMEDCT",
     "curie_base": "http://snomed.info/id/", "ari_suffix": "ARI_SNOMED",
     "link": "https://browser.ihtsdotools.org/?perspective=full&conceptId1={num}&edition=MAIN",
     "search": "https://browser.ihtsdotools.org/?perspective=full&edition=MAIN&languages=en&searchText={name}",
     "noframe": False, "review": True, "main_app": True},
    {"key": "omop", "label": "OMOP", "prefix": "omop",
     "curie_base": "https://athena.ohdsi.org/search-terms/terms/", "ari_suffix": "ARI_OMOP",
     "link": "https://athena.ohdsi.org/search-terms/terms/{num}",
     "search": "https://athena.ohdsi.org/search-terms/terms?query={name}",
     "noframe": True, "review": True, "main_app": True},
    {"key": "doid", "label": "DOID", "prefix": "DOID",
     "curie_base": "http://purl.obolibrary.org/obo/DOID_", "ari_suffix": "ARI_DOID",
     "link": "https://disease-ontology.org/?id=DOID:{num}",
     "search": "https://www.disease-ontology.org/?q={name}",
     "noframe": False, "review": True, "main_app": True},
    {"key": "mondo", "label": "MONDO", "prefix": "MONDO",
     "curie_base": "http://purl.obolibrary.org/obo/MONDO_", "ari_suffix": "ARI_MONDO",
     "link": "https://www.ebi.ac.uk/ols4/ontologies/mondo/classes?short_form=MONDO_{num}",
     "search": "https://www.ebi.ac.uk/ols4/search?q={name}&ontology=mondo",
     "noframe": False, "review": True, "main_app": True},
    {"key": "nci", "label": "NCI", "prefix": "ncit",
     "curie_base": "http://purl.obolibrary.org/obo/NCIT_", "ari_suffix": "ARI_NCI",
     "link": "https://ncithesaurus.nci.nih.gov/ncitbrowser/ConceptReport.jsp?dictionary=NCI_Thesaurus&code={num}",
     "search": "https://www.ebi.ac.uk/ols4/search?q={name}&ontology=ncit",
     "noframe": False, "review": True, "main_app": True},
    {"key": "icd10", "label": "ICD-10", "prefix": "icd10cm",
     "curie_base": "http://purl.bioontology.org/ontology/ICD10CM/", "ari_suffix": "ARI_ICD10",
     "link": "https://www.icd10data.com/search?s={id}",
     "search": "https://www.icd10data.com/search?s={name}",
     "noframe": False, "review": True, "main_app": True},
    {"key": "orphanet", "label": "Orphanet", "prefix": "ORPHA",
     "curie_base": "https://www.orpha.net/en/disease/detail/", "ari_suffix": "ARI_ORPHANET",
     "link": "https://www.orpha.net/en/disease/detail/{num}",
     "search": "https://www.orpha.net/en/disease?keyword={name}",
     "noframe": True, "review": True, "main_app": False},
    {"key": "omim", "label": "OMIM", "prefix": "OMIM",
     "curie_base": "https://omim.org/entry/", "ari_suffix": "ARI_OMIM",
     "link": "https://omim.org/entry/{num}",
     "search": "https://omim.org/search/?search={name}",
     "noframe": True, "review": True, "main_app": False},
    {"key": "umls", "label": "UMLS", "prefix": "umls",
     "curie_base": "https://uts.nlm.nih.gov/uts/umls/concept/", "ari_suffix": "ARI_UMLS",
     "link": "https://uts.nlm.nih.gov/uts/umls/concept/{id}",
     "search": "https://uts.nlm.nih.gov/uts/umls/searchResults?searchString={name}",
     "noframe": True, "review": True, "main_app": True},
    {"key": "mesh", "label": "MeSH", "prefix": "mesh",
     "curie_base": "http://id.nlm.nih.gov/mesh/", "ari_suffix": "ARI_MESH",
     "link": "https://meshb.nlm.nih.gov/record/ui?ui={num}",
     "search": "https://www.ncbi.nlm.nih.gov/mesh/?term={name}",
     "noframe": True, "review": True, "main_app": True},
    # DXCODE is stored under its own annotation property but shares SNOMED's CURIE
    # prefix and link-out, so it is neither a review column nor a main-app chip.
    {"key": "dxcode", "label": "Concept code (DXCODE)", "prefix": "SNOMEDCT",
     "curie_base": "http://snomed.info/id/", "ari_suffix": "ARI_DXCODE",
     "link": None, "search": None, "noframe": False, "review": False, "main_app": False},
]

BY_KEY = {d["key"]: d for d in XREF_DATABASES}

# db key -> object-CURIE prefix (SSSOM). Order preserved from the list above.
PREFIX = {d["key"]: d["prefix"] for d in XREF_DATABASES}

# db key -> ARI_* annotation-property suffix.
XREF_SUFFIXES = {d["key"]: d["ari_suffix"] for d in XREF_DATABASES}

# object-CURIE prefix -> IRI base, de-duplicated (dxcode reuses SNOMEDCT's).
CURIE_BASES = {d["prefix"]: d["curie_base"] for d in XREF_DATABASES if d.get("curie_base")}

_PUBLIC_KEYS = ("key", "label", "prefix", "link", "search", "noframe", "review", "main_app")


def public_list() -> list:
    """The JSON view the frontends consume (internal fields dropped)."""
    return [{k: d[k] for k in _PUBLIC_KEYS} for d in XREF_DATABASES]
