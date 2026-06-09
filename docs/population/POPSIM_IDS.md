# Unified ID scheme for eqasim-bs population producers

This document is the single source of truth for how every id column maps across
the two supported population producers (`simple_ipf_open` and `popsim_mid`).
The MATSim writer (`matsim/scenario/population.py`, `matsim/scenario/households.py`)
reads these columns by name; do not rename them without updating the writer and this doc.

## Column definitions

| Column | Type at writer | Meaning |
|---|---|---|
| `person_id` | `java.lang.Integer` (person XML `id=`) | Synthetic working id, reassigned to sequential integers by `synthesis.population.sampled` |
| `household_id` | `java.lang.Integer` (household XML `id=`) | Synthetic working id, reassigned to sequential integers by `synthesis.population.sampled` |
| `census_person_id` | `java.lang.Long` or `java.lang.String` (see below) | Producer record id: the ORIGINAL id BEFORE `sampled` reassigned it |
| `census_household_id` | `java.lang.Long` or `java.lang.String` (see below) | Producer household record id: the ORIGINAL household id BEFORE reassignment |
| `source_person_id` | internal only (not written directly) | Producer-agnostic donor key; the single source-of-truth for "where did this person come from" |
| `source_household_id` | internal only (not written directly) | Producer-agnostic donor household key |
| `hts_id` | `java.lang.Long` or `java.lang.String` (see below) | Written as `htsPersonId`; filled from `source_person_id` by `enriched_adapter` |
| `hts_household_id` | `java.lang.Long` or `java.lang.String` (see below) | Written as `htsHouseholdId`; filled from `source_household_id` by `enriched_adapter` |

## Long-or-String type selection (bug D2 fix)

`matsim/writers.long_or_string_type(value)` selects the MATSim attribute class at
write time:

- If `value` can be parsed as an integer -> `java.lang.Long` (default pipeline, byte-identical).
- Otherwise -> `java.lang.String` (popsim_mid alphanumeric provenance ids).

This means the four id attributes (`censusHouseholdId`, `censusPersonId`,
`htsHouseholdId`, `htsPersonId`) carry `class="java.lang.Long"` for the IPF
pipeline and `class="java.lang.String"` for popsim_mid. The Java
`ObjectAttributesIO` reader can handle both types for String attributes; the crash
was caused by MATSim trying to parse an alphanumeric String as a Long at load time.

## Per-producer mapping

### `simple_ipf_open` (default)

Producer: `braunschweig.ipf.attributed` -> `synthesis.population.matched`

| Column | Value | Type |
|---|---|---|
| `person_id` | sequential integer (0, 1, 2, ...) after `sampled` | integer |
| `household_id` | sequential integer after `sampled` | integer |
| `census_person_id` | original Zensus person integer id (before `sampled`) | integer -> written as `java.lang.Long` |
| `census_household_id` | original Zensus household integer id (before `sampled`) | integer -> written as `java.lang.Long` |
| `source_person_id` | not used (HTS matched separately) | - |
| `source_household_id` | not used (HTS matched separately) | - |
| `hts_id` | ENTD/MiD person integer id (from HTS survey match) | integer -> written as `java.lang.Long` |
| `hts_household_id` | ENTD/MiD household integer id (from HTS survey match) | integer -> written as `java.lang.Long` |

### `popsim_mid`

Producer: `braunschweig.popsim.assembly` -> `synthesis.population.sampled` -> `braunschweig.popsim.enriched_adapter`

| Column | Value | Example | Type |
|---|---|---|---|
| `person_id` | sequential integer (0, 1, 2, ...) after `sampled` | `42` | integer |
| `household_id` | sequential integer after `sampled` | `7` | integer |
| `census_person_id` | original popsim person string id (before `sampled`): `<cell>_<H_ID>_<occurrence>_<P_ID>` | `"10N548_E43_1234_0_1"` | alphanumeric -> written as `java.lang.String` |
| `census_household_id` | original popsim household string id (before `sampled`): `<cell>_<H_ID>_<occurrence>` | `"10N548_E43_1234_0"` | alphanumeric -> written as `java.lang.String` |
| `source_person_id` | MiD `P_ID` (numeric string) | `"678"` | numeric string |
| `source_household_id` | MiD `H_ID` (numeric string) | `"1234"` | numeric string |
| `hts_id` | copied from `source_person_id` by `enriched_adapter` | `"678"` | numeric string -> written as `java.lang.Long` |
| `hts_household_id` | copied from `source_household_id` by `enriched_adapter` | `"1234"` | numeric string -> written as `java.lang.Long` |

Note: `hts_id` / `hts_household_id` are MiD `P_ID` / `H_ID` which are always
numeric, so they continue to be written as `java.lang.Long` even in `popsim_mid`.
Only `census_*` are alphanumeric compound popsim provenance strings.

## The `enriched_adapter` contract

`braunschweig/popsim/enriched_adapter.py` (aliased to `synthesis.population.enriched`
for `popsim_mid`) is the only stage that sets `hts_id` / `hts_household_id` from
`source_*`. Its invariants:

1. `hts_id = source_person_id` and `hts_household_id = source_household_id` (always set).
2. `census_person_id` and `census_household_id` are set only when ABSENT. They are
   already present after `synthesis.population.sampled` (which copies the original
   popsim string ids to `census_*` before overwriting `person_id`/`household_id`).
   Overwriting here would destroy the popsim provenance chain.

## `synthesis.population.sampled` contract

`synthesis/population/sampled.py` is producer-neutral. It:

1. Copies `person_id` -> `census_person_id` and `household_id` -> `census_household_id`
   BEFORE reassigning new sequential integer ids.
2. Assigns new sequential `person_id` (0..N-1) and `household_id` (0..M-1).

For `simple_ipf_open` the original ids are integers, so `census_*` are integers.
For `popsim_mid` the original ids are alphanumeric popsim strings (set by
`expand.py`), so `census_*` carry those strings to the writer unchanged.

## Java side compatibility note

The eqasim Java side reads `censusPersonId`, `censusHouseholdId`, `htsPersonId`,
`htsHouseholdId` via `ObjectAttributes` (MATSim population attributes XML). As of
the current eqasim Bavaria fork these attributes are read as `Long` in the HTS
matching code (`EqasimHtsPersonFilter`). When `popsim_mid` is used as the
population source:

- `htsPersonId` / `htsHouseholdId` remain `Long` (MiD `P_ID`/`H_ID` are numeric),
  so the Java HTS reader is unaffected.
- `censusPersonId` / `censusHouseholdId` are `String`. If the Java code reads
  these as `Long` it will throw a `ClassCastException`. Currently these attributes
  are only used for traceability (not for any Java computation), so the cast is not
  triggered. If future Java code reads `censusPersonId` as `Long` for `popsim_mid`
  scenarios, either cast to `String` first or introduce a new `String`-typed
  attribute name (e.g. `censusPersonIdStr`). This concern is flagged here so it is
  not silently overlooked.
