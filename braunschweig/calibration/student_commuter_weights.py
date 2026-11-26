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

Pure module (no I/O). :func:`implied_shares` and :func:`fit_distance` are called once per weight
triple by the calibration (about 1,800 calls), so both work on whole columns.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_participation_universe import UNSURVEYED_KREISE
from braunschweig.calibration.srv_student_reference import (
    BRAUNSCHWEIG_KREIS, DEST_LK_HILDESHEIM, DEST_OTHER_OUTSIDE, DEST_OTHER_REGION, DEST_OWN,
    DEST_REGION_HANNOVER, DEST_SITE_PREFIX, HOME_GROUP_ALL, HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_NO_SITE,
    HOME_GROUP_SITE, LANDKREIS_HILDESHEIM_ARS5, REGION_HANNOVER_ARS5, destination_vocabulary)
from braunschweig.calibration.student_behaviour_labels import STUDENT_DISTANCE_EDGES_KM
from braunschweig.data.education.student_od_table import KIND_LOCAL, SCOPE_LOCAL, SCOPE_SURROUNDING, SCOPES

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
DISTANCE_CLASS_EDGES_KM = tuple(STUDENT_DISTANCE_EDGES_KM[3:5])
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
    _require_columns(od, _OD_COLUMNS, "implied_shares od")
    sites = frozenset(site_kreise)
    if BRAUNSCHWEIG_KREIS not in sites:
        raise ValueError(f"implied_shares: site_kreise {sorted(sites)} must contain {BRAUNSCHWEIG_KREIS} "
                         f"(the Braunschweig home group is defined by it)")
    in_region = od["home_in_region"].to_numpy(dtype=bool)
    home = od["home_ars5"].to_numpy(dtype=object)[in_region].astype(str)
    groups = _home_groups(home, sites)
    surveyed = pd.notna(groups)
    home, groups = home[surveyed], groups[surveyed]
    students = od["students_persons"].to_numpy(dtype=float)[in_region][surveyed]
    if not np.isfinite(students).all() or (students < 0).any():
        raise ValueError(f"implied_shares: column 'students_persons' holds negative or non-finite values for home "
                         f"Kreis(e) {sorted(set(home[~np.isfinite(students) | (students < 0)]))[:5]}")
    labels = _destination_groups(home, od["site_kreis_ars5"].to_numpy(dtype=object)[in_region][surveyed],
                                 od["scope"].to_numpy(dtype=object)[in_region][surveyed])

    frame = pd.DataFrame({"home": home, "group": groups, "label": labels, "students": students})
    row_sum = frame.groupby("home", sort=True)["students"].sum()
    mix = pd.to_numeric(pd.Series(kreis_mix).reindex(row_sum.index), errors="coerce")
    no_mix = mix.index[~(np.isfinite(mix.to_numpy(dtype=float)) & (mix.to_numpy(dtype=float) > 0))]
    if len(no_mix):
        raise ValueError(f"implied_shares: surveyed home Kreis(e) {list(no_mix)} have no positive kreis_mix weight "
                         f"(SrV 'students_weighted_persons'); got {mix.loc[no_mix].tolist()}")
    empty = row_sum.index[row_sum.to_numpy() <= 0]
    if len(empty):
        raise ValueError(f"implied_shares: surveyed home Kreis(e) {list(empty)} have an empty row (column "
                         f"'students_persons' sums to 0); their destination distribution is undefined")

    frame["weighted"] = (frame["students"] / frame["home"].map(row_sum)) * frame["home"].map(mix)
    pooled = frame.groupby(["group", "label"], sort=False)["weighted"].sum()
    group_mix = mix.groupby(frame.groupby("home", sort=True)["group"].first()).sum()

    vocabulary = {group: destination_vocabulary(sites, group) for group in FIT_HOME_GROUPS}
    produced = frame[["group", "label"]].drop_duplicates()
    unknown = [(g, l) for g, l in produced.itertuples(index=False) if l not in vocabulary[g]]
    if unknown:
        raise ValueError(f"implied_shares: destination label(s) (home_group, destination_group) {unknown[:5]} are not "
                         f"in the SrV reference vocabulary for site_kreise {sorted(sites)}; model and reference "
                         f"disagree on the site set")

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
                 int((~in_region).sum()), int((~surveyed).sum()), int((~result["reachable"]).sum()), len(result))
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
    if not np.isfinite(implied_share).all():
        raise ValueError(f"fit_distance implied: column 'implied_share' must be finite; non-finite for "
                         f"{implied.loc[~np.isfinite(implied_share), ['home_group', 'destination_group']].values.tolist()[:5]}")
    sums = pd.Series(implied_share, index=implied.index).groupby(implied["home_group"]).sum()
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
