"""Unit tests for the 1km ownership-grid control helpers (issue #240)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import ownership_grid as og


def test_load_ownership_conditionals_validates_and_indexes(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 0.25, "cars_1": 0.25, "cars_2": 0.25,
             "cars_3plus": 0.25, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    brows = [{"rs7": r, "ht": h, "bikes_0": 0.2, "bikes_1": 0.2, "bikes_2": 0.2,
              "bikes_3": 0.2, "bikes_4plus": 0.2, "n_unweighted": 10}
             for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(brows).to_csv(mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    cars, bikes = og.load_ownership_conditionals(str(tmp_path))
    assert cars.loc[(71, 1), "cars_0"] == pytest.approx(0.25)
    assert bikes.loc[(77, 4), "bikes_4plus"] == pytest.approx(0.2)


def test_load_ownership_conditionals_rejects_bad_row_sum(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 0.5, "cars_1": 0.5, "cars_2": 0.5,
             "cars_3plus": 0.5, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    pd.DataFrame(rows).rename(columns={"cars_0": "bikes_0", "cars_1": "bikes_1", "cars_2": "bikes_2",
                                       "cars_3plus": "bikes_3"}).assign(bikes_4plus=0.0).to_csv(
        mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    with pytest.raises(ValueError, match="sum"):
        og.load_ownership_conditionals(str(tmp_path))


def test_load_ownership_conditionals_rejects_incomplete_grid(tmp_path):
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 1.0, "cars_1": 0.0, "cars_2": 0.0,
             "cars_3plus": 0.0, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)][:-1]  # drop (77, 4)
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    brows = [{"rs7": r, "ht": h, "bikes_0": 1.0, "bikes_1": 0.0, "bikes_2": 0.0,
              "bikes_3": 0.0, "bikes_4plus": 0.0, "n_unweighted": 10}
             for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(brows).to_csv(mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    with pytest.raises(ValueError, match="incomplete|missing"):
        og.load_ownership_conditionals(str(tmp_path))


def _uniform_conditional(share_columns):
    rows = []
    for r in range(71, 78):
        for h in (1, 2, 3, 4):
            row = {"rs7": r, "ht": h, "n_unweighted": 100 if h == 1 else 50}
            # ht=1 gets all mass on category 0; other ht all mass on the last category.
            for i, c in enumerate(share_columns):
                row[c] = 1.0 if ((h == 1 and i == 0) or (h != 1 and i == len(share_columns) - 1)) else 0.0
            rows.append(row)
    return pd.DataFrame(rows).set_index(["rs7", "ht"]).sort_index()


def test_prior_mixes_by_dwelling_composition():
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    rs7 = np.array([71])
    dwellings = np.array([[3.0, 1.0, 0.0, 0.0]])  # 75% ht=1, 25% ht=2
    prior = og.per_cell_ownership_priors(rs7, dwellings, cond, og._CARS_SHARE_COLUMNS, "cars")
    assert prior[0, 0] == pytest.approx(0.75)
    assert prior[0, -1] == pytest.approx(0.25)


def test_prior_falls_back_to_n_weighted_rs7_marginal_and_logs(caplog):
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    rs7 = np.array([71, 71])
    dwellings = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]])  # second cell: no info
    with caplog.at_level("INFO"):
        prior = og.per_cell_ownership_priors(rs7, dwellings, cond, og._CARS_SHARE_COLUMNS, "cars")
    # n-weighted marginal: ht1 n=100 on cat0, ht2..4 n=50 each on last cat -> 100/250 vs 150/250.
    assert prior[1, 0] == pytest.approx(0.4)
    assert prior[1, -1] == pytest.approx(0.6)
    assert "fallback" in caplog.text.lower()
    assert "1/2" in caplog.text or "50.0" in caplog.text


def test_prior_all_fallback_raises():
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    with pytest.raises(ValueError, match="100"):
        og.per_cell_ownership_priors(np.array([71]), np.array([[0.0, 0.0, 0.0, 0.0]]),
                                     cond, og._CARS_SHARE_COLUMNS, "cars")


def _shares_frame(share_columns, ars5=("03101", "03102")):
    return pd.DataFrame(
        {c: [1.0 / len(share_columns)] * len(ars5) for c in share_columns},
        index=pd.Index(ars5, name="ars5"))


def test_rake_hits_kreis_margins_and_preserves_row_sums():
    cond_cols = og._CARS_SHARE_COLUMNS
    prior = np.array([[0.7, 0.1, 0.1, 0.1], [0.1, 0.7, 0.1, 0.1],
                      [0.25, 0.25, 0.25, 0.25]])
    hh = np.array([100.0, 300.0, 50.0])
    kreis = np.array(["03101", "03101", "03102"])
    targets = _shares_frame(cond_cols)
    raked = og.rake_ownership_targets(prior, hh, kreis, targets, cond_cols, "cars")
    np.testing.assert_allclose(raked.sum(axis=1), hh, rtol=1e-9)
    np.testing.assert_allclose(raked[:2].sum(axis=0), 400.0 / 4, rtol=1e-8)


def test_rake_zero_prior_category_stays_zero():
    cond_cols = og._CARS_SHARE_COLUMNS
    prior = np.array([[0.5, 0.5, 0.0, 0.0], [0.6, 0.2, 0.2, 0.0]])
    hh = np.array([10.0, 10.0])
    kreis = np.array(["03101", "03101"])
    targets = pd.DataFrame({"cars_0": [0.5], "cars_1": [0.3], "cars_2": [0.2], "cars_3plus": [0.0]},
                           index=pd.Index(["03101"], name="ars5"))
    raked = og.rake_ownership_targets(prior, hh, kreis, targets, cond_cols, "cars")
    assert raked[0, 2] == pytest.approx(0.0)
    assert raked[:, 3].sum() == pytest.approx(0.0)


def test_rake_missing_kreis_target_raises():
    cond_cols = og._CARS_SHARE_COLUMNS
    with pytest.raises(ValueError, match="03102"):
        og.rake_ownership_targets(np.full((1, 4), 0.25), np.array([10.0]), np.array(["03102"]),
                                  _shares_frame(cond_cols, ars5=("03101",)), cond_cols, "cars")


def test_rake_infeasible_margin_raises():
    cond_cols = og._CARS_SHARE_COLUMNS
    # Prior gives category 3plus zero mass everywhere, but the target demands 50% -> cannot converge.
    prior = np.array([[0.5, 0.3, 0.2, 0.0]])
    targets = pd.DataFrame({"cars_0": [0.25], "cars_1": [0.15], "cars_2": [0.10], "cars_3plus": [0.5]},
                           index=pd.Index(["03101"], name="ars5"))
    with pytest.raises(ValueError, match="converge"):
        og.rake_ownership_targets(prior, np.array([100.0]), np.array(["03101"]),
                                  targets, cond_cols, "cars")


def test_prior_raises_on_zero_weight_rs7_stratum():
    # RS7=71 has zero total n_unweighted across all haustyp strata: the n-weighted
    # marginal would need a 0/0 division, which would silently produce a NaN prior
    # (and eventually poison the rake) instead of failing loudly at its origin.
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    cond.loc[71, "n_unweighted"] = 0
    with pytest.raises(ValueError, match="71"):
        og.per_cell_ownership_priors(np.array([71]), np.array([[10.0, 0.0, 0.0, 0.0]]),
                                     cond, og._CARS_SHARE_COLUMNS, "cars")


def test_rake_raises_on_non_finite_margin_error_instead_of_silently_returning():
    # A NaN entry in the prior (e.g. propagated from an upstream zero-weight RS7
    # stratum) drives the margin error to NaN. Under IEEE-754 semantics both
    # `err < tol` and `err >= tol` are False for NaN, so without an explicit
    # finiteness guard the loop would silently exhaust max_iter and fall through to
    # returning NaN-poisoned output -- exactly the failure this test guards against.
    cond_cols = og._CARS_SHARE_COLUMNS
    prior = np.array([[np.nan, 0.5, 0.3, 0.2]])
    targets = pd.DataFrame({"cars_0": [0.25], "cars_1": [0.25], "cars_2": [0.25], "cars_3plus": [0.25]},
                           index=pd.Index(["03101"], name="ars5"))
    with pytest.raises(ValueError, match="non-finite"):
        og.rake_ownership_targets(prior, np.array([100.0]), np.array(["03101"]),
                                  targets, cond_cols, "cars")


def _mini_cells():
    # Two 1km parents in Kreis 03101 (A: Geschoss-only, B: EFH-only) and one in 03102
    # (C: EFH-only). Deliberately carries only ONE of the six haustyp-1 dwelling
    # columns: a partial class is DATA and must be summed over the present columns
    # (an all-or-nothing guard would silently zero the class -- review finding C3).
    # kreis_per_cell mimics mid.resolved_kreis_per_cell: constant per 1km parent.
    hh_col = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    dw1 = "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"
    dw3 = "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"
    cells = pd.DataFrame({
        "ZENSUS100m": ["a1", "a2", "b1", "c1"],
        "ZENSUS1km": ["A", "A", "B", "C"],
        "RegioStaR7": [72.0, 72.0, 77.0, 76.0],
        hh_col: [30.0, 10.0, 20.0, 40.0],
        dw1: [0.0, 0.0, 20.0, 40.0],
        dw3: [30.0, 10.0, 0.0, 0.0],
    })
    kreis_per_cell = pd.Series(["03101", "03101", "03101", "03102"], index=cells.index)
    return cells, kreis_per_cell


def _mini_targets():
    # Feasibility by construction: parent A (40 hh, Geschoss-only -> prior mass on the
    # LAST category), parent B (20 hh, EFH-only -> prior mass on category 0). Kreis
    # 03101 = A + B, so the only feasible split is cat0 = 20/60, last = 40/60; Kreis
    # 03102 = C (EFH-only) forces cat0 = 1. Any target putting mass on a category
    # without prior mass would (correctly) make the rake raise.
    cars_t = pd.DataFrame({"cars_0": [1 / 3, 1.0], "cars_1": [0.0, 0.0],
                           "cars_2": [0.0, 0.0], "cars_3plus": [2 / 3, 0.0]},
                          index=pd.Index(["03101", "03102"], name="ars5"))
    bikes_t = pd.DataFrame({"bikes_0": [1 / 3, 1.0], "bikes_1": [0.0, 0.0], "bikes_2": [0.0, 0.0],
                            "bikes_3": [0.0, 0.0], "bikes_4plus": [2 / 3, 0.0]},
                           index=pd.Index(["03101", "03102"], name="ars5"))
    return cars_t, bikes_t


def test_add_ownership_grid_columns_aggregates_back_to_kreis_targets():
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    bcond = _uniform_conditional(list(og._BIKES_SHARE_COLUMNS))
    cells, kreis_per_cell = _mini_cells()
    cars_t, bikes_t = _mini_targets()
    out = og.add_ownership_grid_columns(cells, cars_t, bikes_t, cond, bcond,
                                        kreis_per_cell=kreis_per_cell)
    for col in og.OWNERSHIP_COLUMNS:
        assert col in out.columns
    hh_col = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    # Per-Kreis aggregation identity: sum(OWN_CARS_x) == share x Kreis household total.
    assert out.loc[kreis_per_cell == "03101", "OWN_CARS_0_agg"].sum() == pytest.approx(20.0)
    assert out.loc[kreis_per_cell == "03101", "OWN_CARS_3plus_agg"].sum() == pytest.approx(40.0)
    assert out.loc[kreis_per_cell == "03102", "OWN_CARS_0_agg"].sum() == pytest.approx(40.0)
    # Back-distribution is household-proportional within the 1km parent A (30 vs 10 hh).
    a = out[out["ZENSUS1km"] == "A"]
    assert a["OWN_CARS_3plus_agg"].iloc[0] == pytest.approx(30.0)
    assert a["OWN_CARS_3plus_agg"].iloc[1] == pytest.approx(10.0)
    # Row identity: the 4 car columns sum to the cell household total.
    np.testing.assert_allclose(
        out[list(og.CARS_COLUMNS)].sum(axis=1).to_numpy(), out[hh_col].to_numpy(), rtol=1e-9)


def test_add_ownership_grid_columns_rejects_mixed_parent_kreis():
    # kreis_per_cell must be the pipeline's parent-atomic resolution; a parent split
    # across Kreise would break the exact aggregation to the KREIS anchor layer.
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    bcond = _uniform_conditional(list(og._BIKES_SHARE_COLUMNS))
    cells, kreis_per_cell = _mini_cells()
    cars_t, bikes_t = _mini_targets()
    mixed = kreis_per_cell.copy()
    mixed.iloc[1] = "03102"  # split parent A across two Kreise
    with pytest.raises(ValueError, match="constant within"):
        og.add_ownership_grid_columns(cells, cars_t, bikes_t, cond, bcond,
                                      kreis_per_cell=mixed)


def test_add_ownership_grid_columns_rejects_divergent_haustyp_mapping(monkeypatch):
    # The dwelling-matrix column order is a contract: per_cell_ownership_priors reads
    # column j as haustyp HAUSTYP_CLASSES[j]. If DWELLING_COLUMNS_BY_HAUSTYP stops
    # describing the same class set, the matrix would silently mis-address the
    # conditional (swapped building types) or drop a class's dwelling mass -- both
    # wrong-but-green -- so the divergence must fail loudly instead.
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    bcond = _uniform_conditional(list(og._BIKES_SHARE_COLUMNS))
    cells, kreis_per_cell = _mini_cells()
    cars_t, bikes_t = _mini_targets()
    divergent = {ht: cols for ht, cols in og.DWELLING_COLUMNS_BY_HAUSTYP.items() if ht != 4}
    divergent[5] = ("NewGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",)
    monkeypatch.setattr(og, "DWELLING_COLUMNS_BY_HAUSTYP", divergent)
    with pytest.raises(ValueError, match="HAUSTYP_CLASSES"):
        og.add_ownership_grid_columns(cells, cars_t, bikes_t, cond, bcond,
                                      kreis_per_cell=kreis_per_cell)


def test_select_load_columns_adds_present_dwelling_columns():
    available = ["GITTER_ID_100m", og.DWELLING_INPUT_COLUMNS[0], og.DWELLING_INPUT_COLUMNS[3]]
    out = og.select_load_columns(["GITTER_ID_100m"], available)
    assert out[0] == "GITTER_ID_100m"
    assert og.DWELLING_INPUT_COLUMNS[0] in out and og.DWELLING_INPUT_COLUMNS[3] in out
    assert len(out) == len(set(out))


def test_stage_injection_requires_active_kreis_entries():
    # The 1km grid controls reuse the KREIS ownership entries' seed columns AND their
    # target2026 anchors, so injecting them while an entry is toggled off would
    # constrain a seed column PopulationSim does not carry. Fail fast instead.
    from braunschweig.popsim import stage
    # The message must name the CONFIG KEYS to flip, not only the entry names.
    with pytest.raises(ValueError, match="number_of_bicycles_kreis_control"):
        stage._inject_ownership_grid_columns(
            context=None, cells=pd.DataFrame(), ownership_grid_on=True,
            active_entry_names=("number_of_cars",), kreise=("03101",))


def test_stage_injection_off_returns_cells_unchanged():
    # OFF must be byte-identical: the same frame object's content, no context touched.
    from braunschweig.popsim import stage
    cells = pd.DataFrame({"ZENSUS100m": ["a", "b"]})
    out = stage._inject_ownership_grid_columns(
        context=None, cells=cells, ownership_grid_on=False,
        active_entry_names=(), kreise=("03101",))
    pd.testing.assert_frame_equal(out, cells)


# --- Review-minor hardening (2026-08-20 triage of the #240 build reviews) ------

def test_load_ownership_conditionals_rejects_duplicate_grid_cells(tmp_path):
    """A duplicated (rs7, ht) row must fail in the LOADER with a clear message.

    Before this guard the duplicate survived validation and surfaced far away as a
    pandas lookup returning a frame instead of a row inside the prior builder.
    """
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 1.0, "cars_1": 0.0, "cars_2": 0.0,
             "cars_3plus": 0.0, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)]
    rows.append(dict(rows[0]))  # duplicate (71, 1)
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    brows = [{"rs7": r, "ht": h, "bikes_0": 1.0, "bikes_1": 0.0, "bikes_2": 0.0,
              "bikes_3": 0.0, "bikes_4plus": 0.0, "n_unweighted": 10}
             for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(brows).to_csv(mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    with pytest.raises(ValueError, match="duplicate"):
        og.load_ownership_conditionals(str(tmp_path))


def test_load_ownership_conditionals_rejects_negative_share(tmp_path):
    """The negative-share branch was implemented but never exercised."""
    mid_dir = tmp_path / "braunschweig" / "mid"
    mid_dir.mkdir(parents=True)
    rows = [{"rs7": r, "ht": h, "cars_0": 1.2, "cars_1": -0.2, "cars_2": 0.0,
             "cars_3plus": 0.0, "n_unweighted": 10}
            for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(rows).to_csv(mid_dir / "mid2023_cars_by_rs7_haustyp.csv", index=False)
    brows = [{"rs7": r, "ht": h, "bikes_0": 1.0, "bikes_1": 0.0, "bikes_2": 0.0,
              "bikes_3": 0.0, "bikes_4plus": 0.0, "n_unweighted": 10}
             for r in range(71, 78) for h in (1, 2, 3, 4)]
    pd.DataFrame(brows).to_csv(mid_dir / "mid2023_bikes_by_rs7_haustyp.csv", index=False)
    with pytest.raises(ValueError, match="negative"):
        og.load_ownership_conditionals(str(tmp_path))


def test_add_ownership_grid_columns_rejects_negative_household_counts():
    """A negative household total would silently produce negative ownership targets."""
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    bcond = _uniform_conditional(list(og._BIKES_SHARE_COLUMNS))
    cells, kreis_per_cell = _mini_cells()
    cars_t, bikes_t = _mini_targets()
    hh_col = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    broken = cells.copy()
    broken.loc[0, hh_col] = -5.0
    with pytest.raises(ValueError, match="negative"):
        og.add_ownership_grid_columns(broken, cars_t, bikes_t, cond, bcond,
                                      kreis_per_cell=kreis_per_cell)


def test_add_ownership_grid_columns_bikes_branch_hits_its_kreis_targets():
    """The bikes branch had no numeric assertion anywhere (cars-only coverage)."""
    cond = _uniform_conditional(list(og._CARS_SHARE_COLUMNS))
    bcond = _uniform_conditional(list(og._BIKES_SHARE_COLUMNS))
    cells, kreis_per_cell = _mini_cells()
    cars_t, bikes_t = _mini_targets()
    out = og.add_ownership_grid_columns(cells, cars_t, bikes_t, cond, bcond,
                                        kreis_per_cell=kreis_per_cell)
    hh_col = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    assert out.loc[kreis_per_cell == "03101", "OWN_BIKES_0_agg"].sum() == pytest.approx(20.0)
    assert out.loc[kreis_per_cell == "03101", "OWN_BIKES_4plus_agg"].sum() == pytest.approx(40.0)
    assert out.loc[kreis_per_cell == "03102", "OWN_BIKES_0_agg"].sum() == pytest.approx(40.0)
    np.testing.assert_allclose(
        out[list(og.BIKES_COLUMNS)].sum(axis=1).to_numpy(), out[hh_col].to_numpy(), rtol=1e-9)


def test_select_load_columns_excludes_dwelling_columns_absent_from_the_parquet():
    """The original test could not fail on the exclusion half of the contract."""
    present, absent = og.DWELLING_INPUT_COLUMNS[0], og.DWELLING_INPUT_COLUMNS[1]
    out = og.select_load_columns(["GITTER_ID_100m"], ["GITTER_ID_100m", present])
    assert present in out
    assert absent not in out, "a dwelling column absent from the parquet must not be requested"


def test_select_load_columns_does_not_duplicate_an_already_requested_column():
    """Dedup is real only if a genuine collision is exercised."""
    present = og.DWELLING_INPUT_COLUMNS[0]
    out = og.select_load_columns(["GITTER_ID_100m", present], ["GITTER_ID_100m", present])
    assert out.count(present) == 1
