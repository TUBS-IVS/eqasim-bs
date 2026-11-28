# Student in-commuters (#140 sub-item 2)

Cross-cordon university students who study at a ZGB (Zweckverband Grossraum
Braunschweig) institution but live outside the ZGB are injected as a second,
independent in-commuter population, structurally parallel to the existing SvB
(sozialversicherungspflichtige Beschaeftigte, i.e. employed) cross-cordon
commuters (`braunschweig.synthesis.incommuters`) but with a Home -> Education ->
Home day instead of Home -> Work -> Home. Implementation:
`braunschweig/synthesis/student_incommuters.py`.

Two paths, chosen by `braunschweig.students.flows_enabled` (default on). The ON path (issue #140,
Phase 4, ADR-0213) takes count, origin, commute length and mode from the origin-destination table T of
ADR-0210 and is described in "Student flows (ON path)" below. The sections "OFF path: count anchor" and "OFF path: reverse-decay
origin model" describe the OFF path (the behaviour before the flows); the tri-state activation table applies to both.
The decisions and the rejected options are in ADR-0213; this file holds the method only, and no figure
that a manifest or a table owns.

## Student flows (ON path)

All quantities rest on ONE table of head counts, T(k, j): the students of site j who live in Kreis k
(`student_od_table.csv`, ADR-0210). The stage takes its in-commuters from the rows of T whose origin lies
outside the region.

- **Count.** Per origin Kreis and site: the heads of T times the effective day factor times the sampling
  rate, rounded with an unbiased controlled rounding that keeps the total of every site and of every origin
  Kreis within one person (`student_incommuter_counts.balance_incommuter_counts`). T counts students who
  live there; the effective day factor turns them into persons of one day. It is the mean of the residents'
  factors `f(k) = e x (1 - a(area(k)))`, weighted by the students of each Kreis in the level table, with `e`
  (the share of students with an education leg) and `a` (the away share by home area) read from the
  committed SrV tables, never typed into the configuration. The factor uses the away share the run applies
  to the residents: by home area with the day-absence cells on (ADR-0212), zero with the day absence off;
  the flows are refused with the day absence on and its cells off. The in-commuters are not drawn by the
  day-absence step; their count already carries the factor.
- **Site and building.** Each agent carries the destination of T it belongs to and a building of that site
  drawn in proportion to capacity (the facilities per destination are built by
  `braunschweig.data.schools.university_facilities`, ADR-0213 Decision 2).
- **Home.** The existing rule: a population-weighted point of a commune of the origin Kreis inside the ring
  around the region, else the nearest cordon gate. The branch of each agent is recorded in `home_branch`,
  and the stage logs both branches per origin Kreis.
- **Commute length.** The length T implies, not the one of the home point (a gate lies at the edge of the
  region and understates a far origin): the agent draws a commune of its origin Kreis by population times
  the weight curve T was built with (`student_geometry.curve_basis`); the straight line from that commune to
  the site point of `student_destination_points.csv`, times `braunschweig.constants.ROUTED_DETOUR_FACTOR`,
  is the commute length for the donor window and the mode band. Where the commune layer is missing or no commune of the
  Kreis has a positive weight, the distance of the cell of T is used and counted as the fallback
  (`student_incommuter_counts.commute_length_km`).
- **Plan and times.** Unchanged: the draw among adult student donors within a window around the agent's
  commute length (`donor_min_effective_pool`), the timing and the timetable check below.
- **Mode.** Car or public transport from the students' mode table by distance band of the routed length
  (`mid2023_student_education_distance_mode.csv`, loader and band function in
  `braunschweig/data/education/student_mode_reference.py`) through the same `mode_reference_from_table` and
  `assign_fixed_mode` path as before. The table has its own band edges. A band with fewer students than
  `braunschweig.student_incommuters.mode_band_min_persons` (ASSUMPTION) is pooled with its lower neighbour
  (the lowest band with its upper one), and the pooled bands are logged.

The residents' side of the same balance (routing by status, the fitted flow matrix on the persons present,
the allotment inside a home Kreis) lives in `braunschweig/synthesis/locations/` and is described in ADR-0213
and the module docstrings of `education_routing.py` and `university_assignment.py`. The acceptance table of
the balance and the diagnostics are written by the analysis stage
`braunschweig.analysis.synthesis.student_flows`; the out-commuting students are measured by
`braunschweig.analysis.synthesis.student_out_commuters`.

## OFF path: count anchor (data-derived, not invented)

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

## OFF path: reverse-decay origin model (documented ASSUMPTION)

The text of this section is the method of the OFF path of `braunschweig.students.flows_enabled`. The ON
path does not use the reverse decay: its origins are the origins of T (ADR-0213).

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
- **A3 (workers' mode-split reused, OFF path; the ON path uses the students' table):** the car/PT mode split is drawn from the
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

The keys of the student flows are one block in `configs/base_bs.yml`; defaults and ASSUMPTION comments are
there and are not repeated here.

Set in `braunschweig/synthesis/student_incommuters.py::configure` (OFF path and shared):

- `cordon_student_incommuters_enabled` (default `None`, tri-state; see table above)
- `education_gravity_enabled` (default `False`, the hard dependency; owned by the education-gravity feature)
- `student_incommuter_age_band` (default `[18, 29]`, the DESTATIS age-class bounds for A1)
- `education_university_slope` (default `-0.1415`, reused from `education_gravity`)
- `education_university_max_radius_km` (default `150.0`, reused from `education_gravity`)
- plus the shared `sampling_rate`, `random_seed`, `cordon_network_source_buffer_m`, `data_path`

