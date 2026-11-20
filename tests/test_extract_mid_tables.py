"""Parser contract of scripts/extract_mid_tables.py on the text of a real MiD regional page.

Defects caught: a German thousands separator ("1.105") or decimal comma ("0,3") split into
two tokens shifts every share of a row by one column (a probe parser did exactly this on
2026-11-20), and a page whose Teilgebiete block lost a Kreis must fail instead of writing a
table without that Kreis.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.extract_mid_tables import SPECS, parse_table_lines  # noqa: E402

H12_1 = next(spec for spec in SPECS if spec.code == "H12_1")

# Text lines of MiD 2023 Grossraum Braunschweig, Tabelle H12.1 (PDF page 31), as pdfplumber
# returns them, cut after the first line of the next section.
H12_1_PAGE = [
    "MiD 2023 - Großraum Braunschweig - Haushalte V2",
    "Tabelle H12.1 Anzahl Elektrofahrräder und Pedelecs im Haushalt",
    "Basis: Alle Haushalte",
    "Anzahl Anzahl Anzahl Elektrofahrräder und Pedelecs im Haushalt Mittel",
    "Haushalte Haushalte",
    "Zeilen% (gewichtet) (ungewichtet) keins 1 2 3 4 und mehr keine Angabe",
    "Gesamt 3.013 5.924 78 13 8 0 0 0 0,3",
    "Teilgebiete",
    "Braunschweig 648 1.105 81 12 7 1 0 0 0,3",
    "Wolfsburg 155 508 81 11 8 0 0 0 0,3",
    "Salzgitter 167 792 77 13 8 1 0 0 0,3",
    "Landkreis Gifhorn 530 706 76 16 8 0 0 0 0,3",
    "Landkreis Peine 316 669 77 15 8 0 0 0 0,3",
    "Landkreis Helmstedt 358 644 76 14 9 0 0 0 0,3",
    "Landkreis Wolfenbüttel 310 510 75 11 12 0 1 0 0,4",
    "Landkreis Goslar 528 990 81 13 5 1 0 0 0,3",
    "regionalstatistischer Gemeindetyp",
    "Regiopole 970 2.405 80 12 7 1 0 0 0,3",
]

COLUMNS = ["n_weighted", "n_unweighted", "ebikes_0", "ebikes_1", "ebikes_2", "ebikes_3",
           "ebikes_4plus", "keine_angabe", "mittel"]


def test_parse_h12_1_page_keeps_every_number_in_its_column():
    df = parse_table_lines(H12_1_PAGE, H12_1).set_index("ars5")
    assert list(df.index) == ["03ZGB", "03101", "03103", "03102", "03151", "03157",
                              "03154", "03158", "03153"]
    rows = df[COLUMNS].to_dict("index")
    assert rows["03ZGB"] == dict(zip(COLUMNS, [3013, 5924, 78, 13, 8, 0, 0, 0, 0.3]))
    assert rows["03101"] == dict(zip(COLUMNS, [648, 1105, 81, 12, 7, 1, 0, 0, 0.3]))
    assert rows["03103"] == dict(zip(COLUMNS, [155, 508, 81, 11, 8, 0, 0, 0, 0.3]))
    assert rows["03158"] == dict(zip(COLUMNS, [310, 510, 75, 11, 12, 0, 1, 0, 0.4]))
    assert df.loc["03158", "kreis"] == "Landkreis Wolfenbüttel"


def test_parse_fails_when_a_kreis_row_is_missing():
    page = [line for line in H12_1_PAGE if not line.startswith("Wolfsburg")]
    with pytest.raises(ValueError, match="03103"):
        parse_table_lines(page, H12_1)
