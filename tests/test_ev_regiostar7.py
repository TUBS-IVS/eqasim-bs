"""Tests for Task B6: RegioStaR7 EV cross-check (extractor + loader + crosscheck).

Covers:
- ``scripts.extract_kba_fleet.extract_ev_regiostar7`` (latest-period kept, the
  residual code 99 "keine Zuordnung" dropped, comma-decimal-safe percent -> fraction).
- ``braunschweig.data.kba.fleet_tables.load_ev_regiostar7`` (schema validation).
- ``braunschweig.synthesis.vehicles.fleet_validation.crosscheck_ev_by_regiostar7``
  (LOGGING-ONLY: must never raise and must never flag/fail the run -- the KBA
  reference is national while the synthesised fleet is regional).

No real KBA raw files or committed derived CSVs are required (the RegioStaR7
timeseries is a new, server-only raw input; see ``scripts/extract_kba_fleet.py``
docstring and ``eqasim-data/data/braunschweig/kba/README.md``).
"""
import logging
import textwrap

import pandas as pd
import pytest

import scripts.extract_kba_fleet as ex
from braunschweig.data.kba import fleet_tables as ft
from braunschweig.synthesis.vehicles import fleet_validation as fv

# ---------------------------------------------------------------------------
# extract_ev_regiostar7
# ---------------------------------------------------------------------------
# Two periods (2025.10 must be dropped, 2026.04 kept); all 7 RS7 codes plus the
# residual 99 ("keine Zuordnung", must be dropped); one German comma-decimal
# value (rs7=76) to prove comma -> dot conversion.
_RS7_CSV_FIXTURE = textwrap.dedent("""\
    Berichtszeitpunkt,RegioStaR7,Pkw Elektro Anteil,Regiostar7 Nummer
    2025.10,Stadtregion - Metropole,1.0,71
    2026.04,Stadtregion - Metropole,8.5,71
    2026.04,Stadtregion - Regiopole und Grossstadt,7.2,72
    2026.04,Stadtregion - Mittelstadt staedtischer Raum,6.1,73
    2026.04,Stadtregion - kleinstaedtischer doerflicher Raum,5.4,74
    2026.04,laendliche Region - zentrale Stadt,4.8,75
    2026.04,laendliche Region - Mittelstadt staedtischer Raum,"4,0",76
    2026.04,laendliche Region - kleinstaedtischer doerflicher Raum,3.2,77
    2026.04,keine Zuordnung,1.0,99
""")


@pytest.fixture()
def rs7_csv(tmp_path):
    """Write the fixture CSV as utf-8-sig to a tmp_path file."""
    p = tmp_path / "kba_ev_regiostar7_timeseries_2023_2026.csv"
    p.write_text(_RS7_CSV_FIXTURE, encoding="utf-8-sig")
    return p


def test_only_latest_period_kept(rs7_csv):
    """Rows for the older period (2025.10) must be dropped; only 2026.04 survives."""
    df = ex.extract_ev_regiostar7(rs7_csv)
    assert set(df["stichtag"]) == {"2026-04-01"}
    # The 2025.10 rs7=71 row (ev_share 1.0/100=0.01) must not leak into the
    # 2026.04 rs7=71 row (ev_share 8.5/100=0.085).
    assert df.set_index("rs7").loc[71, "ev_share"] == pytest.approx(0.085)


def test_keine_zuordnung_99_dropped(rs7_csv):
    """The residual RegioStaR7 code 99 ('keine Zuordnung') must be dropped."""
    df = ex.extract_ev_regiostar7(rs7_csv)
    assert 99 not in set(df["rs7"])
    assert len(df) == 7


def test_all_seven_rs7_codes_present(rs7_csv):
    df = ex.extract_ev_regiostar7(rs7_csv)
    assert set(df["rs7"]) == {71, 72, 73, 74, 75, 76, 77}


def test_ev_share_is_fraction_not_percent(rs7_csv):
    """ev_share must be Anteil / 100 (a fraction in [0, 1], not a percent)."""
    df = ex.extract_ev_regiostar7(rs7_csv)
    row = df.set_index("rs7").loc[72]
    assert row["ev_share"] == pytest.approx(0.072)


def test_german_decimal_comma_converted(rs7_csv):
    """A German decimal comma in the Anteil cell (e.g. '4,0') must parse correctly."""
    df = ex.extract_ev_regiostar7(rs7_csv)
    row = df.set_index("rs7").loc[76]
    assert row["ev_share"] == pytest.approx(0.04)


def test_output_columns_exact(rs7_csv):
    df = ex.extract_ev_regiostar7(rs7_csv)
    assert list(df.columns) == ["rs7", "ev_share", "stichtag"]


def test_sorted_by_rs7(rs7_csv):
    df = ex.extract_ev_regiostar7(rs7_csv)
    assert df["rs7"].tolist() == sorted(df["rs7"].tolist())


def test_dropped_99_is_logged(rs7_csv, caplog):
    """The 99 drop must be logged (no-silent-fallback rule)."""
    with caplog.at_level(logging.INFO, logger="extract_kba_fleet"):
        ex.extract_ev_regiostar7(rs7_csv)
    log_text = " ".join(caplog.messages).lower()
    assert "dropped" in log_text


