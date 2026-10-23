"""Config keys and declared defaults of the portal-trip layer (eqasim-bs#442).

The ONE home for the key names and defaults every reader declares them with: the portal stage,
``braunschweig.popsim.distance_distributions`` (CDF bound) and
``braunschweig.synthesis.locations.secondary_candidates`` (external centroids bounded to the
supply ring). Production values live in configs/base_bs.yml.
"""
from __future__ import annotations

#: Whole feature incl. both guard rails; False is byte-identical to the pre-feature pipeline.
KEY_ENABLED = "braunschweig.portal.enabled"
DEFAULT_ENABLED = True

#: The reporting-day trips the portal stage takes as its INPUT (before any outside stay is built). The SrV
#: comparisons (plan structure, departure times) read this stage instead of ``synthesis.population.trips.final``
#: while the portal layer is on, because SrV has no outside stays and the unchanged reference would otherwise be
#: compared with a rewritten day (ADR-0141, #442).
PRE_PORTAL_TRIPS_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"

#: The reporting-day trips after the portal rewrite (the alias of ``trips_final``).
FINAL_TRIPS_STAGE = "synthesis.population.trips.final"


#: File stem (after the output prefix) of the pre-portal trips table the synthesis output writes while the portal layer is
#: on: ``<prefix>trips_pre_portal.csv`` / ``.parquet``. It carries the vendored trips column set of the donor day, the
#: ``<prefix>trips.csv`` next to it the portal-rewritten day. File-based validators that compare the diary with a
#: survey prefer this file when it exists (``braunschweig.analysis.pipeline_trips_file``).
PRE_PORTAL_TRIPS_FILE_STEM = "trips_pre_portal"

#: File stem of the pre-portal home -> work commute lines (``<prefix>commutes_pre_portal.gpkg`` / ``.geoparquet``),
#: written next to ``<prefix>commutes.gpkg`` while the portal layer is on. ``<prefix>commutes.gpkg`` is built from the
#: written activities, where a far workplace is an ``outside`` activity, so its far commuters are missing; this file
#: is built from the assigned primary locations and the pre-portal trips and has the same schema (``person_id``,
#: ``geometry``; CRS-less like the vendored output, coordinates in the pipeline CRS EPSG:25832). The home -> education
#: lines are the layer ``PRE_PORTAL_EDUCATION_LAYER`` of the same GeoPackage
#: (and the file ``education_commutes_pre_portal.geoparquet``); the first layer of the GeoPackage is the work layer.
PRE_PORTAL_COMMUTES_FILE_STEM = "commutes_pre_portal"
PRE_PORTAL_EDUCATION_LAYER = "education"
PRE_PORTAL_EDUCATION_FILE_STEM = "education_commutes_pre_portal"


def final_view_trips_stage(portal_enabled: bool) -> str:
    """The trips stage an SrV-comparison style consumer of the ``final`` view must read (ADR-0141, #442).

    With the portal layer on, the day has far work/education/other legs replaced by outside stays; the SrV
    comparisons and the work-participation report measure the unchanged day (SrV has no outside stays, a far
    work trip would otherwise vanish from the participation count), so they read the pre-portal trips. With the
    layer off the two stages are the same table and ``synthesis.population.trips.final`` is read as before.
    """
    return PRE_PORTAL_TRIPS_STAGE if portal_enabled else FINAL_TRIPS_STAGE

#: Straight-line metres. Classification threshold (reported distance of a secondary leg, or the
#: home-to-location distance of a work/education leg), upper bound of the distance CDFs and of
#: the external candidates. Defaults to the supply ring width (cordon_network_source_buffer_m)
#: but is a separate key: distance from the trip's origin, not a buffer beyond the ZGB border.
KEY_MAX_ROUTABLE_DISTANCE_M = "braunschweig.portal.max_routable_distance_m"
DEFAULT_MAX_ROUTABLE_DISTANCE_M = 45000.0

#: Relative band around the reported distance within which the external point is drawn.
KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE = "braunschweig.portal.external_point_distance_tolerance"
DEFAULT_EXTERNAL_POINT_DISTANCE_TOLERANCE = 0.2

