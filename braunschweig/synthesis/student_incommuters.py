"""Cross-cordon student in-commuter synthesis stage (#140 sub-item 2).

Default-ON per project convention, but dependency-gated: it needs the resident
university placement, which lives in the ``education_gravity`` feature. When that
parent feature is OFF and the flag is left at its default, the stage SKIPS
(empty frames + one warn) rather than raising -- a legitimate config state, not a
silent fallback. Explicitly enabling the flag while the parent is OFF is a
contradiction and raises. See docs/superpowers/specs/2026-07-18-student-incommuters-design.md.

Structurally parallel to ``braunschweig.synthesis.incommuters`` (the SvB cross-
cordon in-commuter stage), but Home->Education->Home instead of Home->Work->Home:

  - counts:  ``braunschweig.data.education.student_incommuter_counts`` (Task 2) --
             per-university-commune enrollment not filled by resident placement.
  - origins: ``braunschweig.data.education.student_origins`` (Task 3) -- reverse
             distance-decay draw of an origin Kreis per student in-commuter.
  - home:    ``braunschweig.data.cordon.incommuter_origins.incommuter_origin_homes``
             (shared with the SvB stage) for in-ring agents; agents whose origin
             Kreis has no in-ring Gemeinde fall back to the nearest cordon gate
             (never left NaN; see :func:`_nearest_gate_xy`).
  - core frames: the shared ``assemble_incommuter_core_frames`` helper (Task 1),
             called with ``middle_purpose="education"`` -- DRY with the SvB stage.
  - donors:  every student draws its MiD donor household like the SvB in-commuter
             (``braunschweig.data.cordon.donor_draw.KreisDonorSampler``, ADR-0191): from the
             adult weekday student donors with a similar education distance, tilted to the
             income level of the origin Kreis (INKAR); see :func:`_inject`.
  - persons/households: the donor household's income, size and consumption units, built
             like the SvB ``_build_persons``/``_build_households`` but without the per-agent
             German fleet draw; see :func:`_build_student_persons` /
             :func:`_build_student_households`.
  - vehicles: reuses the SvB stage's legacy (non-German-fleet) vehicle builders
             ``_build_legacy_vehicles`` / ``_build_incommuter_passenger_vehicles``
             -- one ``car`` vehicle per car-mode agent plus a ``car_passenger``
             vehicle for every agent (2026-07-18 Task 5 review fix: without this
             every student in-commuter was unroutable, see
             ``braunschweig.matsim.scenario.vehicles``).

Student flows (issue #140, Phase 4, design B6; switch ``braunschweig.students.flows_enabled``, default on). The
list above is the OFF path. ON, the counts and origins come from the outside rows of the origin-destination table T:
``f_eff`` x sampling rate x T per origin Kreis and site, rounded with every margin within one person
(``student_incommuter_counts.balance_incommuter_counts``; ``f_eff`` from :func:`effective_day_factor_of_run`, with
the away shares the run's day absence applies). Every agent carries its site (``destination_id``) and an education
building of that site drawn by capacity. The home rule is unchanged, its branches counted per origin Kreis. The donor
window and the mode band use the commute length T implies (``student_incommuter_counts.commute_length_km``), not the
home point's; the mode comes from the students' own table (``student_mode_reference``). No day-absence draw: the
count already carries ``f_eff``. The residents' placement (``synthesis.population.spatial.primary.locations``) is
declared and read on the OFF path only.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig import synpp_tokens

_log = logging.getLogger(__name__)
#: ADR-0196 decision 1: the pt router's work files (``incommuters._pt_check`` writes them under this stage's cache
#: directory) have a thread-timing row order, so they are left out of the stage output hash.
_OUTPUT_HASH_EXCLUDE = ("incommuter_pt_router/router_*.csv",)
_SENTINEL = object()
CRS_METRIC = "EPSG:25832"
# Fixed RNG offset so student in-commuters never share a substream with residents
# (100000) or SvB in-commuters. Distinct offset keeps draws reproducible+disjoint.
_RNG_OFFSET = 200000

# ASSUMPTION (a warn bound, not a reference value): share of student donors excluded by the age bound
# above which ``_adult_student_donors`` warns. The survey's study flag covers school children, who are
# excluded by design (ADR-0191 decision 7), so a large exclusion is the expected state.
STUDENT_DONOR_AGE_EXCLUDED_WARN_SHARE = 0.9

# Person/household id block reserved above the resident id range for student
# in-commuters, additionally offset above the (unrelated) block the SvB
# in-commuter stage uses starting at the same n_residents/n_resident_households
# base. 10 million agents is far beyond any plausible SvB in-commuter count in
# this regional model, so this fixed offset keeps the two in-commuter id ranges
# disjoint WITHOUT a hard stage dependency on ``braunschweig.synthesis.incommuters``
# (which would drag in the full SvB gate/gravity pipeline just to read a count).
# ASSUMPTION (documented, not measured): re-verify against the actual SvB
# in-commuter count at merge time (Task 5); raise the offset if it is ever
# exceeded.
_ID_OFFSET_ABOVE_RESIDENTS = 10_000_000

# Student in-commuter person attribute defaults, mirroring the eqasim/INSEE
# activity-status mapping used for residents
# (braunschweig.population.socioprofessional_class.derive_socioprofessional_class): studying takes
# precedence over employment, so SPC_STUDENT (=8) applies regardless of the HTS
# donor's own employment status. Household income, size and consumption units are NOT defaults: they
# come from the donor household (``_build_student_persons``, ADR-0191).
#: Smallest effective donor pool (Kish size) of the education-distance window a student in-commuter draws its
#: MiD donor from (ADR-0191); > 0. The window around the agent's education distance grows until it is reached.
KEY_DONOR_MIN_EFFECTIVE_POOL = "braunschweig.student_incommuters.donor_min_effective_pool"
DEFAULT_DONOR_MIN_EFFECTIVE_POOL = 300.0   # ASSUMPTION: owner decision 2026-11-11, not calibrated

#: The donor column the education-distance window is matched on (km, the home-education leg of the MiD donor).
DONOR_DISTANCE_COLUMN = "education_distance_km"

# ------------------------------------------------------------------ the student flows (ON path, design B6)
#: The table T relative to ``data_path``. Key and default are those of
#: ``braunschweig.data.schools.university_facilities`` (KEY_OD_TABLE_PATH), repeated because of the import boundary
#: of Phase 4 and pinned by ``tests/test_student_incommuters_balance.py``.
KEY_OD_TABLE_PATH = "braunschweig.students.od_table_path"
DEFAULT_OD_TABLE_PATH = "braunschweig/schools/student_od_table.csv"
#: The level table (students per home Kreis and age range, Phase 3 A2) relative to ``data_path``: the weights of the
#: effective day factor (design 5a).
KEY_LEVEL_TABLE_PATH = "braunschweig.students.level_table_path"
DEFAULT_LEVEL_TABLE_PATH = "braunschweig/popsim/student_level_table.csv"
#: Subdirectory of ``data_path`` holding the committed SrV student tables (the shares of the day factor).
SRV_SUBDIR = ("braunschweig", "srv")
#: The switches of the day-absence stage (owner ``braunschweig.synthesis.day_absence.absence_stage``: KEY_ENABLED,
#: KEY_STUDENT_CELLS and their defaults), repeated because of the import boundary and pinned by the same test. They
#: decide the away share of the effective day factor (``absence_mode``); the in-commuters are never drawn by the step.
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
DEFAULT_DAY_ABSENCE_ENABLED = True
KEY_DAY_ABSENCE_STUDENT_CELLS = "day_absence_student_cells_enabled"
DEFAULT_DAY_ABSENCE_STUDENT_CELLS = True
#: Smallest number of students with a car or pt education leg per band of the students' mode table; a thinner band
#: is pooled with its neighbour (``student_mode_reference.pool_thin_bands``).
KEY_MODE_BAND_MIN_PERSONS = "braunschweig.student_incommuters.mode_band_min_persons"
DEFAULT_MODE_BAND_MIN_PERSONS = 30   # ASSUMPTION: not from a table; the MiD table states counts, no minimum
#: Share of the in-commuters whose commute length comes from the cell of T (no commune of the origin Kreis with a
#: positive weight) above which a WARNING is logged.
KEY_COMMUTE_FALLBACK_WARN_SHARE = "braunschweig.student_incommuters.commute_length_fallback_warn_share"
DEFAULT_COMMUTE_FALLBACK_WARN_SHARE = 0.05   # ASSUMPTION: T names only Kreise with a commune in reach, so ~0 expected
_FLOWS_TAG = "[student_incommuters:flows]"


def check_flow_settings(day_absence_enabled, day_absence_student_cells_enabled) -> None:
    """Refuse the flows with the day absence ON and its student cells OFF (design 5a item 2, B7).

    The in-commuters count with the effective day factor of the residents; with the cells off the residents would be
    thinned at the rate of their age band, not at the away share of their home area, and the two would not match.
    """
    from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED

    if day_absence_enabled and not day_absence_student_cells_enabled:
        raise ValueError(f"{KEY_FLOWS_ENABLED} is true with {KEY_DAY_ABSENCE_ENABLED} true and "
                         f"{KEY_DAY_ABSENCE_STUDENT_CELLS} false: the student in-commuters count with the away share "
                         f"of the residents' home areas, which the day absence applies only with its student cells. "
                         f"Switch the cells on, the day absence off, or the flows off.")


def _validated_min_effective_pool(value) -> float:
    """``KEY_DONOR_MIN_EFFECTIVE_POOL`` as a float: a finite number > 0, else ValueError naming this key."""
    from braunschweig.synthesis import incommuters

    try:
        return incommuters.validate_donor_min_effective_pool(value)
    except ValueError as exc:
        raise ValueError(f"{KEY_DONOR_MIN_EFFECTIVE_POOL} must be a finite number > 0, got {value!r}") from exc


_STUDENT_PERSON_DEFAULTS = dict(
    employed=False, studies=True,
    socioprofessional_class=8,  # SPC_STUDENT, braunschweig.population.socioprofessional_class
    number_of_bicycles=0,
    bicycle_availability="all", license_type="ja", has_license=True,
    has_pt_subscription=False, pt_subscription_type="never_pt",
    is_bs_resident=False, is_urban_resident=False, age_range="higher_education",
    # See braunschweig.synthesis.incommuters._INCOMMUTER_PERSON_DEFAULTS for why
    # this completeness attribute (synthesise_housing_tenure,
    # braunschweig.synthesis.population.enriched) is not applicable to in-commuters.
    housing_tenure="unknown",
    subpopulation="student_incommuter",
)


def _empty_frames(crs=CRS_METRIC):
    import geopandas as gpd

    from braunschweig.data.cordon import donor_draw, plans  # noqa: PLC0415 (function-level like _inject's import)
    return {
        # The zero day-bound report, so frames["day_bound"] is always present (ADR-0172).
        "day_bound": plans.empty_day_bound(),
        "persons": pd.DataFrame(),
        "households": pd.DataFrame(),
        "trips": pd.DataFrame(),
        "activities": pd.DataFrame(),
        "locations": gpd.GeoDataFrame(geometry=[], crs=crs),
        # Empty vehicles/vehicle_types (2026-07-18 Task 5 review fix), matching
        # the column schema braunschweig.synthesis.incommuters._empty_frames uses,
        # so the OFF/skip path stays a no-op for both consumers
        # (braunschweig.matsim.scenario.vehicles and .population).
        "vehicles": pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"]),
        "vehicle_types": pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                               "hbefa_cat", "hbefa_tech", "hbefa_size",
                                               "hbefa_emission"]),
        # The report of a donor draw without agents, so frames["donor_draw"] is always present (ADR-0191).
        "donor_draw": donor_draw.empty_report(),
    }


#: Every helper whose source decides a value of the student in-commuter frames. This stage
#: reaches all of them through FUNCTION-LEVEL imports, so they are hashed by dotted NAME --
#: synpp hashes only THIS module's source, and none of these is visible in it.
#:
#: The first two are the shared assembly and time-repair helpers. The eight added by the #327
#: gate re-run are the ones that decide WHICH students exist and WHERE they come from:
#: ``student_incommuter_counts`` and ``student_origins`` are the counts and the origin
#: distribution itself, ``cordon.demand`` expands them into agents, ``incommuter_origins``
#: places the origins, ``mode_reference`` owns the distance bands and the mode restriction,
#: ``mikrozensus.reference`` maps an origin ARS to the Bundesland whose mode reference applies,
#: ``external_workplaces`` supplies the Gemeinden geometry, and ``constants`` carries
#: ``ROUTED_DETOUR_FACTOR``, with which the straight-line distances are converted.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.synthesis.incommuters",
    # Reached through incommuters._high_income (ADR-0191).
    "braunschweig.popsim.income",
    # The weighted, distance-matched and Kreis-tilted donor draw of the student in-commuters (ADR-0191).
    "braunschweig.data.cordon.donor_draw",
    "braunschweig.data.cordon.plans",
    "braunschweig.constants",
    "braunschweig.data.cordon.demand",
    "braunschweig.data.cordon.incommuter_origins",
    "braunschweig.data.cordon.mode_reference",
    "braunschweig.data.education.student_incommuter_counts",
    "braunschweig.data.education.student_origins",
    "braunschweig.data.external_workplaces",
    "braunschweig.data.mikrozensus.reference",
    # The student flows (ON path, design B6): the committed tables and the day factor, the students' mode table, the
    # weight curve of T and the unbiased rounding with the building draw.
    "braunschweig.data.education.student_flows_inputs",
    "braunschweig.data.education.student_mode_reference",
    "braunschweig.data.education.student_geometry",
    "braunschweig.synthesis.locations.university_assignment",
    "braunschweig.data.csv_header",
    # Reached through student_geometry's VG250 reader (not called by this stage; listed for the closure audit).
    "braunschweig.analysis.spatial",
    # The rest of this stage's import closure: modules its helpers import, whose code this
    # stage runs without importing it itself (tests/test_audit_synpp_helper_hash.py, ADR-0136).
    "braunschweig.data.census.population",
    "braunschweig.data.cordon.external_points",
    "braunschweig.data.cordon.gate_assignment",
    "braunschweig.data.cordon.mode_balancer",
    "braunschweig.data.cordon.network",
    "braunschweig.data.cordon.pt_reachability",
    "braunschweig.data.kba.feasible_fuels",
    "braunschweig.data.kba.fleet_tables",
    "braunschweig.data.kba.hsn_tsn",
    "braunschweig.ipf.joint_age_size",
    "braunschweig.synthesis.locations.education_gravity_model",
    "braunschweig.synthesis.vehicles.age_income",
    "braunschweig.synthesis.vehicles.fleet_sampling_de",
    "braunschweig.synthesis.vehicles.fleet_validation",
    "braunschweig.synthesis.vehicles.hbefa",
    "braunschweig.synthesis.vehicles.segment",
    "braunschweig.synthesis.vehicles.wohnmobile_age",
    # The timetable check of the pt days (#488 for the commuters, run here too since #528 through
    # incommuters._pt_check / incommuter_pt_connections.check_pt_days): the batch pt router and its connection rule,
    # the schedule reader, the walk limit and the end of the simulated day, plus the rest of the shared module's
    # import closure (ADR-0136).
    "braunschweig.data.transit.connections",
    "braunschweig.data.transit.router_cache",
    "braunschweig.data.transit.schedule",
    "braunschweig.matsim.scenario.day_end_margin",
    "braunschweig.matsim.simulation.plan_check",
    "braunschweig.popsim.closure_dwell",
    "braunschweig.popsim.plan_validation",
    "braunschweig.population.methods",
    "braunschweig.resources",
    "braunschweig.synthesis.incommuter_pt_connections",
    "data.hts.hts",
    "matsim.runtime.eqasim",
    "matsim.runtime.git",
    "matsim.runtime.java",
    "matsim.runtime.maven",
    "matsim.runtime.process_watchdog",
    # Declared stages this stage runs as libraries, and their imports (ADR-0194): the DAG edge covers their
    # output, not this code. ``execute`` imports ``braunschweig.data.hts.mid_donor`` (its key constants); the
    # modules after it are the import closure the audit reports behind it and behind ``regiostar``.
    "braunschweig.data.hts.mid_donor",
    "braunschweig.data.bbsr.regiostar",
    "braunschweig.calibration.reported_time_precision",
    "braunschweig.calibration.srv_departure_times",
    "braunschweig.calibration.srv_distance_targets",
    "braunschweig.calibration.srv_plan_structure",
    "braunschweig.data.mid.income_by_size",
    "braunschweig.data.mid.income_by_status",
    "braunschweig.data.mid.raumtyp_tilt",
    "braunschweig.data.mid.reference_tables",
    "braunschweig.data.mid.status_by_hhtype",
    "braunschweig.gravity.friction",
    "braunschweig.popsim.assembly",
    "braunschweig.popsim.attributes",
    "braunschweig.popsim.chain_matching",
    "braunschweig.popsim.day_type",
    "braunschweig.popsim.departure_time_model",
    "braunschweig.popsim.diary_facts",
    "braunschweig.popsim.escort_pairing",
    "braunschweig.popsim.expand",
    "braunschweig.popsim.income_kreis_control",
    "braunschweig.popsim.member_completion",
    "braunschweig.popsim.mid.csv_format",
    "braunschweig.popsim.mid.donor",
    "braunschweig.popsim.missing",
    "braunschweig.popsim.passenger_availability",
    "braunschweig.popsim.placement_income",
    "braunschweig.popsim.sampling",
    "braunschweig.popsim.seed",
    "braunschweig.popsim.sources",
    "braunschweig.popsim.sources.base",
    "braunschweig.popsim.sources.mid",
    "braunschweig.popsim.stage.config_keys",
    "braunschweig.popsim.stratum",
    "braunschweig.popsim.time_imputation",
    "braunschweig.popsim.trips",
    "braunschweig.popsim.trips_stage",
    "braunschweig.popsim.unused_mid_wege_columns",
    "braunschweig.popsim.weekend_plan_match",
    "braunschweig.population.schema",
    "braunschweig.population.socioprofessional_class",
    "data.hts.entd.cleaned",
    "synthesis.population.matched",
)


def validate(context):
    """Include the shared assembly and time-repair helpers and the VG250 archive in the cache token.

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it), plus
    ``files``: the content digest of the VG250-EW archive that ``_inject`` loads through
    ``external_workplaces._load_gemeinden``, keyed by its dataset id ``vg250_ew``.
    It is taken whenever ``cordon_enabled`` is on, not only when ``_active`` lets ``_inject`` run:
    ``_active`` warns and can raise, which validate() must not repeat, and a cordon run with the
    injection switched off caches an empty frame, so the wider condition costs nothing. With the
    cordon off the archive is not touched (it may be absent) and the digest is ``None``.

    With the student flows on (and the cordon on) ``files`` also holds the CRLF-neutral digests of the committed
    tables the ON path reads (T, the level table, the two SrV tables of the day factor, the students' mode table), so
    a rebuilt table devalidates the stage; a missing table stops the run at DAG time.
    """
    from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED

    token = synpp_tokens.source_token(
        (), _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")
    token["files"] = {"vg250_ew": (synpp_tokens.input_file_sha256(
        os.path.join(context.config("data_path"), context.config("germany.population_path")),
        "VG250 archive") if context.config("cordon_enabled") else None)}
    if context.config("cordon_enabled") and context.config(KEY_FLOWS_ENABLED):
        for name, path in _flows_table_paths(context).items():
            if not os.path.isfile(path):
                raise RuntimeError(f"{_FLOWS_TAG} {name} not found: {path}")
            token["files"][name] = synpp_tokens.text_file_sha256(path)
    return token


def _flows_table_paths(context) -> dict:
    """``{name: absolute path}`` of the committed tables the ON path reads."""
    from braunschweig.data.education import student_flows_inputs as flows_inputs
    from braunschweig.data.education.student_mode_reference import TABLE_RELATIVE_PATH

    data_path = str(context.config("data_path"))
    srv_dir = os.path.join(data_path, *SRV_SUBDIR)
    return {KEY_OD_TABLE_PATH: os.path.join(data_path, context.config(KEY_OD_TABLE_PATH)),
            KEY_LEVEL_TABLE_PATH: os.path.join(data_path, context.config(KEY_LEVEL_TABLE_PATH)),
            flows_inputs.PARAMETERS_TABLE: os.path.join(srv_dir, flows_inputs.PARAMETERS_TABLE),
            flows_inputs.PARAMETERS_BY_AREA_TABLE: os.path.join(srv_dir, flows_inputs.PARAMETERS_BY_AREA_TABLE),
            "student_mode_table": os.path.join(data_path, TABLE_RELATIVE_PATH)}


def configure(context):
    context.config("cordon_enabled")
    context.config("braunschweig.political_prefix")
    context.config("cordon_student_incommuters_enabled", None)
    context.config("education_gravity_enabled", False)
    context.config("student_incommuter_age_band", [18, 29])
    context.config("education_university_slope", -0.1415)
    context.config("education_university_max_radius_km", 150.0)
    context.config("sampling_rate")
    context.config("random_seed")
    context.config("cordon_network_source_buffer_m")
    context.config("cordon_gate_speed_kmh", 30.0)
    context.config("cordon_incommuter_time_chronology", True)
    # Day bound of the education days (#467): the same donor re-draw key and the same day-end margin keys as the
    # commuter in-commuter stage, so both stages share one effective bound (incommuters._time_bound_s).
    from braunschweig.matsim.scenario import day_end_margin
    from braunschweig.synthesis import incommuters
    incommuters.validate_time_redraw_max_attempts(
        context.config(incommuters.KEY_TIME_REDRAW_MAX_ATTEMPTS, incommuters.DEFAULT_TIME_REDRAW_MAX_ATTEMPTS))
    context.config(day_end_margin.KEY_ENABLED, day_end_margin.DEFAULT_ENABLED)
    context.config(day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S, day_end_margin.DEFAULT_LAST_ARRIVAL_MARGIN_S)
    # Timetable check of the pt days (#528): the same switch, keys and batch pt router as the commuter stage (#488).
    incommuters.configure_pt_check(context)
    context.config("data_path")
    _validated_min_effective_pool(context.config(KEY_DONOR_MIN_EFFECTIVE_POOL, DEFAULT_DONOR_MIN_EFFECTIVE_POOL))
    # Mirror the keys declared by braunschweig.data.external_workplaces.configure()
    # (as braunschweig.synthesis.incommuters.configure() also does) so that
    # _inject()'s external_workplaces._load_gemeinden(context) can resolve the
    # VG250-EW archive path. synpp's ExecuteContext.config() raises for a key that
    # was not declared here, so an undeclared read would crash a real cordon run.
    context.config(
        "germany.population_path",
        "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    context.config(
        "germany.population_source",
        "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg")
    from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED

    flows_enabled = context.config(KEY_FLOWS_ENABLED, True)
    context.stage("braunschweig.data.schools.university_facilities")
    if flows_enabled:
        _configure_flows(context)
    else:
        # The residual count of the OFF path needs the residents' placement; the ON path counts from T and does not.
        context.stage("synthesis.population.spatial.primary.locations")
    context.stage("data.spatial.municipalities")
    # German MiD donor (not ENTD): student trip TIMING comes from German
    # behaviour. See braunschweig.data.hts.mid_donor + the design spec.
    context.stage("braunschweig.data.hts.mid_donor", alias="hts")
    context.stage("braunschweig.synthesis.cordon_gates")
    context.stage("synthesis.population.enriched")
    context.stage("braunschweig.data.inkar.household_income")   # origin-Kreis income level (ADR-0191)


def _active(context):
    """Resolve the tri-state activation. Returns True/False; raises on the
    contradictory explicit-on-but-parent-off case."""
    if not context.config("cordon_enabled"):
        return False
    flag = context.config("cordon_student_incommuters_enabled")
    parent = bool(context.config("education_gravity_enabled"))
    if flag is True and not parent:
        raise RuntimeError(
            "cordon_student_incommuters_enabled=True requires "
            "education_gravity_enabled=True (the count anchor needs the resident "
            "university placement). Enable education_gravity or unset the flag.")
    if flag is False:
        return False
    if not parent:
        _log.warning(
            "[student_incommuters] skipped: requires education_gravity_enabled "
            "(parent feature off, flag left at default). Injecting no students.")
        return False
    return True


def _configure_flows(context):
    """The keys of the student flows (ON path, design B6), declared ONLY with the switch on; refusals raise here."""
    from braunschweig.data.education import student_flows_inputs as flows_inputs

    context.config(KEY_OD_TABLE_PATH, DEFAULT_OD_TABLE_PATH)
    context.config(KEY_LEVEL_TABLE_PATH, DEFAULT_LEVEL_TABLE_PATH)
    context.config(flows_inputs.KEY_HOME_AREA_KREISE, flows_inputs.default_home_area_kreise())
    min_persons = context.config(KEY_MODE_BAND_MIN_PERSONS, DEFAULT_MODE_BAND_MIN_PERSONS)
    if isinstance(min_persons, bool) or not isinstance(min_persons, int) or min_persons < 0:
        raise ValueError(f"{KEY_MODE_BAND_MIN_PERSONS} must be a whole number >= 0, got {min_persons!r}")
    warn_share = context.config(KEY_COMMUTE_FALLBACK_WARN_SHARE, DEFAULT_COMMUTE_FALLBACK_WARN_SHARE)
    if isinstance(warn_share, bool) or not isinstance(warn_share, (int, float)) or not 0.0 <= warn_share <= 1.0:
        raise ValueError(f"{KEY_COMMUTE_FALLBACK_WARN_SHARE} must be a share in [0, 1], got {warn_share!r}")
    day_absence_enabled = context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED)
    cells_enabled = None
    if day_absence_enabled:
        cells_enabled = context.config(KEY_DAY_ABSENCE_STUDENT_CELLS, DEFAULT_DAY_ABSENCE_STUDENT_CELLS)
    check_flow_settings(day_absence_enabled, cells_enabled)


def effective_day_factor_of_run(context) -> tuple[float, str]:
    """``(f_eff, absence_mode)``: the effective day factor with the away shares the run applies (design 5a).

    ``absence_mode`` is ``"cells"`` with the day absence and its student cells on (the away share of each home area,
    ``student_flows_inputs.away_share_by_area``) and ``"off"`` with the day absence off (away share 0, ``f = e``);
    the third combination is refused at configure time (:func:`check_flow_settings`). ``f(k) = e x (1 - away(area of
    k))`` per Kreis of the region (``day_factor_by_kreis``), ``f_eff`` its mean weighted by the students of each Kreis
    in the level table (both age ranges, ``effective_day_factor``): the same factor the residents' balance uses.
    """
    from braunschweig.data.education import student_flows_inputs as flows_inputs

    data_path = str(context.config("data_path"))
    srv_dir = os.path.join(data_path, *SRV_SUBDIR)
    absence_mode = flows_inputs.ABSENCE_MODE_OFF
    if context.config(KEY_DAY_ABSENCE_ENABLED):
        check_flow_settings(True, context.config(KEY_DAY_ABSENCE_STUDENT_CELLS))
        absence_mode = flows_inputs.ABSENCE_MODE_CELLS
    e = flows_inputs.education_leg_share(srv_dir)
    away = flows_inputs.away_share_by_area(srv_dir) if absence_mode == flows_inputs.ABSENCE_MODE_CELLS else {}
    area_of_kreis = flows_inputs.area_by_kreis(context.config(flows_inputs.KEY_HOME_AREA_KREISE),
                                               flows_inputs.ZGB_KREISE)
    level = flows_inputs.load_level(os.path.join(data_path, context.config(KEY_LEVEL_TABLE_PATH)),
                                    flows_inputs.ZGB_KREISE)
    level_by_kreis = level.groupby("code")["students_persons"].sum()
    f_by_kreis = flows_inputs.day_factor_by_kreis(e, away, area_of_kreis, absence_mode)
    f_eff = flows_inputs.effective_day_factor(level_by_kreis, f_by_kreis)
    _log.info("%s effective day factor %.6f (absence mode %s, education-leg share e %.6f, away shares %s, "
              "f per Kreis %s)", _FLOWS_TAG, f_eff, absence_mode, e, away or "none (0)",
              {kreis: round(value, 6) for kreis, value in f_by_kreis.items()})
    return f_eff, absence_mode


def _home_branch_by_origin(orig_ars5, is_in_ring) -> pd.DataFrame:
    """``[orig_ars5, in_ring, gate, n]``: the home rule's branches per origin Kreis (sorted by Kreis)."""
    frame = pd.DataFrame({"orig_ars5": np.asarray(orig_ars5, dtype=object),
                          "in_ring": np.asarray(is_in_ring, dtype=bool)})
    table = frame.groupby("orig_ars5", sort=True)["in_ring"].agg(in_ring="sum", n="size").reset_index()
    table["in_ring"] = table["in_ring"].astype("int64")
    table["gate"] = table["n"] - table["in_ring"]
    return table[["orig_ars5", "in_ring", "gate", "n"]]


def execute(context):
    if not _active(context):
        return _empty_frames()
    return _inject(context)


def _nearest_gate_xy(orig_ars5, kreis_xy, gates_gdf):
    """Nearest major cordon-gate point (Euclidean, ``CRS_METRIC``) per agent's
    origin-Kreis centroid.

    Used as the home-point fallback for student in-commuters whose origin Kreis
    has no in-ring Gemeinde (see ``incommuter_origin_homes``): this mirrors the
    SvB stage's gate-anchored home for far agents, but picks the geometrically
    NEAREST gate rather than the SvB gravity-weighted draw
    (``sample_gate_per_agent``) -- the gravity gate-assignment table only covers
    Kreise with SvB commuter volume, which does not necessarily cover every
    student-origin Kreis, so a geometric nearest-gate lookup is the more general
    (and always-defined) fallback.

    Args:
        orig_ars5: per-agent 5-digit origin Kreis ARS (only the far agents).
        kreis_xy: DataFrame ``[ars5, x, y]`` -- candidate origin-Kreis centroids
            (see ``_inject`` step 3); must contain every ars5 in ``orig_ars5``.
        gates_gdf: GeoDataFrame of major cordon gates (point geometry), from the
            ``braunschweig.synthesis.cordon_gates`` stage.

    Returns:
        ``(x, y)`` float ndarrays, one entry per input agent.

    Raises:
        RuntimeError: if ``gates_gdf`` is empty (the gate stage did not run with
            ``cordon_enabled=True``, which should never happen once ``_active``
            returned True).
    """
    if gates_gdf is None or len(gates_gdf) == 0:
        raise RuntimeError(
            "student_incommuters: gate fallback needed for far agents but "
            "braunschweig.synthesis.cordon_gates returned no gates -- check that "
            "cordon_enabled is True for both stages.")
    gate_x = gates_gdf.geometry.x.to_numpy(dtype=float)
    gate_y = gates_gdf.geometry.y.to_numpy(dtype=float)
    kreis_pos = dict(zip(kreis_xy["ars5"].astype(str),
                         zip(kreis_xy["x"].astype(float), kreis_xy["y"].astype(float))))
    xs = np.empty(len(orig_ars5), dtype=float)
    ys = np.empty(len(orig_ars5), dtype=float)
    for i, ars in enumerate(orig_ars5):
        kx, ky = kreis_pos[str(ars)]
        d2 = (gate_x - kx) ** 2 + (gate_y - ky) ** 2
        j = int(np.argmin(d2))
        xs[i] = gate_x[j]
        ys[i] = gate_y[j]
    return xs, ys


def _donor_education_times(donors, hts_trips, person_col):
    """Per-agent Home->education->Home timings (seconds since midnight) from HTS
    donors, memoised per UNIQUE donor id.

    Donors are sampled WITH REPLACEMENT (student donors are far fewer than
    agents), so the same HTS person backs many agents; this mirrors
    ``braunschweig.synthesis.incommuters._agent_times``'s memoisation -- the
    (filter + sort) inside ``extract_activity_times`` runs once per DISTINCT
    donor id, not once per agent.

    Returns ``(depart_home, arrive_mid, depart_mid, arrive_home)`` float ndarrays,
    one entry per agent (donors row order).
    """
    from braunschweig.data.cordon.plans import extract_activity_times

    donor_ids = donors[person_col].to_numpy()
    unique_ids = pd.unique(donor_ids)
    used = hts_trips[hts_trips[person_col].isin(unique_ids)]
    by_person = {pid: sub for pid, sub in used.groupby(person_col)}
    times_by_id = {
        pid: extract_activity_times(by_person[pid], purpose="education")
        for pid in unique_ids
    }
    depart_home = np.array([times_by_id[pid][0] for pid in donor_ids], dtype=float)
    arrive_mid = np.array([times_by_id[pid][1] for pid in donor_ids], dtype=float)
    depart_mid = np.array([times_by_id[pid][2] for pid in donor_ids], dtype=float)
    arrive_home = np.array([times_by_id[pid][3] for pid in donor_ids], dtype=float)
    return depart_home, arrive_mid, depart_mid, arrive_home


def _build_student_persons(ids, donors, modes):
    """Persons frame for injected student in-commuters, mirroring the resident
    attribute schema used by ``braunschweig.synthesis.incommuters._build_persons``
    (age/sex/hts linkage columns, car_availability, income columns) but with the
    student-specific defaults from :data:`_STUDENT_PERSON_DEFAULTS` instead of the
    SvB employed-commuter defaults.

    ``donors`` is agent-aligned and eligible (:func:`_inject`). Household income (EUR/month, rounded to
    whole EUR), household size and consumption units are the donor household's, as for the worker
    in-commuters (ADR-0191); ``high_income`` follows the resident rule. There is no per-agent
    German-fleet draw: ``car_availability``/``number_of_cars`` follow the fixed commute mode (car-mode
    agents own a car; PT-mode agents do not -- a documented simplification, not a measured student
    car-ownership rate).
    """
    from braunschweig.popsim.income import add_income_per_consumption_unit

    person_ids = ids["person_id"].to_numpy()
    household_ids = ids["household_id"].to_numpy()
    modes = np.asarray(modes)
    age = donors["age"].to_numpy()   # a required donor column, checked by _inject
    sex = donors["sex"].to_numpy() if "sex" in donors.columns else "male"
    hts_id = donors["person_id"].to_numpy() if "person_id" in donors.columns else -1
    income_eur = donors["household_income_eur"].to_numpy(dtype=float).round(0)
    is_car = modes == "car"
    persons = pd.DataFrame({
        "person_id": person_ids,
        "household_id": household_ids,
        "census_person_id": person_ids,
        "census_household_id": household_ids,
        "hts_id": hts_id, "hts_household_id": -1,
        "age": age, "sex": sex,
        "car_availability": np.where(is_car, "all", "none"),
        "number_of_cars": np.where(is_car, 1, 0),
        "household_income_eur": income_eur,
        "household_income": [str(int(v)) for v in income_eur],
        "high_income": _high_income(income_eur),
        "household_size": donors["household_size"].to_numpy().astype("int64"),
        "consumption_units": donors["consumption_units"].to_numpy(dtype=float),
    })
    for key, value in _STUDENT_PERSON_DEFAULTS.items():
        persons[key] = value
    persons = add_income_per_consumption_unit(persons)
    # The literal 'never_pt' below is braunschweig.popsim.attributes.PT_TICKET_NEVER,
    # spelled out rather than imported: braunschweig.synthesis must not import from
    # braunschweig.popsim at module level (the one-way layering that
    # braunschweig.popsim.sources.entd_attributes relies on to import safely FROM
    # braunschweig.synthesis without a cycle). If PT_TICKET_NEVER's value ever changes,
    # this literal must be updated to match.
    _log.info(
        "[student_incommuters] pt_subscription_type: %d in-commuter persons hard-coded to "
        "'never_pt' (control-external source; the 14+ resident Kreis control does not "
        "see them -- see ADR-0099 for the rationale, issue #329)", len(persons))
    return persons


def _high_income(income_eur):
    """``high_income`` by the resident rule, shared with the worker in-commuters (ADR-0191)."""
    from braunschweig.synthesis.incommuters import _high_income as high_income_of

    return high_income_of(income_eur)


def _build_student_households(ids, donors, modes):
    """One household record per injected student in-commuter (only the student is simulated),
    mirroring ``braunschweig.synthesis.incommuters._build_households``: the monthly income (EUR,
    rounded) is the donor household's; ``car_availability`` follows the fixed commute mode like the
    person's (ADR-0191)."""
    income_eur = donors["household_income_eur"].to_numpy(dtype=float).round(0)
    return pd.DataFrame({
        "household_id": ids["household_id"].to_numpy(),
        "person_id": ids["person_id"].to_numpy(),
        "census_household_id": ids["household_id"].to_numpy(),
        "household_income": [str(int(v)) for v in income_eur],
        "household_income_eur": income_eur,
        "high_income": _high_income(income_eur),
        "car_availability": np.where(np.asarray(modes) == "car", "all", "none"),
        "bicycle_availability": "all",
    })


def _adult_student_donors(student_donors, age_lower):
    """The student donors of at least ``age_lower`` years (``student_incommuter_age_band[0]``).

    ASSUMPTION: the in-commuters are university students, but the MiD ``studies`` flag also covers school
    children; a donor younger than the lower bound of the configured age band (or without an age) is excluded.
    The count and the primary share are logged next to the donor eligibility of
    :func:`braunschweig.synthesis.incommuters._eligible_donors`; WARNING above
    :data:`STUDENT_DONOR_AGE_EXCLUDED_WARN_SHARE`.
    """
    age = pd.to_numeric(student_donors["age"], errors="coerce")
    no_age = age.isna()
    too_young = ~no_age & (age < float(age_lower))
    keep = ~(no_age | too_young)
    n, n_kept = len(student_donors), int(keep.sum())
    share = (n - n_kept) / n if n else 0.0
    (_log.warning if share > STUDENT_DONOR_AGE_EXCLUDED_WARN_SHARE else _log.info)(
        "[student_incommuters] donor age: primary %d/%d student donors (%.2f%%), excluded %d: "
        "%d younger than %s, %d without an age", n_kept, n, 100.0 * n_kept / n if n else 0.0, n - n_kept,
        int(too_young.sum()), age_lower, int(no_age.sum()))
    if n_kept == 0:
        raise ValueError(
            f"[student_incommuters] no student donor of at least {age_lower} years (the lower bound of "
            f"student_incommuter_age_band): all {n} student donors are excluded ({int(too_young.sum())} younger, "
            f"{int(no_age.sum())} without an age); check the age column of braunschweig.data.hts.mid_donor.")
    return student_donors[keep].reset_index(drop=True)


def _agents_of_balance(balance, od, known_kreise) -> pd.DataFrame:
    """One row per in-commuter of the balance: ``[orig_ars5, destination_id, dest_commune, od_distance_km]``.

    Rows follow the balance (sorted by origin, then site); ``dest_commune`` is the site's Kreis
    (``site_kreis_ars5`` of T) and ``od_distance_km`` the cell's ``distance_km`` (the fallback of the commute length).
    Raises ``ValueError`` for an origin Kreis without a commune in the layer (the home rule cannot place it) or a site
    whose Kreis or a cell whose distance is not unique in T.
    """
    outside = od[~od["home_in_region"]]
    site_kreis = outside.groupby("destination_id")["site_kreis_ars5"].unique()
    ambiguous = sorted(str(site) for site, kreise in site_kreis.items() if len(kreise) != 1)
    if ambiguous:
        raise ValueError(f"{_FLOWS_TAG} sites of T with more than one site_kreis_ars5: {ambiguous}")
    cell_distance = outside.groupby(["home_ars5", "destination_id"])["distance_km"].unique()
    ambiguous_cells = [key for key, values in cell_distance.items() if len(values) != 1]
    if ambiguous_cells:
        raise ValueError(f"{_FLOWS_TAG} cells of T with more than one distance_km: {ambiguous_cells[:5]}")
    unknown = sorted(set(balance["orig_ars5"]) - set(known_kreise))
    if unknown:
        raise ValueError(f"{_FLOWS_TAG} origin Kreise of T without a commune in the VG250 layer outside the region "
                         f"(the home rule cannot place them): {unknown}")
    repeat = balance["n"].to_numpy(dtype="int64")
    orig = np.repeat(balance["orig_ars5"].to_numpy(dtype=object), repeat)
    destination = np.repeat(balance["destination_id"].to_numpy(dtype=object), repeat)
    return pd.DataFrame({
        "orig_ars5": orig, "destination_id": destination,
        "dest_commune": np.array([site_kreis[d][0] for d in destination], dtype=object),
        "od_distance_km": np.array([float(cell_distance[(o, d)][0]) for o, d in zip(orig, destination)], dtype=float),
    })


def _flows_commute_lengths(origins, dest_x, dest_y, gemeinden, weights, rng, detour_factor, warn_share):
    """Routed commute length (km) and its branch per agent (``student_incommuter_counts.commute_length_km``).

    The ``od_cell`` fallback is counted and logged as a primary/fallback rate, overall and per origin Kreis; a share
    above ``warn_share`` (``braunschweig.student_incommuters.commute_length_fallback_warn_share``) is a WARNING.
    """
    from braunschweig.data.education import student_incommuter_counts as sic

    by_kreis = {kreis: frame for kreis, frame in gemeinden.groupby(gemeinden["ars5"].astype(str))} \
        if gemeinden is not None and len(gemeinden) else {}
    n = len(origins)
    lengths = np.empty(n, dtype=float)
    sources = np.empty(n, dtype=object)
    for i, (kreis, cell_km) in enumerate(zip(origins["orig_ars5"], origins["od_distance_km"])):
        lengths[i], sources[i] = sic.commute_length_km(
            kreis, (dest_x[i], dest_y[i]), by_kreis.get(str(kreis)), weights, rng,
            od_distance_km=cell_km, detour_factor=detour_factor)
    fallback = sources == sic.COMMUTE_OD_CELL
    n_fallback = int(fallback.sum())
    per_kreis = (pd.Series(fallback).groupby(origins["orig_ars5"].to_numpy()).agg(["sum", "size"]))
    message = (f"{_FLOWS_TAG} commute length: primary {n - n_fallback}/{n} ({100.0 * (n - n_fallback) / n:.1f}%) "
               f"from a commune drawn by population x T's weight curve, fallback {n_fallback} "
               f"({100.0 * n_fallback / n:.1f}%) on the distance of T's cell; fallback per origin Kreis "
               f"{ {k: int(v) for k, v in per_kreis['sum'].items() if v} or 'none' }")
    (_log.warning if n_fallback / n > warn_share else _log.info)(message)
    return lengths, sources


def _inject(context):
    """Build the Home->Education->Home student in-commuter frames.

    Orchestrates, in order: the count anchor (Task 2), the reverse-decay origin
    draw (Task 3), the shared in-ring/gate home placement, the shared core-frame
    assembly (Task 1), and the student-specific persons/households builders
    above. See the module docstring for the stage-level design.
    """
    from braunschweig.constants import ROUTED_DETOUR_FACTOR
    from braunschweig.data.cordon import donor_draw, plans
    from braunschweig.data.cordon.demand import make_incommuter_ids
    from braunschweig.data.cordon.incommuter_origins import incommuter_origin_homes
    from braunschweig.data.cordon.mode_reference import (
        MID_DISTANCE_EDGES, restrict_to_modes, route_distance_band)
    from braunschweig.data.hts import mid_donor
    from braunschweig.data.education import student_incommuter_counts as sic
    from braunschweig.data.education import student_origins as so
    from braunschweig.data.external_workplaces import _load_gemeinden
    from braunschweig.data.mikrozensus.reference import load_commute_mode_by_distance
    from braunschweig.matsim.scenario import day_end_margin
    from braunschweig.synthesis import incommuter_pt_connections as pt_connections
    from braunschweig.synthesis import incommuters
    from braunschweig.synthesis.incommuters import (
        KEY_TIME_REDRAW_MAX_ATTEMPTS, _build_incommuter_passenger_vehicles, _build_legacy_vehicles,
        _time_bound_s, assemble_incommuter_core_frames)

    sampling_rate = float(context.config("sampling_rate"))
    slope = float(context.config("education_university_slope"))
    max_radius_km = float(context.config("education_university_max_radius_km"))
    age_lower, age_upper = context.config("student_incommuter_age_band")
    rng = np.random.default_rng(int(context.config("random_seed")) + _RNG_OFFSET)
    gate_speed_kmh = float(context.config("cordon_gate_speed_kmh"))
    data_path = context.config("data_path")

    from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED

    flows_enabled = bool(context.config(KEY_FLOWS_ENABLED))
    facilities = context.stage(
        "braunschweig.data.schools.university_facilities").to_crs(CRS_METRIC)
    municipalities = context.stage("data.spatial.municipalities").to_crs(CRS_METRIC)
    if not flows_enabled:
        # primary.locations (alias -> replacement_education_gravity) returns a tuple
        # (df_work, df_education); student in-commuters need the EDUCATION placements
        # (residents placed at university facilities), so take [1]. Latent #140 bug:
        # the real-data inject path was skip-gated, so .copy() on the tuple never ran.
        # Declared and read on the OFF path only: the ON path counts from T.
        resident_placement = context.stage("synthesis.population.spatial.primary.locations")[1]
    residents = context.stage("synthesis.population.enriched")
    _hh, hts_persons, hts_trips = context.stage("hts")
    gate_frames = context.stage("braunschweig.synthesis.cordon_gates")
    inkar_income = context.stage("braunschweig.data.inkar.household_income")
    min_effective_pool = _validated_min_effective_pool(context.config(KEY_DONOR_MIN_EFFECTIVE_POOL))
    # Fail fast, before any origin is drawn: the donor household, the draw attributes and the age.
    # (the age excludes the donors younger than the lower bound of student_incommuter_age_band).
    if "age" not in hts_persons.columns:
        raise ValueError(
            "[student_incommuters] the student in-commuter donor draw needs the donor age (ADR-0191 "
            "decision 7: it bounds the student donors by student_incommuter_age_band), which the MiD "
            f"donor stage only delivers when {mid_donor.KEY_DEMOGRAPHICS} is set to true.")
    incommuters._require_donor_columns(
        hts_persons, needs_distance=True, distance_column=DONOR_DISTANCE_COLUMN, extra_columns=("age",))

    if flows_enabled:
        # 1-2 (ON, design B6). Count and origin from the balance: f_eff x sampling rate x the outside rows of T per
        # origin Kreis and site, rounded with every row, column and the total within one person. No day-absence
        # draw follows: the count already carries f_eff.
        from braunschweig.data.education import student_flows_inputs as flows_inputs

        od_path = os.path.join(data_path, context.config(KEY_OD_TABLE_PATH))
        od = flows_inputs.load_od_table(od_path)
        od_weights = flows_inputs.od_weights(od_path)
        f_eff, _absence_mode = effective_day_factor_of_run(context)
        balance = sic.balance_incommuter_counts(flows_inputs.outside_seed(od), f_eff, sampling_rate, rng)
        balance = balance[balance["n"] > 0].reset_index(drop=True)
        if balance.empty:
            _log.warning("%s the balance gives no in-commuter at this sampling rate -- nothing injected", _FLOWS_TAG)
            return _empty_frames()
    else:
        # 1. Counts per university commune (data-anchored count anchor, Task 2).
        counts = sic.compute_incommuter_counts(
            facilities, municipalities, resident_placement, sampling_rate)
        counts = counts[counts["in_commuters"] > 0].reset_index(drop=True)
        if counts.empty:
            _log.warning(
                "[student_incommuters] no positive in-commuter count in any "
                "university commune (enrollment already filled by resident "
                "placement) -- nothing injected")
            return _empty_frames()

        # 2. Destination point per commune = capacity-weighted centroid of its local
        #    university facilities (the same buildings residents are placed in).
        fac_comm = sic.facility_communes(facilities, municipalities)
        fac = facilities.merge(fac_comm, on="location_id", how="inner")
        dest_xy = {}
        for comm, grp in fac.groupby("commune_ars5"):
            w = grp["capacity"].to_numpy(dtype=float)
            dest_xy[comm] = (float(np.average(grp.geometry.x, weights=w)),
                             float(np.average(grp.geometry.y, weights=w)))

    # 3. Candidate ORIGIN Kreise: all German Kreise OUTSIDE the ZGB cordon, one
    #    representative point (dissolved centroid) per Kreis.
    gem = _load_gemeinden(context).to_crs(CRS_METRIC)
    zgb_ars5 = {str(p) for p in context.config("braunschweig.political_prefix")}
    ext = gem[~gem["ars5"].astype(str).isin(zgb_ars5)].copy()
    centroids = ext.dissolve(by="ars5").geometry.centroid
    kreis_xy = pd.DataFrame({
        "ars5": centroids.index.astype(str),
        "x": centroids.x.to_numpy(dtype=float),
        "y": centroids.y.to_numpy(dtype=float),
    })
    if flows_enabled:
        # 4 (ON). One agent per person of the balance, carrying its site of T (two sites in one Kreis stay apart) and
        # the site's Kreis as ``dest_commune`` (the key the OD analysis reads, as on the OFF path).
        origins = _agents_of_balance(balance, od, set(kreis_xy["ars5"]))
    else:
        kreis_pop = so.student_age_pop_by_kreis(
            data_path, kreis_xy["ars5"].tolist(), age_lower, age_upper)

        # 4. Draw origin Kreise (reverse decay, Task 3).
        origins = so.draw_origin_kreise(dest_xy, counts, kreis_xy, kreis_pop,
                                        slope=slope, max_radius_km=max_radius_km, rng=rng)
    n = len(origins)
    if n == 0:
        _log.warning(
            "[student_incommuters] origin-Kreis draw produced zero agents "
            "(check the 18-29 population table) -- nothing injected")
        return _empty_frames()

    # 5. Home points: real population-weighted origin point when the source
    #    Kreis has an in-ring Gemeinde (shared helper, also used by the SvB
    #    stage); far agents (no in-ring Gemeinde) fall back to the nearest
    #    cordon gate so home coordinates are NEVER left NaN
    #    (CLAUDE.md no-silent-fallbacks).
    zgb_polygon = municipalities.geometry.union_all()
    source_buffer_m = float(context.config("cordon_network_source_buffer_m"))
    inbound_by_kreis = (
        origins.groupby("orig_ars5").size().reset_index(name="flow")
        .rename(columns={"orig_ars5": "ars5"}))
    gemeinden_for_homes = gem[["ars5", "gem_ags", "ewz", "geometry"]].copy()
    home_x, home_y, is_in_ring = incommuter_origin_homes(
        origins["orig_ars5"].tolist(), inbound_by_kreis, gemeinden_for_homes,
        zgb_polygon, source_buffer_m, rng)

    n_far = int((~is_in_ring).sum())
    if n_far > 0:
        far_idx = np.where(~is_in_ring)[0]
        far_ars5 = origins["orig_ars5"].to_numpy()[far_idx]
        far_x, far_y = _nearest_gate_xy(far_ars5, kreis_xy, gate_frames["gates"])
        home_x[far_idx] = far_x
        home_y[far_idx] = far_y
    n_in_ring = n - n_far
    pct_in_ring = 100.0 * n_in_ring / n
    _log.info(
        "[student_incommuters] home placement: in-ring primary %d/%d (%.1f%%), "
        "gate fallback %d/%d (%.1f%%)",
        n_in_ring, n, pct_in_ring, n_far, n, 100.0 - pct_in_ring)
    if np.isnan(home_x).any() or np.isnan(home_y).any():
        raise RuntimeError(
            "student_incommuters: home coordinates still contain NaN after the "
            "in-ring + gate fallback -- this must never happen (fallback bug)")

    if flows_enabled:
        # 5 (ON). T names origin Kreise up to the maximum commute distance, so the gate branch carries more agents
        # than on the OFF path: both branches are counted per origin Kreis.
        branches = _home_branch_by_origin(origins["orig_ars5"].to_numpy(), is_in_ring)
        _log.info("%s home rule per origin Kreis (in-ring primary / gate fallback): %s", _FLOWS_TAG,
                  {row.orig_ars5: (int(row.in_ring), int(row.gate)) for row in branches.itertuples()})
        # 6 (ON). The education location is a building of the agent's site, drawn by capacity.
        from braunschweig.synthesis.locations.university_assignment import draw_buildings

        building_ids = draw_buildings(
            pd.Series(origins["destination_id"].to_numpy(dtype=object), index=pd.RangeIndex(n)),
            pd.DataFrame(facilities[["location_id", "capacity", "destination_id"]]), rng).to_numpy(dtype=object)
        building_xy = dict(zip(facilities["location_id"],
                               zip(facilities.geometry.x.to_numpy(dtype=float),
                                   facilities.geometry.y.to_numpy(dtype=float))))
        dest_x = np.array([building_xy[b][0] for b in building_ids], dtype=float)
        dest_y = np.array([building_xy[b][1] for b in building_ids], dtype=float)
    else:
        # 6. Destination (education) point per agent.
        dest_x = np.array([dest_xy[c][0] for c in origins["dest_commune"]], dtype=float)
        dest_y = np.array([dest_xy[c][1] for c in origins["dest_commune"]], dtype=float)

    # 7. Ids off the residents' running max+1, offset by a large fixed block so
    #    student in-commuters never collide with residents OR the SvB
    #    in-commuter block (see _ID_OFFSET_ABOVE_RESIDENTS).
    n_residents = int(residents["person_id"].max()) + 1
    n_resident_households = int(residents["household_id"].max()) + 1
    ids = make_incommuter_ids(n, n_residents + _ID_OFFSET_ABOVE_RESIDENTS,
                              n_resident_households + _ID_OFFSET_ABOVE_RESIDENTS)
    person_ids = ids["person_id"].to_numpy()
    edu_location_ids = [f"ic_edu_{int(pid)}" for pid in person_ids]

    # 8. Donors + education-leg timings (memoised per unique donor id) + fixed
    #    commute mode from the Mikrozensus distance-band reference.
    dist_km = plans.straight_line_distance_km(home_x, home_y, dest_x, dest_y)

    # The donor of an agent is drawn per origin Kreis from the adult student donors with an education distance of a
    # similar length, weighted by the MiD person weight and tilted to the Kreis's INKAR income level (ADR-0191,
    # decisions 3 to 5), as for the worker in-commuters. The sampler logs its own primary/fallback rates; only the
    # agent distance fallback is logged here.
    if flows_enabled:
        # 8 (ON). The commute length T implies (a commune of the origin Kreis drawn by population x T's weight curve),
        # not the one of the home point: it sets the donor window AND the mode band. The home point stays.
        commute_km, commute_source = _flows_commute_lengths(
            origins, dest_x, dest_y, gem, od_weights, rng, ROUTED_DETOUR_FACTOR,
            float(context.config(KEY_COMMUTE_FALLBACK_WARN_SHARE)))
    else:
        commute_km, n_commute_fallback = incommuters._agent_commute_km(
            home_x, home_y, dest_x, dest_y, has_real_home=is_in_ring, orig_ars=origins["orig_ars5"].to_numpy(),
            gemeinden=gem, detour_factor=ROUTED_DETOUR_FACTOR)
        commute_message = (f"[student_incommuters] agent education distance for the donor match: primary "
                           f"{n - n_commute_fallback}/{n} ({100.0 * (n - n_commute_fallback) / n:.1f}%), fallback "
                           f"{n_commute_fallback} ({100.0 * n_commute_fallback / n:.1f}%) on the own gate "
                           f"(no real home, no Gemeinde of the origin Kreis; a lower bound of the distance)")
        incommuters._check_commute_fallback(n, n_commute_fallback, commute_message)
        if 100.0 * n_commute_fallback / n > incommuters.AGENT_COMMUTE_FALLBACK_WARN_PERCENT:
            _log.warning(commute_message)
        else:
            _log.info(commute_message)

    pool = incommuters._eligible_donors(
        _adult_student_donors(plans.select_student_donors(hts_persons, hts_trips, "person_id"), age_lower),
        needs_distance=True, distance_column=DONOR_DISTANCE_COLUMN)
    scale_by_kreis = (None if inkar_income is None else
                      dict(zip(inkar_income["ars5"].astype(str), inkar_income["scale"].astype(float))))
    sampler = donor_draw.KreisDonorSampler(
        pool, scale_by_kreis, min_effective_pool=min_effective_pool, distance_column=DONOR_DISTANCE_COLUMN)
    kreis = incommuters._incommuter_kreis_ags5(origins["orig_ars5"].to_numpy())
    drawn_rounds = []

    def draw_donors(positions):
        # The first call draws every agent: its rates are logged once (and warned about); the re-draw rounds,
        # which draw only the late agents, stay silent.
        rows = sampler.draw(kreis[positions], commute_km[positions], rng, log=not drawn_rounds)
        drawn_rounds.append(len(positions))
        return pool.iloc[rows].reset_index(drop=True)

    # A donor whose day ends after the bound (the simulated day minus the day-end margin) is re-drawn up to
    # ``time_redraw_max_attempts`` times and the persons follow the final donor (#467); the donor's own
    # home-departure time is replaced below by the distance-consistent seed, so the chain end is the return arrival.
    time_bound_s = _time_bound_s(context)
    donors, (_donor_depart_home, arrive_mid, depart_mid, arrive_home), redraw_stats = (
        plans.redraw_times_within_bound(
            pool, n, rng,
            lambda d, _pos: _donor_education_times(d, hts_trips, "person_id"),
            bound_s=time_bound_s, max_attempts=int(context.config(KEY_TIME_REDRAW_MAX_ATTEMPTS)),
            draw=draw_donors))
    donor_report = sampler.report(kreis, commute_km)
    if scale_by_kreis is not None:
        incommuters._check_kreis_scale_fallback(n, donor_report)
    if inkar_income is None:
        _log.warning("[student_incommuters] no INKAR frame supplied: %d in-commuters are drawn without the "
                     "origin-Kreis income tilt", n)

    # Distance-consistent home-departure seed (mirrors the SvB path's _agent_times):
    # the RAW donor depart_home comes from an HTS trip whose length is unrelated to
    # this agent's synthetic home->campus distance, so for a far agent (home = nearest
    # gate, campus inside ZGB) it can imply an absurd travel speed in the seed plan.
    # Re-seed depart_home = arrive_mid - (routed home->campus distance / gate speed) so
    # the initial schedule is speed-consistent; arrive_mid (donor arrival at education)
    # stays the anchor and the return leg keeps the donor timing, exactly as the SvB
    # stage does. MATSim re-times the simulated leg over the iterations regardless; this
    # only fixes the seed plan (see docs/features/student-incommuters.md timing note).
    travel_s = (dist_km * ROUTED_DETOUR_FACTOR / gate_speed_kmh) * 3600.0
    depart_home = np.maximum(0.0, arrive_mid - travel_s)

    # The re-draw above sees the chain ends BEFORE imputation (a NaN end counts as fitting); the shift below sees
    # them AFTER imputation (D1 design). Last resort for days no re-draw fitted: impute missing donor times, then
    # move a day that still ends after the bound earlier as a whole (as the commuter builder does; never drops an
    # agent), counted and logged.
    depart_home, arrive_mid, depart_mid, arrive_home = plans.impute_incommuter_times(
        depart_home, arrive_mid, depart_mid, arrive_home, middle_purpose="education",
        repair_chronology=context.config("cordon_incommuter_time_chronology"))
    move_s, _late, unresolved = day_end_margin.late_day_shift_s(
        depart_home, arrive_home, target_s=time_bound_s)
    depart_home, arrive_mid, depart_mid, arrive_home = (
        depart_home - move_s, arrive_mid - move_s, depart_mid - move_s, arrive_home - move_s)
    n_days_moved = int((move_s > 0).sum())
    n_still_late = int(unresolved.sum())
    shift_message = (f"[student_incommuters] day bound {time_bound_s:.0f} s: {n_days_moved}/{n} days moved "
                     f"earlier as a whole ({100.0 * n_days_moved / n:.1f}%), {n_still_late} still late")
    if n_days_moved / n > 0.05 or n_still_late:
        _log.warning(shift_message)
    else:
        _log.info(shift_message)
    day_bound = dict(redraw_stats, bound_s=float(time_bound_s), n_days_moved=n_days_moved,
                     n_still_late=n_still_late)
    if flows_enabled:
        # Mode (ON): car or pt from the STUDENTS' mode by band of the routed commute length (design B6).
        from braunschweig.data.education.student_mode_reference import (
            load_student_education_mode_by_distance, student_mode_band)

        reference = load_student_education_mode_by_distance(
            data_path, int(context.config(KEY_MODE_BAND_MIN_PERSONS)))
        modes = list(plans.assign_fixed_mode(commute_km, reference, student_mode_band, rng))
    else:
        reference = restrict_to_modes(
            load_commute_mode_by_distance(data_path), allowed=("car", "pt"))
        band_fn = lambda d: route_distance_band(  # noqa: E731
            d, detour_factor=ROUTED_DETOUR_FACTOR, edges=MID_DISTANCE_EDGES)
        modes = list(plans.assign_fixed_mode(dist_km, reference, band_fn, rng))

    # 8a. Timetable check of the pt days (#528): the #488 chain of the commuter stage, day shift first, then car.
    # Students board at no drawn entry station (their home is a real in-ring point or the nearest road gate), so the
    # station re-draw has nothing to try, and a student switched to car drives from the home it already has.
    pt_report = None
    if context.config(pt_connections.KEY_ENABLED):
        checked = pt_connections.check_pt_days(
            incommuters._pt_check(context), person_ids=person_ids, modes=modes,
            home_xy=np.column_stack([home_x, home_y]), mid_xy=np.column_stack([dest_x, dest_y]),
            depart_home_s=depart_home, arrive_mid_s=arrive_mid, depart_mid_s=depart_mid, arrive_home_s=arrive_home,
            car_allowed=True, subject="student in-commuter")
        modes, pt_report = list(checked.modes), checked.report
        depart_home, arrive_mid, depart_mid, arrive_home = (
            checked.depart_home_s, checked.arrive_mid_s, checked.depart_mid_s, checked.arrive_home_s)

    # 9. Shared core frames (DRY: same helper the SvB stage uses).
    core = assemble_incommuter_core_frames(
        person_ids, home_x, home_y, dest_x, dest_y, edu_location_ids,
        depart_home, arrive_mid, depart_mid, arrive_home, modes,
        municipalities.crs, middle_purpose="education",
        repair_chronology=context.config("cordon_incommuter_time_chronology"))

    # 10. Student persons/households: the donor household (no fleet draw).
    persons = _build_student_persons(ids, donors, modes)
    households = _build_student_households(ids, donors, modes)

    # 10a. Origin Kreis + destination university commune, attached for the
    # downstream OD analysis (braunschweig.analysis.simwrapper.student_commuters,
    # #140 Task 6). ``origins`` (step 4) and ``ids``/``persons`` (step 7-10) are
    # both built from the SAME length-n draw without any intervening sort, so a
    # positional (not id-keyed) assignment is safe here. These are EXTRA columns
    # on top of the schema matsim.scenario.population's concat_frame expects --
    # concat_frame reindexes the in-commuter frame onto the resident columns
    # before concatenating, so the extra columns are dropped there and never
    # reach the MATSim population writer (verified against
    # braunschweig.synthesis.incommuter_merge._base.concat_frame).
    persons["orig_ars5"] = origins["orig_ars5"].to_numpy()
    persons["dest_commune"] = origins["dest_commune"].to_numpy()
    if flows_enabled:
        # ON: the site of T, the education building drawn for it and the commute length with its source (routed km;
        # ``commune_curve`` or the counted fallback ``od_cell``), for the acceptance table and the diagnostics.
        persons["destination_id"] = origins["destination_id"].to_numpy(dtype=object)
        persons["education_building_id"] = building_ids
        persons["commute_length_km"] = commute_km
        persons["commute_length_source"] = commute_source

    # 10b. Vehicles (2026-07-18 Task 5 review fix). Every in-commuter -- resident,
    # SvB, or student -- must own a "car_passenger" vehicle: it is a network-routed
    # mode (eqasim core NETWORK_MODES = [car, car_passenger, truck]) that the
    # in-loop discrete mode choice can assign to ANY agent regardless of car
    # ownership, so a missing vehicle aborts the MATSim router. Car-mode agents
    # additionally need a "car" vehicle. Students have no fleet model (their income is
    # the donor household's, see _build_student_persons), so they reuse the SvB stage's LEGACY (non-German-
    # fleet) vehicle builders -- the same path the SvB stage takes when no
    # ``data_path`` is supplied; no distinct HBEFA vehicle type is introduced, so
    # ``vehicle_types`` stays empty (the resident/SvB ``default_car`` /
    # ``default_car_passenger`` types already cover these vehicles).
    vehicle_types = pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                          "hbefa_cat", "hbefa_tech", "hbefa_size",
                                          "hbefa_emission"])
    vehicles = _build_legacy_vehicles(person_ids, modes)
    vehicles = pd.concat(
        [vehicles, _build_incommuter_passenger_vehicles(person_ids)], ignore_index=True)

    # 11. Per-commune injection counts (CLAUDE.md traceability).
    if flows_enabled:
        _log.info("%s injected %d students at %d sites: %s", _FLOWS_TAG, n, origins["destination_id"].nunique(),
                  origins.groupby("destination_id", sort=True).size().to_dict())
    else:
        _log.info(
            "[student_incommuters] injected %d students across %d university "
            "communes: %s", n, len(counts),
            dict(zip(counts["commune_ars5"], counts["in_commuters"])))

    frames = {
        "persons": persons, "households": households,
        "trips": core["trips"], "activities": core["activities"],
        "locations": core["locations"],
        "vehicles": vehicles, "vehicle_types": vehicle_types,
        "day_bound": day_bound,
        "donor_draw": donor_report,
    }
    if pt_report is not None:
        frames["pt_connection"] = pt_report   # the counts of the #528 check, as the commuter stage reports them
    return frames
