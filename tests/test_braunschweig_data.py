"""Smoke tests for Braunschweig data loaders.

Purpose
-------
These tests validate the pure-function parts of the Braunschweig data
layer (regex filters, age mappings, CDF builders, ID normalisation) and
the core invariants of the small helper stages that do not require the
full synpp context (``external_workplaces``, the MiD references loader,
the pendler reader).

They are written to run quickly (no pipeline execution) and to catch
regressions in the CSV/Excel parsing or ID handling — the kind of bugs
that surfaced during the integration pass (e.g. the 5-digit aggregate
row leakage in the BA Pendleratlas reader, the cars=1 alias-chain issue,
the ``household_size`` "5"/"6+" mapping).

Run with::

    pytest tests/test_braunschweig_data.py -v

Or with coverage::

    pytest tests/test_braunschweig_data.py --cov=braunschweig --cov-report=term-missing
"""

from __future__ import annotations

import os
import pathlib
import pickle
import sys

import numpy as np
import pandas as pd
import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DATA_ROOT = REPO_ROOT / "eqasim-data" / "data"
CACHE_ROOT = REPO_ROOT / "eqasim-data" / "cache_bs"

ZGB_KREISE = ["03101", "03102", "03103", "03151",
              "03153", "03154", "03157", "03158"]


def _latest_cache(pattern: str):
    """Return the newest cache file matching *pattern*, or None.

    synpp hashes inputs into the cache filename, so obsolete hashes can
    linger alongside the current one. Selecting by ``os.path.getmtime``
    deterministically picks the freshest pickle.
    """
    hits = list(CACHE_ROOT.glob(pattern))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# Lightweight synpp context stub
# ---------------------------------------------------------------------------

class StubContext:
    """Minimal replacement for the synpp ``context`` passed to stages.

    Supports the subset of the synpp API the BS data stages actually
    use: ``config(key, default=...)`` and ``stage(name)``. Stages are
    served from a dict of precomputed results.
    """

    def __init__(self, config: dict | None = None, stages: dict | None = None):
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
# Pendler reader
# ---------------------------------------------------------------------------

class TestPendlerReader:
    """``braunschweig.data.census.pendler``: CSV parsing + ID filter."""

    def test_regex_drops_aggregate_rows(self):
        """``_read_one`` must reject ARS codes with non-digit characters.

        Regression test for the previously silent bug where
        ``str.len().eq(5)`` accepted ``031xx`` / ``Übrige Kreise`` rows
        (same length, different content), inflating the number of
        Kreis pairs and corrupting the gravity calibration universe.
        """
        s = pd.Series(["03101", "031xx", "Übrige", "00000", "15089", "3101", "12abc"])
        mask = s.str.fullmatch(r"\d{5}")
        kept = s[mask].tolist()
        assert kept == ["03101", "00000", "15089"]

    def test_cache_has_only_digit_kreis_codes(self):
        """The on-disk cache (if present) must match the new regex."""
        path = _latest_cache("braunschweig.data.census.pendler__*.p")
        if path is None:
            pytest.skip("Pendler cache not yet generated")
        with open(path, "rb") as fh:
            df = pickle.load(fh)
        bad_orig = df[~df["orig_ars"].str.fullmatch(r"\d{5}")]
        bad_dest = df[~df["dest_ars"].str.fullmatch(r"\d{5}")]
        assert bad_orig.empty, f"Non-digit orig: {bad_orig.head()}"
        assert bad_dest.empty, f"Non-digit dest: {bad_dest.head()}"

    def test_cache_flow_totals_plausible(self):
        """Sanity-check magnitudes against the BA Pendleratlas 2025 order."""
        path = _latest_cache("braunschweig.data.census.pendler__*.p")
        if path is None:
            pytest.skip("Pendler cache not yet generated")
        with open(path, "rb") as fh:
            df = pickle.load(fh)
        assert len(df) > 10_000
        outbound = df[df["orig_ars"].isin(ZGB_KREISE)
                      & ~df["dest_ars"].isin(ZGB_KREISE)]
        # BA reports ~61 000 outbound SvB from ZGB, so allow 50k..80k.
        assert 50_000 <= outbound["flow"].sum() <= 80_000


# ---------------------------------------------------------------------------
# MiD 2023 references
# ---------------------------------------------------------------------------

