"""Tests for braunschweig.calibration.student_commuter_weights (#140, Phase 2, Tasks 4 and 5).

The student table T is mapped onto the destination groups of the committed SrV reference
(srv2023_student_education_destination.csv) and compared with it. Inputs are small synthetic
frames with hand-computed expectations; one test reads the committed reference to prove that
both sides agree on the destination-group vocabulary. The grid search is tested on a toy whose
reference is built from a known weight triple and mover share (:func:`reference_from`), so the
calibration must recover them.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

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
    # The unreachable 0.1 is the floor of the no-site group's distance; the other groups reach every
    # cell the reference fills.
    floor = fit.by_group.set_index("home_group")["unreachable_reference_share"]
    assert floor.to_dict() == pytest.approx({SR.HOME_GROUP_BRAUNSCHWEIG: 0.0, SR.HOME_GROUP_SITE: 0.0,
                                             SR.HOME_GROUP_NO_SITE: 0.1})


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
    elif kind == "non-finite implied share":
        implied.loc[implied["destination_group"] == SR.DEST_OTHER_OUTSIDE, "implied_share"] = np.nan
    elif kind == "open implied shares":
        implied.loc[(implied["home_group"] == SR.HOME_GROUP_NO_SITE)
                    & (implied["destination_group"] == SR.DEST_OWN), "implied_share"] = 0.9
    return implied, reference


@pytest.mark.parametrize("kind, match", [
    ("unknown reference label", "reference: column 'destination_group' has unknown label"),
    ("open shares", "reference: column 'share' does not sum to 1"),
    ("missing home group", "reference: column 'home_group'"),
    ("model label unknown to the reference", "disagree"),
    ("non-finite implied share", "implied: column 'implied_share' must be finite"),
    ("open implied shares", "implied: column 'implied_share' does not sum to 1"),
])
def test_reference_with_an_unknown_label_or_open_shares_raises(kind, match):
    implied, reference = _broken(kind)
    with pytest.raises(ValueError, match=match):
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
    # The class names fix the edges; they are taken by value from student_behaviour_labels.
    assert W.DISTANCE_CLASS_EDGES_KM == (25.0, 50.0)
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


# --- the grid search (Task 5) -----------------------------------------------------------------------------

# Toy region Braunschweig (BS), site Kreis S, no-site Kreise N and M. Uni 1 has site U1 in BS (enrollment 1000,
# CHE total 500 -> scale 2), Uni 2 site U2 in S (enrollment 400, CHE total 250 -> scale 1.6), Uni H a
# surrounding site H1 in Region Hannover (outside LSN, unscaled). Each pair loads on ONE anchor, so with the
# triple (a, b, c) and mover share s (movers of U2: 160 - 80a - 40b - 40c) the rows are, by hand:
#   BS: own 1000 - 200a - 100b - 100c, site_kreis_S 80a + s * movers, region_hannover 100b
#   S:  own 400 - 80a - 40b - 40c - s * movers, site_kreis_03101 200a, region_hannover 50c
#   N:  site_kreis_03101 100b, site_kreis_S 40c, region_hannover 100a
#   M:  site_kreis_03101 100c, site_kreis_S 40b, region_hannover 50b
# N and M are pooled 1 : 3 (MIX) into the no-site group. Eight free shares for four parameters: the triple
# and the mover share are identifiable. (0, 0, 0) leaves N and M without students (their fit is undefined).
SITE_KREISE = frozenset({BS, "S"})
MOVER_SITE_KREISE = ("S",)
MIX = pd.Series({BS: 2.0, "S": 1.0, "N": 1.0, "M": 3.0})
_SHORT, _MIDDLE, _LONG = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)


def _toy_builder_inputs():
    origins = {"Uni 1": {BS: 300, "S": 100, "N": 50, "M": 50}, "Uni 2": {BS: 50, "S": 150, "N": 25, "M": 25},
               "Uni H": {BS: 100, "S": 50, "N": 100, "M": 50}}
    cells = pd.DataFrame([(kreis, institution, persons, T.SOURCE_BOTH_VIEWS, float(persons))
                          for institution, by_kreis in origins.items() for kreis, persons in by_kreis.items()],
                         columns=T.CELL_COLUMNS)
    sites = pd.DataFrame([
        {"destination_id": "U1", "che_institution": "Uni 1", "institution": "LSN Uni 1", "site_kreis_ars5": BS,
         "scope": T.SCOPE_LOCAL, "students_persons": 1000.0},
        {"destination_id": "U2", "che_institution": "Uni 2", "institution": "LSN Uni 2", "site_kreis_ars5": "S",
         "scope": T.SCOPE_LOCAL, "students_persons": 400.0},
        {"destination_id": "H1", "che_institution": "Uni H", "institution": "Uni H",
         "site_kreis_ars5": SR.REGION_HANNOVER_ARS5, "scope": T.SCOPE_SURROUNDING, "students_persons": np.nan}])
    coefficients = {(BS, "U1"): _SHORT, (BS, "U2"): _SHORT, (BS, "H1"): _MIDDLE,
                    ("S", "U1"): _SHORT, ("S", "U2"): _SHORT, ("S", "H1"): _LONG,
                    ("N", "U1"): _MIDDLE, ("N", "U2"): _LONG, ("N", "H1"): _SHORT,
                    ("M", "U1"): _LONG, ("M", "U2"): _MIDDLE, ("M", "H1"): _MIDDLE}
    pairs = pd.DataFrame([{"ars5": kreis, "destination_id": site, "distance_km": 10.0, "coeff_short": c[0],
                           "coeff_middle": c[1], "coeff_long": c[2]} for (kreis, site), c in coefficients.items()])
    totals = pd.Series({"Uni 1": 500, "Uni 2": 250, "Uni H": 1000}, dtype="int64")
    return cells, totals, sites, pairs, [BS, "S", "N", "M"]


@pytest.fixture(scope="module")
def toy_build():
    """``CommuterWeights -> T`` on the toy, memoised: the grid searches of several tests share the builds."""
    inputs = _toy_builder_inputs()
    cache = {}

    def build(weights):
        key = tuple(float(value) for value in weights)
        if key not in cache:
            cache[key] = T.build_od_table(*inputs, weights=T.CommuterWeights(*key))[0]
        return cache[key].copy()
    return build


def reference_from(build, weights, noise_floor, mover_share=0.0):
    """A reference in the committed layout whose shares are the toy's implied shares at a known parameter set.

    ``n_unweighted`` and ``se_share`` differ per row so that a column mix-up in the fit table shows; one
    pooled ``region`` row is added, as in the committed table, and must never be fitted.
    """
    od = build(weights)
    if mover_share:
        od = T.apply_movers(od, mover_share, MOVER_SITE_KREISE, BS)
    implied = W.implied_shares(od, SITE_KREISE, MIX)
    counter = np.arange(len(implied)) + 1
    reference = implied.assign(n_persons_group=100, n_unweighted=counter, weight_sum=1.0,
                               share=implied["implied_share"], n_effective=50.0, se_share=0.001 * counter,
                               noise_floor_tvd_95=noise_floor)
    pooled = reference.iloc[[0]].assign(home_group=SR.HOME_GROUP_ALL, share=1.0)
    return pd.concat([reference, pooled], ignore_index=True)[SR.DESTINATION_COLUMNS]


def _slow_fit(build, weights, mover_share, reference):
    od = build(weights)
    if mover_share > 0:
        od = T.apply_movers(od, mover_share, MOVER_SITE_KREISE, BS)
    return W.fit_distance(W.implied_shares(od, SITE_KREISE, MIX), reference)


def test_grid_has_only_non_increasing_triples():
    grid = W.weight_grid(0.5)
    assert len(grid) == 10
    assert len(set(grid)) == 10 and all(isinstance(w, T.CommuterWeights) for w in grid)
    assert all(w.short >= w.middle >= w.long and set(w) <= {0.0, 0.5, 1.0} for w in grid)
    assert len(W.weight_grid(0.05)) == 1771                    # C(23, 3) non-increasing triples on 21 values
    with pytest.raises(ValueError, match="step"):
        W.weight_grid(0.3)                                      # 1 / 0.3 is no whole number: 1 never reached


def test_known_optimum_is_recovered_on_a_toy(toy_build):
    # About 15 s: recovery at the production grid step 0.05 needs all 1,771 builds of the toy (the builder lays
    # out its inputs on every call, about 10 ms); the memo hands these builds to the coarser grids below.
    ref = reference_from(toy_build, T.CommuterWeights(0.6, 0.3, 0.1), noise_floor=0.001)
    res = W.calibrate(toy_build, ref, SITE_KREISE, MIX, grid_step=0.05)
    assert tuple(res.optimum) == pytest.approx((0.6, 0.3, 0.1)) and res.decided
    assert res.stage == "weights" and res.mover_share == 0.0 and res.reasons == []
    assert res.objective == pytest.approx(0.0, abs=1e-12) and res.noise_floor == pytest.approx(0.001)
    assert not W.stage_two_due(res)

    table = res.table
    assert list(table.columns) == W.CALIBRATION_COLUMNS and len(table) == 1771
    assert (table["stage"] == "weights").all() and (table["mover_share"] == 0.0).all()
    # (0, 0, 0) leaves Kreis N without students: its fit is undefined (NaN) and never the optimum.
    zero = table[(table["short"] == 0) & (table["middle"] == 0) & (table["long"] == 0)]
    assert len(zero) == 1 and zero["objective_tvd"].isna().all() and not zero["within_noise_floor"].any()
    within = table[table["within_noise_floor"]]
    assert (within["objective_tvd"] <= res.objective + res.noise_floor).all()
    for name in ("short", "middle", "long"):
        assert res.ranges[name] == (within[name].min(), within[name].max())
        low, high = res.ranges[name]
        assert low <= getattr(res.optimum, name) <= high and high - low <= 0.2
    assert set(res.ranges) == {"short", "middle", "long"}

    proposal = yaml.safe_load(W.config_snippet(res))
    assert proposal == {"student_commuter_weights": {"short": 0.6, "middle": 0.3, "long": 0.1}}


def test_flat_fit_gives_ranges_and_no_snippet(toy_build):
    # A noise floor of 0.5 puts most of the grid within reach of the optimum: the survey cannot decide.
    ref = reference_from(toy_build, T.CommuterWeights(0.6, 0.3, 0.1), noise_floor=0.5)
    res = W.calibrate(toy_build, ref, SITE_KREISE, MIX, grid_step=0.1, max_flat_range=0.2)
    assert tuple(res.optimum) == pytest.approx((0.6, 0.3, 0.1))
    assert not res.decided and W.config_snippet(res) is None
    too_wide = [name for name, (low, high) in res.ranges.items() if high - low > 0.2]
    assert too_wide and len(res.reasons) == len(too_wide)      # interior optimum: one reason per wide range
    assert res.table["within_noise_floor"].sum() > 1


@pytest.fixture(scope="module")
def bound_reference(toy_build):
    """Reference of the triple (1.0, 0.3, 0.1) with mover share 0.4: the short weight sits at its bound."""
    return reference_from(toy_build, T.CommuterWeights(1.0, 0.3, 0.1), noise_floor=0.001, mover_share=0.4)


@pytest.fixture(scope="module")
def bound_stage_one(toy_build, bound_reference):
    return W.calibrate(toy_build, bound_reference, SITE_KREISE, MIX, grid_step=0.1)


@pytest.fixture(scope="module")
def bound_stage_two(toy_build, bound_reference):
    return W.calibrate_movers(toy_build, bound_reference, SITE_KREISE, MIX, MOVER_SITE_KREISE, BS, grid_step=0.1,
                              mover_step=0.1)


def test_optimum_at_a_bound_is_undecided_and_makes_stage_two_due(bound_stage_one):
    # Movers living in Braunschweig look like extra short-distance commuting when the mover share is held at 0.
    res = bound_stage_one
    assert res.optimum.short == 1.0 and W.stage_two_due(res)
    assert not res.decided and res.reasons and W.config_snippet(res) is None
    assert res.objective > 0.0                                  # no triple alone reproduces the movers


def test_stage_two_recovers_a_known_mover_share(bound_stage_two):
    res = bound_stage_two
    assert res.stage == "weights_and_movers"
    assert tuple(res.optimum) == pytest.approx((1.0, 0.3, 0.1)) and res.mover_share == pytest.approx(0.4)
    assert res.objective == pytest.approx(0.0, abs=1e-12)
    table = res.table
    assert list(table.columns) == W.CALIBRATION_COLUMNS and (table["stage"] == res.stage).all()
    assert table["mover_share"].tolist() == pytest.approx([i / 10 for i in range(11)])   # one row per share
    assert set(res.ranges) == {"short", "middle", "long", "mover_share"}
    for name, value in (("short", 1.0), ("middle", 0.3), ("long", 0.1), ("mover_share", 0.4)):
        low, high = res.ranges[name]
        assert low <= value <= high and high - low <= 0.2
    assert res.ranges["short"][1] == 1.0
    assert not res.decided and W.config_snippet(res) is None    # the short weight still sits at the bound 1


def test_ties_are_logged_and_stage_two_ranges_span_every_tied_triple(toy_build, caplog):
    # A build that reads only the short weight: on the 0.5 grid the 3 triples with short 0.5 tie at the optimum
    # (the first in grid order is taken, the count 3 is logged); (0, 0, 0) is undefined. With a noise floor of
    # 0.9 every defined set is indifferent, so stage two's weight ranges must span all of them, not only the
    # best triple per mover share that its table shows.
    def short_only_build(weights):
        return toy_build(T.CommuterWeights(weights.short, weights.short / 2, weights.short / 4))
    ref = reference_from(toy_build, T.CommuterWeights(0.5, 0.25, 0.125), noise_floor=0.9, mover_share=0.5)
    with caplog.at_level("INFO", logger=W.__name__):
        one = W.calibrate(short_only_build, ref, SITE_KREISE, MIX, grid_step=0.5)
    assert tuple(one.optimum) == (0.5, 0.0, 0.0)
    count = re.compile(r"(?<![\d./])3(?![\d./])")             # the tie count as a token; no other line has it
    assert any(count.search(record.getMessage()) for record in caplog.records if record.name == W.__name__)
    two = W.calibrate_movers(short_only_build, ref, SITE_KREISE, MIX, MOVER_SITE_KREISE, BS, grid_step=0.5,
                             mover_step=0.5)
    assert two.mover_share == 0.5 and len(two.table) == 3
    assert {name: two.ranges[name] for name in ("short", "middle", "long")} == {
        "short": (0.5, 1.0), "middle": (0.0, 1.0), "long": (0.0, 1.0)}


@pytest.mark.parametrize("mover_share", [0.0, 1.0])
def test_mover_share_at_a_bound_is_undecided(toy_build, mover_share):
    ref = reference_from(toy_build, T.CommuterWeights(0.75, 0.5, 0.25), noise_floor=0.001, mover_share=mover_share)
    res = W.calibrate_movers(toy_build, ref, SITE_KREISE, MIX, MOVER_SITE_KREISE, BS, grid_step=0.25,
                             mover_step=0.25)
    assert tuple(res.optimum) == pytest.approx((0.75, 0.5, 0.25)) and res.mover_share == mover_share
    assert not res.decided and W.config_snippet(res) is None
    assert [reason for reason in res.reasons if "mover_share" in reason]


def test_stage_two_objective_equals_apply_movers_implied_shares_and_fit_distance(toy_build):
    # Stage two evaluates the mover shares by the linear structure of the movers step; this must equal the
    # direct route (apply_movers, implied_shares, fit_distance) at every share; stage one's array objective
    # must equal it too. 0.25 lies off the grid, so the objectives are not 0. The reference also puts mass on
    # cells the model cannot reach (no-site own_kreis, other_region_kreis) and weights the groups unequally,
    # with unequal noise floors.
    ref = reference_from(toy_build, T.CommuterWeights(0.6, 0.3, 0.1), noise_floor=0.001, mover_share=0.25)
    no_site = ref["home_group"] == SR.HOME_GROUP_NO_SITE
    ref.loc[no_site, "share"] *= 0.8
    ref.loc[no_site & (ref["destination_group"] == SR.DEST_OWN), "share"] = 0.15
    ref.loc[no_site & (ref["destination_group"] == SR.DEST_OTHER_REGION), "share"] = 0.05
    ref["n_effective"] = ref["home_group"].map({SR.HOME_GROUP_BRAUNSCHWEIG: 120.0, SR.HOME_GROUP_SITE: 30.0,
                                                SR.HOME_GROUP_NO_SITE: 60.0, SR.HOME_GROUP_ALL: 1.0})
    ref["noise_floor_tvd_95"] = ref["home_group"].map({SR.HOME_GROUP_BRAUNSCHWEIG: 0.01, SR.HOME_GROUP_SITE: 0.05,
                                                       SR.HOME_GROUP_NO_SITE: 0.03, SR.HOME_GROUP_ALL: 0.5})
    res = W.calibrate_movers(toy_build, ref, SITE_KREISE, MIX, MOVER_SITE_KREISE, BS, grid_step=0.1,
                             mover_step=0.1)
    share = res.table["mover_share"].to_numpy(dtype=float)
    rows = res.table[np.isclose(share, 0.0) | np.isclose(share, 0.3) | np.isclose(share, 1.0)]
    assert len(rows) == 3
    stage_one = W.calibrate(toy_build, ref, SITE_KREISE, MIX, grid_step=0.1).table
    picked = [(0.2, 0.1, 0.1), (0.6, 0.3, 0.1), (1.0, 0.9, 0.4)]
    rows = pd.concat([rows, stage_one[[triple in picked for triple in
                                       zip(stage_one["short"], stage_one["middle"], stage_one["long"])]]])
    assert len(rows) == 6
    assert res.noise_floor == pytest.approx((120 * 0.01 + 30 * 0.05 + 60 * 0.03) / 210)
    columns = {SR.HOME_GROUP_BRAUNSCHWEIG: "tvd_braunschweig", SR.HOME_GROUP_SITE: "tvd_other_site_kreis",
               SR.HOME_GROUP_NO_SITE: "tvd_no_site_kreis"}
    for row in rows.itertuples(index=False):
        fit = _slow_fit(toy_build, T.CommuterWeights(row.short, row.middle, row.long), row.mover_share, ref)
        assert row.objective_tvd > 0.0
        assert row.objective_tvd == pytest.approx(fit.objective, abs=1e-9)
        for group, value in fit.by_group.set_index("home_group")["total_variation_distance"].items():
            assert getattr(row, columns[group]) == pytest.approx(value, abs=1e-9)


def test_fit_table_lists_every_reference_cell_with_the_implied_share(toy_build, bound_reference, bound_stage_one,
                                                                     bound_stage_two):
    ref, stage_one, stage_two = bound_reference, bound_stage_one, bound_stage_two
    cells = ref[ref["home_group"] != SR.HOME_GROUP_ALL].reset_index(drop=True)

    one = W.fit_table(stage_one, toy_build, ref, SITE_KREISE, MIX, MOVER_SITE_KREISE, BS)
    assert list(one.columns) == W.FIT_COLUMNS and (one["stage"] == "weights").all()
    assert list(zip(one["home_group"], one["destination_group"])) == \
        list(zip(cells["home_group"], cells["destination_group"]))
    assert one["srv_share"].tolist() == pytest.approx(cells["share"].tolist())
    assert one["srv_n_unweighted"].tolist() == cells["n_unweighted"].tolist()
    assert one["srv_se_share"].tolist() == pytest.approx(cells["se_share"].tolist())
    # Stage one sits at the triple only (mover share 0): the implied shares are those of the optimum's own T.
    implied = W.implied_shares(toy_build(stage_one.optimum), SITE_KREISE, MIX)
    assert one["implied_share"].tolist() == pytest.approx(implied["implied_share"].tolist(), abs=1e-12)
    assert one["difference_pp"].tolist() == pytest.approx(
        (100.0 * (one["implied_share"] - one["srv_share"])).tolist())
    assert one["difference_pp"].abs().max() > 0.0
    unreachable = one[~one["reachable"]]
    assert ((unreachable["home_group"] == SR.HOME_GROUP_NO_SITE)
            & (unreachable["destination_group"] == SR.DEST_OWN)).any()

    # Stage two applies the optimum's mover share: the toy reference is reproduced cell by cell.
    two = W.fit_table(stage_two, toy_build, ref, SITE_KREISE, MIX, MOVER_SITE_KREISE, BS)
    assert (two["stage"] == "weights_and_movers").all()
    assert two["implied_share"].tolist() == pytest.approx(two["srv_share"].tolist(), abs=1e-9)
