"""Generic multinomial W_ZWD subtype model (issue #127).

Generalises braunschweig.popsim.shop_subtype's binary daily/non-daily shopping split
into an arbitrary number of named subtype groups for any MiD purpose (W_ZWECK), e.g.
splitting leisure trips into "local" vs "visit" via the Wegezweck-Detail (W_ZWD) code.
Estimation reuses the same W_GEW-weighted, (mode, travel-time-band)-conditioned approach
as shop_subtype; see that module and reference-mid-detail-purposes for the underlying
MiD coding. `tt_band` / `TT_BANDS` are imported from shop_subtype rather than copied so
both models share exactly one definition of the travel-time bands.

Taxonomy provenance (LEISURE_SPEC / OTHER_ERRAND_SPEC, issue #127 Task 2):
    The group boundaries in LEISURE_GROUPS / OTHER_ERRAND_GROUPS below were derived
    from a MEASURED 2026-07-09 W_GEW-weighted mean-distance clustering over the raw
    MiD Wege table (wegkm_imp, clipped at 200 km), NOT from the MiD 2023 codeplan's
    semantic category descriptions.

    Codebook verification (issue #242 Task 5): every W_ZWD code referenced below has
    since been checked against the MiD 2023 codeplan
    (MiD2023_Codeplaene_B1_Standard_v1.1.xlsx, sheet "Wege", variable W_ZWD); the
    verified label is transcribed into the comment next to each code. The distance-
    based grouping is confirmed semantically plausible for every code EXCEPT two
    NO-DETAIL ("keine Angabe") codes that the module's own rule ("if a code turns out
    to be a no-assignment code, it must move to the sentinel set") flags for sentinel
    treatment instead of a genuine activity/errand label:
    - W_ZWD 799 "Freizeit k.A." (in "leisure_activity", ~10-18 km band).
    - W_ZWD 699 "Erledigung k.A." (in "other_errand_long", ~11-16 km band).
    Both move to their spec's sentinel set ONLY in the `_CODEPLAN` variant of the spec
    (LEISURE_SPEC_CODEPLAN / OTHER_ERRAND_SPEC_CODEPLAN, selected via
    `purpose_subtype_codeplan_sentinels`; see `leisure_spec` / `other_errand_spec`
    below) so that moving them is an explicit, flag-gated, and reproducible change
    rather than a silent edit to the unflagged LEISURE_SPEC / OTHER_ERRAND_SPEC, which
    keep today's groups completely unchanged (the OFF path is byte-identical by
    construction: `leisure_spec(False) is LEISURE_SPEC`). No other group membership
    changed in this verification pass: local = Gaststaette/Spaziergang/Hund/Kirche/
    Spielplatz, visit = Besuch/Treffen, activity = Kultur/Veranstaltung/Sport/Garten/
    sonstiges/Kurse, excursion = Tagesausflug/Urlaub/Kurzreise, errand short =
    Arzt/Behoerde, errand long = fuer andere Person/sonstiges/Betreuung -- all
    confirmed plausible against the codeplan labels.

    W_ZWD detail codes measurably cross MiD purposes: 7 % of W_ZWECK=5 (other/errand)
    legs carry the leisure code 701, 5 % carry the shop code 503, 5.1 % carry the shop
    code 504 (2026-07-09 measurement). This is WHY LEISURE_SENTINELS / OTHER_ERRAND_
    SENTINELS below include codes that carry a valid label under a DIFFERENT purpose's
    W_ZWD vocabulary (e.g. 503/599 are shop codes, 701/706/711/713/716/721 are leisure
    codes) rather than a leisure- or errand-specific meaning: excluding them keeps
    ESTIMATION from mislabelling a cross-purpose intrusion as if it were a genuine
    group member of the purpose being estimated.

The fifth leisure subtype (issue #373, ADR-0115):
    MiD W_ZWECK 10 "anderer Zweck" legs are realised as leisure by the
    ``w_zweck_10_as_leisure`` trip-build flag (ADR-0111).

    ASSUMPTION (stated on ``braunschweig.popsim.trips.W_ZWECK_OTHER_CODE`` for the MiD 2023
    delivery, not re-measured here): such a leg carries NO W_ZWD detail code, only design
    sentinels (2202 "im PAPI nicht erhoben", 4402 "Kind unter 14 Jahren", 7704 "kein
    Einkaufs-, Erledigungs-, oder Freizeitweg"). Under that assumption the four W_ZWD groups
    above cannot label these legs at all, and estimating the leisure subtype mix on W_ZWECK 7
    legs alone forces every synthetic leisure activity -- including the ones whose donor leg
    was W_ZWECK 10 -- to draw from the code-7 subtype mix and the code-7 subtype distance
    layers.

    The assumption is CHECKED, not trusted: :func:`label_legs` -- the one place the labelling
    rule lives, shared by ``estimate_group_probabilities`` and the committed-reference
    extraction ``scripts/extract_mid_w_zwd_groups.py`` -- counts the legs a W_ZWECK group
    labelled although their W_ZWD is a valid group code (the "overriding" count) and warns with
    that count and rate whenever it is non-zero, so a delivery in which code-10 legs do carry
    real detail codes cannot pass silently.

    ``LEISURE_SPEC_UNSPECIFIED`` / ``LEISURE_SPEC_CODEPLAN_UNSPECIFIED`` (selected via
    ``leisure_spec``'s ``unspecified_subtype`` argument, config key
    ``leisure_unspecified_subtype``) instead give these legs their OWN subtype,
    ``leisure_unspecified``, defined by the RAW purpose code rather than by the detail code
    (``SubtypeSpec.zweck_groups``). This is an ASSUMPTION about grouping, not a measured
    taxonomy claim: the group is not derived from a distance clustering like the four W_ZWD
    groups, because there is no detail code to cluster on -- it simply keeps a class of legs
    whose purpose detail MiD never observed from being imputed into a class whose distance
    behaviour was measured on different legs.
"""
from __future__ import annotations

