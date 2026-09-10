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
# Ownership grid (issue #240): when "on", injects 9 per-100m-cell ownership target
# columns (OWN_CARS_{0,1,2,3plus}_agg + OWN_BIKES_{0,1,2,3,4plus}_agg) and adds the
# corresponding ZENSUS1km catalog controls. SHAPE from the committed MiD B1
# RS7 x haustyp conditionals; LEVEL raked per Kreis to the SAME blended target2026
# tables the KREIS ownership controls consume (asserted consistent). Requires the
# number_of_cars AND number_of_bicycles KREIS entries active (their seed columns).
# Default "on" (project rule); "off" is byte-identical to today's control set.
KEY_OWNERSHIP_GRID = "braunschweig.population.popsim.ownership_grid_1km"

# Fine teen age bands in the tier0 backbone (issue #320): "on" replaces the ten-year
# 10-19 age x sex controls with 10-15 / 16-17 / 18-19, adding 4 controls at 100m. The
# ten-year bands leave the composition inside a band unconstrained, which produced a
# 15-17 excess of +64% and an 18-19 shortfall of -75% against DESTATIS 12411-0018 on the
# 100% population (issue #307). Default "on"; "off" is byte-identical to the pre-#320
# control set (pinned by tests/fixtures/prep3_controls_baseline.csv).
KEY_FINE_TEEN_AGE_BANDS = "braunschweig.population.popsim.fine_teen_age_bands"
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
# escort_participation x Kreis control (issue #227, sixth PERSON-level entry): identical
# wiring to work/leisure/education_participation, steering the per-Kreis distribution of
# the 0/1 has-an-escort-trip flag (mid.compute_has_purpose_trip, ACTIVE W_ZWECK 6 only --
# see mid.PARTICIPATION_W_ZWECK) to the committed SrV-2023 participation target. Default
# "on" (project rule: new features default on). "off" drops its control + seed column
# (byte-identical for that attribute). MiD-only (seed derivation reads the MiD Wege
# table); ignored for source="entd".
KEY_ESCORT_PARTICIPATION_CONTROL = "braunschweig.population.popsim.escort_participation_kreis_control"
# work_by_employment x Kreis control (Plan B, issue #368, ADR-0109): REPLACES
# work_participation by default. Four MECE labels (employment status x direct work leg)
# over the persons 14+ universe (WORK_BY_EMPLOYMENT_MIN_AGE_YEARS), read by
# braunschweig.popsim.stage. Default "on" -- work_participation flips to "off" alongside it
# (see _KREIS_CONTROL_DEFAULT below) so the two controls never both steer the same
# work-trip mass at once (source_resolution.active_kreis_entries raises otherwise).
KEY_WORK_BY_EMPLOYMENT_CONTROL = "braunschweig.population.popsim.work_by_employment_kreis_control"
# education_by_age x Kreis controls (Plan B, issue #368, ADR-0109): ONE toggle for the
# three age-range entries (education_0_5 / education_6_17 / education_18plus,
# kreis_attribute_control.EDUCATION_BY_AGE_ENTRY_NAMES) -- they share a single seed column
# and a single universe mechanism, so they are switched together. REPLACES
# education_participation by default, which flips to "off" alongside it (see
# _KREIS_CONTROL_DEFAULT below).
KEY_EDUCATION_BY_AGE_CONTROL = "braunschweig.population.popsim.education_by_age_kreis_control"
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

