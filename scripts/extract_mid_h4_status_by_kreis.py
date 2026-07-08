"""Extract MiD 2023 Tabelle H4 "Ökonomischer Status des Haushalts" per Kreis.

Source: Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf
        (infas 7555, non-public), PDF page index 20 (the 21st page of the file;
        verified by scanning the table-of-contents headings in the PDF itself,
        not by trusting a fixed offset -- table H3 sits at index 19 and H4
        immediately follows it at index 20).
Output: eqasim-data/data/braunschweig/mid/mid2023_H4_status_by_kreis.csv

The regional study prints the 5-class economic-status distribution (row-%, weighted)
for the ZGB total ("Gesamt") and per Kreis ("Teilgebiete"). This is the direct
per-Kreis target for the economic_status PopulationSim control (issue #108).
"""
from __future__ import annotations

import csv
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PDF = (REPO / "eqasim-data" / "data" / "braunschweig"
       / "Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf")
OUT = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
       / "mid2023_H4_status_by_kreis.csv")
H4_PAGE_INDEX = 20  # 0-indexed PDF page; confirmed against the "Tabelle H4" heading text

STATUS_KEYS = ("very_low", "low", "medium", "high", "very_high")

NAME_TO_ARS5 = {
    "Gesamt": "03ZGB",
    "Braunschweig": "03101",
    "Salzgitter": "03102",
    "Wolfsburg": "03103",
    "Landkreis Gifhorn": "03151",
    "Landkreis Goslar": "03153",
    "Landkreis Helmstedt": "03154",
    "Landkreis Peine": "03157",
    "Landkreis Wolfenbüttel": "03158",
}


def _to_int(token: str) -> int:
    """German integer with '.' thousands separator -> int ('1.105' -> 1105)."""
    return int(token.replace(".", ""))


def parse_h4_rows(lines: list[str]) -> list[dict]:
    """Parse the H4 page lines into one dict per known area.

    A data row ends with the 5 integer status percentages; the two tokens before
    them are n_weighted and n_unweighted; the remaining leading tokens are the area
    name. Only areas in NAME_TO_ARS5 are kept (restricts to the ZGB total + the 8
    Kreise; ignores the Gemeindetyp/Kreistyp/Raumtyp blocks whose labels are absent
    from the map)."""
    rows = []
    for line in lines:
        tokens = line.split()
        if len(tokens) < 8:
            continue
        name = " ".join(tokens[:-7])
        ars5 = NAME_TO_ARS5.get(name)
        if ars5 is None:
            continue
        try:
            n_weighted = _to_int(tokens[-7])
            n_unweighted = _to_int(tokens[-6])
            status = [int(t) for t in tokens[-5:]]
        except ValueError:
            continue
        row = {"kreis": name, "ars5": ars5,
               "n_weighted": n_weighted, "n_unweighted": n_unweighted}
        row.update(dict(zip(STATUS_KEYS, status)))
        rows.append(row)
    return rows


def _pdf_lines(page_index: int) -> list[str]:
    import pdfplumber
    with pdfplumber.open(str(PDF)) as pdf:
        text = pdf.pages[page_index].extract_text() or ""
    return [ln for ln in text.split("\n") if ln.strip()]


def main() -> None:
    rows = parse_h4_rows(_pdf_lines(H4_PAGE_INDEX))
    expected = set(NAME_TO_ARS5.values())
    got = {r["ars5"] for r in rows}
    if got != expected:
        raise SystemExit(f"[extract H4] expected {sorted(expected)}, got {sorted(got)} "
                         f"- PDF layout may have changed on page {H4_PAGE_INDEX + 1}.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        fh.write('# Source: MiD 2023 Großraum Braunschweig (infas 7555), '
                 'Tabelle H4 "Ökonomischer Status des Haushalts", PDF page index 20 '
                 '(21st page of the file). Row-% (weighted).\n')
        writer = csv.DictWriter(fh, fieldnames=["kreis", "ars5", "n_weighted",
                                                "n_unweighted", *STATUS_KEYS])
        writer.writeheader()
        writer.writerows(rows)
    print(f"[extract H4] wrote {len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()