import logging

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from braunschweig.popsim.shop_subtype import TT_BANDS, tt_band  # noqa: F401 (re-exported for callers)
from braunschweig.popsim.trips import W_ZWECK_OTHER_CODE

logger = logging.getLogger(__name__)

# Columns required on the MiD Wege table for probability estimation. code_coverage_guard
# only needs W_ZWECK and the group column; estimation additionally needs mode/travel_time/W_GEW.
_GUARD_REQUIRED_COLUMNS = ("W_ZWECK",)
_ESTIMATION_REQUIRED_COLUMNS = ("W_ZWECK", "mode", "travel_time", "W_GEW")


@dataclass(frozen=True)
class SubtypeSpec:
    """Defines a multinomial W_ZWD subtype split for one MiD purpose (W_ZWECK).

    Parameters
    ----------
    purpose_label : str
        Human-readable purpose name used in log messages and error text (e.g. "leisure").
    zweck_values : frozenset[int]
        MiD W_ZWECK code(s) this spec applies to.
    groups : dict[str, frozenset[int]]
        Group name -> set of W_ZWD codes that label a leg as that group. Every code must
        appear in at most one group (validated in __post_init__). The union of all group
        codes plus `sentinels` should exhaust every W_ZWD code observed on legs with
        W_ZWECK in `zweck_values`; that is enforced separately by `code_coverage_guard`,
        which needs the actual data and therefore cannot run at spec-construction time.
    sentinels : frozenset[int]
        W_ZWD codes that carry no usable label (e.g. PAPI interview, child-reported,
        no-info codes) and are excluded from estimation. Legs with these codes are
        unlabelled, just like any other code that maps to neither a group nor a sentinel
        -- the difference is that `code_coverage_guard` treats unmapped codes as an error
        while sentinel codes are a deliberate, documented exclusion.
    group_col : str
        Column in the MiD Wege table holding the detail code (default "W_ZWD").
    zweck_groups : dict[str, frozenset[int]]
        Groups defined by the RAW W_ZWECK code instead of the detail code (see the field
        comment below and the module docstring). Empty by default, i.e. every group is a
        `group_col` group unless a spec says otherwise.
    """

    purpose_label: str
    zweck_values: frozenset
    groups: dict
    sentinels: frozenset
    group_col: str = "W_ZWD"
    #: Groups defined by the RAW ``W_ZWECK`` code instead of the detail code (issue #373,
    #: ADR-0115): group name -> frozenset of W_ZWECK codes. A leg whose W_ZWECK is in a zweck
    #: group is labelled with that group whatever its ``group_col`` value says -- such legs are
    #: ASSUMED to carry only design sentinels (see the module docstring;
    #: ``estimate_group_probabilities`` counts and warns about the legs where that does not
    #: hold). Every code must be in ``zweck_values``.
    zweck_groups: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError(f"SubtypeSpec '{self.purpose_label}' must define at least one group.")

        all_codes: list = []
        for codes in self.groups.values():
            all_codes.extend(codes)
        if len(all_codes) != len(set(all_codes)):
            raise ValueError(
                f"SubtypeSpec '{self.purpose_label}' assigns one or more {self.group_col} codes to "
                f"more than one group; groups must partition the code space, not overlap."
            )

        overlap = set(all_codes) & set(self.sentinels)
        if overlap:
            raise ValueError(
                f"SubtypeSpec '{self.purpose_label}' has {self.group_col} code(s) {sorted(overlap)} in "
                f"both a group and the sentinel set; a code cannot be both labelled and excluded."
            )

        clash = set(self.zweck_groups) & set(self.groups)
        if clash:
            raise ValueError(
                f"SubtypeSpec '{self.purpose_label}' zweck group name(s) {sorted(clash)} "
                f"clash with a {self.group_col} group name."
            )

        seen: set = set()
        for name, codes in self.zweck_groups.items():
            outside = set(codes) - set(self.zweck_values)
            if outside:
                raise ValueError(
                    f"SubtypeSpec '{self.purpose_label}' zweck group '{name}' has W_ZWECK "
                    f"code(s) {sorted(outside)} outside zweck_values {sorted(self.zweck_values)}."
                )
            if seen & set(codes):
                raise ValueError(
                    f"SubtypeSpec '{self.purpose_label}' assigns W_ZWECK code(s) "
                    f"{sorted(seen & set(codes))} to more than one zweck group."
                )
            seen |= set(codes)

    @property
    def group_names(self) -> list:
        """Every group name the spec can label (W_ZWD groups and W_ZWECK groups), sorted."""
        return sorted([*self.groups, *self.zweck_groups])

    @property
    def group_codes(self) -> frozenset:
        """Union of all W_ZWD codes across all groups, i.e. every code with a valid label."""
        codes: set = set()
        for group_code_set in self.groups.values():
            codes |= set(group_code_set)
        return frozenset(codes)

    @property
    def zweck_group_codes(self) -> frozenset:
        """Union of all W_ZWECK codes across all `zweck_groups`, empty when there are none.

        A leg whose W_ZWECK is in this set is labelled by its zweck group whatever its
        `group_col` value is, so it is never an unlabelled sentinel leg even when its detail
        code is one (see :func:`label_legs`).
        """
        codes: set = set()
        for zweck_code_set in self.zweck_groups.values():
            codes |= set(zweck_code_set)
        return frozenset(codes)