# Diary plan match (issue #365, plan-structure-fix Task 3): when a synthetic
# person's plan source has no realisable MiD diary (no-diary code 803
# "Person ohne Wegeerfassung" with mobil == 1, or 804 "Mobilitaet unbekannt";
# only rbW legs; emptied by the leading arrive-home-leg drop; or a public
# holiday), remap its source_H_ID/source_P_ID to a matched weekday donor with
# a realisable diary (braunschweig.popsim.diary_plan_match.
# reassign_diaryless_plan_sources). Default ON (project rule: new features
# default on). NOTE: the completed_donor stage ALWAYS reads MiD2023_Wege.csv
# and attaches the src_* plan-source diary fact columns, regardless of this
# flag (or the three below) -- they are facts about the plan source's diary,
# not behaviour. "OFF is byte-identical" below refers to source_H_ID/
# source_P_ID (and the downstream plans built from them), NOT to the input
# file set read.
KEY_DIARY_PLAN_MATCH = "braunschweig.population.popsim.diary_plan_match"
# Each DEFAULT_* below is the declared default of the KEY_* immediately above it. Every one of
# these five plan-structure keys is declared by MORE THAN ONE stage
# (braunschweig.popsim.trips_stage, braunschweig.popsim.completed_donor and
# braunschweig.synthesis.commute_day.home_office_donors_stage), because synpp requires every
# stage that READS a key to declare it. synpp resolves ONE value per key per run, so the stages
# must declare the IDENTICAL default or the value a stage sees would depend on which stage
# happened to declare it first. Naming the defaults here -- next to the keys, in the leaf module
# every one of those stages already imports -- makes that identity structural instead of a
# convention three files have to keep by hand. (closure_dwell_min_obs' default stays in
# trips_stage as DEFAULT_CLOSURE_DWELL_MIN_OBS: it sizes the empirical model that module owns,
# and the other stages import it from there for the same one-home reason.)
DEFAULT_DIARY_PLAN_MATCH = True
# Exclude public-holiday-reported diaries (feiertag == 1) from the realisable
# plan-source pool and remap persons sourced from one: SrV reference days
# exclude public holidays, so a holiday-reported diary is not a realisable
# weekday plan. Default ON. Read only when diary_plan_match is ON (see the
# note on that key re: the Wege table + src_* facts being read/attached
# regardless of this flag).
KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES = "braunschweig.population.popsim.exclude_holiday_plan_sources"
DEFAULT_EXCLUDE_HOLIDAY_PLAN_SOURCES = True
# Never relax the `employed` key when the diary plan match re-draws a plan
# source (issue #368, Plan B Task 6). weekend_plan_match.match_person drops
# `employed` second-from-last on its relaxation ladder, so a non-employed person
# could inherit an employed donor's work diary while their own employment
# attribute comes from a DIFFERENT MiD respondent -- a plan the
# employment-conditional work control would then be fighting. A GUARD, not a
# correction (see configs/base_bs.yml for the measured magnitude). Default ON;
# read ONLY by braunschweig.popsim.completed_donor, which is where the diary
# match runs, so it is declared there (like exclude_holiday_plan_sources) and
# reaches the popsim stage through that stage dependency.
KEY_DIARY_MATCH_HARD_EMPLOYMENT = "braunschweig.population.popsim.diary_match_hard_employment"
# Declared next to the key like every neighbouring flag (final fix wave, item 3): the
# stage that declares it used to spell the default as a literal `True`, so the default and
# the key had two independent homes and could drift apart silently.
DEFAULT_DIARY_MATCH_HARD_EMPLOYMENT = True
# Exclude rbW-only diaries (n_direct_legs == 0, n_rbw_legs > 0 -- the diary
# consists ONLY of regelmaessige berufliche Wege summary legs, no individually
# reported trip) from the realisable plan-source pool and remap persons
# sourced from one. Default ON. Read only when diary_plan_match is ON (see the
# note on that key re: the Wege table + src_* facts being read/attached
# regardless of this flag).
KEY_EXCLUDE_RBW_LEGS = "braunschweig.population.popsim.exclude_rbw_legs"
DEFAULT_EXCLUDE_RBW_LEGS = True
# Treat a diary that starts by arriving home (first_so1 == 2, i.e. the
# reporting day begins mid-trip and the first RECORDED leg only arrives home)
# as having that leading leg dropped when counting direct legs, and remap a
# plan source whose diary becomes empty after the drop. Default ON. Read by
# braunschweig.popsim.completed_donor (plan-source realisability, only when
# diary_plan_match is ON), by braunschweig.popsim.trips_stage (the leg is
# actually dropped there) and by braunschweig.popsim.stage, whose trip_class
# seed SUBTRACTS exactly that dropped leg when trip_class_seed_counts_closure
# is on (controller ruling R20) -- all three must see the SAME value, or seed
# and plan count different days again.
KEY_DROP_LEADING_ARRIVE_HOME_LEG = "braunschweig.population.popsim.drop_leading_arrive_home_leg"
DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG = True
# Dwell-time model applied when a synthesised trip-chain closure is needed
# (spec 2026-09-05-plan-structure-fix-design.md): "empirical" (default) draws
# the closing dwell duration from the observed distribution of the same purpose
# x arrival band; "fixed_1h" reproduces the previous hard-coded 3600 s constant
# BYTE-IDENTICALLY -- including the absence of the plan-time cap, which is an
# EMPIRICAL-path behaviour only (only a drawn dwell can exceed the bound where
# the constant would not; ruling R19). Read by braunschweig.popsim.trips_stage.
KEY_CLOSURE_DWELL_MODEL = "braunschweig.population.popsim.closure_dwell_model"
DEFAULT_CLOSURE_DWELL_MODEL = "empirical"
# Minimum number of observations a (purpose x arrival band) cell of the EMPIRICAL
# closure-dwell model must hold before it is drawn from directly; a thinner cell
# falls back to the purpose marginal (rate logged). Positive integer, default 30.
# Inert when closure_dwell_model is "fixed_1h". Read by
# braunschweig.popsim.trips_stage.
KEY_CLOSURE_DWELL_MIN_OBS = "braunschweig.population.popsim.closure_dwell_min_obs"
# Seed the trip_class KREIS-control seed counts from the CLOSURE-augmented
# diary (i.e. after a synthesised closing leg is added) rather than the raw
# MiD anzwege1. Default ON (project rule: new features default on). Read by
# braunschweig.popsim.stage.
KEY_TRIP_CLASS_SEED_COUNTS_CLOSURE = "braunschweig.population.popsim.trip_class_seed_counts_closure"
# Map the PASSIVE escort leg (MiD W_ZWECK 13, the escorted child's own trip) to the
# education purpose (issue #256). This is a TRIP-BUILD flag, declared with this exact
# unprefixed key name and this exact default by braunschweig.popsim.trips_stage and
# braunschweig.synthesis.commute_day.home_office_donors_stage (synpp requires every stage
# that READS a key to declare it, so the declaration -- not the fact -- is repeated).
# THIS stage reads it because the education_flag KREIS-control seed must count the same
# codes as education that the trip build does (Plan B, issue #368): a seed built from
# {3, 11, 12} while the plan realises {3, 11, 12, 13} as education would make the control
# and the plan describe different days -- the same seed-vs-plan mismatch
# KEY_TRIP_CLASS_SEED_COUNTS_CLOSURE exists to close. All stages must therefore see the
# SAME value. Read by braunschweig.popsim.stage -> mid.derive_education_flag_seed.
KEY_ESCORT_PASSIVE_EDUCATION = "escort_passive_education"
DEFAULT_ESCORT_PASSIVE_EDUCATION = False

