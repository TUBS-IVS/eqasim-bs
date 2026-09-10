# MiD purpose mapping: where the vocabulary is produced and who must read the same flags

The eqasim purpose of a MiD leg is decided in ONE function,
`braunschweig.popsim.trips.map_purpose`. Everything else in the model -- the plan,
the PopulationSim participation seeds, the secondary distance layers, the
home-office donor pool, the validation references -- must describe the SAME day,
which means every one of them has to be handed the SAME purpose flags. This note
is the threading list and the standing rules that keep it true. It is a
maintenance note, not a rationale: WHY each rule exists is in its ADR
(ADR-0091 explicit codes, ADR-0072/ADR-0073 escort, ADR-0111 code 10,
ADR-0112 passive escort, ADR-0113 W_ZWD sentinels, ADR-0115 the W_ZWECK-defined
`leisure_unspecified` subtype), and the production state of each is its Feature
Registry record.

## The defect class this exists to prevent

A control whose SEED counts different legs than the PLAN realises constrains a day
the model never builds (ADR-0109 named it for the participation universes; the
same shape produced the `trip_class` seed-vs-plan mismatch of #367). A distance
layer built from a different purpose vocabulary than the legs it is applied to has
the same shape, and so does a donor day spliced in under a different rule than the
day it replaces. None of these fail loudly: everything runs, and the numbers are
quietly wrong. So a purpose flag is never "added to the trip build"; it is added to
every consumer of the purpose vocabulary at once, or it is not added.

## Standing rules

1. **A purpose flag is declared with the `config_keys` constants in EVERY reader.**
   The key name and its default live exactly once, in
   `braunschweig/popsim/stage/config_keys.py` (`KEY_*` / `DEFAULT_*`, with a comment
   naming every stage that reads them); every `configure()` and every `execute()`
   imports those constants instead of retyping the string. Two stages that retype
   the same key agree today and drift on the first typo -- a typo resolves to a
   DIFFERENT config key with no error at all. Parity tests pin this per key by
   driving the real `configure()` functions against a recording context, e.g.
   `tests/test_popsim_trips.py::test_passive_pair_gap_default_agrees_across_its_three_homes`
   and
   `tests/test_secondary_chainsolvers_subtypes.py::test_purpose_subtype_codeplan_sentinels_default_agrees_across_its_two_homes`.
2. **A trip-build flag goes into `ENTD_REJECTED_KEYS`.** ENTD 2008 has no `W_ZWECK`
   vocabulary, so a MiD-only purpose flag left silently ignored would let a
   `popsim_open` config believe it ran a rule that never existed.
   `config_keys.ENTD_REJECTED_KEYS` maps each such key to its SAFE value;
   `EntdSource.build_trips` compares against that mapping (never a literal) and
   raises `ValueError` naming the key, and
   `tests/test_popsim_open_config.py` iterates the mapping against every discovered
   `popsim_open` fixture, so a new entry immediately fails until the fixtures set
   it. A flag the trip build does NOT read (e.g. `purpose_subtype_codeplan_sentinels`)
   stays out of the mapping deliberately.
3. **The keyword's CODE default stays `False` even when the declared default is
   `true`.** A direct caller or test that omits the keyword must keep the previous
   behaviour, so the OFF path is byte-identical by construction rather than by a
   golden. The production value lives in `configs/base_bs.yml` and nowhere else --
   never in an overlay (ADR-0070) and never in a fixture except to set the OFF
   value a rejection requires.
4. **A flag whose feature REQUIRES another flag keeps a `False` declared default.**
   `escort_passive_from_adult` requires `escort_purpose`, whose declared default is
   `False`; a `true` declared default would make the declared default SET internally
   inconsistent, and a config that sets nothing would abort inside
   `braunschweig.popsim.trips_stage` AFTER the full PopulationSim balancing. The
   dependency itself is enforced in `map_purpose`, which raises `ValueError` naming
   BOTH keys.
5. **If the flag changes a RULE inside a helper module, put that module in the
   consuming stage's cache token.** synpp hashes a stage's own module source and its
   resolved config values, not the modules it imports; `_HELPER_MODULES` /
   `_DEFERRED_HELPER_MODULE_NAMES` plus the stage's `validate()` are what make a
   rule change invalidate the cache (see `synpp-helper-hash-audit.md`).
6. **A committed MiD aggregate is measured on the universe a production run can
   realise, and says so in its header.** Two filters make that universe: the
   PopulationSim seed's reporting-day filter (only a weekday MiD diary can become a
   plan source -- `seed.MID_SEED_COLUMNS.day_filter_col` / `.day_filter_values`, the
   config key `braunschweig.population.popsim.seed_day_filter`) and, for anything
   derived from the LEGS, the trip build's own drops
   (`trips.legs_kept_by_the_trip_build`). Import both from their single home; never
   re-type the kernwo set. This is rule 6 because it was learned the expensive way:
   `mid2023_escort_w_zweck_split.csv` was first derived on ALL reporting days while
   the sibling fold table used weekday legs, so two committed tables disagreed about
   the same code-13 population (10,905 vs 6,781 legs) -- regenerated 2026-09-10,
   ruling C-R18. `tests/test_derive_escort_w_zweck_split.py::test_day_filter_values_are_the_seeds_own_constant`
   pins the shared constant.

## Threading list (as of ADR-0111 / ADR-0112 / ADR-0113)

