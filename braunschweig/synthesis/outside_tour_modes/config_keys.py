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

#: The alias the population writer and the synthesis output read (resolves to the stage in base_bs.yml).
SIMULATED_TRIPS_ALIAS = "synthesis.population.trips.simulated"
#: The input: the reporting day after the portal rewrite.
FINAL_TRIPS_STAGE = "synthesis.population.trips.final"
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


def validate_settings(min_share: float, warn_share: float) -> None:
    """Raise ``ValueError`` naming the key when a setting is outside its valid range."""
    if not 0.0 < min_share < 1.0:
        raise ValueError(f"{KEY_MIN_SHARE} must lie in (0, 1), got {min_share!r}")
    if not 0.0 < warn_share <= 1.0:
        raise ValueError(f"{KEY_WARN_SHARE} must lie in (0, 1], got {warn_share!r}")
