# Outside-tour modes (`braunschweig/synthesis/outside_tour_modes/`)

Maintenance rules for the layer that judges the modes of the tours frozen at the cordon: which invariants a
change must keep, how the stage is wired and how to read its log and report. Why the layer exists, what was
rejected and the assumptions are in ADR-0145 (issue #453); the production state is the Feature Registry record
`outside_tour_modes`; the stage semantics are in the stage record
`synthesis.population.trips.simulated` (the DAG node of the stage behind the alias). The SrV table the
rule reads is described by the data record `srv2023_mode_by_distance_band`. Nothing here repeats those.

## Layout

| Module | Role |
|---|---|
| `config_keys.py` | The ONE home of the `braunschweig.outside_tour_modes.*` key names and defaults, `validate_settings`, the alias and input stage names, the RNG offset, the report file name and the always-available modes. Every reader declares the keys with these constants. |
| `srv_table.py` | `ModeShareTable`: the cell lookup over the committed SrV table (band of a distance, the top band, the plausible modes of a cell, the fail-early check that neither pt nor walk is implausible in any cell). |
| `tours.py` | Home-based tours, leg endpoints and straight-line leg distances from the realised locations, the outside classification. Vectorised; a missing location raises. |
| `rule.py` | Purpose group and cell per leg, the implausibility test, the allowed modes per person, the seeded substitute draw per tour (plausible on every leg, portal gate class, counted relaxation) and its application to every leg of the tour. |
| `stage.py` | The synpp stage and its pure entry point `remode_outside_tours`, the report and the log line. |

`synthesis.population.trips.simulated` resolves to `braunschweig.synthesis.outside_tour_modes.stage` in
`configs/base_bs.yml`; the fixtures alias it to the same target as their `synthesis.population.trips.final`.

## The rule in four sentences

A home-based tour starts at every leg that departs from home and is an outside tour when any leg ends outside
the cordon polygon or touches an `outside` activity. Each leg of an outside tour is placed in a cell of the SrV
table by its purpose group and its straight-line distance between the realised locations, and its diary mode is
implausible when the share of that mode in the cell is below `min_share`. For every outside tour with an
implausible leg, one substitute is drawn, seeded, among the modes with a share of at least `min_share` in the
cell of every leg of the tour that the person may use and that keep the mode class of the tour's portal gate,
weighted by the shares of the cell of the tour's longest implausible leg. The substitute is applied to every
leg of the tour, so the tour keeps one mode (the plausible legs swept along are counted). When no mode
qualifies, plausibility is judged in the cell of the longest leg only (counted).

## Invariants a change must keep

- **The alias has exactly two consumers**: `braunschweig/matsim/scenario/population.py` and
  `braunschweig/synthesis/commute_day/output_day.py` (their `DAY_TRIPS_STAGE`). The synthesis output declares
  its trips stage through the `_STAGE_MAP` of `output_day.py`, because the default map of
  `ConfigureDayViewContext` would declare `trips.final` at configure time while `execute` reads the simulated
  alias, which synpp refuses.
- **The chain solver never reads the simulated alias.** It, the portal stage, `activities.final` and the
  analyses keep `synthesis.population.trips.final`; the stage needs the realised locations, which the chain
  solver produces, so the opposite read would be a cycle. `tests/test_outside_tour_modes_wiring.py` pins it.
- **Portal legs are judged in the group `all` at the top band.** A portal stay is a leg that touches an
  `outside` activity at a gate; its distance is not a trip length the table's bands describe, so the top band of
  `all` is the cell. A change to this is a decision and belongs in an ADR.
- **One mode per tour.** The substitute goes to every leg of the tour, which keeps vehicle continuity for the
  mode choice that follows.
- **A portal tour keeps the class of its gate.** The portal stage puts pt stays at rail exit stations and every
  other mode at road gates (ADR-0141), so a tour with a portal leg (column `portal_leg`, imported from
  `portal_trips/rewrite.py`) is restricted to pt when its portal legs are pt, and loses pt otherwise. The
  restriction is applied before the relaxation, never after it. A portal tour never aborts the run: with no
  admissible mode in its gate class, or with portal legs at both classes, it keeps its donor modes
  (`n_portal_tours_left_unchanged`, by reason, WARNING); only a non-portal tour raises on an empty set.
- **The relaxation is counted, never silent.** `n_draws_relaxed_to_longest_leg` and its share are in the report;
  `n_outside_legs_implausible_after` is the post-condition and warns when above zero.
- **The OFF path is byte-identical.** With the flag false `execute` returns the input frame, with no counter
  and no warning, and `configure` declares only the flag and `trips.final`.
- **Fallbacks are counted, never silent.** The stage raises on a person without an availability row, on a leg
  without a location, on a CRS mismatch, on a table cell without a plausible always-available mode, on an
  empty candidate set of a non-portal tour (also after the relaxation) and on a categorical `mode` column; a
  portal tour with no admissible mode is left unchanged and counted instead; the shares go to the log and the
  report.
- **The validate() token covers the data.** It hashes the helper modules (including the portal rewrite module
  and its `config_keys`, the home of the portal flag column) and, with the flag on, the bytes of the committed
  SrV table; a new helper module imported by the stage must be added to its module tuple (the
  closure gate of ADR-0136 fails otherwise).
- **Fixture config invariant.** Every config that aliases `synthesis.population.trips.final` aliases
  `synthesis.population.trips.simulated` to the same target and sets `braunschweig.outside_tour_modes.enabled:
  false` (the flag defaults to true and the stage requires the cordon). The aliases live in the top-level
  `aliases` section of a config file, the flags under `config`.

## Reading the log and the report

The line `[outside_tour_modes] outside tours re-moded N/M (x%; warn above y%) of T tours, P persons; legs
re-moded L, swept along S; substitutions {...}; cells {...}` is logged at WARNING when the share of re-moded
outside tours exceeds `warn_share`, at INFO otherwise. A second INFO line gives the outside tours by purpose
group, the portal legs judged in the group `all`, and the draws restricted by availability with the modes
removed. The same numbers are in `outside_tour_modes_report.json` in the stage's cache directory, under the keys
`n_outside_tours`, `n_outside_tours_remoded`, `share_outside_tours_remoded`, `substitution_matrix`
(`donor_modes->substitute`), `n_legs_remoded`, `n_legs_swept`, `n_portal_legs_as_all_group`,
`n_draws_restricted_by_availability`, `n_removed_by_availability_by_mode`, `cells_used`,
`n_outside_tours_by_group`, `n_outside_legs`, `n_draws_relaxed_to_longest_leg`,
`share_draws_relaxed_to_longest_leg`, `n_outside_legs_implausible_after`, `portal_leg_column_present`,
`n_portal_tours_remoded`, `n_portal_tours_class_restricted`, `n_portal_tours_left_unchanged`,
`portal_tours_left_unchanged_by_reason` (`mixed_gate_class`, `no_candidate_in_gate_class`),
`share_portal_tours_left_unchanged`, `n_unknown_donor_mode` and `unknown_donor_modes`. A WARNING is logged for a
positive `n_outside_legs_implausible_after`, for portal tours left unchanged, for donor modes unknown to the
table and for an absent `portal_leg` column with outside activities present. A high share of re-moded tours means the table and the population disagree
structurally and is a finding to investigate, not a calibration to accept.
