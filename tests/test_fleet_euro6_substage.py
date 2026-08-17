"""Tests for Task B5: Euro-6 substage (6ab/6d-temp/6d) conditional draw + HBEFA
mapping.

Coverage (matches the task brief's Step 1 list):

  (a) substage pmf composition: diesel row vs ``all - diesel``; national FZ 27.4
      fallback when the Kreis row is missing or all-zero.
  (b) a euro6 diesel car in a 6d-heavy Kreis draws ``euro6d`` more often than in
      a 6ab-heavy Kreis (seeded, integration through ``sample_fleet``).
  (c) electric cars NEVER get a substage (stay ``"electric"``); euro1..5 and
      phev/hybrid euro classes are untouched.
  (d) flag OFF, or the substage data effectively absent, reproduces a
      byte-identical seeded run (no extra RNG consumed).
  (e) HBEFA type_ids are distinct for the three substages and pass validation.
  (f) the realised-margin validator is not flagged on the euro_class dimension
      (the ``_effective_expected`` mirror matches the actual draw).
  (g) an all-zero per-Kreis substage falls back to the national pmf, and an
      all-zero national pmf falls back to plain "euro6" -- no NaN anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402
from braunschweig.synthesis.vehicles import hbefa  # noqa: E402

DATA_PATH = str(DATA)
SUBSTAGES = list(ft.EURO6_SUBSTAGE_LABELS)  # ("euro6ab", "euro6dtemp", "euro6d")
_ZGB = list(ft.ZGB_KREISE_AGS5)


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def _euro_row(kreis_ags5: str, teil: str, *, euro1=500, euro2=1000, euro3=2000,
              euro4=3000, euro5=5000, euro6=10_000, other=500,
              euro6d=0.0, euro6dtemp=0.0, euro6ab=0.0) -> dict:
    total = euro1 + euro2 + euro3 + euro4 + euro5 + euro6 + other
    return {
        "kreis_ags5": kreis_ags5, "kreis_name": kreis_ags5, "stichtag": "2025-01-01",
        "teil": teil,
        "euro1": euro1, "euro2": euro2, "euro3": euro3, "euro4": euro4,
        "euro5": euro5, "euro6": euro6, "other": other, "total": total,
        "euro6d": euro6d, "euro6dtemp": euro6dtemp, "euro6ab": euro6ab,
    }


def _write_derived(tmp_path: Path, name: str, df: pd.DataFrame) -> str:
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def _mirror_real_data_with_extras(tmp_path: Path,
                                  extra_files: "dict[str, pd.DataFrame]",
                                  omit_files: "tuple[str, ...]" = ()) -> str:
    """Symlink the real derived CSV directory into ``tmp_path`` and overlay
    ``extra_files`` (name -> DataFrame) on top, mirroring the pattern used by
    ``tests/test_fleet_b1_euro_kreis.py``. Skips the test when the real derived
    data directory is absent (server-generated CSVs may not be present locally
    beyond the base fleet tables).

    ``omit_files`` names CSVs that must NOT appear in the mirror. An
    absent-data scenario has to state its absences explicitly: the real derived
    directory is a moving target -- once a table is generated and committed, a
    test that relied on it being missing would silently start testing the
    present-data path instead (which is exactly what happened when the eight
    new KBA tables landed)."""
    if not (DATA / "braunschweig" / "kba" / "derived").exists():
        pytest.skip("real derived data directory absent")
    derived = tmp_path / "braunschweig" / "kba" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    real_derived = DATA / "braunschweig" / "kba" / "derived"
    for src in real_derived.glob("*.csv"):
        # NEVER symlink a file we are about to overlay: to_csv(derived/name) would
        # then write THROUGH the symlink into the real committed derived CSV and
        # corrupt it (the write-through-a-link data-loss class). Overlaid names are
        # written fresh below instead.
        if src.name in extra_files or src.name in omit_files:
            continue
        dst = derived / src.name
        if dst.exists():
            continue
        try:
            dst.symlink_to(src)
        except OSError:
            import shutil
            shutil.copy2(src, dst)
    for name, df in extra_files.items():
        df.to_csv(derived / name, index=False)
    return str(tmp_path)


AGS_A = _ZGB[0]  # well-defined diesel-vs-nondiesel substage composition
AGS_B = _ZGB[1]  # all-zero substage counts -> must fall back to national


def _make_kreis_euro_df_for_composition_tests() -> pd.DataFrame:
    rows = [
        # Kreis A: diesel row substage sums to 2000 = its euro6 total;
        # non-diesel (all - diesel) sums to 6000 = 8000 - 2000.
        _euro_row(AGS_A, "all", euro6=8_000,
                  euro6d=5_000, euro6dtemp=1_600, euro6ab=1_400),
        _euro_row(AGS_A, "diesel", euro6=2_000,
                  euro6d=1_400, euro6dtemp=400, euro6ab=200),
        # Kreis B: all-zero substage counts (pre-Task-B4 zero-fill or a
        # genuinely Euro-6-substage-free Kreis) -> omitted, national fallback.
        _euro_row(AGS_B, "all", euro6=5_000, euro6d=0.0, euro6dtemp=0.0, euro6ab=0.0),
        _euro_row(AGS_B, "diesel", euro6=1_500, euro6d=0.0, euro6dtemp=0.0, euro6ab=0.0),
    ]
    for ags in _ZGB[2:]:
        rows.append(_euro_row(ags, "all", euro6=4_000,
                               euro6d=2_000, euro6dtemp=1_000, euro6ab=1_000))
        rows.append(_euro_row(ags, "diesel", euro6=1_000,
                               euro6d=500, euro6dtemp=250, euro6ab=250))
    return pd.DataFrame(rows)


def _make_national_substage_df(rows: "list[dict]") -> pd.DataFrame:
    df = pd.DataFrame(rows)
    totals = df.groupby("fuel")["count"].transform("sum")
    df["share"] = df["count"] / totals.where(totals > 0, other=1.0)
    df["stichtag"] = "2025-01-01"
    return df


def _national_substage_rows_with_zero_gas() -> "list[dict]":
    """petrol/diesel have a real substage split; gas has NO Euro-6 registration
    at all (all-zero total) -- used to test the terminal plain-"euro6" fallback."""
    return [
        {"fuel": "petrol", "substage": "euro6ab", "count": 100},
        {"fuel": "petrol", "substage": "euro6dtemp", "count": 300},
        {"fuel": "petrol", "substage": "euro6d", "count": 600},
        {"fuel": "diesel", "substage": "euro6ab", "count": 500},
        {"fuel": "diesel", "substage": "euro6dtemp", "count": 200},
        {"fuel": "diesel", "substage": "euro6d", "count": 300},
        {"fuel": "gas", "substage": "euro6ab", "count": 0},
        {"fuel": "gas", "substage": "euro6dtemp", "count": 0},
        {"fuel": "gas", "substage": "euro6d", "count": 0},
    ]


# --------------------------------------------------------------------------- #
# (a) + (g): unit tests for the composition function + the fallback chain
# --------------------------------------------------------------------------- #
class TestSubstageComposition:
    """``_euro6_substage_given_kreis`` -- pure per-Kreis composition (no
    FleetSampler / no other CSVs needed beyond kba_kreis_euro.csv)."""

    def test_diesel_row_composition(self, tmp_path):
        dp = _write_derived(tmp_path, "kba_kreis_euro.csv",
                             _make_kreis_euro_df_for_composition_tests())
        result = fs._euro6_substage_given_kreis(dp)
        pmf = result[(AGS_A, "diesel")]
        expected = np.array([200, 400, 1_400], dtype=float) / 2_000.0  # ab, dtemp, d
        np.testing.assert_allclose(pmf, expected)

    def test_non_diesel_composition_is_all_minus_diesel(self, tmp_path):
        dp = _write_derived(tmp_path, "kba_kreis_euro.csv",
                             _make_kreis_euro_df_for_composition_tests())
        result = fs._euro6_substage_given_kreis(dp)
        expected = np.array([1_200, 1_200, 3_600], dtype=float) / 6_000.0
        for pt in ("petrol", "gas", "other"):
            np.testing.assert_allclose(result[(AGS_A, pt)], expected,
                                        err_msg=f"powertrain={pt}")

    def test_all_zero_kreis_cell_is_omitted(self, tmp_path):
        """A Kreis whose substage counts are all-zero must NOT appear in the
        per-Kreis dict -- the caller falls through to the national pmf."""
        dp = _write_derived(tmp_path, "kba_kreis_euro.csv",
                             _make_kreis_euro_df_for_composition_tests())
        result = fs._euro6_substage_given_kreis(dp)
        assert (AGS_B, "diesel") not in result
        for pt in ("petrol", "gas", "other"):
            assert (AGS_B, pt) not in result

    def test_kba_kreis_euro_absent_returns_empty_dict(self, tmp_path):
        """No kba_kreis_euro.csv at all -> empty dict (never None)."""
        d = tmp_path / "braunschweig" / "kba" / "derived"
        d.mkdir(parents=True)
        result = fs._euro6_substage_given_kreis(str(tmp_path))
        assert result == {}


class TestEuro6SubstageModelFallbackChain:
    """``Euro6SubstageModel`` -- the full per-Kreis -> national -> absent chain."""

    def _model(self, tmp_path, with_kreis=True, with_national=True) -> "fs.Euro6SubstageModel":
        if with_kreis:
            _write_derived(tmp_path, "kba_kreis_euro.csv",
                            _make_kreis_euro_df_for_composition_tests())
        else:
            (tmp_path / "braunschweig" / "kba" / "derived").mkdir(parents=True, exist_ok=True)
        if with_national:
            _write_derived(tmp_path, "kba_fuel_euro6_substage_nds.csv",
                            _make_national_substage_df(_national_substage_rows_with_zero_gas()))
        return fs.Euro6SubstageModel.from_data_path(str(tmp_path))

    def test_kreis_available_is_primary(self, tmp_path):
        model = self._model(tmp_path)
        pmf = model.substage_pmf(AGS_A, "diesel")
        expected = np.array([200, 400, 1_400]) / 2_000.0
        np.testing.assert_allclose(pmf, expected)
        assert model._kreis_primary == 1
        assert model._national_fallback == 0
        assert model._absent_fallback == 0

    def test_kreis_zero_falls_back_to_national(self, tmp_path):
        model = self._model(tmp_path)
        pmf = model.substage_pmf(AGS_B, "diesel")
        expected = np.array([500, 200, 300]) / 1_000.0  # national diesel pmf (ab, dtemp, d)
        np.testing.assert_allclose(pmf, expected)
        assert model._kreis_primary == 0
        assert model._national_fallback == 1

    def test_national_also_zero_returns_none_no_nan(self, tmp_path):
        """'gas' has zero national counts too -> substage_pmf returns None (the
        caller keeps plain "euro6"); no NaN is ever produced."""
        model = self._model(tmp_path)
        pmf = model.substage_pmf(AGS_B, "gas")
        assert pmf is None
        assert model._absent_fallback == 1
        for v in list(model.kreis_pmf.values()) + list(model.national_pmf.values()):
            assert np.isfinite(v).all(), "no NaN/inf may ever appear in a stored substage pmf"

    def test_both_sources_absent_everything_is_none(self, tmp_path):
        model = self._model(tmp_path, with_kreis=False, with_national=False)
        assert model.kreis_pmf == {}
        assert model.national_pmf == {}
        assert model.substage_pmf(AGS_A, "diesel") is None
        assert model._absent_fallback == 1

    def test_pmf_for_is_pure_and_does_not_increment_counters(self, tmp_path):
        """The validator mirror lookup (``pmf_for``) must not double-count the
        fallback rate that ``substage_pmf`` already tracks at draw time."""
        model = self._model(tmp_path)
        before = (model._kreis_primary, model._national_fallback, model._absent_fallback)
        assert model.pmf_for(AGS_A, "diesel") is not None
        assert model.pmf_for(AGS_B, "diesel") is not None  # national fallback
        assert model.pmf_for(AGS_B, "gas") is None  # fully absent
        after = (model._kreis_primary, model._national_fallback, model._absent_fallback)
        assert before == after


# --------------------------------------------------------------------------- #
# Full-population synthetic household car frame (mirrors test_fleet_sampling_de.py)
# --------------------------------------------------------------------------- #
def _make_cars(n_per_kreis: int = 3000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    for kreis in ft.ZGB_KREISE_AGS5:
        for _ in range(n_per_kreis):
            rows.append({
                "economic_status": rng.choice(statuses),
                "kreis_ags5": kreis,
                "gemeinde": np.nan,
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    return pd.DataFrame(rows)


AGS_6D_HEAVY = _ZGB[0]
AGS_6AB_HEAVY = _ZGB[1]


def _make_kreis_euro_df_substage_contrast() -> pd.DataFrame:
    """8-Kreis fixture with two Kreise carrying OPPOSITE Euro-6 substage
    compositions on a dominant euro6 share (so plenty of euro6 diesel cars are
    drawn and their substage split is clearly attributable to the Kreis)."""
    rows = []
    for ags in ft.ZGB_KREISE_AGS5:
        if ags == AGS_6D_HEAVY:
            d_ab, d_dtemp, d_d = 200.0, 600.0, 7_200.0
        elif ags == AGS_6AB_HEAVY:
            d_ab, d_dtemp, d_d = 7_200.0, 600.0, 200.0
        else:
            d_ab, d_dtemp, d_d = 2_000.0, 3_000.0, 3_000.0
        a_ab, a_dtemp, a_d = d_ab + 500.0, d_dtemp + 500.0, d_d + 1_000.0
        rows.append(_euro_row(
            ags, "all", euro1=200, euro2=200, euro3=200, euro4=300, euro5=300,
            other=100, euro6=a_ab + a_dtemp + a_d,
            euro6ab=a_ab, euro6dtemp=a_dtemp, euro6d=a_d))
        rows.append(_euro_row(
            ags, "diesel", euro1=100, euro2=100, euro3=100, euro4=100, euro5=100,
            other=50, euro6=d_ab + d_dtemp + d_d,
            euro6ab=d_ab, euro6dtemp=d_dtemp, euro6d=d_d))
    return pd.DataFrame(rows)


def _make_national_substage_df_full() -> pd.DataFrame:
    rows = [
        {"fuel": "petrol", "substage": "euro6ab", "count": 3_000},
        {"fuel": "petrol", "substage": "euro6dtemp", "count": 3_000},
        {"fuel": "petrol", "substage": "euro6d", "count": 4_000},
        {"fuel": "diesel", "substage": "euro6ab", "count": 3_000},
        {"fuel": "diesel", "substage": "euro6dtemp", "count": 3_000},
        {"fuel": "diesel", "substage": "euro6d", "count": 4_000},
        {"fuel": "gas", "substage": "euro6ab", "count": 3_000},
        {"fuel": "gas", "substage": "euro6dtemp", "count": 3_000},
        {"fuel": "gas", "substage": "euro6d", "count": 4_000},
        {"fuel": "other", "substage": "euro6ab", "count": 3_000},
        {"fuel": "other", "substage": "euro6dtemp", "count": 3_000},
        {"fuel": "other", "substage": "euro6d", "count": 4_000},
    ]
    return _make_national_substage_df(rows)


@pytest.fixture(scope="module")
def contrast_data_path(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("b5_substage_contrast")
    return _mirror_real_data_with_extras(tmp_path, {
        "kba_kreis_euro.csv": _make_kreis_euro_df_substage_contrast(),
        "kba_fuel_euro6_substage_nds.csv": _make_national_substage_df_full(),
    })


@pytest.fixture(scope="module")
def contrast_sampled(contrast_data_path):
    sampler = fs.FleetSampler.from_data_path(contrast_data_path)
    df_cars = _make_cars(n_per_kreis=3000, seed=7)
    df_spec, df_types, summary = fs.sample_fleet(
        df_cars, contrast_data_path, random_seed=123, sampler=sampler)
    return df_spec, df_types, summary


# --------------------------------------------------------------------------- #
# (b) Kreis composition drives the realised substage split
# --------------------------------------------------------------------------- #
def test_diesel_euro6_substage_reflects_kreis_composition(contrast_sampled):
    df_spec, _, _ = contrast_sampled
    diesel_a = df_spec[(df_spec["kreis_ags5"] == AGS_6D_HEAVY)
                        & (df_spec["powertrain"] == "diesel")]
    diesel_b = df_spec[(df_spec["kreis_ags5"] == AGS_6AB_HEAVY)
                        & (df_spec["powertrain"] == "diesel")]
    assert len(diesel_a) > 50 and len(diesel_b) > 50, (
        "not enough diesel cars drawn in the two contrast Kreise for a stable "
        f"comparison (n_a={len(diesel_a)}, n_b={len(diesel_b)})"
    )
    # ADR-0084: the substage lives in its own column; euro_class keeps "euro6".
    # Compare WITHIN the Euro-6 cars so the share is a substage composition and
    # not diluted by the (Kreis-specific) Euro-6 share itself.
    def _share_6d(df):
        euro6 = df[df["euro_class"] == "euro6"]
        assert len(euro6) > 20, f"too few euro6 diesel cars ({len(euro6)})"
        return float((euro6["euro6_substage"] == "euro6d").mean())

    share_6d_a = _share_6d(diesel_a)
    share_6d_b = _share_6d(diesel_b)
    assert share_6d_a > share_6d_b + 0.3, (share_6d_a, share_6d_b)


# --------------------------------------------------------------------------- #
# (c) Electric never gets a substage; euro1..5 / phev / hybrid untouched
# --------------------------------------------------------------------------- #
def test_electric_never_gets_substage(contrast_sampled):
    df_spec, _, _ = contrast_sampled
    electric = df_spec[df_spec["powertrain"].isin(hbefa.ELECTRIC_EURO_POWERTRAINS)]
    assert len(electric) > 0
    assert (electric["euro_class"] == "electric").all()


def test_phev_hybrid_never_get_a_substage_label(contrast_sampled):
    df_spec, _, _ = contrast_sampled
    phev_hybrid = df_spec[df_spec["powertrain"].isin(["phev", "hybrid"])]
    if len(phev_hybrid) == 0:
        pytest.skip("no phev/hybrid cars drawn in this fixture")
    assert not phev_hybrid["euro_class"].isin(SUBSTAGES).any()


def test_low_euro_classes_still_present_and_valid(contrast_sampled):
    df_spec, _, _ = contrast_sampled
    combustion = df_spec[df_spec["powertrain"].isin(hbefa.COMBUSTION_POWERTRAINS)]
    classes = set(combustion["euro_class"].unique())
    # ADR-0084: euro_class NEVER leaves the canonical KBA vocabulary -- the
    # substage refinement is carried by the euro6_substage column instead.
    allowed = set(ft.EURO_CLASS_LABELS)
    assert classes <= allowed, classes - allowed
    assert classes & {"euro1", "euro2", "euro3", "euro4", "euro5", "other"}, (
        "expected at least one untouched low Euro class to still be present"
    )


def test_substage_labels_actually_appear(contrast_sampled):
    """Sanity: with substage data present everywhere, the feature actually
    fires (this is not a vacuously-passing test suite)."""
    df_spec, _, _ = contrast_sampled
    assert set(SUBSTAGES) & set(df_spec["euro6_substage"].unique())
    # Every combustion Euro-6 car in this fixture has a usable substage pmf
    # (per-Kreis and national), so none may keep the not-applicable label.
    euro6_combustion = df_spec[
        df_spec["powertrain"].isin(hbefa.COMBUSTION_POWERTRAINS)
        & (df_spec["euro_class"] == "euro6")
    ]
    assert len(euro6_combustion) > 0
    assert set(euro6_combustion["euro6_substage"].unique()) <= set(SUBSTAGES), (
        "a combustion Euro-6 car kept the not-applicable substage label even "
        "though both the per-Kreis and the national pmf are available"
    )
    # ... and conversely every non-Euro-6 / electrified car keeps it.
    others = df_spec[~df_spec.index.isin(euro6_combustion.index)]
    assert (others["euro6_substage"] == ft.EURO6_SUBSTAGE_NOT_APPLICABLE).all()


# --------------------------------------------------------------------------- #
# (f) Validator mirror: euro_class dimension not flagged
# --------------------------------------------------------------------------- #
def test_validator_not_flagged_for_euro_class(contrast_sampled):
    _, _, summary = contrast_sampled
    euro_dim = summary["dimensions"].get("euro_class")
    assert euro_dim is not None
    assert not euro_dim["flagged"], euro_dim


# --------------------------------------------------------------------------- #
# (d) Flag OFF / data-absent -> byte-identical, no extra RNG consumed
# --------------------------------------------------------------------------- #
def test_absent_data_flag_value_does_not_matter(tmp_path_factory):
    """With BOTH substage sources absent, euro6_substage=True must consume
    exactly as much RNG as euro6_substage=False -- i.e. produce a byte-identical
    seeded fleet.

    The absence is constructed explicitly in a mirror; both tables are committed
    now, so reading the real data path would test the present-data path instead.
    """
    data_path = _mirror_real_data_with_extras(
        tmp_path_factory.mktemp("b5_absent_both"), {},
        omit_files=("kba_kreis_euro.csv", "kba_fuel_euro6_substage_nds.csv"),
    )
    sampler = fs.FleetSampler.from_data_path(data_path)
    df_cars = _make_cars(n_per_kreis=300, seed=1)

    df_on, _, _ = fs.sample_fleet(
        df_cars, data_path, random_seed=99, sampler=sampler, euro6_substage=True)
    df_off, _, _ = fs.sample_fleet(
        df_cars, data_path, random_seed=99, sampler=sampler, euro6_substage=False)
    pd.testing.assert_frame_equal(df_on, df_off)


def test_flag_off_matches_data_effectively_absent_pre_b4_schema(tmp_path_factory):
    """Even when Euro-6 substage data IS present, flag OFF must reproduce
    EXACTLY the same seeded fleet as a run where the substage columns/national
    CSV are absent (pre-Task-B4 schema) -- proving the flag itself gates the
    feature (zero extra RNG), not merely data availability.

    Both runs share the identical euro1..6/other/total shape (only the three
    additive Euro-6 substage columns differ), so any per-Kreis euro joint
    (Task B3/T6b) behaviour is identical between them; the only thing that
    could possibly diverge is Task B5's own RNG usage."""
    tmp_with = tmp_path_factory.mktemp("b5_flag_with_substage")
    tmp_without = tmp_path_factory.mktemp("b5_flag_without_substage")

    df_with_substage = _make_kreis_euro_df_substage_contrast()
    df_pre_b4 = df_with_substage.drop(columns=["euro6d", "euro6dtemp", "euro6ab"])

    dp_with = _mirror_real_data_with_extras(tmp_with, {
        "kba_kreis_euro.csv": df_with_substage,
        "kba_fuel_euro6_substage_nds.csv": _make_national_substage_df_full(),
    })
    dp_without = _mirror_real_data_with_extras(
        tmp_without,
        {"kba_kreis_euro.csv": df_pre_b4},
        # The national substage table is committed now, so "deliberately not
        # overlaid" is not enough -- it must be omitted from the mirror.
        omit_files=("kba_fuel_euro6_substage_nds.csv",),
    )

    sampler_with = fs.FleetSampler.from_data_path(dp_with)
    sampler_without = fs.FleetSampler.from_data_path(dp_without)
    df_cars = _make_cars(n_per_kreis=300, seed=1)

    df_present_flag_off, _, _ = fs.sample_fleet(
        df_cars, dp_with, random_seed=99, sampler=sampler_with, euro6_substage=False)
    df_absent_flag_on, _, _ = fs.sample_fleet(
        df_cars, dp_without, random_seed=99, sampler=sampler_without, euro6_substage=True)

    pd.testing.assert_frame_equal(df_present_flag_off, df_absent_flag_on)