def _require_columns(mid_wege: pd.DataFrame, purpose_label: str, required_columns) -> None:
    missing = [column for column in required_columns if column not in mid_wege.columns]
    if missing:
        raise ValueError(
            f"[purpose_subtype:{purpose_label}] MiD Wege table is missing required column(s) "
            f"{missing}."
        )


def label_legs(purpose_legs: pd.DataFrame, spec: SubtypeSpec) -> tuple:
    """Label one purpose's legs with their subtype group -- the ONE labelling rule of this model.

    Both consumers of the rule call this: :func:`estimate_group_probabilities` (which turns the
    labels into P(group | mode, tt_band)) and ``scripts/extract_mid_w_zwd_groups.py`` (which turns
    them into the committed MiD group reference). One implementation means ONE labelling rule for
    the model; two copies of the precedence rule could drift apart without any test noticing
    (issue #373 review, ruling R10).

    The shared rule does NOT make the two callers' numbers equal, because their leg UNIVERSES
    differ: the estimation labels every MiD Wege row the stage loads (``mid.load_mid_wege``, no
    weekday and no route-break filter), while the extraction labels the WEEKDAY non-rbW legs
    only (``kernwo`` in {1, 2, 3}, ``W_RBW`` != 1). The measured shares therefore differ between
    them -- the committed reference measures the WEEKDAY mix, not the mix the decider estimates
    on (issue #373 final review, ruling R13; ADR-0115).

    A W_ZWECK-defined group (``spec.zweck_groups``) wins over the detail-code group, because such
    a leg is ASSUMED to carry no usable detail code (issue #373, ADR-0115; see the module
    docstring). The assumption is not trusted blindly: ``n_override`` counts the legs where it
    does NOT hold -- a zweck-group leg whose ``group_col`` value IS a valid group code, i.e. one
    the detail code could have labelled -- and a non-zero count is WARNed about here, in the one
    place the condition is computed. With an empty ``zweck_groups`` this reduces exactly to the
    previous "label by ``group_col``" rule and ``n_by_zweck == n_override == 0``.

    Parameters
    ----------
    purpose_legs : DataFrame
        The legs of ONE purpose, ALREADY filtered to ``spec.zweck_values`` -- both callers filter
        first because they report their coverage rates against that universe. Required columns:
        ``W_ZWECK`` and ``spec.group_col``.
    spec : SubtypeSpec
        Defines the detail-code groups, the W_ZWECK-code groups and the sentinel codes.

    Returns
    -------
    tuple[DataFrame, int, int]
        ``(labelled, n_by_zweck, n_override)``. ``labelled`` is a COPY of the labelled rows of
        ``purpose_legs``, in their original row order, with an added ``_group`` column holding the
        group name. Unlabelled legs (sentinels and any code the spec does not know) are dropped;
        ``code_coverage_guard`` is what turns the latter into an error, not this function.
    """
    _require_columns(purpose_legs, spec.purpose_label, ("W_ZWECK", spec.group_col))

    code_to_group = {code: name for name, codes in spec.groups.items() for code in codes}
    zweck_to_group = {code: name for name, codes in spec.zweck_groups.items() for code in codes}
    by_zweck = purpose_legs["W_ZWECK"].map(zweck_to_group)
    by_detail = purpose_legs[spec.group_col].map(code_to_group)
    label = by_zweck.where(by_zweck.notna(), by_detail)
    labelled_mask = label.notna()

    labelled = purpose_legs[labelled_mask].copy()
    # Positional assignment (`.to_numpy()`): the group labels are taken in row order and do not
    # depend on the index of `label` matching the index of `labelled`.
    labelled["_group"] = label[labelled_mask].to_numpy()

    by_zweck_mask = by_zweck.notna()
    n_by_zweck = int(by_zweck_mask.sum())
    n_override = int((by_zweck_mask & by_detail.notna()).sum())
    if n_override:
        logger.warning(
            "[purpose_subtype:%s] %d/%d legs labelled by a W_ZWECK group (%.1f%%) ALSO carry a "
            "valid %s group code and were relabelled by the W_ZWECK group. The spec ASSUMES such "
            "legs carry only design sentinels (MiD 2023: 2202 / 7704 / 4402); a non-zero rate "
            "means that assumption does not hold for this delivery, so the measured mix moves "
            "real detail-coded legs into the W_ZWECK group. Check spec.zweck_groups against the "
            "data before trusting it.",
            spec.purpose_label, n_override, n_by_zweck, 100.0 * n_override / n_by_zweck,
            spec.group_col,
        )
    return labelled, n_by_zweck, n_override


