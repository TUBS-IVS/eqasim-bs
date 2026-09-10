"""synpp stage: secondary distance distributions from MiD 2023 Wege survey (bug D3).

Aliased to synthesis.population.spatial.secondary.distance_distributions in the
popsim_mid workflow. Builds the IDENTICAL output structure as the default ENTD-based
stage (synthesis/population/spatial/secondary/distance_distributions.py) but uses
the German MiD 2023 Wege survey instead of the French ENTD, so secondary activities
in popsim_mid are placed at German empirical distances rather than French ones.

Output structure (consumed by synthesis/population/spatial/secondary/components.py
CustomDistanceSampler, indexed as distributions[mode]["bounds"] and
distributions[mode]["distributions"][bound_index]["cdf"/"values"/"weights"]):

    {
        mode: {
            "bounds": np.ndarray,        # quantile travel_time bin upper-bounds;
                                         # last element is np.inf
            "distributions": [           # one per travel_time bin
                {
                    "cdf":     np.ndarray,  # cumulative weight, ending at 1.0
                    "values":  np.ndarray,  # euclidean_distance in metres, sorted
                    "weights": np.ndarray,  # W_GEW trip weight per observation
                },
                ...
            ],
        },
        ...
    }

The calculate_bounds helper is imported directly from the default stage so that the
quantile-binning logic is identical (not re-implemented).

MiD distance derivation:
    euclidean_distance_m = wegkm_imp [km] * 1000 / DETOUR_FACTOR

where DETOUR_FACTOR = 1.3 (the same ENTD detour factor used throughout this project:
braunschweig/popsim/trips_stage.py, synthesis/population/trips.py, and the MiD
school-distance calibration). This converts the MiD imputed routed trip length to a
straight-line distance in metres, consistent with the units expected by the RDA solver.

Travel time derivation:
    travel_time_s = arrival_time_s - departure_time_s

where arrival_time and departure_time are seconds since midnight built from the MiD
time columns W_AZS/W_AZM and W_SZS/W_SZM via braunschweig.popsim.trips.mid_time_seconds.

Coded-time rescue (issue #160): W_SZS/W_SZM/W_AZS/W_AZM carry the MiD design codes 99
("keine Angabe", ~1% of Wege) and 701 ("bei regelmaessigen beruflichen Wegen nicht
erhoben" -- rbW summary records of REGULAR COMMUTERS, ~10%), which are NOT missing at
random. mid_time_seconds NaNs travel_time for these rows; when this happens, the trip's
travel_time is instead reconstructed from wegmin_imp1 (MiD's own imputed per-trip
duration in minutes -- the same primary source braunschweig.popsim.time_imputation
trusts for the trips.py consumer). Only rows where wegmin_imp1 is ITSELF coded/missing
are dropped. The observed/imputed/dropped rate is logged explicitly (see run()).

Trip weight:
    W_GEW  -- MiD Wege-Gewicht (Fallzahl-normalised expansion weight; mean ~1.0).
    Source: MiD 2023 Handbuch, Kap. 6.1-6.2 (Tab. weight reference table).
    W_GEW = person weight x Hebefaktor (covers mobile non-reporters + excess trips).
    This is the direct analog of person_weight in the ENTD stage.

Trip selection filter (identical to the default stage):
    Trips where BOTH preceding_purpose AND following_purpose are primary activities
    (home, work, education) are excluded. Trips between a primary and a secondary
    location (or two different secondary locations) are included. This matches the
    filter in synthesis/population/spatial/secondary/distance_distributions.py
    lines 43-47 exactly.

Activity purposes:
    purpose (following_purpose) is mapped from MiD W_ZWECK via
    braunschweig.popsim.trips.map_purpose. preceding_purpose is derived by
    the diary-starts-at-home convention (first trip departs from home; each
    subsequent trip's preceding = previous following), matching the same
    convention in braunschweig.popsim.trips.build_trip_table.
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import logging

import numpy as np
import pandas as pd

# Import the helper directly from the default stage so that the binning logic
# is identical. This is the only function we need from it (no CDF helpers are
# separately defined there; the CDF is computed inline via cumsum).
from synthesis.population.spatial.secondary.distance_distributions import (
    calculate_bounds,
)
# Module OBJECT of the same default-stage import above (in addition to the named
# `calculate_bounds` import): needed so validate()'s _HELPER_MODULES tuple can hash its
# source. Checked for an import cycle: this module has no first-party imports at all
# (only numpy/pandas), so binding it here is safe.
from synthesis.population.spatial.secondary import (
    distance_distributions as _default_distance_distributions,
)

# Module OBJECTS (in addition to the named imports below): needed so validate()'s
# _HELPER_MODULES tuple can hash their source via inspect.getsource. escort_pairing is
# imported here even though this file never calls it directly -- trips.map_purpose does,
# under escort_passive_from_adult -- for exactly the reason trips_stage.py hashes it (see
# the _HELPER_MODULES comment below).
from braunschweig import constants as _constants
from braunschweig.popsim import escort_pairing as _escort_pairing
from braunschweig.popsim import time_imputation as _time_imputation
from braunschweig.popsim import trips as _trips
from braunschweig.popsim.time_imputation import WEGMIN_CODE_THRESHOLD
from braunschweig.popsim.trips import (
    DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES, map_mode, map_purpose, mid_time_seconds)

logger = logging.getLogger(__name__)

# Straight-line detour factor: routed_km / straight-line_km.
# Canonical project-wide constant (braunschweig.constants); local alias kept
# for the existing references.
from braunschweig.constants import ROUTED_DETOUR_FACTOR as DETOUR_FACTOR

# synpp's get_stage_hash hashes only THIS file's own source (inspect.getsource of this
# module); every helper whose code shapes this stage's OUTPUT must therefore be folded
# into validate()'s token below, or an edit to it silently reuses the stale cached
# distance distributions on a partial rerun -- the config VALUE the stage declares is
# hashed, but the RULE CODE inside a helper module is not. This stage had NO token at all
# until issue #373 task 1 (see tests/test_synpp_helper_hash_invariant.py, which carried an
# ALLOWED_VIOLATIONS debt entry for it), the same class of gap
# braunschweig.popsim.trips_stage.py closed for the trip build itself after the
# 2026-08-19 cache-invalidation hazard (docs/runs/smoke-control-fit-03101-v2-2026-08-19.yml).
#
# trips carries map_mode/map_purpose, which this stage's ENTIRE output (mode + purpose
# vocabulary, for both the legacy and the by_purpose layer) is built from. time_imputation
# defines WEGMIN_CODE_THRESHOLD, the validity bound the coded-clock-time rescue (Step 3b
# above) uses to decide whether wegmin_imp1 can rescue a trip's travel_time. escort_pairing
# decides WHICH adult leg a passive escort leg (W_ZWECK 13) is paired with under
# escort_passive_from_adult, and therefore which purpose -- and so which distance layer --
# that leg's distance value lands in; this module never calls it directly, trips.map_purpose
# does, exactly as for trips_stage.py. constants owns ROUTED_DETOUR_FACTOR, which scales
# EVERY distance value this stage produces (Step 4 below); the default-stage
# distance_distributions module owns calculate_bounds, the quantile-binning logic this
# stage reuses verbatim (Step 6). Both are OUT-OF-PACKAGE (not under braunschweig.popsim),
# so the own-package-sibling coverage gate (tests/test_synpp_helper_hash_invariant.py)
# does not require them, but they shape the output just as directly as the own-package
# helpers above and are checked for import cycles (neither has any first-party import).
_HELPER_MODULES = (
    _trips,
    _time_imputation,
    _escort_pairing,
    _constants,
    _default_distance_distributions,
)
# Imported LAZILY inside run()/configure()/execute() (to avoid an unconditional import cost
# when the shop/leisure/other subtype splits are off, and -- for config_keys -- a heavy
# top-level import of the popsim stage package), so they are hashed by dotted module name via
# importlib rather than as a bound module object, mirroring trips_stage.py's own deferred
# tuple. mid.load_mid_wege is this stage's only data source; mid.donor is named SEPARATELY
# from the mid package because mid/__init__.py only RE-EXPORTS load_mid_wege
# (`from .donor import load_mid_wege`) -- inspect.getsource of the package object hashes
# only __init__.py's own text (the import statement), never donor.py's function body where
# load_mid_wege is actually defined, mirroring the identical mid.donor entry
# braunschweig.popsim.completed_donor.py already carries for the same transitive reason.
# purpose_subtype/shop_subtype define the W_ZWD subtype groupings the leisure/shop/other
# subtype splits are built from; config_keys is the shared home of the four purpose-package
# config keys this stage declares.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.popsim.mid",
    "braunschweig.popsim.mid.donor",
    "braunschweig.popsim.purpose_subtype",
    "braunschweig.popsim.shop_subtype",
    "braunschweig.popsim.stage.config_keys",
)


def validate(context):
    """synpp validation token: md5 over the helper modules above.

    Same mechanism and boundary semantics as ``braunschweig.popsim.stage.validate()``
    (the single canonical statement) and ``braunschweig.popsim.trips_stage.validate()``
    (which closes the identical own-package-sibling gap for the trip build); kept minimal
    here because this stage's helper surface is small. A deferred module that fails to
    import raises rather than being skipped -- dropping it would keep the stale cache
    alive exactly when the code is broken.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_module = importlib.import_module(module_name)
            deferred_source = inspect.getsource(deferred_module)
        except Exception as error:
            raise RuntimeError(
                f"distance_distributions validate(): cannot hash the deferred helper "
                f"module {module_name!r} ({type(error).__name__}: {error}); it must not "
                "be skipped, because skipping it would silently reuse stale cached output."
            ) from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()

