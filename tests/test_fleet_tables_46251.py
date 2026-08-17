"""Tests for load_kreis_fuel and load_kreis_euro in fleet_tables.py.

Uses tmp_path to write minimal kba_kreis_fuel.csv / kba_kreis_euro.csv with all
8 ZGB Kreise into a temporary derived directory, validates that the loaders
return them and raise RuntimeError when a Kreis is missing.
"""
import pandas as pd
import pytest
from braunschweig.data.kba import fleet_tables as ft


def _write_derived(tmp_path, name, df):
    d = tmp_path / "braunschweig" / "kba" / "derived"
    d.mkdir(parents=True, exist_ok=True)
    df.to_csv(d / name, index=False)
    return str(tmp_path)


def test_load_kreis_fuel_requires_all_zgb(tmp_path):
    rows = [{"kreis_ags5": k, "kreis_name": k, "stichtag": "2025-01-01",
             "petrol": 1, "diesel": 1, "gas": 0, "bev": 0, "phev": 0,
             "hybrid": 0, "other": 0, "total": 2, "petrol_share": .5,
             "diesel_share": .5, "gas_share": 0, "bev_share": 0,
             "phev_share": 0, "hybrid_share": 0, "other_share": 0}
            for k in ft.ZGB_KREISE_AGS5]
    dp = _write_derived(tmp_path, "kba_kreis_fuel.csv", pd.DataFrame(rows))
    out = ft.load_kreis_fuel(dp)
    assert set(out["kreis_ags5"]) == set(ft.ZGB_KREISE_AGS5)
    dp2 = _write_derived(tmp_path, "kba_kreis_fuel.csv", pd.DataFrame(rows[:-1]))
    with pytest.raises(RuntimeError):
        ft.load_kreis_fuel(dp2)


def test_load_kreis_euro_requires_all_zgb_in_all_teil(tmp_path):
    euro_cols = ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other", "total"]
    euro_shares = [f"{c}_share" for c in ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"]]
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel"):
            row = {"kreis_ags5": k, "kreis_name": k, "stichtag": "2025-01-01", "teil": teil}
            for c in euro_cols:
                row[c] = 1
            for c in euro_shares:
                row[c] = 0.0
            rows.append(row)
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    out = ft.load_kreis_euro(dp)
    assert set(out.loc[out["teil"] == "all", "kreis_ags5"]) == set(ft.ZGB_KREISE_AGS5)
    # Drop one Kreis from the 'all' rows -> should raise
    rows_missing = [r for r in rows if not (r["kreis_ags5"] == ft.ZGB_KREISE_AGS5[-1] and r["teil"] == "all")]
    dp2 = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows_missing))
    with pytest.raises(RuntimeError):
        ft.load_kreis_euro(dp2)


def test_require_zgb_subset_allows_extra_kreise():
    """Task B3: `_require_zgb_subset` accepts non-ZGB Kreis codes as extras."""
    values = list(ft.ZGB_KREISE_AGS5) + ["03241"]  # 03241 = Region Hannover
    ft._require_zgb_subset(values, "test.csv")  # must not raise


def test_require_zgb_subset_raises_when_zgb_kreis_missing():
    """`_require_zgb_subset` still raises when a ZGB Kreis is missing, even if
    extra (non-ZGB) Kreis codes are present."""
    values = list(ft.ZGB_KREISE_AGS5[:-1]) + ["03241"]
    with pytest.raises(RuntimeError):
        ft._require_zgb_subset(values, "test.csv")


def test_require_zgb_kreise_still_rejects_extras():
    """`_require_zgb_kreise` (used by the OTHER loaders) must remain strict:
    it must still reject a table that carries extra non-ZGB Kreis codes. This
    guards against Task B3 accidentally weakening the shared helper used by
    load_kreis_powertrain / load_gemeinde_private_bev / load_gemeinde_ev."""
    values = list(ft.ZGB_KREISE_AGS5) + ["03241"]
    with pytest.raises(RuntimeError):
        ft._require_zgb_kreise(values, "test.csv")