def estimate_group_probabilities(mid_wege: pd.DataFrame, spec: SubtypeSpec, *, min_obs: int = 30):
    """P(group | mode, tt_band) and the overall marginal P(group), W_GEW-weighted.

    Mirrors shop_subtype.estimate_daily_probability, generalised from a single daily/
    non-daily flag to an arbitrary set of named groups (spec.group_names, i.e. the
    spec.groups detail-code groups plus the spec.zweck_groups W_ZWECK-code groups).

    The labelling itself is NOT implemented here: it is delegated to :func:`label_legs`, which
    the committed-reference extraction calls too, so the estimated mix and the measured
    reference cannot rest on two different precedence rules.

    Parameters
    ----------
    mid_wege : DataFrame
        MiD Wege table. Required columns: W_ZWECK, mode, travel_time, W_GEW, and
        spec.group_col (default "W_ZWD").
    spec : SubtypeSpec
        Defines the purpose, the groups (detail-code and W_ZWECK-code), and the sentinel
        codes to exclude.
    min_obs : int
        Minimum row count for a (mode, tt_band) cell to receive its own estimate.
        Cells below this threshold are omitted from the result; callers fall back to
        `marginal` for those cells.

    Returns
    -------
    tuple[dict, dict]
        `cell_probs[(mode, band)] = {group_name: probability}` for cells with >= min_obs
        labelled legs; `marginal = {group_name: probability}` over all labelled legs
        (the fallback for thin or absent cells).
    """
    _require_columns(mid_wege, spec.purpose_label, (*_ESTIMATION_REQUIRED_COLUMNS, spec.group_col))

    group_col = spec.group_col
    purpose_legs = mid_wege[mid_wege["W_ZWECK"].isin(spec.zweck_values)]

    # ONE labelling rule for the whole model (zweck group first, then detail code), shared with
    # scripts/extract_mid_w_zwd_groups.py; label_legs also emits the override warning.
    labelled, n_by_zweck, n_override = label_legs(purpose_legs, spec)

    total_purpose_legs = len(purpose_legs)
    labelled_share = (len(labelled) / total_purpose_legs) if total_purpose_legs else 0.0
    logger.info(
        "[purpose_subtype:%s] labelled %d/%d legs (%.1f%%) by %s group code or W_ZWECK group "
        "(%d of them by W_ZWECK group, %d of those overriding a valid %s group code)",
        spec.purpose_label, len(labelled), total_purpose_legs, 100.0 * labelled_share, group_col,
        n_by_zweck, n_override, group_col,
    )

    if labelled.empty:
        raise ValueError(
            f"[purpose_subtype:{spec.purpose_label}] no legs with a known group code: 0 of "
            f"{total_purpose_legs} legs with W_ZWECK in {sorted(spec.zweck_values)} were labelled "
            f"(by {group_col} group code or W_ZWECK group); cannot estimate group probabilities "
            f"from zero labelled observations."
        )

    labelled["_band"] = labelled["travel_time"].map(tt_band)

    group_names = spec.group_names
    weights = labelled["W_GEW"].astype(float)
    weight_total = float(weights.sum())

    marginal = {
        name: float(weights[labelled["_group"] == name].sum() / weight_total) for name in group_names
    }

    cell_probs: dict = {}
    thin_cell_count = 0
    total_cell_count = 0
    for (mode, band), cell in labelled.groupby(["mode", "_band"]):
        total_cell_count += 1
        if len(cell) >= min_obs:
            cell_weights = cell["W_GEW"].astype(float)
            cell_weight_total = float(cell_weights.sum())
            cell_probs[(mode, int(band))] = {
                name: float(cell_weights[cell["_group"] == name].sum() / cell_weight_total)
                for name in group_names
            }
        else:
            thin_cell_count += 1

    logger.info(
        "[purpose_subtype:%s] %d/%d (mode, tt_band) cells are below min_obs=%d and fall back to "
        "the marginal",
        spec.purpose_label, thin_cell_count, total_cell_count, min_obs,
    )
    if total_cell_count and thin_cell_count == total_cell_count:
        logger.warning(
            "[purpose_subtype:%s] ALL (mode, tt_band) cells are below min_obs=%d; every leg will use "
            "the marginal fallback, i.e. no cell-level signal was estimated. Check min_obs and sample "
            "size before trusting this result.",
            spec.purpose_label, min_obs,
        )

    return cell_probs, marginal


