# ADR-0138 · 2026-09-28 · Drop the never-read MiD Wege columns before the synthetic trip build

- **Status:** active
- **Numbering:** ADR-0138 is the next free id. Checked on 2026-09-28 across ALL local and remote
  branches (`git ls-tree docs/decisions/` for every ref): `main` holds up to ADR-0133; the unmerged
  branches hold ADR-0130 to ADR-0132 (`codex/*`), ADR-0134 and ADR-0136 (`chore/i434-*`), ADR-0135
  (`fix/parallel-memory-robustness`) and ADR-0137 (`feature/employment-grid-kreis-age-shape`).
- **Issue:** none. Done directly on the user's instruction of 2026-09-28, as the follow-up that
  ADR-0135 (branch `fix/parallel-memory-robustness`, not yet merged at the time of writing) lists
  under its rejected alternatives ("Narrowing the trip table").

## Context

`braunschweig.popsim.trips_stage` builds the synthetic trip table by joining the donor MiD Wege
onto every synthetic person that takes its plan from that donor
(`braunschweig.popsim.trips.expand_persons_to_trips`, a `persons.merge(wege, ...)`). Each Wege
column is therefore copied once per synthetic TRIP, and every intermediate copy the build makes of
the table (the trip-time repair, the plan repair, the sorts) carries all of them.
`braunschweig.popsim.mid.donor.load_mid_wege` loads every column of the delivery on purpose, "so
that every MiD Wege extra column is available as a traceability/analysis extra in the output trip
table", and `trips_stage.run` passes them all through. The trip table of the 100 % arm-3 run of
2026-09-07 had 3,655,159 rows and 296 columns (committed column dump
`eqasim-data/data/braunschweig/calibration/plan_structure_fix_arm3_100pct_2026-09-07/trip_table_diagnostics_arm3.txt`).

In the 100 % production run of 2026-09-25 this stage drove the driver to 86.32 GiB of the
machine's 94.28 GiB, leaving 10.14 GiB; the out-of-memory kill of that run came later, in the
chainsolver stage. Both figures are from the run manifest `chainsolver-pool-memory-2026-09-28`,
which, like ADR-0135, is on the branch `fix/parallel-memory-robustness`. ADR-0135 reduces the
number of copies the plan repair holds, and defers narrowing the table itself until every reader
of those columns has been audited, because a reader that quietly falls back when a column is
missing would change results unnoticed.

The audit, done for this decision on 2026-09-28:

- **Readers by name.** 157 of the delivery's Wege columns are named in no tracked first-party code
  or configuration file -- including the committed diagnostics scripts of past runs under
  `eqasim-data/` and `docs/` -- neither as an exact token nor with a merge suffix. All 157 are
  columns of the arm-3 trip table above, in the same order; without them it keeps 139 columns.
  `tests/test_unused_mid_wege_columns.py` repeats this scan over the code and configuration roots
  on every test run.
- **Readers by construction** (a code review, not a committed check). No first-party code builds
  one of those names at run time: no string literal or f-string that is a prefix of one of them
  addresses a column (the literal-prefix hits are mode labels such as `"car"` or `"rad"` and
  aggregate names such as `"min"`), and no `startswith`, `filter(like=...)`, `filter(regex=...)` or
  `select_dtypes` selects columns of a trip frame.
- **Operations that depend on the column SET** (a code review, not a committed check). No
  `drop_duplicates()` or `dropna()` without a subset, no merge without keys and no positional
  column access runs on a frame derived from the trip table; every such call in the code base
  works on an explicit column selection or on a different frame. `synthesis.output` writes an
  explicit list of ten trip columns and `matsim.scenario.population` writes `TRIP_FIELDS`, so no
  output file carries the Wege extras.

Both reviews were done twice on 2026-09-28, by the author and in a separate review of the branch.

## Decision

1. **The list.** `braunschweig.popsim.unused_mid_wege_columns.UNUSED_MID_WEGE_COLUMNS` names the
   157 columns, in the delivery's column order. It claims only that no first-party code or
   configuration names them; what each variable means is documented in the MiD 2023 codeplan. It
   is a subset of the unread columns, not their complement: `W_SZ` and `W_AZ`, named only by a
   committed diagnostics script of the arm-3 run, are kept.
2. **Where they are dropped.** `trips_stage.execute` drops them from the donor Wege right after
   loading them for the MiD source, before handing the Wege to the trip build. Rebinding the local
   name also releases the full-width frame. The MiD households and persons frames that
   `execute` loads but never reads are released at the same point. The ENTD path is untouched:
   its frames carry no MiD names.
