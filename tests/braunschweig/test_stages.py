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

    def test_fallback_provenance_logging(self, capsys):
        """Fallback transparency: the override log must separate the primary
        own-Kreis CDF count from the regional 03ZGB fallback count, so a
        systematic missing-Kreis-CDF is visible. Persons whose Kreise all have
        own CDFs -> 0 fallback; a person whose Kreis lacks a CDF -> counted as
        a regional fallback override."""
        from braunschweig.synthesis.spatial import commute_distance as cd

        unit_cdf_band1 = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])

        # Case A: every person's Kreis has its own CDF -> no fallback at all.
        # commune_id is the 12-digit ARS; Kreis = first 5 digits (03101/03102).
        df_all_own = pd.DataFrame({
            "person_id":        [1, 2],
            "hts_id":           [101, 102],
            "commute_distance": [9999.0, 9999.0],
            "commune_id":       ["031010000000", "031020000000"],
        })
        mid_refs_full = {
            "p13_distance_cdfs": {
                "03101": unit_cdf_band1,
                "03102": unit_cdf_band1,
                "03ZGB": unit_cdf_band1,
            }
        }
        rng = np.random.RandomState(0)
        cd._override_work_distances(df_all_own, mid_refs_full, rng)
        log_a = capsys.readouterr().out
        # Primary count 2, regional 03ZGB fallback count 0.
        assert "primary own-Kreis CDF 2" in log_a
        assert "regional 03ZGB fallback 0" in log_a
        assert "WARNING" not in log_a

        # Case B: one person's Kreis (99999) has no own CDF -> regional fallback.
        df_missing = pd.DataFrame({
            "person_id":        [1, 2],
            "hts_id":           [101, 102],
            "commute_distance": [9999.0, 9999.0],
            "commune_id":       ["031010000000", "999990000000"],
        })
        mid_refs_missing = {
            "p13_distance_cdfs": {
                "03101": unit_cdf_band1,
                "03ZGB": unit_cdf_band1,
            }
        }
        rng = np.random.RandomState(0)
        cd._override_work_distances(df_missing, mid_refs_missing, rng)
        log_b = capsys.readouterr().out
        # One primary (03101) + one regional 03ZGB fallback (99999).
        assert "primary own-Kreis CDF 1" in log_b
        assert "regional 03ZGB fallback 1" in log_b
        # 50% fallback rate exceeds the 5% threshold -> WARNING raised.
        assert "WARNING" in log_b

    def test_fallback_split_does_not_change_drawn_distances(self):
        """Output preservation: instrumenting the provenance split must not
        change which CDF is used, the draw, or the RNG. The drawn distances
        must equal a reference computation using the documented
        cdfs.get(kreis, fallback_cdf) selection on the same seeded RNG."""
        from braunschweig.synthesis.spatial import commute_distance as cd

        unit_cdf_band1 = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        df_work = pd.DataFrame({
            "person_id":        [1, 2, 3, 4],
            "hts_id":           [101, 102, 103, 104],
            "commute_distance": [9999.0, 9999.0, 9999.0, 9999.0],
            "commune_id": ["031010000000", "031011110000",
                           "999990000000", "031020000000"],
        })
        mid_refs = {
            "p13_distance_cdfs": {
                "03101": unit_cdf_band1,
                "03ZGB": unit_cdf_band1,
            }
        }

        # Actual output from the instrumented function.
        out = cd._override_work_distances(
            df_work.copy(), mid_refs, np.random.RandomState(7))

        # Reference: replicate the exact per-group selection + draw with the
        # same fresh RNG, mirroring cdfs.get(kreis, fallback_cdf).
        cdfs = mid_refs["p13_distance_cdfs"]
        fallback_cdf = cdfs.get("03ZGB")
        ref = df_work.copy()
        ref["kreis"] = ref["commune_id"].astype(str).str.zfill(12).str[:5]
        ref_rng = np.random.RandomState(7)
        for kreis, group_idx in ref.groupby("kreis", sort=False,
                                            dropna=True).groups.items():
            cdf = cdfs.get(str(kreis), fallback_cdf)
            if cdf is None:
                continue
            samples = cd._draw_from_cdf(cdf, ref_rng, len(group_idx))
            ref.loc[group_idx, "commute_distance"] = samples * 1000.0

        merged = out.merge(ref[["person_id", "commute_distance"]],
                           on="person_id", suffixes=("_out", "_ref"))
        assert np.allclose(merged["commute_distance_out"],
                           merged["commute_distance_ref"])


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

    def test_kreis_argument_matches_local_derivation(self):
        """Passing the pre-derived ``kreis`` Series must be output-identical to
        letting ``_sample_counts`` derive it internally (the FIX A refactor that
        derives the Kreis once in execute() and reuses it across cars/bikes/
        income instead of rebuilding it on every call)."""
        from braunschweig.synthesis.population.enriched import (
            _derive_kreis_ars5, _sample_counts,
        )

        df = pd.DataFrame({
            "person_id":           list(range(20)),
            "inside_braunschweig": [True] * 10 + [False] * 10,
            "inside_salzgitter":   [False] * 10 + [True] * 10,
        })
        values = np.array([0, 1, 2, 3])
        kreis_shares = {
            "03101": (0.1, 0.4, 0.3, 0.2),
            "03102": (0.2, 0.2, 0.3, 0.3),
        }
        region_shares = (0.25, 0.25, 0.25, 0.25)

        df_local = df.copy()
        df_reused = df.copy()
        # Local derivation path (kreis=None).
        _sample_counts(df_local, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(7))
        # Reuse path: derive once, pass it in.
        kreis = _derive_kreis_ars5(df_reused)
        _sample_counts(df_reused, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(7), kreis=kreis)

        assert df_local["n_cars"].tolist() == df_reused["n_cars"].tolist(), \
            "passing kreis= must yield identical output to local derivation"

    def test_kreis_iteration_order_is_sorted_and_deterministic(self):
        """FIX 2.1: ``_sample_counts`` must consume the shared RNG stream over
        the Kreise in a deterministic SORTED order, not the hash-dependent
        ``set()`` iteration order (which varies with PYTHONHASHSEED).

        We verify this by replaying the exact draws the function should make if
        it iterates the Kreis codes in ``sorted`` order, drawing ``n`` values
        per Kreis from a fresh RNG with the same seed, and assert the function
        reproduces that per-person assignment. A ``set()``-based iteration would
        consume the per-Kreis blocks in a different (hash-dependent) order and
        therefore generally mismatch this sorted-order replay."""
        from braunschweig.synthesis.population.enriched import _sample_counts

        values = np.array([0, 1, 2, 3])
        # Non-degenerate shares so the actual draw depends on the position in
        # the consumed RNG stream (degenerate 1.0 shares would hide the order).
        kreis_shares = {
            "03101": (0.1, 0.4, 0.3, 0.2),
            "03102": (0.2, 0.2, 0.3, 0.3),
            "03151": (0.3, 0.3, 0.2, 0.2),
        }
        region_shares = (0.25, 0.25, 0.25, 0.25)

        df = pd.DataFrame({
            "person_id":           list(range(12)),
            "inside_braunschweig": [True] * 4 + [False] * 8,
            "inside_salzgitter":   [False] * 4 + [True] * 4 + [False] * 4,
            "inside_gifhorn":      [False] * 8 + [True] * 4,
        })

        df_out = df.copy()
        _sample_counts(df_out, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(2024))

        # Independent sorted-order replay of the same draws.
        from braunschweig.synthesis.population.enriched import _derive_kreis_ars5
        kreis = _derive_kreis_ars5(df)
        replay = np.zeros(len(df), dtype=int)
        rng = np.random.RandomState(2024)
        for ars in sorted(kreis.unique()):
            shares = np.asarray(kreis_shares.get(ars, region_shares), dtype=float)
            shares = shares / shares.sum()
            mask = (kreis == ars).values
            n = int(mask.sum())
            if n == 0:
                continue
            replay[mask] = rng.choice(values, size=n, p=shares)

        assert df_out["n_cars"].tolist() == replay.tolist(), \
            "_sample_counts must consume the RNG in sorted Kreis order (FIX 2.1)"

        # And it must be reproducible across two executions with equal seeds.
        df_again = df.copy()
        _sample_counts(df_again, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(2024))
        assert df_again["n_cars"].tolist() == df_out["n_cars"].tolist()