def _fuel_row(kreis_ags5: str, kreis_name: str) -> dict:
    return {
        "kreis_ags5": kreis_ags5, "kreis_name": kreis_name, "stichtag": "2025-01-01",
        "petrol": 1, "diesel": 1, "gas": 0, "bev": 0, "phev": 0,
        "hybrid": 0, "other": 0, "total": 2, "petrol_share": .5,
        "diesel_share": .5, "gas_share": 0, "bev_share": 0,
        "phev_share": 0, "hybrid_share": 0, "other_share": 0,
    }


def test_load_kreis_fuel_allows_extra_non_zgb_kreis(tmp_path):
    """Task B3: load_kreis_fuel keeps a non-ZGB Kreis row (extras allowed)."""
    rows = [_fuel_row(k, k) for k in ft.ZGB_KREISE_AGS5]
    rows.append(_fuel_row("03241", "Region Hannover"))
    dp = _write_derived(tmp_path, "kba_kreis_fuel.csv", pd.DataFrame(rows))
    out = ft.load_kreis_fuel(dp)
    assert set(ft.ZGB_KREISE_AGS5) <= set(out["kreis_ags5"])
    assert "03241" in set(out["kreis_ags5"])

    # Still raises if a ZGB Kreis is missing, even with the extra present.
    rows_missing_zgb = [_fuel_row(k, k) for k in ft.ZGB_KREISE_AGS5[:-1]]
    rows_missing_zgb.append(_fuel_row("03241", "Region Hannover"))
    dp2 = _write_derived(tmp_path, "kba_kreis_fuel.csv", pd.DataFrame(rows_missing_zgb))
    with pytest.raises(RuntimeError):
        ft.load_kreis_fuel(dp2)


