"""Config-key constants for the popsim_mid stage (``braunschweig.popsim.stage``).

Every ``KEY_*`` constant names a ``braunschweig.population.popsim.*`` config key
the stage declares in ``configure()`` and reads in ``execute()``; the two
``_KREIS_CONTROL_*`` dicts drive the per-attribute KREIS control toggles
(``source_resolution.active_kreis_entries``). Extracted verbatim from the stage
module (``__init__``); this is a LEAF submodule (no imports from this package),
so any other submodule may import these names directly without risking a
partial-initialisation ordering problem.
"""

# Config keys.
KEY_CELLS = "braunschweig.population.popsim.cells_100m_path"
KEY_MID = "braunschweig.population.popsim.mid_raw_path"
KEY_CONTROLS = "braunschweig.population.popsim.controls_path"
KEY_SETTINGS = "braunschweig.population.popsim.settings_path"
KEY_LOGGING = "braunschweig.population.popsim.logging_path"
KEY_POPSIMPREP = "braunschweig.population.popsim.popsimprep_dir"
KEY_UV = "braunschweig.population.popsim.uv_path"
KEY_MAX_CELLS = "braunschweig.population.popsim.max_cells"
KEY_WORKERS = "braunschweig.population.popsim.num_workers"
KEY_WORK_DIR = "braunschweig.population.popsim.work_dir"
# Hard per-batch PopulationSim wall-clock limit (seconds). A batch exceeding this is
# killed and flagged "failed (timeout)". Heavy control sets (tier1/2 + stratify) make
# big batches slow; raise this so they finish + converge cleanly instead of being killed.
KEY_BATCH_TIMEOUT = "braunschweig.population.popsim.batch_timeout_s"
# Delete each batch's dead PopulationSim checkpoint store (output/pipeline.h5) once the
# batch is VERIFIED complete (issue #153: ~15 GB/batch at full donor pool would overflow
# the run server's disk mid-campaign). Default ON (project rule: new features default on).
# Set False to keep the stores, e.g. for balancer forensics on a small run.
KEY_CLEANUP_H5 = "braunschweig.population.popsim.cleanup_batch_pipeline"
KEY_KREISE = "braunschweig.political_prefix"
# Donor source identifier: "mid" (default) or a future registered source name.
KEY_SOURCE = "braunschweig.population.popsim.source"
# RegioStaR donor stratification (Phase 4B). Default ON (project rule: new features
# default on); set False for the byte-identical pre-4B path (full seed per batch,
# still supported + unit-tested).
KEY_STRATIFY = "braunschweig.population.popsim.stratify_regiostar"
# Member completion (decision D3, mid source only): fill member-incomplete MiD
# donor households by mirror-household sampling, in ONE pass on the attribute
# donor tables that feeds BOTH the PopulationSim seed and the expansion.
# Default ON (project rule: new features default on); False reproduces the
# legacy load_mid_seed + load_donor path byte-identically.
KEY_COMPLETE_MEMBERS = "braunschweig.population.popsim.complete_members"
# Controls source: "csv" (default, byte-identical, reads the external hand-edited
# file at KEY_CONTROLS) or "catalog" (renders from the typed control catalog via
# control_spec; Task 5 of the control-catalog plan).
KEY_CONTROLS_SOURCE = "braunschweig.population.popsim.controls_source"
# Control tiers: comma-separated tier names included when controls_source="catalog".
# Default "tier0" = byte-identical to the pre-Task-7 baseline.
KEY_CONTROL_TIERS = "braunschweig.population.popsim.control_tiers"
# Tier-3 KREIS controls: directory holding the imported cleancensus kreis_* tables
# (kreis_erwerbsstatus/schulabschluss/berufl_abschluss.parquet). Loaded only when
# "tier3" is among control_tiers (catalog source); ignored otherwise.
KEY_KREIS_CONTROLS = "braunschweig.population.popsim.kreis_controls_dir"
# Employment grid control (Task 5): when "on", activates the ten age-group x sex-resolved
# 100m employment controls (EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg). The targets are
# computed per cell from the Zensus 2000S-2001 employment-by-age SHAPE rescaled per
# Kreis x sex x group to the census Erwerbstaetige Kreis level
# (braunschweig.popsim.employment_grid). Default "off" = byte-identical to today.
KEY_EMPLOYMENT_GRID = "braunschweig.population.popsim.employment_grid"
# PopulationSim per-control importance profile name (see control_spec.IMPORTANCE_PROFILES).
KEY_IMPORTANCE_PROFILE = "braunschweig.population.popsim.importance_profile"
# Seed reporting-day filter: which MiD kernwo values to KEEP in the PopulationSim
# seed. "default" -> (1,2,3) Mo-Fr (legacy: weekend / kernwo=4 households dropped).
# "off"/"all" -> keep ALL reporting days (no day filter). The reporting day is a
# trip-modelling concern, irrelevant to the population's employment/education/HH
# composition; "off" enlarges the donor pool (reduces IPU weight concentration).
KEY_SEED_DAY_FILTER = "braunschweig.population.popsim.seed_day_filter"
# Spatial income tilt (Nettokaltmiete GAMMA layer): default ON per project rule.
# When ON, applies a within-Kreis income redistribution scaled by the per-cell
# net cold rent index (renters) or Eigentümerquote index (owners), preserving the
# per-Kreis income mean exactly. When OFF, the income frame is unchanged (byte-identical).
KEY_INCOME_TILT = "braunschweig.population.popsim.income_spatial_tilt"
KEY_INCOME_TILT_BETA = "braunschweig.population.popsim.income_tilt_beta"
KEY_INCOME_TILT_CLIP = "braunschweig.population.popsim.income_tilt_clip"