#: WARN when the share of outside stays whose mode had to be substituted exceeds this.
KEY_MODE_SUBSTITUTION_WARN_SHARE = "braunschweig.portal.mode_substitution_warn_share"
DEFAULT_MODE_SUBSTITUTION_WARN_SHARE = 0.1

#: WARN when the share of stays that took a FALLBACK instead of the primary method exceeds this:
#: the external point drawn outside the distance band, and the work/education legs classified by
#: the donor's reported distance instead of the assigned location. A high rate means the primary
#: method (the band search, the assigned-location join) is broken, not merely rare.
KEY_FALLBACK_WARN_SHARE = "braunschweig.portal.fallback_warn_share"
DEFAULT_FALLBACK_WARN_SHARE = 0.1

#: Transit modes whose routes may serve as pt portal gates: ``"all"`` (every mode of the schedule) or a list of
#: mode names (compared lower-cased) that restricts the exit stops (#465).
KEY_PT_GATE_MODES = "braunschweig.portal.pt_gate_modes"
DEFAULT_PT_GATE_MODES = "all"

#: Seconds. A pt gate must have an outward departure within [t_gate, t_gate + window] at the planned gate
#: arrival (outbound) and, for stays with a return leg, at the planned re-entry (#465).
KEY_GATE_SERVICE_WINDOW_S = "braunschweig.portal.gate_service_window_s"
DEFAULT_GATE_SERVICE_WINDOW_S = 3600.0

#: Number of exit stops (smallest detour, outward route towards the external point) the router checks per stay.
KEY_GATE_CANDIDATES = "braunschweig.portal.gate_candidates"
DEFAULT_GATE_CANDIDATES = 5

#: Seconds. A pt stay without an admissible gate may shift its trip departures to the grid
#: ``t + k * shift_step_s`` up to ``t + shift_window_s`` (#465).
KEY_SHIFT_WINDOW_S = "braunschweig.portal.shift_window_s"
DEFAULT_SHIFT_WINDOW_S = 7200.0
KEY_SHIFT_STEP_S = "braunschweig.portal.shift_step_s"
DEFAULT_SHIFT_STEP_S = 1800.0

#: Seconds. An outbound shift is allowed only while at least this much time remains between the shifted
#: outbound arrival and the return departure.
KEY_MIN_STAY_S = "braunschweig.portal.min_stay_s"
DEFAULT_MIN_STAY_S = 1800.0

#: WARN when the share of pt stays that did not get a timetable-aware gate (tour switched, shifted or kept
#: without connection) exceeds this.
KEY_NO_CONNECTION_WARN_SHARE = "braunschweig.portal.no_connection_warn_share"
DEFAULT_NO_CONNECTION_WARN_SHARE = 0.05

#: #465 (WP-E smoke: 14 of 123 pt stays kept, their trips became walks of up to 125 km): after the later shifts,
#: try an EARLIER return of the stays still unresolved (same grid, min_stay_s kept), and resolve what remains as a
#: lift (``car_passenger`` without the passenger availability, ASSUMPTION) instead of keeping it as pt.
KEY_SHIFT_RETURN_EARLIER = "braunschweig.portal.shift_return_earlier"
DEFAULT_SHIFT_RETURN_EARLIER = True
KEY_NO_CONNECTION_LAST_RESORT = "braunschweig.portal.no_connection_last_resort"
DEFAULT_NO_CONNECTION_LAST_RESORT = "car_passenger"
NO_CONNECTION_LAST_RESORTS = ("car_passenger", "keep")

#: Added to random_seed for the external-point draw; disjoint from the in-commuter (100000)
#: and student in-commuter (200000) offsets so the streams never overlap.
RNG_OFFSET = 300000

#: The activity type of an outside stay -- the same type the eqasim cutter and the in-commuter
#: homes use, so the Java side (OutsideFilter in particular) sees one vocabulary.
OUTSIDE_PURPOSE = "outside"

