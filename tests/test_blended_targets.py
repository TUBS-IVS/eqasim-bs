"""Unit tests for the per-Kreis target blending decision rules (synthetic data)."""
import pandas as pd
import pytest

from braunschweig.popsim.blended_targets import BlendConfig, blend_kreis_target

CATS = ["a", "b", "c"]


def mid_frame():
    return pd.DataFrame([
        {"ars5": "03101", "a": 0.2, "b": 0.5, "c": 0.3, "n_unweighted": 1000},
        {"ars5": "03103", "a": 0.4, "b": 0.4, "c": 0.2, "n_unweighted": 500},
        {"ars5": "03102", "a": 0.1, "b": 0.3, "c": 0.6, "n_unweighted": 800},
        {"ars5": "Gesamt", "a": 0.25, "b": 0.45, "c": 0.30, "n_unweighted": 3000},
    ])


def srv_frame(a2=0.30):
    # 03101 agrees with MiD within 2pp; 03102 disagrees by 20pp on 'a'.
    return pd.DataFrame([
        {"code": "03101", "a": 0.22, "b": 0.49, "c": 0.29, "n_unweighted": 1000},
        {"code": "03102", "a": a2, "b": 0.30, "c": 1.0 - 0.30 - a2, "n_unweighted": 800},
    ])


def test_wolfsburg_and_gesamt_are_mid():
    out = blend_kreis_target(mid_frame(), srv_frame(), CATS).set_index("ars5")
    assert out.loc["03103", "source"] == "mid"
    assert out.loc["Gesamt", "source"] == "mid"
    assert out.loc["03103", "a"] == pytest.approx(0.4)


def test_agreement_precision_blend():
    out = blend_kreis_target(mid_frame(), srv_frame(), CATS).set_index("ars5")
    assert out.loc["03101", "source"] == "blend"
    # equal n -> midpoint
    assert out.loc["03101", "a"] == pytest.approx(0.21, abs=1e-6)
    assert out.loc["03101", "n_effective"] == 2000


def test_disagreement_without_arbiter_shrinks_mid_toward_gesamt():
    out = blend_kreis_target(mid_frame(), srv_frame(), CATS,
                             config=BlendConfig(disagreement_shrink_lambda=0.3)
                             ).set_index("ars5")
    assert out.loc["03102", "source"] == "mid_shrunk"
    # 0.7*0.1 + 0.3*0.25 = 0.145
    assert out.loc["03102", "a"] == pytest.approx(0.145, abs=1e-6)


def test_disagreement_with_arbiter_picks_rank_closer_source():
    # Three covered Kreise; the surveys ORDER them differently, the register
    # arbiter decides. MiD ranks 03102 richest on 'a' (0.35 = rank 1), SrV
    # ranks it poorest (0.05 = rank 3); the arbiter scores it lowest (rank 3)
    # -> SrV's rank matches (distance 0 vs MiD's 2) -> srv_arbitrated.
    mid = pd.DataFrame([
        {"ars5": "03101", "a": 0.20, "b": 0.50, "c": 0.30, "n_unweighted": 1000},
        {"ars5": "03151", "a": 0.30, "b": 0.40, "c": 0.30, "n_unweighted": 900},
        {"ars5": "03102", "a": 0.35, "b": 0.35, "c": 0.30, "n_unweighted": 800},
        {"ars5": "Gesamt", "a": 0.25, "b": 0.45, "c": 0.30, "n_unweighted": 3000},
    ])
    srv = pd.DataFrame([
        {"code": "03101", "a": 0.22, "b": 0.49, "c": 0.29, "n_unweighted": 1000},
        {"code": "03151", "a": 0.31, "b": 0.39, "c": 0.30, "n_unweighted": 900},
        {"code": "03102", "a": 0.05, "b": 0.30, "c": 0.65, "n_unweighted": 800},
    ])
    arbiter = pd.Series({"03101": 50.0, "03151": 100.0, "03102": 10.0})
    out = blend_kreis_target(mid, srv, CATS, arbiter=arbiter,
                             rank_score_columns=["a"]).set_index("ars5")
    assert out.loc["03102", "source"] == "srv_arbitrated"
    assert out.loc["03102", "a"] == pytest.approx(0.05, abs=1e-6)
    # the two agreeing Kreise stay blends
    assert out.loc["03101", "source"] == "blend"
    assert out.loc["03151", "source"] == "blend"