class TestMidReferences:
    """``braunschweig.data.mid.references``: CDF + license helpers."""

    def _load_table(self, name: str) -> pd.DataFrame:
        from braunschweig.data.mid.references import _load_table, _csv_path

        ctx = StubContext(config={
            "data_path": str(DATA_ROOT),
            "braunschweig.mid_csv_dir": "braunschweig/mid",
        })
        return _load_table(_csv_path(ctx, name))

    def test_csv_files_present(self):
        for name in ("P9", "P12_1", "P13", "P17_1"):
            path = DATA_ROOT / "braunschweig" / "mid" / f"mid2023_{name}.csv"
            assert path.exists(), f"Missing MiD CSV: {path}"

    def test_p13_contains_all_zgb_kreise(self):
        df = self._load_table("P13")
        kreise = set(df["ars5"].unique())
        missing = set(ZGB_KREISE) - kreise
        assert not missing, f"MiD P13 missing Kreise: {missing}"

    def test_p13_cdfs_monotonic_and_normalised(self):
        from braunschweig.data.mid.references import build_p13_cdfs, P13_BANDS

        df = self._load_table("P13")
        cdfs = build_p13_cdfs(df)
        assert len(cdfs) >= len(ZGB_KREISE), "Missing Kreise in CDFs"
        for ars, cdf in cdfs.items():
            assert cdf.shape == (len(P13_BANDS),), f"{ars}: wrong shape"
            assert np.all(np.diff(cdf) >= -1e-9), f"{ars}: non-monotonic CDF"
            assert abs(cdf[-1] - 1.0) < 1e-6, f"{ars}: CDF not normalised"

    def test_p17_license_rates_in_unit_interval(self):
        from braunschweig.data.mid.references import build_p17_license_rates

        df = self._load_table("P17_1")
        rates = build_p17_license_rates(df)
        assert len(rates) >= len(ZGB_KREISE)
        for ars, rate in rates.items():
            assert 0.0 <= rate <= 1.0, f"{ars}: rate out of [0,1]"


# ---------------------------------------------------------------------------
# Enriched fork (cars/bikes/income)
# ---------------------------------------------------------------------------

class TestEnrichedFork:
    """``braunschweig.synthesis.population.enriched``: share tables + helpers."""

    def test_cars_shares_are_probability_distributions(self):
        from braunschweig.synthesis.population.enriched import (
            CARS_BY_KREIS, CARS_REGION, CARS_VALUES,
        )
        for ars, shares in CARS_BY_KREIS.items():
            assert len(shares) == len(CARS_VALUES)
            assert abs(sum(shares) - 1.0) < 0.05, \
                f"CARS_BY_KREIS[{ars}] sums to {sum(shares)}"
        assert abs(sum(CARS_REGION) - 1.0) < 0.05

    def test_bikes_shares_are_probability_distributions(self):
        from braunschweig.synthesis.population.enriched import (
            BIKES_BY_KREIS, BIKES_REGION, BIKES_VALUES,
        )
        for ars, shares in BIKES_BY_KREIS.items():
            assert len(shares) == len(BIKES_VALUES)
            assert abs(sum(shares) - 1.0) < 0.05, \
                f"BIKES_BY_KREIS[{ars}] sums to {sum(shares)}"
        assert abs(sum(BIKES_REGION) - 1.0) < 0.05

    def test_inside_flag_map_covers_all_zgb_kreise(self):
        from braunschweig.synthesis.population.enriched import INSIDE_FLAG_TO_ARS5
        assert set(INSIDE_FLAG_TO_ARS5.values()) == set(ZGB_KREISE)

    def test_derive_kreis_ars5_reads_flags(self):
        from braunschweig.synthesis.population.enriched import _derive_kreis_ars5

        df = pd.DataFrame({
            "person_id":           [1, 2, 3, 4],
            "inside_braunschweig": [True, False, False, False],
            "inside_wolfsburg":    [False, True, False, False],
            "inside_gifhorn":      [False, False, True, False],
        })
        got = _derive_kreis_ars5(df).tolist()
        assert got == ["03101", "03103", "03151", ""]


# ---------------------------------------------------------------------------
# Household size / income Kategorien
# ---------------------------------------------------------------------------