# Map MiD W_ZWECK 10 ("anderer Zweck") to the leisure purpose (issue #373, ADR-0111):
# MiD's own hwzweck1 derivation folds code 10 to 6 Freizeit for 100% of legs (committed
# evidence table mid2023_w_zweck_by_hwzweck1.csv), so the trip build, the seed and the
# distance layers must all treat it as leisure or they describe different days again --
# the same seed-vs-plan mismatch class KEY_ESCORT_PASSIVE_EDUCATION exists to close. A
# TRIP-BUILD flag, declared with this exact unprefixed key name (like
# escort_passive_education) by every stage that reads it: braunschweig.popsim.trips_stage,
# braunschweig.popsim.distance_distributions, braunschweig.popsim.stage (the
# leisure_participation KREIS-control seed must count the same W_ZWECK codes as leisure
# that the trip build does) and
# braunschweig.synthesis.commute_day.home_office_donors_stage. Default True is the
# PRODUCTION default (issue #373 task 2); the CODE default of the map_purpose /
# build_trip_table / participation_w_zweck keyword arguments stays False so a direct
# caller/test that omits it keeps today's behaviour.
KEY_W_ZWECK_10_AS_LEISURE = "w_zweck_10_as_leisure"
DEFAULT_W_ZWECK_10_AS_LEISURE = True

