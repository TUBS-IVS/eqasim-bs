"""Regression tests for the 2026-07-17 latent-FRAGILE hardening batch.

Each target was verified NOT a live bug on today's clean inputs, but was one
input-drift away from a silent failure. These tests pin the hardened behaviour:

- ``kreis_key_guard.keep_valid_kreis5`` + its wiring into the INKAR full-panel
  and BA sector-pendler loaders (float-formatted / aggregate keys are dropped
  loudly, not carried as non-joining garbage keys).
- ``inspire.landuse`` raises (does not silently return an empty prior) when the
  feature flag is ON but the tile is missing.
- ``cordon.network.read_matsim_links`` counts + logs links referencing an
  unknown node instead of dropping them silently.
"""
from __future__ import annotations

import gzip
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.kreis_key_guard import keep_valid_kreis5  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal synpp context stub (subset used by these stages)
# ---------------------------------------------------------------------------

class StubContext:
    def __init__(self, config: dict):
        self._config = config

    def config(self, key, default=...):
        if key in self._config:
            return self._config[key]
        if default is not ...:
            return default
        raise KeyError(f"StubContext missing config key: {key}")


# ---------------------------------------------------------------------------
# Item 1a -- keep_valid_kreis5 helper
# ---------------------------------------------------------------------------

def test_keep_valid_kreis5_drops_float_and_aggregate_keys():
    df = pd.DataFrame({
        "ars5": ["03101", "3101.0", "DG", "03153"],
        "value": [1, 2, 3, 4],
    })
    kept = keep_valid_kreis5(df, "ars5", source="test")
    assert list(kept["ars5"]) == ["03101", "03153"]
    assert list(kept["value"]) == [1, 4]


def test_keep_valid_kreis5_multi_column_requires_both_valid():
    df = pd.DataFrame({
        "home_kreis": ["03101", "03101", "03102"],
        "work_kreis": ["03102", "3102.0", "03103"],
        "flow": [10, 20, 30],
    })
    kept = keep_valid_kreis5(df, ["home_kreis", "work_kreis"], source="test")
    # Row 1 kept (both valid), row 2 dropped (work float-formatted), row 3 kept.
    assert list(kept["flow"]) == [10, 30]


def test_keep_valid_kreis5_raises_when_all_dropped():
    df = pd.DataFrame({"ars5": ["3101.0", "DG"], "value": [1, 2]})
    with pytest.raises(RuntimeError, match="dropped ALL"):
        keep_valid_kreis5(df, "ars5", source="test")


def test_keep_valid_kreis5_logs_drop_rate(caplog):
    df = pd.DataFrame({"ars5": ["03101", "DG"], "value": [1, 2]})
    with caplog.at_level(logging.WARNING):
        keep_valid_kreis5(df, "ars5", source="test")
    assert any("Kreis-key guard" in r.message for r in caplog.records)


def test_keep_valid_kreis5_empty_frame_does_not_raise():
    df = pd.DataFrame({"ars5": pd.Series([], dtype=str)})
    kept = keep_valid_kreis5(df, "ars5", source="test")
    assert kept.empty


# ---------------------------------------------------------------------------
# Item 1b -- BA sector-pendler loader drops garbage keys
# ---------------------------------------------------------------------------

def test_pendler_detailed_drops_non_kreis5_rows(tmp_path):
    from braunschweig.data.ba import pendler_detailed

    csv = tmp_path / "pendler.csv"
    csv.write_text(
        "home_kreis;work_kreis;sector;flow\n"
        "03101;03102;A;100\n"
        "3101;3102;B;50\n"       # short numeric -> zfill -> 03101/03102, kept
        "DG;03101;C;10\n"        # aggregate label -> dropped
        "03101;03151.0;D;7\n",   # float-formatted work key -> dropped
        encoding="utf-8",
    )
    ctx = StubContext({
        "data_path": str(tmp_path),
        "braunschweig.ba_pendler_detailed_path": "pendler.csv",
        "braunschweig.ba_pendler_detailed_separator": ";",
        "braunschweig.political_prefix": None,
    })
    out = pendler_detailed.execute(ctx)
    assert len(out) == 2
    assert out["home_kreis"].str.fullmatch(r"\d{5}").all()
    assert out["work_kreis"].str.fullmatch(r"\d{5}").all()


# ---------------------------------------------------------------------------
# Item 2 -- inspire landuse: no silent empty prior when flag ON but file missing
# ---------------------------------------------------------------------------

def _landuse_ctx(tmp_path, *, flag: bool):
    return StubContext({
        "data_path": str(tmp_path),
        "braunschweig.inspire_landuse_path": "does_not_exist.parquet",
        "braunschweig.use_landuse_prior": flag,
    })


def test_landuse_execute_raises_when_flag_on_but_file_missing(tmp_path):
    from braunschweig.data.inspire import landuse
    with pytest.raises(RuntimeError, match="use_landuse_prior is ON"):
        landuse.execute(_landuse_ctx(tmp_path, flag=True))


def test_landuse_validate_raises_when_flag_on_but_file_missing(tmp_path):
    from braunschweig.data.inspire import landuse
    with pytest.raises(RuntimeError, match="use_landuse_prior is ON"):
        landuse.validate(_landuse_ctx(tmp_path, flag=True))


def test_landuse_flag_off_returns_empty_no_raise(tmp_path):
    from braunschweig.data.inspire import landuse
    gdf = landuse.execute(_landuse_ctx(tmp_path, flag=False))
    assert len(gdf) == 0
    assert landuse.validate(_landuse_ctx(tmp_path, flag=False)) == 0


# ---------------------------------------------------------------------------
# Item 4 -- cordon network link-skip counter
# ---------------------------------------------------------------------------

_NETWORK_WITH_DANGLING_LINK = (
    b"""<?xml version="1.0"?><network><nodes>"""
    b"""<node id="1" x="600000" y="5800000"/><node id="2" x="601000" y="5800000"/>"""
    b"""</nodes><links>"""
    b"""<link id="L1" from="1" to="2" capacity="8000"/>"""
    b"""<link id="L2" from="1" to="99" capacity="500"/>"""  # 99 unknown -> skipped
    b"""</links></network>"""
)


def test_read_matsim_links_counts_skipped_dangling_links(tmp_path, caplog):
    from braunschweig.data.cordon.network import read_matsim_links

    p = tmp_path / "network.xml.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(_NETWORK_WITH_DANGLING_LINK)

    with caplog.at_level(logging.WARNING):
        links = read_matsim_links(str(p), crs="EPSG:25832")

    assert list(links["link_id"]) == ["L1"]  # dangling L2 excluded
    assert any(
        "skipped" in r.message and "unknown from/to node" in r.message
        for r in caplog.records
    ), "expected a WARNING logging the skipped-link count"