def impute_groups(modes, tt_values, cell_probs: dict, marginal: dict, rng) -> np.ndarray:
    """Draw one group per leg from P(group | mode, tt_band), falling back to the marginal.

    Determinism: exactly one uniform draw is consumed per leg, in leg order, via
    `rng.random_sample(n)`. For each leg, the applicable probability vector -- the
    (mode, tt_band) cell's distribution if present in `cell_probs`, otherwise `marginal`
    -- is walked in SORTED group-name order while accumulating a running (cumulative)
    sum; the leg is assigned the first group whose cumulative probability exceeds the
    leg's own uniform draw (standard inverse-CDF sampling). Given the same `rng` state
    and the same `modes`/`tt_values`/`cell_probs`/`marginal` inputs, this always returns
    the same output, regardless of how legs happen to be grouped internally by
    (mode, band) during the vectorised computation.

    Parameters
    ----------
    modes : array-like of str
        Mode label per synthetic leg.
    tt_values : array-like of float
        Travel time in seconds per synthetic leg.
    cell_probs : dict
        Output of estimate_group_probabilities: {(mode, band): {group: probability}}.
    marginal : dict
        {group: probability} fallback used when the (mode, band) cell is absent from
        cell_probs.
    rng : numpy.random.RandomState
        Seeded random state for reproducibility.

    Returns
    -------
    np.ndarray of str (dtype object)
        Group name assigned to each leg, same length and order as `modes`.
    """
    modes = np.asarray(modes)
    tt_values = np.asarray(tt_values, dtype=float)
    leg_count = modes.shape[0]
    if tt_values.shape[0] != leg_count:
        raise ValueError(
            f"modes and tt_values must have the same length, got {leg_count} and "
            f"{tt_values.shape[0]}."
        )

    group_names = sorted(marginal)
    bands = np.array([tt_band(value) for value in tt_values], dtype=int)

    # Exactly one uniform draw per leg, in leg order -- see docstring on determinism.
    draws = rng.random_sample(leg_count)

    out = np.empty(leg_count, dtype=object)
    fallback_mask = np.zeros(leg_count, dtype=bool)

    for mode_value, band_value in sorted(set(zip(modes.tolist(), bands.tolist()))):
        cell_mask = (modes == mode_value) & (bands == band_value)
        probs = cell_probs.get((mode_value, band_value))
        if probs is None:
            probs = marginal
            fallback_mask |= cell_mask
        cumulative = np.cumsum([probs.get(name, 0.0) for name in group_names])
        choice = np.searchsorted(cumulative, draws[cell_mask], side="right")
        choice = np.clip(choice, 0, len(group_names) - 1)
        out[cell_mask] = np.asarray(group_names, dtype=object)[choice]

    fallback_count = int(fallback_mask.sum())
    if fallback_count:
        logger.info(
            "[purpose_subtype] marginal fallback used for %d/%d legs (%.1f%%) whose (mode, tt_band) "
            "cell had no direct estimate",
            fallback_count, leg_count, 100.0 * fallback_count / leg_count,
        )

    return out


def code_coverage_guard(mid_wege: pd.DataFrame, spec: SubtypeSpec) -> None:
    """Raise if any observed W_ZWD code is neither grouped nor a sentinel.

    This is the safeguard against a silent NaN bucket: every W_ZWD code observed on a
    leg with W_ZWECK in `spec.zweck_values` must be explicitly accounted for, either as
    a group code (`spec.groups`) or as a deliberately excluded sentinel
    (`spec.sentinels`). An unmapped code would otherwise be imputed as if it were simply
    "unlabelled", hiding the fact that the spec does not actually cover the real MiD
    coding.
    """
    _require_columns(mid_wege, spec.purpose_label, (*_GUARD_REQUIRED_COLUMNS, spec.group_col))

    purpose_legs = mid_wege[mid_wege["W_ZWECK"].isin(spec.zweck_values)]
    known_codes = spec.group_codes | frozenset(spec.sentinels)
    observed_codes = set(purpose_legs[spec.group_col].unique())
    unmapped_codes = sorted(observed_codes - known_codes)

    if unmapped_codes:
        raise ValueError(
            f"[purpose_subtype:{spec.purpose_label}] {spec.group_col} code(s) {unmapped_codes} appear "
            f"on legs with W_ZWECK in {sorted(spec.zweck_values)} but are mapped to neither a group "
            f"nor a sentinel; add them explicitly to avoid a silent NaN bucket."
        )


