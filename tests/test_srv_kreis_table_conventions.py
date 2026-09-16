"""The row/column convention of the committed SrV per-Kreis reference tables (issue #405).

These tests read the COMMITTED tables, not a fixture: the per-builder unit tests already prove
that each generator emits the right shape, but nothing proved that what is actually checked in
still has it. That is the gap this file closes -- the committed table is what every consumer
reads, and it is what a regeneration on a future delivery can silently change.

Scope: the tables of the ``code,level`` contract family that issue #405 brings onto one
convention. Three other families exist in the same directory and are deliberately NOT asserted
here, because bringing them onto this contract is a separate change with its own consumers, not
a test fix:

* ``srv2023_participation_by_kreis.csv`` -- same ``code,level`` / ``03ZGB`` family and the same
  data-driven row set (``persons.groupby("ars5")`` in
  ``scripts/build_srv_participation_aggregate.py``), so it is the next table that belongs here.
  It is out of scope only because its consumer surface is much wider (popsim Kreis controls and
  the participation-fit validation stage, not one target builder). Named explicitly rather than
  quietly skipped: an unexplained carve-out is exactly the silent convention drift these tests
  exist to stop.
* the ``level,code,name`` family (``cars``, ``bikes``, ``dticket``, ``income5``, ...) writes a
  ``total,total,Gesamt`` region row plus ``stratum`` rows and covers a different geography set.
* the ``level_geo,code,source`` family (``commute_distance*``, ``education_distance*``) fills
  the unsurveyed Kreis from a documented RS7 proxy (``proxy_rs7_72``) rather than with a zero
  row, which is a modelling decision, not a shape inconsistency.
"""
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.analysis.spatial import ZGB8

SRV_DIR = (Path(__file__).resolve().parents[1] / "eqasim-data" / "data" / "braunschweig" / "srv")

REGION_CODE = "03ZGB"
LEVEL_KREIS = "kreis"
LEVEL_TOTAL = "total"

# Committed tables that share the code,level contract, with the name of the generator that has
# to keep them on it.
CODE_LEVEL_TABLES = (
    ("srv2023_work_by_employment_by_kreis.csv", "scripts/extract_srv_participation_universe.py"),
    ("srv2023_education_by_age_by_kreis.csv", "scripts/extract_srv_participation_universe.py"),
)


def _read(name: str) -> pd.DataFrame:
    path = SRV_DIR / name
    # The committed SrV tables are tracked despite eqasim-data/ being ignored, so an absent file
    # means a broken checkout, never "nothing to test": assert instead of skipping, or this whole
    # file would pass vacuously.
    assert path.exists(), (
        f"Committed SrV reference table missing: {path}. It is tracked in git (force-added past "
        "the eqasim-data/ ignore rule); a missing file means the checkout is broken, not that the "
        "test does not apply.")
    return pd.read_csv(path, comment="#", dtype={"code": str})


@pytest.mark.parametrize("name,generator", CODE_LEVEL_TABLES)
def test_every_zgb_kreis_has_a_row_and_the_region_row_is_present(name, generator):
    """All eight ZGB Kreise plus the region row -- an unsurveyed Kreis is a ZERO row.

    This is the property that stops a reader from having to know how many Kreise to expect. It
    cost real work before it held: the #368 target builder carried "expected = ZGB8 minus
    Wolfsburg" because the table it read simply had no row for Wolfsburg.
    """
    table = _read(name)
    kreis_codes = set(table.loc[table["level"] == LEVEL_KREIS, "code"])
    assert kreis_codes == set(ZGB8), (
        f"{name} ({generator}) must carry one kreis row per ZGB Kreis {sorted(ZGB8)}; "
        f"found {sorted(kreis_codes)}. A Kreis the survey does not cover is a zero row, never "
        "an absent one.")

    region_rows = table[table["level"] == LEVEL_TOTAL]
    assert set(region_rows["code"]) == {REGION_CODE}, (
        f"{name} ({generator}) must code its region-total row(s) {REGION_CODE!r}; found "
        f"{sorted(set(region_rows['code']))}.")
    assert not region_rows.empty, f"{name} ({generator}) has no region-total row."

    unexpected_levels = set(table["level"]) - {LEVEL_KREIS, LEVEL_TOTAL}
    assert not unexpected_levels, (
        f"{name} ({generator}) has unexpected level value(s) {sorted(unexpected_levels)}.")


@pytest.mark.parametrize("name,generator", CODE_LEVEL_TABLES)
def test_the_first_two_columns_are_code_then_level(name, generator):
    """One column order across the family. Every reader goes through pandas by NAME, so this
    buys no correctness -- it buys a maintainer being able to diff two of these tables against
    each other, which is how the divergence of issue #405 stayed invisible."""
    table = _read(name)
    assert list(table.columns)[:2] == ["code", "level"], (
        f"{name} ({generator}) must start with the columns code, level; found "
        f"{list(table.columns)[:2]}.")


@pytest.mark.parametrize("name,generator", CODE_LEVEL_TABLES)
def test_an_unsurveyed_kreis_row_is_zero_with_no_fabricated_share(name, generator):
    """Wolfsburg's zero row must carry NaN shares, never 0.0.

    A 0.0 share reads as a measured rate of zero and would flow into a control target as one; a
    NaN forces the consumer to decide explicitly (today: take the region-total rate, recorded as
    source ``srv_region_total``). The distinction is the whole point of emitting the row.
    """
    table = _read(name)
    wolfsburg = table[(table["level"] == LEVEL_KREIS) & (table["code"] == "03103")]
    assert not wolfsburg.empty, f"{name} ({generator}) has no 03103 row."
    assert (wolfsburg["n_unweighted"] == 0).all(), (
        f"{name} ({generator}): 03103 is recorded as not surveyed by SrV but carries persons. "
        "If a delivery now covers it, the region-total assumption in every consumer is stale.")
    share_columns = [c for c in table.columns
                     if c.startswith(("p_", "share_", "employed_share"))]
    assert share_columns, f"{name} ({generator}): no share column found to check."
    assert wolfsburg[share_columns].isna().all().all(), (
        f"{name} ({generator}): 03103 has a non-NaN share in {share_columns} despite "
        "n_unweighted == 0; an empty class must never be reported as a measured 0.0.")
