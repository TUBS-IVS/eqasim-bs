"""The student table T against the SrV education-destination groups (#140, Phase 2).

The commuter weights of T (:mod:`braunschweig.data.education.student_od_table`) are calibrated
against the committed SrV reference :data:`srv_student_reference.DESTINATION_TABLE`: for the
adult students with an education leg, per home group (Braunschweig; another Kreis with a local
site; a Kreis without one), the share whose first education leg ends in each destination group.
This module maps T onto exactly those groups (:func:`implied_shares`) and measures the distance
between model and survey (:func:`fit_distance`). The group vocabulary is the one of the reference
builder (:func:`srv_student_reference.destination_vocabulary`); no code or label is retyped here.

What T and the reference compare: T counts the students of a site by the Kreis they LIVE in (heads);
the reference counts the first education leg of the surveyed students on one day. A home Kreis's row
of T, normalised to 1, stands for the destination distribution of its students.

- ASSUMPTION A9: within a pooled home group, the Kreis mix of the students with an education leg
  equals that of all students; the Kreise are pooled with the SrV ``students_weighted_persons`` of
  :data:`srv_student_reference.STUDENT_SHARE_TABLE` (passed in as ``kreis_mix``), never with the
  model's own row sums, so that the model's Kreis sizes cannot move the pooled reference mix.
- The reference's ``own_kreis`` of the no-site group and its ``other_region_kreis`` have no
  destination in the model (no institution of the choice set lies there): they are emitted with
  implied share 0 and ``reachable`` False and stay in the distance, so that the survey mass the
  model cannot reach is counted, never dropped.

The calibration (:func:`calibrate`, :func:`calibrate_movers`) searches a grid of weight triples, and in
its second stage also of mover shares, for the smallest :func:`fit_distance` objective, and decides only
where the survey can tell the candidates apart (:class:`CalibrationResult`). It never writes a
configuration value: :func:`config_snippet` returns a proposal text for Phase 4 when the result is
decided, ``None`` otherwise.

Pure module (no I/O). The grid search evaluates :func:`fit_distance`'s objective on whole arrays (one
build of T per triple, all mover shares of a triple by arithmetic); :func:`fit_table` reports the
result's optimum through :func:`implied_shares` and :func:`fit_distance` themselves.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Callable, NamedTuple

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_participation_universe import UNSURVEYED_KREISE
from braunschweig.calibration.srv_student_reference import (
    BRAUNSCHWEIG_KREIS, DEST_LK_HILDESHEIM, DEST_OTHER_OUTSIDE, DEST_OTHER_REGION, DEST_OWN,
    DEST_REGION_HANNOVER, DEST_SITE_PREFIX, HOME_GROUP_ALL, HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_NO_SITE,
    HOME_GROUP_SITE, LANDKREIS_HILDESHEIM_ARS5, REGION_HANNOVER_ARS5, destination_vocabulary)
from braunschweig.calibration.student_behaviour_labels import STUDENT_DISTANCE_EDGES_KM
from braunschweig.data.education.student_od_table import (
    KIND_LOCAL, SCOPE_LOCAL, SCOPE_SURROUNDING, SCOPES, CommuterWeights, apply_movers)

LOGGER = logging.getLogger(__name__)

# The three home groups the fit runs over, in the reference's order; HOME_GROUP_ALL (all three pooled)
# is a reported row of the reference, not fitted.
FIT_HOME_GROUPS = (HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_SITE, HOME_GROUP_NO_SITE)
_FIXED_DESTINATIONS = frozenset({DEST_OWN, DEST_OTHER_REGION, DEST_REGION_HANNOVER, DEST_LK_HILDESHEIM,
                                 DEST_OTHER_OUTSIDE})
IMPLIED_COLUMNS = ["home_group", "destination_group", "implied_share", "reachable"]
BY_GROUP_COLUMNS = ["home_group", "total_variation_distance", "unreachable_reference_share", "n_effective",
                    "noise_floor_tvd_95"]
_OD_COLUMNS = ["home_ars5", "home_in_region", "site_kreis_ars5", "scope", "students_persons"]
_REFERENCE_COLUMNS = ["home_group", "destination_group", "share", "n_effective", "noise_floor_tvd_95"]

# Float rounding only, no modelling meaning: the reference is written with ten significant digits, so
# its shares per home group sum to 1 within this tolerance; the implied shares are held to the same.
REFERENCE_SHARE_TOLERANCE = 1e-6

# Centroid-to-site distance classes reported next to the fit (never fitted). The edges are the 25 and
# 50 km edges of student_behaviour_labels.STUDENT_DISTANCE_EDGES_KM (the bands of the SrV
# education-distance table), left-inclusive like there: [0, 25), [25, 50), [50, inf). Rows of kind
# KIND_LOCAL are own_kreis.
DISTANCE_CLASSES = ("own_kreis", "other_kreis_under_25_km", "other_kreis_25_50_km", "other_kreis_50_plus_km")
_DISTANCE_CLASS_EDGE_VALUES_KM = (25.0, 50.0)   # the edges DISTANCE_CLASSES names; must exist in the owner
if not set(_DISTANCE_CLASS_EDGE_VALUES_KM) <= set(STUDENT_DISTANCE_EDGES_KM):
    raise ValueError(f"student_commuter_weights: distance-class edges {_DISTANCE_CLASS_EDGE_VALUES_KM} km are not all "
                     f"in student_behaviour_labels.STUDENT_DISTANCE_EDGES_KM {STUDENT_DISTANCE_EDGES_KM}")
DISTANCE_CLASS_EDGES_KM = tuple(edge for edge in STUDENT_DISTANCE_EDGES_KM if edge in _DISTANCE_CLASS_EDGE_VALUES_KM)
DISTANCE_CLASS_COLUMNS = ["code", "distance_class", "students_persons", "share"]


class FitDistance(NamedTuple):
    """Distance between the implied and the SrV destination shares.

    ``objective``: total-variation distance per home group, averaged with the reference's ``n_effective``;
    ``noise_floor``: the same weighted mean of the reference's ``noise_floor_tvd_95``; ``by_group``:
    :data:`BY_GROUP_COLUMNS`, one row per home group of :data:`FIT_HOME_GROUPS`.
    ``unreachable_reference_share`` is the reference share on the group's cells with ``reachable`` False:
    the model puts 0 there, so the group's distance cannot go below it (no weight triple can).
    """
    objective: float
    noise_floor: float
    by_group: pd.DataFrame


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what}: missing column(s) {missing}; got {list(frame.columns)}")


def _home_groups(home: np.ndarray, site_kreise: frozenset) -> np.ndarray:
    """Vectorised :func:`home_group`: object array, None for an unsurveyed Kreis."""
    groups = np.where(home == BRAUNSCHWEIG_KREIS, HOME_GROUP_BRAUNSCHWEIG,
                      np.where(np.isin(home, sorted(site_kreise)), HOME_GROUP_SITE, HOME_GROUP_NO_SITE))
    groups = groups.astype(object)
    groups[np.isin(home, list(UNSURVEYED_KREISE))] = None
    return groups


def _destination_groups(home: np.ndarray, site_kreis: np.ndarray, scope: np.ndarray) -> np.ndarray:
    """Vectorised :func:`destination_group`."""
    unknown = sorted({str(value) for value in scope if value not in SCOPES})
    if unknown:
        raise ValueError(f"destination group: column 'scope' has unknown value(s) {unknown}; expected {list(SCOPES)}")
    site_kreis = site_kreis.astype(str)
    local = scope == SCOPE_LOCAL
    local_label = np.where(site_kreis == home, DEST_OWN, np.char.add(DEST_SITE_PREFIX, site_kreis).astype(object))
    outside_label = np.where(site_kreis == REGION_HANNOVER_ARS5, DEST_REGION_HANNOVER,
                             np.where(site_kreis == LANDKREIS_HILDESHEIM_ARS5, DEST_LK_HILDESHEIM,
                                      DEST_OTHER_OUTSIDE))
    return np.where(local, local_label, outside_label).astype(object)


class _SurveyedRows(NamedTuple):
    """The rows of T that enter the fit (home in the region and surveyed by SrV), with their labels."""
    home: np.ndarray            # home Kreis code (str)
    group: np.ndarray           # SrV home group
    label: np.ndarray           # SrV destination group
    students: np.ndarray        # students_persons, finite and >= 0
    n_in_commuter: int          # rows left out: home outside the region
    n_unsurveyed: int           # rows left out: home Kreis not surveyed by SrV


def _surveyed_rows(od: pd.DataFrame, sites: frozenset, what: str) -> _SurveyedRows:
    """Validate T and label its surveyed region rows; shared by :func:`implied_shares` and the grid search.

    ``what`` names the caller in the error messages. The label vocabulary is checked separately
    (:func:`_check_vocabulary`), after the caller's mix-weight and empty-row checks.
    """
    _require_columns(od, _OD_COLUMNS, f"{what} od")
    if BRAUNSCHWEIG_KREIS not in sites:
        raise ValueError(f"{what}: site_kreise {sorted(sites)} must contain {BRAUNSCHWEIG_KREIS} "
                         f"(the Braunschweig home group is defined by it)")
    in_region = od["home_in_region"].to_numpy(dtype=bool)
    home = od["home_ars5"].to_numpy(dtype=object)[in_region].astype(str)
    groups = _home_groups(home, sites)
    surveyed = pd.notna(groups)
    home, groups = home[surveyed], groups[surveyed]
    students = od["students_persons"].to_numpy(dtype=float)[in_region][surveyed]
    if not np.isfinite(students).all() or (students < 0).any():
        raise ValueError(f"{what}: column 'students_persons' holds negative or non-finite values for home "
                         f"Kreis(e) {sorted(set(home[~np.isfinite(students) | (students < 0)]))[:5]}")
    labels = _destination_groups(home, od["site_kreis_ars5"].to_numpy(dtype=object)[in_region][surveyed],
                                 od["scope"].to_numpy(dtype=object)[in_region][surveyed])
    return _SurveyedRows(home=home, group=groups, label=labels, students=students,
                         n_in_commuter=int((~in_region).sum()), n_unsurveyed=int((~surveyed).sum()))


def _check_vocabulary(rows: _SurveyedRows, sites: frozenset, what: str) -> None:
    """Raise unless every (home group, destination group) of ``rows`` is a label of the SrV reference."""
    vocabulary = {group: set(destination_vocabulary(sites, group)) for group in FIT_HOME_GROUPS}
    produced = set(zip(rows.group, rows.label))
    unknown = sorted((g, l) for g, l in produced if l not in vocabulary[g])
    if unknown:
        raise ValueError(f"{what}: destination label(s) (home_group, destination_group) {unknown[:5]} are not "
                         f"in the SrV reference vocabulary for site_kreise {sorted(sites)}; model and reference "
                         f"disagree on the site set")


def _mix_weights(kreise, kreis_mix: pd.Series, what: str) -> np.ndarray:
    """``kreis_mix`` for ``kreise`` (ASSUMPTION A9); raises unless every weight is positive and finite."""
    mix = pd.to_numeric(pd.Series(kreis_mix).reindex(list(kreise)), errors="coerce")
    values = mix.to_numpy(dtype=float)
    no_mix = mix.index[~(np.isfinite(values) & (values > 0))]
    if len(no_mix):
        raise ValueError(f"{what}: surveyed home Kreis(e) {list(no_mix)} have no positive kreis_mix weight "
                         f"(SrV 'students_weighted_persons'); got {mix.loc[no_mix].tolist()}")
    return values


def home_group(ars5: str, site_kreise: frozenset[str]) -> str | None:
    """The SrV home group of a region Kreis: Braunschweig, another Kreis of ``site_kreise``, or any other.

    ``None`` for a Kreis SrV does not survey (:data:`srv_participation_universe.UNSURVEYED_KREISE`,
    Wolfsburg): it has no reference to be fitted against.
    """
    return _home_groups(np.array([str(ars5)], dtype=object), frozenset(site_kreise))[0]


def destination_group(home_ars5: str, site_kreis_ars5: str, scope: str) -> str:
    """The SrV destination group of a site seen from a home Kreis.

    A local site in the home Kreis is ``own_kreis``, one elsewhere ``site_kreis_<ars5>``; a surrounding
    site is ``region_hannover``, ``landkreis_hildesheim`` or ``other_outside`` by its Kreis.

    Raises:
        ValueError: ``scope`` not in :data:`student_od_table.SCOPES`.
    """
    return _destination_groups(np.array([str(home_ars5)], dtype=object),
                               np.array([str(site_kreis_ars5)], dtype=object), np.array([scope], dtype=object))[0]


def implied_shares(od: pd.DataFrame, site_kreise: frozenset[str], kreis_mix: pd.Series) -> pd.DataFrame:
    """The destination shares T implies per SrV home group, pooled with the SrV Kreis mix.

    Rows with ``home_in_region`` False (in-commuters) and rows of an unsurveyed home Kreis are left out.
    Per remaining home Kreis ``k`` the row of T is summed per destination group (a commuter and a
    mover row of one (Braunschweig, site) pair add up: both are students living in Braunschweig) and
    normalised, ``p_k(g)``; per home group ``H`` the implied share is
    ``sum_k m_k p_k(g) / sum_k m_k`` over the Kreise of ``H`` present in ``od``, with ``m_k = kreis_mix[k]``
    (ASSUMPTION A9, module docstring).

    Parameters:
        od: T, at least the columns ``home_ars5, home_in_region, site_kreis_ars5, scope, students_persons``.
        site_kreise: the region Kreise with a local site; must contain :data:`BRAUNSCHWEIG_KREIS`.
        kreis_mix: ars5 -> SrV ``students_weighted_persons``.

    Returns:
        :data:`IMPLIED_COLUMNS`, for every home group present in ``od`` one row per label of
        :func:`srv_student_reference.destination_vocabulary`, in its order; ``reachable`` is True where
        at least one row of the group maps to the label (a reachable label can still carry share 0).

    Raises:
        ValueError: a missing column, ``site_kreise`` without Braunschweig, a negative or non-finite
            ``students_persons``, a surveyed Kreis without a positive finite mix weight, a surveyed
            Kreis whose row sums to 0 (empty row), or a destination label the reference does not have
            (the two sides disagree on the site set).
    """
    sites = frozenset(site_kreise)
    rows = _surveyed_rows(od, sites, "implied_shares")
    frame = pd.DataFrame({"home": rows.home, "group": rows.group, "label": rows.label, "students": rows.students})
    row_sum = frame.groupby("home", sort=True)["students"].sum()
    mix = pd.Series(_mix_weights(row_sum.index, kreis_mix, "implied_shares"), index=row_sum.index)
    empty = row_sum.index[row_sum.to_numpy() <= 0]
    if len(empty):
        raise ValueError(f"implied_shares: surveyed home Kreis(e) {list(empty)} have an empty row (column "
                         f"'students_persons' sums to 0); their destination distribution is undefined")
    _check_vocabulary(rows, sites, "implied_shares")

    frame["weighted"] = (frame["students"] / frame["home"].map(row_sum)) * frame["home"].map(mix)
    pooled = frame.groupby(["group", "label"], sort=False)["weighted"].sum()
    group_mix = mix.groupby(frame.groupby("home", sort=True)["group"].first()).sum()
    vocabulary = {group: destination_vocabulary(sites, group) for group in FIT_HOME_GROUPS}
    produced = frame[["group", "label"]].drop_duplicates()

    parts = []
    for group in FIT_HOME_GROUPS:
        if group not in group_mix.index:
            continue
        group_labels = vocabulary[group]
        values = pooled.loc[group].reindex(group_labels, fill_value=0.0) / group_mix[group]
        reached = set(produced.loc[produced["group"] == group, "label"])
        parts.append(pd.DataFrame({"home_group": group, "destination_group": group_labels,
                                   "implied_share": values.to_numpy(dtype=float),
                                   "reachable": [label in reached for label in group_labels]}))
    result = pd.concat(parts, ignore_index=True)[IMPLIED_COLUMNS]
    LOGGER.debug("[student_commuter_weights] implied shares: %d of %d rows in surveyed region Kreise (%d in-commuter, "
                 "%d unsurveyed rows left out); %d of %d labels unreachable", len(frame), len(od),
                 rows.n_in_commuter, rows.n_unsurveyed, int((~result["reachable"]).sum()), len(result))
    return result


def _is_known_label(labels: pd.Series) -> np.ndarray:
    text = labels.astype(str)
    site = text.str.startswith(DEST_SITE_PREFIX) & (text.str.len() > len(DEST_SITE_PREFIX))
    return (text.isin(_FIXED_DESTINATIONS) | site).to_numpy()


def _validated_reference(reference: pd.DataFrame) -> pd.DataFrame:
    _require_columns(reference, _REFERENCE_COLUMNS, "fit_distance reference")
    groups = set(reference["home_group"])
    if groups - {HOME_GROUP_ALL} != set(FIT_HOME_GROUPS):
        raise ValueError(f"fit_distance reference: column 'home_group' must hold exactly {list(FIT_HOME_GROUPS)} "
                         f"(plus the pooled {HOME_GROUP_ALL!r}); got {sorted(map(str, groups))}")
    frame = reference[reference["home_group"] != HOME_GROUP_ALL]
    duplicated = frame.duplicated(["home_group", "destination_group"]).to_numpy()
    if duplicated.any():
        raise ValueError(f"fit_distance reference: duplicated (home_group, destination_group) row(s) "
                         f"{frame.loc[duplicated, ['home_group', 'destination_group']].values.tolist()[:5]}")
    known = _is_known_label(frame["destination_group"])
    if not known.all():
        raise ValueError(f"fit_distance reference: column 'destination_group' has unknown label(s) "
                         f"{sorted(set(frame.loc[~known, 'destination_group'].astype(str)))}")
    share = pd.to_numeric(frame["share"], errors="coerce").to_numpy(dtype=float)
    if not (np.isfinite(share) & (share >= 0) & (share <= 1)).all():
        raise ValueError("fit_distance reference: column 'share' must be finite and in [0, 1] on every row")
    sums = pd.Series(share, index=frame.index).groupby(frame["home_group"]).sum()
    open_groups = sums[(sums - 1.0).abs() > REFERENCE_SHARE_TOLERANCE]
    if len(open_groups):
        raise ValueError(f"fit_distance reference: column 'share' does not sum to 1 for home group(s) "
                         f"{open_groups.to_dict()}")
    for column in ("n_effective", "noise_floor_tvd_95"):
        per_group = pd.to_numeric(frame[column], errors="coerce").groupby(frame["home_group"])
        values = per_group.first()
        if (per_group.nunique() > 1).any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"fit_distance reference: column {column!r} must be one finite value per home group; "
                             f"got {per_group.unique().to_dict()}")
    if (pd.to_numeric(frame["n_effective"]) <= 0).any():
        raise ValueError("fit_distance reference: column 'n_effective' must be positive")
    return frame.assign(share=share)


def fit_distance(implied: pd.DataFrame, reference: pd.DataFrame) -> FitDistance:
    """Total-variation distance between :func:`implied_shares` and the SrV reference, per home group.

    Per home group ``0.5 * sum_g |implied(g) - srv(g)|`` over ALL destination groups of the reference
    (unreachable ones included); ``objective`` and ``noise_floor`` are means over :data:`FIT_HOME_GROUPS`
    weighted with the reference's ``n_effective`` (see :class:`FitDistance`). The reference's pooled
    :data:`srv_student_reference.HOME_GROUP_ALL` rows are not fitted.

    Raises:
        ValueError: checked on the reference first: a missing column, home groups other than the three
            (plus the pooled one), a duplicated row, an unknown destination label, a share outside [0, 1]
            or shares not summing to 1 per home group, ``n_effective`` or ``noise_floor_tvd_95`` not one
            finite value per group, a non-positive ``n_effective``. Then: ``implied`` without one of the
            three home groups, with a duplicated row, a non-finite ``implied_share``, shares not summing
            to 1 per home group, or a label set per home group other than the
            reference's (the two sides disagree on the site set).
    """
    reference = _validated_reference(reference)
    _require_columns(implied, IMPLIED_COLUMNS, "fit_distance implied")
    groups = set(implied["home_group"])
    if groups != set(FIT_HOME_GROUPS):
        raise ValueError(f"fit_distance implied: column 'home_group' must hold exactly {list(FIT_HOME_GROUPS)}; "
                         f"got {sorted(map(str, groups))}")
    if implied.duplicated(["home_group", "destination_group"]).any():
        raise ValueError("fit_distance implied: duplicated (home_group, destination_group) row(s)")
    implied_share = pd.to_numeric(implied["implied_share"], errors="coerce").to_numpy(dtype=float)
    keys = implied[["home_group", "destination_group"]].reset_index(drop=True)   # positional, any index
    non_finite = ~np.isfinite(implied_share)
    if non_finite.any():
        raise ValueError(f"fit_distance implied: column 'implied_share' must be finite; non-finite for "
                         f"{keys[non_finite].values.tolist()[:5]}")
    sums = pd.Series(implied_share).groupby(keys["home_group"]).sum()
    open_groups = sums[(sums - 1.0).abs() > REFERENCE_SHARE_TOLERANCE]
    if len(open_groups):
        raise ValueError(f"fit_distance implied: column 'implied_share' does not sum to 1 for home group(s) "
                         f"{open_groups.to_dict()}")
    merged = reference.merge(implied[IMPLIED_COLUMNS].assign(implied_share=implied_share),
                             on=["home_group", "destination_group"], how="outer", indicator=True)
    if (merged["_merge"] != "both").any():
        unmatched = merged.loc[merged["_merge"] != "both", ["home_group", "destination_group", "_merge"]]
        raise ValueError(f"fit_distance: model and reference disagree on the destination groups (left_only = "
                         f"reference only, right_only = model only): {unmatched.values.tolist()[:5]}")
    merged["absolute_difference"] = (merged["implied_share"] - merged["share"]).abs()
    merged["unreachable_share"] = np.where(merged["reachable"].to_numpy(dtype=bool), 0.0, merged["share"])
    by_group = merged.groupby("home_group").agg(
        absolute_difference=("absolute_difference", "sum"),
        unreachable_reference_share=("unreachable_share", "sum"), n_effective=("n_effective", "first"),
        noise_floor_tvd_95=("noise_floor_tvd_95", "first")).reindex(list(FIT_HOME_GROUPS))
    by_group["total_variation_distance"] = 0.5 * by_group["absolute_difference"]
    by_group = by_group.reset_index()[BY_GROUP_COLUMNS]
    weight = by_group["n_effective"].to_numpy(dtype=float)
    objective = float(np.dot(weight, by_group["total_variation_distance"].to_numpy(dtype=float)) / weight.sum())
    noise_floor = float(np.dot(weight, by_group["noise_floor_tvd_95"].to_numpy(dtype=float)) / weight.sum())
    return FitDistance(objective=objective, noise_floor=noise_floor, by_group=by_group)


def implied_distance_classes(od: pd.DataFrame, region_kreise) -> pd.DataFrame:
    """Students of T per region Kreis by centroid-to-site distance class (reported next to the fit).

    Every row with a home in ``region_kreise`` counts: ``cell_kind`` :data:`KIND_LOCAL` as ``own_kreis``,
    every other row (commuter, mover, out-commuter) by its ``distance_km`` (population-weighted commune
    centroid to site commune centroid, :data:`DISTANCE_CLASS_EDGES_KM`, left-inclusive).

    Returns:
        :data:`DISTANCE_CLASS_COLUMNS`, one row per (region Kreis, :data:`DISTANCE_CLASSES`), Kreise in
        the order of ``region_kreise``; ``share`` is NaN, never 0, for a Kreis whose students sum to 0.

    Raises:
        ValueError: a missing column, a duplicated region Kreis, a region Kreis without rows, or a
            non-local row whose ``distance_km`` is missing or negative.
    """
    _require_columns(od, ["home_ars5", "site_kreis_ars5", "distance_km", "students_persons", "cell_kind"],
                     "implied_distance_classes od")
    region = [str(code) for code in region_kreise]
    if len(set(region)) != len(region):
        raise ValueError(f"implied_distance_classes: duplicate Kreis code(s) in region_kreise {region}")
    home = od["home_ars5"].astype(str)
    absent = sorted(set(region) - set(home))
    if absent:
        raise ValueError(f"implied_distance_classes: region Kreis(e) {absent} have no row (column 'home_ars5')")
    rows = od[home.isin(region).to_numpy()]
    local = (rows["cell_kind"] == KIND_LOCAL).to_numpy()
    distance = rows["distance_km"].to_numpy(dtype=float)
    bad = ~local & ~(np.isfinite(distance) & (distance >= 0))
    if bad.any():
        raise ValueError(f"implied_distance_classes: column 'distance_km' is missing or negative on non-local row(s) "
                         f"(home_ars5, site_kreis_ars5) {rows.loc[bad, ['home_ars5', 'site_kreis_ars5']].values.tolist()[:5]}")
    class_index = np.where(local, 0, 1 + np.searchsorted(DISTANCE_CLASS_EDGES_KM, np.nan_to_num(distance), side="right"))
    kreis_index = pd.Index(region).get_indexer(rows["home_ars5"].astype(str))
    n_classes = len(DISTANCE_CLASSES)
    persons = np.bincount(kreis_index * n_classes + class_index, weights=rows["students_persons"].to_numpy(dtype=float),
                          minlength=len(region) * n_classes).reshape(len(region), n_classes)
    totals = persons.sum(axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(totals > 0, persons / np.where(totals > 0, totals, 1.0), np.nan)
    return pd.DataFrame({"code": np.repeat(region, n_classes), "distance_class": DISTANCE_CLASSES * len(region),
                         "students_persons": persons.ravel(), "share": share.ravel()},
                        columns=DISTANCE_CLASS_COLUMNS)


# --- the grid search and the decide-or-range rule (design B3 step 7, B4) ----------------------------------

STAGE_WEIGHTS = "weights"
STAGE_WEIGHTS_AND_MOVERS = "weights_and_movers"
CALIBRATION_COLUMNS = ["stage", "short", "middle", "long", "mover_share", "objective_tvd", "tvd_braunschweig",
                       "tvd_other_site_kreis", "tvd_no_site_kreis", "within_noise_floor"]
FIT_COLUMNS = ["stage", "home_group", "destination_group", "srv_n_unweighted", "srv_share", "srv_se_share",
               "implied_share", "difference_pp", "reachable"]
_TVD_COLUMNS = {HOME_GROUP_BRAUNSCHWEIG: "tvd_braunschweig", HOME_GROUP_SITE: "tvd_other_site_kreis",
                HOME_GROUP_NO_SITE: "tvd_no_site_kreis"}
_WEIGHT_NAMES = tuple(CommuterWeights._fields)                 # ("short", "middle", "long")
_MOVER_NAME = "mover_share"

# Float rounding only, no modelling meaning: a parameter within this distance of 0 or 1 sits at the bound,
# objectives this close are a tie, and a range exceeds max_flat_range only by more than this.
_FLOAT_TOLERANCE = 1e-12
# Float rounding only: the grid search's array objective and fit_distance on the same table agree to this.
_OBJECTIVE_AGREEMENT = 1e-9
# Names the grid search (calibrate, calibrate_movers) in the error messages of the shared validation helpers.
_GRID_SEARCH = "calibration grid search"


class CalibrationResult(NamedTuple):
    """Outcome of one calibration stage.

    ``stage``: :data:`STAGE_WEIGHTS` or :data:`STAGE_WEIGHTS_AND_MOVERS`. ``table``:
    :data:`CALIBRATION_COLUMNS` (stage one: one row per triple of the grid; stage two: one row per mover
    share, with its best triple). ``optimum`` / ``mover_share`` / ``objective``: the parameter set with the
    smallest objective (ties: the first in grid order). ``noise_floor``: the reference's noise floor
    (:class:`FitDistance`). ``ranges``: min and max of each calibrated parameter over the indifference set
    (every evaluated parameter set with ``objective <= optimum + noise_floor``). ``decided``: False, with one
    entry in ``reasons`` each, when a calibrated parameter of the optimum lies at 0 or 1 or a range is wider
    than ``max_flat_range``.
    """
    stage: str
    table: pd.DataFrame
    optimum: CommuterWeights
    mover_share: float
    objective: float
    noise_floor: float
    ranges: dict[str, tuple[float, float]]
    decided: bool
    reasons: list[str]


def _unit_grid(step: float, name: str) -> list[float]:
    """``{0, step, ..., 1}`` with values ``round(i * step, 10)``; ``1 / step`` must be a whole number."""
    try:
        value = float(step)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number in (0, 1], got {step!r}") from None
    if not 0.0 < value <= 1.0:
        raise ValueError(f"{name} must lie in (0, 1], got {step!r}")
    count = int(round(1.0 / value))
    if abs(count * value - 1.0) > 1e-9:
        raise ValueError(f"{name} {step!r}: 1 / step must be a whole number, otherwise the grid misses 1")
    return [round(i * value, 10) for i in range(count + 1)]


def weight_grid(step: float) -> list[CommuterWeights]:
    """Every non-increasing triple on ``{0, step, ..., 1}`` (values ``round(i * step, 10)``).

    Order (the grid order of the tie rule): short ascending, then middle, then long. ``step`` 0.05 gives
    1,771 triples. Raises ValueError unless ``step`` lies in (0, 1] and ``1 / step`` is a whole number.
    """
    values = _unit_grid(step, "grid_step")
    return [CommuterWeights(values[i], values[j], values[k])
            for i in range(len(values)) for j in range(i + 1) for k in range(j + 1)]


class _GridObjective:
    """:func:`fit_distance`'s objective for many tables at once, as arrays.

    A table enters as the matrix ``students[k, c]`` of its surveyed home Kreise ``k`` by the cells ``c``
    (home group, destination group) of the reference (:meth:`students`); :meth:`evaluate` normalises each
    Kreis row, pools with ``kreis_mix`` (ASSUMPTION A9) and measures the n_effective-weighted
    total-variation distance, the same arithmetic as :func:`implied_shares` and :func:`fit_distance`. The
    matrix is linear in the students, which stage two uses to evaluate every mover share of a triple from
    two tables. A table whose surveyed Kreis has no students has no defined fit: objective NaN.
    """

    def __init__(self, reference: pd.DataFrame, site_kreise, kreis_mix: pd.Series):
        self.sites = frozenset(site_kreise)
        if BRAUNSCHWEIG_KREIS not in self.sites:
            raise ValueError(f"{_GRID_SEARCH}: site_kreise {sorted(self.sites)} must contain {BRAUNSCHWEIG_KREIS}")
        frame = _validated_reference(reference)
        cells = [(group, label) for group in FIT_HOME_GROUPS for label in destination_vocabulary(self.sites, group)]
        keys = list(zip(frame["home_group"], frame["destination_group"]))
        if set(keys) != set(cells):
            raise ValueError(f"fit_distance: model and reference disagree on the destination groups (reference only, "
                             f"model only): {sorted(set(keys) - set(cells))[:5]}, {sorted(set(cells) - set(keys))[:5]}")
        self.cell_index = {cell: index for index, cell in enumerate(cells)}
        self.reference_share = pd.Series(frame["share"].to_numpy(dtype=float), index=pd.MultiIndex.from_tuples(keys)
                                          ).reindex(pd.MultiIndex.from_tuples(cells)).to_numpy(dtype=float)
        self.cell_group = np.array([FIT_HOME_GROUPS.index(group) for group, _ in cells])
        per_group = frame.groupby("home_group")[["n_effective", "noise_floor_tvd_95"]].first()
        per_group = per_group.reindex(list(FIT_HOME_GROUPS)).astype(float)
        n_effective = per_group["n_effective"].to_numpy()
        self.group_weight = n_effective / n_effective.sum()
        self.noise_floor = float(np.dot(self.group_weight, per_group["noise_floor_tvd_95"].to_numpy()))
        self.kreis_mix = kreis_mix
        self.kreise: np.ndarray | None = None

    def _bind_kreise(self, kreise: np.ndarray, groups: np.ndarray) -> None:
        # The first table fixes the surveyed home Kreise, their mix weights and the pooled mix per group.
        self.kreise = kreise
        self.mix = _mix_weights(kreise, self.kreis_mix, _GRID_SEARCH)
        group_index = np.array([FIT_HOME_GROUPS.index(group) for group in groups])
        self.group_mix = np.bincount(group_index, weights=self.mix, minlength=len(FIT_HOME_GROUPS))
        missing = [group for group, total in zip(FIT_HOME_GROUPS, self.group_mix) if total <= 0]
        if missing:
            raise ValueError(f"{_GRID_SEARCH}: T has no surveyed home Kreis for the home group(s) {missing}; the "
                             f"fit needs all of {list(FIT_HOME_GROUPS)}")

    def students(self, od: pd.DataFrame) -> np.ndarray:
        """The (Kreis, cell) student matrix of one T table."""
        if not isinstance(od, pd.DataFrame):
            raise ValueError(f"{_GRID_SEARCH}: build must return the T table as a DataFrame, got {type(od).__name__}")
        rows = _surveyed_rows(od, self.sites, _GRID_SEARCH)
        _check_vocabulary(rows, self.sites, _GRID_SEARCH)
        kreise, first = np.unique(rows.home, return_index=True)
        if self.kreise is None:
            self._bind_kreise(kreise, rows.group[first])
        elif not np.array_equal(kreise, self.kreise):
            raise ValueError(f"{_GRID_SEARCH}: the surveyed home Kreise of T changed between tables "
                             f"({list(self.kreise)} -> {list(kreise)}); build must lay out every triple alike")
        cell = np.fromiter((self.cell_index[key] for key in zip(rows.group, rows.label)), dtype=int,
                           count=len(rows.home))
        matrix = np.zeros((len(kreise), len(self.cell_index)))
        np.add.at(matrix, (np.searchsorted(kreise, rows.home), cell), rows.students)
        return matrix

    def evaluate(self, students: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """``(objective[...], tvd[..., group])`` for student matrices of shape ``(..., Kreis, cell)``."""
        row_sum = students.sum(axis=-1)
        defined = (row_sum > 0).all(axis=-1)
        normalised = students / np.where(row_sum > 0, row_sum, 1.0)[..., None]
        pooled = np.einsum("...kc,k->...c", normalised, self.mix) / self.group_mix[self.cell_group]
        difference = np.abs(pooled - self.reference_share)
        tvd = 0.5 * np.stack([difference[..., self.cell_group == g].sum(axis=-1)
                              for g in range(len(FIT_HOME_GROUPS))], axis=-1)
        objective = tvd @ self.group_weight
        return np.where(defined, objective, np.nan), np.where(defined[..., None], tvd, np.nan)


@contextlib.contextmanager
def _builder_log_at_warning():
    """Hold the builder's logger at WARNING (one INFO line per build would flood the log); warnings pass."""
    logger = logging.getLogger(apply_movers.__module__)
    previous = logger.level
    if logger.getEffectiveLevel() < logging.WARNING:
        logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(previous)


