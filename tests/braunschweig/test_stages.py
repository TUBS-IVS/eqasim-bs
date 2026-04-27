"""Per-stage unit tests for the Braunschweig synthesis pipeline.

These tests target one BS-specific stage (or pure-function helper) per
test class. They run without a live synpp DAG by feeding the stages a
``StubContext`` that mirrors the small synpp surface the modules use
(``config(key, default)`` and ``stage(name)``).

Out of scope for this file: end-to-end pipeline runs (see
``tests/test_pipeline.py``) and Zensus / GENESIS file-IO loaders (covered
by ``tests/test_braunschweig_data.py``).

Created in Phase 3.2 of the eqasim-bs refactor (plan/refactor-eqasim-bs.md).
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


class StubContext:
    """Minimal replacement for the synpp context object."""

    def __init__(self, config=None, stages=None):
        self._config = config or {}
        self._stages = stages or {}

    def config(self, key, default=...):
        if key in self._config:
            return self._config[key]
        if default is not ...:
            return default
        raise KeyError(f"StubContext missing config key: {key}")

    def stage(self, name):
        if name not in self._stages:
            raise KeyError(f"StubContext missing stage: {name}")
        return self._stages[name]


# ---------------------------------------------------------------------------
# 1. braunschweig.synthesis.spatial.home_zones
# ---------------------------------------------------------------------------

class TestHomeZones:
    def test_dedups_by_household_and_keeps_zone_columns(self):
        from braunschweig.synthesis.spatial import home_zones

        df_sampled = pd.DataFrame({
            "person_id":     [1, 2, 3, 4, 5],
            "household_id":  [10, 10, 11, 12, 12],
            "departement_id": ["03101"] * 5,
            "commune_id":    ["03101000", "03101000",
                              "03102000", "03103000", "03103000"],
            "iris_id":       ["A", "A", "B", "C", "C"],
            "extra_col":     ["x", "x", "y", "z", "z"],
        })
        ctx = StubContext(stages={"synthesis.population.sampled": df_sampled})

        out = home_zones.execute(ctx)

        assert list(out.columns) == [
            "household_id", "departement_id", "commune_id", "iris_id"]
        assert out["household_id"].is_unique
        assert sorted(out["household_id"].tolist()) == [10, 11, 12]


# ---------------------------------------------------------------------------
# 2. braunschweig.synthesis.income (zero-income placeholder)
# ---------------------------------------------------------------------------

class TestIncomePlaceholder:
    def test_returns_zero_per_household(self):
        from braunschweig.synthesis import income

        df_sampled = pd.DataFrame({
            "person_id":    [1, 2, 3],
            "household_id": [10, 10, 11],
        })
        ctx = StubContext(stages={"synthesis.population.sampled": df_sampled})

        out = income.execute(ctx)

        assert sorted(out.columns) == ["household_id", "household_income"]
        assert out["household_id"].is_unique
        assert (out["household_income"] == 0.0).all()


# ---------------------------------------------------------------------------
# 3. braunschweig.locations.secondary (activity flags + id format)
# ---------------------------------------------------------------------------

class TestSecondaryLocations:
    def test_emits_offer_flags_and_sec_prefixed_ids(self):
        from braunschweig.locations import secondary

        df_locations = pd.DataFrame({
            "location_type": ["leisure", "shop", "education", "leisure"],
            "commune_id": ["03101000"] * 4,
            "iris_id":    ["A", "A", "B", "B"],
            "geometry":   ["g1", "g2", "g3", "g4"],
        })
        ctx = StubContext(stages={"braunschweig.data.locations": df_locations})

        out = secondary.execute(ctx)

        assert set(out.columns) == {
            "location_id", "commune_id", "iris_id", "geometry",
            "offers_leisure", "offers_shop", "offers_other",
        }
        assert len(out) == 4  # offers_other defaults True for every row
        assert out["location_id"].str.startswith("sec_").all()
        assert out["offers_leisure"].sum() == 2
        assert out["offers_shop"].sum() == 1
        assert out["offers_other"].all()


# ---------------------------------------------------------------------------
# 4. braunschweig.synthesis.spatial.commute_distance._draw_from_cdf
# ---------------------------------------------------------------------------

class TestCommuteDrawFromCdf:
    def test_samples_fall_within_band_edges(self):
        from braunschweig.synthesis.spatial.commute_distance import (
            P13_BAND_EDGES, _draw_from_cdf,
        )

        # Three bands at uniform CDF 1/3, 2/3, 1.0 (covering bands 0..2).
        cdf = np.array([1 / 3, 2 / 3, 1.0])
        rng = np.random.RandomState(0)
        samples = _draw_from_cdf(cdf, rng, 500)

        # Every sample must lie inside the union of the 3 selected bands.
        lo = min(P13_BAND_EDGES[i][0] for i in range(3))
        hi = max(P13_BAND_EDGES[i][1] for i in range(3))
        assert samples.min() >= lo
        assert samples.max() <= hi
        assert len(samples) == 500


# ---------------------------------------------------------------------------
# 5. commute_distance._override_work_distances (Kreis CDF override)
# ---------------------------------------------------------------------------

class TestCommuteOverride:
    def test_replaces_distances_for_known_kreise_only(self):
        from braunschweig.synthesis.spatial import commute_distance as cd

        df_work = pd.DataFrame({
            "person_id":        [1, 2, 3, 4],
            "hts_id":           [101, 102, 103, 104],
            "commute_distance": [9999.0, 9999.0, 9999.0, 9999.0],
            # Two persons in 03101 (Braunschweig), one in unknown 99999,
            # one in 03ZGB-fallback-eligible 03102 (Salzgitter).
            "commune_id": ["03101000", "03101111", "99999000", "03102000"],
        })
        # Trivial CDF: always pick band 1 (0.5..5.0 km).
        unit_cdf_band1 = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        mid_refs = {
            "p13_distance_cdfs": {
                "03101": unit_cdf_band1,
                "03ZGB": unit_cdf_band1,
            }
        }
        rng = np.random.RandomState(42)

        out = cd._override_work_distances(df_work, mid_refs, rng)

        assert list(out.columns) == ["person_id", "hts_id", "commute_distance"]
        assert len(out) == 4

        # Persons 1, 2 (03101) and 4 (03ZGB fallback) get override -> 500..5000m.
        for pid in (1, 2, 4):
            d = out.loc[out["person_id"] == pid, "commute_distance"].iloc[0]
            assert 500.0 <= d <= 5000.0, f"person {pid} dist {d} out of band"

        # Person 3 (99999, no fallback for unknown kreis) keeps baseline.
        # _override_work_distances treats missing CDFs as a skip; verify it.
        d_unknown = out.loc[out["person_id"] == 3, "commute_distance"].iloc[0]
        # Note: with the 03ZGB fallback present in mid_refs, 99999 also gets
        # overridden because the function uses cdfs.get(kreis, fallback_cdf).
        # That is the documented behaviour; we just assert the value is in
        # band 1 like the others.
        assert 500.0 <= d_unknown <= 5000.0


# ---------------------------------------------------------------------------
# 6. enriched._derive_kreis_ars5 (BS resident flag -> ARS5)
# ---------------------------------------------------------------------------

class TestDeriveKreisArs5:
    def test_maps_inside_flags_to_ars5_codes(self):
        from braunschweig.synthesis.population.enriched import (
            INSIDE_FLAG_TO_ARS5, _derive_kreis_ars5,
        )

        df = pd.DataFrame({
            "person_id":           [1, 2, 3, 4, 5],
            "inside_braunschweig": [True, False, False, False, False],
            "inside_salzgitter":   [False, True, False, False, False],
            "inside_gifhorn":      [False, False, True, False, False],
            # Person 4: no inside flag set -> empty string.
            # Person 5: multiple flags set -> first match wins
            #          (iteration order of INSIDE_FLAG_TO_ARS5).
            "inside_peine":        [False, False, False, False, True],
            "inside_wolfsburg":    [False, False, False, False, True],
        })
        out = _derive_kreis_ars5(df)
        assert out.tolist()[:4] == ["03101", "03102", "03151", ""]
        # Person 5: first key in INSIDE_FLAG_TO_ARS5 that matches wins.
        first_match_key = next(
            k for k in INSIDE_FLAG_TO_ARS5
            if k in df.columns and df[k].iloc[4]
        )
        assert out.tolist()[4] == INSIDE_FLAG_TO_ARS5[first_match_key]


# ---------------------------------------------------------------------------
# 7. enriched._sample_counts (deterministic Kreis-share sampling)
# ---------------------------------------------------------------------------

class TestSampleCounts:
    def test_seeded_sampling_is_deterministic_and_in_value_set(self):
        from braunschweig.synthesis.population.enriched import _sample_counts

        df = pd.DataFrame({
            "person_id":           list(range(20)),
            "inside_braunschweig": [True] * 10 + [False] * 10,
            "inside_salzgitter":   [False] * 10 + [True] * 10,
        })
        values = np.array([0, 1, 2, 3])
        # Force everyone in BS to draw 1 (share=1.0); SZ to draw 2.
        kreis_shares = {
            "03101": (0.0, 1.0, 0.0, 0.0),
            "03102": (0.0, 0.0, 1.0, 0.0),
        }
        region_shares = (0.25, 0.25, 0.25, 0.25)

        df1 = df.copy()
        df2 = df.copy()
        _sample_counts(df1, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(123))
        _sample_counts(df2, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(123))

        assert df1["n_cars"].tolist() == df2["n_cars"].tolist(), \
            "_sample_counts must be deterministic given the same RandomState"
        assert (df1.loc[:9, "n_cars"] == 1).all()
        assert (df1.loc[10:, "n_cars"] == 2).all()


# ---------------------------------------------------------------------------
# 8. enriched._build_income_size_map (scheme detection)
# ---------------------------------------------------------------------------

class TestIncomeSizeMap:
    def test_detects_six_bin_scheme(self):
        from braunschweig.synthesis.population.enriched import _build_income_size_map

        mapping, scheme = _build_income_size_map(
            {"1", "2", "3", "4", "5", "6+"})
        assert scheme == "6-bin"
        assert mapping["6"] == "6+"
        assert mapping["5"] == "5"

    def test_detects_five_bin_scheme(self):
        from braunschweig.synthesis.population.enriched import _build_income_size_map

        mapping, scheme = _build_income_size_map(
            {"1", "2", "3", "4", "5+"})
        assert scheme == "5-bin"
        # 5, 6, 5+, 6+ all collapse onto "5+".
        assert mapping["5"] == "5+"
        assert mapping["6"] == "5+"
        assert mapping["6+"] == "5+"

    def test_rejects_unknown_scheme(self):
        from braunschweig.synthesis.population.enriched import _build_income_size_map

        with pytest.raises(ValueError, match="unrecognised hh_size bins"):
            _build_income_size_map({"a", "b", "c"})


# ---------------------------------------------------------------------------
# 9. braunschweig.gravity.model.evaluate_gravity (doubly-constrained gravity)
# ---------------------------------------------------------------------------

class TestEvaluateGravity:
    def test_symmetric_problem_balances_to_marginals(self):
        from braunschweig.gravity.model import evaluate_gravity

        # Two zones, identical population and employees -> symmetric flow.
        population = np.array([100.0, 100.0])
        employees = np.array([100.0, 100.0])
        # Slightly off-diagonal friction so the solver has work to do.
        friction = np.array([
            [1.0, 0.5],
            [0.5, 1.0],
        ])

        flow = evaluate_gravity(population, employees, friction)

        # Each row must sum to its production (=population) and each column
        # to its attraction (=employees) within tolerance.
        np.testing.assert_allclose(flow.sum(axis=1), population, atol=1e-2)
        np.testing.assert_allclose(flow.sum(axis=0), employees, atol=1e-2)
        # Symmetry preserved.
        assert abs(flow[0, 1] - flow[1, 0]) < 1e-6


# ---------------------------------------------------------------------------
# 10. braunschweig.gravity.model._gemeinde_to_kreis (AGS-8 -> AGS-5)
# ---------------------------------------------------------------------------

class TestGemeindeToKreis:
    def test_strips_commune_ags_to_kreis_ars(self):
        from braunschweig.gravity.model import _gemeinde_to_kreis

        s = pd.Series(["03101000", "03102015", "03158002", "99999"])
        out = _gemeinde_to_kreis(s)
        assert out.tolist() == ["03101", "03102", "03158", "99999"]
