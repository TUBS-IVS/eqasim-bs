"""Candidate-frame column vocabularies (offer / potential columns).

Maps every internal chainsolver activity to the candidate potential column
it is scored on, and names the special-purpose offer/potential column pairs
(residential visits, escort education / residential candidates). Kept in a
dependency-free leaf module because BOTH the candidate-assembly helpers and
the SrV location-type helpers consume these names.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from .activity_types import LEISURE_SUBTYPE_ACTIVITIES, OTHER_SUBTYPE_ACTIVITIES


_ACTIVITY_POTENTIAL_COLUMN = {
    "shop": "pot_shop",
    "shop_daily": "pot_shop_daily",
    "shop_non_daily": "pot_shop_non_daily",
    "leisure": "pot_leisure",
    "other": "pot_other",
}
_ACTIVITY_POTENTIAL_COLUMN.update({name: "pot_leisure" for name in LEISURE_SUBTYPE_ACTIVITIES})
_ACTIVITY_POTENTIAL_COLUMN.update({name: "pot_other" for name in OTHER_SUBTYPE_ACTIVITIES})
# Escort location-type activities (issue #201). "escort_residential" reuses the
# literal "pot_visit" column name here (NOT the VISIT_POTENTIAL_COLUMN constant,
# which is defined further below in this module) so this default/OFF-path
# mapping does not depend on a forward reference.
_ACTIVITY_POTENTIAL_COLUMN.update({
    "escort_edu_kindergarten": "pot_escort_edu",
    "escort_edu_school": "pot_escort_edu",
    "escort_edu_university": "pot_escort_edu",
    "escort_leisure": "pot_leisure",
    "escort_other": "pot_other",
    "escort_residential": "pot_visit",
    "escort_shop": "pot_shop",
})

# Offer / potential columns used for the "leisure_visit" activity ONLY when
# ``leisure_visit_building_potential`` is ON (Task 5, issue #127). Residential
# candidates appended by ``append_residential_visit_candidates`` carry
# ``offers_visit`` / ``pot_visit`` instead of the generic ``offers_leisure`` /
# ``pot_leisure`` shared by the other three leisure groups, so a residential
# building is a candidate for "leisure_visit" and NOTHING else. This dict
# stays fixed (does not update ``_ACTIVITY_POTENTIAL_COLUMN``, which is the
# OFF-path / default mapping tested by
# ``test_activity_potential_column_covers_all_subtype_activities``); the
# override is applied locally inside ``_build_locations_df``.
VISIT_OFFER_COLUMN = "offers_visit"
VISIT_POTENTIAL_COLUMN = "pot_visit"

# Warn if appending residential visit candidates multiplies the locations-
# frame row count by more than this factor (CLAUDE.md "Fallback
# transparency" / growth-guard requirement: a large, unforeseen growth of the
# carla candidate universe is a runtime-cost and correctness risk that must
# be surfaced, not hidden).
VISIT_CANDIDATE_WARN_FACTOR = 3.0


# Offer / potential columns for the escort education candidates (issue #201).
ESCORT_EDU_OFFER_BY_TYPE = {
    "kindergarten": "offers_escort_edu_kindergarten",
    "school": "offers_escort_edu_school",
    "university": "offers_escort_edu_university",
}
ESCORT_EDU_POTENTIAL_COLUMN = "pot_escort_edu"
ESCORT_RESIDENTIAL_OFFER_COLUMN = "offers_escort_residential"
