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
| `census_person_id` | `java.lang.Long` | Synthetic integer record id set by `enriched_adapter` from `person_id` |
| `census_household_id` | `java.lang.Long` | Synthetic integer household record id set by `enriched_adapter` from `household_id` |
| `source_person_id` | internal only (not written directly) | Donor person surrogate (see below); single source-of-truth for "which donor person" |
| `source_household_id` | internal only (not written directly) | Donor household surrogate (see below) |
| `hts_id` | `java.lang.Long` | Written as `htsPersonId`; filled from `source_person_id` (surrogate integer) by `enriched_adapter` |
| `hts_household_id` | `java.lang.Long` | Written as `htsHouseholdId`; filled from `source_household_id` (surrogate integer) by `enriched_adapter` |

## Data-protection design (popsim_mid)

MiD 2023 is restricted scientific-use microdata (BMDV licence). Publishing the
raw `H_ID` / `P_ID` values in the synthetic output would allow re-identification
of the survey respondent each synthetic agent was derived from. Two leak paths
existed in the original design:

1. `source_person_id` / `source_household_id` were set to the raw MiD `P_ID` / `H_ID`.
2. The popsim `person_id` format `<cell>_<H_ID>_<occurrence>[_<P_ID>]` embeds both
   ids; `synthesis.population.sampled` copied these strings to `census_*`.

Both leaks are closed by:

1. **Donor surrogates** (`braunschweig.popsim.assembly.assign_donor_surrogates`):
   each unique donor household `H_ID` is assigned a sequential integer surrogate
   (`pd.factorize(H_ID, sort=True)[0] + 1`), and each unique donor person
   `(H_ID, P_ID)` a sequential integer surrogate. `source_person_id` and
   `source_household_id` carry these surrogates -- they are numeric (clean
   `java.lang.Long`) and reveal nothing about the real MiD respondent without the
   mapping. The surrogates are deterministic (sort=True) and reproducible.

2. **census_* overwrite** (`braunschweig.popsim.enriched_adapter.run`): the adapter
   ALWAYS overwrites `census_person_id` / `census_household_id` with the current
   integer `person_id` / `household_id` (assigned by `sampled`). For popsim_mid
   there is no separate per-person census record, so the synthetic integer id is
   the natural non-leaking record id. This replaces the former "only-if-absent" guard
   which would have preserved the leaking embedding strings.

3. **Local-only pseudonym map**: `braunschweig.popsim.stage` writes
   `work_dir/pseudonym_map.csv` (columns: `source_person_id, source_household_id,
   H_ID, P_ID`) for internal re-linking. This file is in the pipeline `work_dir`
   (local-only, gitignored path) and must NEVER be committed or published.

The raw `H_ID` / `P_ID` columns remain on the internal persons frame for the trips
join (`braunschweig.popsim.trips` needs them) but are NOT included in any
output/writer field list (verified: `matsim.scenario.population.PERSON_FIELDS` and
`synthesis.output.select_person_output_columns` contain none of `H_ID`, `P_ID`).

## Long-or-String type selection (bug D2 fix)

`matsim/writers.long_or_string_type(value)` selects the MATSim attribute class at
write time:

- If `value` can be parsed as an integer -> `java.lang.Long` (default pipeline, byte-identical).
- Otherwise -> `java.lang.String` (fallback).

After the pseudonymisation fix, all four id attributes (`censusHouseholdId`,
`censusPersonId`, `htsHouseholdId`, `htsPersonId`) are integers for popsim_mid
(as for the IPF pipeline), so they are uniformly written as `java.lang.Long`.
The `class="java.lang.String"` path is no longer exercised by popsim_mid.

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
| `census_person_id` | synthetic integer `person_id` (set by `enriched_adapter`, always overwritten) | `42` | integer -> written as `java.lang.Long` |
| `census_household_id` | synthetic integer `household_id` (set by `enriched_adapter`, always overwritten) | `7` | integer -> written as `java.lang.Long` |
| `source_person_id` | donor person surrogate (sequential integer, not raw `P_ID`) | `3` | integer |
| `source_household_id` | donor household surrogate (sequential integer, not raw `H_ID`) | `2` | integer |
| `hts_id` | copied from `source_person_id` (surrogate) by `enriched_adapter` | `3` | integer -> written as `java.lang.Long` |
| `hts_household_id` | copied from `source_household_id` (surrogate) by `enriched_adapter` | `2` | integer -> written as `java.lang.Long` |

Note: All id attributes for `popsim_mid` are now integers so they are uniformly
written as `java.lang.Long`. The former `java.lang.String` path for alphanumeric
popsim embedding strings (`<cell>_<H_ID>_...`) is no longer reached.

## The `enriched_adapter` contract

`braunschweig/popsim/enriched_adapter.py` (aliased to `synthesis.population.enriched`
for `popsim_mid`) is the only stage that sets `hts_id` / `hts_household_id` from
`source_*`. Its invariants:

1. `hts_id = source_person_id` and `hts_household_id = source_household_id` (always set).
2. `census_person_id = person_id` and `census_household_id = household_id` (ALWAYS
   overwritten with the current integer synthetic ids). This replaces the former
   "only-if-absent" guard, which was intentionally changed to close the data-protection
   leak: `synthesis.population.sampled` copies the leaking popsim embedding strings
   (e.g. `"<cell>_<H_ID>_<occurrence>[_<P_ID>]"`) to `census_*` before reassigning
   integer ids; the adapter overwrites them with clean integer ids.

## `synthesis.population.sampled` contract

`synthesis/population/sampled.py` is producer-neutral. It:

1. Copies `person_id` -> `census_person_id` and `household_id` -> `census_household_id`
   BEFORE reassigning new sequential integer ids.
2. Assigns new sequential `person_id` (0..N-1) and `household_id` (0..M-1).

For `simple_ipf_open` the original ids are integers, so `census_*` are integers
(the enriched_adapter for IPF does not touch `census_*`).
For `popsim_mid` the original ids are alphanumeric embedding strings (set by
`expand.py`), but the `enriched_adapter` always overwrites `census_*` with the
final integer ids so no embedding string reaches the output.

## Java side compatibility note

After the pseudonymisation fix, all four id attributes written to the MATSim XML
(`censusPersonId`, `censusHouseholdId`, `htsPersonId`, `htsHouseholdId`) are
integers for `popsim_mid`, so they are written as `java.lang.Long` (same as the
IPF pipeline). The former concern about `ClassCastException` on alphanumeric
`censusPersonId` strings no longer applies. The eqasim Java `EqasimHtsPersonFilter`
reads `htsPersonId` as `Long`; this continues to work unchanged.

## Internal traceability: pseudonym_map.csv

To enable re-linking from surrogate back to the original MiD respondent for
internal scientific use, `braunschweig.popsim.stage` writes a local-only file:

```
<work_dir>/pseudonym_map.csv
```

Columns: `source_person_id, source_household_id, H_ID, P_ID`

This file is in the pipeline `work_dir` (local-only, gitignored path).
It MUST NOT be committed, published, or included in any output. The surrogate
assignment is deterministic (factorize sort=True) and reproducible without the
file, but the file provides a convenient lookup without re-running assembly.