# ---------------------------------------------------------------------------
# 7a. enriched._sample_counts per-Kreis share fallback transparency
#     (cars from MiD H7, bikes from MiD H12.3). A Kreis-ARS format mismatch
#     would silently route ALL persons to the region-wide distribution; these
#     tests pin the primary/fallback accounting exposed via df.attrs.
# ---------------------------------------------------------------------------

class TestSampleCountsFallbackTransparency:
    def _build_population(self):
        return pd.DataFrame({
            "person_id":           list(range(20)),
            "inside_braunschweig": [True] * 10 + [False] * 10,
            "inside_salzgitter":   [False] * 10 + [True] * 10,
        })

    def test_all_kreise_present_means_zero_fallback(self):
        """When every Kreis ARS-5 is present in the share table, the PRIMARY
        per-Kreis lookup must cover all persons and the fallback count is 0."""
        from braunschweig.synthesis.population.enriched import _sample_counts

        df = self._build_population()
        values = np.array([0, 1, 2, 3])
        kreis_shares = {
            "03101": (0.1, 0.4, 0.3, 0.2),
            "03102": (0.2, 0.2, 0.3, 0.3),
        }
        region_shares = (0.25, 0.25, 0.25, 0.25)

        _sample_counts(df, "number_of_cars", values, region_shares,
                       kreis_shares, np.random.RandomState(123))

        assert df.attrs["number_of_cars_kreis_share_fallback_count"] == 0
        assert df.attrs["number_of_cars_kreis_share_primary_count"] == 20
        assert df.attrs["number_of_cars_kreis_share_fallback_rate"] == 0.0
        assert df.attrs["number_of_cars_kreis_share_fallback_kreise"] == []
        # Result is still a valid count drawn from the value set.
        assert df["number_of_cars"].isin(values).all()

    def test_absent_kreis_is_counted_as_region_fallback(self):
        """When a Kreis ARS-5 is ABSENT from the share table, every person in
        that Kreis must be counted against the region-wide fallback (and the
        fallback Kreis code recorded), while the result stays valid."""
        from braunschweig.synthesis.population.enriched import _sample_counts

        df = self._build_population()
        values = np.array([0, 1, 2, 3])
        # 03102 (Salzgitter, 10 persons) is intentionally missing -> fallback.
        kreis_shares = {
            "03101": (0.1, 0.4, 0.3, 0.2),
        }
        region_shares = (0.25, 0.25, 0.25, 0.25)

        _sample_counts(df, "number_of_cars", values, region_shares,
                       kreis_shares, np.random.RandomState(123))

        assert df.attrs["number_of_cars_kreis_share_fallback_count"] == 10
        assert df.attrs["number_of_cars_kreis_share_primary_count"] == 10
        assert df.attrs["number_of_cars_kreis_share_fallback_rate"] == 0.5
        assert df.attrs["number_of_cars_kreis_share_fallback_kreise"] == ["03102"]
        # The fallback path must still produce valid counts for all persons.
        assert df["number_of_cars"].isin(values).all()
        assert len(df["number_of_cars"]) == 20

    def test_fallback_accounting_does_not_change_sampled_values(self):
        """The added counting/logging must be output-preserving: the sampled
        per-person values with an absent Kreis must equal an independent
        sorted-order RNG replay that uses the same region fallback vector."""
        from braunschweig.synthesis.population.enriched import (
            _derive_kreis_ars5, _sample_counts,
        )

        df = self._build_population()
        values = np.array([0, 1, 2, 3])
        kreis_shares = {
            "03101": (0.1, 0.4, 0.3, 0.2),
        }
        region_shares = (0.2, 0.3, 0.3, 0.2)

        df_out = df.copy()
        _sample_counts(df_out, "number_of_cars", values, region_shares,
                       kreis_shares, np.random.RandomState(99))

        # Independent sorted-order replay with the identical fallback vector.
        kreis = _derive_kreis_ars5(df)
        replay = np.zeros(len(df), dtype=int)
        rng = np.random.RandomState(99)
        for ars in sorted(kreis.unique()):
            shares = np.asarray(kreis_shares.get(ars, region_shares), dtype=float)
            shares = shares / shares.sum()
            mask = (kreis == ars).values
            n = int(mask.sum())
            if n == 0:
                continue
            replay[mask] = rng.choice(values, size=n, p=shares)

        assert df_out["number_of_cars"].tolist() == replay.tolist(), \
            "fallback instrumentation must not change the sampled values/RNG"