# Measured 2026-07-09 on the raw MiD Wege (W_GEW-weighted mean km, wegkm_imp
# clipped at 200; see the spec table). Semantic labels verified against the MiD 2023
# codeplan (MiD2023_Codeplaene_B1_Standard_v1.1.xlsx, sheet "Wege", variable W_ZWD;
# issue #242 Task 5) -- see the module docstring for the verification outcome.
LEISURE_ZWECK = frozenset({7})
#: MiD W_ZWECK 10 "anderer Zweck": folded to leisure by w_zweck_10_as_leisure (ADR-0111). These
#: legs are ASSUMED to carry no W_ZWD detail (only the design sentinels 2202 / 7704 / 4402; see
#: the module docstring for the assumption and the check on it), so they form their own
#: W_ZWECK-defined subtype with their own distance layer (ADR-0115) instead of being imputed one
#: of the four W_ZWD groups. The code itself is owned by
#: braunschweig.popsim.trips.W_ZWECK_OTHER_CODE and imported, not retyped.
LEISURE_UNSPECIFIED_GROUP = "leisure_unspecified"
LEISURE_UNSPECIFIED_ZWECK = frozenset({W_ZWECK_OTHER_CODE})
LEISURE_GROUPS = {
    # leisure_local (~4-7 km): 706 Restaurant/Gaststaette, 710 Spaziergang,
    # 711 Hund ausfuehren, 713 Kirche/Friedhof, 716 Begleitung von Kindern
    # (Spielplatz).
    "leisure_local":     frozenset({706, 710, 711, 713, 716}),
    # leisure_visit (19.1 km): 701 Besuch/Treffen Freunde, Verwandte.
    "leisure_visit":     frozenset({701}),
    # leisure_activity (~10-18 km): 702 kulturelle Einrichtung, 703 Veranstaltung,
    # 704 Sport selbst aktiv, 707 Schrebergarten/Wochenendhaus, 720 sonstiger
    # Freizeitzweck, 721 andere Treffen (Kurse, Hobby, Verein), 799 Freizeit k.A.
    # -- 799 is a NO-DETAIL ("keine Angabe") code; LEISURE_SPEC_CODEPLAN moves it
    # to LEISURE_SENTINELS under purpose_subtype_codeplan_sentinels (see below).
    "leisure_activity":  frozenset({702, 703, 704, 707, 720, 721, 799}),
    # leisure_excursion (45-100 km): 708 Tagesausflug, 709 Urlaub (ab 4
    # Uebernachtungen), 722 Kurzreise (bis 3 Uebernachtungen).
    "leisure_excursion": frozenset({708, 709, 722}),
}
# Generic MiD design sentinels (2202 "im PAPI nicht erhoben", 4402 "Kind unter
# 14 Jahren" -- not asked of children) plus cross-purpose intrusions confirmed
# by the codeplan: 503 (Stadt-/Einkaufsbummel) and 599 (Einkauf k.A.) are SHOP
# W_ZWD codes; 603 (private Erledigung fuer andere Person) and 605 (Betreuung
# Familienmitglieder) are ERRAND W_ZWD codes. All four are measured to
# occasionally appear on W_ZWECK=7 (leisure) legs (cross-purpose leakage, see
# module docstring) but carry no leisure-specific meaning, so they stay
# unlabelled here rather than being force-fit into a leisure group. 999: label
# not in the verified codeplan excerpt (issue #242 Task 5); kept as a sentinel
# pending a dedicated codeplan lookup.
LEISURE_SENTINELS = frozenset({2202, 4402, 599, 999, 503, 603, 605})
# Defensive addition (issue #373, ADR-0111): 7704 "kein Einkaufs-, Erledigungs-, oder
# Freizeitweg" and 7705 "Weg ohne Info zum Wegezweck" (codebook labels, MiD2023_
# Codeplaene_B1_Standard_v1.1.xlsx, sheet "Wege", variable W_ZWD). LEISURE_SPEC's
# zweck_values is {7} only, so a code-10 leg's OWN row never reaches ESTIMATION here
# (estimate_group_probabilities / code_coverage_guard read only W_ZWECK == 7 rows);
# these two sentinels guard a FUTURE widening of zweck_values to include 10, so such a
# widening could not silently trip the coverage guard on a code-10 leg's sentinel-only
# W_ZWD.
#
# That is true for ESTIMATION only -- it does NOT mean code-10 legs are unaffected by
# w_zweck_10_as_leisure at APPLICATION time. distance_distributions.run()'s
# leisure_subtype_split step filters on following_purpose == "leisure" (the eqasim-
# mapped purpose, not the raw W_ZWECK); under the flag that set INCLUDES code-10 legs.
# ASSUMPTION (measured in the #373 A/B, not a defect this task fixes): because a
# code-10 leg's own W_ZWD is always a sentinel and never a LEISURE_GROUPS code, it
# contributes to neither a subtype-specific distance layer nor the subtype-ASSIGNMENT
# model (estimated on code-7 legs only, via LEISURE_ZWECK={7}) -- every synthetic
# "leisure" activity therefore draws its subtype AND its subtype-specific distance
# from the code-7 subtype mix and the code-7 subtype distance layers, regardless of
# whether the underlying donor leg was originally W_ZWECK 7 or 10.
LEISURE_SENTINELS |= {7704, 7705}

