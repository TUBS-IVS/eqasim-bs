"""Map MiD Wege (trips) onto the synthetic persons -> eqasim activity chains.

Each synthetic popsim_mid person is a copy of a MiD donor person ``(H_ID, P_ID)``;
the donor's MiD Wege (trips) become that person's trip chain. This module maps the
MiD trip purpose and mode to the eqasim vocabulary and joins the donor Wege onto
the synthetic persons. Codes are grounded in the MiD 2023 codebook (Wege sheet),
documented inline, not invented.

The activity-chain construction proper (building the home/work/... activity
sequence with times and coordinates between consecutive trips) builds on the
trip records produced here.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.seed import MID_SEED_COLUMNS
from data.hts import hts

logger = logging.getLogger(__name__)

# MiD W_ZWECK (Wegezweck) -> eqasim activity type at the trip destination.
# Labels verbatim from the codeplan (MiD2023_Codeplaene_B1_Standard_v1.1.xlsx, sheet "Wege",
# variable W_ZWECK) -- NOT inferred from MiD's derived variables:
#    1 Erreichen der Arbeitsstaette          -> work
#    2 dienstlich/geschaeftlich               -> work
#    3 Erreichen der Ausbildungsstaette/Schule -> education
#    4 Einkauf                                -> shop
#    5 private Erledigungen                   -> other
#    6 Bringen oder Holen von Personen        -> other  (-> "escort" under the #201 flag)
#    7 Freizeitaktivitaet                     -> leisure
#    8 nach Hause                             -> home
#    9 Rueckweg vom vorherigen Weg            -> home
#   10 anderer Zweck                          -> other
#   11 Schule, auch Vorschule                 -> education
#   12 Kita/Kindergarten                      -> education
#   13 Begleitung Erwachsener                 -> other  (-> "escort" under the #201 flag)
#   14 Sport/Sportverein                      -> leisure
#   15 Freunde besuchen/treffen               -> leisure
#   16 Unterricht (nicht Schule)              -> leisure   <-- see the note below
#   99 keine Angabe                           -> other
#
# Codes 13-16 and 99 used to reach "other" through a silent fillna (issue #241): about 3 % of
# all donor legs (W_GEW-weighted) arrived at their purpose by fallback, with no counter and no
# log line, and MiD 2023 had introduced those codes without anything noticing.
#
# Code 16 deserves its reasoning stated: the codeplan label "Unterricht (nicht Schule)" is
# educational (music lessons, driving school, evening classes), yet it maps to LEISURE here,
# following MiD's own `zweck` derivation (16 -> 7 Freizeit). The reason is what `education`
# MEANS in eqasim: the person's ASSIGNED educational facility, which the primary-location
# machinery anchors (school / Kita). An evening class is not that facility, so anchoring it
# there would place the activity wrongly. See the ADR for the full argument.
#: MiD ``W_ZWECK`` 99 "keine Angabe": the respondent did not say why they travelled. The LEG is
#: real (a trip happened), so the trip build still gives it a purpose -- DEFAULT_PURPOSE "other",
#: the same bucket the map below assigns -- but it is a NON-ANSWER, not a behaviour, so it must
#: not feed any ESTIMATION: neither a purpose distance pool nor a subtype probability
#: (exclude_no_answer_purpose_legs, ADR-0117). 6,029 legs of the 2026-09 delivery carry it,
#: 4,271 of them in the weekday diary universe (0.58 % of its legs, 2.80 % of everything the
#: model calls "other"; measured 2026-09-11, ad hoc).
W_ZWECK_NO_ANSWER_CODE = 99

PURPOSE_BY_W_ZWECK = {
    1: "work",
    2: "work",
    3: "education",
    4: "shop",
    5: "other",
    6: "other",
    7: "leisure",
    8: "home",
    9: "home",
    10: "other",
    11: "education",
    12: "education",
    13: "other",
    14: "leisure",
    15: "leisure",
    16: "leisure",
    99: "other",
}
DEFAULT_PURPOSE = "other"

# The three codes whose mapping CHANGES the population relative to the pre-#241 behaviour
# (they used to land in "other" via the fallback). Flag-gated so the A/B can isolate the
# effect; with the flag off they are remapped to DEFAULT_PURPOSE explicitly, which keeps them
# COVERED so the coverage guard below stays meaningful in both modes.
ROUND_TRIP_LEISURE_W_ZWECK = frozenset({14, 15, 16})

# Escort (Begleitung) W_ZWECK codes (issue #201). Code 6 = Bringen/Holen; code 13
# is classified as escort by BOTH of MiD's own derived purpose variables
# (zweck: 13 -> 6; hwzweck1: 13 -> 7 = Begleitung; verified 2026-07-24 on the raw
# Wege table -- see docs/superpowers/specs/2026-07-24-escort-purpose-design.md).
# The semantic codeplan label of 13 is still to be confirmed (codeplan xlsx not in
# repo); the CATEGORY membership is established by the MiD-internal derivations.
# Deliberately separate from purpose_subtype.OTHER_ESCORT_ZWECK ({6}), which the
# escort-OFF path (secondary_other_subtype_split) continues to use unchanged.
#
# Issue #256 further splits this set: code 6 is the ACTIVE escort leg (the
# escorting adult's own trip) and code 13 is the PASSIVE leg (the escorted
# person's own trip -- 100% minors on the raw MiD file; pinned active/passive
# split shares in eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv,
# derived by scripts/derive_escort_w_zweck_split.py). When escort_passive_education
# is ON (map_purpose below), code 13 is relabelled to "education" instead of
# "escort" because it is the child's own trip to their assigned Kita/school, not
# an escort trip in its own right; code 6 keeps mapping to "escort".
ESCORT_W_ZWECK = frozenset({6, 13})

#: MiD W_ZWECK 10 "anderer Zweck". MiD's own main-purpose derivation hwzweck1 folds it to 6 Freizeit for
#: 100 % of the legs (committed mid2023_w_zweck_by_hwzweck1.csv, ADR-0111); its W_ZWD is always a sentinel.
W_ZWECK_OTHER_CODE = 10
LEISURE_PURPOSE = "leisure"

#: The child's (passive escort leg's) purpose derived from the W_ZWECK of the ACCOMPANYING adult
#: leg the pairing found (issue #372, ADR-0112). Read as "the adult travelled for X, so the child
#: taken along arrived at X too".
#:
#: Two codes are deliberately ABSENT and resolved elsewhere in
#: :func:`passive_purpose_for_pairs`, because their child-side purpose depends on an active flag
#: rather than on the adult code alone: 6 (:data:`ADULT_ESCORT_W_ZWECK`, the adult's own
#: Bringen/Holen leg -- the child is then genuinely being brought somewhere of their own, so the
#: existing passive rule decides between "education" and "escort") and
#: :data:`W_ZWECK_OTHER_CODE` (10, which follows ``w_zweck_10_as_leisure`` exactly as the adult's
#: own leg does). Every OTHER documented W_ZWECK code is listed here explicitly, so a code the
#: MiD codeplan documents can never reach the fallback silently (the same coverage rule
#: ``PURPOSE_BY_W_ZWECK`` follows since issue #241).
#:
#: The three EDUCATION codes (3 Ausbildungsstaette, 11 Schule, 12 Kita) map the child to "other",
#: NOT to "education": eqasim's ``education`` purpose means the person's OWN assigned educational
#: facility, which the primary-location machinery anchors. A toddler taken along to an older
#: sibling's school is not at their own Kita, so anchoring them there would place the activity
#: wrongly -- the same argument PURPOSE_BY_W_ZWECK states for code 16.
PASSIVE_PURPOSE_BY_ADULT_W_ZWECK = {
    1: "other", 2: "other", 3: "other", 4: "shop", 5: "other", 7: "leisure",
    8: "home", 9: "home", 11: "other", 12: "other", 14: "leisure", 15: "leisure",
    16: "leisure", 99: "other",
}
#: MiD W_ZWECK 6 "Bringen oder Holen von Personen" -- the ACTIVE escort leg. A passive leg paired
#: with one keeps the ``escort_passive_education`` rule (see PASSIVE_PURPOSE_BY_ADULT_W_ZWECK).
ADULT_ESCORT_W_ZWECK = 6

#: Default maximum departure-time gap (MINUTES) between a passive escort leg and the candidate
#: adult leg it is paired with. MUST equal ``escort_pairing.DEFAULT_MAX_GAP_MINUTES``, which is
#: the OWNER of the value: that module cannot be imported here at module level (it imports
#: :func:`mid_time_seconds` from THIS module), and ``stage.config_keys`` -- which needs a literal
#: synpp default -- must stay a leaf. The three homes are pinned equal by
#: ``tests/test_popsim_trips.py::test_passive_pair_gap_default_agrees_across_its_three_homes``.
DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES = 15.0


#: MiD W_ZWECK codes that mean "arrived at home" -- the destination a LEADING arrive-home leg
#: has (see :func:`leading_arrive_home_leg_index`). Same two codes ``PURPOSE_BY_W_ZWECK`` maps to
#: ``"home"``, derived from it so the two can never disagree.
HOME_W_ZWECK = frozenset(code for code, purpose in PURPOSE_BY_W_ZWECK.items() if purpose == "home")
#: MiD ``W_SO1`` (start situation of the reporting day's first recorded leg) = 2: the day begins
#: mid-trip and the first RECORDED leg only ARRIVES home, i.e. it precedes the observed window.
LEADING_ARRIVE_HOME_W_SO1 = 2
#: MiD ``W_RBW`` = 1: a "regelmaessiger beruflicher Weg" SUMMARY record, not an individually
#: reported diary leg (see ``time_imputation``'s module docstring for the audited background).
RBW_LEG_FLAG = 1


def rbw_leg_mask(wege: pd.DataFrame, *, rbw_col: str = "W_RBW") -> pd.Series:
    """Boolean mask of the rbW summary legs the trip build drops under ``exclude_rbw_legs``.

    The ONE definition of "this leg is an rbW summary record", used both by
    :func:`expand_persons_to_trips` (which drops them and counts the donors it empties) and by
    :func:`legs_kept_by_the_trip_build` (which the SEED derivations and the reference-derivation
    scripts use to reproduce the plan's leg universe). Raises ``KeyError`` naming the column when
    it is absent, rather than reading a missing flag as "not rbW".
    """
    if rbw_col not in wege.columns:
        raise KeyError(f"[popsim.trips] exclude_rbw_legs=True requires the MiD Wege column '{rbw_col}'")
    return wege[rbw_col] == RBW_LEG_FLAG


def leading_arrive_home_leg_index(
    wege: pd.DataFrame, *, household_col: str = "H_ID", person_col: str = "P_ID",
    trip_col: str = "W_ID", so1_col: str = "W_SO1", zweck_col: str = "W_ZWECK",
) -> pd.Index:
    """Index labels of the leading "arrive home from elsewhere" legs the trip build drops.

    A person's FIRST leg (by ``trip_col``) qualifies when it both arrives home
    (``zweck_col`` in :data:`HOME_W_ZWECK`) and started elsewhere
    (``so1_col`` == :data:`LEADING_ARRIVE_HOME_W_SO1`): such a leg is not the diary's actual
    first trip but a leftover record from before the observed window. The ONE definition, shared
    exactly like :func:`rbw_leg_mask`. Raises ``KeyError`` when ``so1_col`` is absent.
    """
    if so1_col not in wege.columns:
        raise KeyError(
            f"[popsim.trips] drop_leading_arrive_home_leg=True requires the MiD Wege column '{so1_col}'")
    ordered = wege.sort_values([household_col, person_col, trip_col])
    first = ordered.groupby([household_col, person_col], sort=False).head(1)
    return first.index[(first[so1_col] == LEADING_ARRIVE_HOME_W_SO1)
                       & first[zweck_col].isin(HOME_W_ZWECK)]


def legs_kept_by_the_trip_build(
    wege: pd.DataFrame, *, exclude_rbw_legs: bool, drop_leading_arrive_home_leg: bool,
    household_col: str = "H_ID", person_col: str = "P_ID", trip_col: str = "W_ID",
) -> pd.DataFrame:
    """``wege`` reduced to the legs the trip build actually turns into plan legs.

    Applies :func:`rbw_leg_mask` and then :func:`leading_arrive_home_leg_index`, in the SAME
    order :func:`expand_persons_to_trips` applies them (the second rule reads "the person's first
    remaining leg", so the order is load-bearing). Every consumer that must reason about the
    realised plan on the RAW Wege table -- the ``education_flag`` seed's passive-escort pairing
    (``mid.participation``) and the committed-reference derivation
    (``scripts/derive_escort_w_zweck_split.py``) -- goes through this function instead of
    re-implementing the two rules, so a seed or a pinned reference cannot describe a different
    day than the plan (issue #372 fix round 1, controller rulings C-R12 / IMPORTANT 3).

    Does NOT log the emptied-donor counters :func:`expand_persons_to_trips` reports: those are
    about the SYNTHETIC persons' plans and need a persons frame, which the seed/reference callers
    do not have here. Returns a filtered view/copy; ``wege`` is not mutated.
    """
    out = wege
    if exclude_rbw_legs:
        out = out[~rbw_leg_mask(out)]
    if drop_leading_arrive_home_leg:
        out = out.drop(index=leading_arrive_home_leg_index(
            out, household_col=household_col, person_col=person_col, trip_col=trip_col))
    return out


#: The model's ONE weekday definition: the SAME day filter the PopulationSim seed applies
#: (``seed.MID_SEED_COLUMNS.day_filter_values``, the MiD ``kernwo`` core-week codes; the seed
#: module's own alias for the value set is ``seed.WEEKDAY_KERNWO``). Read from the seed rather
#: than re-typed, so a change to the model's weekday universe cannot leave a second, silently
#: stale copy behind. Every MiD-based estimation of a WEEKDAY quantity uses this universe
#: (issue #373, ADR-0116).
WEEKDAY_DIARY_KERNWO = tuple(MID_SEED_COLUMNS.day_filter_values)

#: The two MiD Wege columns the weekday diary universe is defined on: the reporting-day code and
#: the rbW summary flag. Single source for both names -- the universe helpers check exactly these
#: (the first one overridable per call) and RAISE naming the missing one, so a delivery that
#: cannot express the universe fails instead of silently widening it.
WEEKDAY_DIARY_COLUMNS = ("kernwo", "W_RBW")


def weekday_diary_leg_mask(wege: pd.DataFrame, *, kernwo_col: str = "kernwo") -> pd.Series:
    """Boolean mask of the legs that form the WEEKDAY DIARY universe.

    True where the reporting day is in :data:`WEEKDAY_DIARY_KERNWO` AND the leg is not an rbW
    summary record (:func:`rbw_leg_mask`). This is the ONE definition of "the leg universe of
    one synthetic weekday" (issue #373, ADR-0116), shared by the secondary distance layers
    (``braunschweig.popsim.distance_distributions``), the three MiD-based subtype deciders
    (``braunschweig.synthesis.locations.secondary_chainsolvers.deciders``) and the two committed
    MiD reference extractions (``scripts/extract_mid_w_zwd_groups.py``,
    ``scripts/extract_mid_w_zweck_hwzweck1.py``), so a reference and the estimation it is
    compared against can never describe different days.

    ``kernwo_col`` values are coerced to numeric (a CSV delivery may hand the codes over as
    text); an uncoercible value counts as NOT weekday rather than raising, because a leg whose
    reporting day cannot be read must not be admitted to a weekday universe.

    Raises ``ValueError`` naming the column when ``kernwo_col`` or ``W_RBW`` is absent: the
    universe must never be applied silently to a frame that cannot express it (a missing
    ``kernwo`` would otherwise read as "every leg is a weekday leg").
    """
    is_weekday, is_rbw = _weekday_and_rbw_masks(wege, kernwo_col=kernwo_col)
    return is_weekday & ~is_rbw


def _weekday_and_rbw_masks(wege: pd.DataFrame, *, kernwo_col: str) -> tuple:
    """``(is_weekday, is_rbw)`` for the weekday diary universe -- the ONE place both are built.

    Kept private and shared by :func:`weekday_diary_leg_mask` and
    :func:`restrict_to_weekday_diary_legs` so the universe rule is expressed once and each
    caller walks the frame once: the public mask needs the conjunction, the logging wrapper
    needs the two drop reasons separately.
    """
    rbw_col = WEEKDAY_DIARY_COLUMNS[1]          # the rbW summary flag of the universe
    universe_columns = (kernwo_col, rbw_col)
    for column in universe_columns:
        if column not in wege.columns:
            raise ValueError(
                f"[popsim.trips] the weekday diary universe needs the MiD Wege column "
                f"{column!r} (universe columns: {universe_columns}); it is absent from "
                f"the frame (present: {list(wege.columns[:20])} ...)."
            )
    is_weekday = pd.to_numeric(wege[kernwo_col], errors="coerce").isin(WEEKDAY_DIARY_KERNWO)
    return is_weekday, rbw_leg_mask(wege, rbw_col=rbw_col)


def restrict_to_weekday_diary_legs(wege: pd.DataFrame, *, log_tag: str) -> pd.DataFrame:
    """``wege`` reduced to the weekday diary universe, with the kept rate logged.

    Applies :func:`weekday_diary_leg_mask` and logs ``kept n/total (rate)`` together with the
    two drop reasons at INFO -- a filter that shrinks an estimation universe must never be
    silent (CLAUDE.md "Fallback transparency"). The two reasons PARTITION the dropped legs:
    ``non-weekday`` counts every leg outside the day filter (rbW or not) and ``rbW`` counts the
    weekday legs dropped for being summary records, so kept + non-weekday + rbW == total.

    Raises ``ValueError`` when nothing is kept: a stage that would estimate on an empty frame
    must stop rather than fall back to something else (an empty result here means the delivery's
    ``kernwo`` / ``W_RBW`` contents are not what the universe assumes).

    Returns a filtered view/copy; ``wege`` is not mutated.
    """
    # One pass: the shared helper builds both masks (and raises when a universe column is
    # absent), the conjunction is the universe and the two complements are the drop reasons.
    is_weekday, is_rbw = _weekday_and_rbw_masks(wege, kernwo_col="kernwo")
    mask = is_weekday & ~is_rbw
    n_total = len(wege)
    n_kept = int(mask.sum())
    n_non_weekday = int((~is_weekday).sum())
    n_rbw = int((is_weekday & is_rbw).sum())
    logger.info(
        "%s weekday diary universe: kept %d/%d legs (%.1f%%); dropped %d non-weekday "
        "(kernwo outside %s), %d rbW summary records",
        log_tag, n_kept, n_total, 100.0 * n_kept / n_total if n_total else float("nan"),
        n_non_weekday, list(WEEKDAY_DIARY_KERNWO), n_rbw,
    )
    if n_kept == 0:
        raise ValueError(
            f"{log_tag} the weekday diary universe is EMPTY: none of {n_total} legs is a "
            f"weekday (kernwo in {list(WEEKDAY_DIARY_KERNWO)}) non-rbW leg; check the kernwo "
            "and W_RBW contents of the MiD delivery."
        )
    return wege[mask]


def passive_purpose_for_pairs(adult_codes, *, escort_passive_education: bool,
                              w_zweck_10_as_leisure: bool) -> np.ndarray:
    """The passive escort legs' purposes, derived from their paired adults' ``W_ZWECK`` codes.

    Pure vectorised lookup over :data:`PASSIVE_PURPOSE_BY_ADULT_W_ZWECK` with the two
    flag-dependent codes resolved on top (see that mapping's own documentation):
    :data:`ADULT_ESCORT_W_ZWECK` keeps the existing passive rule and
    :data:`W_ZWECK_OTHER_CODE` follows ``w_zweck_10_as_leisure``.

    Args:
        adult_codes: the paired adult legs' ``W_ZWECK`` codes, one per passive leg. Any
            array-like of integer-coercible codes.
        escort_passive_education: the value of the ``escort_passive_education`` trip-build flag
            for THIS run; decides what a child paired with an ACTIVE escort leg becomes
            (``"education"`` when on, ``"escort"`` otherwise).
        w_zweck_10_as_leisure: the value of the ``w_zweck_10_as_leisure`` trip-build flag for
            THIS run (issue #373, ADR-0111); decides what a child paired with an adult code-10
            leg becomes.

    Returns:
        ``np.ndarray`` of purpose strings, one per input code, in input order.

    A code outside the table -- including a MISSING one -- falls back to :data:`DEFAULT_PURPOSE`,
    COUNTED and NAMED in a warning (CLAUDE.md fallback transparency): every code the codeplan
    documents is mapped, so an unknown code means a NEW MiD code that must be added explicitly.

    ``explicit_round_trip_purposes`` is deliberately NOT threaded in: it is True in production
    and inert there, and its pre-#241 arm would send the ADULT's own leg for W_ZWECK 14/15/16
    back to ``"other"`` while this table still gives the child ``"leisure"`` -- an asymmetry that
    only matters on that A/B arm, where ``escort_passive_from_adult`` is not used. Thread it if
    the two flags are ever combined.
    """
    # to_numeric(errors="coerce"), NOT astype(int): a NaN adult code (an unpaired row slipping
    # in, or a delivery with a blank W_ZWECK) would otherwise raise IntCastingNaNError instead of
    # reaching the counted, named fallback this function's contract promises.
    codes = pd.to_numeric(pd.Series(np.asarray(adult_codes)), errors="coerce")
    passive_rule = "education" if escort_passive_education else "escort"
    purpose = codes.map(PASSIVE_PURPOSE_BY_ADULT_W_ZWECK)
    purpose = purpose.where(codes != ADULT_ESCORT_W_ZWECK, passive_rule)
    purpose = purpose.where(codes != W_ZWECK_OTHER_CODE,
                            LEISURE_PURPOSE if w_zweck_10_as_leisure else DEFAULT_PURPOSE)
    unknown = purpose.isna()
    if bool(unknown.any()):
        # dropna() before sorting: a NaN code cannot be compared with an int, and it is reported
        # by its own count rather than as a sortable "code".
        # int(): to_numeric yields float64 as soon as one code is missing, and a codeplan code
        # reported as "77.0" reads like a different value than the "77" the codeplan documents.
        unknown_codes = sorted(int(code) for code in codes[unknown].dropna().unique())
        n_missing = int(codes[unknown].isna().sum())
        logger.warning(
            "[popsim.trips] passive pairing: adult W_ZWECK code(s) %s are not in "
            "PASSIVE_PURPOSE_BY_ADULT_W_ZWECK (plus %d leg(s) with no adult code at all); "
            "%d legs fall back to %r",
            unknown_codes, n_missing, int(unknown.sum()), DEFAULT_PURPOSE)
    return purpose.fillna(DEFAULT_PURPOSE).to_numpy()


def leisure_w_zweck_codes(*, w_zweck_10_as_leisure: bool) -> frozenset:
    """The W_ZWECK codes that mean 'leisure' under the active flags (single source for seeds and plans)."""
    codes = {code for code, purpose in PURPOSE_BY_W_ZWECK.items() if purpose == LEISURE_PURPOSE}
    if w_zweck_10_as_leisure:
        codes.add(W_ZWECK_OTHER_CODE)
    return frozenset(codes)


# MiD hvm_imp (imputed Hauptverkehrsmittel; handbook Kap. 4.2 mandates the
# imputed variant) -> eqasim canonical mode. hvm_imp is fully imputed (codes
# 1..5 only); any other code is a data/contract error and raises.
# 1 zu Fuss -> walk; 2 Fahrrad -> bicycle (canonical eqasim mode, not "bike");
# 3 MIV-Mitfahrer -> car_passenger; 4 MIV-Fahrer -> car; 5 OEPV -> pt.
MODE_BY_HVM = {
    1: "walk",
    2: "bicycle",
    3: "car_passenger",
    4: "car",
    5: "pt",
}


def map_purpose(wege: pd.DataFrame, *, zweck_col: str = "W_ZWECK",
                escort_purpose: bool = False,
                escort_passive_education: bool = False,
                explicit_round_trip_purposes: bool = True,
                w_zweck_10_as_leisure: bool = False,
                escort_passive_from_adult: bool = False,
                passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
                pairing_candidate_mask: pd.Series | None = None,
                ) -> pd.DataFrame:
    """Add the eqasim activity ``purpose`` from MiD ``W_ZWECK``.

    When ``escort_purpose`` is True (issue #201), W_ZWECK codes in
    ``ESCORT_W_ZWECK`` map to the dedicated ``"escort"`` purpose instead of
    ``"other"``; the override is applied on top of ``PURPOSE_BY_W_ZWECK`` so the
    OFF path stays byte-identical. The escort share is logged (W_GEW-weighted
    when the weight column is present) -- no silent re-mapping.

    When ``escort_passive_education`` is ALSO True (issue #256), the PASSIVE
    side of the escort pair (W_ZWECK 13 -- the escorted person's own leg) is
    relabelled to ``"education"`` instead of ``"escort"``: it is the child's own
    trip to their assigned Kita/school (anchored there downstream by the
    plan-based ``has_education_trip`` primary-location machinery, which covers
    both chain sides), not an escort trip in its own right. The ACTIVE side
    (W_ZWECK 6 -- the escorting adult's leg) keeps mapping to ``"escort"``.
    Requires ``escort_purpose`` to also be True (raises ``ValueError``
    otherwise, checked before the escort-specific remapping); default False
    keeps the #201 behaviour -- including the exact log line -- byte-identical.

    Parameters
    ----------
    wege:
        MiD Wege with at least ``zweck_col``. ``W_GEW`` (trip weight), if
        present, is used to log a weighted escort/passive share.
    zweck_col:
        Name of the MiD W_ZWECK column.
    escort_purpose:
        If True (issue #201), map ``ESCORT_W_ZWECK`` codes to ``"escort"``.
    escort_passive_education:
        If True (issue #256), further relabel the passive leg (W_ZWECK 13) to
        ``"education"``. Requires ``escort_purpose=True``.
    w_zweck_10_as_leisure:
        If True (issue #373, ADR-0111), remap W_ZWECK 10 ("anderer Zweck") to
        ``"leisure"`` instead of ``"other"``, following MiD's own hwzweck1
        fold (100 % of code-10 legs fold to 6 Freizeit; see the committed
        evidence table ``mid2023_w_zweck_by_hwzweck1.csv``). Default False
        keeps every existing caller byte-identical; the production default is
        wired in a later task (issue #373 task 2).
    escort_passive_from_adult:
        If True (issue #372, ADR-0112), a PAIRED passive escort leg (W_ZWECK
        13) takes the purpose derived from the accompanying adult's W_ZWECK
        (``passive_purpose_for_pairs`` over the pairing
        ``braunschweig.popsim.escort_pairing.pair_passive_legs`` finds)
        instead of the flat ``escort_passive_education`` relabel: the child is
        wherever the adult went, which is their own Kita/school for only ~21 %
        of the legs. An UNPAIRED passive leg keeps the existing mapping (the
        ``escort_passive_education`` rule), and the paired/unpaired split is
        logged. Requires ``escort_purpose=True`` (and, for the "education"
        reading of the adult's own escort leg, ``escort_passive_education``).
        Default False keeps every existing caller byte-identical, INCLUDING
        the output columns (the pairing columns are added only when the flag
        is on).
    passive_pair_max_gap_minutes:
        Maximum |departure-time gap| in MINUTES for a passive leg to count as
        paired with an adult leg; forwarded verbatim to ``pair_passive_legs``.
        Inert unless ``escort_passive_from_adult`` is True. Default
        :data:`DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES`.
    pairing_candidate_mask:
        Issue #373 task 2 (ruling C-R20/C-R21). Optional boolean ``pd.Series``,
        indexed IDENTICALLY to ``wege`` (raises ``ValueError`` otherwise),
        restricting which legs the passive-escort PAIRING may consider -- as
        both a possible passive leg AND a possible candidate adult leg -- to
        ``wege[pairing_candidate_mask]``. Inert unless
        ``escort_passive_from_adult`` is True.

        This caller (``braunschweig.popsim.trips_stage`` via
        ``expand_persons_to_trips``) already restricts ``wege`` to
        :func:`legs_kept_by_the_trip_build` BEFORE calling this function, so
        its own pairing naturally sees only the legs the plan will realise.
        A caller that must keep every leg in its OWN output -- for example
        ``braunschweig.popsim.distance_distributions``, whose distance pool
        must include every leg regardless of the trip build's leg-drop flags
        -- has no such pre-filter to rely on, so without this mask its pairing
        could pick a leg the plan never realises (an excluded rbW summary leg
        or a dropped leading arrive-home leg) as the nearest-in-time adult
        candidate, resolving a purpose the plan does not.

        A passive leg OUTSIDE the mask is not considered for pairing at all
        and keeps the existing passive rule (the ``escort_passive_education``
        relabel applied above), exactly like an UNPAIRED leg -- the mask
        narrows the universe the pairing runs on, it does not change what an
        excluded leg's purpose falls back to. The DISTANCE POOL (or whatever
        else the caller does with ``wege``) is entirely unaffected by this
        mask: only the pairing's candidate universe is restricted, every row
        of ``wege`` is still returned. Default ``None`` reproduces today's
        behaviour byte-identically (the pairing considers every row of
        ``wege``).

    Returns
    -------
    pd.DataFrame
        ``wege`` with an added ``purpose`` column, plus -- only when
        ``escort_passive_from_adult`` is True -- the
        ``escort_pairing.PAIRING_COLUMNS`` traceability columns (which adult
        leg each passive leg was paired with, and why an unpaired one was not).

    Raises
    ------
    ValueError
        If ``escort_passive_education`` or ``escort_passive_from_adult`` is
        True while ``escort_purpose`` is False (there is no passive side to
        split off without the dedicated escort purpose being active), or if
        ``escort_passive_from_adult`` is True and ``wege`` lacks a column the
        pairing needs.
    """
    out = wege.copy()
    codes = out[zweck_col]
    mapped = codes.map(PURPOSE_BY_W_ZWECK)

    # Coverage guard (issue #241, CLAUDE.md fallback transparency): a code the table does not
    # know still falls back to DEFAULT_PURPOSE -- dropping the leg would be worse -- but it is
    # now COUNTED and named. A new MiD edition adding a code must not pass unnoticed again.
    unmapped = mapped.isna()
    if bool(unmapped.any()):
        if "W_GEW" in out.columns:
            weights = out["W_GEW"].astype(float)
            total = float(weights.sum())
            share = float(weights[unmapped].sum() / total) if total else 0.0
            basis = "W_GEW-weighted"
        else:
            share = float(unmapped.mean())
            basis = "unweighted"
        logger.warning(
            "[popsim.trips] map_purpose: W_ZWECK code(s) %s are not in PURPOSE_BY_W_ZWECK "
            "and fall back to %r -- %d/%d legs (%.2f%% %s). Every code the codeplan "
            "documents is mapped, so this means a NEW code: add it to the table explicitly "
            "instead of leaving it to the fallback (issue #241).",
            sorted(set(codes[unmapped].dropna().tolist())), DEFAULT_PURPOSE,
            int(unmapped.sum()), len(out), 100.0 * share, basis,
        )
    out["purpose"] = mapped.fillna(DEFAULT_PURPOSE)

    if not explicit_round_trip_purposes:
        # Pre-#241 behaviour for the A/B: the round-trip leisure codes go back to "other".
        reverted = codes.isin(ROUND_TRIP_LEISURE_W_ZWECK)
        out.loc[reverted, "purpose"] = DEFAULT_PURPOSE
        logger.info(
            "[popsim.trips] explicit_round_trip_purposes OFF: W_ZWECK %s reverted to %r "
            "(%d legs) -- pre-#241 assignment for the A/B",
            sorted(ROUND_TRIP_LEISURE_W_ZWECK), DEFAULT_PURPOSE, int(reverted.sum()))
    if w_zweck_10_as_leisure:
        is_code_10 = codes == W_ZWECK_OTHER_CODE
        out.loc[is_code_10, "purpose"] = LEISURE_PURPOSE
        if "W_GEW" in out.columns:
            total_weight = float(out["W_GEW"].astype(float).sum())
            share = float(out.loc[is_code_10, "W_GEW"].astype(float).sum() / total_weight) if total_weight else 0.0
            basis = "W_GEW-weighted"
        else:
            share = float(is_code_10.mean()) if len(out) else 0.0
            basis = "unweighted"
        logger.info("[popsim.trips] w_zweck_10_as_leisure ON: W_ZWECK 10 'anderer Zweck' -> 'leisure' for %d/%d legs "
                    "(%.2f%% %s), following MiD's hwzweck1 fold (ADR-0111)",
                    int(is_code_10.sum()), len(out), 100.0 * share, basis)
    if escort_passive_education and not escort_purpose:
        raise ValueError(
            "[popsim.trips] escort_passive_education requires escort_purpose to be ON "
            "(without a dedicated escort purpose there is no passive side to split off)."
        )
    if escort_passive_from_adult and not escort_purpose:
        raise ValueError(
            "[popsim.trips] escort_passive_from_adult requires escort_purpose to be ON "
            "(without a dedicated escort purpose there is no passive side to re-derive "
            "from the accompanying adult's leg)."
        )
    if escort_purpose:
        escort_mask = out[zweck_col].isin(ESCORT_W_ZWECK)
        out.loc[escort_mask, "purpose"] = "escort"
        # 13 is the ONLY code in ESCORT_W_ZWECK currently classified passive; if a
        # future code is ever added to ESCORT_W_ZWECK it must be explicitly
        # classified active-or-passive here too, not silently assumed active.
        passive_mask = out[zweck_col] == 13
        if escort_passive_education:
            # Issue #256: W_ZWECK 13 is the escorted person's OWN (passive) leg --
            # 100% minors on the raw file. It becomes the child's own education
            # trip, anchored at their own assigned Kita/school by the plan-based
            # primary machinery (has_education_trip covers both chain sides).
            out.loc[passive_mask, "purpose"] = "education"
        if "W_GEW" in out.columns:
            weights = out["W_GEW"].astype(float)
            total = float(weights.sum())
            share_active = float(weights[escort_mask & ~passive_mask].sum() / total) if total else 0.0
            share_passive = float(weights[passive_mask].sum() / total) if total else 0.0
            basis = "W_GEW-weighted"
        else:
            share_active = float((escort_mask & ~passive_mask).mean()) if len(out) else 0.0
            share_passive = float(passive_mask.mean()) if len(out) else 0.0
            basis = "unweighted"
        if escort_passive_education:
            logger.info(
                "[popsim.trips] escort_passive_education ON: active W_ZWECK 6 -> "
                "'escort' %d legs (%.2f%% %s); passive W_ZWECK 13 -> 'education' "
                "%d legs (%.2f%%) at the child's own school.",
                int((escort_mask & ~passive_mask).sum()), 100.0 * share_active, basis,
                int(passive_mask.sum()), 100.0 * share_passive,
            )
        else:
            logger.info(
                "[popsim.trips] escort_purpose ON: %d/%d legs (%.2f%% %s) mapped to "
                "'escort' (W_ZWECK in %s)",
                int(escort_mask.sum()), len(out), 100.0 * (share_active + share_passive),
                basis, sorted(ESCORT_W_ZWECK),
            )
    if escort_passive_from_adult:
        # Runs AFTER the escort block on purpose: every code-13 leg has already received the
        # passive rule, so the block below only has to OVERWRITE the legs it could pair, and an
        # unpaired leg keeps that rule without any second code path deciding it.
        #
        # Imported HERE rather than at module level: escort_pairing imports mid_time_seconds
        # from THIS module, so a module-level import would be a cycle.
        from braunschweig.popsim.escort_pairing import (
            PAIRING_COLUMNS, PASSIVE_W_ZWECK, REQUIRED_COLUMNS, STATUS_PAIRED,
            pair_passive_legs,
        )
        if zweck_col != "W_ZWECK":
            raise ValueError(
                f"[popsim.trips] escort_passive_from_adult=True is only defined for the MiD "
                f"purpose column 'W_ZWECK' (got zweck_col={zweck_col!r}); the pairing reads "
                "W_ZWECK directly to find the accompanying adult's leg.")
        missing = [column for column in REQUIRED_COLUMNS if column not in out.columns]
        if missing:
            raise ValueError(
                f"[popsim.trips] escort_passive_from_adult=True requires the MiD Wege column(s) "
                f"{missing}, which this frame does not carry (has {sorted(out.columns)[:20]} ...). "
                "map_purpose is called on the RAW Wege frame (before the synthetic-person join), "
                "so the household id, the member's age and the departure time needed to find the "
                "accompanying adult's leg must all still be present; load them or set "
                "escort_passive_from_adult to False.")

        if pairing_candidate_mask is None:
            # Today's behaviour, kept BYTE-IDENTICAL: the pairing considers every leg in
            # `out` as both a possible passive leg and a possible candidate adult leg.
            paired_frame, pairing = pair_passive_legs(
                out, max_gap_minutes=passive_pair_max_gap_minutes)
            is_paired = (paired_frame["passive_pair_status"] == STATUS_PAIRED).to_numpy()
            out.loc[is_paired, "purpose"] = passive_purpose_for_pairs(
                paired_frame.loc[is_paired, "passive_pair_adult_w_zweck"],
                escort_passive_education=escort_passive_education,
                w_zweck_10_as_leisure=w_zweck_10_as_leisure)
            # Carried as extras so a downstream analysis can see WHICH adult leg a child's
            # purpose came from, and why an unpaired leg kept the passive rule.
            for column in PAIRING_COLUMNS:
                out[column] = paired_frame[column].values
            n_paired = int(is_paired.sum())
            n_passive = int(pairing["n_passive"])
        else:
            # Issue #373 task 2 (ruling C-R20/C-R21): restrict the pairing's CANDIDATE
            # UNIVERSE -- both the passive legs it tries to pair and the adult legs it may
            # pair them with -- to pairing_candidate_mask, without dropping any row from
            # `out` itself (see the parameter's docstring above for why a caller like
            # distance_distributions.run needs this: it must keep every leg for its own
            # distance pool, but its pairing should agree with the trip build's about which
            # legs even exist to be paired).
            if not pairing_candidate_mask.index.equals(out.index):
                raise ValueError(
                    "[popsim.trips] escort_passive_from_adult: pairing_candidate_mask must "
                    "be indexed identically to the Wege frame passed to map_purpose (got a "
                    "mismatched index); the mask restricts which passive AND candidate-adult "
                    "legs the pairing considers, so a misaligned index would silently pair "
                    "the wrong rows.")
            # MINOR 7 fix (cleanup wave fix round): a duplicate-labelled index makes the
            # `.loc[paired_frame.index, column] = ...` assignment below raise an opaque pandas
            # error ("Must have equal len keys and value when setting with an iterable") --
            # fail loudly and name the count up front instead, since `.loc` by label requires
            # a unique index to mean what this function assumes it means.
            if out.index.has_duplicates:
                duplicate_labels = out.index[out.index.duplicated(keep=False)].unique()
                raise ValueError(
                    f"[popsim.trips] escort_passive_from_adult: the Wege frame's index has "
                    f"{len(duplicate_labels)} duplicate label(s) (e.g. "
                    f"{sorted(duplicate_labels.tolist())[:10]}); pairing_candidate_mask "
                    "restricts rows by index label via .loc, which requires a unique index. "
                    "Call reset_index(drop=True) on the Wege frame before map_purpose.")
            # MINOR 6 fix (cleanup wave fix round): pd.Series.astype(bool) casts NaN to True
            # (NaN is a nonzero float), which would silently ADD a leg to the pairing's
            # candidate universe instead of failing loudly -- fail before that cast can happen.
            if pairing_candidate_mask.isna().any():
                n_nan = int(pairing_candidate_mask.isna().sum())
                raise ValueError(
                    f"[popsim.trips] escort_passive_from_adult: pairing_candidate_mask has "
                    f"{n_nan} NaN value(s); astype(bool) would silently cast a NaN to True, "
                    "including that leg in the pairing's candidate universe by accident. "
                    "Every entry must be an actual True/False.")
            mask = pairing_candidate_mask.astype(bool)
            candidates = out.loc[mask]
            paired_frame, pairing = pair_passive_legs(
                candidates, max_gap_minutes=passive_pair_max_gap_minutes)
            paired_status = paired_frame["passive_pair_status"] == STATUS_PAIRED
            is_paired_series = pd.Series(False, index=out.index)
            is_paired_series.loc[paired_status.index[paired_status]] = True
            is_paired = is_paired_series.to_numpy()
            out.loc[is_paired, "purpose"] = passive_purpose_for_pairs(
                paired_frame.loc[paired_status, "passive_pair_adult_w_zweck"],
                escort_passive_education=escort_passive_education,
                w_zweck_10_as_leisure=w_zweck_10_as_leisure)
            # Every leg OUTSIDE the mask was never considered for pairing at all -- its
            # PAIRING_COLUMNS stay NaN (like a leg the None-mask path never sees any
            # differently than an unpaired one), distinguishing "not considered" from
            # "considered but unpaired" only via the (still available) mask itself.
            for column in PAIRING_COLUMNS:
                out[column] = np.nan
            out["passive_pair_status"] = out["passive_pair_status"].astype(object)
            for column in PAIRING_COLUMNS:
                out.loc[paired_frame.index, column] = paired_frame[column].values
            n_paired = int(is_paired.sum())
            n_passive = int(pairing["n_passive"])
            n_passive_total = int((out[zweck_col] == PASSIVE_W_ZWECK).sum())
            n_outside_mask = n_passive_total - n_passive
            if n_outside_mask:
                logger.info(
                    "[popsim.trips] escort_passive_from_adult pairing_candidate_mask ON: %d/%d "
                    "passive legs (%.1f%%) sit OUTSIDE the pairing's candidate universe and are "
                    "not considered for pairing at all -- they keep the existing passive rule "
                    "(%r).",
                    n_outside_mask, n_passive_total,
                    100.0 * n_outside_mask / n_passive_total if n_passive_total else 0.0,
                    "education" if escort_passive_education else "escort",
                )
        # MINOR 5 fix (cleanup wave fix round): restrict the RATE denominator to the exact
        # same set n_paired/n_passive already count over -- the whole frame when
        # pairing_candidate_mask is None (today's behaviour, byte-identical: pairing_universe
        # is then all-True, so this is a no-op), or the mask itself otherwise. Computing
        # is_passive_leg over the WHOLE frame while n_passive came from pair_passive_legs'
        # OWN mask-internal count (the masked branch above) silently understated share_paired
        # whenever a passive leg outside the mask carried weight: that leg was never even a
        # pairing candidate, so it must not inflate the denominator of "share of the legs the
        # pairing actually considered that got paired".
        if pairing_candidate_mask is None:
            pairing_universe = np.ones(len(out), dtype=bool)
            universe_description = "in the Wege frame"
        else:
            pairing_universe = mask.to_numpy()
            universe_description = "inside the pairing's candidate universe"
        is_passive_leg = (out[zweck_col] == PASSIVE_W_ZWECK).to_numpy() & pairing_universe
        if "W_GEW" in out.columns:
            weights = out["W_GEW"].astype(float).to_numpy()
            passive_weight = float(weights[is_passive_leg].sum())
            share_paired = float(weights[is_paired].sum() / passive_weight) if passive_weight else 0.0
            basis = "W_GEW-weighted"
        else:
            share_paired = (n_paired / n_passive) if n_passive else 0.0
            basis = "unweighted"
        # The resulting purposes of the RELABELLED legs: a distribution collapsed onto a single
        # purpose is the signature of a broken pairing (e.g. every adult leg resolving to the
        # same code), which the count alone would not show.
        distribution = out.loc[is_paired, "purpose"].value_counts().to_dict()
        logger.info(
            "[popsim.trips] escort_passive_from_adult ON: %d/%d passive legs %s (%.2f%% %s) take "
            "the paired adult's purpose %s; %d unpaired legs keep the passive rule (%r). "
            "Pairing outcome: %s",
            n_paired, n_passive, universe_description, 100.0 * share_paired, basis, distribution,
            n_passive - n_paired, "education" if escort_passive_education else "escort",
            {key: value for key, value in pairing.items() if key != "share_paired"},
        )
    return out


def map_mode(wege: pd.DataFrame, *, hvm_col: str = "hvm_imp") -> pd.DataFrame:
    """Add the eqasim ``mode`` from MiD imputed main mode ``hvm_imp``.

    Raises on any unmapped code (no silent walk fallback)."""
    out = wege.copy()
    mapped = out[hvm_col].map(MODE_BY_HVM)
    if mapped.isna().any():
        bad = out.loc[mapped.isna(), hvm_col].value_counts().to_dict()
        raise ValueError(f"[popsim.trips] unmapped {hvm_col} codes: {bad}")
    out["mode"] = mapped
    return out


def mid_time_seconds(wege: pd.DataFrame, hour_col: str, minute_col: str) -> pd.Series:
    """Seconds since midnight from MiD hour + minute columns.

    The MiD Wege time fields (W_SZS/W_SZM/W_AZS/W_AZM) carry missing/design
    codes OUTSIDE the valid clock range, audited against the raw data: 99
    ("keine Angabe", item non-response) and 701 ("bei regelmaessigen
    beruflichen Wegen nicht erhoben", design code for rbW summary records) in
    BOTH the hour and the minute fields. Any out-of-range value (hour not in
    0..23, minute not in 0..59) invalidates the time and returns NaN, so coded
    rows are NOT converted to multi-day timestamps that survive the downstream
    trip-time repairs; the owning person is then classified unfixable and
    replaced by the same-cell resample. A range check is used instead of an
    explicit code list because 9 is a VALID minute (and hour) — only values
    outside the clock range are codes.
    """
    hours = wege[hour_col].astype(float)
    minutes = wege[minute_col].astype(float)
    coded = (~hours.between(0, 23)) | (~minutes.between(0, 59))
    seconds = hours * 3600.0 + minutes * 60.0
    seconds[coded] = float("nan")
    return seconds


# Ordered tuple of columns that constitute the eqasim trip schema subset produced by
# build_trip_table.  Downstream stages (data/hts/hts.py fix/validate and
# synthesis/population/activities.py) expect exactly these columns; all other MiD
# Wege columns are carried through as extras.
EQASIM_TRIP_COLUMNS = (
    "person_id",
    "trip_id",
    "departure_time",
    "arrival_time",
    "trip_duration",
    "activity_duration",
    "preceding_purpose",
    "following_purpose",
    "is_first_trip",
    "is_last_trip",
    "mode",
)


def build_trip_table(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    household_col: str = "H_ID",
    person_col: str = "P_ID",
    trip_col: str = "W_ID",
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
    explicit_round_trip_purposes: bool = True,
    exclude_rbw_legs: bool = False,
    drop_leading_arrive_home_leg: bool = False,
    w_zweck_10_as_leisure: bool = False,
    escort_passive_from_adult: bool = False,
    passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
) -> pd.DataFrame:
    """Map MiD Wege onto synthetic persons into the eqasim trip schema (+ extras).

    Mirrors ``data/hts/entd/cleaned.py`` exactly, reusing the shared helpers from
    ``data/hts/hts.py`` in the same order as the ENTD path:

    0a. (optional, ``exclude_rbw_legs``) Drop rbW legs (``W_RBW == 1``) before
        the join; a donor person left with no legs is dropped (and counted).
    0b. (optional, ``drop_leading_arrive_home_leg``) Drop a donor person's first
        leg when it both arrives home and started elsewhere (``W_SO1 == 2``),
        applied after step 0a. Both steps run inside
        ``expand_persons_to_trips``, before the purpose/mode mapping.
    1. ``expand_persons_to_trips`` — join donor Wege onto synthetic persons, map
       purpose and mode, produce a string ``trip_key`` (``<person_id>_<W_ID>``) for
       traceability.
    2. Sort by ``(person_id, trip_col)``; assign an integer global ``trip_id``
       (0..n-1) so that ``hts.compute_first_last`` sorts trips correctly within
       each person.
    3. ``hts.compute_first_last`` — sorts by ``(person_id, trip_id)`` and sets
       ``is_first_trip`` / ``is_last_trip``.
    4. ``preceding_purpose``: per-person shift of ``following_purpose``.
       **ASSUMPTION**: MiD travel diaries start at home, so the first trip's
       ``preceding_purpose`` is hard-set to ``"home"``.  This is the standard
       diary-starts-at-home convention used throughout eqasim.  A log message
       reports the COUNT of first trips this assumption is applied to (the
       magnitude), and explicitly does NOT report a destination-based percentage
       because a first trip almost never has home as its destination — such a
       figure would look like validation while checking nothing about the
       origin.
    5. ``departure_time`` / ``arrival_time`` in float seconds since midnight via
       ``mid_time_seconds``.
    6. ``hts.fix_trip_times`` — repairs negative durations (swap / +24 h midnight
       crossing) and overlapping trips; essential for MiD diaries crossing midnight.
    7. ``trip_duration = arrival_time - departure_time``; ``hts.compute_activity_duration``
       (NaN on last trip of each person).
    8. ``hts.fix_activity_types`` — enforces ``following_purpose[i] == preceding_purpose[i+1]``.
    9. Integer per-person ``trip_index`` = 0-based cumcount (the column consumed by
       ``synthesis/population/activities.py``).

    Produces one row per (synthetic person, MiD trip) with the columns listed in
    ``EQASIM_TRIP_COLUMNS`` (plus ``trip_key``, ``trip_index``, and all original
    MiD Wege columns) so that the eqasim trip-time fix/validation layer and
    activity-chain construction apply unchanged to popsim_mid trips.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
        One row per unique synthetic person is expected; duplicates on
        ``person_id`` are dropped before the join so that each unique synthetic
        person gets exactly one copy of the donor trip chain (avoids a
        person x wege cross-join that would produce duplicate trip_key values).
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.  All columns are preserved.
    household_col:
        Name of the household-ID column shared by ``persons`` and ``mid_wege``.
    person_col:
        Name of the within-household person-ID column shared by both frames.
    trip_col:
        Name of the within-person trip-sequence column in ``mid_wege`` (used to
        build the unique ``trip_key`` and to sort trips within each person).
    escort_purpose:
        If True (issue #201), W_ZWECK codes in ``ESCORT_W_ZWECK`` map to the
        dedicated ``"escort"`` purpose instead of ``"other"`` (forwarded to
        ``map_purpose`` via ``expand_persons_to_trips``). Default False keeps
        the OFF path byte-identical.
    escort_passive_education:
        If True (issue #256), the passive escort leg (W_ZWECK 13) maps to
        ``"education"`` instead of ``"escort"`` (forwarded to ``map_purpose``
        via ``expand_persons_to_trips``). Requires ``escort_purpose=True``.
        Default False keeps the OFF path byte-identical.
    exclude_rbw_legs:
        If True, drop rbW legs (``W_RBW == 1``) before the join (forwarded to
        ``expand_persons_to_trips``); see that function's docstring for the
        rationale and the emptied-persons handling. Default False keeps the
        OFF path byte-identical.
    drop_leading_arrive_home_leg:
        If True, drop a donor person's leading "arrive home from elsewhere"
        leg (forwarded to ``expand_persons_to_trips``); see that function's
        docstring for the rationale. Default False keeps the OFF path
        byte-identical.
    w_zweck_10_as_leisure:
        If True (issue #373, ADR-0111), remap W_ZWECK 10 ("anderer Zweck") to
        ``"leisure"`` instead of ``"other"`` (forwarded to ``map_purpose`` via
        ``expand_persons_to_trips``). Default False keeps the OFF path
        byte-identical.
    escort_passive_from_adult:
        If True (issue #372, ADR-0112), a PAIRED passive escort leg (W_ZWECK
        13) takes the purpose derived from the accompanying adult's W_ZWECK
        instead of the flat ``escort_passive_education`` relabel; an UNPAIRED
        one keeps that relabel (forwarded to ``map_purpose`` via ``expand_persons_to_trips``). Requires
        ``escort_purpose=True``. Default False keeps the OFF path
        byte-identical.
    passive_pair_max_gap_minutes:
        Maximum |departure-time gap| in MINUTES for a passive leg to count as
        paired (forwarded to ``map_purpose`` via ``expand_persons_to_trips``). Inert unless
        ``escort_passive_from_adult`` is True.

    Returns
    -------
    pd.DataFrame
        One row per (synthetic person, MiD trip) sorted by ``(person_id, trip_col)``,
        containing the full eqasim trip schema (see ``EQASIM_TRIP_COLUMNS``) plus
        ``trip_key`` (string traceability id), ``trip_index`` (per-person 0-based
        integer for activities.py), and all original MiD Wege columns.
    """
    # One trip chain per unique synthetic person; avoids a person x wege cross-join
    # that would produce duplicate trip_key values when the caller passes a persons
    # frame that has already been exploded (e.g. one row per household member).
    persons = persons.drop_duplicates(subset="person_id")

    # Member completion (braunschweig.popsim.member_completion): a filler person
    # carries a synthetic (host H_ID, fresh P_ID) pair that does NOT exist in the
    # MiD Wege file, so joining on it would silently give fillers no trips. The
    # total traceability columns source_H_ID / source_P_ID reference the MIRROR
    # donor for fillers and the own ids for regular persons, so using them as the
    # effective join keys gives fillers the mirror's Wege and leaves everyone
    # else unchanged. Frames without the columns (legacy path) join as before.
    if "source_H_ID" in persons.columns and "source_P_ID" in persons.columns:
        persons = persons.assign(**{household_col: persons["source_H_ID"],
                                    person_col: persons["source_P_ID"]})

    # Step 1: join donor Wege, map purpose and mode.
    # expand_persons_to_trips produces a string trip_id (<person_id>_<W_ID>)
    # which we rename to trip_key for traceability; a global integer trip_id is
    # assigned below so hts.compute_first_last sorts correctly.
    df = expand_persons_to_trips(
        persons,
        mid_wege,
        household_col=household_col,
        person_col=person_col,
        trip_col=trip_col,
        escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
    )

    # Step 2: sort by (person_id, trip_col); assign integer trip_id (0..n-1).
    df = df.sort_values(["person_id", trip_col]).reset_index(drop=True)
    df = df.rename(columns={"trip_id": "trip_key"})
    df["trip_id"] = range(len(df))

    # Step 3: hts.compute_first_last returns a (re-sorted) DataFrame with
    # is_first_trip / is_last_trip set.  It sorts by (person_id, trip_id), which
    # is correct because trip_id is now a global integer reflecting within-person
    # order from the sort above.
    df = hts.compute_first_last(df)

    # Step 4: purpose columns.
    # following_purpose = destination activity mapped from W_ZWECK.
    df["following_purpose"] = df["purpose"]
    # preceding_purpose = destination of the previous trip within the same person.
    df["preceding_purpose"] = df.groupby("person_id")["following_purpose"].shift(1)
    # ASSUMPTION: MiD travel diaries start at home (diary-starts-at-home convention).
    # The first trip of each person therefore departs from home regardless of what
    # W_ZWECK recorded.  This is standard eqasim behaviour (mirrors entd/cleaned.py).
    df.loc[df["is_first_trip"], "preceding_purpose"] = "home"

    # Make the home-start ASSUMPTION observable (no silent assumption). MiD records
    # no per-trip origin purpose (only the destination W_ZWECK), so the first trip's
    # origin CANNOT be validated from the data; we apply the diary-starts-at-home
    # convention to every person's first trip. Log the magnitude (how many first
    # trips this touches). The complementary, data-checkable quantity is the home-END
    # closure repair rate, logged by PlanValidator (the day's end IS in the data via
    # the W_ZWECK home codes 8/9). We deliberately do NOT report a destination-based
    # percentage here: a first trip's destination is almost never home, so such a
    # number would look like validation while checking nothing about the origin.
    n_first_trips = int(df["is_first_trip"].sum())
    logger.info(
        "[popsim.trips] home-start assumption applied to %d first trips "
        "(MiD has no per-trip origin purpose; diary-starts-at-home convention, "
        "mirrors entd/cleaned.py). Home-END closure is checked/repaired by PlanValidator.",
        n_first_trips,
    )

    # Step 5: trip times in seconds since midnight.
    df["departure_time"] = mid_time_seconds(df, "W_SZS", "W_SZM").to_numpy()
    df["arrival_time"] = mid_time_seconds(df, "W_AZS", "W_AZM").to_numpy()

    # Step 6: fix_trip_times repairs negative durations (swap / +24h midnight
    # crossing) and overlapping trips — essential for MiD diaries crossing midnight.
    # The function mutates df in place and also returns it.
    df = hts.fix_trip_times(df)

    # Step 7: trip_duration and activity_duration (NaN on last trip of each person).
    df["trip_duration"] = df["arrival_time"] - df["departure_time"]
    hts.compute_activity_duration(df)

    # Step 8: fix_activity_types enforces following_purpose[i] == preceding_purpose[i+1].
    # Mutates df in place, returns None.
    hts.fix_activity_types(df)

    # Step 9: per-person 0-based trip_index consumed by synthesis/population/activities.py.
    df["trip_index"] = df.groupby("person_id").cumcount()

    return df


def expand_persons_to_trips(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    household_col: str = "H_ID",
    person_col: str = "P_ID",
    trip_col: str = "W_ID",
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
    explicit_round_trip_purposes: bool = True,
    exclude_rbw_legs: bool = False,
    drop_leading_arrive_home_leg: bool = False,
    w_zweck_10_as_leisure: bool = False,
    escort_passive_from_adult: bool = False,
    passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
) -> pd.DataFrame:
    """Join the donor MiD Wege onto the synthetic persons -> one row per trip.

    Each synthetic person (``person_id``, referencing donor ``(H_ID, P_ID)``) gets
    the donor person's trips, with the purpose and mode mapped to the eqasim
    vocabulary and a unique ``trip_id`` (``<person_id>_<W_ID>``). Persons whose
    donor has no Wege are dropped (they make no trips).

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``.
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.
    exclude_rbw_legs:
        If True, drop legs with ``W_RBW == 1`` (regelmaessiger beruflicher Weg --
        a regular-commuter summary record standing in for the diary leg, see
        ``time_imputation.py``'s module docstring) BEFORE the purpose/mode
        mapping. A donor person whose Wege become empty as a result is dropped
        from the table (same as a donor without Wege today) and COUNTED; if
        that count is > 0 a warning is logged with the hint that
        ``braunschweig.population.popsim.diary_plan_match`` should have
        remapped those persons upstream. The count is taken over the donors
        REFERENCED by ``persons`` (the synthetic universe), not over the whole
        Wege table: emptying a donor nobody sources their plan from has no
        effect on any plan, and counting those made the warning fire on every
        production run regardless of whether the remap worked (controller
        ruling R21). Default False keeps every existing caller byte-identical.
    drop_leading_arrive_home_leg:
        If True, drop each donor person's FIRST leg (by ``trip_col`` order)
        when it both arrives home (``W_ZWECK`` in {8, 9}) and started from
        elsewhere (``W_SO1 == 2``): such a leg is not the diary's actual first
        trip (which starts at home by the diary-starts-at-home convention),
        it is a leftover "arrive home" record from before the observed diary
        window. Applied AFTER ``exclude_rbw_legs``. A donor person whose Wege
        become empty as a result (their only leg WAS the dropped leading
        arrive-home leg) is dropped from the table and COUNTED the same way
        as ``exclude_rbw_legs`` (over the REFERENCED donors only, ruling R21);
        if that count is > 0 a warning is logged with the same diary-plan-match
        hint. Default False keeps every existing caller byte-identical.
    w_zweck_10_as_leisure:
        If True (issue #373, ADR-0111), remap W_ZWECK 10 ("anderer Zweck") to
        ``"leisure"`` instead of ``"other"`` (forwarded to ``map_purpose``).
        Default False keeps every existing caller byte-identical.
    escort_passive_from_adult:
        If True (issue #372, ADR-0112), a PAIRED passive escort leg (W_ZWECK
        13) takes the purpose derived from the accompanying adult's W_ZWECK
        instead of the flat ``escort_passive_education`` relabel; an UNPAIRED
        one keeps that relabel (forwarded to ``map_purpose``). Requires
        ``escort_purpose=True``. Default False keeps the OFF path
        byte-identical.
    passive_pair_max_gap_minutes:
        Maximum |departure-time gap| in MINUTES for a passive leg to count as
        paired (forwarded to ``map_purpose``). Inert unless
        ``escort_passive_from_adult`` is True.

    Raises
    ------
    KeyError
        If ``exclude_rbw_legs`` is True and ``mid_wege`` lacks ``W_RBW``, or if
        ``drop_leading_arrive_home_leg`` is True and ``mid_wege`` lacks ``W_SO1``.
    """
    total = len(mid_wege)
    wege_in = mid_wege
    # The "emptied by the drop" counters below are about PLANS, so their universe is
    # the set of donors the synthetic persons actually source from -- not the whole MiD
    # Wege table, which also holds donors nobody references (controller ruling R21: a
    # whole-table count made the warning a permanent false alarm).
    referenced_donors = pd.MultiIndex.from_frame(
        persons[[household_col, person_col]].drop_duplicates()
    )

    def _n_referenced_donors_with_legs(frame: pd.DataFrame) -> int:
        """Number of REFERENCED donors that still have at least one leg in ``frame``."""
        if len(frame) == 0:
            return 0
        present = pd.MultiIndex.from_frame(frame[[household_col, person_col]].drop_duplicates())
        return int(present.isin(referenced_donors).sum())

    n_referenced = len(referenced_donors)
    if exclude_rbw_legs:
        # The rule itself lives in rbw_leg_mask (the ONE definition the seed derivations and the
        # reference-derivation scripts also use); only the emptied-donor counters below are
        # specific to this call site.
        is_rbw = rbw_leg_mask(wege_in)
        n_referenced_before = _n_referenced_donors_with_legs(wege_in)
        wege_in = wege_in[~is_rbw]
        n_emptied = n_referenced_before - _n_referenced_donors_with_legs(wege_in)
        logger.info("[popsim.trips] rbW legs dropped: %d/%d (%.2f%%); referenced donor persons emptied by "
                    "the drop: %d/%d (%.2f%%)",
                    int(is_rbw.sum()), total, 100.0 * is_rbw.sum() / max(total, 1),
                    n_emptied, n_referenced, 100.0 * n_emptied / max(n_referenced, 1))
        if n_emptied:
            logger.warning("[popsim.trips] %d donor persons referenced by this population have ONLY rbW legs "
                           "and become trip-less; with braunschweig.population.popsim.diary_plan_match on they "
                           "should have been remapped upstream (completed_donor) -- check the flags are "
                           "consistent", n_emptied)
    if drop_leading_arrive_home_leg:
        # Same split as above: leading_arrive_home_leg_index owns the rule, this call site owns
        # the counters. n_first is the population the drop RATE below is reported over.
        drop_idx = leading_arrive_home_leg_index(
            wege_in, household_col=household_col, person_col=person_col, trip_col=trip_col)
        n_first = wege_in[[household_col, person_col]].drop_duplicates().shape[0]
        n_referenced_before_arrive_home = _n_referenced_donors_with_legs(wege_in)
        wege_in = wege_in.drop(index=drop_idx)
        n_emptied_arrive_home = (
            n_referenced_before_arrive_home - _n_referenced_donors_with_legs(wege_in)
        )
        logger.info("[popsim.trips] leading arrive-home legs dropped: %d donor persons (%.2f%% of persons with Wege); "
                    "referenced donor persons emptied by the drop: %d/%d (%.2f%%)",
                    len(drop_idx), 100.0 * len(drop_idx) / max(n_first, 1),
                    n_emptied_arrive_home, n_referenced,
                    100.0 * n_emptied_arrive_home / max(n_referenced, 1))
        if n_emptied_arrive_home:
            logger.warning("[popsim.trips] %d donor persons referenced by this population have ONLY a leading "
                           "arrive-home leg and become trip-less; with "
                           "braunschweig.population.popsim.diary_plan_match on they should have been remapped "
                           "upstream (completed_donor) -- check the flags are consistent",
                           n_emptied_arrive_home)
    wege = map_mode(map_purpose(
        wege_in, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        explicit_round_trip_purposes=explicit_round_trip_purposes,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes,
    ))
    merged = persons.merge(
        wege, on=[household_col, person_col], how="inner", suffixes=("", "_weg")
    )
    merged["trip_id"] = (
        merged["person_id"].astype(str) + "_" + merged[trip_col].astype(str)
    )

    # Instrument the inner join: persons whose donor (H_ID, P_ID) has no Wege
    # row are silently dropped (they become trip-less home-only persons). A
    # low match rate almost always signals a broken donor-key join rather than
    # a genuinely immobile donor population, so log it as an explicit rate
    # (mirrors the ENTD twin, sources/entd.py build_trips).
    n_persons_total = len(persons)
    n_persons_with_trips = merged["person_id"].nunique() if n_persons_total > 0 else 0
    n_persons_without_trips = n_persons_total - n_persons_with_trips
    match_rate = n_persons_with_trips / max(n_persons_total, 1)
    logger.info(
        "[popsim.trips] expand_persons_to_trips: %d/%d persons (%.1f%%) have donor "
        "trips; %d persons without trips.",
        n_persons_with_trips, n_persons_total, 100.0 * match_rate, n_persons_without_trips,
    )
    if n_persons_total > 0 and match_rate < MIN_EXPECTED_TRIP_MATCH_RATE:
        logger.warning(
            "[popsim.trips] expand_persons_to_trips: donor-trip match rate %.1f%% is "
            "below the expected minimum %.1f%% -- this usually indicates a broken "
            "(H_ID, P_ID) join between synthetic persons and MiD Wege, not a "
            "genuinely immobile donor population.",
            100.0 * match_rate, 100.0 * MIN_EXPECTED_TRIP_MATCH_RATE,
        )

    return merged.reset_index(drop=True)


def build_validated_trip_table(
    persons: pd.DataFrame,
    mid_wege: pd.DataFrame,
    *,
    require_home_closure: bool = True,
    repair: bool = True,
    resample: bool = False,
    resample_cell_col: str | None = None,
    random_seed: int | None = None,
    escort_purpose: bool = False,
    escort_passive_education: bool = False,
    exclude_rbw_legs: bool = False,
    drop_leading_arrive_home_leg: bool = False,
    w_zweck_10_as_leisure: bool = False,
    escort_passive_from_adult: bool = False,
    passive_pair_max_gap_minutes: float = DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES,
    dwell_model=None,
    **kwargs,
):
    """Build the trip table, optionally repair + resample, return (table, ValidationReport).

    Thin convenience wrapper over build_trip_table + PlanValidator. When repair is
    True (default) the PlanValidator enforces home-end closure and logs its repair
    rates (the rates are emitted by repair_trips itself, so they remain observable
    even though the RepairReport is not returned here). When ``resample`` is True,
    unfixable persons go through a two-stage cascade:

    - Stage A (``time_imputation.impute_chain_times``): persons with the
      ``nan_times`` issue (MiD coded times 99/701 — the 701 rbW group are
      systematically REGULAR COMMUTERS) and a complete, code-free own
      ``wegmin_imp1`` keep their OWN chain (purposes/modes/distances are real)
      and only the times are imputed from empirical same-purpose pools; the
      affected persons are then re-repaired (home-end closure now applies).
    - Stage B (``_match_unfixable``): persons still unfixable after stage A
      have their whole chain replaced by the chain of an ATTRIBUTE-MATCHED
      valid donor (hierarchical-relaxation matching via
      ``synthesis.population.matched.match_donors`` on sex, age_class,
      employed, socioprofessional_class[, RegioStaR7]); behavioural
      similarity beats 100 m proximity (the legacy same-cell pool held only
      1-3 donors at 1 % sampling and 31.8 % of persons found none). The
      replaced rows carry ``chain_donor_id`` (the donor's person_id) for
      traceability. Persons that cannot be matched (no donor shares their
      ``sex``, the never-relaxed first key) become trip-less home-only
      persons — the rate is logged loudly and expected to be ~0. Persons
      frames without a ``sex`` column (minimal fixtures) fall back to the
      legacy same-cell resample (``plan_validation.resample_chains``) with a
      loud warning.

    The returned ValidationReport reflects the FINAL (post-repair, post-impute,
    post-resample) table.

    Parameters
    ----------
    persons:
        Synthetic persons with ``person_id`` + donor keys ``H_ID`` / ``P_ID``
        (+ ``resample_cell_col`` when ``resample`` is True).
    mid_wege:
        MiD Wege keyed by ``(H_ID, P_ID)``.
    require_home_closure:
        If True (default) the validator enforces home-end closure.
    repair:
        If True (default) repair fixable issues in the trip table before validation.
    resample:
        If True, replace unfixable persons' chains via the stage A/B cascade
        (time imputation, then attribute-matched donor chains).  Requires
        ``repair=True`` (the unfixable classification comes from the
        RepairReport) and a non-None ``random_seed`` (determinism is mandatory).
    resample_cell_col:
        Column in ``persons`` that defines the donor-matching cell (e.g.
        ``"ZENSUS100m"``).  Only used by the LEGACY same-cell resample path,
        which stage B falls back to when the persons frame carries no ``sex``
        column; on that path ``None`` means every unfixable person falls back
        to a home-only plan (logged loudly by resample_chains).
    random_seed:
        Seed for the stage A/B RNG streams (``np.random.RandomState``; see
        ``TIME_IMPUTATION_SEED_OFFSET`` / ``MATCHED_REPLACEMENT_SEED_OFFSET``).
    escort_purpose:
        If True (issue #201), W_ZWECK codes in ``ESCORT_W_ZWECK`` map to the
        dedicated ``"escort"`` purpose instead of ``"other"`` (forwarded to
        ``build_trip_table`` / ``map_purpose``). Default False keeps the OFF
        path byte-identical.
    escort_passive_education:
        If True (issue #256), the passive escort leg (W_ZWECK 13) maps to
        ``"education"`` instead of ``"escort"`` (forwarded to
        ``build_trip_table`` / ``map_purpose``). Requires ``escort_purpose=True``.
        Default False keeps the OFF path byte-identical.
    exclude_rbw_legs:
        If True, drop rbW legs (``W_RBW == 1``) before the join (forwarded to
        ``build_trip_table`` / ``expand_persons_to_trips``). Default False
        keeps the OFF path byte-identical.
    drop_leading_arrive_home_leg:
        If True, drop a donor person's leading "arrive home from elsewhere"
        leg (forwarded to ``build_trip_table`` / ``expand_persons_to_trips``).
        Default False keeps the OFF path byte-identical.
    w_zweck_10_as_leisure:
        If True (issue #373, ADR-0111), remap W_ZWECK 10 ("anderer Zweck") to
        ``"leisure"`` instead of ``"other"`` (forwarded to ``build_trip_table``
        / ``map_purpose``). Default False keeps the OFF path byte-identical.
    escort_passive_from_adult:
        If True (issue #372, ADR-0112), a PAIRED passive escort leg (W_ZWECK
        13) takes the purpose derived from the accompanying adult's W_ZWECK
        instead of the flat ``escort_passive_education`` relabel; an UNPAIRED
        one keeps that relabel (forwarded to ``build_trip_table`` / ``map_purpose``). Requires
        ``escort_purpose=True``. Default False keeps the OFF path
        byte-identical.
    passive_pair_max_gap_minutes:
        Maximum |departure-time gap| in MINUTES for a passive leg to count as
        paired (forwarded to ``build_trip_table`` / ``map_purpose``). Inert unless
        ``escort_passive_from_adult`` is True.
    dwell_model:
        Optional ``braunschweig.popsim.closure_dwell.ClosureDwellModel`` forwarded
        to every ``PlanValidator.repair_trips`` call this function makes
        (including the stage A re-repair inside ``_impute_nan_time_unfixable``,
        for which that re-repair is the ONLY home-end closure a stage A person
        gets) so the synthetic return-home trip's dwell time is drawn from
        observed donor activity durations instead of the constant
        ``HOME_CLOSURE_DWELL_S``. Default ``None`` keeps the constant-dwell
        behaviour.
    **kwargs:
        Passed to build_trip_table (e.g., household_col, person_col, trip_col).

    Returns
    -------
    tuple[pd.DataFrame, ValidationReport]
        The built (and optionally repaired/resampled) trip table and the
        validation report reflecting the final state.
    """
    from braunschweig.popsim.plan_validation import PlanValidator, resample_chains

    if resample and not repair:
        raise ValueError(
            "[popsim.trips] resample=True requires repair=True: the unfixable-person "
            "classification that drives the resample comes from the RepairReport."
        )
    if resample and random_seed is None:
        raise ValueError(
            "[popsim.trips] resample=True requires an explicit random_seed "
            "(deterministic donor draws are mandatory)."
        )
    if resample and resample_cell_col is not None and resample_cell_col not in persons.columns:
        raise ValueError(
            f"[popsim.trips] resample_cell_col '{resample_cell_col}' is not a column of "
            f"the persons frame (columns: {sorted(persons.columns)}). Pass None to "
            f"resample without cell matching (home-only fallback) or fix the column name."
        )

    table = build_trip_table(
        persons, mid_wege, escort_purpose=escort_purpose,
        escort_passive_education=escort_passive_education,
        exclude_rbw_legs=exclude_rbw_legs,
        drop_leading_arrive_home_leg=drop_leading_arrive_home_leg,
        w_zweck_10_as_leisure=w_zweck_10_as_leisure,
        escort_passive_from_adult=escort_passive_from_adult,
        passive_pair_max_gap_minutes=passive_pair_max_gap_minutes, **kwargs,
    )
    validator = PlanValidator(require_home_closure=require_home_closure)
    repair_report = None
    if repair:
        table, repair_report = validator.repair_trips(table, dwell_model=dwell_model)

    # Cascade stage A: time imputation for coded-time (nan_times) persons with a
    # complete own wegmin_imp1.  Runs AFTER the first repair (the nan_times
    # classification comes from its RepairReport) and BEFORE the resample; the
    # helper re-runs repair_trips on the imputed table so the now-timed chains
    # receive home-end closure, and returns the updated RepairReport whose
    # unfixable set drives stage B (the existing same-cell resample).
    if resample and repair_report is not None and repair_report.unfixable_persons:
        table, repair_report = _impute_nan_time_unfixable(
            table, repair_report, validator, random_seed=random_seed, dwell_model=dwell_model
        )

    if resample and repair_report is not None and repair_report.unfixable_persons:
        table = _match_unfixable(
            table,
            persons,
            repair_report.unfixable_persons,
            resample_cell_col=resample_cell_col,
            random_seed=random_seed,
            resample_chains=resample_chains,
        )

    report = validator.validate_trips(table)
    return table, report


def _impute_nan_time_unfixable(
    table: pd.DataFrame,
    repair_report,
    validator,
    *,
    random_seed: int,
    dwell_model=None,
):
    """Cascade stage A: keep coded-time persons' own chains, impute only the times.

    Sequencing (documented because the order is load-bearing):

    1. Runs AFTER the first ``repair_trips`` because the ``nan_times``
       classification (which persons have MiD coded times 99/701) is derived
       from its output — a person is a stage A candidate iff they are in the
       unfixable set AND still carry a NaN departure/arrival time (the exact
       condition PlanValidator flags as ``nan_times``).
    2. ``impute_chain_times`` writes times only for persons whose ``wegmin_imp1``
       is complete and code-free on every trip; everyone else stays NaN.
    3. The FULL table is then re-repaired: the imputed persons were excluded
       from the first home-end closure pass (NaN times), so the second pass
       closes their chains, recomputes trip_id/first-last/durations/trip_index
       globally, and re-classifies.  Re-repairing the full table (instead of a
       subset) is safe because repair_trips is a no-op for already-repaired
       chains (fix_trip_times leaves consistent times unchanged, closure only
       appends for non-home-ending persons, the recompute step is idempotent)
       and it avoids a fragile subset-concat-recompute sequence.
    4. The second RepairReport's unfixable set (imputation-skipped persons,
       plus any chain the closure append pushed past the plan bound) flows to
       stage B, the existing same-cell resample, unchanged.

    The stage A RNG is a child stream of the caller's ``random_seed``
    (``RandomState(random_seed + TIME_IMPUTATION_SEED_OFFSET)``) so the
    imputation draws are decorrelated from the resample and jitter streams,
    which both consume ``RandomState(random_seed)`` directly.

    ``dwell_model`` is forwarded to the re-repair's ``repair_trips`` call: for
    stage A persons the re-repair IS their only home-end closure (their NaN
    times excluded them from the first pass entirely), so without threading it
    here they would silently get the constant ``HOME_CLOSURE_DWELL_S`` even
    when the caller asked for the empirical model.
    """
    import numpy as np

    from braunschweig.popsim.time_imputation import (
        TIME_IMPUTATION_SEED_OFFSET,
        impute_chain_times,
    )

    nan_rows = table["departure_time"].isna() | table["arrival_time"].isna()
    nan_time_persons = (
        set(table.loc[nan_rows, "person_id"].unique()) & repair_report.unfixable_persons
    )
    if not nan_time_persons:
        return table, repair_report

    if "wegmin_imp1" not in table.columns:
        # The MiD delivery always carries wegmin_imp1 (MID_WEGE_REQUIRED_COLS);
        # other donor sources (e.g. ENTD) do not.  Without it stage A cannot
        # run — observable, then the legacy stage B resample handles everyone.
        logger.warning(
            "[popsim.trips] stage A time imputation unavailable: column "
            "'wegmin_imp1' is missing from the trip table; %d nan-time persons "
            "go straight to the stage B resample.",
            len(nan_time_persons),
        )
        return table, repair_report

    rng = np.random.RandomState(random_seed + TIME_IMPUTATION_SEED_OFFSET)
    table, imputation_report = impute_chain_times(
        table,
        nan_time_persons,
        rng=rng,
        max_plan_time_seconds=validator.max_plan_time_seconds,
    )

    if imputation_report.n_imputed == 0:
        # Nothing changed; keep the first report (and skip a redundant repair).
        return table, repair_report

    table, second_report = validator.repair_trips(table, dwell_model=dwell_model)
    return table, second_report


# Matching keys for the stage B attribute-matched chain replacement, in
# priority order: match_donors relaxes keys FROM THE END of this list, so the
# order encodes priority and the FIRST key (sex) is never relaxed.  Mirrors the
# ENTD diary-donor chain matching (braunschweig.popsim.sources.entd).  Keys
# missing from the persons frame are dropped with a logged warning.
# RegioStaR7 is the SYNTHETIC HOME's cell RS7, joined onto the merged
# PopulationSim output by braunschweig.popsim.stage.join_cell_attributes and
# expanded onto every person; older cell parquets without the column trigger
# the logged drop and the matching falls back to the non-spatial key list.
MATCHED_REPLACEMENT_COLUMNS = [
    "sex", "age_class", "employed", "socioprofessional_class", "RegioStaR7",
]

# Continuous matching columns derived from a differently-named raw column.
_MATCHED_REPLACEMENT_SOURCE_COLUMN = {"age_class": "age"}

# Child-stream offset for the stage B matching RNG: match_donors consumes
# RandomState(random_seed + MATCHED_REPLACEMENT_SEED_OFFSET), decorrelated from
# the jitter / legacy-resample streams (RandomState(random_seed)) and the
# stage A imputation stream (random_seed + TIME_IMPUTATION_SEED_OFFSET = 4159).
MATCHED_REPLACEMENT_SEED_OFFSET = 7211

# Below this donor-match rate, expand_persons_to_trips is assumed to be hitting a
# join defect (wrong key, format mismatch) rather than the expected small share of
# genuinely immobile / unmatched MiD donors. ASSUMPTION: chosen conservatively, not
# fitted to observed data; revisit if a legitimate high-immobility scenario trips it.
MIN_EXPECTED_TRIP_MATCH_RATE = 0.5


def _recompute_chain_ids(table: pd.DataFrame) -> pd.DataFrame:
    """Re-derive trip_id / first-last / durations / trip_index on the final frame.

    Same recompute sequence as ``PlanValidator.repair_trips`` step 4: replaced
    donor chains carry the DONOR's stale ``trip_id`` / ``trip_index``, which
    would otherwise collide with the kept persons' rows.
    """
    from data.hts import hts

    table = table.sort_values(["person_id", "departure_time"]).reset_index(drop=True)
    table["trip_id"] = range(len(table))
    table = hts.compute_first_last(table)
    table["trip_duration"] = table["arrival_time"] - table["departure_time"]
    hts.compute_activity_duration(table)  # modifies in-place, no return
    table["trip_index"] = table.groupby("person_id").cumcount()
    return table


def _match_unfixable(
    table: pd.DataFrame,
    persons: pd.DataFrame,
    unfixable_persons,
    *,
    resample_cell_col: str | None,
    random_seed: int,
    resample_chains,
) -> pd.DataFrame:
    """Cascade stage B: replace unfixable persons' chains by attribute matching.

    Each unfixable person is matched to a VALID-chain donor person (post
    stage A) via the reusable hierarchical-relaxation matcher
    ``synthesis.population.matched.match_donors`` on
    ``MATCHED_REPLACEMENT_COLUMNS`` and inherits the matched donor's FULL
    final (post-repair) chain; the copied rows carry ``chain_donor_id`` (the
    donor's person_id) for traceability, consistent with the ENTD diary-donor
    chain matching convention (``braunschweig.popsim.sources.entd``).

    Replaces the legacy same-cell (``resample_cell_col``) draw: at 1 % sampling
    31.8 % of unfixable persons had NO same-cell donor (pool of 1-3 chains) and
    fell back to home-only plans — attribute similarity over the whole valid
    pool is both better-covered and behaviourally closer.  Home-only (=
    trip-less, no rows in the table) remains ONLY as the loudly-logged catch
    for match failures: persons whose ``sex`` value (the never-relaxed first
    key) has fewer donors than ``minimum_observations``, or — belt-and-braces —
    a ``RuntimeError`` from full-relaxation failure.

    Fallback transparency: when the persons frame carries no ``sex`` column at
    all (minimal unit-test fixtures), the matching is impossible and the
    legacy same-cell resample (``resample_chains``) handles everyone — logged
    loudly, never silent.

    Determinism: ``match_donors`` is seeded with
    ``random_seed + MATCHED_REPLACEMENT_SEED_OFFSET`` (see the constant's
    comment for the stream layout).
    """
    # Reusing the legacy statistical-matching machinery from the shared
    # synthesis tree is established practice in this package (see
    # braunschweig.popsim.sources.entd, which wraps the same helper).
    from braunschweig.popsim.chain_matching import (
        derive_age_class,
        effective_minimum_observations,
    )
    from synthesis.population.matched import match_donors

    unfixable = set(unfixable_persons)
    n_unfixable = len(unfixable)

    # One attribute row per unique synthetic person (like build_trip_table).
    persons_unique = persons.drop_duplicates(subset="person_id").copy()

    if "sex" not in persons_unique.columns:
        logger.warning(
            "[popsim.trips] stage B: persons frame carries no 'sex' column, so "
            "attribute-matched chain replacement is impossible; falling back to "
            "the legacy same-cell resample for all %d unfixable persons.",
            n_unfixable,
        )
        return _resample_unfixable(
            table, persons, unfixable_persons,
            resample_cell_col=resample_cell_col,
            random_seed=random_seed,
            resample_chains=resample_chains,
        )

    # Matching keys actually available on the persons frame; missing keys are
    # dropped with a logged warning (never silently).
    columns = []
    for column in MATCHED_REPLACEMENT_COLUMNS:
        source_column = _MATCHED_REPLACEMENT_SOURCE_COLUMN.get(column, column)
        if source_column not in persons_unique.columns:
            logger.warning(
                "[popsim.trips] stage B matching: key '%s' dropped because "
                "column '%s' is missing on the persons frame.",
                column, source_column,
            )
            continue
        if column == "age_class":
            persons_unique["age_class"] = derive_age_class(persons_unique["age"])
        columns.append(column)

    # Pool = persons with VALID chains after stage A (rows kept in the table
    # and not classified unfixable); one row per person, weight 1.0 (the
    # synthetic frame is already expanded), hts_id = their person_id.
    valid_table = table[~table["person_id"].isin(unfixable)]
    valid_chain_persons = set(valid_table["person_id"].unique())
    pool = persons_unique[
        persons_unique["person_id"].isin(valid_chain_persons)
    ].copy()
    pool = pool.rename(columns={"person_id": "hts_id"})
    pool["weight"] = 1.0

    targets = persons_unique[persons_unique["person_id"].isin(unfixable)]

    assignment = pd.DataFrame(columns=["person_id", "hts_id"])
    if len(pool) == 0:
        logger.warning(
            "[popsim.trips] stage B matching: the valid-chain donor pool is "
            "EMPTY; all %d unfixable persons become trip-less (home-only). "
            "This signals a broken trip table, not a tolerable fallback.",
            n_unfixable,
        )
    else:
        minimum_observations = effective_minimum_observations(len(pool))

        # Feasibility pre-filter on the FIRST key: match_donors never relaxes
        # it, and a single infeasible target would raise RuntimeError for the
        # whole call. Targets whose first-key value has too few donors are
        # left trip-less individually (logged below) instead of aborting all.
        first_key = columns[0]
        donor_counts = pool[first_key].value_counts()
        feasible_values = set(
            donor_counts[donor_counts >= minimum_observations].index
        )
        feasible = targets[first_key].isin(feasible_values)
        n_infeasible = int((~feasible).sum())
        if n_infeasible > 0:
            logger.warning(
                "[popsim.trips] stage B matching: %d/%d unfixable persons are "
                "unmatchable (their '%s' value has < %d valid-chain donors) "
                "and become trip-less (home-only). A high rate signals a "
                "broken donor pool, not a tolerable fallback.",
                n_infeasible, n_unfixable, first_key, minimum_observations,
            )
        targets = targets.loc[feasible]

        if len(targets) > 0:
            try:
                assignment = match_donors(
                    targets[["person_id"] + columns],
                    pool[["hts_id", "weight"] + columns],
                    matching_columns=columns,
                    minimum_observations=minimum_observations,
                    random_seed=random_seed + MATCHED_REPLACEMENT_SEED_OFFSET,
                )
            except RuntimeError:
                # Belt-and-braces: the pre-filter above should prevent this;
                # if it fires anyway, leave the remaining persons trip-less
                # and report loudly rather than crashing the trips build.
                logger.error(
                    "[popsim.trips] stage B matching: match_donors failed at "
                    "full relaxation for %d unfixable persons; they become "
                    "trip-less (home-only). Investigate the donor pool.",
                    len(targets),
                )

    # Matched persons inherit the matched donor's FULL final chain; the donor
    # is recorded per trip row as chain_donor_id (entd.py convention).
    replacement_rows = None
    if len(assignment) > 0:
        replacement_rows = valid_table.merge(
            assignment.rename(columns={"person_id": "_target_person_id"}),
            left_on="person_id",
            right_on="hts_id",
            how="inner",
        )
        replacement_rows["chain_donor_id"] = replacement_rows["hts_id"]
        replacement_rows["person_id"] = replacement_rows["_target_person_id"]
        replacement_rows = replacement_rows.drop(
            columns=["_target_person_id", "hts_id"]
        )

    n_matched = int(assignment["person_id"].nunique())
    n_home_only = n_unfixable - n_matched
    log = logger.warning if n_home_only > 0 else logger.info
    log(
        "[popsim.trips] stage B: %d unfixable persons -> %d matched chains, "
        "%d home-only (trip-less persons without trip rows; expected ~0).",
        n_unfixable, n_matched, n_home_only,
    )

    if replacement_rows is not None:
        table = pd.concat([valid_table, replacement_rows],
                          ignore_index=True, sort=False)
    else:
        # Everyone unmatched: only the valid persons keep rows (home-only
        # persons are trip-less by the eqasim stay-home convention).
        table = valid_table

    return _recompute_chain_ids(table)


def _resample_unfixable(
    table: pd.DataFrame,
    persons: pd.DataFrame,
    unfixable_persons,
    *,
    resample_cell_col: str | None,
    random_seed: int,
    resample_chains,
) -> pd.DataFrame:
    """Replace unfixable persons' chains with same-cell donor chains; rebuild ids.

    Donor chains are the FINAL (post-repair) chains of the valid persons that
    live in the same ``resample_cell_col`` cell as an unfixable person; donor
    pools are only built for cells that actually contain unfixable persons (the
    full population can be ~1 M persons, so materialising every cell's pool
    would be wasteful).  Unfixable persons without any same-cell donor receive
    resample_chains' home-only fallback row (NaN times); those rows are dropped
    here — in the eqasim trips contract a stay-at-home person simply has no
    trips — and the drop count is logged.

    After the replacement, ``trip_id`` / ``is_first_trip`` / ``is_last_trip`` /
    ``trip_duration`` / ``activity_duration`` / ``trip_index`` are re-derived on
    the final sorted frame (same recompute sequence as
    ``PlanValidator.repair_trips`` step 4) because the donor chains carry the
    DONOR's ids, which would otherwise collide with the kept persons' rows.
    """
    import numpy as np

    # person -> cell mapping (one row per unique person, like build_trip_table).
    persons_unique = persons.drop_duplicates(subset="person_id")
    if resample_cell_col is not None:
        person_cells = dict(
            zip(persons_unique["person_id"], persons_unique[resample_cell_col])
        )
    else:
        person_cells = {}

    # Donor pools: valid persons' final chains, only for the cells that contain
    # at least one unfixable person.  Chains are stored WITHOUT person_id
    # (resample_chains assigns the recipient's person_id).
    needed_cells = {
        person_cells[p] for p in unfixable_persons if p in person_cells
    }
    donor_chains: dict = {cell: [] for cell in needed_cells}
    valid_table = table[~table["person_id"].isin(set(unfixable_persons))]
    # Sorted iteration over donor person ids for deterministic pool order.
    for person_id, chain in valid_table.sort_values(
        ["person_id", "departure_time"]
    ).groupby("person_id", sort=True):
        cell = person_cells.get(person_id)
        if cell in donor_chains:
            donor_chains[cell].append(chain.drop(columns=["person_id"]))

    table = resample_chains(
        table,
        unfixable_persons,
        person_cells,
        donor_chains,
        rng=np.random.RandomState(random_seed),
    )

    # Drop home-only fallback rows (NaN times): in the trips contract a person
    # without trips simply has no rows; activities.py gives them a single home
    # activity.  Observable, not silent.
    nan_rows = table["departure_time"].isna() | table["arrival_time"].isna()
    if nan_rows.any():
        dropped_persons = table.loc[nan_rows, "person_id"].nunique()
        logger.warning(
            "[popsim.trips] %d resampled persons had no same-cell donor and become "
            "trip-less (home-only) persons; their %d placeholder rows are dropped "
            "from the trips table.",
            dropped_persons, int(nan_rows.sum()),
        )
        table = table[~nan_rows]

    # Re-derive ids and derived columns on the final frame: donor chains carry
    # the donor's trip_id / trip_index, which are stale for the recipient.
    return _recompute_chain_ids(table)