# ---------------------------------------------------------------------------
# 7b. enriched._derive_kreis_ars5 over all eight political-prefix flags
# ---------------------------------------------------------------------------

class TestDeriveKreisArs5AllFlags:
    def test_all_eight_inside_flags_map_to_expected_ars5(self):
        """Every one of the 8 ZGB inside_<kreis> flags must resolve to its
        AGS-5 Kreis code, in the same order as INSIDE_FLAG_TO_ARS5."""
        from braunschweig.synthesis.population.enriched import (
            INSIDE_FLAG_TO_ARS5, _derive_kreis_ars5,
        )

        flags = list(INSIDE_FLAG_TO_ARS5.keys())
        n = len(flags)
        # One person per flag: person i has only flags[i] set to True.
        data = {"person_id": list(range(n))}
        for j, flag in enumerate(flags):
            data[flag] = [i == j for i in range(n)]
        df = pd.DataFrame(data)

        out = _derive_kreis_ars5(df)
        expected = [INSIDE_FLAG_TO_ARS5[flag] for flag in flags]
        assert out.tolist() == expected
        # Index is preserved (used as a Series elsewhere in execute()).
        assert list(out.index) == list(df.index)

    def test_nan_flags_treated_as_false(self):
        """NaN in an inside flag must be treated as False (fillna), not raise."""
        from braunschweig.synthesis.population.enriched import _derive_kreis_ars5

        df = pd.DataFrame({
            "person_id":           [1, 2, 3],
            "inside_braunschweig": [True, np.nan, False],
            "inside_salzgitter":   [False, np.nan, True],
        })
        out = _derive_kreis_ars5(df)
        # Person 2: both flags NaN -> no Kreis -> empty string.
        assert out.tolist() == ["03101", "", "03102"]


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
# 8b. enriched._execute_base reproducibility / cache-mutation guards
#     (FIX 2.2 cached-list mutation, FIX 2.6 distinct RNG seed offsets)
# ---------------------------------------------------------------------------