OTHER_ERRAND_ZWECK = frozenset({5})   # W_ZWECK 5: private Erledigung (verified, MiD 2023 codeplan)
OTHER_ESCORT_ZWECK = frozenset({6})   # W_ZWECK 6: Bringen/Holen (verified; no W_ZWD detail is collected for this purpose)
OTHER_ERRAND_GROUPS = {
    # other_errand_short (~5-9 km): 601 Arztbesuch/medizinisch, 602 Behoerde,
    # Bank, Post.
    "other_errand_short": frozenset({601, 602}),
    # other_errand_long (~11-16 km): 603 private Erledigung fuer andere
    # Person, 604 sonstiger Erledigungszweck, 605 Betreuung
    # Familienmitglieder, 699 Erledigung k.A. -- 699 is a NO-DETAIL ("keine
    # Angabe") code; OTHER_ERRAND_SPEC_CODEPLAN moves it to
    # OTHER_ERRAND_SENTINELS under purpose_subtype_codeplan_sentinels (see
    # below).
    "other_errand_long":  frozenset({603, 604, 605, 699}),
}
# Generic MiD design sentinels (2202 "im PAPI nicht erhoben", 4402 "Kind unter
# 14 Jahren", 7704 "kein Einkaufs-, Erledigungs-, oder Freizeitweg", 7705 "Weg
# ohne Info zum Wegezweck") plus cross-purpose intrusions confirmed by the
# codeplan: 599 (Einkauf k.A.), 503 (Stadt-/Einkaufsbummel) and 504
# (Dienstleistungen, Friseur, Schuster) are SHOP W_ZWD codes (measured: 5.0 %
# / 5.1 % of W_ZWECK=5 legs carry 503 / 504 respectively, see module
# docstring); 701 (Besuch/Treffen Freunde, Verwandte), 706/711/713/716
# (leisure_local) and 721 (leisure_activity) are LEISURE W_ZWD codes
# (measured: 7 % of W_ZWECK=5 legs carry 701). All are codeplan-confirmed as
# belonging to another purpose's W_ZWD vocabulary, so they stay unlabelled
# here rather than being force-fit into an errand group. 999: label not in
# the verified codeplan excerpt (issue #242 Task 5); kept as a sentinel
# pending a dedicated codeplan lookup.
OTHER_ERRAND_SENTINELS = frozenset({2202, 4402, 7704, 7705, 599, 999,
                                    503, 504, 701, 706, 711, 713, 716, 721})

# Module-level specs built from the constants above; later tasks import these exact
# names rather than re-declaring the code sets.
LEISURE_SPEC = SubtypeSpec(
    purpose_label="leisure",
    zweck_values=LEISURE_ZWECK,
    groups=LEISURE_GROUPS,
    sentinels=LEISURE_SENTINELS,
)

OTHER_ERRAND_SPEC = SubtypeSpec(
    purpose_label="other_errand",
    zweck_values=OTHER_ERRAND_ZWECK,
    groups=OTHER_ERRAND_GROUPS,
    sentinels=OTHER_ERRAND_SENTINELS,
)


def _move_code_to_sentinels(groups: dict, sentinels: frozenset, *, code: int,
                             group_name: str) -> tuple:
    """Return ``(new_groups, new_sentinels)`` with ``code`` moved out of
    ``groups[group_name]`` and into ``sentinels`` (issue #242 Task 5).

    Used to build the ``_CODEPLAN`` sentinel variant of a base spec's groups/
    sentinels from the unchanged base groups/sentinels: a NO-DETAIL ("keine
    Angabe") W_ZWD code carries no usable subtype signal and must be excluded
    from ESTIMATION the same way the design-code sentinels above are, rather
    than silently diluting a real group's probability with unlabelled legs.

    Raises
    ------
    ValueError
        If ``code`` is not currently a member of ``groups[group_name]`` -- a
        defensive check so a future edit to the base group cannot silently
        desynchronise the codeplan variant from the group it is meant to
        modify.
    """
    if code not in groups.get(group_name, frozenset()):
        raise ValueError(
            f"[purpose_subtype] cannot move W_ZWD code {code} out of group "
            f"{group_name!r}; it is not currently a member of that group "
            f"({sorted(groups.get(group_name, ()))})."
        )
    new_groups = dict(groups)
    new_groups[group_name] = frozenset(groups[group_name]) - {code}
    new_sentinels = frozenset(sentinels) | {code}
    return new_groups, new_sentinels


def _add_zweck_group(spec: SubtypeSpec, *, name: str, codes: frozenset) -> SubtypeSpec:
    """Return ``spec`` with one additional W_ZWECK-defined group (issue #373, ADR-0115).

    Copies every field of ``spec`` explicitly (SubtypeSpec is frozen), widening
    ``zweck_values`` by ``codes`` so the new group's legs actually enter estimation and the
    coverage guard, and adding ``name -> codes`` to ``zweck_groups``. ``groups``,
    ``sentinels`` and ``group_col`` are carried over unchanged, so the derived spec differs
    from its base in exactly the two fields the new group needs.
    """
    return SubtypeSpec(purpose_label=spec.purpose_label,
                       zweck_values=frozenset(spec.zweck_values) | frozenset(codes),
                       groups=spec.groups, sentinels=spec.sentinels, group_col=spec.group_col,
                       zweck_groups={**spec.zweck_groups, name: frozenset(codes)})