# ---------------------------------------------------------------------------
# load_ev_regiostar7
# ---------------------------------------------------------------------------
def _write_derived(tmp_path, df: pd.DataFrame, filename: str = "kba_ev_regiostar7.csv"):
    derived = tmp_path / "braunschweig" / "kba" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    df.to_csv(derived / filename, index=False)
    return tmp_path


def _valid_rs7_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "rs7": [71, 72, 73, 74, 75, 76, 77],
        "ev_share": [0.085, 0.072, 0.061, 0.054, 0.048, 0.040, 0.032],
        "stichtag": ["2026-04-01"] * 7,
    })


def test_load_ev_regiostar7_valid(tmp_path):
    data_path = _write_derived(tmp_path, _valid_rs7_frame())
    df = ft.load_ev_regiostar7(str(data_path))
    assert not df.empty
    assert {"rs7", "ev_share", "stichtag"} <= set(df.columns)
    assert set(df["rs7"]) == {71, 72, 73, 74, 75, 76, 77}


def test_load_ev_regiostar7_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ft.load_ev_regiostar7(str(tmp_path))


def test_load_ev_regiostar7_missing_column_raises(tmp_path):
    df = _valid_rs7_frame().drop(columns=["ev_share"])
    data_path = _write_derived(tmp_path, df)
    with pytest.raises(RuntimeError, match="missing columns"):
        ft.load_ev_regiostar7(str(data_path))


def test_load_ev_regiostar7_unexpected_code_raises(tmp_path):
    """A residual code (e.g. 99) reaching the loader is schema drift and must raise."""
    df = _valid_rs7_frame()
    df.loc[0, "rs7"] = 99
    data_path = _write_derived(tmp_path, df)
    with pytest.raises(RuntimeError, match="unexpected RegioStaR7"):
        ft.load_ev_regiostar7(str(data_path))


# ---------------------------------------------------------------------------
# crosscheck_ev_by_regiostar7 (LOGGING-ONLY -- must never raise, never flag)
# ---------------------------------------------------------------------------
def _df_spec(raumtyp, powertrain) -> pd.DataFrame:
    return pd.DataFrame({"raumtyp": raumtyp, "powertrain": powertrain})


def test_crosscheck_returns_per_rs7_realised_reference_delta():
    df_spec = _df_spec(
        [71] * 10 + [72] * 10,
        ["bev"] * 3 + ["petrol"] * 7 + ["phev"] * 1 + ["diesel"] * 9,
    )
    df_rs7 = _valid_rs7_frame()
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert 71 in result and 72 in result
    assert result[71]["realised"] == pytest.approx(0.3)   # 3/10 bev
    assert result[71]["reference"] == pytest.approx(0.085)
    assert result[71]["n_cars"] == 10
    assert result[71]["delta_pp"] == pytest.approx((0.3 - 0.085) * 100.0)
    assert result[72]["realised"] == pytest.approx(0.1)   # 1/10 phev


def test_crosscheck_counts_bev_phev_and_hydrogen():
    """realised must combine bev + phev + hydrogen (matches the KBA 'Elektro' share
    definition used elsewhere in this project, e.g. extract_gemeinde_ev's ev_share)."""
    df_spec = _df_spec([71] * 4, ["bev", "phev", "hydrogen", "diesel"])
    df_rs7 = _valid_rs7_frame()
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert result[71]["realised"] == pytest.approx(0.75)  # 3/4


def test_crosscheck_never_raises_on_missing_spec_columns():
    df_spec = pd.DataFrame({"segment": ["kompaktklasse"]})
    df_rs7 = _valid_rs7_frame()
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert result == {}


def test_crosscheck_never_raises_on_missing_rs7_columns():
    df_spec = _df_spec([71], ["bev"])
    df_rs7 = pd.DataFrame({"rs7": [71]})  # missing ev_share
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert result == {}


def test_crosscheck_skips_nan_raumtyp_without_raising():
    df_spec = _df_spec([71, float("nan"), 72], ["bev", "petrol", "diesel"])
    df_rs7 = _valid_rs7_frame()
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert set(result.keys()) <= {71, 72}


def test_crosscheck_skips_rs7_code_absent_from_reference():
    df_spec = _df_spec([71, 999], ["bev", "petrol"])
    df_rs7 = _valid_rs7_frame()
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert 999 not in result
    assert 71 in result


def test_crosscheck_empty_df_spec_returns_empty_dict():
    df_spec = pd.DataFrame({"raumtyp": pd.Series(dtype=float),
                            "powertrain": pd.Series(dtype=object)})
    df_rs7 = _valid_rs7_frame()
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert result == {}


def test_crosscheck_never_flags_even_with_large_delta():
    """A wildly different realised vs national-reference share must NOT raise
    and must NOT produce any 'flagged' key -- this is a cross-check, not a
    pass/fail validator (see validate_realised_margins for the latter)."""
    df_spec = _df_spec([71] * 10, ["bev"] * 10)  # 100% electric (unrealistic)
    df_rs7 = _valid_rs7_frame()  # national reference ~8.5%
    result = fv.crosscheck_ev_by_regiostar7(df_spec, df_rs7)
    assert result[71]["realised"] == pytest.approx(1.0)
    assert "flagged" not in result[71]