# Primary activity types — trips where BOTH ends are primary are excluded.
# Matches the default stage exactly (synthesis/population/spatial/secondary/
# distance_distributions.py, line 43).
PRIMARY_ACTIVITIES = frozenset(["home", "work", "education"])

# Bin size: number of travel-time observations per quantile bin.
# Must match the default stage (line 51: bin_size = 200).
BIN_SIZE = 200

# MiD columns required to build the distributions.
REQUIRED_COLUMNS = ("H_ID", "P_ID", "W_ID", "W_ZWECK", "hvm_imp",
                    "wegkm_imp", "W_SZS", "W_SZM", "W_AZS", "W_AZM", "W_GEW")

# Warn threshold for the coded-clock-time-AND-invalid-wegmin_imp1 drop rate
# (see Step 3 below). A rate above this almost always signals a broken
# wegmin_imp1 join/load rather than genuinely unrecoverable trips (issue #160).
CODED_TIME_DROP_WARN_RATE = 0.02

# Optional columns kept when present:
# - W_ZWD (Wegezweck-Detail): needed by the shop daily/non-daily split (Task 5) and by
#   the leisure/other subtype splits below (Task 3, issue #127).
# - W_ZWECK (raw MiD purpose code): dropped by default because map_purpose() already
#   derives "purpose"/"following_purpose" from it and nothing downstream of Step 5
#   needed the raw code -- until the other_subtype_split below, which must distinguish
#   "other_errand" (W_ZWECK=5) from "other_escort" (W_ZWECK=6) even though BOTH map to
#   the same following_purpose == "other" (issue #127, Task 3).
_OPTIONAL_COLUMNS = ("W_ZWD", "W_ZWECK")


def _build_preceding_purpose(wege: pd.DataFrame) -> pd.Series:
    """Derive preceding_purpose from following_purpose per (H_ID, P_ID) chain.

    Mirrors build_trip_table's diary-starts-at-home convention: the first trip
    of each person departs from home; each subsequent trip departs from the
    destination of the previous trip.

    Parameters
    ----------
    wege:
        DataFrame with ``H_ID``, ``P_ID``, ``W_ID`` (trip sequence), and
        ``following_purpose`` (eqasim activity at the trip destination).

    Returns
    -------
    pd.Series
        ``preceding_purpose`` aligned to the wege index.
    """
    # Sort within (H_ID, P_ID) by W_ID to get the diary order.
    sorted_idx = wege.sort_values(["H_ID", "P_ID", "W_ID"]).index
    sorted_wege = wege.loc[sorted_idx]

    preceding = (
        sorted_wege
        .groupby(["H_ID", "P_ID"])["following_purpose"]
        .shift(1)
    )
    # First trip of each person departs from home.
    is_first = sorted_wege.groupby(["H_ID", "P_ID"]).cumcount() == 0
    preceding.loc[is_first] = "home"

    # Re-align to the original index order.
    return preceding.reindex(wege.index)


def _build_mode_distributions(df: pd.DataFrame) -> dict:
    """Per-mode quantile travel-time bins + W_GEW-weighted euclidean-distance CDFs.

    This is the exact legacy Step-6 logic, factored out so it can be applied to
    the whole frame (by_purpose=False) or to a per-purpose sub-frame
    (by_purpose=True).

    Parameters
    ----------
    df:
        Already-prepared DataFrame with columns: ``mode``, ``travel_time``,
        ``distance``, ``weight`` (= W_GEW), ``preceding_purpose``,
        ``following_purpose``. Primary-only trips must already be filtered out.

    Returns
    -------
    dict
        ``{mode: {"bounds": np.ndarray, "distributions": [{"cdf", "values",
        "weights"}, ...]}}`` — the exact structure consumed by
        CustomDistanceSampler.
    """
    distributions = {}

    for mode in df["mode"].unique():
        mode_df = df[df["mode"] == mode]

        bounds = calculate_bounds(mode_df["travel_time"].values, BIN_SIZE)
        distributions[mode] = dict(bounds=np.array(bounds), distributions=[])

        for lower_bound, upper_bound in zip([-np.inf] + bounds[:-1], bounds):
            bin_df = mode_df[
                (mode_df["travel_time"] > lower_bound) &
                (mode_df["travel_time"] <= upper_bound)
            ]

            values = bin_df["distance"].values
            weights = bin_df["weight"].values

            sorter = np.argsort(values)
            values = values[sorter]
            weights = weights[sorter]

            cdf = np.cumsum(weights)
            # Guard against empty bins: empty bins raise IndexError on cdf[-1],
            # zero-weight bins raise ZeroDivisionError. This guard handles both.
            cdf = cdf / cdf[-1] if len(cdf) and cdf[-1] > 0 else cdf

            distributions[mode]["distributions"].append(
                dict(cdf=cdf, values=values, weights=weights)
            )

    logger.info(
        "[popsim.distance_distributions] built distributions for %d modes: %s",
        len(distributions),
        sorted(distributions.keys()),
    )

    return distributions