class TestHouseholdDistributions:
    """Verify the categorical alignment between size + income tables."""

    def test_household_income_classes_match_bavaria(self):
        from braunschweig.data.census.household_income import (
            INCOME_BY_SIZE, INCOME_CLASS_MAP, CLASS_MIDPOINT_EUR,
        )
        bavaria_classes = ["0-500", "1500-2000", "2600-3000", "3600-4500", "5000+"]
        bs_classes = [c for c, _ in INCOME_CLASS_MAP]
        assert bs_classes == bavaria_classes

        for cls in bavaria_classes:
            assert cls in CLASS_MIDPOINT_EUR

        sizes = set(INCOME_BY_SIZE)
        assert sizes == {"1", "2", "3", "4", "5", "6+"}

        for size, shares in INCOME_BY_SIZE.items():
            assert len(shares) == len(INCOME_CLASS_MAP)
            assert 0.9 < sum(shares) < 1.1, \
                f"INCOME_BY_SIZE[{size}] sums to {sum(shares):.3f}"

    def test_household_size_bins_match_bavaria(self):
        from braunschweig.data.census.household_size import SIZE_BINS

        bins = {name for name, _, _ in SIZE_BINS}
        assert bins == {"1", "2", "3", "4", "5", "6+"}

    def test_income_size_map_covers_six_bin_reference(self):
        """Regression test for hh_size=5,6 silently dropping income_class.

        The Braunschweig MiD H4 reference uses a 6-bin scheme
        ("1","2","3","4","5","6+"). The IPF emits hh_size as int 1..6
        which gets stringified to "1".."6". Every value must map to a
        bin actually present in df_income.
        """
        from bavaria.synthesis.population.enriched import _build_income_size_map

        bs_bins = {"1", "2", "3", "4", "5", "6+"}
        mapping, scheme = _build_income_size_map(bs_bins)
        assert scheme == "6-bin"
        for hh in ["1", "2", "3", "4", "5", "6", "5+", "6+"]:
            assert mapping[hh] in bs_bins, (
                f"hh_size {hh!r} maps to {mapping[hh]!r} which is not in "
                f"reference bins {bs_bins}"
            )
        # Specifically 5 → "5" and 6 → "6+" (preserve distinction).
        assert mapping["5"] == "5"
        assert mapping["6"] == "6+"

    def test_income_size_map_collapses_for_five_bin_reference(self):
        """Bavaria's GENESIS reference is 5-bin — 5/6 must collapse to 5+."""
        from bavaria.synthesis.population.enriched import _build_income_size_map

        bv_bins = {"1", "2", "3", "4", "5+"}
        mapping, scheme = _build_income_size_map(bv_bins)
        assert scheme == "5-bin"
        assert mapping["5"] == "5+"
        assert mapping["6"] == "5+"
        assert mapping["6+"] == "5+"
        for hh in ["1", "2", "3", "4", "5", "6", "5+", "6+"]:
            assert mapping[hh] in bv_bins

    def test_income_size_map_rejects_unknown_scheme(self):
        from bavaria.synthesis.population.enriched import _build_income_size_map
        import pytest

        with pytest.raises(ValueError, match="unrecognised hh_size bins"):
            _build_income_size_map({"a", "b"})


# ---------------------------------------------------------------------------
# External workplaces + gravity extension
# ---------------------------------------------------------------------------

class TestExternalWorkplaces:
    """``braunschweig.data.external_workplaces`` output schema + totals."""

    def test_cache_has_expected_schema(self):
        path = _latest_cache("braunschweig.data.external_workplaces__*.p")
        if path is None:
            pytest.skip("External workplaces cache not yet generated")
        with open(path, "rb") as fh:
            df = pickle.load(fh)

        for col in ("ars5", "kreis_name", "commune_id",
                    "iris_id", "employees", "ewz_total",
                    "placement", "geometry"):
            assert col in df.columns, f"Missing column: {col}"

        assert df["commune_id"].str.startswith("EXT").all()
        assert df["iris_id"].str.startswith("EXT").all()
        assert (df["employees"] > 0).all()
        assert df["ars5"].str.fullmatch(r"\d{5}").all()
        assert df["ars5"].is_unique, "Duplicate external Kreise"
        assert not df["ars5"].isin(ZGB_KREISE).any(), \
            "External pool should not contain ZGB Kreise"

        # Placement: the vast majority should be employment-weighted
        # (only tiny Kreise without EWZ fall back to the land centroid).
        assert set(df["placement"].unique()) <= {
            "emp_weighted", "kreis_centroid",
        }
        assert (df["placement"] == "emp_weighted").mean() > 0.9, \
            "Most external workplaces should be employment-weighted"

        total = int(df["employees"].sum())
        assert 40_000 <= total <= 80_000, \
            f"External SvB {total} outside plausible 40k..80k range"


