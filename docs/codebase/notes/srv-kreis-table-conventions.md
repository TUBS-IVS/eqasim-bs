# SrV per-Kreis reference table conventions

The rule to follow when adding or consuming a committed SrV 2023 per-Kreis
reference table under `eqasim-data/data/braunschweig/srv/`. Recorded because two
row conventions coexist and nothing said which one is the default — the #368
whole-branch review triaged that as an open minor after `work_by_employment` and
`education_by_age` were added alongside an older table shaped differently.

## The canonical Kreis codes

`braunschweig.analysis.spatial.ZGB8` is the ONE mapping from the eight 5-digit
ARS codes to their display names. Never re-list the pairs in a CSV header, a
Data Registry record, a docstring or a test — read them from `ZGB8`, or point at
it. The registry records for these tables previously spelled the mapping out as
prose, which had already drifted (the prose dropped the `SK` / `LK` prefixes
`ZGB8` carries).

## The dominant convention (use this for a new table)

Columns start `code,level,...`; `code` is the 5-digit ARS, `level` is `kreis`
for a Kreis row and `total` for the region row, whose `code` is `03ZGB`. Seven
Kreis rows: Wolfsburg (`03103`) is not surveyed by SrV and gets NO row, so a
consumer that needs all eight fills Wolfsburg from the region total and
documents that as an assumption (`scripts/build_participation_target.py` is the
worked example). Tables following it:

- `srv2023_participation_by_kreis.csv`
- `srv2023_work_by_employment_by_kreis.csv`
- `srv2023_education_by_age_by_kreis.csv`

## The one exception

`srv2023_work_participation_by_kreis.csv` differs in three ways at once, and is
NOT the pattern to copy:

- columns start `level,code,...` — the two swapped;
- the region row is coded `zgb`, lowercase, with `level` also `zgb`;
- it carries EIGHT Kreis rows, including a PLACEHOLDER `03103` Wolfsburg row with
  `n_persons = 0` and empty (NaN) shares. That row is not a filled value: a
  consumer must treat it as absent exactly as it treats a missing row in the
  seven-row tables, and must never read its shares as zeros.

It is a validation reference for the commute-day-state model, not a control
target, which is why the divergence was never load-bearing enough to justify
regenerating it and re-pointing its consumers. Leave it as it is; a consumer
reading both must branch on the table, not assume one shape.

## What this means for a consumer

Match on the table's own convention, never on "the SrV convention". A lookup
written against `03ZGB` silently finds nothing in a table whose total row is
`zgb`, and a positional read of the first column gets `level` instead of `code`
from the exception above. Each table's Data Registry record under
`docs/registry/data/` states its own shape; that record is the authority for
that table.