# Kreis-Income-Control: real MiD income draw + max-entropy per-Kreis calibration.
# Default ON (project rule). When ON it OVERWRITES the apply_inkar_income_eur output
# (build_persons) with a real continuous draw reshaped to the per-Kreis INKAR target.
# When OFF, build_persons' midpoint x INKAR_scale output is left byte-identical.
KEY_INCOME_KC = "braunschweig.population.popsim.income_kreis_control"
KEY_INCOME_KC_METHOD = "braunschweig.population.popsim.income_draw_method"
KEY_INCOME_KC_HHSIZE = "braunschweig.population.popsim.income_kreis_control_hhsize_correct"
KEY_INCOME_KC_PARETO = "braunschweig.population.popsim.income_open_top_pareto"
KEY_INCOME_KC_PARETO_ALPHA = "braunschweig.population.popsim.income_open_top_pareto_alpha"
# placement_income (L2, issue #108): donor keeps its OWN MiD income; the per-Kreis
# INKAR relativity is approached by signature-preserving donor reallocation after the
# popsim merge. Default ON (project rule). ON overrides income_kreis_control AND
# income_spatial_tilt (logged); OFF is byte-identical to the legacy path.
KEY_PLACEMENT_INCOME = "braunschweig.population.popsim.placement_income"
# economic_status x Kreis control (Level 1, issue #109). Default "on" (project rule:
# new features default on). "off" -> no status control + seed schema unchanged (byte-
# identical). MiD-only (oek_status has no ENTD pendant); ignored for source="entd".
KEY_STATUS_KREIS_CONTROL = "braunschweig.population.popsim.status_kreis_control"
# Dirichlet shrinkage of the per-Kreis H4 status target toward the ZGB aggregate, in
# pseudo-households. Default 0.0 = raw per-Kreis H4 (no shrinkage). Range: >= 0.
KEY_STATUS_KREIS_SHRINKAGE_N = "braunschweig.population.popsim.status_kreis_shrinkage_n"
# Additional per-Kreis attribute controls (S1c, issue #109 follow-up), each driven by a
# committed blended target (target2026_*) and individually toggleable; MiD-only (their
# seed columns have no ENTD pendant). "off" for a given attribute drops its control + its
# seed column (byte-identical to today for that attribute). The blended targets are FINAL
# (consumed with prior_n = 0). number_of_cars / number_of_bicycles / has_ebike all default
# "on" (project rule: new features default on) -- has_ebike was blocked pending server
# verification of the MiD household e-bike column; that verification landed 2026-07-08
# (H_ANZPED, see KEY_EBIKE_SEED_COLUMN), so it is now wired and defaults on like the rest.
KEY_CARS_KREIS_CONTROL = "braunschweig.population.popsim.number_of_cars_kreis_control"
KEY_BIKES_KREIS_CONTROL = "braunschweig.population.popsim.number_of_bicycles_kreis_control"
KEY_EBIKE_KREIS_CONTROL = "braunschweig.population.popsim.has_ebike_kreis_control"
# trip_class x Kreis control (first PERSON-level KREIS attribute control, issue #116
# follow-on, 2026-07-08): steers the per-Kreis distribution of trips-on-the-reporting-day
# (0 / 1-2 / 3-4 / 5+), targeted at the committed SrV 2023 aggregate. Default "on"
# (project rule: new features default on). "off" drops its control + seed column
# (byte-identical for that attribute). MiD-only (its seed column, anzwege1, has no ENTD
# pendant); ignored for source="entd".
KEY_TRIPS_KREIS_CONTROL = "braunschweig.population.popsim.trip_class_kreis_control"
# employment_status x Kreis control (feature #172 task 4, second PERSON-level entry):
# steers the per-Kreis distribution of the seven MiD P_BKAT employment-extent classes
# (vollzeit/teilzeit/geringfuegig/sonstiges/erwerbstaetig_unspec/in_ausbildung/
# nicht_erwerbstaetig) to the committed MiD-P9 x SrV-V_ERW blended target. Default "on"
# (project rule: new features default on). "off" drops its control + seed column
# (byte-identical for that attribute). MiD-only (employment_status has no ENTD pendant);
# ignored for source="entd". Its committed target + seed universe are BOTH restricted to
# age >= 14 (kreis_attribute_control.REGISTRY entry min_age=14) -- see
# person_total_by_kreis_min_age below.
KEY_EMPLOYMENT_STATUS_KREIS_CONTROL = "braunschweig.population.popsim.employment_status_kreis_control"
# work_participation x Kreis control (feature #224 task 4, third PERSON-level entry):
# steers the per-Kreis distribution of the 0/1 has-a-work-trip flag (derived from each
# person's MiD Wege via mid.compute_has_work_trip) to the committed SrV-2023
# participation blended target. Default "on" (project rule: new features default on).
# "off" drops its control + seed column (byte-identical for that attribute). MiD-only
# (its seed derivation reads the MiD Wege table); ignored for source="entd".
KEY_WORK_PARTICIPATION_CONTROL = "braunschweig.population.popsim.work_participation_kreis_control"
# leisure_participation / education_participation x Kreis controls (feature #224 task 5,
# fourth and fifth PERSON-level entries): identical wiring to work_participation, steering
# the per-Kreis distribution of the 0/1 has-a-leisure-trip / has-an-education-trip flag
# (mid.compute_has_purpose_trip, parametrized by purpose) to their respective committed
# SrV-2023 participation targets. Default "on" (project rule: new features default on).
# "off" drops the control + seed column (byte-identical for that attribute). MiD-only
# (seed derivation reads the MiD Wege table); ignored for source="entd".
KEY_LEISURE_PARTICIPATION_CONTROL = "braunschweig.population.popsim.leisure_participation_kreis_control"
KEY_EDUCATION_PARTICIPATION_CONTROL = "braunschweig.population.popsim.education_participation_kreis_control"
# Name of the MiD household e-bike column feeding the has_ebike control. Default
# "H_ANZPED" (Anzahl Pedelecs, 0..10, missing code 99) -- verified 2026-07-08 against the
# server MiD B1 microdata (see braunschweig.popsim.attributes.map_has_ebike). Kept
# configurable in case a future MiD delivery renames the column (no silent fallback if
# has_ebike is active and this resolves empty).
KEY_EBIKE_SEED_COLUMN = "braunschweig.population.popsim.ebike_seed_column"
# Weekend-plan match: include weekend-surveyed MiD households in the seed by
# relaxing the day filter to ALL_REPORTING_KERNWO and remapping their
# source_H_ID/source_P_ID to a matched weekday household.  Default ON
# (project rule: new features default on).  When OFF, the donor build is
# byte-identical to today (weekday (1,2,3) filter only, no remap).
KEY_WEEKEND_PLAN_MATCH = "braunschweig.population.popsim.weekend_plan_match"