# --------------------------------------------------------------------------- #
# (e) HBEFA type_ids distinct + validation
# --------------------------------------------------------------------------- #
class TestHbefaSubstageMapping:
    @pytest.mark.parametrize("fuel", ["petrol", "diesel", "gas", "other"])
    def test_type_ids_distinct_for_three_substages(self, fuel):
        vt_ab = hbefa.vehicle_type_for(fuel, "euro6ab", "kompaktklasse")
        vt_dtemp = hbefa.vehicle_type_for(fuel, "euro6dtemp", "kompaktklasse")
        vt_d = hbefa.vehicle_type_for(fuel, "euro6d", "kompaktklasse")
        ids = {vt_ab.type_id, vt_dtemp.type_id, vt_d.type_id}
        assert len(ids) == 3, ids
        for vt in (vt_ab, vt_dtemp, vt_d):
            assert hbefa.is_valid_vehicle_type(vt)

    def test_emission_concept_strings(self):
        assert hbefa.emission_concept_for("diesel", "euro6ab") == "PC diesel Euro-6ab"
        assert hbefa.emission_concept_for("diesel", "euro6dtemp") == "PC diesel Euro-6d-temp"
        assert hbefa.emission_concept_for("diesel", "euro6d") == "PC diesel Euro-6d"

    def test_plain_euro6_still_maps_distinctly_from_the_substages(self):
        vt_plain = hbefa.vehicle_type_for("diesel", "euro6", "kompaktklasse")
        vt_d = hbefa.vehicle_type_for("diesel", "euro6d", "kompaktklasse")
        assert vt_plain.type_id != vt_d.type_id
        assert hbefa.is_valid_vehicle_type(vt_plain)

    def test_electric_powertrains_ignore_substage_labels(self):
        vt = hbefa.vehicle_type_for("bev", "electric", "kompaktklasse")
        assert vt.hbefa_emission == "PC BEV"
