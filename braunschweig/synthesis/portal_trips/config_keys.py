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
#: compared with a rewritten day (ruling R33).
PRE_PORTAL_TRIPS_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"

#: The reporting-day trips after the portal rewrite (the alias of ``trips_final``).
FINAL_TRIPS_STAGE = "synthesis.population.trips.final"


def final_view_trips_stage(portal_enabled: bool) -> str:
    """The trips stage an SrV-comparison style consumer of the ``final`` view must read (rulings R33, R35).

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
                      mode_substitution_warn_share: float, fallback_warn_share: float) -> None:
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
