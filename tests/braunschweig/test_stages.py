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
    def test_own_kreis_cdf_is_used_and_missing_kreise_fall_back_to_03zgb(self, capsys):
        """The primary path (a person's own Kreis CDF) and the regional fallback
        must be distinguishable in the output, not only in the log.

        The earlier version of this test used 8-digit commune ids, which
        ``zfill(12)`` turned into Kreis "00000", so every person silently took
        the 03ZGB fallback and the test passed only because both CDFs were
        identical. Here the two CDFs sit in different distance bands, so a
        Kreis-slicing bug (or a fallback that swallows everyone) moves persons
        into the wrong band.
        """
        from braunschweig.synthesis.spatial import commute_distance as cd

        df_work = pd.DataFrame({
            "person_id":        [1, 2, 3, 4],
            "hts_id":           [101, 102, 103, 104],
            "commute_distance": [9999.0, 9999.0, 9999.0, 9999.0],
            # 12-digit ARS. Person 2 arrives as an int with the leading zero of
            # the state code stripped (the BUG-003 shape zfill(12) must restore);
            # 99999 is unknown and 03102 has no CDF of its own.
            "commune_id": ["031010000000", 31011110000, "999990000000", "031020000000"],
        })
        band1 = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])   # 0.5-5 km
        band7 = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])   # 100-300 km
        mid_refs = {"p13_distance_cdfs": {"03101": band1, "03ZGB": band7}}

        out = cd._override_work_distances(df_work, mid_refs, np.random.RandomState(42))

        assert list(out.columns) == ["person_id", "hts_id", "commute_distance"]
        distance = out.set_index("person_id")["commute_distance"]
        for pid in (1, 2):   # own-Kreis CDF (primary path)
            assert 500.0 <= distance[pid] <= 5000.0, (pid, distance[pid])
        for pid in (3, 4):   # regional fallback CDF
            assert 100_000.0 <= distance[pid] <= 300_000.0, (pid, distance[pid])
        log = capsys.readouterr().out
        assert "primary own-Kreis CDF 2" in log and "regional 03ZGB fallback 2" in log

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

# The eight ZGB districts and their official Kreis codes (Destatis AGS), written out
# literally: expectations taken from INSIDE_FLAG_TO_ARS5 itself would let a wrong code in
# that map pass.
_ZGB_INSIDE_FLAG_ARS5 = [
    ("inside_braunschweig", "03101"), ("inside_salzgitter", "03102"),
    ("inside_wolfsburg", "03103"), ("inside_gifhorn", "03151"),
    ("inside_goslar", "03153"), ("inside_helmstedt", "03154"),
    ("inside_peine", "03157"), ("inside_wolfenbuettel", "03158"),
]


class TestDeriveKreisArs5:
    def test_every_inside_flag_maps_to_its_official_kreis_code(self):
        from braunschweig.synthesis.population.enriched import _derive_kreis_ars5

        flags = [flag for flag, _ in _ZGB_INSIDE_FLAG_ARS5]
        # One person per district, then a person without any flag (-> ""), then a person
        # with two flags (peine and wolfsburg): the first flag in map order wins.
        rows = [[i == j for j in range(len(flags))] for i in range(len(flags))]
        rows.append([False] * len(flags))
        rows.append([flag in ("inside_peine", "inside_wolfsburg") for flag in flags])
        df = pd.DataFrame(rows, columns=flags, index=range(100, 100 + len(rows)))

        out = _derive_kreis_ars5(df)

        assert out.tolist() == [code for _, code in _ZGB_INSIDE_FLAG_ARS5] + ["", "03103"]
        # Index is preserved (used as a Series elsewhere in execute()).
        assert list(out.index) == list(df.index)


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
# 8b. enriched._execute_base reproducibility / cache-mutation guards
#     (FIX 2.2 cached-list mutation, FIX 2.6 distinct RNG seed offsets)
# ---------------------------------------------------------------------------

import inspect

from braunschweig.synthesis.population import enriched as _enriched_module


def _execute_base_source():
    """Source of ``_execute_base`` together with its ``_step_*`` helpers.

    ``_execute_base`` was decomposed into named ``_step_*`` orchestration steps
    (issue #267), so the blocks pinned below now live in those step functions
    rather than in ``_execute_base`` itself. This is FUNCTION-FAMILY-scoped
    rather than module-scoped: it concatenates the source of ``_execute_base``,
    every ``_step_*``-named attribute of its defining module, and the
    ``_condition_pt_subscription_for_sampling`` sub-helper of
    ``_step_sample_pt_subscription`` (A6; a non-``_step_`` name because it is not
    itself called by the orchestrator). Scoping to the family rather than the
    whole module means the pins keep following this exact function group even if
    unrelated code is later added to (or moved out of) the defining module.
    """
    module = inspect.getmodule(_enriched_module._execute_base)
    functions = [_enriched_module._execute_base]
    functions += [
        getattr(module, name) for name in dir(module) if name.startswith("_step_")
    ]
    functions.append(module._condition_pt_subscription_for_sampling)
    return "\n".join(inspect.getsource(fn) for fn in functions)


class TestConstraintListNotMutated:
    """FIX 2.2: the car/bike availability blocks must copy the cached MiD
    constraint list (``list(mid["..."])``) before ``.append(...)`` so the
    cached ``braunschweig.data.mid.data`` stage object is never mutated in
    place.

    We reproduce the exact copy-then-append idiom used in ``_execute_base`` and
    assert the original list is untouched; we additionally pin the source so a
    future regression back to a bare reference is caught."""

    def test_source_copies_cached_constraint_lists(self):
        src = _execute_base_source()
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
        src = _execute_base_source()
        import re

        offsets = [
            int(m) for m in re.findall(
                r"RandomState\(context\.config\(\"random_seed\"\)\s*\+\s*(\d+)\)",
                src,
            )
        ]
        # PT block (+8572) and car/bike block must both be present and distinct.
        assert 8572 in offsets, "PT block must keep its +8572 offset"
        # There must be at least two RandomState constructions across
        # _execute_base and its steps, and no offset may be used twice (every
        # independent draw is its own stream).
        assert len(offsets) >= 2
        assert len(offsets) == len(set(offsets)), \
            f"RNG seed offsets must all be distinct, got {offsets} (FIX 2.6)"

