# Student in-commuters (#140 sub-item 2)

Cross-cordon university students who study at a ZGB (Zweckverband Grossraum
Braunschweig) institution but live outside the ZGB are injected as a second,
independent in-commuter population, structurally parallel to the existing SvB
(sozialversicherungspflichtige Beschaeftigte, i.e. employed) cross-cordon
commuters (`braunschweig.synthesis.incommuters`) but with a Home -> Education ->
Home day instead of Home -> Work -> Home. Implementation:
`braunschweig/synthesis/student_incommuters.py`.

## Count anchor (data-derived, not invented)

Per ZGB university commune, the injected in-commuter count is the real
enrollment not already filled by resident placement:

```
in_commuters_c = max(0, round(enrollment_c * sampling_rate) - residents_c)
```

`enrollment_c` is the summed `capacity` of that commune's local university
facilities, i.e. the real LSN SS2025 Hochschule enrollment already distributed
across OSM buildings by `braunschweig.data.schools.university_facilities`
(`eqasim-data/data/braunschweig/schools/nds_hochschulen.csv`, local-only, seeded
by `scripts/seed_nds_hochschulen.py`). `residents_c` is the count of resident
students the `education_gravity` model placed at a local facility in commune c
(`synthesis.population.spatial.primary.locations`). This is arithmetic on two
already-committed quantities (enrollment register + resident placement output),
not an invented reference value. A negative raw count (residents exceed scaled
enrollment) is floored to 0 and logged as a warning (implementation:
`braunschweig/data/education/student_incommuter_counts.py::compute_incommuter_counts`).

## Reverse-decay origin model (documented ASSUMPTION)

No committed current-residence student origin-destination dataset exists for
the ZGB region, so the residence of each non-resident student is drawn with a
documented assumption rather than a reference (`braunschweig/data/education/student_origins.py`):

- **A1 (age proxy):** the 18-29 age band is used as the student-age population
  proxy (DESTATIS 12411-0018 age-by-Kreis table, lower bounds 18/20/25 fall in
  range). No student-specific national age table is used.
