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
