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
enforces this: it scans every first-party code and configuration file (outside `tests/`,
`docs/`, `eqasim-data/`) for an exact token of each listed name, also with the merge suffixes
`_weg`, `_x` and `_y`, and fails naming the file, the line and the column. Without the rule, the
new reader would receive a trip table without its column: a `KeyError` at best, and at worst an
`if column in frame.columns` branch that quietly takes its fallback.

## What the scan cannot see, and therefore must not be written

- **A column name assembled at run time** (`f"W_VM_{letter}"`, `"opnv_" + suffix`,
  `startswith("alter_gr")`). Spell the full name, so the scan sees it.
- **A column addressed by position** (`iloc[:, k]`, `df.columns[k]`) on a trips-derived frame.
- **A result that depends on the column SET** of a trips-derived frame: `drop_duplicates()` or
  `dropna()` without `subset`, a merge without keys. Select the columns first.

## Adding a column to the list

Only a column that no first-party code or configuration reads may be added; the scan must stay
green. A column a future MiD delivery adds is kept automatically, because it is not listed.
`trips_stage` hashes the module, so any change to the list rebuilds the cached trip table and
everything downstream of it.