def _build_leisure_unspecified_layer(df: pd.DataFrame) -> dict | None:
    """Per-mode distributions of the W_ZWECK-10 leisure legs, or None when empty.

    The fifth leisure subtype (issue #373, ADR-0115). Unlike the four W_ZWD
    groups this one is defined by the RAW MiD purpose code -- W_ZWECK 10
    ("anderer Zweck", ``purpose_subtype.LEISURE_UNSPECIFIED_ZWECK``) legs become
    leisure through ``w_zweck_10_as_leisure`` but never carry a leisure W_ZWD
    detail code, so no W_ZWD grouping can reach them.

    Parameters
    ----------
    df:
        The already-prepared, primary-only-filtered frame of Step 5 (columns
        ``mode``, ``travel_time``, ``distance``, ``weight``,
        ``following_purpose``, plus the raw ``W_ZWECK``).

    Returns
    -------
    dict or None
        ``{mode: {"bounds", "distributions"}}`` for the W_ZWECK-10 leisure
        legs, or None when there are none (the caller then adds no layer).

    Raises
    ------
    ValueError
        When ``W_ZWECK`` is absent: the column DEFINES the group, so the layer
        cannot be built and must not be skipped silently. Defensive -- W_ZWECK
        is in REQUIRED_COLUMNS and is kept through Step 5 via _OPTIONAL_COLUMNS.

    Side effects
    ------------
    Logs the share of the leisure universe this group takes (INFO), and warns
    when the group is empty -- the fold being off is the expected cause, and a
    silently missing layer would send every code-10 leg to the aggregate
    fallback without any trace (CLAUDE.md "Fallback transparency").
    """
    if "W_ZWECK" not in df.columns:
        raise ValueError(
            "[popsim.distance_distributions] leisure_unspecified_subtype=True needs the "
            "raw W_ZWECK column on the Wege frame (it defines the group), but it is absent; "
            "the layer cannot be built and must not be skipped silently."
        )

    from braunschweig.popsim.purpose_subtype import (LEISURE_UNSPECIFIED_GROUP,
                                                     LEISURE_UNSPECIFIED_ZWECK)

    leisure_df = df[df["following_purpose"] == "leisure"]
    unspecified_df = leisure_df[leisure_df["W_ZWECK"].isin(LEISURE_UNSPECIFIED_ZWECK)]
    # An empty leisure universe has no rate, so say that instead of printing
    # "nan%" -- a NaN in a rate line reads as a broken computation and hides the
    # real finding (there were no leisure legs to split at all).
    rate_text = (
        f"({100.0 * len(unspecified_df) / len(leisure_df):.1f}%)" if len(leisure_df)
        else "(no leisure legs)"
    )
    logger.info(
        "[popsim.distance_distributions] leisure subtype %s: %d/%d leisure legs %s",
        LEISURE_UNSPECIFIED_GROUP, len(unspecified_df), len(leisure_df), rate_text,
    )
    if not len(unspecified_df):
        logger.warning(
            "[popsim.distance_distributions] leisure subtype %s: 0 legs -- is "
            "w_zweck_10_as_leisure on? The layer is not built.", LEISURE_UNSPECIFIED_GROUP)
        return None
    return _build_mode_distributions(unspecified_df)


