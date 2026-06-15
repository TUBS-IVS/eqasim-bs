import numpy as np
import pytest

from braunschweig.popsim import income_kreis_control as kic
from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES


def test_bracket_expected_eur_shape_and_values():
    e_b = kic.bracket_expected_eur()
    assert e_b.shape == (len(INCOME_BRACKET_CATEGORIES),)
    # under_500 floored at INCOME_MIN_EUR=100 -> (100+500)/2 = 300
    assert e_b[0] == pytest.approx(300.0)
    # 2000_3000 -> (2000+3000)/2 = 2500 (look up by name, robust to bracket reorder)
    assert e_b[INCOME_BRACKET_CATEGORIES.index("2000_3000")] == pytest.approx(2500.0)
    # open top -> 7000*(1+0.4) = 9800
    assert e_b[-1] == pytest.approx(9800.0)
    # strictly increasing
    assert np.all(np.diff(e_b) > 0)


def test_build_class_midpoint_eur_matches_attributes():
    table = kic.build_class_midpoint_eur()
    assert table["under_500"] == pytest.approx(250.0)
    assert table["over_7000"] == pytest.approx(8000.0)
    assert table["5000_5600"] == pytest.approx(5300.0)


def test_income_class_from_eur_is_monotone():
    table = kic.build_class_midpoint_eur()
    labels = kic.income_class_from_eur(np.array([100.0, 5400.0, 9000.0]), table)
    assert labels[0] == "under_500"
    assert labels[1] == "5000_5600"
    assert labels[2] == "over_7000"


def test_build_kreis_income_targets_mean_one_and_hhsize_correction():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [0.882, 1.091]})
    # UNEQUAL hh_count so the normalization is genuinely household-count-WEIGHTED
    # (equal weights would let an unweighted mean pass and hide the weighting bug).
    stats = pd.DataFrame({
        "ars5": ["03102", "03103"],
        "hh_count": [100.0, 300.0],
        "mean_size": [1.8, 2.1],
    })
    rf = kic.build_kreis_income_targets(inkar, stats, ["03102", "03103"], hhsize_correct=True)
    # household-count-WEIGHTED mean of rf == 1.0 (by construction)
    weighted = (rf["03102"] * 100.0 + rf["03103"] * 300.0) / 400.0
    assert weighted == pytest.approx(1.0)
    # the UNweighted mean is NOT 1.0 -> proves the weighting is actually applied
    assert (rf["03102"] + rf["03103"]) / 2 != pytest.approx(1.0)
    # Wolfsburg richer per-EW AND larger HH -> rf > Salzgitter
    assert rf["03103"] > rf["03102"]


def test_build_kreis_income_targets_degenerate_scale_falls_back_to_one():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [-1.0, -1.0]})
    stats = pd.DataFrame({"ars5": ["03102", "03103"], "hh_count": [100.0, 100.0],
                          "mean_size": [1.8, 2.1]})
    rf = kic.build_kreis_income_targets(inkar, stats, ["03102", "03103"])
    assert rf["03102"] == pytest.approx(1.0)
    assert rf["03103"] == pytest.approx(1.0)


def test_build_kreis_income_targets_single_kreis_is_noop():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03101"], "scale": [1.003]})
    stats = pd.DataFrame({"ars5": ["03101"], "hh_count": [100.0], "mean_size": [2.0]})
    rf = kic.build_kreis_income_targets(inkar, stats, ["03101"])
    assert rf["03101"] == pytest.approx(1.0)


def test_build_kreis_income_targets_hhsize_off_uses_per_ew():
    import pandas as pd
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [0.882, 1.091]})
    stats = pd.DataFrame({"ars5": ["03102", "03103"], "hh_count": [100.0, 100.0],
                          "mean_size": [1.8, 2.1]})
    rf = kic.build_kreis_income_targets(inkar, stats, ["03102", "03103"], hhsize_correct=False)
    # per-EW only: rf proportional to scale, mean-1
    assert rf["03103"] / rf["03102"] == pytest.approx(1.091 / 0.882)


def _toy_income_tables():
    # 10-bracket toy NDS-only tables (raumtyp tables empty -> NDS base used).
    import pandas as pd
    from braunschweig.data.mid.income_by_size import INCOME_BRACKET_CATEGORIES
    brackets = list(INCOME_BRACKET_CATEGORIES)
    def size_rows(size, weights):
        return [{"region": "niedersachsen", "hh_size": size, "income_bracket": b,
                 "share_pct": w, "base_weighted": 100.0} for b, w in zip(brackets, weights)]
    def status_rows(status, weights):
        return [{"region": "niedersachsen", "status": status, "income_bracket": b,
                 "share_pct": w, "base_weighted": 100.0} for b, w in zip(brackets, weights)]
    low = [40, 25, 15, 8, 6, 3, 1, 1, 1, 0]
    high = [2, 3, 5, 8, 15, 22, 20, 12, 7, 6]
    size_bl = pd.DataFrame(size_rows("1", low) + size_rows("2", high))
    status_bl = pd.DataFrame(status_rows("very_low", low) + status_rows("very_high", high))
    empty_size = pd.DataFrame(columns=["region", "hh_size", "income_bracket", "share_pct", "base_weighted"])
    empty_status = pd.DataFrame(columns=["region", "status", "income_bracket", "share_pct", "base_weighted"])
    return {"size_bl": size_bl, "size_rt": empty_size,
            "status_bl": status_bl, "status_rt": empty_status}