def _check_flat_range(max_flat_range: float) -> float:
    value = float(max_flat_range)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"calibration: max_flat_range must lie in [0, 1], got {max_flat_range!r}")
    return value


def _decide(stage: str, names: tuple[str, ...], parameters: np.ndarray, objective: np.ndarray, noise_floor: float,
            max_flat_range: float) -> tuple[int, dict, bool, list[str], np.ndarray]:
    """Optimum, indifference set, ranges and the decide-or-range rule over all evaluated parameter sets.

    ``parameters`` is (set, parameter) in grid order, ``objective`` (set,) with NaN where undefined.
    Returns ``(optimum index, ranges, decided, reasons, within)``.
    """
    defined = np.isfinite(objective)
    if not defined.any():
        raise ValueError(f"calibration stage {stage!r}: no parameter set of the grid gives a defined fit (every "
                         f"table leaves a surveyed home Kreis without students)")
    best = int(np.nanargmin(objective))
    ties = int((np.abs(objective[defined] - objective[best]) <= _FLOAT_TOLERANCE).sum())
    if ties > 1:
        LOGGER.info("[student_commuter_weights] stage %s: %d parameter sets tie at the optimum objective %.6g; the "
                    "first in grid order is taken", stage, ties, float(objective[best]))
    within = defined & (objective <= objective[best] + noise_floor)
    ranges = {name: (float(parameters[within, i].min()), float(parameters[within, i].max()))
              for i, name in enumerate(names)}
    reasons = []
    for i, name in enumerate(names):
        value = float(parameters[best, i])
        if value <= _FLOAT_TOLERANCE or value >= 1.0 - _FLOAT_TOLERANCE:
            reasons.append(f"optimum {name} = {value:g} lies at the bound of [0, 1]: the best value may lie beyond "
                           f"what the grid can express")
    for name, (low, high) in ranges.items():
        if high - low > max_flat_range + _FLOAT_TOLERANCE:
            reasons.append(f"{name} ranges from {low:g} to {high:g} within the noise floor {noise_floor:.6g} of the "
                           f"optimum (width {high - low:.6g} > max_flat_range {max_flat_range:g})")
    return best, ranges, not reasons, reasons, within