def test_rows_renormalized():
    out = blend_kreis_target(mid_frame(), srv_frame(), CATS)
    assert ((out[CATS].sum(axis=1) - 1.0).abs() < 1e-9).all()


def test_mid_arbitrated_when_mid_rank_matches_register():
    # Mirror of test_disagreement_with_arbiter_picks_rank_closer_source: here
    # MiD's ranking on 'a' matches the register arbiter exactly, while SrV's
    # does not, for the Kreis (03102) whose disagreement exceeds tolerance.
    #
    # MiD 'a': 03101=0.20, 03151=0.35, 03102=0.05
    #   -> ranks (1=highest): 03151=1, 03101=2, 03102=3
    # SrV 'a':  03101=0.22, 03151=0.36, 03102=0.55
    #   -> ranks (1=highest): 03102=1, 03151=2, 03101=3
    # arbiter:  03101=50,    03151=100,  03102=10
    #   -> ranks (1=highest): 03151=1, 03101=2, 03102=3
    # For 03102: rank_dist_mid = |3 - 3| = 0; rank_dist_srv = |1 - 3| = 2.
    # 0 < 2 -> MiD's rank is closer to the register -> "mid_arbitrated".
    # For 03101/03151, MiD and SrV agree within tolerance_pp (<=5pp) -> "blend".
    mid = pd.DataFrame([
        {"ars5": "03101", "a": 0.20, "b": 0.50, "c": 0.30, "n_unweighted": 1000},
        {"ars5": "03151", "a": 0.35, "b": 0.35, "c": 0.30, "n_unweighted": 900},
        {"ars5": "03102", "a": 0.05, "b": 0.65, "c": 0.30, "n_unweighted": 800},
        {"ars5": "Gesamt", "a": 0.25, "b": 0.45, "c": 0.30, "n_unweighted": 3000},
    ])
    srv = pd.DataFrame([
        {"code": "03101", "a": 0.22, "b": 0.49, "c": 0.29, "n_unweighted": 1000},
        {"code": "03151", "a": 0.36, "b": 0.34, "c": 0.30, "n_unweighted": 900},
        {"code": "03102", "a": 0.55, "b": 0.20, "c": 0.25, "n_unweighted": 800},
    ])
    arbiter = pd.Series({"03101": 50.0, "03151": 100.0, "03102": 10.0})
    out = blend_kreis_target(mid, srv, CATS, arbiter=arbiter,
                             rank_score_columns=["a"]).set_index("ars5")
    assert out.loc["03102", "source"] == "mid_arbitrated"
    assert out.loc["03102", "a"] == pytest.approx(0.05, abs=1e-6)
    assert out.loc["03102", "b"] == pytest.approx(0.65, abs=1e-6)
    assert out.loc["03102", "c"] == pytest.approx(0.30, abs=1e-6)
    # the two agreeing Kreise stay blends
    assert out.loc["03101", "source"] == "blend"
    assert out.loc["03151", "source"] == "blend"


