"""Fallback-rate instrumentation of the data loaders (audit D, issue-less package).

Each test drives the real loader on a small synthetic input where the PRIMARY
path covers everything, asserts the returned table and that the rate line
reports no fallback (CLAUDE.md "Fallback transparency", point 3).
"""
from __future__ import annotations

import logging

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Site 1: pendler (_read_one + execute dedup)
# ---------------------------------------------------------------------------

_PENDLER_HEADER = "Arbeitsort;RS;Wohnort;RS;Insgesamt;Maenner;Frauen;Deutsche;Auslaender;Azubis"


def _write_pendler(path, rows):
    content = "\n".join(["preamble"] * 10 + [_PENDLER_HEADER] + rows) + "\n"
    path.write_text(content, encoding="utf-8")


def test_pendler_primary_path_reports_zero_suppressed_and_dedup(tmp_path, capsys):
    from braunschweig.data.census import pendler

    _write_pendler(tmp_path / "ein.csv", [
        "Braunschweig;03101;Wolfsburg;03103;1.000;1;1;1;1;1",
        "Braunschweig;03101;Niedersachsen;031xx;9.999;1;1;1;1;1",
    ])
    _write_pendler(tmp_path / "aus.csv", [
        # Same ordered pair as the first Einpendler row (orig=03103, dest=03101).
        "Wolfsburg;03103;Braunschweig;03101;1.000;1;1;1;1;1",
        "Wolfsburg;03103;Peine;03157;200;1;1;1;1;1",
    ])

    class Ctx:
        def config(self, key, default=None):
            return {
                "data_path": str(tmp_path),
                "braunschweig.pendler_ein_path": "ein.csv",
                "braunschweig.pendler_aus_path": "aus.csv",
                "braunschweig.political_prefix": ["031"],
            }[key]

    out = pendler.execute(Ctx())
    text = capsys.readouterr().out

    assert sorted(out.itertuples(index=False, name=None)) == [
        ("03103", "03101", 1000), ("03103", "03157", 200),
    ]
    assert "kept 1/2 rows, dropped 1 aggregate rows, dropped 0 suppressed rows" in text
    assert "dedup: 3 rows -> 2 pairs (1 collapsed by max, 0 pairs with differing flows" in text
    assert "WARNING" not in text


# ---------------------------------------------------------------------------
# Site 3: MiD reference_tables zero-sum rows -> uniform vector
# ---------------------------------------------------------------------------

def _write_license_csv(tmp_path, rows):
    from braunschweig.data.mid import reference_tables as rt

    directory = tmp_path.joinpath(*rt.MID_SUBDIR.split("/"))
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["ars5,ja,nein,keine_angabe"] + rows
    (directory / "mid2023_P17_1.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_license_breakdown_primary_path_logs_no_fallback(tmp_path, caplog):
    from braunschweig.data.mid import reference_tables as rt

    _write_license_csv(tmp_path, ["03101,80,15,5", "03ZGB,50,50,0"])
    with caplog.at_level(logging.INFO, logger=rt.logger.name):
        by_kreis, region = rt.load_license_breakdown(str(tmp_path))

    assert by_kreis["03101"].tolist() == pytest.approx([0.8, 0.15, 0.05])
    assert region.tolist() == pytest.approx([0.5, 0.5, 0.0])
    messages = [r for r in caplog.records if "load_license_breakdown" in r.getMessage()]
    assert len(messages) == 1
    assert "fallback 0 zero-sum rows" in messages[0].getMessage()
    assert messages[0].levelno == logging.INFO


def test_license_breakdown_zero_sum_row_is_counted_and_warned(tmp_path, caplog):
    from braunschweig.data.mid import reference_tables as rt

    _write_license_csv(tmp_path, ["03101,0,0,0", "03103,60,30,10", "03ZGB,50,50,0"])
    with caplog.at_level(logging.INFO, logger=rt.logger.name):
        by_kreis, _ = rt.load_license_breakdown(str(tmp_path))

    assert by_kreis["03101"].tolist() == pytest.approx([1 / 3] * 3)  # behaviour unchanged
    messages = [r for r in caplog.records if "load_license_breakdown" in r.getMessage()]
    assert messages[0].levelno == logging.WARNING
    assert "fallback 1 zero-sum rows" in messages[0].getMessage()


def _run_pendler(tmp_path, ein_rows, aus_rows):
    from braunschweig.data.census import pendler

    _write_pendler(tmp_path / "ein.csv", ein_rows)
    _write_pendler(tmp_path / "aus.csv", aus_rows)

    class Ctx:
        def config(self, key):
            return {
                "data_path": str(tmp_path),
                "braunschweig.pendler_ein_path": "ein.csv",
                "braunschweig.pendler_aus_path": "aus.csv",
                "braunschweig.political_prefix": ["031"],
            }[key]

    return pendler.execute(Ctx())


def test_pendler_dedup_zero_vs_positive_is_info_but_two_positives_warn(tmp_path, capsys):
    # Zero in one view, positive in the other: expected suppression artefact.
    out = _run_pendler(
        tmp_path,
        ["Braunschweig;03101;Wolfsburg;03103;0;1;1;1;1;1"],
        ["Wolfsburg;03103;Braunschweig;03101;300;1;1;1;1;1"],
    )
    text = capsys.readouterr().out
    assert out["flow"].tolist() == [300]
    assert "1 pairs with a zero in one view" in text
    assert "0 pairs with differing flows" in text
    assert "WARNING" not in text

    # Two different positive values for the same pair: genuine disagreement.
    out = _run_pendler(
        tmp_path,
        ["Braunschweig;03101;Wolfsburg;03103;100;1;1;1;1;1"],
        ["Wolfsburg;03103;Braunschweig;03101;300;1;1;1;1;1"],
    )
    text = capsys.readouterr().out
    assert out["flow"].tolist() == [300]  # max, unchanged
    assert "WARNING" in text and "1 pairs with differing flows" in text

    # Real exports: many pairs in one file only plus ONE both-positive disagreement.
    # The conflict share of ALL pairs is tiny, yet any disagreement must warn, and the
    # rate is printed against the pairs present in both files.
    ein = ["Braunschweig;03101;Wolfsburg;03103;100;1;1;1;1;1"] + [
        "Braunschweig;03101;Ort{};03{:03d};5;1;1;1;1;1".format(i, 200 + i) for i in range(40)
    ]
    aus = ["Wolfsburg;03103;Braunschweig;03101;300;1;1;1;1;1"] + [
        "Ort{};03{:03d};Wolfsburg;03103;7;1;1;1;1;1".format(i, 300 + i) for i in range(40)
    ]
    _run_pendler(tmp_path, ein, aus)
    text = capsys.readouterr().out
    assert "WARNING" in text
    assert "1 pairs with differing flows = 100.0% of the 1 pairs present in both files" in text