def run(mid_wege: pd.DataFrame, *, by_purpose: bool = False,
        shop_daily_split: bool = False,
        leisure_subtype_split: bool = False,
        other_subtype_split: bool = False,
        escort_purpose: bool = False,
        explicit_round_trip_purposes: bool = True,
        escort_passive_education: bool = False,
        w_zweck_10_as_leisure: bool = False,
        escort_passive_from_adult: bool = False,
        passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
        exclude_rbw_legs: bool = False,
        drop_leading_arrive_home_leg: bool = False,
        codeplan_sentinels: bool = False,
        leisure_unspecified_subtype: bool = False,
        weekday_legs_only: bool = False) -> dict:
    """Build secondary distance distributions from the MiD 2023 Wege survey.

    This is the pure computational core, factored out of execute() so that
    tests can call it directly without a synpp context.

    Parameters
    ----------
    mid_wege:
        MiD 2023 Wege DataFrame with at least the columns in REQUIRED_COLUMNS.
        All rows are used; caller is responsible for any pre-filtering (e.g.
        restricting to a specific Bundesland). The weight column W_GEW is the
        MiD Wege-Gewicht (Fallzahl-normalised expansion weight).
    by_purpose:
        When False (default), returns the legacy ``{mode: ...}`` structure —
        byte-identical to the pre-refactor output.
        When True, returns ``{purpose: {mode: ...}}`` where purpose is the
        eqasim secondary purpose (``shop``/``leisure``/``other``/``work``/
        ``education``) from ``map_purpose``.
    shop_daily_split:
        When True (and ``by_purpose=True`` and ``W_ZWD`` is present in the
        input frame), adds ``shop_daily`` and ``shop_non_daily`` keys to the
        purpose layer built from the MiD W_ZWD detail codes (501 = daily;
        502/503/504/505 = non-daily). The aggregate ``shop`` key is kept as a
        fallback. If ``W_ZWD`` is absent, a warning is logged and the split is
        skipped silently (no KeyError). Pass False (default) to preserve the
        current behaviour.
    leisure_subtype_split:
        When True (and ``by_purpose=True`` and ``W_ZWD`` is present), adds one key per
        ``purpose_subtype.LEISURE_GROUPS`` entry (``leisure_local``, ``leisure_visit``,
        ``leisure_activity``, ``leisure_excursion``) built from the following_purpose
        == "leisure" legs, grouped by their W_ZWD detail code. The aggregate
        ``leisure`` key is kept as a fallback. If ``W_ZWD`` is absent, a warning is
        logged and the split is skipped silently (mirrors ``shop_daily_split``).
    other_subtype_split:
        When True (and ``by_purpose=True``), adds ``other_escort`` (following_purpose
        == "other" legs with the raw W_ZWECK code in ``purpose_subtype.
        OTHER_ESCORT_ZWECK``; needs only W_ZWECK, not W_ZWD) and, when ``W_ZWD`` is
        also present, ``other_errand_short``/``other_errand_long`` (following_purpose
        == "other" legs with W_ZWECK in ``purpose_subtype.OTHER_ERRAND_ZWECK`` grouped
        by ``purpose_subtype.OTHER_ERRAND_GROUPS``). The aggregate ``other`` key is
        kept as a fallback (it also serves an ``other_rest`` group). If W_ZWD is
        absent, only the errand short/long split is skipped (with a warning);
        ``other_escort`` is still built since it does not depend on W_ZWD.
    escort_purpose:
        When True (issue #201), maps W_ZWECK {6, 13} legs to the dedicated
        "escort" purpose so the purpose layer gains an "escort" key; requires
        by_purpose for a dedicated layer, harmless otherwise.
    escort_passive_education:
        When True (issue #256), the passive escort leg (W_ZWECK 13) maps to
        "education" instead of "escort" (forwarded to ``map_purpose``), so the
        "escort" layer holds only the active W_ZWECK-6 legs. Requires
        ``escort_purpose=True`` (enforced by ``map_purpose``); requires
        by_purpose for a dedicated layer, harmless otherwise. Default False
        keeps the OFF path byte-identical.
    w_zweck_10_as_leisure:
        When True (issue #373, ADR-0111), maps W_ZWECK 10 ("anderer Zweck") to
        the ``"leisure"`` purpose instead of ``"other"`` (forwarded to
        ``map_purpose``), following MiD's own hwzweck1 fold. Default False
        keeps the OFF path byte-identical.
    escort_passive_from_adult:
        When True (issue #372, ADR-0112), a PAIRED passive escort leg (W_ZWECK
        13) takes the purpose derived from the accompanying adult's W_ZWECK
        (forwarded to ``map_purpose``), so those legs land in the distance
        layer of the purpose the plan actually gives them instead of all in
        ``education``. Requires ``escort_purpose=True`` and the MiD Wege
        columns the pairing needs (``H_ID``/``P_ID``/``W_ID``/``W_SZS``/
        ``W_SZM``/``HP_ALTER``; ``load_mid_wege`` loads all of them). Default
        False keeps the OFF path byte-identical.
    passive_pair_max_gap_minutes:
        Maximum |departure-time gap| in MINUTES for that pairing; inert unless
        ``escort_passive_from_adult`` is True.
    exclude_rbw_legs:
    drop_leading_arrive_home_leg:
        Issue #373 task 2 (ruling C-R20/C-R21). Restrict the passive-escort
        pairing's CANDIDATE UNIVERSE (both the passive legs it may pair and
        the adult legs it may pair them with) to
        ``trips.legs_kept_by_the_trip_build(mid_wege, exclude_rbw_legs=...,
        drop_leading_arrive_home_leg=...)``, forwarded to ``map_purpose`` as
        ``pairing_candidate_mask``. The trip build itself already drops these
        legs BEFORE it pairs (they never reach ``map_purpose`` there), so its
        pairing naturally agrees with these flags; this stage keeps every leg
        for its OWN distance pool (which is UNAFFECTED by these two flags --
        they only narrow which legs the PAIRING considers), so without this
        it could pair a passive leg to a leg the plan never realises (an
        excluded rbW summary leg or a dropped leading arrive-home leg),
        landing the leg's distance under a purpose the plan does not give it.
        Both default False (matching every other flag's code default in this
        function; the byte-identical direct-call behaviour, with the pairing
        considering every leg); ``configure()``/``execute()`` read the SAME
        shared ``KEY_EXCLUDE_RBW_LEGS`` / ``KEY_DROP_LEADING_ARRIVE_HOME_LEG``
        constants ``braunschweig.popsim.trips_stage`` declares, whose
        production default (``configs/base_bs.yml``) is True for both, so a
        full pipeline run resolves the SAME value the trip build uses. Inert
        unless ``escort_passive_from_adult`` is also True.
    codeplan_sentinels:
        When True (issue #242 Task 5, ADR-0113), the leisure_subtype_split /
        other_subtype_split donor pools above are built from
        ``purpose_subtype.leisure_spec(True)`` / ``other_errand_spec(True))``
        instead of the raw ``LEISURE_GROUPS`` / ``OTHER_ERRAND_GROUPS``
        constants, so W_ZWD 799 ("Freizeit k.A.") and 699 ("Erledigung k.A.")
        -- NO-DETAIL codeplan codes -- are excluded from the
        "leisure_activity" / "other_errand_long" donor pool the SAME way
        ``braunschweig.synthesis.locations.secondary_chainsolvers``'s
        deciders exclude them from ESTIMATION. Both consumers read the SAME
        ``purpose_subtype_codeplan_sentinels`` config key and must resolve the
        same value, or a leg the decider labels "leisure_activity" /
        "other_errand_long" would draw its distance from a donor pool that
        still includes the excluded legs. Default False keeps this function's
        OFF path byte-identical to before issue #242 Task 5; inert unless
        ``leisure_subtype_split`` or ``other_subtype_split`` is also True.
    leisure_unspecified_subtype:
        When True (issue #373, ADR-0115), adds the FIFTH leisure subtype layer
        ``leisure_unspecified``: the legs whose RAW MiD purpose code is in
        ``purpose_subtype.LEISURE_UNSPECIFIED_ZWECK`` (W_ZWECK 10, "anderer
        Zweck"). Those legs are leisure only under ``w_zweck_10_as_leisure``
        and never carry a leisure W_ZWD detail code, so the four W_ZWD group
        layers above cannot reach them and they would otherwise land in the
        aggregate "leisure" fallback layer alone. The group is therefore
        defined by W_ZWECK, not by W_ZWD -- it is built whether or not the
        W_ZWD column is present (mirroring ``other_escort``). Requires
        ``leisure_subtype_split`` (the subtype layers only exist there) and,
        to be non-empty, ``w_zweck_10_as_leisure``; ``configure()`` refuses
        that latter contradiction before any stage runs, but only while
        ``secondary_leisure_subtype_split`` is on -- with the split off this
        flag is inert and its value cannot contradict anything. An empty group
        is logged as a WARNING rather than skipped silently. Default False
        keeps this function's OFF path byte-identical.
    weekday_legs_only:
        When True (issue #373, ADR-0116), EVERY layer this function builds is
        estimated on the WEEKDAY DIARY universe -- the legs of
        ``trips.weekday_diary_leg_mask``: the reporting day is in the
        PopulationSim seed's own day filter (``trips.WEEKDAY_DIARY_KERNWO``) and
        the leg is not an rbW summary record. The synthetic population IS a
        weekday and the committed MiD reference tables measure that same
        universe, so without this the layers describe a mixture of weekdays and
        weekends (ADR-0115 "Two universes"). Applied in Step 0a, BEFORE the
        pairing mask of Step 0b and before the purpose mapping, so the aggregate,
        the per-purpose and every subtype layer share ONE universe. Requires the
        MiD Wege columns ``kernwo`` and ``W_RBW``; they are deliberately NOT in
        ``REQUIRED_COLUMNS`` (they are needed only under this flag), and the
        helper raises naming the missing column. Default False keeps this
        function's OFF path byte-identical; ``configure()`` / ``execute()`` read
        the shared ``KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY`` constant (production
        default True), which
        ``braunschweig.synthesis.locations.secondary_chainsolvers`` declares too
        so the deciders' ESTIMATION universe and these layers' donor universe
        cannot diverge.

    Returns
    -------
    dict
        When ``by_purpose=False``: ``{mode: {"bounds", "distributions"}}`` —
        the EXACT structure consumed by CustomDistanceSampler (see module
        docstring).
        When ``by_purpose=True``: ``{purpose: {mode: {"bounds",
        "distributions"}}}`` — one inner dict per purpose present in the
        filtered frame.
    """
    # Guard against invalid flag combination: shop_daily_split only takes
    # effect inside the by_purpose branch, so using it without by_purpose
    # silently loses the shop distance split.
    if shop_daily_split and not by_purpose:
        raise ValueError(
            "secondary_shop_daily_split=True requires secondary_distance_by_purpose=True "
            "(the shop daily/non-daily distance layers live inside the per-purpose layer)."
        )
    if leisure_subtype_split and not by_purpose:
        raise ValueError(
            "secondary_leisure_subtype_split=True requires secondary_distance_by_purpose=True "
            "(the leisure subtype distance layers live inside the per-purpose layer)."
        )
    if other_subtype_split and not by_purpose:
        raise ValueError(
            "secondary_other_subtype_split=True requires secondary_distance_by_purpose=True "
            "(the other subtype distance layers live inside the per-purpose layer)."
        )

    missing = [c for c in REQUIRED_COLUMNS if c not in mid_wege.columns]
    if missing:
        raise ValueError(
            f"[popsim.distance_distributions] MiD Wege frame is missing "
            f"required columns: {missing}. "
            f"Available columns: {list(mid_wege.columns[:20])} ..."
        )

    df = mid_wege.copy()

    # --- Step 0a: the weekday diary universe (issue #373, ADR-0116). ----------
    # Every layer this stage builds describes ONE synthetic weekday, so the donor legs are
    # the seed's weekday diaries without rbW summary records -- the SAME universe the
    # committed MiD reference tables measure (scripts/extract_mid_w_zwd_groups.py, whose
    # filter calls the very same helper). Applied BEFORE the pairing mask of Step 0b and
    # before the purpose mapping of Step 1, so the aggregate, the per-purpose and every
    # subtype layer are built from one universe rather than a half-filtered mixture. The
    # helper logs the kept rate with both drop reasons and raises on an empty result or a
    # missing kernwo/W_RBW column (those two are required ONLY on this path, which is why
    # they are not in REQUIRED_COLUMNS).
    if weekday_legs_only:
        df = _trips.restrict_to_weekday_diary_legs(
            df, log_tag="[popsim.distance_distributions]")

    # --- Step 0b: restrict the passive-escort pairing's candidate universe -----
    # (issue #373 task 2, ruling C-R20/C-R21). Built BEFORE map_purpose (which does
    # the actual pairing) and BEFORE the primary/secondary filter below, from the
    # SAME helper the trip build's own leg-drop step uses -- see the run() docstring's
    # exclude_rbw_legs/drop_leading_arrive_home_leg entry for the full rationale. A
    # mask is only constructed when at least one flag is True, so the default
    # (both False) leaves pairing_candidate_mask at None and map_purpose takes its
    # byte-identical OFF path (no mask ever built or passed).
    pairing_candidate_mask = None
    if exclude_rbw_legs or drop_leading_arrive_home_leg:
        kept = _trips.legs_kept_by_the_trip_build(
            df, exclude_rbw_legs=exclude_rbw_legs,
            drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        )
        pairing_candidate_mask = pd.Series(df.index.isin(kept.index), index=df.index)
        n_total_legs = len(df)
        n_candidate_legs = int(pairing_candidate_mask.sum())
        logger.info(
            "[popsim.distance_distributions] pairing_candidate_mask "
            "(exclude_rbw_legs=%s, drop_leading_arrive_home_leg=%s): %d/%d legs "
            "(%.1f%%) form the passive-escort pairing's candidate universe; the "
            "DISTANCE POOL itself is unaffected by this mask -- every leg still "
            "contributes its distance under whichever purpose it resolves to.",
            exclude_rbw_legs, drop_leading_arrive_home_leg,
            n_candidate_legs, n_total_legs,
            100.0 * n_candidate_legs / n_total_legs if n_total_legs else 0.0,
        )

    # --- Step 1: map mode and purpose from MiD codes. ----------------------
    df = map_mode(map_purpose(
        df, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
        pairing_candidate_mask=pairing_candidate_mask,
    ))
    # following_purpose = destination activity.
    df["following_purpose"] = df["purpose"]

    # --- Step 2: derive preceding_purpose (diary-starts-at-home). ----------
    df["preceding_purpose"] = _build_preceding_purpose(df)

    # --- Step 3: compute travel_time in seconds. ----------------------------
    df["departure_time"] = mid_time_seconds(df, "W_SZS", "W_SZM")
    df["arrival_time"] = mid_time_seconds(df, "W_AZS", "W_AZM")
    df["travel_time"] = df["arrival_time"] - df["departure_time"]

    # Repair negative travel_time from midnight crossing: add 24 h.
    midnight_cross = df["travel_time"] < 0
    df.loc[midnight_cross, "travel_time"] += 24 * 3600

    # --- Step 3b: rescue coded clock times via wegmin_imp1 (issue #160). ---
    # W_SZS/W_SZM/W_AZS/W_AZM carry the MiD design codes 99 ("keine Angabe",
    # item non-response, ~1% of Wege) and 701 ("bei regelmaessigen beruflichen
    # Wegen nicht erhoben" -- rbW summary records of REGULAR COMMUTERS, ~10%),
    # which are NOT missing at random (see braunschweig.popsim.time_imputation
    # module docstring). mid_time_seconds NaNs travel_time for these rows;
    # blindly dropping them (the previous behaviour) silently removed almost
    # all commuter trips from the secondary distance distributions, biasing
    # them towards non-commute travel.
    #
    # Unlike the trips.py consumer (braunschweig.popsim.time_imputation.
    # impute_chain_times), this aggregate stage only needs the trip's
    # DURATION as a quantile-binning key -- never an absolute clock time --
    # so there is no need to reconstruct a full day schedule from empirical
    # anchor/activity-duration pools. Instead we reuse the SAME primary data
    # source stage A trusts for a trip's own duration: wegmin_imp1, MiD's own
    # imputed per-trip duration in minutes (audited as fully populated and
    # code-free for rbW rows; see time_imputation.py). Rows whose
    # wegmin_imp1 is ITSELF coded/missing cannot be reconstructed and are
    # dropped -- this should be rare, and the rate is logged below so a high
    # drop rate (e.g. a broken wegmin_imp1 load) is never silent.
    coded_clock_time = df["travel_time"].isna()
    if "wegmin_imp1" in df.columns:
        wegmin_minutes = df["wegmin_imp1"].astype(float)
        wegmin_is_valid = (
            wegmin_minutes.notna()
            & (wegmin_minutes > 0)
            & (wegmin_minutes < WEGMIN_CODE_THRESHOLD)
        )
        rescued = coded_clock_time & wegmin_is_valid
        df.loc[rescued, "travel_time"] = wegmin_minutes.loc[rescued] * 60.0
    else:
        # wegmin_imp1 not supplied by the caller: no rescue is possible, so
        # every coded-clock-time row is dropped below. Loud because this
        # column is always present in production (mid.MID_WEGE_REQUIRED_COLS).
        rescued = pd.Series(False, index=df.index)
        if coded_clock_time.any():
            logger.warning(
                "[popsim.distance_distributions] %d rows have coded/missing clock "
                "times (W_SZS/W_AZS design codes 99/701) but 'wegmin_imp1' is not "
                "present in the input frame; none of these rows can be rescued and "
                "all will be dropped from the distance distributions.",
                int(coded_clock_time.sum()),
            )

    n_total_time = len(df)
    n_rescued = int(rescued.sum())
    n_observed = n_total_time - int(coded_clock_time.sum())
    n_dropped_time = int(coded_clock_time.sum()) - n_rescued
    logger.info(
        "[popsim.distance_distributions] travel_time source: observed (valid "
        "clock times) %d/%d (%.1f%%); imputed from wegmin_imp1 (coded W_SZS/"
        "W_AZS, design codes 99/701) %d/%d (%.1f%%); dropped (coded clock time "
        "AND invalid/missing wegmin_imp1) %d/%d (%.1f%%).",
        n_observed, n_total_time, 100.0 * n_observed / n_total_time if n_total_time else 0.0,
        n_rescued, n_total_time, 100.0 * n_rescued / n_total_time if n_total_time else 0.0,
        n_dropped_time, n_total_time, 100.0 * n_dropped_time / n_total_time if n_total_time else 0.0,
    )
    if n_total_time > 0 and (n_dropped_time / n_total_time) > CODED_TIME_DROP_WARN_RATE:
        logger.warning(
            "[popsim.distance_distributions] %.1f%% of trips were dropped because "
            "BOTH the clock time and wegmin_imp1 were coded/missing -- this exceeds "
            "the %.0f%% expected-rare threshold and likely signals a data or join "
            "problem rather than genuinely unrecoverable trips.",
            100.0 * n_dropped_time / n_total_time,
            100.0 * CODED_TIME_DROP_WARN_RATE,
        )

    # Clamp to positive (zero-duration trips are kept; negative after repair
    # cannot occur but guard against data errors). Rows still NaN here are the
    # unrescued coded-time rows counted/logged as "dropped" above.
    df = df[df["travel_time"] >= 0].copy()

    # --- Step 4: compute euclidean_distance in metres from wegkm_imp. ------
    # wegkm_imp is the MiD imputed routed trip length in kilometres.
    # Dividing by DETOUR_FACTOR gives the straight-line distance in km; x1000 -> m.
    df["distance"] = df["wegkm_imp"].astype(float) * 1000.0 / DETOUR_FACTOR

    # --- Step 5: select columns needed and filter primary-only trips. ------
    # Keep W_ZWD when present: needed by Task 5 (shop daily split) and
    # by the byte-identical test which replicates this selection exactly.
    keep_cols = ["mode", "travel_time", "distance", "W_GEW",
                 "preceding_purpose", "following_purpose"]
    for opt_col in _OPTIONAL_COLUMNS:
        if opt_col in df.columns:
            keep_cols.append(opt_col)
    df = df[keep_cols].rename(columns={"W_GEW": "weight"})

    # Replicate the default stage filter exactly (lines 43-47):
    # exclude trips where BOTH ends are primary activities.
    is_primary_both = (
        df["preceding_purpose"].isin(PRIMARY_ACTIVITIES) &
        df["following_purpose"].isin(PRIMARY_ACTIVITIES)
    )
    n_before = len(df)
    df = df[~is_primary_both]
    n_after = len(df)
    n_excluded = n_before - n_after

    logger.info(
        "[popsim.distance_distributions] total trips: %d; "
        "primary-only excluded: %d (%.1f%%); secondary included: %d",
        n_before, n_excluded,
        100.0 * n_excluded / n_before if n_before > 0 else 0.0,
        n_after,
    )

    # --- Step 6: build per-mode (or per-purpose × mode) distributions. ------
    if not by_purpose:
        # Legacy path: build over the whole filtered frame.
        # _build_mode_distributions replicates the exact Step-6 logic that was
        # inlined here before the refactor; by_purpose=False is byte-identical.
        return _build_mode_distributions(df)

    # Purpose layer: split by following_purpose, then build per-mode within each.
    out = {}
    for purpose in df["following_purpose"].unique():
        pdf = df[df["following_purpose"] == purpose]
        if len(pdf) == 0:
            continue
        out[purpose] = _build_mode_distributions(pdf)

    logger.info(
        "[popsim.distance_distributions] purpose-layer built for purposes: %s",
        sorted(out.keys()),
    )

    # --- Step 7: shop daily / non-daily sub-distributions (Task 5). ----------
    # When both flags are set and W_ZWD survived the column selection, build
    # separate distributions for daily (501) and non-daily (502-505) shop legs.
    # The aggregate "shop" key is KEPT so downstream callers without the split
    # can still use it as a fallback.
    if shop_daily_split:
        if "W_ZWD" not in df.columns:
            logger.warning(
                "[popsim.distance_distributions] shop_daily_split=True but "
                "W_ZWD column is absent from the Wege frame; skipping the "
                "daily/non-daily split."
            )
        else:
            from braunschweig.popsim.shop_subtype import (
                SHOP_DAILY_W_ZWD,
                SHOP_NONDAILY_W_ZWD,
            )
            shop_df = df[df["following_purpose"] == "shop"]
            daily_df = shop_df[shop_df["W_ZWD"].isin(SHOP_DAILY_W_ZWD)]
            nondaily_df = shop_df[shop_df["W_ZWD"].isin(SHOP_NONDAILY_W_ZWD)]
            if len(daily_df):
                out["shop_daily"] = _build_mode_distributions(daily_df)
            if len(nondaily_df):
                out["shop_non_daily"] = _build_mode_distributions(nondaily_df)
            logger.info(
                "[popsim.distance_distributions] shop split: "
                "daily=%d legs, non_daily=%d legs",
                len(daily_df),
                len(nondaily_df),
            )

    # --- Step 8: leisure subtype sub-distributions (Task 3, issue #127). -----
    # Splits following_purpose == "leisure" legs by their W_ZWD detail code into
    # the groups defined in purpose_subtype.leisure_spec(codeplan_sentinels)
    # (local/visit/activity/excursion; see codeplan_sentinels above for the
    # 799 "Freizeit k.A." sentinel treatment). The aggregate "leisure" key is
    # KEPT so downstream callers without the split can still use it as a
    # fallback (mirrors the shop split above).
    if leisure_subtype_split:
        if "W_ZWD" not in df.columns:
            logger.warning(
                "[popsim.distance_distributions] leisure_subtype_split=True but "
                "W_ZWD column is absent from the Wege frame; skipping the "
                "leisure subtype split."
            )
        else:
            from braunschweig.popsim.purpose_subtype import leisure_spec

            # leisure_spec(codeplan_sentinels) selects LEISURE_SPEC_CODEPLAN
            # (799 "Freizeit k.A." excluded as a NO-DETAIL sentinel) or the
            # unchanged LEISURE_SPEC by identity -- see the codeplan_sentinels
            # docstring parameter above.
            leisure_groups = leisure_spec(codeplan_sentinels).groups
            leisure_df = df[df["following_purpose"] == "leisure"]
            for group_name, codes in leisure_groups.items():
                group_df = leisure_df[leisure_df["W_ZWD"].isin(codes)]
                logger.info(
                    "[popsim.distance_distributions] leisure subtype %s: %d legs",
                    group_name, len(group_df),
                )
                if len(group_df):
                    out[group_name] = _build_mode_distributions(group_df)

        # --- Step 8b: the fifth leisure subtype (issue #373, ADR-0115). ------
        # Deliberately OUTSIDE the W_ZWD branch above: this group is defined by
        # the RAW W_ZWECK code, not by a detail code, so -- exactly like
        # "other_escort" in Step 9 -- it is still built when W_ZWD is absent.
        # It differs from Step 9 in the other direction: Step 9 warns and SKIPS
        # when W_ZWECK is absent, because its split is one optional refinement of
        # a layer ("other") that exists either way, whereas here the flag being
        # on IS the request for this layer and there is no W_ZWD path that could
        # still produce it -- so a missing W_ZWECK RAISES rather than silently
        # leaving every code-10 leg on the aggregate fallback.
        if leisure_unspecified_subtype:
            from braunschweig.popsim.purpose_subtype import LEISURE_UNSPECIFIED_GROUP

            unspecified_distributions = _build_leisure_unspecified_layer(df)
            if unspecified_distributions is not None:
                out[LEISURE_UNSPECIFIED_GROUP] = unspecified_distributions

    # --- Step 9: other subtype sub-distributions (Task 3, issue #127). -------
    # other_escort only needs the raw W_ZWECK code (Bringen/Holen has no W_ZWD
    # detail); other_errand_short/long additionally need W_ZWD. The aggregate
    # "other" key is KEPT as a fallback (it also serves an other_rest group).
    if other_subtype_split:
        if "W_ZWECK" not in df.columns:
            logger.warning(
                "[popsim.distance_distributions] other_subtype_split=True but "
                "W_ZWECK column is absent from the Wege frame; skipping the "
                "other subtype split entirely (other_escort also needs it)."
            )
        else:
            from braunschweig.popsim.purpose_subtype import (
                OTHER_ERRAND_ZWECK,
                OTHER_ESCORT_ZWECK,
                other_errand_spec,
            )

            other_df = df[df["following_purpose"] == "other"]

            if escort_purpose and len(other_df[other_df["W_ZWECK"].isin(OTHER_ESCORT_ZWECK)]) == 0:
                logger.info(
                    "[popsim.distance_distributions] escort_purpose ON: no W_ZWECK-6 "
                    "legs remain under following_purpose == 'other'; the internal "
                    "'other_escort' subtype layer is expectedly empty and skipped."
                )

            escort_df = other_df[other_df["W_ZWECK"].isin(OTHER_ESCORT_ZWECK)]
            logger.info(
                "[popsim.distance_distributions] other subtype other_escort: %d legs",
                len(escort_df),
            )
            if len(escort_df):
                out["other_escort"] = _build_mode_distributions(escort_df)

            if "W_ZWD" not in df.columns:
                logger.warning(
                    "[popsim.distance_distributions] other_subtype_split=True but "
                    "W_ZWD column is absent from the Wege frame; skipping the "
                    "other_errand_short/long split (other_escort was still built "
                    "since it does not depend on W_ZWD)."
                )
            else:
                # other_errand_spec(codeplan_sentinels) selects
                # OTHER_ERRAND_SPEC_CODEPLAN (699 "Erledigung k.A." excluded as
                # a NO-DETAIL sentinel) or the unchanged OTHER_ERRAND_SPEC by
                # identity -- see the codeplan_sentinels docstring parameter.
                other_errand_groups = other_errand_spec(codeplan_sentinels).groups
                errand_df = other_df[other_df["W_ZWECK"].isin(OTHER_ERRAND_ZWECK)]
                for group_name, codes in other_errand_groups.items():
                    group_df = errand_df[errand_df["W_ZWD"].isin(codes)]
                    logger.info(
                        "[popsim.distance_distributions] other subtype %s: %d legs",
                        group_name, len(group_df),
                    )
                    if len(group_df):
                        out[group_name] = _build_mode_distributions(group_df)

    return out