def test_arbiter_rank_tie_falls_back_to_blend():
    # The disagreeing Kreis (03102) has the SAME rank on 'a' in MiD, SrV and
    # the arbiter, so both rank distances are 0 and the tie falls back to blend.
    #
    # MiD 'a': 03101=0.10, 03151=0.20, 03102=0.50
    #   -> ranks (1=highest): 03102=1, 03151=2, 03101=3
    # SrV 'a':  03101=0.05, 03151=0.15, 03102=0.90
    #   -> ranks (1=highest): 03102=1, 03151=2, 03101=3
    # arbiter:  03101=10,    03151=50,   03102=100
    #   -> ranks (1=highest): 03102=1, 03151=2, 03101=3
    # For 03102: rank_dist_mid = |1 - 1| = 0; rank_dist_srv = |1 - 1| = 0.
    # d_mid == d_srv -> fall back to apply_blend() -> "blend", even though
    # |0.50 - 0.90| = 40pp is far beyond tolerance_pp.
    mid = pd.DataFrame([
        {"ars5": "03101", "a": 0.10, "b": 0.50, "c": 0.40, "n_unweighted": 1000},
        {"ars5": "03151", "a": 0.20, "b": 0.40, "c": 0.40, "n_unweighted": 900},
        {"ars5": "03102", "a": 0.50, "b": 0.30, "c": 0.20, "n_unweighted": 800},
        {"ars5": "Gesamt", "a": 0.25, "b": 0.45, "c": 0.30, "n_unweighted": 3000},
    ])
    srv = pd.DataFrame([
        {"code": "03101", "a": 0.05, "b": 0.55, "c": 0.40, "n_unweighted": 1000},
        {"code": "03151", "a": 0.15, "b": 0.45, "c": 0.40, "n_unweighted": 900},
        {"code": "03102", "a": 0.90, "b": 0.05, "c": 0.05, "n_unweighted": 800},
    ])
    arbiter = pd.Series({"03101": 10.0, "03151": 50.0, "03102": 100.0})
    out = blend_kreis_target(mid, srv, CATS, arbiter=arbiter,
                             rank_score_columns=["a"]).set_index("ars5")
    assert out.loc["03102", "source"] == "blend"
    # weighted average of MiD (n=800) and SrV (n=800): equal precision -> midpoint
    assert out.loc["03102", "a"] == pytest.approx(0.70, abs=1e-6)


def test_arbiter_without_rank_score_columns_raises():
    with pytest.raises(ValueError):
        blend_kreis_target(mid_frame(), srv_frame(), CATS,
                           arbiter=pd.Series({"03101": 1.0}))


def test_renormalization_actually_fires():
    # 03103 has no SrV coverage (Wolfsburg-style) and its MiD row only sums
    # to 0.90 (0.18 + 0.45 + 0.27), so it takes the "mid" (unchanged) branch
    # before the final _renorm pass rescales it back up to 1.0.
    mid = pd.DataFrame([
        {"ars5": "03103", "a": 0.18, "b": 0.45, "c": 0.27, "n_unweighted": 500},
        {"ars5": "Gesamt", "a": 0.25, "b": 0.45, "c": 0.30, "n_unweighted": 3000},
    ])
    out = blend_kreis_target(mid, srv_frame(), CATS).set_index("ars5")
    row = out.loc["03103"]
    assert row["source"] == "mid"
    assert row[CATS].sum() == pytest.approx(1.0, abs=1e-9)
    # relative proportions (a:b:c) are preserved by the uniform rescale.
    assert row["a"] == pytest.approx(0.18 / 0.90, abs=1e-9)
    assert row["b"] == pytest.approx(0.45 / 0.90, abs=1e-9)
    assert row["c"] == pytest.approx(0.27 / 0.90, abs=1e-9)
    assert row["a"] / row["b"] == pytest.approx(0.18 / 0.45, abs=1e-9)


def test_non_positive_row_sum_raises():
    # 03103 has no SrV coverage and an all-zero MiD row -> the row sum is 0,
    # which _renorm must reject rather than divide by.
    mid = pd.DataFrame([
        {"ars5": "03103", "a": 0.0, "b": 0.0, "c": 0.0, "n_unweighted": 500},
        {"ars5": "Gesamt", "a": 0.25, "b": 0.45, "c": 0.30, "n_unweighted": 3000},
    ])
    with pytest.raises(ValueError):
        blend_kreis_target(mid, srv_frame(), CATS)