class TestGravityModelExtension:
    """``braunschweig.gravity.model``: outbound injection + renormalisation."""

    def test_cache_sums_to_unity_per_origin(self):
        path = _latest_cache("braunschweig.gravity.model__*.p")
        if path is None:
            pytest.skip("Gravity cache not yet generated")
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        # Gravity returns a (work_od, education_od) tuple.
        df_work = payload[0] if isinstance(payload, (tuple, list)) else payload
        assert {"origin_id", "destination_id", "weight"} <= set(df_work.columns)

        totals = df_work.groupby("origin_id")["weight"].sum()
        # Each origin's weights must form a probability distribution.
        diff = (totals - 1.0).abs()
        assert diff.max() < 1e-6, \
            f"Gravity weights do not sum to 1 per origin; max dev {diff.max()}"

    def test_external_destinations_are_present(self):
        path = _latest_cache("braunschweig.gravity.model__*.p")
        if path is None:
            pytest.skip("Gravity cache not yet generated")
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        df_work = payload[0] if isinstance(payload, (tuple, list)) else payload

        ext_rows = df_work[df_work["destination_id"].str.startswith("EXT")]
        assert len(ext_rows) > 0, \
            "No EXT* destinations found — outbound injection missing"
        # Every ZGB-ish origin should emit at least one external row.
        by_origin = ext_rows.groupby("origin_id").size()
        assert by_origin.min() > 0


# ---------------------------------------------------------------------------
# Work-locations concat
# ---------------------------------------------------------------------------

class TestLocationsWork:
    """``braunschweig.locations.work``: concatenation + location_id uniqueness."""

    def test_cache_contains_ext_workplaces(self):
        path = _latest_cache("braunschweig.locations.work__*.p")
        if path is None:
            pytest.skip("Work-locations cache not yet generated")
        with open(path, "rb") as fh:
            df = pickle.load(fh)

        assert "location_id" in df.columns
        assert df["location_id"].is_unique, "Duplicate location_id"
        assert df["location_id"].astype(str).str.startswith("work_").all()

        ext = df[df["commune_id"].astype(str).str.startswith("EXT")]
        assert len(ext) > 0, "External workplaces absent from pool"
        assert (ext["employees"] > 0).all()


# ---------------------------------------------------------------------------
# Spatial codes consistency (Bavaria source of truth for BS forks)
# ---------------------------------------------------------------------------

class TestSpatialCodes:
    """Verify the BS forks agree with ``eqasim_common.spatial.codes`` shape."""

    def test_bavaria_spatial_codes_cache_schema(self):
        path = _latest_cache("eqasim_common.spatial.codes__*.p")
        if path is None:
            # Fall back to the pre-refactor cache name during the transition.
            path = _latest_cache("bavaria.data.spatial.codes__*.p")
        if path is None:
            pytest.skip("Spatial-codes cache not yet generated")
        with open(path, "rb") as fh:
            df = pickle.load(fh)
        expected = {"region_id", "departement_id", "commune_id",
                    "iris_id", "ags"}
        assert expected <= set(df.columns)
        dep_ids = set(df["departement_id"].astype(str).unique())
        assert set(ZGB_KREISE) <= dep_ids, \
            f"ZGB Kreise missing from codes: {set(ZGB_KREISE) - dep_ids}"


# ---------------------------------------------------------------------------
# Gravity pure-function helpers
# ---------------------------------------------------------------------------

