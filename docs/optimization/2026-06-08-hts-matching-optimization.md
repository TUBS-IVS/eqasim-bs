# HTS statistical-matching optimization (steps 1, 2, 4)

**Date:** 2026-06-08 · **Scope:** `synthesis/population/matched.py`,
`braunschweig/ipf/attributed.py`, `braunschweig/data/bbsr/regiostar.py`,
`braunschweig/analysis/population_validation/`.

The synthetic population inherits its daily activity chains from a **French HTS
donor** (ENTD) via priority-list statistical matching with graceful relaxing:
`statistical_matching` tries all keys and, when a cell has fewer than
`matching_minimum_observations` (default 20) donors, drops the lowest-priority
key and matches more coarsely. It can therefore never "fail" -- more keys = finer
matching where donors are dense, automatically coarser where thin.

This document records the implemented optimization steps. **Step 3 (replace the
ENTD donor with German MiD trips)** is the largest structural lever and is
deliberately deferred -- it dominates any key tweak but is a separate work item.

---

## Step 1 -- richer, EXOGENOUS matching keys (implemented, default ON in configs)

Legacy keys: `[sex, age_class, has_license]` (+ `studies` appended by
`reactivate_person_attributes`). Added the exogenous demographic anchors that are
genuinely populated on BOTH sides **at matching time** and drive mobility:

| Key | Target source (at `matched`) | HTS source | Behaviour signal |
|---|---|---|---|
| `employed` | real (GENESIS/IPF) | ENTD `SITUA` | commute vs. none |
| `household_size_class` | real (binned 1..5+) | ENTD `NPERS` | trip generation |
| `urban_class` | commune_id -> RegioStaR-2 | ENTD UU2010 | urban/rural trip length |

New config list (all `config_*braunschweig*.yml`), order = matching priority
(last key relaxed first):

```yaml
matching_attributes: ["sex", "age_class", "employed", "has_license", "household_size_class", "urban_class"]
```

**Why NOT the other wished-for attributes.** A key audit found the discriminator:
the matching target is `braunschweig.ipf.attributed` (= `data.census.filtered`),
which runs **upstream** of `braunschweig.synthesis.population.enriched`. At
matching time:

- `car_availability` / `number_of_cars` -> **placeholder** (`number_of_cars == 1`
  for everyone; real cars are the MiD-H7 draw in `enriched`).
- `has_pt_subscription` -> **placeholder** (`False`; real P24.1 draw in `enriched`).
- `economic_status` -> **does not exist yet** (status_from_hhtype is in `enriched`).

Matching on these would be matching on invented data (violates the no-silent-
fallback / no-invented-data rules), so they are excluded. `socioprofessional_class`
is also excluded: on the target it is a deterministic function of `(employed, age,
studies)` -- already keys -- so it only fragments cells without independent signal.

**Bringing car/pt/status forward (the deferred Option B) is high risk:** it breaks
the seeded `status_from_hhtype` reproducibility (full vs. sampled household set ->
different draws -> all outputs change), it is partly **circular** (car ownership is
itself modelled from economic_status, so keying the donor on it reinforces our own
car model rather than adding information), it forces a large `enriched` re-ordering
+ cache invalidation, and it needs another DE<->FR crosswalk
(`economic_status` <-> ENTD `income_class`). Decision: **measure with step 2 first**
(does a car-related modal gap remain after employed+hh_size+urban?), only then
consider the refactor -- data-driven, not on suspicion.

**Crosswalks (documented approximations, both sides' official definitions):**
- DE urban/rural = RegioStaR-2: Stadtregion (RS7 71-74) -> `urban`, Laendliche
  Region (75-77) -> `rural` (`regiostar2_label`).
- FR urban/rural = unite-urbaine UU2010: ville-centre / banlieue / isolated city
  -> `urban`, hors UU -> `rural` (`urban_class_from_urban_type`).
- The target column is attached by `attach_urban_class` (commune_id -> AGS-8 ->
  RS7 -> label); communes with no RS7 get the explicit `unknown` sentinel and the
  mapped/unmapped rate is logged (no silent fallback).

Gating: the **config list is the switch** -- a config that does not list a new key
is byte-identical (the derivation block and the regiostar dependency only fire
when the key is present).

Tests: `tests/test_matching_keys.py`.

---

## Step 2 -- trip-coherence check (implemented, closed-loop measurement)

`braunschweig/analysis/population_validation/trip_coherence.py` +
`run_population_validation` integration. Makes step 1 evaluable by comparing the
donor-derived activity chains against **real MiD 2023 targets**, segmented by the
same exogenous anchors (`employed` / `urban_class` / `household_size`):

- trip-purpose distribution vs **MiD W1** (`mid2023_W1.csv`), scored on the four
  unambiguous purposes (work->arbeit, education->ausbildung, shop->einkauf,
  leisure->freizeit), both sides re-normalised over those four (removes the
  home/dienst/erledigung/begleitung crosswalk ambiguity).
- mobility rate (share of persons with >= 1 trip) vs **MiD P36_1**
  (`mid2023_P36_1.csv`), overall + per segment.

**Modal split is intentionally out of scope here:** the synthesis `trips.csv`
carries no transport mode (it is written only by the MATSim mode-choice run), and
donor-inherited modes would be French-biased regardless -- that comparison belongs
to the MATSim-output validation (`run_mid_validation`, P12_1) and is precisely the
gap step 3 closes.

Outputs (when a `<prefix>trips.csv` is present): `trip_coherence_purpose.csv`,
`trip_coherence_mobility_by_segment.csv`, a `trip_coherence` block in
`report.json`, and a section in `summary.md`. A failure here is logged loudly but
does not abort the control validation.

Tests: `tests/test_trip_coherence.py`.

---

## Step 4 -- within-cell kNN similarity weighting (implemented, default OFF)

After the demographic keys define a (relaxed) cell, donors are drawn not just by
survey weight but additionally weighted by proximity to the target in a continuous
secondary attribute (default exact `age`):

```
P(donor | target) ~ survey_weight * exp(-|age_target - age_donor| / bandwidth)
```

`kernel_weighted_sample` (vectorised, target-chunked to bound memory). Refines the
choice WITHIN the cell only -- cell membership and the anti-overfitting relaxing
are unchanged. Bounded cost: cells with more than `matching_similarity_max_donors`
(default 2000) donors fall back to the legacy shared-CDF draw; the applied-vs-
fallback rate is logged (no silent fallback).

Config (default OFF -> legacy shared-CDF draw, byte-identical):

```yaml
matching_similarity: false
matching_similarity_attribute: age
matching_similarity_bandwidth: 5.0     # same unit as the attribute (years for age)
matching_similarity_max_donors: 2000
```

Tests: `tests/test_matching_keys.py` (`kernel_weighted_sample` + an end-to-end
similarity draw).

---

## Recommended sequence

1. Run the 25 % synthesis with step 1 ON, then `run_population_validation` (step 2)
   to measure the realised purpose/mobility coherence per segment.
2. If a residual car/mode-related gap remains, evaluate step 4 (similarity ON) and
   only then weigh the step-1 Option-B refactor against its risks above.
3. **Step 3 (MiD trip donor)** -- the dominant realism lever -- as a separate
   work item; the step-2 tool is the objective function for it.