3. **Observable.** `drop_unused_mid_wege_columns` logs, at INFO, the dropped and kept column counts
   and the Wege frame's in-memory size before and after
   (`[trips_stage] MiD Wege columns no first-party code reads: dropped ...`). Listed columns the
   frame does not carry are a WARNING: on a real delivery they mean renamed variables and a list
   that needs re-deriving.
4. **Enforced.** `tests/test_unused_mid_wege_columns.py` fails, naming the file, line and column,
   as soon as a file under the first-party code and configuration roots names a listed column,
   also with every literal merge suffix the code base uses. A second test keeps those roots complete against
   every code path the Stage and Feature Registries name. The maintainer rule -- remove the column
   from the list in the same change -- is in `docs/codebase/notes/unused-mid-wege-columns.md`.
5. **Cached correctly.** `trips_stage` hashes the module (`_HELPER_MODULES`), so a change to the
   list rebuilds the cached trip table.

## Rejected alternatives

- **A keep-list of the read columns.** A column a future delivery adds, or one read through a
  path the scan cannot see, would silently vanish. The drop list removes only columns shown to
  be unread and lets every other column through, and "every listed name is unread" is a claim a
  test can check.
- **Dropping inside `trips_stage.run`.** `execute` and `MidSource.build_trips` would still hold
  the full-width donor frame for the whole build, so only the copies would shrink.
- **Dropping in the loader** (`load_mid_wege` / `MidSource.load_donor`). Every other consumer of
  the loader (the PopulationSim seed, the diary match, the distance distributions) would lose
  the columns too, and both modules are hashed by the PopulationSim stage, which would be rebuilt
  for a change confined to the trip build.
- **Also narrowing the persons side of the join.** The trip table also carries every column of
  the synthetic persons frame. Person attributes are read by name throughout the code base, so a
  token scan cannot show which of them nobody reads FROM THE TRIP TABLE; that needs an audit of
  each consumer and stays outside this decision.
- **A configuration flag with an OFF path.** Nothing can change (the columns are unread, and the
  scan enforces it), so a second code path would only add maintenance.
  `test_run_output_does_not_depend_on_the_unused_mid_wege_columns` pins the equivalence instead:
  under the production flag set, on both plan-validation paths and on a fixture that synthesises
  closures, replaces unfixable chains and pairs passive escort legs, every remaining column of the
  trip table is identical with and without the listed columns in values, dtypes, row order and
  relative column order.

## Consequences

- **Results do not change.** No dropped column is read (scan), and the remaining trip table is
  identical (test above). `trips.csv` never carried the columns.
- **The cached trip table and `synthesis.population.trips.final` lose the 157 columns.**
  `braunschweig.synthesis.commute_day.plan_replacement` nulls correspondingly fewer extra columns
  for the replaced persons; its logged count of nulled columns falls.
- **Memory.** The donor Wege frame and every copy of the trip table lose those columns. No figure
  is claimed here: the log line of point 3 reports the donor frame's size in every run, and the
  first production run after this change measures the stage peak; its run manifest carries the
  evidence.
- **Cache.** The next run recomputes `braunschweig.popsim.trips_stage` (its own source changed)
  and every stage downstream of it, plus
  `braunschweig.synthesis.commute_day.home_office_donors_stage`, which hashes `trips_stage.py`
  without depending on its output and therefore recomputes once with an unchanged result.
  PopulationSim stays cached: `trips_stage` is not part of its validation token.
- **Traceability.** The dropped columns carried no identity. A diary trip keeps `H_ID`, `P_ID` and
  `W_ID` from the join (`trip_key` is `<person_id>_<W_ID>`), through which any dropped variable can
  still be joined back from the delivery. Rows that never carried donor values lose nothing: a
  synthesised closure is written with NaN in every extra column (`trip_key`
  `<person_id>_closure`), and `plan_replacement` nulls the extras of the persons whose day it
  replaces.
- **Two stale statements stay for now.** In `braunschweig/popsim/mid/donor.py`, the docstring of
  `load_mid_wege` still gives the old reason for loading every column, and the comment above
  `MID_WEGE_REQUIRED_COLS` still says every remaining column is carried as an extra. They are not
  edited here because `donor.py` is part of the PopulationSim stage's validation token, so a
  comment-only edit would rebuild PopulationSim. The loader still loads every column; only the
  stated purpose is outdated. Both are to be corrected together with the next substantive change
  to that module.