# Give a PAIRED passive escort leg (MiD W_ZWECK 13, "Begleitung, passiv") the purpose derived
# from the accompanying adult's W_ZWECK instead of the flat escort_passive_education relabel
# (issue #372, ADR-0112): the escorted child is wherever the adult went, and raw MiD B1 analysis
# (2026-09-09) puts the adult's own escort leg -- the only case that really is the child's own
# Kita/school trip -- at only ~21 % of the paired code-13 legs, so the flat "education" relabel is
# wrong for the other ~79 %. A TRIP-BUILD flag, declared with this exact unprefixed key name (like
# escort_passive_education / w_zweck_10_as_leisure) by every stage that reads it:
# braunschweig.popsim.trips_stage, braunschweig.popsim.distance_distributions,
# braunschweig.popsim.stage (its education_flag KREIS-control seed must count exactly the code-13
# legs the trip build realises as education -- the seed-vs-plan mismatch class
# KEY_ESCORT_PASSIVE_EDUCATION exists to close) and
# braunschweig.synthesis.commute_day.home_office_donors_stage. Default True is the PRODUCTION
# default (project rule: new features default on); the CODE default of the map_purpose /
# build_trip_table / trips_stage.run keyword arguments stays False so a direct caller/test that
# omits it keeps today's behaviour.
#
# THE ONE STATEMENT of this flag's two defaults (referenced, never repeated, elsewhere): the
# CODE / DECLARED default is False; the PRODUCTION value true is set in configs/base_bs.yml (added
# by task 7 of issue #372 together with ADR-0112), so a config that does NOT compose that base
# leaves the feature off. The declared default is False, like KEY_ESCORT_PASSIVE_EDUCATION's above and
# unlike KEY_W_ZWECK_10_AS_LEISURE's, because this flag REQUIRES escort_purpose, whose own
# declared default is False: a True declared default would make the declared default SET
# internally inconsistent -- a config that sets nothing would abort inside
# braunschweig.popsim.trips_stage after the full PopulationSim balancing, which is exactly the
# failure issue #373 fix round 1 found and the ENTD_REJECTED_KEYS guard below exists to prevent.
# The same split applies to KEY_PASSIVE_PAIR_MAX_GAP_MINUTES below.
KEY_ESCORT_PASSIVE_FROM_ADULT = "escort_passive_from_adult"
DEFAULT_ESCORT_PASSIVE_FROM_ADULT = False
# Maximum |departure-time gap| in MINUTES between a passive escort leg and the adult leg it is
# paired with; a nearest candidate farther than this leaves the leg UNPAIRED (it then keeps the
# escort_passive_education rule). Unit: minutes. Valid range: > 0. Inert while
# KEY_ESCORT_PASSIVE_FROM_ADULT is off.
#
# The default MUST equal braunschweig.popsim.escort_pairing.DEFAULT_MAX_GAP_MINUTES (the module
# that OWNS the pairing, and where the 94.8 %-within-15-minutes raw-MiD measurement behind the
# value is documented). It is repeated as a literal here because THIS module is a leaf by
# contract (see the module docstring: no imports from this package), and pinned equal by
# tests/test_popsim_trips.py::test_passive_pair_gap_default_agrees_across_its_three_homes.
KEY_PASSIVE_PAIR_MAX_GAP_MINUTES = "escort_passive_pair_max_gap_minutes"
DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES = 15.0

