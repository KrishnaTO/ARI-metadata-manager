# `data/2-databases` — reference database indexes for cross-reference prediction

These files back the **predicted** (yellow) cross-references on the reference-review
page (issue #42). For a disease whose target-database cell is still blank,
[`app/predict_service`](../../app/predict_service.py) exact-matches the disease's
label and synonyms against the terms in these indexes and proposes a candidate id —
which a curator then verifies and confirms.

## What's here

| File | Source | Built from | Committed? |
| --- | --- | --- | --- |
| `mondo.index.tsv` | [MONDO](https://mondo.monarchinitiative.org/) (CC BY 4.0) | `raw/mondo.obo` | yes |
| `doid.index.tsv`  | [Human Disease Ontology](https://disease-ontology.org/) (CC0) | `raw/doid.obo` | yes |
| `raw/` | downloaded OBO dumps | — | no (git-ignored, large) |

Each `*.index.tsv` is one row per ontology term:

```
id	label	synonyms	snomed	omop	doid	mondo	nci	icd10	orphanet	omim	umls	mesh
```

`synonyms` is ` | `-joined (EXACT synonyms only); each database column holds the
`;`-joined ids that term cross-references there (its own column holds its own id).

## Coverage of the ten target databases

**MONDO is the hub.** A single MONDO term carries xrefs to SNOMED (`SCTID`), DOID,
NCI, ICD-10-CM, Orphanet, OMIM, UMLS and MeSH, so matching a disease name to MONDO
can fill **nine of the ten** columns at once. DOID adds a second, independent lexical
source. Both are freely redistributable, so their indexes are committed here.

**OMOP is not covered.** OMOP concept ids are OHDSI-specific and are not carried by
these ontologies. Predicting OMOP requires the OHDSI **Athena** vocabulary bundle,
which is license-gated and cannot be redistributed here. Likewise, SNOMED/OMIM/UMLS
predictions come *only* via MONDO's xrefs — the primary SNOMED CT, OMIM and UMLS
releases are license-restricted and are not stored in this repo.

To extend predictions with a licensed vocabulary you are entitled to use, drop a
compatible `<db>.index.tsv` (same columns) into this folder — `predict_service`
loads every `*.index.tsv` automatically. Do **not** commit license-restricted data
(the `.gitignore` here blocks the common cases).

## Regenerating

```bash
python scripts/fetch_databases.py              # download raw + rebuild indexes
python scripts/fetch_databases.py --offline    # rebuild from existing raw/ only
python scripts/fetch_databases.py --only mondo  # just one source
```

The predicted-SSSOM snapshot ([`mappings/ari.predicted.sssom.tsv`](../../mappings/ari.predicted.sssom.tsv))
is produced from these indexes plus the current ontology; see `predict_service.build_predicted_sssom`.