The OFF path introduces no config keys beyond `cordon_student_incommuters_enabled` and
`student_incommuter_age_band` -- everything else reuses the `education_gravity` / cordon config already
present.

## Config wiring

The stage is invoked via `context.stage("braunschweig.synthesis.student_incommuters")`
directly from the four scenario writers, gated on `cordon_enabled`, mirroring
the existing SvB `incommuters` wiring exactly:
`braunschweig/matsim/scenario/population.py`, `households.py`, `vehicles.py`,
`facilities.py`. Because `context.stage()` self-registers the dependency with
`synpp`, no separate pipeline-stage-list entry is required.

Both `cordon_enabled: true` and `education_gravity_enabled: true` are set (i.e.
the dependency is already met, so the tri-state flag already resolves to ON by
default) in the composed all-features base `configs/base_bs.yml` (applying to
every scale overlay) and in the two standalone popsim fixtures
`configs/fixtures/config_popsim_mid_braunschweig.yml` and
`configs/fixtures/config_popsim_open_braunschweig.yml`. (Prior to the
config-composition cleanup, #230, this was set individually in six run
configs -- `config_popsim_mid_braunschweig.yml`,
`config_popsim_mid_braunschweig_population_allfeatures.yml`,
`config_popsim_open_braunschweig.yml`, and the three
`config_server_braunschweig_{1pct,25pct,100pct}_allfeat_popsim.yml` --
of which the `population_allfeatures` and the three server variants are now
superseded/removed in favour of the single composed base.) In each, the flag
is documented as a **comment** next to `cordon_enabled` (not an explicit
`cordon_student_incommuters_enabled: true`): leaving it unset already resolves to
ON there, and a comment avoids the footgun where an explicit `true` would turn a
later `education_gravity_enabled: false` edit into a hard `RuntimeError` instead
of a graceful skip. The non-allfeat fixtures
(`configs/fixtures/config_local_braunschweig*.yml`,
`configs/fixtures/config_dryrun_braunschweig.yml`) and all smoke/mini configs
leave `education_gravity_enabled` at its default `False` (or explicit `false`),
so the feature stays OFF there, as intended -- neither flag was flipped to wire
this feature in.

## Injected frames and mode/timing

Home->Education->Home persons, households, trips, activities, locations, and
vehicles are built by `_inject()` and merged into the same frames as the SvB
in-commuters (see the module docstrings of the four `braunschweig/matsim/scenario/*.py`
writers). Persons/households carry the household of the student's MiD donor, drawn as
ADR-0191 decision 7 says; unlike the SvB builders there is no per-agent German-fleet vehicle draw (students reuse the legacy
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

### Timetable check of the pt days (ADR-0173, #528)

A pt student's two trips are checked against the timetable with the chain of the
commuter in-commuters (ADR-0153; `incommuter_pt_connections.check_pt_days`, switch
`braunschweig.incommuters.pt_connection_check.enabled`): a day without a connection
moves as a whole (earlier first) within the shift window, else the student drives from
the home it already has. Students board at no drawn entry station, so the station
re-draw of ADR-0172 does not apply. Counted, logged and reported as `pt_connection`;
the measurement that motivated it is `docs/runs/student-incommuter-pt-walks-2026-10-30.yml`.

Consequences to watch in the next run (neither is measured yet):

- The realised student pt share falls below the Mikrozensus draw by the `to_car`
  count; the student stage has no mode balancer that pulls it back (the commuter
  stage re-balances, ADR-0172).
- A far student's home is the nearest road gate, not a station. If no stop is
  reachable from a gate, every pt student there goes `to_car` for the geometry,
  not the timetable. In the three measured populations no pt student had a plan
  of walks only (`walk_only` 0), so every pt student's home reached a stop on at
  least one trip; a high `to_car` share in the WARNING line is the signal to look
  at the gates.

## OD / distance analysis outputs

On the ON path the acceptance table, the realised flows and the distance bands are written by the analysis
stage `student_flows`; the three files below are the simulation-independent OD view of the injected agents
and are written on both paths.

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

ON path: the committed tables of the registry records `student_od_balance`, `student_destination_points`,
`student_level_table`, `srv2023_student_reference` and `mid2023_student_reference`. OFF path:

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
`tests/test_student_commuter_analysis.py`, and for the ON path
`tests/test_student_incommuters_balance.py`, `tests/test_student_mode_reference.py`,
`tests/test_student_flows_inputs.py`, `tests/test_student_flows_switches.py`, plus the shared purpose-agnostic
helper tests `tests/test_incommuter_plans_purpose.py` and the existing SvB
in-commuter suite (`tests/test_incommuters.py`, `tests/test_incommuter_merge.py`)
to confirm no regression.

## Status

Run evidence and validation state are not kept here: the feature records
(`docs/registry/features/student_incommuters.yml`, `docs/registry/features/student_flows.yml`), the run
manifests under `docs/runs/` and the open work in the GitHub issues own them. No validation against an
observed student-commute dataset is claimed for either path; T rests on the CHE origins and the owner's
commuter weights (ADR-0210).