def configure(context):
    """Declare stage dependencies: MiD Wege path + random_seed + purpose/shop flags."""
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_ESCORT_PASSIVE_FROM_ADULT,
        DEFAULT_EXCLUDE_RBW_LEGS, DEFAULT_LEISURE_UNSPECIFIED_SUBTYPE,
        DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
        DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS,
        DEFAULT_SECONDARY_MID_WEEKDAY_LEGS_ONLY, DEFAULT_W_ZWECK_10_AS_LEISURE,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_ESCORT_PASSIVE_FROM_ADULT,
        KEY_EXCLUDE_RBW_LEGS, KEY_LEISURE_UNSPECIFIED_SUBTYPE,
        KEY_PASSIVE_PAIR_MAX_GAP_MINUTES,
        KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS,
        KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY, KEY_W_ZWECK_10_AS_LEISURE,
    )
    context.config("braunschweig.population.popsim.mid_dir")
    # random_seed is not consumed here (the default stage also does not use one)
    # but we declare it for consistent config validation across popsim stages.
    context.config("random_seed")
    context.config("secondary_distance_by_purpose", False)
    context.config("secondary_shop_daily_split", False)
    # Captured because the leisure_unspecified_subtype guard below is scoped to it
    # (the fifth subtype only exists inside the leisure subtype layers).
    leisure_subtype_split = context.config("secondary_leisure_subtype_split", False)
    context.config("secondary_other_subtype_split", False)
    # No-detail ("keine Angabe") W_ZWD codeplan sentinel treatment (issue #242
    # Task 5, ADR-0113). Key/default declared ONCE in config_keys (see that
    # module's comment on KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS) and imported
    # here rather than retyped, because
    # braunschweig.synthesis.locations.secondary_chainsolvers ALSO declares
    # this exact key -- both stages must resolve the SAME value (the
    # leisure_activity / other_errand_long donor pool built here must exclude
    # exactly the legs the chainsolver deciders' estimation excludes, or a leg
    # placed under one label draws its distance from a donor pool built for a
    # different label). Declared UNCONDITIONALLY (like the two split flags
    # above) so an all-flags-off config never needs it; inert unless
    # leisure_subtype_split or other_subtype_split is also True. The
    # production value is also set in configs/base_bs.yml (issue #242 Task 7).
    context.config(KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS, DEFAULT_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS)
    # The fifth leisure subtype (issue #373, ADR-0115). Same shared-key reasoning
    # as the codeplan sentinels above: this stage builds the leisure_unspecified
    # DISTANCE layer while
    # braunschweig.synthesis.locations.secondary_chainsolvers estimates the
    # decider that labels legs with it, so both must resolve the SAME value or a
    # leg labelled leisure_unspecified would find no layer built for it (and fall
    # back to the aggregate "leisure" pool), or a layer would be built that no leg
    # ever draws from. Declared UNCONDITIONALLY (like the split flags above);
    # inert unless leisure_subtype_split is also True.
    leisure_unspecified_subtype = context.config(
        KEY_LEISURE_UNSPECIFIED_SUBTYPE, DEFAULT_LEISURE_UNSPECIFIED_SUBTYPE)
    context.config("escort_purpose", False)
    context.config("escort_passive_education", False)
    # W_ZWECK 10 "anderer Zweck" -> leisure (issue #373, ADR-0111): a SHARED
    # key/default constant, like the plan-structure flags elsewhere -- the
    # distance layers must count the same W_ZWECK codes as leisure that the
    # trip build does, or a "leisure" distance distribution is built from a
    # different set of legs than the plan actually realises.
    w_zweck_10_as_leisure = context.config(
        KEY_W_ZWECK_10_AS_LEISURE, DEFAULT_W_ZWECK_10_AS_LEISURE)
    # Configure-time contradiction guard (issue #373, ADR-0115), same shape as the
    # C-R22 guard in braunschweig.popsim.trips_stage.configure: fail the whole DAG
    # before any stage executes rather than after hours of upstream compute. With
    # the fold off, no W_ZWECK-10 leg reaches following_purpose "leisure", so the
    # leisure_unspecified class would be estimated by the chainsolver decider but
    # never have a donor pool here -- a silently empty layer, exactly what the
    # fallback-transparency rule forbids.
    #
    # SCOPED to secondary_leisure_subtype_split (ruling R8): without the split
    # there are no leisure subtype layers at all, so leisure_unspecified_subtype
    # is inert and its value cannot contradict anything -- config_keys states the
    # same ("effective only with secondary_leisure_subtype_split on"). Raising
    # unscoped would abort every split-off configuration that legitimately sets
    # w_zweck_10_as_leisure false (the popsim_open fixtures do), for a flag that
    # does nothing there.
    if (bool(leisure_subtype_split) and bool(leisure_unspecified_subtype)
            and not bool(w_zweck_10_as_leisure)):
        raise ValueError(
            f"[popsim.distance_distributions] {KEY_LEISURE_UNSPECIFIED_SUBTYPE}: true requires "
            f"{KEY_W_ZWECK_10_AS_LEISURE}: true when secondary_leisure_subtype_split is on -- "
            "with the fold off no W_ZWECK-10 leg is leisure, so the leisure_unspecified class "
            f"would be estimated but never realised. Set both or disable "
            f"{KEY_LEISURE_UNSPECIFIED_SUBTYPE}."
        )
    # Passive escort leg -> the accompanying adult's purpose (issue #372, ADR-0112),
    # declared with the SHARED key/default constants for the same reason: a passive leg
    # the trip build sends to "shop" must contribute to the SHOP distance distribution,
    # not to the education one, or the sampler draws its distance from the wrong layer.
    # Declared default False; the production true is added to configs/base_bs.yml by task 7
    # (see config_keys for the one statement of both defaults).
    context.config(KEY_ESCORT_PASSIVE_FROM_ADULT, DEFAULT_ESCORT_PASSIVE_FROM_ADULT)
    context.config(KEY_PASSIVE_PAIR_MAX_GAP_MINUTES, DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES)
    # Passive-escort pairing candidate universe (issue #373 task 2, ruling C-R20/C-R21):
    # the SAME shared key/default constants braunschweig.popsim.trips_stage declares, so
    # this stage's pairing agrees with the trip build's about which legs even exist to be
    # paired (see run()'s exclude_rbw_legs/drop_leading_arrive_home_leg docstring entry).
    # Declaring them here does NOT change this stage's own leg-drop behaviour -- the
    # distance pool still includes every leg regardless of these two flags' value.
    context.config(KEY_EXCLUDE_RBW_LEGS, DEFAULT_EXCLUDE_RBW_LEGS)
    context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG, DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG)
    # The WEEKDAY DIARY estimation universe (issue #373, ADR-0116). Same shared-key reasoning
    # as the codeplan sentinels and the fifth leisure subtype above:
    # braunschweig.synthesis.locations.secondary_chainsolvers declares this identical key for
    # its three MiD-based subtype deciders, and the two must resolve the SAME value -- the
    # decider labels a leg and this stage builds that label's donor pool, so a disagreement
    # would pair a label estimated on one leg universe with distances drawn from another.
    # Declared UNCONDITIONALLY (like the split flags above) so an all-flags-off config never
    # needs it; it applies to every layer this stage builds, including the aggregate one.
    context.config(KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY,
                   DEFAULT_SECONDARY_MID_WEEKDAY_LEGS_ONLY)


