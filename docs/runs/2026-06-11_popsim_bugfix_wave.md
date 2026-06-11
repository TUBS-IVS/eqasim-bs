# Popsim bugfix wave — validation summary (2026-06-11)

Branch: `feature/population-method-workflows` (after merging `main`/cordon).
Scope: all 12 verified bugs from the 2026-06-10 review (docs/codebase/CONCERNS.md),
the 4 red main tests, and feature-parity flags for the popsim configs.
28 commits, 55 files, ~+3.1k/-0.3k lines. Design doc:
docs/superpowers/specs/2026-06-10-popsim-bugfix-wave-design.md (local-only).

## Validation results

1. **Fast test suite: 1504 passed, 0 failed, 27 skipped** (baseline before the
   wave: 5-6 failed). Local-only-data tests now skip with a reason when the
   gitignored input is absent.
2. **Three 1 % mini smokes all EXIT 0**: simple_ipf (regression, unchanged),
   popsim_mid_mini (16/16 stages), popsim_open_mini. No `braunschweig.ipf.*`
   stage executes in the popsim DAGs anymore (gravity reads the
   `data.census.filtered` alias).
3. **Three-case comparability (1 % smoke outputs, BS city cells only):**

| metric | simple_ipf_open | popsim_mid (before -> after) | popsim_open (before -> after) |
|---|---|---|---|
| has_driving_license | 72.9 % | 52.2 % -> **80.9 %** | 70.8 % (unchanged) |
| trips/person | 3.14 | 3.03 -> 2.58* | 1.48 -> **3.68** |
| persons | 11,474 | 2,548 -> 2,831 (member completion, D3) | 2,557 |
| employed | 41.6 % | 57.1 % -> 52.8 % | 43.4 % |
| high_income (hh) | 19.7 % | 31.4 % -> 32.6 % | 8.7 % |

*popsim_mid trips/person reflects the rbW time-code handling: 13.1 % of donor
persons carry non-collected times (code 701) and are resampled from same-cell
donors; in the 1 % mini smoke 31.8 % of those found no same-cell donor and fell
back to a home-only plan (loudly logged). At higher sampling rates the same-cell
donor pools grow and this rate falls; a commune-level fallback cascade is a
known candidate improvement.

## Intentional result changes (by design, user-approved)

- Licence share popsim_mid rises to the P17.1-plausible range (adult coverage
  codes 202/404 imputed instead of forced False).
- popsim_open persons all carry chains (trip-less persons matched to ENTD diary
  donors; coverage log line reports direct/matched/trip-less shares).
- popsim_mid households are member-complete (16.9 % of seed households filled by
  mirror-household sampling; fillers carry `member_imputed` + `source_*` keys
  and inherit the mirror donor's Wege).
- Business trips (W_ZWECK=2) no longer set the commute distance (first home-leg
  + seeded CDF imputation).
- sex is binary for MATSim; `sex_raw` (male/female/diverse/not_specified)
  retained in the synthesis output.

## Known remaining (next wave: PopulationSim controls)

- employed_share and high_income_share for popsim_mid still reflect the MiD
  donor skew — to be constrained via Kreis-level employment and MiD-H4×Zensus
  income count controls (proposal in the 2026-06-10 session report).
- handoff (100 m cell -> building home locations) still unwired; housing_tenure
  for popsim paths still absent.
- Cordon × popsim and fleet × popsim are flag-enabled but not yet e2e-tested on
  a real run (config parity commit bccb21f lists the honest caveats).
