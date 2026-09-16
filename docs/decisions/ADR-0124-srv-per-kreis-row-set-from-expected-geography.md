# ADR-0124 · 2026-09-16 · SrV per-Kreis row set comes from the expected geography, not the delivery

- **Status:** accepted for implementation
- **Issue:** #405 (found by the #368 package review, deferred from PR #404)

## Context

Two committed SrV reference tables describing the same eight ZGB Kreise disagreed on whether a
Kreis the survey does not cover gets a row:

| | `srv2023_work_by_employment_by_kreis.csv` | `srv2023_work_participation_by_kreis.csv` |
|---|---|---|
| row set from | the data (`adults.groupby("kreis")`) | the contract (`for kreis in ZGB_KREISE`) |
| Wolfsburg 03103 | absent | present, `n_persons=0`, NaN shares |
| rows | 8 (7 Kreise + region) | 9 (8 Kreise + region) |

The two conventions never meet in code — each table has its own reader — so they met in a
maintainer's head, and that already cost work: the #368 target builder carried a hardcoded
`_EXPECTED_SRV_KREIS_CODES = ZGB8 minus Wolfsburg` because the table it reads simply has no row
for Wolfsburg, and any reader expecting eight Kreise silently receives seven.

`srv_participation_universe.py` already applied the opposite rule *inside* the same file: an
empty age BAND is emitted with `n_unweighted = 0` and a NaN share, "never dropped, so a consumer
never has to guess whether a band is missing or empty". Only the Kreis dimension was
data-driven.

## Decision

**The row set of a per-Kreis SrV aggregate is a property of the expected geography, never of the
delivery.** All three builders of the `code,level` family — the two in
`braunschweig/calibration/srv_participation_universe.py` and `compute_participation` in
`scripts/build_srv_participation_aggregate.py` — take `expected_kreise` (default `ZGB_KREISE`)
and emit one row per expected code; a Kreis with no person becomes a zero row with NaN shares.
Each also raises if the universe carries a code outside the expected set, which under a
contract-driven row set would otherwise reach the region row but no Kreis row. Consequences:

1. **NaN, never 0.0, on an empty row.** A 0.0 share reads as a measured rate of zero and would
   flow into a control target as one. NaN forces the consumer to decide explicitly.
2. **The coverage guard changes shape, and gains power.** When a Kreis without persons produced
   no row, a lost Kreis showed up as a *missing* row; now it shows up as a *zero* row, so
   `check_kreis_coverage` had to start rejecting a **surveyed Kreis at `n_unweighted == 0`** —
   otherwise a delivery that lost a Kreis would satisfy every invariant while shipping a hole.
   It also rejects an **unsurveyed Kreis that carries persons**: every consumer applies the
   documented assumption that Wolfsburg's rates come from the region total, and real data makes
   that assumption stale while it keeps being applied.
3. **The reader stops knowing the answer its input states.** Both target builders
   (`build_participation_universe_targets.py`, `build_participation_target.py`) expect all eight
   rows and read *which* Kreise lack a measurable rate from `n_unweighted == 0`. The
   region-total substitution itself is unchanged — a zero row carries no rate, so Wolfsburg's
   rates still come from `03ZGB` — but the substitution is now logged as an explicit
   primary-vs-fallback rate every run (CLAUDE.md fallback transparency), which it never was.
4. **A test asserts the convention on the committed tables**
   (`tests/test_srv_kreis_table_conventions.py`), not only on builder fixtures. The committed
   table is what consumers read and what a future regeneration can silently change.

### Evidence that this is behaviour-preserving

Measured 2026-09-16 against the local raw delivery (`b1dfb244`):

- Before the change, every affected table regenerates **row-identical** to the committed one —
  so the A/B has a trustworthy baseline.
- After the change, the data-row diff is **exactly** the added zero rows: 1 in the work table, 3
  band rows in the education table, 1 in the participation table. Every pre-existing data row is
  unchanged.
- All eight derived `target2026_*` control tables regenerate **byte-identical** (four
  work/education universe targets, four purpose-participation targets). The change does not move
  any scientific result.

## Rejected alternatives

**Renaming the `work_participation` region row `zgb` → `03ZGB`** (proposed as part of #405 on the
grounds that `03ZGB` matches the `03xxx` Kreis codes). Rejected on two measurements:

- `03ZGB` is the *minority* convention, not the standard. Across the 20 committed per-Kreis SrV
  tables: `total` 11×, `zgb` 5×, `03ZGB` 3×, `Gesamt` 1×. Renaming would move
  `work_participation` **away** from its own family — `commute_distance*` and
  `education_distance*`, all written by `srv_distance_targets.py` and read by the commute
  analysis stage, all `zgb`.
- It is not a data-only change. `braunschweig/analysis/synthesis/work_participation_by_kreis.py`
  uses `ZGB_ROW_CODE = "zgb"` both to read the SrV reference **and** to build its own model-side
  aggregate rows, which are joined on `code`. Renaming the table's region code forces renaming
  the `code` values in that stage's output tables, invalidating the codes quoted in existing run
  manifests — far outside the issue's "five touch points per table".

**Applying the convention to every committed per-Kreis SrV table in this change.** The issue's
acceptance criterion asks for a test over *every* such table; three different families exist.
All three tables of the `code,level` family are now on the convention, including
`srv2023_participation_by_kreis.csv`, which carried the same defect
(`persons.groupby("ars5")` in `scripts/build_srv_participation_aggregate.py`). The other two
families are out of scope and named in the docstring of
`tests/test_srv_kreis_table_conventions.py`: they are different contracts (a
`total,total,Gesamt` region row plus `stratum` rows; a documented RS7 proxy instead of a zero
row), not the same contract applied inconsistently.

The participation table was initially deferred on an over-broad estimate of its consumer
surface: grepping `participation_by_kreis.csv` matches the four DERIVED
`target2026_<purpose>_participation_by_kreis.csv` files too, which made 16 files look like
readers of the SrV aggregate. Exactly one module reads it
(`scripts/build_participation_target.py`), plus two tests — the same shape as the other two
tables. Recorded because the measurement, not the estimate, is what decided the scope.

That table carried a second defect the row change would otherwise have amplified: its share
formula was `... if tot > 0 else 0.0`, so an empty Kreis would have been emitted with a
participation rate of **0.0** — a fabricated measured zero flowing straight into a control
target. Empty rows are NaN; a Kreis that has persons but none of a given purpose keeps its
genuine 0.0. `scripts/build_participation_target.py` also had no completeness guard at all
(only `kreis_rows.empty`), so a source that lost a Kreis would have shipped a short target
silently; it now expects all eight rows and logs the primary-vs-fallback rate like its sibling.

**Changing column order in `work_participation` (`level,code` → `code,level`).** Every reader
goes through pandas by column name (verified: no positional or `header=None` read of these
tables), so the order buys no correctness. Within the `code,level` family the order is now
asserted by the new test; imposing it on `work_participation` would force regenerating a third
table from restricted microdata for a cosmetic gain, and that table is staying on its own
family's conventions per the rejection above.