# Config toggle per KREIS attribute control (kreis_attribute_control.REGISTRY entry).
# economic_status keeps its historical key; the S1c additions get their own keys.
_KREIS_CONTROL_TOGGLE_KEY = {
    "economic_status": KEY_STATUS_KREIS_CONTROL,
    "number_of_cars": KEY_CARS_KREIS_CONTROL,
    "number_of_bicycles": KEY_BIKES_KREIS_CONTROL,
    "has_ebike": KEY_EBIKE_KREIS_CONTROL,
    "trip_class": KEY_TRIPS_KREIS_CONTROL,
    "employment_status": KEY_EMPLOYMENT_STATUS_KREIS_CONTROL,
    "work_participation": KEY_WORK_PARTICIPATION_CONTROL,
    "leisure_participation": KEY_LEISURE_PARTICIPATION_CONTROL,
    "education_participation": KEY_EDUCATION_PARTICIPATION_CONTROL,
}

# Per-entry default for its toggle (project rule: new features default "on"). has_ebike
# was blocked pending server verification of the MiD household e-bike column (issue
# #116); that verification landed 2026-07-08 (H_ANZPED, see KEY_EBIKE_SEED_COLUMN), and
# both seed paths (load_mid_seed and project_completed_seed) now derive it, so it
# defaults "on" like the other three entries.
_KREIS_CONTROL_DEFAULT = {
    "economic_status": "on",
    "number_of_cars": "on",
    "number_of_bicycles": "on",
    "has_ebike": "on",
    "trip_class": "on",
    "employment_status": "on",
    "work_participation": "on",
    "leisure_participation": "on",
    "education_participation": "on",
}