def test_household_base_pmf_matrix_combined_rows_sum_to_one():
    import pandas as pd
    tables = _toy_income_tables()
    hh = pd.DataFrame({
        "household_id": [1, 2],
        "household_size": ["1", "2"],
        "economic_status": ["very_low", "very_high"],
        "RegioStaR7": [73, 73],
    })
    mat, diag = kic.household_base_pmf_matrix(hh, tables, method="combined")
    assert mat.shape == (2, 10)
    assert np.allclose(mat.sum(axis=1), 1.0)
    # very_low+size1 household leans low; very_high+size2 leans high
    assert mat[0, :3].sum() > mat[0, 7:].sum()
    assert mat[1, 7:].sum() > mat[1, :3].sum()
    assert diag["fallback_rate"] == 0.0


def test_household_base_pmf_matrix_size_only_method():
    import pandas as pd
    tables = _toy_income_tables()
    hh = pd.DataFrame({
        "household_id": [1],
        "household_size": ["1"],
        "economic_status": ["very_high"],  # ignored in size_only
        "RegioStaR7": [73],
    })
    mat, diag = kic.household_base_pmf_matrix(hh, tables, method="size_only")
    assert np.allclose(mat.sum(axis=1), 1.0)
    # size_only uses the size-1 (low) distribution regardless of status
    assert mat[0, :3].sum() > mat[0, 7:].sum()


def test_household_base_pmf_matrix_missing_size_falls_back_uniform():
    import pandas as pd
    tables = _toy_income_tables()
    hh = pd.DataFrame({
        "household_id": [1],
        "household_size": ["4"],  # absent from toy size table -> uniform fallback
        "economic_status": ["very_low"],
        "RegioStaR7": [73],
    })
    mat, diag = kic.household_base_pmf_matrix(hh, tables, method="combined")
    assert np.allclose(mat.sum(axis=1), 1.0)
    assert np.allclose(mat[0], 1.0 / 10)
    assert diag["fallback_rate"] == 1.0


def test_solve_kreis_lambda_recovers_target_mean():
    e_b = kic.bracket_expected_eur()
    rng = np.random.RandomState(0)
    pmf = rng.dirichlet(np.ones(10), size=200)  # 200 households
    base = float((kic.tilt_pmf_rows(pmf, e_b, 0.0) * e_b[None, :]).sum(axis=1).mean())
    target = base * 1.08  # ask for +8%
    lam, clamped = kic.solve_kreis_lambda(pmf, e_b, target)
    realized = float((kic.tilt_pmf_rows(pmf, e_b, lam) * e_b[None, :]).sum(axis=1).mean())
    assert not clamped
    assert realized == pytest.approx(target, rel=1e-4)


def test_solve_kreis_lambda_lower_target_gives_negative_lambda():
    e_b = kic.bracket_expected_eur()
    rng = np.random.RandomState(1)
    pmf = rng.dirichlet(np.ones(10), size=200)
    base = float((kic.tilt_pmf_rows(pmf, e_b, 0.0) * e_b[None, :]).sum(axis=1).mean())
    lam, _ = kic.solve_kreis_lambda(pmf, e_b, base * 0.9)
    assert lam < 0


def test_solve_kreis_lambda_unreachable_target_clamps():
    e_b = kic.bracket_expected_eur()
    pmf = np.tile(np.eye(10)[0], (5, 1))  # all mass on lowest bracket
    lam, clamped = kic.solve_kreis_lambda(pmf, e_b, e_b.max() * 2)  # impossible
    assert clamped


def test_tilt_pmf_rows_preserves_rows_sum_to_one():
    e_b = kic.bracket_expected_eur()
    pmf = np.random.RandomState(2).dirichlet(np.ones(10), size=10)
    tilted = kic.tilt_pmf_rows(pmf, e_b, 0.0005)
    assert np.allclose(tilted.sum(axis=1), 1.0)


def test_tilt_pmf_rows_lambda_zero_is_identity():
    e_b = kic.bracket_expected_eur()
    pmf = np.random.RandomState(3).dirichlet(np.ones(10), size=10)
    tilted = kic.tilt_pmf_rows(pmf, e_b, 0.0)
    assert np.allclose(tilted, pmf)


def test_draw_brackets_inverse_cdf_deterministic():
    pmf = np.tile(np.eye(10)[3], (4, 1))  # all mass -> bracket index 3
    idx = kic.draw_brackets(pmf, np.array([0.1, 0.5, 0.9, 0.999]))
    assert (idx == 3).all()