def _log_stage(stage: str, n_sets: int, n_undefined: int, optimum: CommuterWeights, mover_share: float,
               objective: float, noise_floor: float, n_within: int, ranges: dict, decided: bool,
               reasons: list[str]) -> None:
    LOGGER.info("[student_commuter_weights] stage %s: %d parameter sets, fit defined for %d/%d (%.1f%%), undefined "
                "%d (%.1f%%); optimum %s mover share %g, objective %.6g, noise floor %.6g; %d sets within the noise "
                "floor, ranges %s; decided %s", stage, n_sets, n_sets - n_undefined, n_sets,
                100.0 * (n_sets - n_undefined) / n_sets, n_undefined, 100.0 * n_undefined / n_sets, tuple(optimum),
                mover_share, objective, noise_floor, n_within, ranges, decided)
    if n_undefined:
        LOGGER.warning("[student_commuter_weights] stage %s: %d of %d parameter sets leave a surveyed home Kreis "
                       "without students; their fit is undefined (NaN) and they cannot be the optimum",
                       stage, n_undefined, n_sets)
    if not decided:
        LOGGER.warning("[student_commuter_weights] stage %s undecided, no configuration proposal: %s",
                       stage, "; ".join(reasons))


def _log_optimum_build(build: Callable[[CommuterWeights], pd.DataFrame], optimum: CommuterWeights,
                       mover_share: float = 0.0, mover_site_kreise: tuple[str, ...] = (),
                       braunschweig_kreis: str = BRAUNSCHWEIG_KREIS) -> None:
    # build returns T only, not the builder's diagnostics: rebuilding the optimum at the normal log level lets
    # the builder's own line report the weight-dependent counts (multi-site splits by enrollment only); with
    # a mover share, the movers step's own line reports the persons moved at the chosen optimum.
    LOGGER.info("[student_commuter_weights] rebuilding the optimum %s with mover share %g once at the normal log "
                "level for the builder's diagnostics (multi-site splits by weight / by enrollment only)",
                tuple(optimum), mover_share)
    od = build(optimum)
    if mover_share > 0.0:
        apply_movers(od, mover_share, mover_site_kreise, braunschweig_kreis)