- **A2 (same decay as residents):** the origin Kreis is drawn with the SAME
  calibrated distance-decay slope and radius as the resident university
  placement (`education_university_slope`, `education_university_max_radius_km`)
  -- just run in reverse: the destination (university commune centroid,
  capacity-weighted across that commune's local facilities) is fixed, and
  candidate origin Kreise (all German Kreise outside the ZGB cordon, one
  dissolved centroid each) are drawn `~ kreis_pop_18_29 * exp(slope * d_km)` via
  the shared `assign_by_decay` helper, with a nearest-Kreis fallback beyond the
  radius (logged).
- **A3 (workers' mode-split reused):** the car/PT mode split is drawn from the
  same Mikrozensus distance-band commute-mode reference used for the SvB
  (employed) in-commuters (`braunschweig.data.mikrozensus.reference.load_commute_mode_by_distance`,
  restricted to `car`/`pt`) -- there is no separate student mode-share
  reference.
- **A4 (enrollment-minus-residents is the true unmet demand):** the count
  anchor's implicit assumption is that all enrollment not captured by resident
  placement corresponds to a real commuting student, i.e. no drop-out /
  distance-learning discount is applied.

These are documented assumptions per CLAUDE.md's no-invented-reference-values
rule, not validated targets. No committed data source can currently confirm or
refute A1-A4.

## Dependency coupling (tri-state, default-ON)

The stage needs the resident university placement (to compute the count
anchor), which only exists when `education_gravity_enabled=True`. The
activation flag is a **tri-state** (`_active()` in
`braunschweig/synthesis/student_incommuters.py`):

| `cordon_enabled` | `education_gravity_enabled` | `cordon_student_incommuters_enabled` | Result |
|---|---|---|---|
| `false` | any | any | OFF (no-op, byte-identical) |
| `true` | `false` | unset (default `None`) | OFF, one `warn` log ("skipped: requires education_gravity_enabled") |
| `true` | `false` | `true` (explicit) | `RuntimeError` (contradictory config) |
| `true` | `false` | `false` (explicit) | OFF (explicit opt-out, no warning) |
| `true` | `true` | unset or `true` | **ON** (default-ON per project convention once the dependency is met) |
| `true` | `true` | `false` (explicit) | OFF (explicit opt-out) |

So the feature is default-ON in the project-convention sense (unset resolves to
ON whenever both `cordon_enabled` and `education_gravity_enabled` are true) but
never silently activates without its data dependency, and never silently
disables itself when explicitly forced on against a contradictory parent
(raises instead, per CLAUDE.md fallback-transparency).

## Config keys

Set in `braunschweig/synthesis/student_incommuters.py::configure`:

- `cordon_student_incommuters_enabled` (default `None`, tri-state; see table above)
- `education_gravity_enabled` (default `False`, the hard dependency; owned by the education-gravity feature)
- `student_incommuter_age_band` (default `[18, 29]`, the DESTATIS age-class bounds for A1)
- `education_university_slope` (default `-0.1415`, reused from `education_gravity`)
- `education_university_max_radius_km` (default `150.0`, reused from `education_gravity`)
- plus the shared `sampling_rate`, `random_seed`, `cordon_network_source_buffer_m`, `data_path`

No new config keys are introduced beyond `cordon_student_incommuters_enabled`
and `student_incommuter_age_band` -- everything else reuses the
`education_gravity` / cordon config already present.

## Config wiring

The stage is invoked via `context.stage("braunschweig.synthesis.student_incommuters")`
directly from the four scenario writers, gated on `cordon_enabled`, mirroring
the existing SvB `incommuters` wiring exactly:
`braunschweig/matsim/scenario/population.py`, `households.py`, `vehicles.py`,
`facilities.py`. Because `context.stage()` self-registers the dependency with
`synpp`, no separate pipeline-stage-list entry is required.

Six run configs currently have both `cordon_enabled: true` and
`education_gravity_enabled: true` set (i.e. the dependency is already met, so
the tri-state flag already resolves to ON by default):
`config_popsim_mid_braunschweig.yml`,
`config_popsim_mid_braunschweig_population_allfeatures.yml`,
`config_popsim_open_braunschweig.yml`,
`config_server_braunschweig_1pct_allfeat_popsim.yml`,
`config_server_braunschweig_25pct_allfeat_popsim.yml`,
`config_server_braunschweig_100pct_allfeat_popsim.yml`. Each was given an
explicit `cordon_student_incommuters_enabled: true` line next to `cordon_enabled`
for visibility (project "feature parity" convention) -- this does **not**
change effective behaviour, since unset already resolved to `true` there. The
non-allfeat server configs (`config_server_braunschweig_{1,25,100}pct.yml`) and
all smoke/mini/dry-run configs leave `education_gravity_enabled` at its default
`False` (or explicit `false`), so the feature stays OFF there, as intended --
neither flag was flipped to wire this feature in.

## Injected frames and mode/timing

Home->Education->Home persons, households, trips, activities, locations, and
vehicles are built by `_inject()` and merged into the same frames as the SvB
in-commuters (see the module docstrings of the four `braunschweig/matsim/scenario/*.py`
writers). Persons/households are deliberately simpler than the SvB builders: no
origin-Kreis INKAR income tilt (flat `INCOMMUTER_BASE_INCOME_EUR` for every
student) and no per-agent German-fleet vehicle draw (students reuse the legacy
`default_car` / `default_car_passenger` vehicle types). Person/household ids are
offset by a fixed `10,000,000` block above the resident range
(`_ID_OFFSET_ABOVE_RESIDENTS`) to avoid colliding with the SvB in-commuter id
block without a hard stage dependency on it; `assert_unique_ids` in the scenario
writers is a loud safety net that verifies the two in-commuter blocks never
actually overlap.

### Timing: distance-consistent home-departure seed

The HTS donor is the **German MiD 2023 survey** (nationwide pool), supplied via
the stage `braunschweig.data.hts.mid_donor` which reuses popsim's MiD loaders.
This is a deliberate scientific change from the previous ENTD-based timing and
makes the in-commuter path non-byte-identical vs earlier runs (though no unit
tests broke, as they stub the HTS stage; observable only in real runs).
Distance, mode, count, and origin remain derived from other German sources
(Mikrozensus, LSN, DESTATIS) and are unaffected.

The donor's education-leg times (`_donor_education_times`, memoised per unique
donor id) supply the arrival at education (`arrive_mid`), the education departure
and the home arrival. The home **departure** is NOT taken raw from the donor;
it is re-seeded from the agent's synthetic home->campus straight-line distance at
the configured gate speed (`cordon_gate_speed_kmh`, default 30 km/h) with the
routed-detour factor, exactly mirroring the SvB stage's `_agent_times`:

    depart_home = max(0, arrive_mid - dist_km * ROUTED_DETOUR_FACTOR / gate_speed_kmh * 3600)

This keeps the seed schedule speed-consistent for every agent regardless of the
donor's own (unrelated) trip length -- important because a far agent's home is a
nearest cordon gate, so a raw donor time would otherwise imply an absurd
door-to-door speed. MATSim re-times the simulated leg over the iterations; this
only fixes the initial plan. Verified by
`tests/test_student_incommuters_stage.py::test_injection_seeds_distance_consistent_departure`
(implied outbound speed is the constant `gate_speed_kmh / detour` for all agents).

## OD / distance analysis outputs

`braunschweig/analysis/simwrapper/student_commuters.py` (mirrors the SvB
`commuters.py` analysis) aggregates the injected agents -- pure aggregation of
model output, not compared against any external reference:

- `student_commuter_od.csv` -- `[from_ars5, to_commune, value]`, one row per
  observed origin-Kreis -> destination-university-commune flow.
- `student_commuter_top_relations.csv` -- the same OD table's top `N` (default
  50) relations, renamed `[from, to, value]` for the SimWrapper flow-map card.