def test_draw_income_within_bracket_bounds():
    rng = np.random.RandomState(0)
    # closed bracket 2000_3000 (index 4) and open top (index 9) and under_500 (index 0)
    idx = np.array([4, 9, 0])
    eur = kic.draw_income_within_bracket(idx, rng)
    assert 2000.0 <= eur[0] < 3000.0
    assert eur[1] >= 7000.0 and eur[1] <= kic.INCOME_OPEN_TOP_MAX_EUR
    assert eur[2] >= kic.INCOME_MIN_EUR  # under_500 floored at 100


def test_draw_brackets_respects_distribution():
    rng = np.random.RandomState(1)
    pmf = np.tile(np.array([0.5, 0, 0, 0, 0, 0, 0, 0, 0, 0.5]), (2000, 1))
    idx = kic.draw_brackets(pmf, rng.random_sample(2000))
    frac_low = (idx == 0).mean()
    assert 0.45 < frac_low < 0.55  # ~50/50 between bracket 0 and 9
    assert set(np.unique(idx)).issubset({0, 9})


def _persons_two_kreise(n_per=400):
    import pandas as pd
    rng = np.random.RandomState(7)
    rows = []
    hid = 0
    for kreis, status_mix in [("03102", 0.7), ("03103", 0.3)]:  # SZ poorer status mix
        for _ in range(n_per):
            hid += 1
            status = "very_low" if rng.random() < status_mix else "very_high"
            size = "1" if status == "very_low" else "2"
            rows.append({"household_id": hid, "departement_id": kreis,
                         "household_size": size, "economic_status": status,
                         "RegioStaR7": 73, "household_income_eur": 2500.0,
                         "household_income": "2000_2600", "high_income": False})
    return pd.DataFrame(rows)


def test_apply_off_path_is_byte_identical():
    import pandas as pd
    df = _persons_two_kreise()
    out, diag = kic.apply_kreis_income_control(
        df, inkar_df=pd.DataFrame(), kreis_stats_df=pd.DataFrame(),
        income_tables={}, enabled=False, random_seed=1234)
    pd.testing.assert_frame_equal(out, df, check_exact=True)
    assert diag == {}


def test_apply_on_path_reshapes_and_hits_targets():
    import pandas as pd
    df = _persons_two_kreise()
    tables = _toy_income_tables()
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [0.882, 1.091]})
    stats = pd.DataFrame({"ars5": ["03102", "03103"], "hh_count": [400.0, 400.0],
                          "mean_size": [1.8, 2.1]})
    out, diag = kic.apply_kreis_income_control(
        df, inkar_df=inkar, kreis_stats_df=stats, income_tables=tables,
        enabled=True, method="combined", random_seed=1234)
    sz = out.loc[out.departement_id == "03102", "household_income_eur"].mean()
    wob = out.loc[out.departement_id == "03103", "household_income_eur"].mean()
    # Salzgitter mean below Wolfsburg (between-Kreis relativity imposed)
    assert sz < wob
    # economic_status untouched
    pd.testing.assert_series_equal(out["economic_status"], df["economic_status"])
    # label + high_income re-derived from EUR (consistent with the value)
    assert (out["high_income"] == (out["household_income_eur"] >= 5000.0)).all()
    # per-Kreis realized mean tracks the target factor
    assert diag["kreis_realized_mean"]["03102"] < diag["kreis_realized_mean"]["03103"]
    assert "03102" in diag["kreis_lambda"]


def test_apply_on_path_income_is_per_household_consistent():
    import pandas as pd
    # All persons in a household must share the same drawn household_income_eur.
    rows = [
        {"household_id": 1, "departement_id": "03102", "household_size": "2",
         "economic_status": "very_low", "RegioStaR7": 73,
         "household_income_eur": 2500.0, "household_income": "2000_2600", "high_income": False},
        {"household_id": 1, "departement_id": "03102", "household_size": "2",
         "economic_status": "very_low", "RegioStaR7": 73,
         "household_income_eur": 2500.0, "household_income": "2000_2600", "high_income": False},
        {"household_id": 2, "departement_id": "03103", "household_size": "1",
         "economic_status": "very_high", "RegioStaR7": 73,
         "household_income_eur": 2500.0, "household_income": "2000_2600", "high_income": False},
    ]
    df = pd.DataFrame(rows)
    tables = _toy_income_tables()
    inkar = pd.DataFrame({"ars5": ["03102", "03103"], "scale": [0.882, 1.091]})
    stats = pd.DataFrame({"ars5": ["03102", "03103"], "hh_count": [1.0, 1.0],
                          "mean_size": [2.0, 1.0]})
    out, _ = kic.apply_kreis_income_control(
        df, inkar_df=inkar, kreis_stats_df=stats, income_tables=tables,
        enabled=True, random_seed=1234)
    hh1 = out.loc[out.household_id == 1, "household_income_eur"].unique()
    assert len(hh1) == 1  # both persons in HH 1 share one income