def calibrate(build: Callable[[CommuterWeights], pd.DataFrame], reference: pd.DataFrame, site_kreise,
              kreis_mix: pd.Series, grid_step: float = 0.05, max_flat_range: float = 0.2) -> CalibrationResult:
    """Stage ``weights``: the weight triple with the smallest :func:`fit_distance` objective, mover share 0.

    Every triple of :func:`weight_grid` is built once (``build``, T for a triple) and measured against
    ``reference``; the decide-or-range rule is that of :class:`CalibrationResult` over the three weights.
    A triple whose T leaves a surveyed home Kreis without students (e.g. ``(0, 0, 0)``) has no defined
    fit: its row carries NaN, it is counted and logged, never the optimum.

    ASSUMPTIONS of the lead (CLI arguments): ``grid_step`` (the resolution of the search) and
    ``max_flat_range`` (the widest range of a parameter over the indifference set that still counts as
    decided).

    Raises:
        ValueError: an invalid ``grid_step`` or ``max_flat_range``, a reference :func:`fit_distance` rejects,
            a table :func:`implied_shares` rejects, or no triple with a defined fit.
    """
    max_flat_range = _check_flat_range(max_flat_range)
    grid = weight_grid(grid_step)
    measure = _GridObjective(reference, site_kreise, kreis_mix)
    with _builder_log_at_warning():
        matrices = np.stack([measure.students(build(weights)) for weights in grid])
    objective, tvd = measure.evaluate(matrices)
    parameters = np.array([tuple(weights) for weights in grid], dtype=float)
    best, ranges, decided, reasons, within = _decide(STAGE_WEIGHTS, _WEIGHT_NAMES, parameters, objective,
                                                     measure.noise_floor, max_flat_range)
    table = pd.DataFrame({"stage": STAGE_WEIGHTS, "short": parameters[:, 0], "middle": parameters[:, 1],
                          "long": parameters[:, 2], "mover_share": 0.0, "objective_tvd": objective,
                          **{_TVD_COLUMNS[group]: tvd[:, g] for g, group in enumerate(FIT_HOME_GROUPS)},
                          "within_noise_floor": within}, columns=CALIBRATION_COLUMNS)
    optimum = grid[best]
    _log_stage(STAGE_WEIGHTS, len(grid), int(np.isnan(objective).sum()), optimum, 0.0, float(objective[best]),
               measure.noise_floor, int(within.sum()), ranges, decided, reasons)
    _log_optimum_build(build, optimum)
    return CalibrationResult(stage=STAGE_WEIGHTS, table=table, optimum=optimum, mover_share=0.0,
                             objective=float(objective[best]), noise_floor=measure.noise_floor, ranges=ranges,
                             decided=decided, reasons=reasons)


