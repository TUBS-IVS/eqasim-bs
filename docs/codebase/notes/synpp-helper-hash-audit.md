# synpp stage helper-hash coverage audit

> **Snapshot as of 2026-08-14, commit `4175515a`.** This is a point-in-time
> inventory, not a live view: every stage count, category, and uncovered-module
> list below was derived once, by the method in this file, against that
> commit. Re-run the method (see "Method") to refresh it; treat any number
> here as stale the moment the codebase changes. Produced for issue #290; it
> deliberately fixes nothing (see "Scope").

## Scope

synpp's `get_stage_hash` hashes only a stage module's **own** source. Code a
stage calls that lives in another first-party module is therefore outside its
cache key: edit that helper, and a partial rerun silently reuses output
produced by the old code (see `docs/codebase/notes/gravity-model-split.md`
and `popsim-stage-split.md` for the two known, already-fixed cases). This
audit answers, for **every** synpp stage in the repo, not just those two:

1. How many stages exist, and how they were counted.
2. Which first-party modules each stage's own code imports directly — module
   level AND lazy (function-body) imports.
3. Whether an optional `validate(context)` hook closes that gap, and if so
   whether its coverage is complete.
4. Which stages use another synpp stage module as a plain function library
   without declaring it via `context.stage(...)` (undeclared cache edge).

This is an **inventory**, not a fix: no behaviour, cache token, or
`_HELPER_MODULES` tuple was changed while producing it.

## Method

A pure static-analysis pass over the repository's Python AST; nothing was
imported or executed except the final cross-check in "Verification" below,
which imports five already-existing modules to read their own tuples (no
`configure`/`execute` was ever called).

**Step 1 — enumerate stages.** Walked every `.py` file under the five
first-party roots — `braunschweig/`, `data/`, `eqasim_common/`, `matsim/`,
`synthesis/` (verified these are the only top-level first-party packages by
listing the repo root; the `documentation/` directory at repo root is a
separate, unrelated set of paper-generation scripts, not a package under
audit — see "Limitations"). A module is a **stage** if `ast.parse` finds
`FunctionDef` nodes named `configure` **and** `execute` directly in the
module's top-level body (`ast.Module.body`, not nested in a class or another
function). A package counts via its `__init__.py`, addressed by the package's
dotted name. Verified there is no factory-assignment stage pattern
(`configure = ...` / `execute = ...` at module level) anywhere in the five
roots that this `def`-only check would miss (`grep` for the pattern found
none) — see "Limitations" for what a purely static check still cannot rule
out.

**Step 2 — collect each stage's own imports.** For every stage module, walked
the **whole** AST (any nesting depth, not just the top level) collecting every
`Import`/`ImportFrom` node, and classified each as **module-level** (executes
at import time: top-level statements, module-level `if`/`try` branches, class
bodies) or **lazy** (nested inside a `FunctionDef`/`AsyncFunctionDef` body at
any depth — this is exactly how three of the four uncovered gravity
dependencies and two of the `popsim.stage` undeclared-library imports are
reached, so a module-level-only scan would have missed them). `if
TYPE_CHECKING:`-guarded imports are excluded (they never execute at runtime);
six non-stage files use that guard, none of the 230 stage modules do, so the
exclusion is inert on this codebase today but is checked, not assumed. For
`from X import Y`, both readings — `Y` as an attribute of `X`, and `X.Y` as a
submodule — were tried against the on-disk module set, and whichever exists
was kept (this is exactly the `from braunschweig.gravity import
attraction_vector, balancing, ...` shape: `attraction_vector` is a submodule,
not an attribute). Relative imports (`from . import x`) were resolved with
the same `bits = package.rsplit('.', level - 1)` rule `importlib` itself uses.
Only first-party roots were kept (third-party is out of scope, pinned by the
environment per CLAUDE.md). No import failed to resolve to an on-disk module
across all 230 stages (reported explicitly, not assumed — see the counts
below).

**Step 3 — separate "already covered by synpp" from "helper".** A first-party
import that is itself a stage module (has `configure`+`execute`) is not
automatically a coverage gap: if the importing stage's own `configure()`
declares it via a literal `context.stage("name")` call, synpp's own DAG
derives that stage's hash and propagates it through the declared edge — no
extra token needed for that one. Declared names were resolved through the
`aliases:` block of `configs/base_bs.yml` (22 entries; overlays carry no
aliases, confirmed by reading `configs/overlays/test_100pct.yml`) before
comparing, since a declared name is often an upstream alias, not the real
module path (e.g. `context.stage("data.census.filtered")` really means
`braunschweig.popsim.stage`). Every `context.stage(...)` call found across all
230 stages had a literal string first argument — none were dynamic/computed,
so no stage needed an `unknown` mark for this step. A stage's **required
helper set** = (all first-party imports) minus (first-party stage imports
that are declared dependencies). This set is what a `validate()` hook would
need to cover completely for category (b).