# Codeplan no-detail sentinel variants (issue #242 Task 5, ADR-0113): 799
# "Freizeit k.A." and 699 "Erledigung k.A." are NO-DETAIL codes (see the module
# docstring); these variants move them from their group into the sentinel set,
# leaving every other code exactly where LEISURE_SPEC / OTHER_ERRAND_SPEC has it.
_LEISURE_GROUPS_CODEPLAN, _LEISURE_SENTINELS_CODEPLAN = _move_code_to_sentinels(
    LEISURE_GROUPS, LEISURE_SENTINELS, code=799, group_name="leisure_activity")

LEISURE_SPEC_CODEPLAN = SubtypeSpec(
    purpose_label="leisure",
    zweck_values=LEISURE_ZWECK,
    groups=_LEISURE_GROUPS_CODEPLAN,
    sentinels=_LEISURE_SENTINELS_CODEPLAN,
)

_OTHER_ERRAND_GROUPS_CODEPLAN, _OTHER_ERRAND_SENTINELS_CODEPLAN = _move_code_to_sentinels(
    OTHER_ERRAND_GROUPS, OTHER_ERRAND_SENTINELS, code=699, group_name="other_errand_long")

OTHER_ERRAND_SPEC_CODEPLAN = SubtypeSpec(
    purpose_label="other_errand",
    zweck_values=OTHER_ERRAND_ZWECK,
    groups=_OTHER_ERRAND_GROUPS_CODEPLAN,
    sentinels=_OTHER_ERRAND_SENTINELS_CODEPLAN,
)

# leisure_unspecified variants (issue #373, ADR-0115): add the fifth, W_ZWECK-defined leisure
# subtype -- W_ZWECK 10 "anderer Zweck" legs, which w_zweck_10_as_leisure realises as leisure but
# which are ASSUMED to carry no W_ZWD detail -- on top of each of the two spec variants above,
# leaving every W_ZWD group and sentinel exactly where its base spec has it.
LEISURE_SPEC_UNSPECIFIED = _add_zweck_group(
    LEISURE_SPEC, name=LEISURE_UNSPECIFIED_GROUP, codes=LEISURE_UNSPECIFIED_ZWECK)
LEISURE_SPEC_CODEPLAN_UNSPECIFIED = _add_zweck_group(
    LEISURE_SPEC_CODEPLAN, name=LEISURE_UNSPECIFIED_GROUP, codes=LEISURE_UNSPECIFIED_ZWECK)


def leisure_spec(codeplan_sentinels: bool, unspecified_subtype: bool = False) -> SubtypeSpec:
    """Select the leisure SubtypeSpec for its two config flags,
    ``purpose_subtype_codeplan_sentinels`` (issue #242 Task 5, ADR-0113) and
    ``leisure_unspecified_subtype`` (issue #373, ADR-0115).

    The 2x2 table of module constants returned, all by IDENTITY rather than as re-derived
    equivalent objects:

    =========================  =========================  =====================================
    ``codeplan_sentinels``     ``unspecified_subtype``    returns
    =========================  =========================  =====================================
    False                      False                      ``LEISURE_SPEC``
    True                       False                      ``LEISURE_SPEC_CODEPLAN``
    False                      True                       ``LEISURE_SPEC_UNSPECIFIED``
    True                       True                       ``LEISURE_SPEC_CODEPLAN_UNSPECIFIED``
    =========================  =========================  =====================================

    ``codeplan_sentinels`` moves 799 "Freizeit k.A." out of ``leisure_activity`` into the
    sentinel set; ``unspecified_subtype`` adds the W_ZWECK-defined fifth group
    ``leisure_unspecified``. Because the selection is by identity and both flags default to
    the pre-feature value, the OFF/OFF path is byte-identical to the pre-Task-5 behaviour by
    construction (``leisure_spec(False) is LEISURE_SPEC``).
    """
    if unspecified_subtype:
        return LEISURE_SPEC_CODEPLAN_UNSPECIFIED if codeplan_sentinels else LEISURE_SPEC_UNSPECIFIED
    return LEISURE_SPEC_CODEPLAN if codeplan_sentinels else LEISURE_SPEC


def other_errand_spec(codeplan_sentinels: bool) -> SubtypeSpec:
    """Select the other-errand SubtypeSpec for the same flag as ``leisure_spec``.

    Returns ``OTHER_ERRAND_SPEC_CODEPLAN`` (699 "Erledigung k.A." excluded as
    a NO-DETAIL sentinel) when ``codeplan_sentinels`` is True, else the
    unchanged ``OTHER_ERRAND_SPEC`` -- by IDENTITY (``other_errand_spec(False)
    is OTHER_ERRAND_SPEC``), so the OFF path is byte-identical to the
    pre-Task-5 behaviour by construction.
    """
    return OTHER_ERRAND_SPEC_CODEPLAN if codeplan_sentinels else OTHER_ERRAND_SPEC
