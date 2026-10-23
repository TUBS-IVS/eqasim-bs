"""Config keys, defaults and stage names of the outside-tour mode layer (eqasim-bs#453).

The ONE home for the key names and defaults; production values live in configs/base_bs.yml.
"""
from __future__ import annotations

#: Whole feature; False returns the trips.final frame unchanged (byte-identical OFF path).
KEY_ENABLED = "braunschweig.outside_tour_modes.enabled"
DEFAULT_ENABLED = True

#: A mode is implausible in a (purpose group, band) cell when its SrV share is below this. Range (0, 1).
KEY_MIN_SHARE = "braunschweig.outside_tour_modes.min_share"
DEFAULT_MIN_SHARE = 0.01

#: WARN when more than this share of the outside tours had to be re-moded (the table and the population
#: disagree structurally). Range (0, 1].
KEY_WARN_SHARE = "braunschweig.outside_tour_modes.warn_share"
DEFAULT_WARN_SHARE = 0.5

#: What happens to a tour that nothing else resolves (ADR-0159): ``car_passenger`` = a lift without the passenger
#: availability (a pt tour without a timetable connection, a road-gate portal tour without an admissible mode);
#: ``keep`` = the former behaviour (the router realises the trip as a walk). The values of ``pt_connections``.
KEY_LAST_RESORT = "braunschweig.outside_tour_modes.last_resort"
DEFAULT_LAST_RESORT = "car_passenger"

#: Timetable check of the pt legs of outside tours without a portal leg (#465, ADR-0159).
KEY_PT_CHECK_ENABLED = "braunschweig.outside_tour_modes.pt_connection_check.enabled"
DEFAULT_PT_CHECK_ENABLED = True
#: ASSUMPTION (ADR-0159): how far (s) a tour may move to reach a connection, and in which steps; the values of the
#: portal and in-commuter shifts (ADR-0152, ADR-0153), not calibrated.
KEY_PT_SHIFT_WINDOW_S = "braunschweig.outside_tour_modes.pt_shift_window_s"
DEFAULT_PT_SHIFT_WINDOW_S = 7200.0
KEY_PT_SHIFT_STEP_S = "braunschweig.outside_tour_modes.pt_shift_step_s"
DEFAULT_PT_SHIFT_STEP_S = 1800.0
#: WARN when more than this share of the checked tours is switched, lifted or kept. Range [0, 1].
KEY_PT_NO_CONNECTION_WARN_SHARE = "braunschweig.outside_tour_modes.pt_no_connection_warn_share"
DEFAULT_PT_NO_CONNECTION_WARN_SHARE = 0.05

#: The alias the population writer and the synthesis output read (resolves to the stage in base_bs.yml).
SIMULATED_TRIPS_ALIAS = "synthesis.population.trips.simulated"
#: The activities of the simulated day (ADR-0159): the eqasim transform on the simulated trips, whose times the pt
#: check may move; the population writer and the synthesis output read it instead of ``activities.final``.
SIMULATED_ACTIVITIES_ALIAS = "synthesis.population.activities.simulated"
#: The input: the reporting day after the portal rewrite.
FINAL_TRIPS_STAGE = "synthesis.population.trips.final"
FINAL_ACTIVITIES_STAGE = "synthesis.population.activities.final"
LOCATIONS_STAGE = "synthesis.population.spatial.locations"
PERSONS_STAGE = "synthesis.population.enriched"
MUNICIPALITIES_STAGE = "data.spatial.municipalities"

#: Added to random_seed; disjoint from 100000 (in-commuters), 200000 (students) and 300000 (portal).
RNG_OFFSET = 400000
REPORT_FILE_NAME = "outside_tour_modes_report.json"
#: Modes every person may use (BraunschweigModeAvailability); one of them must be plausible in every cell.
ALWAYS_AVAILABLE_MODES = ("pt", "walk")
OUTSIDE_PURPOSE = "outside"
HOME_PURPOSE = "home"


def validate_settings(min_share: float, warn_share: float, last_resort: str = DEFAULT_LAST_RESORT,
                      pt_no_connection_warn_share: float = DEFAULT_PT_NO_CONNECTION_WARN_SHARE) -> None:
    """Raise ``ValueError`` naming the key when a setting is outside its valid range."""
    if not 0.0 < min_share < 1.0:
        raise ValueError(f"{KEY_MIN_SHARE} must lie in (0, 1), got {min_share!r}")
    if not 0.0 < warn_share <= 1.0:
        raise ValueError(f"{KEY_WARN_SHARE} must lie in (0, 1], got {warn_share!r}")
    if last_resort not in ("car_passenger", "keep"):
        raise ValueError(f"{KEY_LAST_RESORT} must be 'car_passenger' or 'keep', got {last_resort!r}")
    if not 0.0 <= pt_no_connection_warn_share <= 1.0:
        raise ValueError(f"{KEY_PT_NO_CONNECTION_WARN_SHARE} must lie in [0, 1], got {pt_no_connection_warn_share!r}")
