# Departure-time model (`braunschweig/popsim/departure_time_model.py`)

How a synthetic person's day gets its START time: the reporting-precision rule
the de-rounding is built on, the rank-preserving quantile mapping onto the
committed SrV 2023 first-departure distribution, the coarsening ladder for thin
cells, the offset column every model writes, the TWO places the model runs, and
the analysis stage that measures what it did. WHY the model exists (and what was
rejected) is ADR-0114 (issue #123); its production state, flags and evidence are
the Feature Registry record `departure_time_model`. The reporting-day
(`.final`) view the second call site belongs to is described in
[`commute-day-two-view-trips.md`](commute-day-two-view-trips.md) -- read that
note first if the pre-assignment/reporting-day split is unfamiliar.

## The reporting-precision rule (`braunschweig/calibration/reported_time_precision.py`)

Both surveys this model touches report clock times on a grid: a respondent who
left "at half past seven" writes `7:30`. `half_width_minutes` reads the
precision OF A REPORT from its minute value and returns the half width of the
cell the true time is assumed to lie in:

| reported minute of hour | assumed cell | half width |
|---|---|---|
| a multiple of 15 (`:00`, `:15`, `:30`, `:45`) | quarter hour | `QUARTER_HOUR_HALF_WIDTH_MINUTES` = 7.5 min |
| a multiple of 5 but not of 15 | five minutes | `FIVE_MINUTE_HALF_WIDTH_MINUTES` = 2.5 min |
| anything else | to the minute | 0 |

`deround_minutes_of_day` draws `U(-h, +h)` ONCE per element with that half
width. It is deliberately intolerant: a NaN, a negative or a non-integer-valued
minute RAISES naming the offending count as `n/total` rather than silently
falling back to half width 0 -- a de-rounding that silently did nothing would be
indistinguishable from a de-rounding that worked (CLAUDE.md fallback
transparency).

ASSUMPTION (ADR-0114, spec section 6): the precision of a report is read from
its minute value alone. A person who genuinely left at exactly 7:00 cannot be
told apart from one who rounded, so their reported time is widened too. The rule
is applied to BOTH sides of every comparison -- the committed SrV reference is
de-rounded by this same function when it is built
(`scripts/extract_srv_departure_times.py`), which is what makes the model side
and the reference side comparable at all.

## The offset column

`OFFSET_COLUMN = "departure_time_offset_seconds"` is declared in
`departure_time_model` and re-exported by `braunschweig.popsim.trips_stage`
(import direction: `trips_stage` imports the model module at module level, the
model imports `apply_per_person_jitter` lazily inside the dispatch, so there is
no cycle). EVERY model writes it -- including `eqasim_uniform`, where
`apply_per_person_jitter` records the jitter it already applied -- as the
whole-second offset that was added to `departure_time` AND `arrival_time` of
every row of that person. So

```
departure_time - departure_time_offset_seconds == the pre-model (repaired) departure
```

holds exactly for whole-second inputs, which is what lets
`braunschweig.analysis.departure_time` decompose a realised time without
re-deriving anything from the RNG stream. Two consequences worth knowing:

* `braunschweig.synthesis.commute_day.plan_replacement` treats it as a
  RECOMPUTED column (`_RECOMPUTED_COLUMNS`), not as a generic extra column: a
  replaced row's other extra columns are nulled and counted, this one is
  overwritten a few lines later by the model itself, so counting it would report
  a null nobody can observe.
* `synthesis.output` selects its columns explicitly, so the column stays on the
  cached trips frame and never reaches `trips.csv`.

## The three models

Selected per run by `departure_time_model` (`MODELS` in the model module); the
dispatch is `apply_departure_time_model(table, persons, *, model, random_seed,
reference, min_reference_n, min_model_n, max_median_shift_hours,
ranking_context)`.

* **`eqasim_uniform`** -- the inherited eqasim jitter: one uniform offset per
  person in `+/- min(1800 s, first departure)`, delegated verbatim to
  `apply_per_person_jitter`. This is the CODE default and the byte-identical OFF
  path.
* **`derounded`** -- one offset per person drawn inside the precision cell of
  that person's FIRST departure (rule above), floored to whole minutes for the
  half-width lookup; needs no reference table.
* **`srv_mapped`** -- `derounded`, then the mapping below. The production value
  in `configs/base_bs.yml`.

In all three the WHOLE chain moves by ONE offset, and that offset is rounded to
whole seconds BEFORE it is applied (this deviates from `apply_per_person_jitter`,
which rounds each shifted time separately -- on purpose: mapped targets land on
half seconds, and rounding row by row would move the two rows of a chain by
21601 s instead of 21600 s, i.e. it would change a trip duration). Trip
durations and activity durations are the model's hold-out dimension and must
stay bitwise unchanged.

Clipping, both counted and logged: the offset is bounded below so the person's
EARLIEST departure stays `>= 0` and above so the last arrival stays
`<= braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS`. When both bounds
conflict (a chain too long to fit even starting at 0) non-negativity wins and
the conflict is counted separately -- it is a pre-existing chain-length problem,
not one this model introduced. A negative time after the model RAISES; it is not
an `assert` (`python -O` strips those).

## The mapping (`quantile_map_first_departures`)

Cell = `(harmonised purpose of the person's FIRST trip, harmonised group of the
person)`; the group rule is `srv_plan_structure.harmonised_group`, the same one
the committed reference is segmented by, and the purpose is the leg's
DESTINATION purpose, again the same side the reference uses.

Per rung the model has a RANKING BASE -- the model population, or a supplied
ranking context, of that rung's key (see
[The ranking base](#the-ranking-base-whose-distribution-is-a-person-ranked-in)
below, which owns that rule). Every target's quantile is its MID-RANK in that
base,

```
q = (#{base values below the target} + 0.5 * #{ties} + 0.5) / (N_base + 1)
```

taken through the inverse CDF of the rung's cumulated 15-minute
`share_derounded` column, LINEAR inside the 15-minute bin so a cell does not
collapse onto a 15-minute comb. A person who is themselves in the base -- every
person mapped at rung 1, where the base IS their own cell -- ties with their own
value and so lands at `i / (N + 1)`. Two targets between the same two base
values share a quantile, which is the base's granularity of `1 / (N + 1)`. No
sort over the targets is needed or performed: `q` is a monotone function of the
target's own value in a base the whole rung shares, so the reported order is
preserved and the result cannot depend on any row or dict iteration order.

The mapping offset is `target - de-rounded first departure`; the total offset is
`de-rounding + mapping`. The de-rounding therefore cancels out of the total for
a mapped person by construction -- its job is to place the person INSIDE the
reported quarter hour before ranking, which is what breaks the ties between the
many persons reporting the same clock time. The de-rounding draw
(`RandomState(random_seed + DEPARTURE_TIME_SEED_OFFSET)`,
`DEPARTURE_TIME_SEED_OFFSET = 7361`, verified unused elsewhere) is the ONLY
random component of `srv_mapped`; everything after it is deterministic.

**Only the FIRST departure is mapped.** Later departures, trip durations and
activity durations follow the donor diary untouched -- they are the hold-out
dimension of the validation, and nothing in this module may start mapping them
without a new decision record.

## The ranking base: whose distribution is a person ranked in?

A quantile map needs a distribution to rank a person IN, and there is ONE rule
for it (rulings A-R18 / A-R20, ADR-0114 Consequences):

> **A rung's ranking base is the model population of that rung's own key** --
> the person's `(purpose, group)` cell at rung 1, every person of that purpose at
> rung 2 (the groups that already mapped at rung 1 included), everyone at rung 3.

The TARGETS at a rung are still the still-pending persons; only the base is keyed
on the rung. Two properties follow. The base can only GROW as the ladder
coarsens, which is the least a coarsening ladder should do; and a person's
placement depends on their own value and the rung's base alone, never on which
OTHER cells happened to be thin in this run.

Where that population comes from is the one difference between the two call
sites:

* the **trip build** passes no `ranking_context`, so the MAPPED SET is its own
  base. Correct, because its set IS the whole synthetic population.
* the **reporting-day splice** passes one, because its set is only the replaced
  home-office persons split by `(first purpose x group)`: at a smoke or a 1 %
  scale those cells would coarsen to `all_all` or stay unmapped although the
  population has thousands of persons in the same cell -- and a rank among a
  handful of persons is not a meaningful quantile anyway.

`apply_departure_time_model(..., ranking_context=<frame>)` takes that base with
`RANKING_CONTEXT_COLUMNS` -- `person_id`, `raw_first_departure_seconds`,
`purpose`, `group`, exactly one row per POPULATION person -- built by
`build_ranking_context(trips, persons)` from the PRE-ASSIGNMENT trips view and
the population's own attributes:

* the value is the RAW time, `departure_time - departure_time_offset_seconds`
  of the person's first trip, i.e. the reported grid time before any model ran.
  Ranking a donor's reported time against ALREADY mapped times would compare two
  different scales.
* it is de-rounded by the same reporting-precision rule as the targets, from the
  model's own stream (targets first, context second, context rows sorted by
  `person_id`), so a context never shifts the targets' own draws and a shuffled
  context frame produces identical output. ASSUMPTION: this is an independent
  realisation of the same rule, not a replay of the trip build's draw for those
  persons, and since A-R20 it is the ONLY thing that still differs between the
  two call sites (ADR-0114 Assumption 10b carries the measured size: a median of
  44 s, up to hours in the reference's sparse tail).
* each person's quantile is their mid-rank in the rung's base,
  `(#below + 0.5 * #ties + 0.5) / (N + 1)` -- rank-preserving (a monotone
  function of the person's own value) and strictly inside `(0, 1)`. Two
  placements agree EXACTLY when the bases agree, whether or not the person is
  themselves in the base.
* a SUPPLIED context is never itself mapped, and duplicate `person_id` rows are
  rejected (they would double-count a person and draw two de-roundings).
* `min_model_n` sizes the base, clamped to at least 1 -- an empty base would
  otherwise hand every person the quantile 0.5 and collapse the whole cell onto
  the reference's median. `min_reference_n` is untouched, and there is
  deliberately no second threshold.

A context handed to `derounded` or `eqasim_uniform` RAISES: those models rank
nothing, and a caller believing a base is in effect when it is not is the hidden
defect the fallback-transparency rule exists to stop.

## The coarsening ladder

`(purpose, group)` -> `(purpose, "all")` -> `("all", "all")` -> `unmapped`
(`LEVEL_LABELS`). A rung is taken when BOTH sides are thick enough:

* the REFERENCE cell of that rung exists, is non-empty (`n_unweighted > 0` with
  finite shares -- an empty committed cell means "no reference", never a zero
  distribution) and has `n_unweighted >= min_reference_n`; AND
* the rung's RANKING BASE holds at least `max(min_model_n, 1)` persons (see
  "The ranking base" above -- the base is keyed on the rung, not on whichever
  cells happened to be pending).

Pooling is what makes the ladder more than a reference lookup: rung 2 pools
every group of that purpose that could not take rung 1 and ranks them JOINTLY,
rung 3 pools everyone still unplaced. A person for whom no rung is usable stays
`unmapped` and keeps ONLY their de-rounding offset -- their start time was not
calibrated, which is exactly why the level split is logged rather than
summarised as a single success count.

Per cell the report carries `level`, `n_model` (the cell's own persons),
`n_model_pooled` (the persons MAPPED together at the rung it mapped at; for an
unmapped cell, the size of the LAST pooled attempt, so a reader sees how far it
was from the threshold), `n_context` / `n_context_pooled` (the cell's own base
and the base it was actually ranked AMONG at that rung -- population persons with
a supplied context, model persons without one), `n_reference`,
`median_shift_min` and `median_abs_shift_min`. The run-level
`n_ranking_context` (`None` when the model set was its own base) and the shared
renderer `format_ranking_base` say where the base came from; the per-rung sizes
are the per-cell `n_context_pooled`, which is why the run-level line points at
them instead of naming one number.

Guards, all under the log tag `[departure time]` and all rendered as
`n/total (rate)` by the single helper `format_level_split`:

| guard | threshold | behaviour |
|---|---|---|
| unmapped share | `UNMAPPED_SHARE_WARN_THRESHOLD` = 0.05 | WARN naming what those persons kept |
| share mapped below `purpose_group` | `COARSENED_SHARE_WARN` = 0.25 | WARN with the full level split -- an observability ASSUMPTION, not a scientific bound |
| median absolute shift of a cell | `departure_time_mapping_max_median_shift_hours` (2.0 h) | WARN naming the cell, both medians, `n_model`, `n_reference` and the level; offsets are NOT clipped |
| persons without a first departure | any | counted, warned, offset 0, counted as unmapped |
| lower / upper clip, clip conflict | see above | counted, conflict warned |
| unknown MiD `P_TAET` in `persons_from_mid_schema` (tested utility only, not a production call site -- see "Where it runs" below) | `UNKNOWN_TAET_WARN_THRESHOLD` = 0.10 | INFO below / WARN above; treated as not employed (documented ASSUMPTION, no imputation) |

## Where it runs: exactly two call sites

1. **`braunschweig.popsim.trips_stage.run`** -- the PRE-ASSIGNMENT trips view,
   for the whole population. The persons frame is the popsim-assembled
   SYNTHETIC persons frame (`synthesis.population.sampled` -- `age` from
   `braunschweig.popsim.expand.map_demographics`, `employed` IMPUTED by
   `braunschweig.popsim.assembly.map_mid_person_attributes` ->
   `braunschweig.popsim.attributes.map_employed`), adapted by
   `persons_from_synthetic_schema`, built ONLY for `srv_mapped` (`derounded`
   uses no group at all); the adaptation happens at the TOP of `run()`, so a
   schema gap fails in seconds instead of after the trip build. Times do not
   feed the location assignment, so mapping them here does not disturb the
   two-view architecture.
2. **`braunschweig.synthesis.commute_day.plan_replacement.build_day_trips(...,
   departure_time=DepartureTimeSettings(...))`**, called by
   `trips_day_stage` -- the spliced home-office chains of the reporting-day
   view, which the donor pool deliberately leaves un-jittered so a donor day is
   never shifted twice. The cell is keyed by the RECEIVING person's group
   (`persons_from_synthetic_schema` on the `synthesis.population.enriched`
   frame the stage already depends on) and the DONOR chain's first purpose, and
   the persons handed to the model are RESTRICTED to the replaced set -- those,
   and only those, are the persons whose day this call SHIFTS. What they are
   RANKED IN is the settings' `ranking_context`, which `trips_day_stage` builds
   with `build_ranking_context` from the pre-assignment trips and the same
   enriched persons -- lazily, only when the match count says a day will actually
   be spliced, while the reference is resolved unconditionally so a misconfigured
   run still aborts (rulings A-R18 / A-R20; see "The ranking base" above).
   `departure_time=None` keeps the plain jitter byte-identically.

**ONE attribute source, at every call site (ruling A-R17, final fix wave item
1).** Both call sites above, and the comparison stage below, now feed
`person_groups` the SAME harmonised input: `persons_from_synthetic_schema` on
the population's own `age` / IMPUTED `employed`. Before the final fix wave,
`trips_stage.run` instead used `persons_from_mid_schema` (raw MiD `P_TAET`, NO
imputation), so a person whose `P_TAET` was unknown could be grouped as
NOT-employed at the trip build while the SAME person's imputed `employed` (used
by the plan replacement and the comparison stage) resolved differently -- the
same person calibrated in one group and measured in another.
`persons_from_mid_schema` is kept as a TESTED UTILITY (fixture construction and
the direct unit tests of the dispatch), not a production call site any more.

`min_model_n` governs both call sites, and since rulings A-R18 / A-R20 it sizes
the same thing at both: the RANKING BASE of a rung, which is the population
either way (the mapped set in the trip build, the ranking context at the
splice). Each call site logs its realised level split and where its base came
from in one shared vocabulary (`format_ranking_base`), and every cell reports
`n_context` / `n_context_pooled` -- read the level TOGETHER with the base size
before trusting a `srv_mapped` run.

The reference is loaded ONCE per stage by
`departure_time_model.load_reference_for_model(data_path, model, config_key=...)`
-- `None` for `eqasim_uniform` / `derounded`, the committed table (filtered to
`position == "first"`, the only position the model may map from) for
`srv_mapped`. A missing file RAISES naming both the expected path and the config
key; there is no fallback to an uncalibrated run. The helper lives in the model
module, not in either stage, so both stages fail with the same message.

## Configuration

Four flat keys, declared by both stages from the single constant pair in
`braunschweig.popsim.stage.config_keys` so the two stages cannot disagree about
a name or a default. Only `departure_time_model` itself is MiD-only (rejected
by `EntdSource`, see below); the three numeric keys are MODEL PARAMETERS that
only size the `srv_mapped` coarsening ladder -- a mapping the model-key
rejection already forbids on the ENTD path -- so they are accepted and ignored
there rather than rejected (final fix wave item 3).

| key | code default | production value (`configs/base_bs.yml`) |
|---|---|---|
| `departure_time_model` | `eqasim_uniform` | `srv_mapped` |
| `departure_time_mapping_min_reference_n` | 200 | 200 |
| `departure_time_mapping_min_model_n` (sizes a rung's RANKING BASE) | 50 | 50 |
| `departure_time_mapping_max_median_shift_hours` | 2.0 | 2.0 |

`EntdSource.build_trips` REJECTS a non-default `departure_time_model`
(`config_keys.ENTD_REJECTED_KEYS`) because the ENTD path has no SrV cell
structure; the three numeric keys are model parameters and are accepted and
ignored there. Both `popsim_open` fixtures set `departure_time_model:
eqasim_uniform` explicitly.

## The analysis stage, and why `dep_hour_share_*` is biased

`braunschweig.analysis.synthesis.departure_time_vs_srv` (pure metrics in
`braunschweig/analysis/departure_time.py`) is where the model is MEASURED: the
three-way decomposition raw / pre-offset / realised, the 15-minute comparison
against both SrV columns with `emd_on_bands`, and the activity-duration
hold-out. Its stage record documents the outputs; do not re-derive its numbers
anywhere else.

The older `plan_structure_vs_srv` rows `dep_hour_share_*` compare model
departures against the SrV report AT HOUR LEVEL, with the model side de-rounded
and the SrV side AS REPORTED. That comparison is biased BY CONSTRUCTION and will
look worse for hour 6 the better the model gets: within hour 7 the raw SrV
reports cluster on `7:00`, so ANY symmetric de-rounding of the model side moves
half of that mass into hour 6 while the un-de-rounded reference keeps all of it
in hour 7. Those rows are kept as they are (they are the historical
as-reported comparison); the unbiased one -- both sides de-rounded, 15-minute
bins -- lives in `analysis/departure_time_vs_srv/`, and
`plan_structure_vs_srv`'s summary carries one sentence pointing there.

## Rules for touching this code

* **Cache tokens.** `braunschweig.popsim.trips_stage` and
  `braunschweig.synthesis.commute_day.trips_day_stage` fold the model module and
  its cross-package helpers (`reported_time_precision`, `srv_departure_times`,
  `srv_plan_structure`, plus `attributes` / `plan_validation` respectively) into
  their `_HELPER_MODULES` hash, because synpp hashes only the stage file itself
  and `validate()` hashes non-transitively. An edit to any of them therefore
  devalidates `synthesis.population.trips` and everything downstream -- but NOT
  PopulationSim. `tests/test_synpp_helper_hash_invariant.py` polices only
  OWN-package siblings, so the cross-package entries are the ones a future edit
  can silently lose: add, never remove, and say why in the comment next to them
  (see [`synpp-helper-hash-audit.md`](synpp-helper-hash-audit.md)).
* **One tolerance, one validator.** `REFERENCE_SUM_TOLERANCE` and
  `validate_departure_time_table` / `validate_activity_duration_table` live in
  `braunschweig.calibration.srv_departure_times`, next to the builder that
  normalises the shares; the model module and the analysis module import them
  (`SHARE_SUM_TOLERANCE` there is an alias). Two copies could drift until one
  loader accepted a table the other rejected --
  `tests/test_departure_time_vs_srv.py::test_the_tolerance_constant_has_exactly_one_home`
  pins the identity.
* **The OFF path is a contract, not a convention.** `eqasim_uniform` must stay
  byte-identical to `apply_per_person_jitter` on every pre-existing column;
  `tests/test_popsim_trips_stage.py::test_jitter_output_matches_the_pre_task_1_golden_values`
  pins the pre-#123 values, and three further tests pin the byte identity at the
  dispatch, the stage and the plan replacement (listed in the feature record).
