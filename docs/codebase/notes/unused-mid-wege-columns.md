# Unused MiD Wege columns: the drop list and the rule that keeps it safe

`braunschweig.popsim.trips_stage.execute` drops the MiD Wege columns in
`braunschweig.popsim.unused_mid_wege_columns.UNUSED_MID_WEGE_COLUMNS` from the donor Wege before
the trip build joins them onto the synthetic persons. The join copies every Wege column once per
synthetic trip, so a column nobody reads costs memory in every copy of the trip table and buys
nothing. This note is the maintenance rule; WHY the drop happens there, and what was rejected, is
ADR-0138.

## The rule

A listed column must stay unread by first-party code and configuration. When code starts reading
one of them, remove it from `UNUSED_MID_WEGE_COLUMNS` in the SAME change.

`tests/test_unused_mid_wege_columns.py::test_no_listed_column_is_read_by_first_party_code`
enforces this. It scans every file under the first-party code and configuration roots
(`FIRST_PARTY_ROOTS` in that test: the pipeline packages, `scripts/`, `configs/`, CI and the
environment files) and the root-level files, for an exact token of each listed name, also with
every literal merge suffix the code base uses (`_weg`, `_x`, `_y`, `_hh`, `_person`, `_child`,
`_seed`, `_school`, `_home`). A hit fails the test naming the file, the line and the column.
Without the rule, the new reader would receive a trip table without its column: a `KeyError` at
best, and at worst an `if column in frame.columns` branch that quietly takes its fallback.

`test_every_registered_code_path_lies_in_a_scanned_root` keeps the roots complete: every code
path the Stage and Feature Registries name must lie in one of them. A new top-level package
therefore joins `FIRST_PARTY_ROOTS` in the change that registers it.

Outside the scan by design: `tests/` (fixtures may name listed columns), and `docs/` and
`eqasim-data/`, whose committed scripts are diagnostics of past runs. A local environment or
untracked output next to the code is never walked, so it can neither slow the scan down nor fail
it on third-party text. A hit that is not a column read (prose or an unrelated identifier that
happens to spell a listed name, such as `tempo`) is resolved by rephrasing or renaming it.

## What the scan cannot see, and therefore must not be written

- **A column name assembled at run time** (`f"W_VM_{letter}"`, `"opnv_" + suffix`,
  `startswith("alter_gr")`). Spell the full name, so the scan sees it.
- **A merge suffix built at run time** (`suffixes=(f"_{name_a}", f"_{name_b}")`) on a frame that
  carries a listed column.
- **A column addressed by position** (`iloc[:, k]`, `df.columns[k]`) on a trips-derived frame.
- **A result that depends on the column SET** of a trips-derived frame: `drop_duplicates()` or
  `dropna()` without `subset`, a merge without keys. Select the columns first.

## Adding a column to the list, and a new delivery

Only a column that no first-party code or configuration names may be added; the scan must stay
green. A column a future MiD delivery adds is kept automatically, because it is not listed. When a
delivery renames variables, the drop logs a WARNING counting the listed columns it did not find;
re-derive the list then. `trips_stage` hashes the module, so any change to the list rebuilds the
cached trip table and everything downstream of it.