Trip-build flags -- `escort_purpose`, `escort_passive_education`,
`explicit_round_trip_purposes`, `w_zweck_10_as_leisure`, `escort_passive_from_adult`,
`escort_passive_pair_max_gap_minutes` (plus the plan-structure keys
`exclude_rbw_legs`, `drop_leading_arrive_home_leg`, `closure_dwell_model`) -- reach:

| Module | Entry points that carry the flags |
|---|---|
| `braunschweig/popsim/trips.py` | `map_purpose` (the ONE rule), `expand_persons_to_trips`, `build_trip_table`, `build_validated_trip_table`; helpers `leisure_w_zweck_codes`, `passive_purpose_for_pairs`, `legs_kept_by_the_trip_build` |
| `braunschweig/popsim/escort_pairing.py` | `pair_passive_legs` (imported LAZILY inside `map_purpose`: this module imports `trips.mid_time_seconds`, so a module-level import would be a cycle) |
| `braunschweig/popsim/trips_stage.py` | `configure`, `execute`, `run`, `build_closure_dwell_model` |
| `braunschweig/popsim/sources/{base,mid,entd}.py` | `build_trips` (Protocol + both adapters; ENTD rejects, see rule 2) |
| `braunschweig/popsim/mid/participation.py` | `participation_w_zweck`, `compute_has_purpose_trip`, `derive_participation_seed`, `derive_education_flag_seed`, `_wege_without_non_education_passive_legs` |
| `braunschweig/popsim/mid/seed_loading.py` | `load_mid_seed` and `project_completed_seed` (both private participation steps) |
| `braunschweig/popsim/stage/__init__.py` | `configure`, `execute`, `_build_populationsim_seed` (both MiD branches) |
| `braunschweig/popsim/distance_distributions.py` | `configure`, `execute`, `run` |
| `braunschweig/synthesis/commute_day/{home_office_donors_stage,donor_pool}.py` | `configure`, `execute`, `build_home_office_donor_pool`, `donor_trips` |
| `braunschweig/analysis/population_validation/trip_coherence.py`, `run_population_validation.py`, `scripts/measure_trip_coherence.py` | the escort/W1 references, declared per report run rather than inferred |

`purpose_subtype_codeplan_sentinels` and `leisure_unspecified_subtype` are NOT
trip-build flags. Each reaches exactly the same two consumers, which must move
together or they disagree about what `leisure_activity` (respectively
`leisure_unspecified`) means:
`braunschweig/synthesis/locations/secondary_chainsolvers/`
(`__init__.configure`, `deciders._build_leisure_subtype_decider` /
`_build_other_subtype_decider` -- the ESTIMATION) and
`braunschweig/popsim/distance_distributions.py` (`run()` steps 8/8b/9 -- the subtype
DISTANCE-layer donor pools).

A subtype group is normally defined by the `W_ZWD` DETAIL code, but a
`purpose_subtype.SubtypeSpec` may also define a group by the RAW `W_ZWECK` code
(`SubtypeSpec.zweck_groups`, applied by the shared helper `purpose_subtype.label_legs`,
where a `W_ZWECK` group wins over the detail code); the only instance today is
`leisure_unspecified` (`W_ZWECK` 10, issue #373 / ADR-0115), whose legs carry no
leisure `W_ZWD` detail at all.

## Cache tokens that currently cover these rules

- `braunschweig.popsim.trips_stage._HELPER_MODULES` includes `escort_pairing`.
- `braunschweig.popsim.stage._DEFERRED_HELPER_MODULE_NAMES` includes `escort_pairing`
  (it decides which W_ZWECK-13 legs the `education_flag` seed counts -- the same
  stale-seed hazard the other second-level exceptions were admitted for).
- `braunschweig.synthesis.commute_day.home_office_donors_stage._HELPER_MODULES`
  includes `escort_pairing`.
- `braunschweig.synthesis.locations.secondary_chainsolvers._HELPER_MODULES` includes
  `braunschweig.popsim.purpose_subtype` (imported at that package's module level
  solely so it is a hashable module object).
- `braunschweig.popsim.distance_distributions` gained its own `validate()` in the
  purpose-correctness wave (issue #373): `_HELPER_MODULES` covers `trips`,
  `time_imputation`, `escort_pairing`, `constants` and the default upstream
  distance-distributions module, and `_DEFERRED_HELPER_MODULE_NAMES` covers `mid`,
  `mid.donor`, `purpose_subtype`, `shop_subtype` and `stage.config_keys`, so a rule
  change inside any of them now invalidates the cached distributions even when no
  declared config value moved. This closes the gap this note previously recorded as
  open; `synpp-helper-hash-audit.md` remains the shared register of the gaps that
  are still open elsewhere.

## Where the vocabulary's evidence lives

The committed tables that state what a code IS (never a control target, never edited
by hand -- regenerate through the script named in each header):
`eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv` (the W_ZWECK x
`hwzweck1` fold), `mid2023_escort_w_zweck_split.csv` (the active/passive split and
the passive purpose fold under pairing) and `mid2023_w_zwd_group_reference.csv` (the
subtype group mix under both sentinel settings). Data Registry: the first and the
third have a record of the same name; the escort split has none of its own and is
documented in the umbrella record `mid2023_reference_tables` (whose
`storage.expected_path` glob `mid/mid2023_*.csv` covers it).
