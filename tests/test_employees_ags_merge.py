"""Fallback-transparency test for the AGS->commune_id merge in
``braunschweig.data.census.employees`` (issue #163, item 2).

The inner merge against the ``eqasim_common.spatial.codes`` table silently
drops any 8-digit AGS that is not present in the codes table -- its SvB
Arbeitsort total disappears from the work-attraction vector with no
diagnostic. This test drives ``execute()`` end-to-end with a synthetic
context so the module's own instrumentation (counts + WARN/RAISE) is
exercised, not a re-implementation of it.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from braunschweig.data.census import employees


class _FakeContext:
    """Minimal synpp-context stand-in: config() returns preset values,
    stage() returns preset DataFrames."""

    def __init__(self, config: dict, stages: dict) -> None:
        self._config = config
        self._stages = stages

    def config(self, key, default=None):
        return self._config.get(key, default)

    def stage(self, name):
        return self._stages[name]


def _employees_raw_frame(rows: list[list]) -> pd.DataFrame:
    """Build the frame ``pd.read_excel`` would hand ``execute()`` post-parse.

    ``ags`` must stay a zero-padded string (as the genuine GENESIS export
    stores it) -- round-tripping through an actual ``.xlsx`` file is avoided
    here because openpyxl/pandas' own numeric-looking-string inference would
    silently strip the leading zero, which is an orthogonal concern from the
    AGS->commune_id merge instrumentation under test.
    """
    return pd.DataFrame(rows, columns=employees.COLUMN_NAMES)


def _codes_frame(ags_list: list[str], kreis_scope: list[str] | None = None) -> pd.DataFrame:
    # `kreis_scope` lets a test declare a Kreis "in scope" (present in
    # departement_id) without every Gemeinde AGS of that Kreis being listed --
    # reproducing a codes table that covers the Kreis but is missing one of
    # its Gemeinden.
    departement_ids = [a[:5] for a in ags_list]
    if kreis_scope:
        departement_ids = list(dict.fromkeys(departement_ids + kreis_scope))
        # Pad the codes frame with the extra scope Kreise on an unrelated AGS
        # row so departement_id.unique() includes them without adding a real
        # ags->commune_id mapping for the missing Gemeinde.
        extra = [k for k in kreis_scope if k not in [a[:5] for a in ags_list]]
        ags_list = ags_list + [f"{k}999" for k in extra]
    return pd.DataFrame({
        "ags": ags_list,
        "commune_id": [f"{a}0000" for a in ags_list],
        "departement_id": [a[:5] for a in ags_list],
    })


def test_clean_ags_scope_has_no_lost_weight(tmp_path, monkeypatch, capsys):
    # Both 8-digit AGS in the export are covered by the codes table.
    raw = _employees_raw_frame([
        ["03101000", "Braunschweig", 1000, 600, 400, 50, 30, 20],
        ["03102000", "Wolfsburg", 800, 500, 300, 40, 20, 20],
    ])
    monkeypatch.setattr(employees.pd, "read_excel", lambda *a, **k: raw)
    (tmp_path / "employees.xlsx").touch()
    df_codes = _codes_frame(["03101000", "03102000"])
    ctx = _FakeContext(
        {"data_path": str(tmp_path), "braunschweig.employees_path": "employees.xlsx"},
        {"eqasim_common.spatial.codes": df_codes},
    )
    out_df = employees.execute(ctx)
    out = capsys.readouterr().out

    assert len(out_df) == 2
    assert out_df["weight"].sum() == 1800
    assert "0 AGS unmatched" in out
    assert "WARNING" not in out


def test_ags_missing_from_codes_table_is_counted_and_warned(tmp_path, monkeypatch, capsys):
    # 03103000 is a valid Gemeinde in the ZGB scope but missing from the codes
    # table (e.g. a stale/incomplete codes export) -- the merge silently drops
    # its 900-employee total unless the instrumentation surfaces it.
    raw = _employees_raw_frame([
        ["03101000", "Braunschweig", 1000, 600, 400, 50, 30, 20],
        ["03103000", "Missing Gemeinde", 900, 500, 400, 10, 5, 5],
    ])
    monkeypatch.setattr(employees.pd, "read_excel", lambda *a, **k: raw)
    (tmp_path / "employees.xlsx").touch()
    df_codes = _codes_frame(["03101000"], kreis_scope=["03103"])
    ctx = _FakeContext(
        {"data_path": str(tmp_path), "braunschweig.employees_path": "employees.xlsx"},
        {"eqasim_common.spatial.codes": df_codes},
    )

    # 900 / 1900 = 47.4% lost, well above both thresholds -> raise, not just
    # warn (CLAUDE.md "high fallback rate is a failure signal").
    with pytest.raises(RuntimeError, match="implausibly high"):
        employees.execute(ctx)


def test_landkreis_aggregate_rows_are_excluded_not_counted_as_lost(
    tmp_path, monkeypatch, capsys
):
    """#128 follow-up: the GENESIS export carries 5-digit LANDKREIS aggregate
    rows (e.g. "03151" = LK Gifhorn total). Only kreisfreie Staedte (whose
    padded AGS exists as a real Gemeinde in the codes table) may be normalised
    to 8 digits; padding LK aggregates fabricates non-existent AGS that the
    merge then drops and the loss accounting reports as lost SvB weight. On
    the full ZGB-8 scope those aggregates sum to ~27% "loss", tripping the 25%
    raise threshold and aborting every full-region run."""
    raw = _employees_raw_frame([
        # kreisfreie Stadt: 5-digit row, padded AGS exists in codes -> keep.
        ["03101", "Braunschweig, krfr. Stadt", 1000, 600, 400, 50, 30, 20],
        # Landkreis AGGREGATE row: padded AGS 03151000 is NOT a Gemeinde.
        ["03151", "Gifhorn, Landkreis", 500, 300, 200, 20, 10, 10],
        # The LK's actual Gemeinden, fully covered by the codes table.
        ["03151009", "Gifhorn, Stadt", 300, 200, 100, 10, 5, 5],
        ["03151016", "Meine", 200, 100, 100, 10, 5, 5],
    ])
    monkeypatch.setattr(employees.pd, "read_excel", lambda *a, **k: raw)
    (tmp_path / "employees.xlsx").touch()
    df_codes = _codes_frame(["03101000", "03151009", "03151016"])
    ctx = _FakeContext(
        {"data_path": str(tmp_path), "braunschweig.employees_path": "employees.xlsx"},
        {"eqasim_common.spatial.codes": df_codes},
    )

    out_df = employees.execute(ctx)
    out = capsys.readouterr().out

    # The aggregate row is excluded up front: it neither survives into the
    # output nor counts as "lost" merge weight (which would trip the raise).
    assert len(out_df) == 3
    assert out_df["weight"].sum() == 1500
    assert "0 AGS unmatched" in out
    assert "WARNING" not in out