#: Prefix of the facility id of a portal gate: the locations output carries ``portal_<gate_id>`` for every
#: ``outside`` activity and ``braunschweig.matsim.scenario.facilities`` registers one facility per used gate
#: (eqasim core's Java LinkAssignment throws for an activity whose facility does not exist).
PORTAL_LOCATION_ID_PREFIX = "portal_"

#: Boolean activity attribute the population writer sets to true on every "outside" activity it writes. It is an
#: identification marker: analyses and any later Java consumer can tell a portal gate from an eqasim-cutter
#: outside activity. No Java code in the production configuration reads it (ADR-0141). The vendored writer
#: matsim.scenario.population repeats the name as a literal; tests/test_population_writer_portal_gate.py pins both.
PORTAL_GATE_ACTIVITY_ATTRIBUTE = "portalGate"


def validate_settings(max_routable_distance_m: float, external_point_distance_tolerance: float,
                      mode_substitution_warn_share: float, fallback_warn_share: float, *,
                      gate_candidates: int = DEFAULT_GATE_CANDIDATES,
                      gate_service_window_s: float = DEFAULT_GATE_SERVICE_WINDOW_S,
                      shift_window_s: float = DEFAULT_SHIFT_WINDOW_S, shift_step_s: float = DEFAULT_SHIFT_STEP_S,
                      min_stay_s: float = DEFAULT_MIN_STAY_S,
                      no_connection_warn_share: float = DEFAULT_NO_CONNECTION_WARN_SHARE,
                      shift_return_earlier: bool = DEFAULT_SHIFT_RETURN_EARLIER,
                      no_connection_last_resort: str = DEFAULT_NO_CONNECTION_LAST_RESORT) -> None:
    """Raise ``ValueError`` naming the key when a setting is outside its valid range."""
    if not max_routable_distance_m > 0.0:
        raise ValueError(f"{KEY_MAX_ROUTABLE_DISTANCE_M} must be > 0 m, got {max_routable_distance_m!r}")
    if not 0.0 < external_point_distance_tolerance < 1.0:
        raise ValueError(f"{KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE} must lie in (0, 1), "
                         f"got {external_point_distance_tolerance!r}")
    if not 0.0 < mode_substitution_warn_share <= 1.0:
        raise ValueError(f"{KEY_MODE_SUBSTITUTION_WARN_SHARE} must lie in (0, 1], "
                         f"got {mode_substitution_warn_share!r}")
    if not 0.0 < fallback_warn_share <= 1.0:
        raise ValueError(f"{KEY_FALLBACK_WARN_SHARE} must lie in (0, 1], got {fallback_warn_share!r}")
    if int(gate_candidates) != gate_candidates or gate_candidates < 1:
        raise ValueError(f"{KEY_GATE_CANDIDATES} must be an integer >= 1, got {gate_candidates!r}")
    if not gate_service_window_s >= 0.0:
        raise ValueError(f"{KEY_GATE_SERVICE_WINDOW_S} must be >= 0 s, got {gate_service_window_s!r}")
    if not shift_window_s >= 0.0:
        raise ValueError(f"{KEY_SHIFT_WINDOW_S} must be >= 0 s, got {shift_window_s!r}")
    if not shift_step_s > 0.0:
        raise ValueError(f"{KEY_SHIFT_STEP_S} must be > 0 s, got {shift_step_s!r}")
    if not min_stay_s >= 0.0:
        raise ValueError(f"{KEY_MIN_STAY_S} must be >= 0 s, got {min_stay_s!r}")
    if not 0.0 < no_connection_warn_share <= 1.0:
        raise ValueError(f"{KEY_NO_CONNECTION_WARN_SHARE} must lie in (0, 1], got {no_connection_warn_share!r}")
    if not isinstance(shift_return_earlier, bool):
        raise ValueError(f"{KEY_SHIFT_RETURN_EARLIER} must be true or false, got {shift_return_earlier!r}")
    if no_connection_last_resort not in NO_CONNECTION_LAST_RESORTS:
        raise ValueError(f"{KEY_NO_CONNECTION_LAST_RESORT} must be one of {list(NO_CONNECTION_LAST_RESORTS)}, "
                         f"got {no_connection_last_resort!r}")