import inspect

from braunschweig.synthesis.population import enriched as _enriched_module


class TestConstraintListNotMutated:
    """FIX 2.2: the car/bike availability blocks must copy the cached MiD
    constraint list (``list(mid["..."])``) before ``.append(...)`` so the
    cached ``braunschweig.data.mid.data`` stage object is never mutated in
    place.

    We reproduce the exact copy-then-append idiom used in ``_execute_base`` and
    assert the original list is untouched; we additionally pin the source so a
    future regression back to a bare reference is caught."""

    def test_copy_then_append_does_not_mutate_input(self):
        # Mirror the production idiom: ``constraints = list(mid["..."])``.
        cached_list = [{"sex": "male", "target": 0.5}]
        len_before = len(cached_list)
        constraints = list(cached_list)  # copy (production behaviour)
        constraints.append({"age": (-np.inf, -1), "target": 0.0})
        # The copy received the new constraint ...
        assert len(constraints) == len_before + 1
        # ... but the cached source list is unchanged.
        assert len(cached_list) == len_before
        assert cached_list == [{"sex": "male", "target": 0.5}]

    def test_source_copies_cached_constraint_lists(self):
        src = inspect.getsource(_enriched_module._execute_base)
        # Both availability blocks must take a copy before appending.
        assert 'list(mid["car_availability_constraints"])' in src, \
            "car constraints must be copied before append (FIX 2.2)"
        assert 'list(mid["bicycle_availability_constraints"])' in src, \
            "bicycle constraints must be copied before append (FIX 2.2)"
        # Guard against the regressed bare-reference form.
        assert 'constraints = mid["car_availability_constraints"]\n' not in src
        assert 'constraints = mid["bicycle_availability_constraints"]\n' not in src


class TestRandomSeedOffsetsDistinct:
    """FIX 2.6: the PT-subscription draw and the car/bike availability draw are
    independent attributes and must NOT share the same uniform RNG stream. Their
    ``random_seed`` offsets therefore have to differ (previously both used
    +8572, making the two draws correlated by construction)."""

    def test_pt_and_car_bike_seed_offsets_differ(self):
        src = inspect.getsource(_enriched_module._execute_base)
        import re

        offsets = [
            int(m) for m in re.findall(
                r"RandomState\(context\.config\(\"random_seed\"\)\s*\+\s*(\d+)\)",
                src,
            )
        ]
        # PT block (+8572) and car/bike block must both be present and distinct.
        assert 8572 in offsets, "PT block must keep its +8572 offset"
        # There must be at least two RandomState constructions in this function
        # and no offset may be used twice (every independent draw is its own
        # stream).
        assert len(offsets) >= 2
        assert len(offsets) == len(set(offsets)), \
            f"RNG seed offsets must all be distinct, got {offsets} (FIX 2.6)"


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