def execute(context):
    """Load MiD Wege and build secondary distance distributions.

    Returns the same structure as the default
    synthesis.population.spatial.secondary.distance_distributions stage so that
    synthesis.population.spatial.secondary.locations (and CustomDistanceSampler)
    can consume it without modification.
    """
    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.stage.config_keys import (
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_ESCORT_PASSIVE_FROM_ADULT,
        KEY_EXCLUDE_RBW_LEGS, KEY_LEISURE_UNSPECIFIED_SUBTYPE,
        KEY_PASSIVE_PAIR_MAX_GAP_MINUTES,
        KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS,
        KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY, KEY_W_ZWECK_10_AS_LEISURE,
    )

    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    by_purpose = context.config("secondary_distance_by_purpose")
    shop_daily_split = context.config("secondary_shop_daily_split")
    leisure_subtype_split = context.config("secondary_leisure_subtype_split")
    other_subtype_split = context.config("secondary_other_subtype_split")
    codeplan_sentinels = bool(context.config(KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS))
    leisure_unspecified_subtype = bool(context.config(KEY_LEISURE_UNSPECIFIED_SUBTYPE))
    escort_purpose = context.config("escort_purpose")
    escort_passive_education = context.config("escort_passive_education")
    w_zweck_10_as_leisure = bool(context.config(KEY_W_ZWECK_10_AS_LEISURE))
    escort_passive_from_adult = bool(context.config(KEY_ESCORT_PASSIVE_FROM_ADULT))
    passive_pair_max_gap_minutes = float(context.config(KEY_PASSIVE_PAIR_MAX_GAP_MINUTES))
    exclude_rbw_legs = bool(context.config(KEY_EXCLUDE_RBW_LEGS))
    drop_leading_arrive_home_leg = bool(context.config(KEY_DROP_LEADING_ARRIVE_HOME_LEG))
    # One-argument execute-context read (the key and its default are declared in configure()).
    weekday_legs_only = bool(context.config(KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY))

    logger.info(
        "[popsim.distance_distributions] loading MiD Wege from %s", mid_dir
    )
    mid_wege = mid_module.load_mid_wege(mid_dir)
    logger.info(
        "[popsim.distance_distributions] loaded %d MiD trips", len(mid_wege)
    )
    return run(
        mid_wege,
        by_purpose=by_purpose,
        shop_daily_split=shop_daily_split,
        leisure_subtype_split=leisure_subtype_split,
        other_subtype_split=other_subtype_split,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        codeplan_sentinels=codeplan_sentinels,
        leisure_unspecified_subtype=leisure_unspecified_subtype,
        weekday_legs_only=weekday_legs_only,
    )