# W_ZWD codeplan no-detail sentinel treatment (issue #242 Task 5, ADR-0113): moves the
# two NO-DETAIL ("keine Angabe") W_ZWD codes -- 799 "Freizeit k.A." (in
# purpose_subtype.LEISURE_GROUPS["leisure_activity"]) and 699 "Erledigung k.A." (in
# purpose_subtype.OTHER_ERRAND_GROUPS["other_errand_long"]) -- out of their group and
# into their spec's sentinel set (purpose_subtype.leisure_spec() /
# other_errand_spec()). NOT a trip-build flag (trips_stage never reads it, so it is
# absent from ENTD_REJECTED_KEYS below); it only governs which purpose_subtype.
# SubtypeSpec two DOWNSTREAM MiD-only consumers estimate from. Declared with this
# exact unprefixed key name and this exact default by BOTH
# braunschweig.popsim.distance_distributions.configure (the leisure_activity /
# other_errand_long DISTANCE-layer donor pool, run() Steps 8/9) and
# braunschweig.synthesis.locations.secondary_chainsolvers.configure (the leisure/
# other subtype deciders' ESTIMATION, re-read via the single-argument execute-context
# form inside deciders.py's _build_leisure_subtype_decider /
# _build_other_subtype_decider) -- all three sites import this constant rather than
# retyping the key string, so they cannot silently resolve different keys or
# defaults. Default True (project rule: new features default on); the production
# value is also set in configs/base_bs.yml (issue #242 Task 7).
KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS = "purpose_subtype_codeplan_sentinels"
DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS = True

# leisure_unspecified_subtype (issue #373, ADR-0115): MiD W_ZWECK 10 legs -- leisure via
# w_zweck_10_as_leisure, never carrying a W_ZWD detail -- form the fifth leisure subtype
# "leisure_unspecified" with its own distance layer instead of being imputed one of the four W_ZWD
# groups. Read by braunschweig.popsim.distance_distributions (the layer) AND
# braunschweig.synthesis.locations.secondary_chainsolvers (the decider); both must resolve the same
# value. Effective only with secondary_leisure_subtype_split on; REQUIRES w_zweck_10_as_leisure
# (both stages raise at configure time otherwise: with the fold off no code-10 leg is leisure, so
# the class would be estimated but never realised). Not a trip-build key -> not in
# ENTD_REJECTED_KEYS (same reasoning as KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS).
KEY_LEISURE_UNSPECIFIED_SUBTYPE = "leisure_unspecified_subtype"
DEFAULT_LEISURE_UNSPECIFIED_SUBTYPE = True

# MiD-only trip-build config keys that braunschweig.popsim.sources.entd.EntdSource.
# build_trips REJECTS on a non-default value, mapped to the SAFE (non-rejected) value
# each must be set to for a popsim_open (ENTD source) run -- ENTD carries none of the
# MiD-specific codings (W_RBW rbW-leg flag, W_SO1 diary start situation, W_ZWECK purpose
# vocabulary) or the MiD Wege table the empirical closure dwell is estimated from.
#
# Defined ONCE so a newly REJECTED keyword cannot silently reopen the bug issue #373 fix
# round 1 found: two popsim_open fixtures (config_popsim_open_braunschweig.yml,
# config_smoke_popsim_open_mini.yml) silently missed the w_zweck_10_as_leisure override
# this key's own addition required (it defaults to True, EntdSource.build_trips rejects
# True), so both configurations aborted inside braunschweig.popsim.trips_stage AFTER the
# full PopulationSim balancing. EntdSource.build_trips' rejection checks read this SAME
# dict for their comparison values (deferred import -- see that module's docstring for
# why config_keys cannot be imported at ITS module level), and
# tests/test_popsim_open_config.py's popsim_open config-parity guard reads it too, so a
# future MiD-only rejection (e.g. issue #373 task 4's two passive-escort keywords) is
# enforced on every popsim_open fixture automatically.
#
# Both passive-escort keys (issue #372 task 4) are listed, not only the boolean one (controller
# ruling C-R7): the gap threshold alone cannot do anything on an ENTD run either -- there is no
# W_ZWECK 13 to pair and no HP_ALTER/W_SZS household diary to pair it against -- so a run that
# deliberately TUNED it would otherwise be silently inert. That is the opposite treatment from
# closure_dwell_min_obs, which is accepted-and-ignored because it only sizes cells of a model the
# closure_dwell_model rejection already forbids building; a tuned gap has no such second guard
# naming it, and the parity guard over this dict is what keeps every popsim_open fixture honest.
ENTD_REJECTED_KEYS: dict[str, object] = {
    KEY_EXCLUDE_RBW_LEGS: False,
    KEY_DROP_LEADING_ARRIVE_HOME_LEG: False,
    KEY_CLOSURE_DWELL_MODEL: "fixed_1h",
    KEY_W_ZWECK_10_AS_LEISURE: False,
    KEY_ESCORT_PASSIVE_FROM_ADULT: False,
    KEY_PASSIVE_PAIR_MAX_GAP_MINUTES: DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
}


