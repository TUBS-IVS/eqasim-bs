"""The row/column convention of the committed SrV per-Kreis reference tables (issue #405).

These tests read the COMMITTED tables, not a fixture: the per-builder unit tests already prove
that each generator emits the right shape, but nothing proved that what is actually checked in
still has it. That is the gap this file closes -- the committed table is what every consumer
reads, and it is what a regeneration on a future delivery can silently change.

Scope: every committed table of the ``code,level`` contract family. Two other families exist in
the same directory and are deliberately NOT asserted here, because they are different contracts
rather than the same contract applied inconsistently:

* the ``level,code,name`` family (``cars``, ``bikes``, ``dticket``, ``income5``, ...) writes a
  ``total,total,Gesamt`` region row plus ``stratum`` rows and covers a different geography set.
* the ``level_geo,code,source`` family (``commute_distance*``, ``education_distance*``) fills
  the unsurveyed Kreis from a documented RS7 proxy (``proxy_rs7_72``) rather than with a zero
  row, which is a modelling decision, not a shape inconsistency.

Both are named rather than quietly skipped: an unexplained carve-out is exactly the silent
convention drift these tests exist to stop.
"""
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.analysis.spatial import ZGB8

SRV_DIR = (Path(__file__).resolve().parents[1] / "eqasim-data" / "data" / "braunschweig" / "srv")

REGION_CODE = "03ZGB"
LEVEL_KREIS = "kreis"
LEVEL_TOTAL = "total"

# Committed tables that share the code,level contract: the generator that has to keep them on
# it, and the share columns that must be NaN on an unsurveyed Kreis. The share columns are named
# per table rather than guessed from a prefix: a heuristic that silently matches nothing would
# turn the strongest assertion below into a no-op.
CODE_LEVEL_TABLES = (
    ("srv2023_work_by_employment_by_kreis.csv", "scripts/extract_srv_participation_universe.py",
     ("employed_share", "p_work_employed", "p_work_nonemployed")),
    ("srv2023_education_by_age_by_kreis.csv", "scripts/extract_srv_participation_universe.py",
     ("p_education",)),
    ("srv2023_participation_by_kreis.csv", "scripts/build_srv_participation_aggregate.py",
     ("work", "education", "leisure", "escort")),
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


@pytest.mark.parametrize("name,generator,share_columns", CODE_LEVEL_TABLES)
def test_every_zgb_kreis_has_a_row_and_the_region_row_is_present(name, generator, share_columns):
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


@pytest.mark.parametrize("name,generator,share_columns", CODE_LEVEL_TABLES)
def test_the_first_two_columns_are_code_then_level(name, generator, share_columns):
    """One column order across the family. Every reader goes through pandas by NAME, so this
    buys no correctness -- it buys a maintainer being able to diff two of these tables against
    each other, which is how the divergence of issue #405 stayed invisible."""
    table = _read(name)
    assert list(table.columns)[:2] == ["code", "level"], (
        f"{name} ({generator}) must start with the columns code, level; found "
        f"{list(table.columns)[:2]}.")


@pytest.mark.parametrize("name,generator,share_columns", CODE_LEVEL_TABLES)
def test_an_unsurveyed_kreis_row_is_zero_with_no_fabricated_share(name, generator, share_columns):
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
    missing = [c for c in share_columns if c not in table.columns]
    assert not missing, f"{name} ({generator}): declared share column(s) {missing} not in the table."
    assert wolfsburg[list(share_columns)].isna().all().all(), (
        f"{name} ({generator}): 03103 has a non-NaN share in {share_columns} despite "
        "n_unweighted == 0; an empty class must never be reported as a measured 0.0.")