**Step 4 — read `validate()` coverage.** 78 of the 230 stages define a
module-level `validate()`, but `grep -rl "inspect.getsource"` across the five
roots shows only **five** of those actually hash Python source at all —
`braunschweig/gravity/model.py`, `braunschweig/popsim/stage/__init__.py`,
`braunschweig/synthesis/locations/secondary_candidates.py`,
`braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py`, and
`braunschweig/synthesis/population/enriched/__init__.py`. The other 73 use
`validate()` for the standard, unrelated synpp idiom of hashing **input data
file** existence/size (e.g. `data/census/raw.py`: `return
os.path.getsize(census_path)`) — a legitimate, separate mechanism for
detecting changed raw inputs, but it covers zero Python source and therefore
zero of the required-helper gap. For the five source-hashing stages, their
`_HELPER_MODULES` (and, for `popsim.stage`, `_DEFERRED_HELPER_MODULE_NAMES`)
tuples were read directly from source and, to remove any hand-transcription
risk, resolved by actually importing each of the five modules in this
environment and reading `{m.__name__ for m in mod._HELPER_MODULES}` back —
module import only, no stage execution.

## Stage count

**230 stage modules** found across the five roots (`braunschweig` 84, `data`
57, `eqasim_common` 45, `synthesis` 29, `matsim` 15 — both stand-alone modules
and package `__init__.py`s counted by their package's dotted name). 564 `.py`
files were scanned in total across the five roots; 230 of them define both
`configure` and `execute` at module level.

## Category (a) — no first-party helpers (143 stages)

Cache-correct by construction: every first-party module these stages import
is either absent entirely, or is itself a synpp stage the `configure()`
already declares (so synpp's own DAG hash covers it — e.g.
`braunschweig.freight.trips` imports `braunschweig.freight.extraction` at
module level, but also declares `context.stage("braunschweig.freight.extraction")`,
so this is not a gap). Grouped by root; no further detail is owed to these
since by definition there is nothing left uncovered:

- **braunschweig** (39): `braunschweig.analysis.verbindungen_validation`, `braunschweig.data.alkis`, `braunschweig.data.bbsr.regiostar`, `braunschweig.data.bosserhof_purpose`, `braunschweig.data.building_potentials`, `braunschweig.data.buildings`, `braunschweig.data.census.employees`, `braunschweig.data.census.employment`, `braunschweig.data.census.employment_gemband`, `braunschweig.data.census.household_size`, `braunschweig.data.census.households_size_age`, `braunschweig.data.census.licenses`, `braunschweig.data.census.pendler`, `braunschweig.data.census.population`, `braunschweig.data.education.student_share`, `braunschweig.data.freight.german_wide`, `braunschweig.data.inkar.household_income`, `braunschweig.data.inspire.landuse`, `braunschweig.data.landuse`, `braunschweig.data.locations`, `braunschweig.data.mid.references`, `braunschweig.data.mid.zones`, `braunschweig.data.osm`, `braunschweig.data.spatial.taz`, `braunschweig.data.verbindungen.margins`, `braunschweig.data.verbindungen.work_od`, `braunschweig.data.vrb.zones`, `braunschweig.data.zensus_grid.population`, `braunschweig.freight.trips`, `braunschweig.gravity.distance_matrix_taz`, `braunschweig.locations.home`, `braunschweig.locations.secondary`, `braunschweig.locations.work`, `braunschweig.popsim.commute_distance`, `braunschweig.popsim.enriched_adapter`, `braunschweig.synthesis.income`, `braunschweig.synthesis.population.regiostar`, `braunschweig.synthesis.spatial.commute_distance`, `braunschweig.synthesis.spatial.home_zones`
- **data** (43): `data.ban.raw`, `data.bdtopo.output`, `data.bdtopo.raw`, `data.bpe.raw`, `data.census.filtered`, `data.census.projection`, `data.census.raw`, `data.external.education`, `data.gtfs.output`, `data.hts.commute_distance`, `data.hts.comparison`, `data.hts.edgt_44.reweighted`, `data.hts.edgt_lyon.raw_adisp`, `data.hts.edgt_lyon.raw_cerema`, `data.hts.edgt_lyon.reweighted`, `data.hts.egt.raw`, `data.hts.entd.raw`, `data.hts.entd.reweighted`, `data.hts.output`, `data.hts.selected`, `data.income.municipality`, `data.income.region`, `data.od.cleaned`, `data.od.raw`, `data.od.weighted`, `data.osm.osmosis`, `data.sirene.cleaned`, `data.sirene.localized`, `data.sirene.output`, `data.sirene.raw_geoloc`, `data.sirene.raw_siren`, `data.sirene.raw_siret`, `data.spatial.centroid_distances`, `data.spatial.code_changes`, `data.spatial.codes`, `data.spatial.departments`, `data.spatial.iris`, `data.spatial.municipalities`, `data.spatial.population`, `data.spatial.urban_type`, `data.tiles.raw`, `data.vehicles.raw`, `data.vehicles.types`
- **eqasim_common** (28): `eqasim_common.analysis.debug.sc`, `eqasim_common.analysis.grid.comparison_flow_volume`, `eqasim_common.analysis.reference.hts.activities`, `eqasim_common.analysis.reference.hts.mode_distances`, `eqasim_common.analysis.reference.income`, `eqasim_common.analysis.reference.od.commute_distance`, `eqasim_common.analysis.reference.od.commute_flow`, `eqasim_common.data.buildings`, `eqasim_common.data.census.employees`, `eqasim_common.data.census.employment`, `eqasim_common.data.census.household_income`, `eqasim_common.data.census.household_size`, `eqasim_common.data.census.licenses`, `eqasim_common.data.census.population`, `eqasim_common.data.mid.data`, `eqasim_common.data.mid.zones`, `eqasim_common.data.mvg.zones`, `eqasim_common.data.osm.chunked`, `eqasim_common.data.osm.locations`, `eqasim_common.data.osm.osmconvert`, `eqasim_common.data.population.municipalities`, `eqasim_common.data.population.raw`, `eqasim_common.data.spatial.iris`, `eqasim_common.gravity.distance_matrix`, `eqasim_common.locations.education`, `eqasim_common.locations.synthesis.education`, `eqasim_common.spatial.codes`, `eqasim_common.spatial.entd_codes`
- **matsim** (11): `matsim.output`, `matsim.runtime.eqasim`, `matsim.runtime.git`, `matsim.runtime.java`, `matsim.runtime.maven`, `matsim.runtime.pt2matsim`, `matsim.scenario.supply.gtfs`, `matsim.scenario.supply.osm`, `matsim.scenario.supply.processed`, `matsim.simulation.prepare` *(vendored `matsim.simulation.prepare`; the BS override `braunschweig.matsim.simulation.prepare` is a separate stage, listed under category (c)/(d) below)*, `matsim.simulation.run`
- **synthesis** (22): `synthesis.locations.education`, `synthesis.locations.home.addresses`, `synthesis.locations.home.locations`, `synthesis.locations.home.output`, `synthesis.locations.secondary`, `synthesis.locations.work`, `synthesis.output`, `synthesis.population.activities`, `synthesis.population.income.selected`, `synthesis.population.projection.ipu`, `synthesis.population.projection.reweighted`, `synthesis.population.sampled`, `synthesis.population.spatial.commute_distance`, `synthesis.population.spatial.home.zones`, `synthesis.population.spatial.locations`, `synthesis.population.spatial.primary.locations`, `synthesis.population.spatial.secondary.distance_distributions`, `synthesis.population.trips`, `synthesis.vehicles.cars.default`, `synthesis.vehicles.cars.fleet_sampling`, `synthesis.vehicles.passengers.default`, `synthesis.vehicles.vehicles`

## Category (b) — has helpers, fully covered (1 stage)

| Stage | Path | Required helpers | Covered | Uncovered |
|---|---|---|---|---|
| `braunschweig.popsim.stage` | `braunschweig/popsim/stage/__init__.py` | 44 | 44 | none |

The only stage in the repo whose `validate()` closes its entire helper
surface — see `docs/codebase/notes/popsim-stage-split.md` for the canonical
description. It is worth stating explicitly: this stage **also** exhibits the
category-(d) pattern (it imports two other synpp stages,
`braunschweig.data.census.household_size` and
`braunschweig.synthesis.population.enriched`, as plain libraries without
declaring them via `context.stage(...)`), but because its `_DEFERRED_HELPER_MODULE_NAMES`
/ `_HELPER_MODULES` tuples explicitly include both, that gap is closed too.
**Tie-break rule applied:** the issue text only resolves the (c)+(d) overlap
("list it under (c) and mark it"); by the same logic, a stage that would
otherwise qualify for both (b) and (d) is listed under (b) (coverage is what
determines cache correctness) and flagged as also exhibiting the (d) pattern,
rather than invented as a fifth category.

## Category (c) — has helpers, no or incomplete coverage (86 stages)

37 of these are reachable in the resolved production config (see
"Prioritisation"); those are listed first, sorted by name. The remaining 49
follow, also sorted by name. "d" marks a stage that also qualifies for
category (d) (an uncovered module that is itself an undeclared stage
dependency — see that column's entry in the next table for exactly which
one). All 86 came from the 87 stages with a non-empty required-helper set,
minus `braunschweig.popsim.stage` (fully covered, category (b) above).

### Production-reachable (37)

| Stage | Path | Uncovered modules | d |
|---|---|---|---|
| `braunschweig.analysis.analysis_suite` | `braunschweig/analysis/analysis_suite.py` | `braunschweig.analysis.dashboard.build_dashboard`, `braunschweig.analysis.popsim_validation.run_popsim_control_validation`, `braunschweig.analysis.population_validation.controls`, `braunschweig.analysis.population_validation.population_source`, `braunschweig.analysis.population_validation.run_population_validation`, `braunschweig.analysis.run_education_validation`, `braunschweig.analysis.run_household_composition`, `braunschweig.analysis.run_integerizer_quality`, `braunschweig.analysis.run_mid_validation` | |
| `braunschweig.analysis.cordon_validation` | `braunschweig/analysis/cordon_validation.py` | `braunschweig.data.cordon.validation_output` | |
| `braunschweig.analysis.simwrapper_export` | `braunschweig/analysis/simwrapper_export.py` | `braunschweig.analysis.simwrapper.export` | |
| `braunschweig.data.bosserhof_location_category` | `braunschweig/data/bosserhof_location_category.py` | `braunschweig.data.bosserhof_purpose` | d |
| `braunschweig.data.cordon_gemeinden` | `braunschweig/data/cordon_gemeinden.py` | `braunschweig.data.cordon.network` | |
| `braunschweig.data.cordon_network` | `braunschweig/data/cordon_network.py` | `braunschweig.data.cordon.network` | |
| `braunschweig.data.cordon_pt_gates` | `braunschweig/data/cordon_pt_gates.py` | `braunschweig.data.cordon.network`, `braunschweig.data.cordon.pt_reachability` | |
| `braunschweig.data.external_secondary_points` | `braunschweig/data/external_secondary_points.py` | `braunschweig.data.external_workplaces` | d |
| `braunschweig.data.external_workplaces` | `braunschweig/data/external_workplaces.py` | `braunschweig.data.cordon.external_points` | |
| `braunschweig.data.hts.mid_donor` | `braunschweig/data/hts/mid_donor.py` | `braunschweig.popsim.attributes`, `braunschweig.popsim.sources.mid`, `braunschweig.popsim.stage`, `braunschweig.popsim.trips` | d |
| `braunschweig.data.schools.facilities` | `braunschweig/data/schools/facilities.py` | `braunschweig.data.schools.typing` | |
| `braunschweig.data.schools.kita_facilities` | `braunschweig/data/schools/kita_facilities.py` | `braunschweig.data.building_potential_attach` | |
| `braunschweig.data.schools.university_facilities` | `braunschweig/data/schools/university_facilities.py` | `braunschweig.data.building_potential_attach` | |
| `braunschweig.data.verbindungen.zones` | `braunschweig/data/verbindungen/zones.py` | `braunschweig.data.bbsr.regiostar` | d |
| `braunschweig.freight.extraction` | `braunschweig/freight/extraction.py` | `braunschweig.data.spatial.cordon` | |
| `braunschweig.gravity.model` | `braunschweig/gravity/model.py` | `braunschweig.gravity.friction`, `braunschweig.gravity.production_mass`, `braunschweig.gravity.taz_margins`, `braunschweig.gravity.verbindungen_anchor` | |
| `braunschweig.locations.synthesis.replacement_education_gravity` | `braunschweig/locations/synthesis/replacement_education_gravity.py` | `synthesis.population.spatial.primary.locations` | d |
| `braunschweig.matsim.scenario.facilities` | `braunschweig/matsim/scenario/facilities.py` | `matsim.scenario.facilities` | d |
| `braunschweig.matsim.scenario.households` | `braunschweig/matsim/scenario/households.py` | `braunschweig.synthesis.incommuter_merge._base`, `matsim.scenario.households` | d |
| `braunschweig.matsim.scenario.population` | `braunschweig/matsim/scenario/population.py` | `braunschweig.synthesis.incommuter_merge._base`, `matsim.scenario.population` | d |
| `braunschweig.matsim.scenario.vehicles` | `braunschweig/matsim/scenario/vehicles.py` | `braunschweig.synthesis.incommuter_merge._base`, `matsim.scenario.vehicles` | d |
| `braunschweig.matsim.simulation.prepare` | `braunschweig/matsim/simulation/prepare.py` | `braunschweig.data.cordon.extent`, `braunschweig.data.spatial.cordon`, `matsim.runtime.eqasim`, `matsim.simulation.prepare` | d |
| `braunschweig.popsim.completed_donor` | `braunschweig/popsim/completed_donor.py` | `braunschweig.popsim.mid`, `braunschweig.popsim.seed`, `braunschweig.popsim.stage`, `braunschweig.popsim.weekend_plan_match` | d |
| `braunschweig.popsim.distance_distributions` | `braunschweig/popsim/distance_distributions.py` | `braunschweig.constants`, `braunschweig.popsim.mid`, `braunschweig.popsim.purpose_subtype`, `braunschweig.popsim.shop_subtype`, `braunschweig.popsim.time_imputation`, `braunschweig.popsim.trips`, `synthesis.population.spatial.secondary.distance_distributions` | d |
| `braunschweig.popsim.trips_stage` | `braunschweig/popsim/trips_stage.py` | `braunschweig.constants`, `braunschweig.popsim.plan_validation`, `braunschweig.popsim.sources`, `braunschweig.popsim.trips` | |
| `braunschweig.synthesis.cordon_gates` | `braunschweig/synthesis/cordon_gates.py` | `braunschweig.data.cordon.gate_assignment`, `braunschweig.data.cordon.gates`, `braunschweig.data.spatial.cordon` | |
| `braunschweig.synthesis.incommuters` | `braunschweig/synthesis/incommuters.py` | `braunschweig.constants`, `braunschweig.data.cordon.demand`, `braunschweig.data.cordon.gate_assignment`, `braunschweig.data.cordon.incommuter_origins`, `braunschweig.data.cordon.mode_balancer`, `braunschweig.data.cordon.mode_reference`, `braunschweig.data.cordon.network`, `braunschweig.data.cordon.plans`, `braunschweig.data.cordon.pt_reachability`, `braunschweig.data.external_workplaces`, `braunschweig.data.mikrozensus.reference`, `braunschweig.synthesis.vehicles.fleet_sampling_de` | d |
| `braunschweig.synthesis.locations.education_gravity` | `braunschweig/synthesis/locations/education_gravity.py` | `braunschweig.data.schools.bbs_share`, `braunschweig.synthesis.locations.education_gravity_model` | |
| `braunschweig.synthesis.locations.home_cell` | `braunschweig/synthesis/locations/home_cell.py` | `braunschweig.popsim.cells`, `braunschweig.popsim.prepared_cells`, `braunschweig.synthesis.locations.building_typing`, `braunschweig.synthesis.locations.cell_building_signals`, `braunschweig.synthesis.locations.home_matcher` | |
| `braunschweig.synthesis.locations.secondary_candidates` | `braunschweig/synthesis/locations/secondary_candidates.py` | `braunschweig.synthesis.locations.landuse_candidates`, `braunschweig.synthesis.locations.secondary_chainsolvers` | d |
| `braunschweig.synthesis.locations.secondary_chainsolvers` | `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py` | `braunschweig.calibration.secondary_measurement`, `braunschweig.parallelism`, `braunschweig.synthesis.locations.escort_links`, `synthesis.population.spatial.secondary.problems` | |
| `braunschweig.synthesis.student_incommuters` | `braunschweig/synthesis/student_incommuters.py` | `braunschweig.constants`, `braunschweig.data.cordon.demand`, `braunschweig.data.cordon.incommuter_origins`, `braunschweig.data.cordon.mode_reference`, `braunschweig.data.cordon.plans`, `braunschweig.data.education.student_incommuter_counts`, `braunschweig.data.education.student_origins`, `braunschweig.data.external_workplaces`, `braunschweig.data.mikrozensus.reference`, `braunschweig.synthesis.incommuters` | d |
| `braunschweig.synthesis.vehicles.cars.household` | `braunschweig/synthesis/vehicles/cars/household.py` | `braunschweig.data.kba.hsn_tsn`, `braunschweig.synthesis.vehicles.fleet_sampling_de` | |
| `data.gtfs.cleaned` | `data/gtfs/cleaned.py` | `braunschweig.data.cordon.network_clip`, `data.gtfs.utils` | |
| `data.hts.entd.cleaned` | `data/hts/entd/cleaned.py` | `data.hts.hts` | |
| `data.osm.cleaned` | `data/osm/cleaned.py` | `braunschweig.data.cordon.network_clip` | |
| `synthesis.population.spatial.primary.candidates` | `synthesis/population/spatial/primary/candidates.py` | `braunschweig.gravity.taz_margins` | |

Note: `braunschweig.freight.extraction` appears here because it is imported
by `braunschweig.freight.trips` at module level as expected (a declared
dependency, not a gap for `trips`), but `extraction` **itself** imports
`braunschweig.data.spatial.cordon` without declaring it — the gap belongs to
`extraction`, not to its caller.

### Not reachable in the resolved production config (49)

| Stage | Uncovered modules | d |
|---|---|---|
| `braunschweig.data.ba.pendler_detailed` | `braunschweig.data.kreis_key_guard` | |
| `braunschweig.data.census.household_income` | `braunschweig.data.mid.reference_tables` | |
| `braunschweig.data.census.households_type` | `braunschweig.data.census.households_size_age` | d |
| `braunschweig.data.inkar.full_panel` | `braunschweig.data.kreis_key_guard` | |
| `braunschweig.data.mid.data` | `braunschweig.data.mid.reference_tables` | |
| `braunschweig.data.mid.school_distance` | `braunschweig.calibration.circuity` | |
| `braunschweig.data.mikrozensus.school_distance` | `braunschweig.calibration.circuity` | |
| `braunschweig.ipf.attributed` | `braunschweig.ipf.config_validation`, `braunschweig.ipf.household_composition` | |
| `braunschweig.ipf.model` | `braunschweig.ipf.config_validation`, `braunschweig.ipf.joint_age_size` | |
| `braunschweig.ipf.prepare` | `braunschweig.ipf.joint_age_size` | |
| `braunschweig.synthesis.population.enriched` | `braunschweig.data.mid.income_by_size`, `braunschweig.data.mid.income_by_status`, `braunschweig.data.mid.reference_tables`, `braunschweig.data.mid.tenure_by_income` | |
| `data.bpe.cleaned` | `data.spatial.utils` | |
| `data.census.cleaned` | `data.hts.hts` | |
| `data.hts.edgt_44.cleaned` | `data.hts.hts` | |
| `data.hts.edgt_44.filtered` | `data.hts.hts` | |
| `data.hts.edgt_44.raw` | `data.hts.edgt_44.format` | |
| `data.hts.edgt_lyon.cleaned_adisp` | `data.hts.hts` | |
| `data.hts.edgt_lyon.cleaned_cerema` | `data.hts.hts` | |
| `data.hts.edgt_lyon.filtered` | `data.hts.hts` | |
| `data.hts.egt.cleaned` | `data.hts.hts` | |
| `data.hts.egt.filtered` | `data.hts.hts` | |
| `data.hts.entd.filtered` | `data.hts.hts` | |
| `eqasim_common.analysis.methods.income.compare_methods` | `synthesis.population.income.utils` | |
| `eqasim_common.analysis.reference.census.sociodemographics` | `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.reference.hts.chains` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.chains`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.reference.hts.commute_distance` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.reference.hts.commute_flow` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.reference.hts.sociodemographics` | `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.commute_distance` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.commute_flow` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.income` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.matching` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.mode_distances` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.sociodemographics.chains` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.chains`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.sociodemographics.general` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.sociodemographics.spatial` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.statistics.marginal` | `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics` | |
| `eqasim_common.analysis.synthesis.statistics.monte_carlo` | `eqasim_common.analysis.bootstrapping`, `eqasim_common.analysis.marginals`, `eqasim_common.analysis.statistics`, `eqasim_common.analysis.synthesis.statistics.marginal` | d |
| `eqasim_common.locations.synthesis.replacement` | `synthesis.population.spatial.primary.locations` | d |
| `matsim.scenario.facilities` | `matsim.writers` | |
| `matsim.scenario.households` | `matsim.writers` | |
| `matsim.scenario.population` | `matsim.writers` | |
| `matsim.scenario.vehicles` | `matsim.writers` | |
| `synthesis.population.enriched` | `data.hts.egt.cleaned`, `data.hts.entd.cleaned` | d |
| `synthesis.population.income.bhepop2` | `synthesis.population.income.utils` | |
| `synthesis.population.income.uniform` | `synthesis.population.income.utils` | |
| `synthesis.population.matched` | `data.hts.egt.cleaned`, `data.hts.entd.cleaned` | d |
| `synthesis.population.spatial.home.locations` | `data.spatial.utils` | |
| `synthesis.population.spatial.secondary.locations` | `synthesis.population.spatial.secondary.components`, `synthesis.population.spatial.secondary.problems`, `synthesis.population.spatial.secondary.rda` | |

These are mostly vendored-upstream stage variants (ENTD/EDGT/EGT/Lyon HTS
pipelines, the plain `matsim.scenario.*` MATSim writers, the base
`synthesis.population.enriched`/`matched`) that the BS production config
supersedes via the `aliases:` block rather than running directly — "not
reachable" here is a firm, derived answer (from the committed DAG snapshot,
not a guess), not `unknown`.

## Category (d) — undeclared stage-as-library import (21 stages)

Every stage whose own code imports another synpp stage module (has its own
`configure`+`execute`) as a plain function/attribute library, where that
import is **not** matched by a `context.stage(...)` call (alias-resolved) in
the importer's own `configure()`. 16 of 21 are production-reachable.

| Stage | Reachable | Primary category | Undeclared stage import(s) |
|---|---|---|---|
| `braunschweig.data.bosserhof_location_category` | yes | c | `braunschweig.data.bosserhof_purpose` |
| `braunschweig.data.external_secondary_points` | yes | c | `braunschweig.data.external_workplaces` |
| `braunschweig.data.hts.mid_donor` | yes | c | `braunschweig.popsim.stage` |
| `braunschweig.data.verbindungen.zones` | yes | c | `braunschweig.data.bbsr.regiostar` |
| `braunschweig.locations.synthesis.replacement_education_gravity` | yes | c | `synthesis.population.spatial.primary.locations` |
| `braunschweig.matsim.scenario.facilities` | yes | c | `matsim.scenario.facilities` |
| `braunschweig.matsim.scenario.households` | yes | c | `matsim.scenario.households` |
| `braunschweig.matsim.scenario.population` | yes | c | `matsim.scenario.population` |
| `braunschweig.matsim.scenario.vehicles` | yes | c | `matsim.scenario.vehicles` |
| `braunschweig.matsim.simulation.prepare` | yes | c | `matsim.runtime.eqasim`, `matsim.simulation.prepare` |
| `braunschweig.popsim.completed_donor` | yes | c | `braunschweig.popsim.stage` |
| `braunschweig.popsim.distance_distributions` | yes | c | `synthesis.population.spatial.secondary.distance_distributions` |
| `braunschweig.popsim.stage` | yes | **b** | `braunschweig.data.census.household_size`, `braunschweig.synthesis.population.enriched` |
| `braunschweig.synthesis.incommuters` | yes | c | `braunschweig.data.external_workplaces` |
| `braunschweig.synthesis.locations.secondary_candidates` | yes | c | `braunschweig.synthesis.locations.secondary_chainsolvers` |
| `braunschweig.synthesis.student_incommuters` | yes | c | `braunschweig.data.external_workplaces`, `braunschweig.synthesis.incommuters` |
| `braunschweig.data.census.households_type` | no | c | `braunschweig.data.census.households_size_age` |
| `eqasim_common.analysis.synthesis.statistics.monte_carlo` | no | c | `eqasim_common.analysis.synthesis.statistics.marginal` |
| `eqasim_common.locations.synthesis.replacement` | no | c | `synthesis.population.spatial.primary.locations` |
| `synthesis.population.enriched` | no | c | `data.hts.egt.cleaned`, `data.hts.entd.cleaned` |
| `synthesis.population.matched` | no | c | `data.hts.egt.cleaned`, `data.hts.entd.cleaned` |

**A distinct, systematic sub-pattern inside this list:** the four BS
MATSim-writer overrides (`braunschweig.matsim.scenario.facilities` /
`households` / `population` / `vehicles`) plus
`braunschweig.matsim.simulation.prepare` each `import matsim.scenario.<X> as
base` (or the `matsim.simulation.prepare` equivalent) at module level and call
into it as a code base, without ever declaring
`context.stage("matsim.scenario.<X>")`. This is the same shape as the two
documented `popsim.stage` cases, just repeated four/five times across the
MATSim-scenario-writer family — none of these five stages currently has a
`validate()` hook at all, so an edit confined to the vendored base module
would silently reuse stale cached scenario-writer output.

`determining "declared"` required resolving each `context.stage("name")`
literal through the 22-entry `aliases:` table in `configs/base_bs.yml` before
comparing to the imported module's own dotted path (see "Method", step 3);
every case above was resolved this way with no ambiguity — no stage needed an
`unknown` mark for this determination.

## Prioritisation (production impact)

"Reachable" = the stage's alias-resolved module dotted name appears as a node
in the committed DAG snapshot `docs/registry/dag/production.json`, which
records the resolution of exactly `configs/base_bs.yml` +
`configs/overlays/test_100pct.yml` (confirmed from the snapshot's own
`config.base`/`config.overlay` fields) — i.e. derived from the canonical
production config, not guessed. 90 of the snapshot's 91 nodes map onto a
scanned stage (the missing one, `documentation.meta_output`, lives outside
the five audited roots — see "Limitations").

Ordered by combined severity — reachable AND in category (d) (an undeclared
stage-as-library edge, the sharper failure mode since neither synpp's DAG nor
any token sees it) first, then reachable category (c) with the largest
uncovered surface, then everything else:

1. **The `matsim.scenario.*` BS-override family (5 stages, reachable, (d), no
   `validate()` at all):** `braunschweig.matsim.scenario.facilities`,
   `households`, `population`, `vehicles`, and
   `braunschweig.matsim.simulation.prepare` each use the vendored
   `matsim.scenario.*`/`matsim.simulation.prepare` stage as an undeclared
   code base. This is the MATSim-facing end of the pipeline (facilities,
   households, population and vehicle scenario files, and simulation
   preparation) — a silently stale scenario write would be very hard to
   notice downstream. No coverage of any kind exists today.
2. **`braunschweig.synthesis.incommuters` / `student_incommuters` /
   `popsim.completed_donor` (reachable, (d), no `validate()`):** the cordon
   in-commuter chain — each has a large plain-helper surface (11-12 uncovered
   modules for `incommuters`) plus an undeclared stage-as-library edge, and no
   token at all.
3. **`braunschweig.gravity.model` (reachable, (c), known):** the documented
   gap from issue #289 — `friction`, `production_mass`, `taz_margins`,
   `verbindungen_anchor` uncovered; already tracked, not new.
4. **`braunschweig.synthesis.locations.secondary_chainsolvers` (reachable,
   (c), NOT previously documented):** its own split note
   (`secondary-chainsolvers-split.md`) states "this package has no external
   siblings outside its own boundary... so there is no equivalent 'known
   gap'" — that claim only considered sibling submodules; it imports 4
   modules **outside** its own package boundary
   (`braunschweig.calibration.secondary_measurement`, `braunschweig.parallelism`,
   `braunschweig.synthesis.locations.escort_links`,
   `synthesis.population.spatial.secondary.problems`) that its `validate()`
   does not hash. This is a genuine, newly-surfaced gap on a stage already
   believed fully covered.
5. **`braunschweig.synthesis.population.enriched` (reachable only via the
   `simple_ipf_open` fixture per its own split note; not in the popsim-based
   production DAG snapshot — hence listed as "not reachable" above, but
   flagged here because it is the enrichment path for the non-popsim
   pipeline) (c), NOT previously documented:** its own module-level import
   `from braunschweig.data.mid.reference_tables import load_class_midpoint_eur`
   plus 3 lazily-imported `braunschweig.data.mid.*` modules are outside
   `_HELPER_MODULES`.
6. **`braunschweig.synthesis.locations.secondary_candidates` (reachable, (c)
   + (d)):** its `validate()` delegates entirely to
   `secondary_chainsolvers._HELPER_MODULES`, so it hashes chainsolvers'
   submodules but never `secondary_chainsolvers/__init__.py` itself (the
   facade file, undeclared as a dependency) nor
   `braunschweig.synthesis.locations.landuse_candidates`.
7. Everything else in the reachable category-(c) table, roughly grouped by
   family: the cordon-data chain (`cordon_network`/`cordon_gemeinden`/
   `cordon_pt_gates`/`cordon_gates`/`external_secondary_points`/
   `external_workplaces`), the schools family, and
   `braunschweig.analysis.analysis_suite`/`cordon_validation`/
   `simwrapper_export` (each of which fans out to several
   `braunschweig.analysis.*` submodules with no token covering them).

Category (c)/(d) stages **not** in the reachable table (49 + 5) are lower
priority: mostly vendored HTS pipeline variants (ENTD/EDGT/EGT/Lyon) and
`eqasim_common` analysis-reference modules the BS config's `run:` targets
never reach.

## Verification

**Classifier reproduces both known-good reference cases:**

- `braunschweig/gravity/model.py`: required helpers = the 5 covered siblings
  (`attraction_vector`, `balancing`, `base`, `kreis_calibration`, `od`) **plus**
  `friction`, `production_mass`, `taz_margins`, `verbindungen_anchor` — 9
  total, 5 covered, 4 uncovered. The 4 uncovered names match issue #289's list
  **exactly**. Reproduced independently, not copied from the issue text.
- `braunschweig/popsim/stage/__init__.py`: required helpers = 44, covered =
  44, uncovered = none (category b). The two undeclared-stage-as-library
  imports the classifier found by AST alone —
  `braunschweig.data.census.household_size` and
  `braunschweig.synthesis.population.enriched` — match the two cases the
  stage's own `validate()` docstring names as "exactly two modules qualify
  today".

**Three category-(a) verdicts checked by hand:**

- `braunschweig/freight/trips.py` imports `braunschweig.freight.extraction` at
  module level (`from braunschweig.freight import extraction`), but its
  `configure()` declares `context.stage("braunschweig.freight.extraction")`,
  so this is a declared edge already covered by synpp's own hash propagation,
  not a gap — correctly excluded from `required_helpers`. (`extraction`
  itself is a separate row in the reachable category-(c) table above, for its
  own, unrelated uncovered import of `braunschweig.data.spatial.cordon`.)
- `data/sirene/raw_siret.py` imports only `os` and `pandas` — no first-party
  imports at all; trivially category (a).
- `braunschweig/synthesis/income.py` imports only `numpy`, `pandas`,
  `multiprocessing`, `tqdm` — no first-party imports; trivially category (a).

**Three category-(c) verdicts checked by hand (beyond gravity, above):**

- `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py` line
  66-67: `from braunschweig.calibration.secondary_measurement import
  boundary_clip_share` and `from synthesis.population.spatial.secondary.problems
  import find_assignment_problems`, both module-level, neither in
  `_HELPER_MODULES` (verified by reading the tuple directly at source line
  251).
- `data/hts/entd/cleaned.py` line 4: `import data.hts.hts as hts` at module
  level; the stage has no `validate()` at all, so this plain helper import is
  completely uncovered.
- `synthesis/population/spatial/primary/candidates.py` line 227:
  `from braunschweig.gravity.taz_margins import assign_taz`, inside a function
  body (lazy) — exactly the kind of import a module-level-only scan would
  miss; the stage has no `validate()`, so it is uncovered.

## Limitations

What this method cannot see, checked rather than assumed:

- **`__getattr__`-based lazy attribute access (PEP 562):** three first-party
  modules define a module-level `__getattr__` —
  `braunschweig/data/census/household_income.py` (lazily computes
  `CLASS_MIDPOINT_EUR` from data, not a module import),
  `braunschweig/popsim/stage/__init__.py` and
  `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py` (both
  forward a fixed, small set of named attributes to an already-imported
  sibling module for worker-state mutability — not a way to reach an
  otherwise-uncounted module). None of the three introduces an import this
  scan would miss.
- **Dynamic `importlib.import_module` with a computed name:** the only
  non-trivial use found is inside `popsim.stage`'s own `validate()`, over the
  fixed, literal `_DEFERRED_HELPER_MODULE_NAMES` tuple — not a computed name,
  so fully resolvable statically; no other first-party module in the five
  roots uses `importlib.import_module` at all (checked by `grep`).
- **A `def`-only stage-detection rule:** confirmed (by `grep` for
  `^configure\s*=`/`^execute\s*=` across all five roots) that no stage in this
  codebase is produced by a factory function returning `(configure, execute)`
  closures bound at module level — `braunschweig/synthesis/incommuter_merge/_base.py`
  defines exactly such a factory (`make_wrapper`) but nothing currently calls
  it to produce a module-level stage, so it does not register as one and
  correctly does not appear in the 230.
- **Out-of-scope root:** `documentation.meta_output`, one of the 91 nodes in
  the committed production DAG snapshot, resolves to a module under the
  repo-root `documentation/` directory, which is not one of the five audited
  first-party roots (it is a separate set of paper/report-generation
  scripts, unrelated to the `braunschweig`/`data`/`eqasim_common`/`matsim`/
  `synthesis` package tree). Its coverage status is genuinely `unknown` here,
  not derived.
- **Every count here is a LOWER BOUND, not a total.** Because the scan stops at
  one level (next bullet), a stage counted as (b) "fully covered" is covered
  only to that same depth, and the uncovered-module lists for (c) name only the
  stage module's own direct imports. `secondary_chainsolvers` illustrates the
  difference: the stage module has 4 uncovered direct imports, while its covered
  submodules import roughly as many more first-party modules again, all equally
  outside the token. Read a (c) row as "at least these".
- **Transitive imports beyond one level:** by design (matching the two
  reference implementations' own stated boundary), this audit resolves only
  each stage's **own direct** imports, not what its helpers in turn import.
  A change buried two levels deep (a helper's helper) is out of scope for
  every category here, including (b) — this mirrors the explicit boundary
  `popsim.stage`'s own `validate()` docstring states for itself.
- **No dynamic/computed `context.stage(...)` calls exist today** (checked:
  every call across all 230 stages had a literal string first argument), so
  no stage needed an `unknown` mark for declared-dependency resolution this
  time; a future stage that builds its dependency name from a variable would
  need one, and this method would have to flag it explicitly rather than
  guess.
- **Aliases are read only from `configs/base_bs.yml`**, the canonical
  production config, per CLAUDE.md's truth hierarchy — other committed
  configs (`configs/dryrun_*`, any fixture-only config) may declare different
  aliases for the same stage name; this audit does not attempt to resolve
  "declared" against every possible config, only the canonical one, so a
  stage's undeclared-dependency status could differ under a non-canonical
  config. Not evaluated here; out of scope by the issue's own framing
  ("prioritised by whether affected stages are default-ON in the canonical
  production config").

## PR / issue reference

Issue #290 (audit), companion to issue #289 (the gravity-specific coverage
gap this audit reproduces independently). No code, cache token, or
`_HELPER_MODULES` tuple was changed to produce this file.