- `student_commute_distance.csv` -- straight-line home->campus distance banded
  into `[0,5,10,20,50,100,inf]` km buckets, with `count` per band and the
  overall `mean_km`.

All three files are written only when at least one student in-commuter was
injected (`write_outputs` no-ops on an empty frame).

## Data provenance

- `eqasim-data/data/braunschweig/schools/nds_hochschulen.csv` (local-only, not
  committed): real LSN (Landesamt fuer Statistik Niedersachsen) SS2025
  enrollment per Hochschule, seeded by `scripts/seed_nds_hochschulen.py`.
- DESTATIS table 12411-0018 (age x sex population by Kreis): the 18-29
  population weight used for the reverse-decay draw
  (`braunschweig.data.census.population.load_age_sex_by_kreis`).
- `braunschweig.data.mikrozensus.reference.load_commute_mode_by_distance`: the
  shared commute mode-by-distance-band reference (reused unchanged from the SvB
  in-commuter stage).

## Tests

`tests/test_student_incommuter_counts.py`, `tests/test_student_origins.py`,
`tests/test_student_incommuters_stage.py`, `tests/test_student_incommuter_merge.py`,
`tests/test_student_commuter_analysis.py`, plus the shared purpose-agnostic
helper tests `tests/test_incommuter_plans_purpose.py` and the existing SvB
in-commuter suite (`tests/test_incommuters.py`, `tests/test_incommuter_merge.py`)
to confirm no regression.

## Status

Built and unit/integration-tested locally (feature-flagged, default-ON once its
`education_gravity` dependency is on; see the tri-state table above). **Not**
yet run end-to-end on a data-complete environment (the LSN/OSM/DESTATIS inputs
above require the server data tree) -- a 1% cordon dry-run with
`education_gravity_enabled=True` confirming the per-commune count log, injected
`education` activities, the in-ring/gate fallback rate, and the written
`student_commuter_od.csv` is the remaining verification step (see
`PROJECT_STATUS.md`). No validation against a real observed student-commute
dataset has been performed or claimed -- see "Reverse-decay origin model"
above.
