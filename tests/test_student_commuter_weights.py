"""Tests for braunschweig.calibration.student_commuter_weights (#140, Phase 2, Task 4).

The student table T is mapped onto the destination groups of the committed SrV reference
(srv2023_student_education_destination.csv) and compared with it. Inputs are small synthetic
frames with hand-computed expectations; one test reads the committed reference to prove that
both sides agree on the destination-group vocabulary.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_student_reference as SR
from braunschweig.calibration import student_commuter_weights as W
from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS, ZGB_KREISE
from braunschweig.data.education import student_od_table as T

ROOT = Path(__file__).resolve().parents[1]
SRV = ROOT / "eqasim-data" / "data" / "braunschweig" / "srv"

BS = SR.BRAUNSCHWEIG_KREIS
GROUPS = (SR.HOME_GROUP_BRAUNSCHWEIG, SR.HOME_GROUP_SITE, SR.HOME_GROUP_NO_SITE)


def _row(home, site_kreis, students, *, scope=T.SCOPE_LOCAL, kind=None, in_region=True, distance_km=10.0,
         destination_id=None):
    if kind is None:
        kind = T.KIND_OUT_COMMUTER if scope == T.SCOPE_SURROUNDING else (
            T.KIND_LOCAL if home == site_kreis else T.KIND_COMMUTER)
    row = {column: np.nan for column in T.T_COLUMNS}
    row.update(home_ars5=home, home_in_region=in_region, destination_id=destination_id or "site_" + site_kreis,
               institution="inst_" + site_kreis, site_kreis_ars5=site_kreis, scope=scope,
               distance_km=distance_km, students_persons=float(students), movers_persons=0.0, cell_kind=kind)
    return row


def _od(*rows):
    return pd.DataFrame(list(rows), columns=T.T_COLUMNS)


def _share(shares, group, label):
    cell = shares[(shares["home_group"] == group) & (shares["destination_group"] == label)]
    assert len(cell) == 1, (group, label)
    return cell.iloc[0]


def _reference(site_kreise, shares, n_effective, noise_floor):
    """A reference frame in the committed layout; every vocabulary label not in ``shares`` is 0."""
    rows = []
    for group in GROUPS:
        for label in SR.destination_vocabulary(site_kreise, group):
            rows.append({"home_group": group, "destination_group": label, "n_persons_group": 10,
                         "n_unweighted": 1, "weight_sum": 1.0, "share": shares[group].get(label, 0.0),
                         "n_effective": n_effective[group], "se_share": 0.0,
                         "noise_floor_tvd_95": noise_floor[group]})
    return pd.DataFrame(rows, columns=SR.DESTINATION_COLUMNS)


def _implied(site_kreise, shares):
    rows = []
    for group in GROUPS:
        for label in SR.destination_vocabulary(site_kreise, group):
            rows.append({"home_group": group, "destination_group": label,
                         "implied_share": shares[group].get(label, 0.0), "reachable": True})
    return pd.DataFrame(rows)


def test_destination_groups_by_hand():
    assert W.destination_group(BS, BS, T.SCOPE_LOCAL) == SR.DEST_OWN
    assert W.destination_group(BS, "03158", T.SCOPE_LOCAL) == SR.DEST_SITE_PREFIX + "03158"
    assert W.destination_group("03153", BS, T.SCOPE_LOCAL) == SR.DEST_SITE_PREFIX + BS
    assert W.destination_group(BS, SR.REGION_HANNOVER_ARS5, T.SCOPE_SURROUNDING) == SR.DEST_REGION_HANNOVER
    assert W.destination_group(BS, SR.LANDKREIS_HILDESHEIM_ARS5, T.SCOPE_SURROUNDING) == SR.DEST_LK_HILDESHEIM
    assert W.destination_group(BS, "09162", T.SCOPE_SURROUNDING) == SR.DEST_OTHER_OUTSIDE
    with pytest.raises(ValueError, match="scope"):
        W.destination_group(BS, BS, "abroad")


def test_wolfsburg_has_no_home_group_and_outside_rows_are_ignored():
    sites = frozenset({BS, "03158", WOLFSBURG_KREIS})
    assert W.home_group(WOLFSBURG_KREIS, sites) is None
    assert W.home_group(BS, sites) == SR.HOME_GROUP_BRAUNSCHWEIG
    assert W.home_group("03158", sites) == SR.HOME_GROUP_SITE
    assert W.home_group("03151", sites) == SR.HOME_GROUP_NO_SITE

    od = _od(_row(BS, BS, 80), _row(BS, "03158", 20),
             # Wolfsburg (unsurveyed, no mix weight) and an in-commuter from outside the region: if either
             # reached a home group, the missing mix weight would raise or the Braunschweig shares would move.
             _row(WOLFSBURG_KREIS, WOLFSBURG_KREIS, 500), _row(WOLFSBURG_KREIS, BS, 300),
             _row("09162", BS, 1000, in_region=False))
    shares = W.implied_shares(od, sites, pd.Series({BS: 1.0}))
    assert set(shares["home_group"]) == {SR.HOME_GROUP_BRAUNSCHWEIG}
    assert _share(shares, SR.HOME_GROUP_BRAUNSCHWEIG, SR.DEST_OWN)["implied_share"] == pytest.approx(0.8)
    assert _share(shares, SR.HOME_GROUP_BRAUNSCHWEIG, SR.DEST_SITE_PREFIX + "03158")["implied_share"] == \
        pytest.approx(0.2)


def test_pooled_home_groups_use_the_srv_kreis_mix():
    # Kreis P: 900 own, 100 to Braunschweig (row 1000); Kreis Q: 50 own, 50 to Braunschweig (row 100); mix 1 : 1.
    # Pooling with the model's own row sums would give own = 950 / 1100 instead of (0.9 + 0.5) / 2.
    od = _od(_row("P", "P", 900), _row("P", BS, 100), _row("Q", "Q", 50), _row("Q", BS, 50),
             _row(BS, BS, 10))
    sites = frozenset({"P", "Q", BS})
    shares = W.implied_shares(od, sites, pd.Series({"P": 1.0, "Q": 1.0, BS: 1.0}))
    own = shares[(shares.home_group == SR.HOME_GROUP_SITE) & (shares.destination_group == SR.DEST_OWN)]
    assert own["implied_share"].iloc[0] == pytest.approx((0.9 + 0.5) / 2)
    assert _share(shares, SR.HOME_GROUP_SITE, SR.DEST_SITE_PREFIX + BS)["implied_share"] == pytest.approx(0.3)

    site_group = shares[shares["home_group"] == SR.HOME_GROUP_SITE]
    assert list(site_group["destination_group"]) == SR.destination_vocabulary(sites, SR.HOME_GROUP_SITE)
    assert site_group["implied_share"].sum() == pytest.approx(1.0)
    # Reachable means a row of the group maps to the label; site_kreis_P and site_kreis_Q get none here.
    reachable = dict(zip(site_group["destination_group"], site_group["reachable"]))
    assert reachable == {label: label in (SR.DEST_OWN, SR.DEST_SITE_PREFIX + BS) for label in reachable}


def test_braunschweig_commuter_and_mover_rows_of_one_site_are_summed():
    od = _od(_row(BS, BS, 60), _row(BS, "03158", 30, destination_id="site_x"),
             _row(BS, "03158", 10, kind=T.KIND_MOVER, destination_id="site_x"))
    shares = W.implied_shares(od, frozenset({BS, "03158"}), pd.Series({BS: 2.0}))
    assert _share(shares, SR.HOME_GROUP_BRAUNSCHWEIG, SR.DEST_SITE_PREFIX + "03158")["implied_share"] == \
        pytest.approx(0.4)


@pytest.mark.parametrize("mix, od_rows, match", [
    (pd.Series({BS: 1.0}), [_row(BS, BS, 10), _row("03151", BS, 10)], "mix"),           # no mix weight
    (pd.Series({BS: 1.0, "03151": np.nan}), [_row(BS, BS, 10), _row("03151", BS, 10)], "mix"),
    (pd.Series({BS: 1.0, "03151": 1.0}), [_row(BS, BS, 10), _row("03151", BS, 0)], "empty"),
    (pd.Series({BS: 1.0}), [_row(BS, BS, 10), _row(BS, "03999", 5)], "label"),           # site outside site_kreise
])
def test_implied_shares_rejects_missing_mix_empty_rows_and_unknown_sites(mix, od_rows, match):
    with pytest.raises(ValueError, match=match):
        W.implied_shares(_od(*od_rows), frozenset({BS, "03158"}), mix)


def test_unreachable_reference_cells_stay_in_the_distance():
    # reference: no-site group 0.1 own_kreis (unreachable), 0.9 site_kreis_03101; model 1.0 site_kreis_03101 -> TVD 0.1
    sites = frozenset({BS, "S"})
    od = _od(_row(BS, BS, 100), _row("S", "S", 100), _row("N", BS, 100))
    implied = W.implied_shares(od, sites, pd.Series({BS: 1.0, "S": 1.0, "N": 1.0}))
    own = _share(implied, SR.HOME_GROUP_NO_SITE, SR.DEST_OWN)
    assert own["implied_share"] == 0.0 and not own["reachable"]

    reference = _reference(
        sites, {SR.HOME_GROUP_BRAUNSCHWEIG: {SR.DEST_OWN: 1.0}, SR.HOME_GROUP_SITE: {SR.DEST_OWN: 1.0},
                SR.HOME_GROUP_NO_SITE: {SR.DEST_OWN: 0.1, SR.DEST_SITE_PREFIX + BS: 0.9}},
        n_effective=dict.fromkeys(GROUPS, 10.0), noise_floor=dict.fromkeys(GROUPS, 0.3))
    fit = W.fit_distance(implied, reference)
    by_group = fit.by_group.set_index("home_group")["total_variation_distance"]
    assert by_group.to_dict() == pytest.approx({SR.HOME_GROUP_BRAUNSCHWEIG: 0.0, SR.HOME_GROUP_SITE: 0.0,
                                                SR.HOME_GROUP_NO_SITE: 0.1})
    assert fit.objective == pytest.approx(0.1 / 3)
    assert fit.noise_floor == pytest.approx(0.3)


def test_fit_distance_weights_groups_with_n_effective():
    # TVD 0.2 / 0.0 / 0.4 with n_effective 40 / 20 / 20 -> (8 + 0 + 8) / 80 = 0.2;
    # noise floors 0.1 / 0.2 / 0.3 -> (4 + 4 + 6) / 80 = 0.175.
    sites = frozenset({BS, "03158"})
    site = SR.DEST_SITE_PREFIX + "03158"
    reference = _reference(
        sites, {SR.HOME_GROUP_BRAUNSCHWEIG: {SR.DEST_OWN: 0.8, site: 0.2},
                SR.HOME_GROUP_SITE: {SR.DEST_OWN: 0.5, SR.DEST_SITE_PREFIX + BS: 0.5},
                SR.HOME_GROUP_NO_SITE: {SR.DEST_OWN: 0.4, SR.DEST_SITE_PREFIX + BS: 0.6}},
        n_effective={SR.HOME_GROUP_BRAUNSCHWEIG: 40.0, SR.HOME_GROUP_SITE: 20.0, SR.HOME_GROUP_NO_SITE: 20.0},
        noise_floor={SR.HOME_GROUP_BRAUNSCHWEIG: 0.1, SR.HOME_GROUP_SITE: 0.2, SR.HOME_GROUP_NO_SITE: 0.3})
    implied = _implied(sites, {SR.HOME_GROUP_BRAUNSCHWEIG: {SR.DEST_OWN: 1.0},
                               SR.HOME_GROUP_SITE: {SR.DEST_OWN: 0.5, SR.DEST_SITE_PREFIX + BS: 0.5},
                               SR.HOME_GROUP_NO_SITE: {SR.DEST_SITE_PREFIX + BS: 1.0}})
    fit = W.fit_distance(implied, reference)
    assert fit.objective == pytest.approx(0.2)
    assert fit.noise_floor == pytest.approx(0.175)
    assert sorted(fit.by_group["home_group"]) == sorted(GROUPS)


def _broken(kind):
    sites = frozenset({BS, "03158"})
    reference = _reference(sites, {group: {SR.DEST_OWN: 1.0} for group in GROUPS},
                           n_effective=dict.fromkeys(GROUPS, 10.0), noise_floor=dict.fromkeys(GROUPS, 0.3))
    implied = _implied(sites, {group: {SR.DEST_OWN: 1.0} for group in GROUPS})
    if kind == "unknown reference label":
        reference.loc[reference["destination_group"] == SR.DEST_OTHER_OUTSIDE, "destination_group"] = "elsewhere"
    elif kind == "open shares":
        reference.loc[(reference["home_group"] == SR.HOME_GROUP_SITE)
                      & (reference["destination_group"] == SR.DEST_OWN), "share"] = 0.9
    elif kind == "missing home group":
        reference = reference[reference["home_group"] != SR.HOME_GROUP_NO_SITE]
    elif kind == "model label unknown to the reference":
        implied.loc[implied["destination_group"] == SR.DEST_SITE_PREFIX + "03158", "destination_group"] = \
            SR.DEST_SITE_PREFIX + "03999"
    return implied, reference


@pytest.mark.parametrize("kind", ["unknown reference label", "open shares", "missing home group",
                                  "model label unknown to the reference"])
def test_reference_with_an_unknown_label_or_open_shares_raises(kind):
    implied, reference = _broken(kind)
    with pytest.raises(ValueError):
        W.fit_distance(implied, reference)


def test_implied_distance_classes_by_hand():
    # Kreis A: 55 local (own_kreis, its distance is ignored), 25 commuters + 5 movers at 20 km (under 25),
    # 15 out-commuters at exactly 25 km (left-inclusive: 25 to 50), 0 at 80 km -> 100 in total.
    # Kreis B: 40 local, 10 commuters at exactly 50 km (50 plus) -> 50. Kreis C lies outside the region.
    od = _od(_row("A", "A", 55, distance_km=3.0), _row("A", "B", 25, distance_km=20.0),
             _row("A", "B", 5, kind=T.KIND_MOVER, distance_km=20.0),
             _row("A", "H", 15, scope=T.SCOPE_SURROUNDING, distance_km=25.0),
             _row("A", "K", 0, scope=T.SCOPE_SURROUNDING, distance_km=80.0),
             _row("B", "B", 40, distance_km=4.0), _row("B", "A", 10, distance_km=50.0),
             _row("C", "A", 1000, in_region=False, distance_km=5.0))
    classes = W.implied_distance_classes(od, ["A", "B"])
    assert list(classes.columns) == ["code", "distance_class", "students_persons", "share"]
    table = classes.set_index(["code", "distance_class"])
    expected = {"A": (55.0, 30.0, 15.0, 0.0), "B": (40.0, 0.0, 0.0, 10.0)}
    for code, persons in expected.items():
        for name, value in zip(W.DISTANCE_CLASSES, persons):
            assert table.loc[(code, name), "students_persons"] == pytest.approx(value)
            assert table.loc[(code, name), "share"] == pytest.approx(value / sum(persons))
    assert set(classes["code"]) == {"A", "B"}


def test_committed_reference_is_accepted_and_matches_the_model_vocabulary():
    """The committed SrV table and implied_shares agree on the home and destination groups."""
    header = " ".join(line for line in (SRV / SR.DESTINATION_TABLE).read_text(encoding="utf-8").splitlines()
                      if line.startswith("#"))
    match = re.search(r"Site Kreise \([^)]*\): (\[[^\]]*\]);", header)
    assert match, "no 'Site Kreise' header line in %s" % SR.DESTINATION_TABLE
    sites = frozenset(ast.literal_eval(match.group(1)))
    reference = pd.read_csv(SRV / SR.DESTINATION_TABLE, comment="#", dtype={"home_group": str,
                                                                            "destination_group": str})
    mix_table = pd.read_csv(SRV / SR.STUDENT_SHARE_TABLE, comment="#", dtype={"code": str})
    mix = mix_table[mix_table["level"] == "kreis"].set_index("code")["students_weighted_persons"]

    # Every site Kreis studies at its own site, every other Kreis commutes to Braunschweig.
    od = _od(*[_row(code, code, 100) if code in sites else _row(code, BS, 100) for code in ZGB_KREISE])
    implied = W.implied_shares(od, sites, mix)
    fit = W.fit_distance(implied, reference)

    assert sorted(fit.by_group["home_group"]) == sorted(GROUPS)
    assert 0.0 < fit.objective <= 1.0
    floors = reference.groupby("home_group")["noise_floor_tvd_95"].first().loc[list(GROUPS)]
    assert floors.min() <= fit.noise_floor <= floors.max()
