"""Internal chainsolver activity-name vocabularies (stage-private).

Subtype and escort location-type activities exist only INSIDE the
chainsolver stage package: legs are tagged with these names so the solver
can route them to their own distance layers and candidate pools, and
``_extract_locations`` maps every one of them back to its public eqasim
purpose (``shop`` / ``leisure`` / ``other`` / ``escort``) before the stage
returns. Nothing outside the stage ever sees these names.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""


# Internal shop subtype activities (chainsolver-only). They never leak into the
# eqasim output: _extract_locations maps them back to the "shop" purpose.
SHOP_SUBTYPE_ACTIVITIES = ("shop_daily", "shop_non_daily")

# Internal leisure subtype activities (chainsolver-only; Task 4, issue #127).
# Mirror the four purpose_subtype.LEISURE_GROUPS keys exactly (kept as literal
# strings here, not imported, matching how SHOP_SUBTYPE_ACTIVITIES mirrors
# shop_subtype's daily/non-daily vocabulary without importing it). They never
# leak into the eqasim output: _extract_locations maps them back to "leisure".
LEISURE_SUBTYPE_ACTIVITIES = (
    "leisure_local", "leisure_visit", "leisure_activity", "leisure_excursion",
)

# Internal "other" errand/escort subtype activities (chainsolver-only; Task 4,
# issue #127). Mirror the two purpose_subtype.OTHER_ERRAND_GROUPS keys plus the
# always-labelled escort outcome. "other_rest" is deliberately NOT included: it
# keeps the plain "other" activity rather than becoming its own chainsolver
# activity name (see _build_other_subtype_decider). They never leak into the
# eqasim output: _extract_locations maps them back to "other".
OTHER_SUBTYPE_ACTIVITIES = ("other_errand_short", "other_errand_long", "other_escort")

# Internal escort location-type activities (chainsolver-only; issue #201). One
# per drawable location category; the draw happens per escort leg in
# _build_plans_df via _build_escort_location_decider. They never leak into the
# eqasim output: _extract_locations maps them back to the "escort" purpose.
ESCORT_LOCATION_ACTIVITIES = (
    "escort_edu_kindergarten", "escort_edu_school", "escort_edu_university",
    "escort_leisure", "escort_other", "escort_residential", "escort_shop",
)

# Config category vocabulary -> internal activity name. Config uses the short
# category names (edu_kindergarten, ..., shop); the SrV-derived default weights
# below are the output of scripts/derive_escort_location_weights.py
# (srv2023_escort_destination_types.csv) -- regenerate there, never edit here.
ESCORT_CATEGORY_TO_ACTIVITY = {
    "edu_kindergarten": "escort_edu_kindergarten",
    "edu_school": "escort_edu_school",
    "edu_university": "escort_edu_university",
    "leisure": "escort_leisure",
    "other": "escort_other",
    "residential": "escort_residential",
    "shop": "escort_shop",
}
DEFAULT_ESCORT_LOCATIONS_ACTIVITIES = [
    "edu_kindergarten", "edu_school", "edu_university",
    "other", "leisure", "residential", "shop",
]
DEFAULT_ESCORT_LOCATIONS_WEIGHTS = [0.433, 0.199, 0.004, 0.141, 0.113, 0.105, 0.005]
# SrV-derived escort distance factors per destination type (A3; issue #201
# follow-up). Values are the factor_applied column of
# srv2023_escort_distance_factors.csv (weighted-median ratio to the overall
# escort median; thin categories neutralized to 1.0) -- regenerate via
# scripts/derive_escort_location_weights.py, never edit here.
DEFAULT_ESCORT_DISTANCE_FACTORS = [0.618, 0.8339, 1.0, 1.7361, 1.3607, 1.8035, 1.0]