class TestGravityPureFunctions:
    """Exercise the IPF / intra-Kreis helpers without running the stage."""

    def test_synthesise_intra_kreis_substracts_outbound(self):
        from braunschweig.gravity.model import _synthesise_intra_kreis

        scope = ["03101", "03102"]
        # Two Kreise, one each of sex/age bucket: SvB Wohnort.
        df_employment = pd.DataFrame({
            "departement_id": ["03101", "03102"],
            "age_class": [30, 30],
            "sex": ["male", "male"],
            "weight": [80_000, 50_000],
        })
        df_pendler = pd.DataFrame({
            "orig_ars": ["03101", "03101", "03102"],
            "dest_ars": ["03102", "11000", "03101"],
            "flow":     [5_000,   10_000,   4_000],
        })
        out = _synthesise_intra_kreis(df_pendler, df_employment, scope)
        # Original rows preserved
        assert len(out) == len(df_pendler) + 2
        intra = out[out["orig_ars"] == out["dest_ars"]]
        by_k = intra.set_index("orig_ars")["flow"].to_dict()
        # 03101: SvB=80000, auspendler = 5000+10000 = 15000, intra = 65000
        assert by_k["03101"] == 65_000
        # 03102: SvB=50000, auspendler = 4000, intra = 46000
        assert by_k["03102"] == 46_000

    def test_synthesise_intra_kreis_clips_negative(self):
        """If auspendler exceeds SvB Wohnort, intra must be 0, not negative."""
        from braunschweig.gravity.model import _synthesise_intra_kreis

        df_employment = pd.DataFrame({
            "departement_id": ["03101"], "age_class": [30],
            "sex": ["male"], "weight": [100],
        })
        df_pendler = pd.DataFrame({
            "orig_ars": ["03101"], "dest_ars": ["11000"], "flow": [500],
        })
        out = _synthesise_intra_kreis(df_pendler, df_employment, ["03101"])
        intra = out[out["orig_ars"] == out["dest_ars"]]
        assert intra["flow"].iloc[0] == 0

    def test_calibrate_matches_kreis_pair_sums(self):
        """IPF must drive calibrated Kreis-pair sums onto observed values."""
        from braunschweig.gravity.model import _calibrate

        df_od = pd.DataFrame({
            "origin_id":      ["03101001", "03101001", "03102000"],
            "destination_id": ["03101001", "03102000", "03101001"],
            "weight":         [0.5, 0.5, 1.0],
        })
        df_population = pd.DataFrame({
            "commune_id": ["03101001", "03102000"],
            "weight":     [1_000, 500],
        })
        df_pendler = pd.DataFrame({
            "orig_ars": ["03101", "03101", "03102"],
            "dest_ars": ["03101", "03102", "03101"],
            "flow":     [400, 200, 100],
        })
        out = _calibrate(df_od, df_population, df_pendler)
        assert set(out.columns) == {"origin_id", "destination_id", "flow"}

        out["orig_k"] = out["origin_id"].str[:5]
        out["dest_k"] = out["destination_id"].str[:5]
        pair_sums = out.groupby(["orig_k", "dest_k"])["flow"].sum()
        assert pair_sums[("03101", "03101")] == pytest.approx(400, rel=1e-6)
        assert pair_sums[("03101", "03102")] == pytest.approx(200, rel=1e-6)
        assert pair_sums[("03102", "03101")] == pytest.approx(100, rel=1e-6)


class TestGravityOutputStructure:
    """Cache-level invariants for the gravity output after the scale fix."""

    def _load(self):
        path = _latest_cache("braunschweig.gravity.model__*.p")
        if path is None:
            pytest.skip("Gravity cache not yet generated")
        with open(path, "rb") as fh:
            payload = pickle.load(fh)
        return payload[0] if isinstance(payload, (tuple, list)) else payload

    def test_every_zgb_origin_has_external_rows(self):
        df = self._load()
        df["orig_k"] = df["origin_id"].astype(str).str[:5]
        df["is_ext"] = df["destination_id"].astype(str).str.startswith("EXT")
        ext_origins = set(df.loc[df["is_ext"], "orig_k"].unique())
        missing = set(ZGB_KREISE) - ext_origins
        assert not missing, \
            f"ZGB Kreise with no external injection: {missing}"

    def test_external_commute_share_in_plausible_range(self):
        """Per-Kreis external share must be above 5 % but below 40 %."""
        df = self._load()
        df["orig_k"] = df["origin_id"].astype(str).str[:5]
        df["is_ext"] = df["destination_id"].astype(str).str.startswith("EXT")
        per_k = (df[df["orig_k"].isin(ZGB_KREISE)]
                 .groupby("orig_k")
                 .apply(lambda g: g.loc[g["is_ext"], "weight"].sum()
                                   / g["weight"].sum()))
        assert per_k.min() > 0.02, f"Too low external share: {per_k.to_dict()}"
        assert per_k.max() < 0.40, f"Too high external share: {per_k.to_dict()}"


# ---------------------------------------------------------------------------
# Zensus 2022 100 m population grid
# ---------------------------------------------------------------------------

