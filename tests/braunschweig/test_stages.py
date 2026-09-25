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

    def test_the_seeded_override_draw_is_pinned(self):
        """Reproducibility of the override draw (per-Kreis groups in appearance order
        on one seeded RNG), pinned on literal distances in metres for seed 7
        (computed 2026-09-25). It replaces a replay that re-implemented the group loop
        and so would have agreed with any change made to both."""
        from braunschweig.synthesis.spatial import commute_distance as cd

        band1 = np.array([0.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        df_work = pd.DataFrame({
            "person_id":        [1, 2, 3, 4],
            "hts_id":           [101, 102, 103, 104],
            "commute_distance": [9999.0, 9999.0, 9999.0, 9999.0],
            "commune_id": ["031010000000", "031011110000",
                           "999990000000", "031020000000"],
        })
        mid_refs = {"p13_distance_cdfs": {"03101": band1, "03ZGB": band1}}

        out = cd._override_work_distances(df_work, mid_refs, np.random.RandomState(7))

        assert np.allclose(out["commute_distance"],
                           [2472.841541, 3755.5933, 2923.231417, 824.2301])


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

    def test_pinned_draw_in_sorted_kreis_order_with_a_region_fallback(self):
        """FIX 2.1 and the fallback instrumentation, pinned on literal values.

        Persons are listed Gifhorn, Braunschweig, Salzgitter -- NOT in sorted ARS order --
        and Salzgitter has no own share vector, so it draws from the region-wide one.
        _sample_counts must consume the RNG over the Kreise in sorted order (a set or
        appearance-order iteration yields [1, 2, 0, 0, 1, 1, 2, 2, 1, 1, 0, 2] here,
        checked 2026-09-25), the counting must not change the draw, and a pre-derived
        ``kreis`` argument (the FIX A reuse path) must give the identical result.
        """
        from braunschweig.synthesis.population.enriched import (
            _derive_kreis_ars5, _sample_counts,
        )

        df = pd.DataFrame({
            "person_id":           list(range(12)),
            "inside_gifhorn":      [True] * 4 + [False] * 8,
            "inside_braunschweig": [False] * 4 + [True] * 4 + [False] * 4,
            "inside_salzgitter":   [False] * 8 + [True] * 4,
        })
        values = np.array([0, 1, 2, 3])
        kreis_shares = {"03101": (0.1, 0.4, 0.3, 0.2), "03151": (0.3, 0.3, 0.2, 0.2)}
        region_shares = (0.2, 0.3, 0.3, 0.2)
        expected = [1, 1, 0, 2, 2, 2, 1, 0, 1, 0, 2, 2]

        local = df.copy()
        _sample_counts(local, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(2024))
        reused = df.copy()
        _sample_counts(reused, "n_cars", values, region_shares, kreis_shares,
                       np.random.RandomState(2024), kreis=_derive_kreis_ars5(reused))

        assert local["n_cars"].tolist() == expected
        assert reused["n_cars"].tolist() == expected
        assert local.attrs["n_cars_kreis_share_fallback_count"] == 4
        assert local.attrs["n_cars_kreis_share_fallback_kreise"] == ["03102"]

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

class TestConstraintListNotMutated:
    """FIX 2.2: the car/bike availability blocks must copy the cached MiD
    constraint list (``list(mid["..."])``) before ``.append(...)`` so the
    cached ``braunschweig.data.mid.data`` stage object is never mutated in
    place.

    The two imputation steps run twice, as on an in-process re-run, and the cached
    constraint lists must come out unchanged; a step that appended to the cached
    list directly would grow it by one age constraint per run. (It replaces a check
    on the source text, which any other spelling of the copy would have failed.)"""

    def test_imputation_steps_leave_the_cached_constraint_lists_untouched(self):
        from braunschweig.synthesis.population.enriched.base import (
            _step_impute_bicycle_availability, _step_impute_car_availability,
        )

        class _Context:
            def config(self, key, *args, **kwargs):
                return {"braunschweig.minimum_age.car_availability": 18,
                        "braunschweig.minimum_age.bicycle_availability": 6}[key]

            def progress(self, iterable, **kwargs):
                return iterable

        mid = {
            "car_availability_constraints": [{"sex": "male", "target": 0.8}],
            "bicycle_availability_constraints": [{"sex": "female", "target": 0.6}],
        }
        persons = pd.DataFrame({"age": [5, 30, 45], "sex": ["male", "female", "male"]})
        for _run in range(2):
            _step_impute_car_availability(_Context(), persons, mid, iterations=2)
            _step_impute_bicycle_availability(_Context(), persons, mid, iterations=2)

        assert mid["car_availability_constraints"] == [{"sex": "male", "target": 0.8}]
        assert mid["bicycle_availability_constraints"] == [{"sex": "female", "target": 0.6}]


class TestRandomSeedOffsetsDistinct:
    """FIX 2.6: the PT-subscription draw and the car/bike availability draw are
    independent attributes and must NOT share the same uniform RNG stream. Their
    ``random_seed`` offsets therefore have to differ (previously both used
    +8572, making the two draws correlated by construction).

    A policy lint over EVERY module of the enriched package, reading only real
    ``RandomState(<seed> + N)`` / ``default_rng(<seed> + N)`` calls from the syntax tree:
    the earlier regex scanned two functions of one module, while seven further streams
    live in availability, vehicle ownership, income, economic status and housing."""

    def test_every_rng_stream_of_the_enriched_package_has_its_own_offset(self):
        import ast

        from braunschweig.synthesis.population import enriched

        package = pathlib.Path(enriched.__file__).parent
        streams = []
        for path in sorted(package.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                seed = node.args[0]
                if (name in ("RandomState", "default_rng") and isinstance(seed, ast.BinOp)
                        and isinstance(seed.op, ast.Add) and isinstance(seed.right, ast.Constant)):
                    streams.append((seed.right.value, f"{path.name}:{node.lineno}"))

        offsets = [offset for offset, _ in streams]
        assert 8572 in offsets, "the PT block must keep its +8572 offset"
        assert len(offsets) >= 8, streams  # the lint must actually see the package's streams
        assert len(offsets) == len(set(offsets)), \
            f"RNG seed offsets must all be distinct (FIX 2.6): {sorted(streams)}"

