# General day-absence state (`braunschweig/synthesis/day_absence/`)

The two-stage SrV-anchored draw that gives EVERY synthetic person a reporting-day
absence state, how it composes with the pre-existing commute-day state
(ADR-0104) in the reporting-day trips, the resulting flag matrix of
`trips_day_stage`, where the drawn state is exported, and how the two
plan-structure/work-participation analyses read it. Why the model exists is
ADR-0110 (issue #370); its production state is the Feature Registry record
`general_day_absence`. The commute-day two-view trips architecture this
mechanism plugs into is documented separately in
[`commute-day-two-view-trips.md`](commute-day-two-view-trips.md) -- read that
note first if the pre-assignment/reporting-day split is unfamiliar.

## The two-stage draw (`absence.py`)

`day_absence_state` in `{present, absent_household, absent_individual}`,
drawn by `absence.draw_absence` for every person in
`synthesis.population.enriched` -- no employment or workplace precondition,
unlike the commute-day-state model, which only ever states a *worker*.

1. **Household stage.** Every household gets `size_class = min(size, 5)`
   (`srv_absence.household_size_class`); a single uniform draw against the
   committed `p_all_absent_by_size` (`srv2023_absence_household_by_size.csv`)
   marks the WHOLE household `absent_household`.
2. **Individual residual stage.** For every age band `a`
   (`srv_absence.AGE_BAND_LABELS`), the residual probability
   `p_individual(a) = max(0, (r(a) - r_hh(a)) / (1 - r_hh(a)))` is computed
   from the committed per-band rate `r(a)` (`srv2023_absence_by_age_band.csv`)
   and `r_hh(a)`, the rate the household stage ALREADY realised in that band
   on THIS draw. Every still-present person in the band is then drawn
   `absent_individual` at that probability.

The two stages together hit the committed per-band rates in expectation while
reproducing the household clustering (55.8 % of absent persons live in a
fully absent household, ADR-0110) that an independent per-person draw at the
same rates cannot: the residual formula subtracts out exactly what the
household stage already contributed to a band, so the two stages never
double-count. **Rule for touching this module:** the residual formula's
overshoot guard (`r_hh(a) > r(a)`, logged as a WARNING) must stay a direct
comparison, not merely `residual < 0` -- a band the household stage has
already fully absorbed (`r_hh(a) == 1.0`) hits the division-by-zero guard
before a negative residual would ever appear, and a residual-only check would
silently swallow that case.

One seeded RNG stream per run
(`numpy.random.RandomState(random_seed + DAY_ABSENCE_SEED_OFFSET)`,
`DAY_ABSENCE_SEED_OFFSET = 7351`), households sorted by `household_id` and
persons by `(household_id, person_id)` before either draw -- the result does
not depend on the input frame's row order.

## The stage (`absence_stage.py`)

Owns only the synpp plumbing: config keys (`day_absence_enabled`,
`day_absence_household_stage_enabled`, `day_absence_max_band_deviation_pp`),
loading the two committed reference tables, and the per-band deviation guard
(bands with `>= 1,000` persons whose realised share deviates from the
reference by more than `day_absence_max_band_deviation_pp` WARN). With the
flag off, `_disabled_frame` returns a schema- and dtype-identical frame
(every person `present`, `reason="disabled"`) without reading any reference
file -- the same "OFF path never fails where the ON path would" contract
`trips_day_stage` follows (see below): a missing age WARNS on the disabled
path instead of raising, because the flag being off must never turn a data
gap into a hard failure.

## Composition with the commute-day state (`plan_replacement.build_day_trips`)

The two absence concepts are drawn INDEPENDENTLY and composed only once, in
`plan_replacement.build_day_trips`'s `general_absence` keyword: a person is
trip-less in the reporting-day view when `commute_day_state == "absent"` OR
the general draw's `day_absence_state != "present"`. The union is counted
three ways in the returned `diagnostics` so a run can see the overlap, not
only the total:

| diagnostics key | meaning |
|---|---|
| `n_persons_absent_commute` | commute-day `absent` far commuters (same value as the pre-#370 `n_persons_absent`, kept for backward compatibility) |
| `n_persons_absent_general` | generally absent persons (any `day_absence_state != "present"`) |
| `n_persons_absent_both` | the overlap of the two |
| `n_persons_absent_total` | the union -- the actual number of persons with zero rows in the reporting-day trips |
| `n_trips_removed_general` | rows removed BECAUSE OF general absence specifically, excluding persons already counted under commute-absence, so the two removal reasons are never double-counted |

**Absence wins over a splice.** A `home` worker whose donor day was about to
be spliced in who is ALSO generally absent is excluded from the matched set
BEFORE the splice loop runs -- no donor block is ever built for them, and
they are counted once, under `n_persons_absent_general`, never under
`n_persons_replaced`. The identical exclusion applies to the *unmatched*-home
set: a home person without a donor who is also generally absent has their
rows removed like any other absent person, not kept unchanged, so they must
not inflate `n_home_unmatched`.

**No escort protection in the general draw.** ADR-0104 Assumption 4 (an
active escort leg evidences presence at home) is specific to the FAR-COMMUTER
`absent` state; it is not re-applied here. A family vacation takes an
escorted child along by construction of the household stage, and a solo
escorting adult being absent is a genuine, measured SrV pattern (ADR-0110
Context: partially absent households are dominated by lone 18-29-year-olds),
not a defect to guard against.

## The flag matrix (`trips_day_stage.py`)

Two INDEPENDENT flags -- `commute_day_state_enabled` and
`day_absence_enabled` -- gate what `trips_day_stage.execute` composes into
the reporting day. `configure()` declares all four upstream stages
(`synthesis.population.trips`, `state_stage`, `home_office_donors_stage`,
`braunschweig.synthesis.day_absence.absence_stage`) UNCONDITIONALLY; the
gating happens entirely in `execute()`. This is deliberate, not an oversight:
the test-harness stub context used across `tests/test_commute_day_stages.py`
does not resolve a config value the way real synpp's `ConfigureContext` does,
so a conditional `context.stage(...)` in `configure()` would never be
recorded as declared under that stub -- the same trade-off
`state_stage`/`home_office_donors_stage` already made for
`commute_day_state_enabled` before this feature existed.

| `commute_day_state_enabled` | `day_absence_enabled` | behaviour |
|---|---|---|
| `false` | `false` | returns the pre-assignment frame ITSELF (byte-identical by construction; pinned by `test_trips_day_stage_off_off_returns_the_identical_object`) |
| `true` | `false` | today's (pre-#370) commute-day behaviour, `general_absence=None` |
| `false` | `true` | `build_day_trips` runs with EMPTY commute-day placeholders (`empty_states()`, `empty_matches()`, `_empty_donor_trips()`, correct columns, no state/donor stage output touched) plus the real absence frame -- only the general-absence removal applies |
| `true` | `true` | both compositions apply together |

**Rule for touching this stage:** never gate a NEW dependency in
`configure()` on a config value here -- add it to the unconditional
declaration list and gate its USE in `execute()` instead, or the stub-context
tests silently stop exercising it.

`activities_day_stage` needs no absence-specific code at all: a person with
zero rows in the reporting-day trips already receives a full-day home
activity from the vendored `synthesis.population.activities`'s own
`df_missing` branch, the same mechanism that already handles a commute-day
`absent` person.

## Export columns and where the counts are logged

`day_absence_state` reaches two writers through the SAME optional-column /
optional-attribute mechanisms `commute_day_state` already uses, each gated by
its own independent `day_absence_enabled` check and its own
`attach_day_absence_state` helper (schema-identical sibling of
`attach_commute_day_state`, same fallback-transparency contract: coverage
rate logged, zero coverage raises rather than shipping a silently all-empty
column):

- `braunschweig.synthesis.commute_day.output_day` merges it into the
  enriched persons frame before the vendored `synthesis.output` writer
  selects columns (`synthesis.output.PERSON_OPTIONAL_OUTPUT_COLUMNS`) --
  written to `persons.csv` for EVERY person (unlike `commute_day_state`,
  which is empty for a person without an assigned workplace).
- `braunschweig.matsim.scenario.population` merges it into the resident
  persons frame before `matsim.scenario.population.add_person` writes it as
  the person attribute `dayAbsenceState` (`java.lang.String`), the same
  camel-case convention as `commuteDayState`.

Every guard and rate this mechanism owns is tagged `[day absence]`
(`absence.py`, `absence_stage.py`): the reference-table key-coverage check
(raises), the household-stage overshoot warning, and the per-band deviation
guard. Two further warnings live under OTHER modules' own tags because they
exist to make THIS feature's composition observable inside a consumer, not
because they belong to the draw itself: `work_participation_by_kreis` (tag
`[commute_day_state]`) warns when a non-empty absence draw overrides nobody
in the employed universe (`apply_general_absence`), and
`plan_structure_vs_srv` (tag `[plan_structure_vs_srv]`) warns when
`at_home_zero` is compared without the draw applied.

## The analysis view switch (`plan_structure_vs_srv.py`)

`plan_structure_trips_view` (`"final"` default / `"pre_assignment"`) picks
which trips stage the MODEL side of the SrV comparison is harmonised from.
Only with the `"final"` view AND `day_absence_enabled` both on is
`braunschweig.synthesis.day_absence.absence_stage` declared and read at all;
its away-from-home person ids reach
`braunschweig.analysis.plan_structure.harmonise_model`'s
`absent_person_ids` keyword, which sets `away_from_home` /
`reported_at_home` so the model side can be restricted to the SrV
`at_home_only` universe the same way `braunschweig.calibration
.srv_plan_structure.build_reference` restricts the reference side (the same
universe ADR-0109 introduced for the PopulationSim participation controls).
Reading `plan_structure_srv_universe == "at_home_zero"` WITHOUT the draw
applied (flag off, or the `"pre_assignment"` view) is a silent universe
mismatch -- the model then has no away-from-home concept at all while the
`at_home_zero` reference still counts such persons as zero-trip -- so the
stage logs a WARNING naming both sides rather than reporting a number that
looks comparable and is not.

**Rule for reading `plan_structure_vs_srv` output:** always check
`provenance.json`'s `trips_view` and `day_absence_enabled` fields before
comparing a run's numbers to another one's, or to the ADR-0104/ADR-0109
`pre_assignment`-view headline numbers quoted in earlier run manifests -- the
two views are not the same population and are not interchangeable without
saying so.

`work_participation_by_kreis`'s ADR-0104 check 1 applies the same drawn
absence ids the other direction: `apply_general_absence` overrides a
generally absent EMPLOYED person's `commute_day_state` to `"absent"` before
the per-Kreis shares are summed, folding them out of `share_no_workplace`
and into `share_absent`, and the count is reported as the appended
`n_absent_general` column of `commute_day_state_shares.csv` -- present (as
`0`) even when the absence draw is off, so the column schema never depends
on the flag.