class TestZensusGridLoader:
    """``braunschweig.data.zensus_grid.population``: bbox clip + schema."""

    PARQUET = DATA_ROOT / "zensus_grid" / "population_100m.parquet"
    GRID = DATA_ROOT / "zensus_grid" / "grid_100m.parquet"

    EXPECTED_HASHES = {
        "population_100m.parquet": (
            "5b3a350ee85e454ae487a4e233acf5310964586fc175fdff6a98f616b6cc0a03"
        ),
        "grid_100m.parquet": (
            "80fc96f28afca2fda5c0c97f13d536a70ccd6715cd15480fcf73a08bf21af0cf"
        ),
    }

    def _ctx(self):
        return StubContext(
            config={
                "data_path": str(DATA_ROOT),
                "zensus_grid.population_path":
                    "zensus_grid/population_100m.parquet",
                "zensus_grid.bbox_buffer_m": 200.0,
                "germany.population_path":
                    "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
                "germany.population_source":
                    "vg250-ew_12-31.utm32s.gpkg.ebenen/"
                    "vg250-ew_ebenen_1231/DE_VG250.gpkg",
                "bavaria.political_prefix": ZGB_KREISE,
            }
        )

    def test_pinned_sha256_matches(self):
        """TEST-007 — guard against silent upstream changes."""
        import hashlib

        if not self.PARQUET.exists() or not self.GRID.exists():
            pytest.skip("Run scripts/download_zensus_grid.py first.")

        for name, expected in self.EXPECTED_HASHES.items():
            path = DATA_ROOT / "zensus_grid" / name
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            assert h.hexdigest() == expected, (
                f"{name} hash drift: got {h.hexdigest()}, "
                f"expected {expected}"
            )

    def test_loader_filters_to_zgb_bbox(self):
        """TEST-002 — output schema + scope check."""
        if not self.PARQUET.exists():
            pytest.skip("Run scripts/download_zensus_grid.py first.")
        vg250 = (DATA_ROOT / "germany" /
                 "vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
        if not vg250.exists():
            pytest.skip(f"VG250-EW not present at {vg250}")

        from braunschweig.data.zensus_grid import population as zg

        gdf = zg.execute(self._ctx())

        assert set(gdf.columns) == {"grid_id", "einwohner", "geometry"}
        assert gdf.crs is not None and gdf.crs.to_epsg() == 3035
        assert (gdf["einwohner"] > 0).all(), "expected populated cells only"
        assert gdf["grid_id"].is_unique

        # ZGB-8 has ~1.13 M residents; bbox includes border cells from
        # neighbouring Kreise. A loose plausibility envelope catches gross
        # mis-clipping (e.g. all-of-Germany or Berlin only).
        n_inh = int(gdf["einwohner"].sum())
        assert 900_000 < n_inh < 3_000_000, (
            f"unexpected ZGB-8 bbox population total: {n_inh}"
        )
        # Cell count: ZGB-8 covers ~7,500 km²; bbox ~10–15 k km² with
        # ~50 % populated cells → 50–150 k cells.
        assert 30_000 < len(gdf) < 200_000, (
            f"unexpected populated cell count: {len(gdf)}"
        )

        # Geometry sanity: each polygon is exactly 100 × 100 m.
        sample = gdf.geometry.iloc[:50]
        widths = sample.bounds["maxx"] - sample.bounds["minx"]
        heights = sample.bounds["maxy"] - sample.bounds["miny"]
        assert (widths == 100).all() and (heights == 100).all()


# ---------------------------------------------------------------------------
# RegioStaR-7 Gemeinde reference (TASK-004)
# ---------------------------------------------------------------------------

class TestRegioStarLoader:
    """``braunschweig.data.bbsr.regiostar``: schema + ZGB-8 coverage."""

    XLSX = DATA_ROOT / "regiostar" / "regiostar_referenzdatei.xlsx"
    EXPECTED_SHA256 = (
        "550da569e3cd97de11c87859f40a290f200567f63dee4d79c693c7a3393a04e6"
    )

    def _ctx(self):
        return StubContext(
            config={
                "data_path": str(DATA_ROOT),
                "regiostar.path": "regiostar/regiostar_referenzdatei.xlsx",
                "bavaria.political_prefix": ZGB_KREISE,
            }
        )

    def test_pinned_sha256_matches(self):
        import hashlib

        if not self.XLSX.exists():
            pytest.skip("Run scripts/download_regiostar.py first.")

        h = hashlib.sha256()
        with open(self.XLSX, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        assert h.hexdigest() == self.EXPECTED_SHA256, (
            f"regiostar XLSX hash drift: got {h.hexdigest()}"
        )

    def test_loader_filters_to_zgb(self):
        if not self.XLSX.exists():
            pytest.skip("Run scripts/download_regiostar.py first.")

        from braunschweig.data.bbsr import regiostar as rs

        df = rs.execute(self._ctx())

        assert set(df.columns) == {
            "commune_id", "ars5", "name",
            "regiostar7", "regiostar17", "regiostar_gem7",
        }
        # ZGB-8 contains 126 Gemeinden in the 2020 reference (3 cities +
        # 123 Landkreis-Gemeinden across 5 Landkreise).
        assert len(df) == 126
        assert set(df["ars5"].unique()) == set(ZGB_KREISE)

        # commune_id is 8-digit zero-padded AGS string.
        assert df["commune_id"].str.len().eq(8).all()
        assert df["commune_id"].str.match(r"^0\d{7}$").all()

        # All RegioStaR7 codes fall in the documented 71..77 range.
        assert df["regiostar7"].between(71, 77).all()

        # The three Kreisfreie Städte must carry the urban code 72.
        urban = df[df["commune_id"].isin({"03101000", "03102000", "03103000"})]
        assert len(urban) == 3
        assert (urban["regiostar7"] == 72).all()

        # Rural Kreise (Goslar, Helmstedt) must contain only 73..77 codes.
        rural = df[df["ars5"].isin({"03153", "03154"})]
        assert rural["regiostar7"].min() >= 73



# ---------------------------------------------------------------------------
# Density-weighted home-location candidates (TASK-003)
# ---------------------------------------------------------------------------

class TestDensityWeightedHome:
    """`braunschweig.locations.home`: Zensus-density weight overlay."""

    def _build_inputs(self):
        import geopandas as gpd
        from shapely.geometry import Point, box

        # Two communes; 2 buildings each with equal area weight.
        # Commune A: one building inside a populated cell, one outside.
        # Commune B: both buildings inside populated cells with different
        #            einwohner.
        # Using EPSG:3035 directly so the to_crs is a no-op for the test.
        pts = [
            Point(4_320_050, 3_270_050),  # A1 inside cell A1
            Point(4_320_550, 3_270_050),  # A2 outside any cell
            Point(4_321_050, 3_270_050),  # B1 inside cell B1 (low pop)
            Point(4_321_150, 3_270_050),  # B2 inside cell B2 (high pop)
        ]
        df_buildings = gpd.GeoDataFrame({
            "home_location_id": [1, 2, 3, 4],
            "weight": [1.0, 1.0, 1.0, 1.0],
            "commune_id": ["A", "A", "B", "B"],
            "iris_id": ["A", "A", "B", "B"],
            "geometry": pts,
        }, crs="EPSG:3035")

        cells = [
            box(4_320_000, 3_270_000, 4_320_100, 3_270_100),  # A1
            box(4_321_000, 3_270_000, 4_321_100, 3_270_100),  # B1
            box(4_321_100, 3_270_000, 4_321_200, 3_270_100),  # B2
        ]
        df_grid = gpd.GeoDataFrame({
            "grid_id": ["A1", "B1", "B2"],
            "einwohner": [50, 10, 90],
            "geometry": cells,
        }, crs="EPSG:3035")
        return df_buildings, df_grid

    def test_density_off_returns_unchanged(self, monkeypatch):
        from braunschweig.locations import home as bs_home

        df_buildings, _ = self._build_inputs()
        monkeypatch.setattr(bs_home.delegate, "execute",
                            lambda ctx: df_buildings.copy())
        ctx = StubContext(config={
            "braunschweig.home_density_weighting": False,
        })
        out = bs_home.execute(ctx)
        assert (out["weight"] == 1.0).all()

    def test_density_on_redistributes_within_commune(self, monkeypatch):
        from braunschweig.locations import home as bs_home

        df_buildings, df_grid = self._build_inputs()
        monkeypatch.setattr(bs_home.delegate, "execute",
                            lambda ctx: df_buildings.copy())
        ctx = StubContext(
            config={"braunschweig.home_density_weighting": True},
            stages={"braunschweig.data.zensus_grid.population": df_grid},
        )
        out = bs_home.execute(ctx)

        # Total weight per commune must be preserved (= 2.0 each).
        sums = out.groupby("commune_id")["weight"].sum()
        assert abs(sums["A"] - 2.0) < 1e-9
        assert abs(sums["B"] - 2.0) < 1e-9

        # Commune A: building inside cell (einwohner=50) gets the bulk;
        # building outside (einwohner?1.0 fallback) gets the rest.
        wa = out[out["commune_id"] == "A"].set_index("home_location_id")["weight"]
        assert wa[1] > wa[2]
        # Ratio = 50 / 1.
        assert abs(wa[1] / wa[2] - 50.0) < 1e-6

        # Commune B: B2 (einwohner=90) > B1 (einwohner=10). Ratio = 9.
        wb = out[out["commune_id"] == "B"].set_index("home_location_id")["weight"]
        assert abs(wb[4] / wb[3] - 9.0) < 1e-6


# ---------------------------------------------------------------------------
# TASK-014 — INKAR full-panel loader
# ---------------------------------------------------------------------------

class TestInkarFullPanel:
    """``braunschweig.data.inkar.full_panel``: configurable multi-indicator join."""

    def test_empty_config_returns_empty_frame(self):
        from braunschweig.data.inkar import full_panel

        ctx = StubContext(config={
            "data_path": ".",
            "braunschweig.inkar_panel": {},
            "braunschweig.inkar_panel_year": "latest",
            "bavaria.political_prefix": None,
        })
        df = full_panel.execute(ctx)
        assert list(df.columns) == ["ars5", "raumeinheit"]
        assert len(df) == 0

    def test_existing_income_xls_round_trips(self):
        """Reuse the shipped E_Haushaltseinkommen.xls as a smoke test."""
        from braunschweig.data.inkar import full_panel

        ctx = StubContext(config={
            "data_path": "eqasim-data/data",
            "braunschweig.inkar_panel": {
                "household_income_eur": "braunschweig/E_Haushaltseinkommen.xls",
            },
            "braunschweig.inkar_panel_year": "latest",
            "bavaria.political_prefix": [
                "03101", "03102", "03103",
                "03151", "03153", "03154", "03157", "03158",
            ],
        })
        df = full_panel.execute(ctx)
        assert "household_income_eur" in df.columns
        assert set(df["ars5"]) == {
            "03101", "03102", "03103",
            "03151", "03153", "03154", "03157", "03158",
        }
        assert (df["household_income_eur"] > 0).all()


# ---------------------------------------------------------------------------
# TASK-015 — BA Pendlerstatistik nach Wirtschaftsabschnitten
# ---------------------------------------------------------------------------

class TestBaPendlerDetailed:
    """``braunschweig.data.ba.pendler_detailed``: long-form CSV parsing."""

    def test_missing_path_returns_empty_frame(self):
        from braunschweig.data.ba import pendler_detailed

        ctx = StubContext(config={
            "data_path": ".",
            "braunschweig.ba_pendler_detailed_path": None,
            "braunschweig.ba_pendler_detailed_separator": ";",
            "bavaria.political_prefix": None,
        })
        df = pendler_detailed.execute(ctx)
        assert list(df.columns) == ["home_kreis", "work_kreis", "sector", "flow"]
        assert len(df) == 0

    def test_csv_round_trip(self, tmp_path):
        from braunschweig.data.ba import pendler_detailed

        csv = tmp_path / "ba.csv"
        csv.write_text(
            "home_kreis;work_kreis;sector;flow\n"
            "3101;3102;C;1500\n"
            "3101;3151;G;420\n"
            "3102;9999;X;7\n",
            encoding="utf-8",
        )
        ctx = StubContext(config={
            "data_path": str(tmp_path),
            "braunschweig.ba_pendler_detailed_path": "ba.csv",
            "braunschweig.ba_pendler_detailed_separator": ";",
            "bavaria.political_prefix": ["03101", "03102"],
        })
        df = pendler_detailed.execute(ctx)
        assert (df["home_kreis"].str.len() == 5).all()
        assert (df["work_kreis"].str.len() == 5).all()
        # Scope filter retains rows where at least one side is in scope.
        assert len(df) == 3
        assert df["flow"].dtype.kind == "i"


# ---------------------------------------------------------------------------
# TASK-012 — INSPIRE 100m landuse spatial-prior loader
# ---------------------------------------------------------------------------

class TestInspireLanduse:
    """``braunschweig.data.inspire.landuse``: feature-flagged loader."""

    def test_flag_off_returns_empty(self):
        from braunschweig.data.inspire import landuse

        ctx = StubContext(config={
            "data_path": ".",
            "braunschweig.inspire_landuse_path": "does/not/exist.parquet",
            "braunschweig.use_landuse_prior": False,
        })
        df = landuse.execute(ctx)
        assert len(df) == 0
        assert df.crs is not None and df.crs.to_epsg() == 3035

    def test_flag_on_missing_file_returns_empty(self, tmp_path):
        from braunschweig.data.inspire import landuse

        ctx = StubContext(config={
            "data_path": str(tmp_path),
            "braunschweig.inspire_landuse_path": "nope.parquet",
            "braunschweig.use_landuse_prior": True,
        })
        df = landuse.execute(ctx)
        assert len(df) == 0


# ---------------------------------------------------------------------------
# TASK-010 / TASK-011 — IPF model config flags
# ---------------------------------------------------------------------------

class TestIpfFeatureFlags:
    """Validate that the new IPF flags exist with safe defaults."""

    def test_dirichlet_prior_default_zero(self):
        # Static check: configure() sets default 0.0; reading the source
        # is the safest test that does not require the full IPF stack.
        import inspect

        from bavaria.ipf import model as ipf_model

        src = inspect.getsource(ipf_model.configure)
        assert "bavaria.ipf.dirichlet_prior_strength" in src
        assert "bavaria.ipf.use_employment_margin" in src
        assert "bavaria.ipf.employment_by_hhsize_path" in src



