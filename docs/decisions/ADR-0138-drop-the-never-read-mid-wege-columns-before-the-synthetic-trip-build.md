# ADR-0138 · 2026-09-28 · Drop the never-read MiD Wege columns before the synthetic trip build

- **Status:** active
- **Numbering:** ADR-0138 is the next free id. Checked on 2026-09-28 across ALL local and remote
  branches (`git ls-tree docs/decisions/` for every ref): `main` holds up to ADR-0133; the unmerged
  branches hold ADR-0130 to ADR-0132 (`codex/*`), ADR-0134 and ADR-0136 (`chore/i434-*`), ADR-0135
  (`fix/parallel-memory-robustness`) and ADR-0137 (`feature/employment-grid-kreis-age-shape`).
- **Issue:** none. Done directly on the user's instruction of 2026-09-28, as the follow-up that
  ADR-0135 (branch `fix/parallel-memory-robustness`) lists under its rejected alternatives
  ("Narrowing the trip table").

## Context

`braunschweig.popsim.trips_stage` builds the synthetic trip table by joining the donor MiD Wege
onto every synthetic person that takes its plan from that donor
(`braunschweig.popsim.trips.expand_persons_to_trips`, a `persons.merge(wege, ...)`). Each Wege
column is therefore copied once per synthetic TRIP, and every intermediate copy the build makes of
the table (the trip-time repair, the plan repair, the sorts) carries all of them.
`braunschweig.popsim.mid.donor.load_mid_wege` loads every column of the delivery on purpose, "so
that every MiD Wege extra column is available as a traceability/analysis extra in the output trip
table", and `trips_stage.run` passes them all through.

The 100 % production run of 2026-09-25 reached the machine's memory limit in this stage.
ADR-0135 cut the number of copies the plan repair holds, and deferred narrowing the table itself
until every reader of those columns was audited, because a reader that quietly falls back when a
column is missing would change results unnoticed.

The audit, done for this decision on 2026-09-28 over every first-party code and configuration
file (outside `tests/`, `docs/` and `eqasim-data/`):

- **Readers by name.** 157 of the delivery's Wege columns occur nowhere as an exact token, also
  not with a merge suffix (`_weg`, `_x`, `_y`). `tests/test_unused_mid_wege_columns.py` repeats
  this scan on every test run.
- **Readers by construction.** No first-party code builds one of those names at run time: no
  string literal or f-string that is a prefix of one of them is used to address a column (the
  literal-prefix hits are mode labels such as `"car"` or `"rad"` and aggregate names such as
  `"min"`), and no configuration file names one of them.
- **Operations that depend on the column SET.** No `drop_duplicates()` or `dropna()` without a
  subset, no merge without keys and no positional column access runs on a frame derived from the
  trip table; every such call in the code base works on an explicit column selection or on a
  different frame. `synthesis.output` writes an explicit list of ten trip columns and
  `matsim.scenario.population` writes `TRIP_FIELDS`, so no output file carries the Wege extras.

## Decision

1. **The list.** `braunschweig.popsim.unused_mid_wege_columns.UNUSED_MID_WEGE_COLUMNS` names the
   157 never-read columns, in the delivery's column order. It claims only that no first-party code
   reads them; what each variable means is documented in the MiD 2023 codeplan.
2. **Where they are dropped.** `trips_stage.execute` drops them from the donor Wege right after
   loading them for the MiD source, before handing the Wege to the trip build. Rebinding the local
   name also releases the full-width frame. The MiD households and persons frames that
   `execute` loads but never reads are released at the same point. The ENTD path is untouched:
   its frames carry no MiD names.
3. **Observable.** `drop_unused_mid_wege_columns` logs the dropped and kept column counts, the
   listed columns the frame does not carry, and the Wege frame's in-memory size before and after
   (`[trips_stage] MiD Wege columns no first-party code reads: dropped ...`).
4. **Enforced.** `tests/test_unused_mid_wege_columns.py` fails, naming the file and the column,
   as soon as first-party code or configuration reads a listed column. The maintainer rule --
   remove the column from the list in the same change -- is in
   `docs/codebase/notes/unused-mid-wege-columns.md`.
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
  with and without the listed columns, every remaining column of the trip table is identical in
  values, dtypes and order.

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
  and every stage downstream of it. PopulationSim stays cached: `trips_stage` is not part of its
  validation token.
- **Traceability.** Every trip keeps its donor keys (`H_ID`, `P_ID`, `W_ID`, and `trip_key` built
  from them), through which any dropped MiD variable can be joined back from the delivery.
- **A stale sentence stays for now.** The docstring of `load_mid_wege` still gives the old reason
  for loading every column. It is not edited here because `braunschweig/popsim/mid/donor.py` is
  part of the PopulationSim stage's validation token, so a docstring-only edit would rebuild
  PopulationSim. The loader still loads every column; only its stated purpose is outdated. It is
  to be corrected together with the next substantive change to that module.
