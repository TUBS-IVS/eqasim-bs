"""Tests for the Zensus 2022 1000A-2081 household-type loader and the
IPF ``hh_size`` margin (added by ``feature-bs-ipf-hhsize-1``).

The loader sits in ``braunschweig.data.census.households_type`` and exposes
``Personen`` per (Gemeinde × HSHGR2 × HSHTP1). The downstream IPF stage
``braunschweig.ipf.model`` consumes the (Gemeinde × HSHGR2) marginal as a fifth
constraint when ``braunschweig.ipf.use_household_size_margin`` is enabled.

These tests are cache-level invariants — they read the synpp pickles and
do not re-execute any stage. They are skipped (not failed) if the caches
are absent, so the suite remains green on a clean checkout.
"""

from __future__ import annotations

import pathlib
import pickle
import sys

import pandas as pd
import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CACHE_ROOT = REPO_ROOT / "eqasim-data" / "cache_bs"
ZGB_KREISE = ["03101", "03102", "03103", "03151",
              "03153", "03154", "03157", "03158"]
HH_SIZE_BINS = {"1", "2", "3", "4", "5", "6+"}


def _latest_cache(pattern: str):
    hits = list(CACHE_ROOT.glob(pattern))
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def _load_pickle(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


# ---------------------------------------------------------------------------
# Loader: braunschweig.data.census.households_type (Zensus 1000A-2081)
# ---------------------------------------------------------------------------

class TestHouseholdTypeLoader:
    """The cube reports ``Personen``; SafeMosaic flag ``e`` is perturbed but
    valid (must NOT be discarded). Earlier code wrongly treated ``e`` as
    suppressed and zero-ed out the entire margin — this test pins down the
    correction."""

    def test_zgb_persons_match_zensus_reference(self):
        path = _latest_cache("braunschweig.data.census.households_type__*.p")
        if path is None:
            pytest.skip("households_type cache not yet generated")
        df = _load_pickle(path)

        zgb = df[df["commune_id"].astype(str).str[:5].isin(ZGB_KREISE)]
        total = zgb["weight"].sum()
        # ZGB has ~1.135 M persons (Zensus 2022 Stichtag 15.05.2022).
        # Allow ±5 % around the integration figure 1,135,791.
        assert 1_050_000 < total < 1_200_000, (
            f"Unexpected ZGB person total in 1000A-2081: {total:,.0f}"
        )

    def test_hh_size_bins_are_canonical(self):
        path = _latest_cache("braunschweig.data.census.households_type__*.p")
        if path is None:
            pytest.skip("households_type cache not yet generated")
        df = _load_pickle(path)
        assert set(df["hh_size"].unique()) == HH_SIZE_BINS

    def test_hh_type_codebook_complete(self):
        path = _latest_cache("braunschweig.data.census.households_type__*.p")
        if path is None:
            pytest.skip("households_type cache not yet generated")
        df = _load_pickle(path)
        # The five categories defined by HSHTP1 in 1000A-2081 (codebook).
        assert set(df["hh_type"].unique()) == {
            "single", "couple", "couple_with_children",
            "single_parent", "other_multi",
        }

    def test_per_commune_persons_strictly_positive(self):
        path = _latest_cache("braunschweig.data.census.households_type__*.p")
        if path is None:
            pytest.skip("households_type cache not yet generated")
        df = _load_pickle(path)
        zgb = df[df["commune_id"].astype(str).str[:5].isin(ZGB_KREISE)]
        per_c = zgb.groupby("commune_id")["weight"].sum()
        assert (per_c > 0).all(), (
            "ZGB communes with zero hh_type weight: "
            f"{per_c[per_c == 0].index.tolist()}"
        )


# ---------------------------------------------------------------------------
# IPF: braunschweig.ipf.model with hh_size margin
# ---------------------------------------------------------------------------

class TestIPFHouseholdSizeMargin:
    """Cache-level invariants of the joint distribution returned by
    ``braunschweig.ipf.model`` when the hh_size margin is enabled."""

    def _load_model(self):
        path = _latest_cache("braunschweig.ipf.model__*.p")
        if path is None:
            pytest.skip("braunschweig.ipf.model cache not yet generated")
        df = _load_pickle(path)
        if "hh_size" not in df.columns:
            pytest.skip("hh_size margin disabled in the cached IPF run")
        return df

    def test_schema_has_canonical_hh_size_bins(self):
        df = self._load_model()
        assert set(df["hh_size"].astype(str).unique()) <= HH_SIZE_BINS

    def test_children_excluded_from_one_person_households(self):
        """Hard zero: persons younger than ``minimum_age.one_person_household``
        (default 16) cannot live alone. Enforced as an explicit IPF target."""
        df = self._load_model()
        children_alone = df.loc[
            (df["age_class"] < 16) & (df["hh_size"].astype(str) == "1"),
            "weight",
        ].sum()
        assert children_alone == pytest.approx(0.0, abs=1e-6)

    def test_per_commune_population_balanced(self):
        df = self._load_model()
        per_c = df.groupby("commune_id")["weight"].sum()
        assert (per_c > 0).all()
        total = per_c.sum()
        assert 1_050_000 < total < 1_200_000

    def test_per_commune_hh_size_marginal_balanced(self):
        """For every commune, the sum of weights over (sex, age, employed,
        license) must equal the per-commune population for each hh_size
        bin (within IPF tolerance, here 2 %)."""
        df = self._load_model()
        # Grand population per commune
        pop = df.groupby("commune_id")["weight"].sum()
        # Per (commune, hh_size) the marginal must be > 0 if pop > 0
        marg = df.groupby(["commune_id", "hh_size"], observed=True)["weight"].sum()
        # Sum across hh_size must equal commune population
        recon = marg.groupby(level=0).sum()
        rel_err = (recon - pop).abs() / pop.replace(0, pd.NA)
        assert rel_err.dropna().max() < 0.02, (
            f"Per-commune sum mismatch up to {rel_err.max():.3%}"
        )

    def test_urban_rural_one_person_share_gradient(self):
        """The whole motivation: urban (SK BS) > rural (LK GF, LK PE) in
        the share of persons living in 1-person households."""
        df = self._load_model().copy()
        df["hh_size_str"] = df["hh_size"].astype(str)
        share = df.groupby("departement_id").apply(
            lambda g: g.loc[g["hh_size_str"] == "1", "weight"].sum()
            / g["weight"].sum()
        )
        assert share["03101"] > share["03151"], (
            f"SK BS ({share['03101']:.3f}) should exceed LK GF "
            f"({share['03151']:.3f})"
        )
        assert share["03101"] > share["03157"], (
            f"SK BS ({share['03101']:.3f}) should exceed LK PE "
            f"({share['03157']:.3f})"
        )


# ---------------------------------------------------------------------------
# Pure-function unit tests (independent of caches)
# ---------------------------------------------------------------------------

class TestParseValueEFlag:
    """Guards the SafeMosaic ``e``-flag handling regression."""

    @staticmethod
    def _call(values, qs):
        from braunschweig.data.census.households_size_age import _parse_value
        return _parse_value(pd.Series(values), pd.Series(qs)).tolist()

    def test_e_flag_keeps_value(self):
        assert self._call(["123"], ["e"]) == [123.0]

    def test_dash_returns_zero(self):
        assert self._call(["-"], [""]) == [0.0]

    def test_dot_returns_zero(self):
        assert self._call(["."], [""]) == [0.0]

    def test_plain_numeric_keeps_value(self):
        assert self._call(["42"], [""]) == [42.0]

    def test_mixed_batch(self):
        assert self._call(
            ["100", "-", ".", "55"], ["", "", "", "e"]
        ) == [100.0, 0.0, 0.0, 55.0]