# Config toggle per KREIS attribute control (kreis_attribute_control.REGISTRY entry).
# economic_status keeps its historical key; the S1c additions get their own keys.
KEY_PT_TICKET_KREIS_CONTROL = "braunschweig.population.popsim.pt_ticket_kreis_control"
# never_pt group split (issue #329): when "on" (default; project rule), the
# four-group entry pt_ticket_group4 REPLACES pt_ticket_group in the rendered
# KREIS controls (same marginal, finer resolution -- never both). Requires
# pt_ticket_kreis_control "on"; "on" with the base control "off" is a config
# error (fail-fast). "off" restores the exact three-group behaviour.
KEY_PT_TICKET_NEVER_GROUP = "braunschweig.population.popsim.pt_ticket_never_group"

_KREIS_CONTROL_TOGGLE_KEY = {
    "economic_status": KEY_STATUS_KREIS_CONTROL,
    "number_of_cars": KEY_CARS_KREIS_CONTROL,
    "number_of_bicycles": KEY_BIKES_KREIS_CONTROL,
    "has_ebike": KEY_EBIKE_KREIS_CONTROL,
    "trip_class": KEY_TRIPS_KREIS_CONTROL,
    "employment_status": KEY_EMPLOYMENT_STATUS_KREIS_CONTROL,
    "pt_ticket_group": KEY_PT_TICKET_KREIS_CONTROL,
    "pt_ticket_group4": KEY_PT_TICKET_NEVER_GROUP,
    "work_participation": KEY_WORK_PARTICIPATION_CONTROL,
    "leisure_participation": KEY_LEISURE_PARTICIPATION_CONTROL,
    "education_participation": KEY_EDUCATION_PARTICIPATION_CONTROL,
    "escort_participation": KEY_ESCORT_PARTICIPATION_CONTROL,
    "work_by_employment": KEY_WORK_BY_EMPLOYMENT_CONTROL,
    # The three education-by-age entries share ONE toggle key (they are switched together).
    "education_0_5": KEY_EDUCATION_BY_AGE_CONTROL,
    "education_6_17": KEY_EDUCATION_BY_AGE_CONTROL,
    "education_18plus": KEY_EDUCATION_BY_AGE_CONTROL,
}

# Shared default for the three education_by_age entries (Plan B, issue #368, ADR-0109):
# declared ONCE because they share a SINGLE config toggle (KEY_EDUCATION_BY_AGE_CONTROL,
# see _KREIS_CONTROL_TOGGLE_KEY above) -- a real run only ever resolves ONE value for all
# three, so their _KREIS_CONTROL_DEFAULT entries must be textually identical by
# construction, not merely equal by coincidence. Without this, `configure()` reads
# _KREIS_CONTROL_DEFAULT["education_6_17"] while a test double resolving the shared
# toggle key by iterating this dict (or _KREIS_CONTROL_TOGGLE_KEY) could silently pick up
# a DIFFERENT one of the three names first and diverge from configure() the moment a
# future edit changes just one of the three literals.
_EDUCATION_BY_AGE_DEFAULT = "on"

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
    "pt_ticket_group": "on",
    "pt_ticket_group4": "on",
    # work_participation / education_participation flip to "off" (Plan B, issue #368,
    # ADR-0109): work_by_employment / education_by_age REPLACE them by default. The
    # OFF-path tests (tests/test_participation_universe_controls.py) pin that turning the
    # new controls off and these two back on reproduces exactly today's legacy active set.
    "work_participation": "off",
    "leisure_participation": "on",
    "education_participation": "off",
    "escort_participation": "on",
    "work_by_employment": "on",
    "education_0_5": _EDUCATION_BY_AGE_DEFAULT,
    "education_6_17": _EDUCATION_BY_AGE_DEFAULT,
    "education_18plus": _EDUCATION_BY_AGE_DEFAULT,
}