def _euro_row(kreis_ags5: str, kreis_name: str, teil: str) -> dict:
    euro_cols = ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other", "total"]
    euro_shares = [f"{c}_share" for c in ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"]]
    row = {"kreis_ags5": kreis_ags5, "kreis_name": kreis_name, "stichtag": "2025-01-01", "teil": teil}
    for c in euro_cols:
        row[c] = 1
    for c in euro_shares:
        row[c] = 0.0
    return row


def test_load_kreis_euro_allows_extra_non_zgb_kreis(tmp_path):
    """Task B3: load_kreis_euro keeps a non-ZGB Kreis row (extras allowed)."""
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel"):
            rows.append(_euro_row(k, k, teil))
    for teil in ("all", "diesel"):
        rows.append(_euro_row("03241", "Region Hannover", teil))
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    out = ft.load_kreis_euro(dp)
    assert set(ft.ZGB_KREISE_AGS5) <= set(out.loc[out["teil"] == "all", "kreis_ags5"])
    assert "03241" in set(out["kreis_ags5"])


def test_load_kreis_euro_rejects_unexpected_teil(tmp_path):
    euro_cols = ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other", "total"]
    euro_shares = [f"{c}_share" for c in ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"]]
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel", "petrol"):  # 'petrol' is unexpected
            row = {"kreis_ags5": k, "kreis_name": k, "stichtag": "2025-01-01", "teil": teil}
            for c in euro_cols:
                row[c] = 1
            for c in euro_shares:
                row[c] = 0.0
            rows.append(row)
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    with pytest.raises(RuntimeError):
        ft.load_kreis_euro(dp)


# ---------------------------------------------------------------------------
# Task B4: euro6d / euro6dtemp / euro6ab are ADDITIVE, OPTIONAL columns on
# kba_kreis_euro.csv -- present on a freshly-regenerated (post-B4) CSV, but
# ABSENT on the legacy-schema fixtures used above (and on any pre-B4 committed
# CSV). load_kreis_euro must degrade gracefully (fill 0.0 + log) rather than
# raise when they are missing, so every fixture above keeps working unchanged.
# ---------------------------------------------------------------------------
def test_load_kreis_euro_backward_compatible_without_substage_columns(tmp_path, caplog):
    """A legacy-schema kba_kreis_euro.csv (no euro6d/euro6dtemp/euro6ab) must
    still load: the loader fills the three columns with 0.0 and logs the
    fallback (no-silent-fallback rule), rather than raising."""
    import logging

    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel"):
            rows.append(_euro_row(k, k, teil))
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    with caplog.at_level(logging.INFO):
        out = ft.load_kreis_euro(dp)
    for c in ("euro6d", "euro6dtemp", "euro6ab"):
        assert c in out.columns
        assert (out[c] == 0.0).all()
    msgs = " ".join(r.message for r in caplog.records).lower()
    assert "euro6d" in msgs or "substage" in msgs, (
        f"Expected a log message about the missing Euro-6 substage columns; got: {msgs!r}"
    )


def test_load_kreis_euro_keeps_substage_columns_when_present(tmp_path):
    """When euro6d/euro6dtemp/euro6ab ARE present (post-B4 extractor run),
    the loader returns them unchanged (PRIMARY path, no fallback fill)."""
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel"):
            row = _euro_row(k, k, teil)
            row["euro6d"] = 3.0
            row["euro6dtemp"] = 2.0
            row["euro6ab"] = 5.0
            rows.append(row)
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    out = ft.load_kreis_euro(dp)
    assert (out["euro6d"] == 3.0).all()
    assert (out["euro6dtemp"] == 2.0).all()
    assert (out["euro6ab"] == 5.0).all()


def test_load_kreis_euro_rejects_negative_substage_counts(tmp_path):
    """A negative euro6d/euro6dtemp/euro6ab count is invalid data and must
    raise rather than load silently."""
    rows = []
    for k in ft.ZGB_KREISE_AGS5:
        for teil in ("all", "diesel"):
            row = _euro_row(k, k, teil)
            row["euro6d"] = 3.0
            row["euro6dtemp"] = 2.0
            row["euro6ab"] = -1.0  # invalid
            rows.append(row)
    dp = _write_derived(tmp_path, "kba_kreis_euro.csv", pd.DataFrame(rows))
    with pytest.raises(RuntimeError):
        ft.load_kreis_euro(dp)


# ---------------------------------------------------------------------------
# Task B4: load_fuel_euro6_substage_nds (FZ 27.4 Euro-6 substage table)
# ---------------------------------------------------------------------------
def _substage_row(fuel: str, substage: str, count: float, share: float) -> dict:
    return {
        "fuel": fuel, "substage": substage, "count": count, "share": share,
        "stichtag": "2025-01-01",
    }


def test_load_fuel_euro6_substage_nds_schema_and_shares(tmp_path):
    rows = [
        _substage_row("petrol", "euro6ab", 200, 0.2),
        _substage_row("petrol", "euro6dtemp", 200, 0.2),
        _substage_row("petrol", "euro6d", 600, 0.6),
        _substage_row("diesel", "euro6ab", 1000, 0.5),
        _substage_row("diesel", "euro6dtemp", 300, 0.15),
        _substage_row("diesel", "euro6d", 700, 0.35),
    ]
    dp = _write_derived(tmp_path, "kba_fuel_euro6_substage_nds.csv", pd.DataFrame(rows))
    out = ft.load_fuel_euro6_substage_nds(dp)
    assert set(out.columns) >= {"fuel", "substage", "count", "share", "stichtag"}
    assert set(out["substage"]) == {"euro6ab", "euro6dtemp", "euro6d"}
    per_fuel = out.groupby("fuel")["share"].sum()
    for fuel, total in per_fuel.items():
        assert total == pytest.approx(1.0, abs=1e-6)


def test_load_fuel_euro6_substage_nds_missing_column_raises(tmp_path):
    rows = [_substage_row("petrol", "euro6ab", 200, 1.0)]
    df = pd.DataFrame(rows).drop(columns=["share"])
    dp = _write_derived(tmp_path, "kba_fuel_euro6_substage_nds.csv", df)
    with pytest.raises(RuntimeError):
        ft.load_fuel_euro6_substage_nds(dp)


def test_load_fuel_euro6_substage_nds_rejects_unexpected_substage(tmp_path):
    rows = [_substage_row("petrol", "euro6e", 200, 1.0)]  # 'euro6e' is folded into euro6d, not a valid label
    dp = _write_derived(tmp_path, "kba_fuel_euro6_substage_nds.csv", pd.DataFrame(rows))
    with pytest.raises(RuntimeError):
        ft.load_fuel_euro6_substage_nds(dp)