def stage_two_due(result: CalibrationResult) -> bool:
    """True when the optimum's short weight is 1: the calibration ends at the bound of the short band
    (design B3 step 7), so the movers to Braunschweig are calibrated with the triple (:func:`calibrate_movers`)."""
    return float(result.optimum.short) >= 1.0 - _FLOAT_TOLERANCE


def calibrate_movers(build: Callable[[CommuterWeights], pd.DataFrame], reference: pd.DataFrame, site_kreise,
                     kreis_mix: pd.Series, mover_site_kreise: tuple[str, ...], braunschweig_kreis: str,
                     grid_step: float = 0.05, mover_step: float = 0.05,
                     max_flat_range: float = 0.2) -> CalibrationResult:
    """Stage ``weights_and_movers``: every triple of :func:`weight_grid` times every mover share on
    ``{0, mover_step, ..., 1}`` (:func:`student_od_table.apply_movers` on the table of the triple).

    One build per triple. The movers step is linear in the share: it moves ``share * movers_persons`` of
    each local row of ``mover_site_kreise`` to Braunschweig's row. So with ``p0`` the (Kreis, destination
    group) students at share 0 and ``d`` the change one full share causes (from one ``apply_movers(od, 1)``),
    the students at share ``s`` are exactly ``p0 + s * d``; every share is evaluated from these two, not by
    one ``apply_movers`` per share. The optimum, the ranges (four parameters) and ``decided`` follow
    :class:`CalibrationResult`, over all (triple, share) sets in grid order (triple-major); a mover share
    of 0 or 1 counts as a bound. ``table`` holds one row per mover share, with the best triple for that
    share (NaN where no triple gives a defined fit) and ``within_noise_floor`` of that row.

    ASSUMPTIONS of the lead (CLI arguments): ``grid_step``, ``mover_step``, ``max_flat_range``.

    Raises:
        ValueError: as :func:`calibrate`, plus an empty ``mover_site_kreise`` and every input
            :func:`student_od_table.apply_movers` rejects.
    """
    max_flat_range = _check_flat_range(max_flat_range)
    mover_site_kreise = tuple(mover_site_kreise)
    if not mover_site_kreise:
        raise ValueError("calibrate_movers: mover_site_kreise is empty; the movers stage needs at least one site Kreis")
    grid = weight_grid(grid_step)
    shares = np.array(_unit_grid(mover_step, "mover_step"))
    measure = _GridObjective(reference, site_kreise, kreis_mix)
    objective = np.empty((len(grid), len(shares)))
    tvd = np.empty((len(grid), len(shares), len(FIT_HOME_GROUPS)))
    with _builder_log_at_warning():
        for i, weights in enumerate(grid):
            od = build(weights)
            at_zero = measure.students(od)
            change = measure.students(apply_movers(od, 1.0, mover_site_kreise, braunschweig_kreis)) - at_zero
            objective[i], tvd[i] = measure.evaluate(at_zero[None] + shares[:, None, None] * change[None])

    n_shares = len(shares)
    parameters = np.column_stack([np.repeat(np.array([tuple(w) for w in grid], dtype=float), n_shares, axis=0),
                                  np.tile(shares, len(grid))])
    best, ranges, decided, reasons, within = _decide(
        STAGE_WEIGHTS_AND_MOVERS, (*_WEIGHT_NAMES, _MOVER_NAME), parameters, objective.ravel(),
        measure.noise_floor, max_flat_range)
    optimum, mover_share = grid[best // n_shares], float(shares[best % n_shares])

    rows = []
    for j, share in enumerate(shares):
        column = objective[:, j]
        if np.isfinite(column).any():
            i = int(np.nanargmin(column))
            rows.append({"short": grid[i].short, "middle": grid[i].middle, "long": grid[i].long,
                         "objective_tvd": column[i], **{_TVD_COLUMNS[group]: tvd[i, j, g]
                                                         for g, group in enumerate(FIT_HOME_GROUPS)},
                         "within_noise_floor": bool(within.reshape(objective.shape)[i, j])})
        else:
            rows.append({"within_noise_floor": False})
        rows[-1].update(stage=STAGE_WEIGHTS_AND_MOVERS, mover_share=float(share))
    table = pd.DataFrame(rows).reindex(columns=CALIBRATION_COLUMNS)
    table["within_noise_floor"] = table["within_noise_floor"].astype(bool)
    _log_stage(STAGE_WEIGHTS_AND_MOVERS, objective.size, int(np.isnan(objective).sum()), optimum, mover_share,
               float(objective.ravel()[best]), measure.noise_floor, int(within.sum()), ranges, decided, reasons)
    _log_optimum_build(build, optimum, mover_share, mover_site_kreise, braunschweig_kreis)
    return CalibrationResult(stage=STAGE_WEIGHTS_AND_MOVERS, table=table, optimum=optimum, mover_share=mover_share,
                             objective=float(objective.ravel()[best]), noise_floor=measure.noise_floor,
                             ranges=ranges, decided=decided, reasons=reasons)


def fit_table(result: CalibrationResult, build: Callable[[CommuterWeights], pd.DataFrame], reference: pd.DataFrame,
              site_kreise, kreis_mix: pd.Series, mover_site_kreise: tuple[str, ...],
              braunschweig_kreis: str) -> pd.DataFrame:
    """The reference next to T at the result's optimum, cell by cell (:data:`FIT_COLUMNS`).

    T is built at ``result.optimum`` and, when ``result.mover_share`` is above 0, the movers are applied
    (:func:`student_od_table.apply_movers`); the implied shares come from :func:`implied_shares`. One row
    per fitted reference cell, in the reference's order (the pooled ``region`` rows are not fitted);
    ``difference_pp`` = 100 * (implied - SrV share), ``reachable`` as in :func:`implied_shares`.

    Raises:
        ValueError: a reference without ``n_unweighted`` or ``se_share``, anything :func:`fit_distance`
            rejects, or an objective at the optimum that differs from ``result.objective`` (the result
            was calibrated with another build, reference or mix).
    """
    _require_columns(reference, ["n_unweighted", "se_share"], "fit_table reference")
    od = build(result.optimum)
    if result.mover_share > 0.0:
        od = apply_movers(od, result.mover_share, tuple(mover_site_kreise), braunschweig_kreis)
    implied = implied_shares(od, site_kreise, kreis_mix)
    fit = fit_distance(implied, reference)
    if abs(fit.objective - result.objective) > _OBJECTIVE_AGREEMENT:
        raise ValueError(f"fit_table: the objective at the optimum is {fit.objective:.10g}, the result says "
                         f"{result.objective:.10g}; the result was calibrated with another build, reference or mix")
    cells = reference[reference["home_group"] != HOME_GROUP_ALL]
    merged = cells.merge(implied, on=["home_group", "destination_group"], how="left", validate="one_to_one")
    srv_share = pd.to_numeric(merged["share"]).to_numpy(dtype=float)
    implied_share = merged["implied_share"].to_numpy(dtype=float)
    return pd.DataFrame({"stage": result.stage, "home_group": merged["home_group"].to_numpy(),
                         "destination_group": merged["destination_group"].to_numpy(),
                         "srv_n_unweighted": merged["n_unweighted"].to_numpy(), "srv_share": srv_share,
                         "srv_se_share": pd.to_numeric(merged["se_share"]).to_numpy(dtype=float),
                         "implied_share": implied_share, "difference_pp": 100.0 * (implied_share - srv_share),
                         "reachable": merged["reachable"].to_numpy(dtype=bool)}, columns=FIT_COLUMNS)


def _yaml_number(value: float) -> str:
    return repr(round(float(value), 10))


def config_snippet(result: CalibrationResult) -> str | None:
    """A YAML proposal of the calibrated values for Phase 4, or ``None`` unless ``result.decided``.

    Keys ``student_commuter_weights`` (``short``, ``middle``, ``long``) and, for stage
    :data:`STAGE_WEIGHTS_AND_MOVERS`, ``student_movers_to_braunschweig_share``. Text only: the CLI writes
    it to the run artifact; no configuration file is changed here.
    """
    if not result.decided:
        return None
    lines = [f"# Proposal for Phase 4 from calibration stage '{result.stage}' against the SrV destination "
             f"reference (objective {result.objective:.6g}, noise floor {result.noise_floor:.6g}); not applied.",
             "# The key names are proposals too, to be confirmed when Phase 4 adds them to the configuration.",
             "student_commuter_weights:",
             *(f"  {name}: {_yaml_number(value)}" for name, value in zip(_WEIGHT_NAMES, result.optimum))]
    if result.stage == STAGE_WEIGHTS_AND_MOVERS:
        lines.append(f"student_movers_to_braunschweig_share: {_yaml_number(result.mover_share)}")
    return "\n".join(lines) + "\n"
