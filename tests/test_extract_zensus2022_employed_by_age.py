"""Extraction of Zensus 2022 employed persons by age class and sex per Kreis (ADR-0137).

The committed employment-grid reference had an exact age shape only for the three kreisfreie
Staedte, so five of the eight ZGB Kreise fell back to a national shape. The Zensus 2022
Regionaltabelle "Bildung und Erwerbstaetigkeit" publishes the same quantity for EVERY Kreis and
by sex (sheet "CSV-ET_Alter"). These tests pin the extraction on a synthetic workbook laid out
like the real one: the readable sheet "ET Alter" names the age classes, the machine sheet
"CSV-ET_Alter" carries the numbers in columns ET_ALTER_ERW__<class>_<M|W>.
"""
from __future__ import annotations

import openpyxl
import pandas as pd
import pytest

from scripts import extract_zensus2022_employed_by_age as ex

#: The class labels exactly as the real sheet spells them (row 4, one label per three columns).
REAL_CLASS_LABELS = ["15 - 19 ", "20 - 29 ", "30 - 39 ", "40 - 49 ", "50 - 59 ", "60 - 67 ", "68 und älter"]


def _counts(base):
    """(zusammen, maennlich, weiblich) for the seven classes, men = 60 % of each class."""
    values = []
    for index in range(7):
        total = base + 100 * index
        values += [total, int(0.6 * total), total - int(0.6 * total)]
    return values


def _row(region, name, level, base):
    counts = _counts(base)
    total = sum(counts[0::3])
    return [20220515, region, name, level, total, sum(counts[1::3]), sum(counts[2::3])] + counts


def _workbook(tmp_path, rows, class_labels=REAL_CLASS_LABELS):
    workbook = openpyxl.Workbook()
    readable = workbook.active
    readable.title = "ET Alter"
    readable.append(["Zensus 2022 – Ausgewählte Zensusergebnisse"])
    readable.append(["Erwerbstätige Personen nach Altersklassen"])
    readable.append(["Amtlicher Regionalschlüssel (ARS)", "Name", "Regionalebene",
                     "Insgesamt", "Männlich", "Weiblich", "Im Alter von … bis … Jahren"])
    label_row = [None] * 6
    for label in class_labels:
        label_row += [label, None, None]
    readable.append(label_row)
    machine = workbook.create_sheet("CSV-ET_Alter")
    header = ["Berichtszeitpunkt", "_RS", "Name", "Regionalebene",
              "ET_ALTER_ERW", "ET_ALTER_ERW__M", "ET_ALTER_ERW__W"]
    for index in range(1, 8):
        header += [f"ET_ALTER_ERW__{index}", f"ET_ALTER_ERW__{index}_M", f"ET_ALTER_ERW__{index}_W"]
    machine.append(header)
    for row in rows:
        machine.append(row)
    path = tmp_path / "Regionaltabelle_Bildung_Erwerbstaetigkeit.xlsx"
    workbook.save(path)
    return path


def _rows():
    return [
        _row("00", "Deutschland", "Bund", 100000),
        _row("03", "Niedersachsen", "Land", 9000),
        _row("03151", "Gifhorn", "Stadtkreis/kreisfreie Stadt/Landkreis", 1000),
        _row("031510009009", "Gifhorn, Stadt", "Gemeinde", 300),
    ]


def test_extracts_one_tidy_row_per_region_class_and_sex(tmp_path):
    frame = ex.extract_employed_by_age(_workbook(tmp_path, _rows()), regions=["00", "03151"])

    assert list(frame.columns) == ["region", "region_name", "age_class", "sex", "employed_persons"]
    assert len(frame) == 2 * 7 * 2
    assert list(frame["age_class"].unique()) == list(ex.AGE_CLASSES)
    gifhorn = frame[(frame["region"] == "03151") & (frame["age_class"] == "30-39")]
    assert dict(zip(gifhorn["sex"], gifhorn["employed_persons"])) == {"M": 720, "F": 480}


def test_the_class_order_is_verified_against_the_readable_sheet(tmp_path):
    swapped = list(REAL_CLASS_LABELS)
    swapped[2], swapped[3] = swapped[3], swapped[2]
    with pytest.raises(ValueError, match="age classes"):
        ex.extract_employed_by_age(_workbook(tmp_path, _rows(), swapped), regions=["03151"])


def test_a_requested_region_that_is_absent_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="03157"):
        ex.extract_employed_by_age(_workbook(tmp_path, _rows()), regions=["03151", "03157"])


def test_a_suppressed_count_for_a_requested_region_fails_loudly(tmp_path):
    rows = _rows()
    # The Zensus marks a cell withheld for secrecy with "/"; column 11 is ET_ALTER_ERW__2_M, a
    # count the extraction uses (it reads the per-sex columns, not the "zusammen" ones).
    rows[2][11] = "/"
    with pytest.raises(ValueError, match="03151"):
        ex.extract_employed_by_age(_workbook(tmp_path, rows), regions=["03151"])


def test_the_comparison_mode_writes_the_change_against_the_previous_reference(tmp_path, capsys):
    source = _workbook(tmp_path, _rows())
    destination = tmp_path / "reference.csv"
    legacy = tmp_path / "legacy.csv"
    pd.DataFrame([("DE_large_gemeinden", "30-39", 100, 50, 0.5), ("DE_large_gemeinden", "50-59", 100, 50, 0.5)],
                 columns=["region", "age_band", "total", "erwerbstaetige", "rate"]).to_csv(legacy, index=False)
    comparison = tmp_path / "comparison.csv"

    ex.main(["--regionaltabelle", str(source), "--out", str(destination), "--regions", "00", "03151",
             "--compare-with", str(legacy), "--comparison-out", str(comparison)])

    table = pd.read_csv(comparison, dtype={"kreis": str})
    assert set(table["kreis"]) == {"03151"}, "Germany is the fallback row, not a Kreis to compare"
    assert "change_percentage_points" in table.columns
    assert "03151" in capsys.readouterr().out


def test_the_written_csv_carries_its_provenance_and_round_trips(tmp_path):
    source = _workbook(tmp_path, _rows())
    destination = tmp_path / "zensus2022_employed_by_age_kreis.csv"

    ex.main(["--regionaltabelle", str(source), "--out", str(destination), "--regions", "00", "03151"])

    text = destination.read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "CSV-ET_Alter" in text and "2022-05-15" in text and "sha256" in text
    frame = pd.read_csv(destination, comment="#", dtype={"region": str})
    assert frame.equals(ex.extract_employed_by_age(source, regions=["00", "03151"]))
