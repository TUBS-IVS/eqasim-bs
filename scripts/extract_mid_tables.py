"""
Extractor for additional MiD 2023 regional sample tables (P0 set).

Reads the MiD 2023 regional table volume PDF at
    eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf

and writes per-table CSVs to
    eqasim-data/data/braunschweig/mid/<table>.csv

Tables in scope:
    P9       Umfang Erwerbstätigkeit           (Kreis × {Vollzeit,Teilzeit,...})
    P12.1    Verkehrsmittelnutzung Arbeitsweg  (Kreis × {Fuß,Fahrrad,ÖV,Auto,...})
    P13      Entfernung Arbeitsstätte          (Kreis × Distanzklassen + Mittel km)
    P17.1    Führerscheinbesitz Auto           (Kreis + Geschlecht + Alter × ja/nein)
    W10.3    Hauptverkehrsmittel               (Kreis × Modus, Wege-Ebene)
    W12      Wegelänge                         (Kreis × Distanzklassen)

Design notes
------------
The PDF uses fixed-width tabular text on each page (``Tabelle A`` variant has
the regional breakdown; ``Tabelle B`` has socio-economic breakdowns). We
parse the text rather than the layout: locate the ``Teilgebiete`` / ``Alter``
/ ``Geschlecht`` section headers and read the following rows line-by-line.

Rows inside a section follow ``<label> <n_weighted> <n_unweighted> <pct ...>``
with integer percentages; ``*`` marks cells suppressed for fallzahl < 30.
The trailing ``Mittel`` column (present in P13, P17.1) carries a decimal
separated by ``,`` which we normalise to ``.``.

The P24_1 ``TableSpec`` column list below is derived from
``braunschweig.data.mid.reference_tables.P24_RAW_COLUMN_BY_CATEGORY`` (issue
#329) instead of being re-spelled by hand, so this extractor cannot drift from
the raw-CSV boundary it (re)produces (``mid2023_P24_1*.csv`` keep the
codebook-German column headers for provenance).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pdfplumber

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from braunschweig.data.mid.reference_tables import (  # noqa: E402
    P24_RAW_COLUMN_BY_CATEGORY, PT_TICKET_CATEGORIES)

PDF = REPO / "eqasim-data" / "data" / "braunschweig" / \
    "Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf"
OUT_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "mid"


# ZGB-Kreise wie sie im PDF als Teilgebiete erscheinen → 5-stellige Kennziffer.
TEILGEBIETE = {
    "Braunschweig":           "03101",
    "Wolfsburg":              "03103",
    "Salzgitter":             "03102",
    "Landkreis Gifhorn":      "03151",
    "Landkreis Peine":        "03157",
    "Landkreis Helmstedt":    "03154",
    "Landkreis Wolfenbüttel": "03158",
    "Landkreis Goslar":       "03153",
}

# Known section headers in A-tables. Lines between a header and the next
# known header belong to that group.
SECTION_HEADERS = {
    "Teilgebiete",
    "regionalstatistischer Gemeindetyp",
    "Raumtyp",
    "Kreistyp",
    "Geschlecht",
    "Alter",
    # B-table headers (ignored for regional extraction but used as stop markers)
    "Höchster allgemeinbildender Schulabschluss",
    "Haupttätigkeit",
    "Ökonomischer Status des Haushalts",
    "Haushaltstyp",
    "Haushaltsgröße",
    "Mobilitätssegmente",
    "PKW-Führerscheinbesitz",
    "Mobilitätsausstattung des Haushalts",
    "(Elektro-)Fahrräder im Haushalt",
    "Anzahl Autos im Haushalt",
    "Haushalt mit Carsharing-Mitgliedschaft",
}


@dataclass
class TableSpec:
    code: str
    page_a: int             # 1-based page number of Tabelle A (regional)
    page_b: int | None      # 1-based page number of Tabelle B (socio-econ)
    columns: list[str]      # category column labels in order
    has_mittel: bool = False
    # If True, also emit ``_by_sex.csv`` and ``_by_age.csv`` derived from
    # the ``Geschlecht`` / ``Alter`` sections of Tabelle A.  Used as an
    # additional IPF margin (e.g. P24.1 PT-ticket type).
    extract_margins: bool = False


SPECS = [
    TableSpec("P9",    65, 66,
              ["vollzeit", "teilzeit", "geringfuegig",
               "sonstiges", "erwerbstaetig_unspec", "in_ausbildung",
               "nicht_erwerbstaetig", "keine_angabe"]),
    TableSpec("P12_1", 73, 74,
              ["zu_fuss", "fahrrad", "oeffentlich", "auto", "anderes",
               "ganz_unterschiedlich", "keine_feste_arbeit", "keine_angabe"]),
    TableSpec("P13",   77, 78,
              ["d_0", "d_0_5", "d_5_10", "d_10_20", "d_20_30",
               "d_30_50", "d_50_100", "d_100p", "keine_feste_arbeit",
               "keine_angabe"], has_mittel=True),
    TableSpec("P17_1", 87, 88,
              ["ja", "nein", "keine_angabe"],
              extract_margins=True),
    # P24.1 (page 105): Üblicherweise genutzte ÖPNV-Fahrkartenart I,
    # Basis = Personen ab 14 Jahre.  Nine row% columns.  ``extract_margins``
    # produces additional ``_by_sex.csv`` / ``_by_age.csv`` consumed by the
    # categorical PT-ticket IPF in braunschweig.synthesis.population.enriched.
    TableSpec("P24_1", 105, 106,
              [P24_RAW_COLUMN_BY_CATEGORY[c] for c in PT_TICKET_CATEGORIES],
              extract_margins=True),
    # P36.1 Mobilität am Stichtag (Mobilitätsquote, Basis = alle Personen).
    TableSpec("P36_1", 195, 196,
              ["nicht_mobil", "mobil", "unbekannt"]),
    # W1 Hauptwegezweck (analog MiD 2008): Heimwege werden auf den
    # vorherigen Weg-Zweck zurück-gemapped — daher keine eigene "home"-
    # Kategorie. Basis = alle Wege.
    TableSpec("W1", 231, 232,
              ["arbeit", "dienst", "ausbildung", "einkauf",
               "erledigung", "freizeit", "begleitung", "keine_angabe"]),
    # W2 Hauptwegezweck (unter Berücksichtigung der Wegekette): Heimwege
    # werden auf den ranghöchsten Zweck der Wegekette gemapped. Auch ohne
    # explizite "home"-Kategorie.
    TableSpec("W2", 233, 234,
              ["arbeit", "dienst", "ausbildung", "einkauf",
               "erledigung", "freizeit", "begleitung", "keine_angabe"]),
]

# Sex labels in the ``Geschlecht`` section (PDF) -> short key written to CSV.
SEX_LABELS = {
    "männlich": "male",
    "weiblich": "female",
}

# Age labels in the ``Alter`` section -> (age_lo, age_hi) inclusive bounds.
AGE_BINS = {
    "14 bis 17 Jahre":     (14, 17),
    "18 bis 29 Jahre":     (18, 29),
    "30 bis 39 Jahre":     (30, 39),
    "40 bis 49 Jahre":     (40, 49),
    "50 bis 59 Jahre":     (50, 59),
    "60 bis 64 Jahre":     (60, 64),
    "65 bis 74 Jahre":     (65, 74),
    "75 bis 79 Jahre":     (75, 79),
    "80 Jahre und älter":  (80, 999),
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^-?\*|^-?\d+(?:[.,]\d+)?$")


def _split_row(line: str):
    """Split a data row into (label_tokens, numeric_tokens).

    Numeric tokens are recognised greedily from the end of the line: a token
    either matches a plain integer, a decimal (with ``,`` separator), a ``*``
    suppression marker, or ``1.583`` thousand-separator form (digits + dot +
    triple digits). The label is whatever comes before the first numeric run.
    """
    tokens = line.split()
    # Find first numeric token index. Valid numeric tokens:
    #   - plain int:     re.fullmatch(r"\d+", ...)
    #   - thousand:      re.fullmatch(r"\d{1,3}\.\d{3}", ...)
    #   - decimal comma: re.fullmatch(r"\d+,\d+", ...)
    #   - suppressed:    tok == "*"
    num_pat = re.compile(r"(?:\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?|\*)")
    first_num = None
    for i, tok in enumerate(tokens):
        if num_pat.fullmatch(tok):
            first_num = i
            break
    if first_num is None or first_num == 0:
        return None
    label = " ".join(tokens[:first_num])
    values = tokens[first_num:]
    return label, values


def _to_number(tok: str):
    if tok == "*":
        return None
    return float(tok.replace(".", "").replace(",", "."))


def _extract_section(lines, header, stop_headers):
    """Return list of raw lines between ``header`` and the next stop header."""
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for line in lines[start:]:
        if line.strip() in stop_headers:
            break
        if line.strip():
            out.append(line)
    return out


def _parse_region_rows(lines, num_cols, has_mittel):
    rows = []
    for raw in lines:
        parsed = _split_row(raw)
        if not parsed:
            continue
        label, values = parsed
        if label not in TEILGEBIETE:
            continue
        if len(values) < 2 + num_cols + (1 if has_mittel else 0):
            continue
        n_w = _to_number(values[0])
        n_u = _to_number(values[1])
        pct = [_to_number(v) for v in values[2:2 + num_cols]]
        mittel = _to_number(values[2 + num_cols]) if has_mittel else None
        rows.append({
            "kreis": label,
            "ars5": TEILGEBIETE[label],
            "n_weighted": n_w,
            "n_unweighted": n_u,
            **{col: val for col, val in zip(range(num_cols), pct)},
            **({"mittel": mittel} if has_mittel else {}),
        })
    return rows


def _parse_gesamt(lines, num_cols, has_mittel):
    for raw in lines:
        parsed = _split_row(raw)
        if not parsed:
            continue
        label, values = parsed
        if label != "Gesamt":
            continue
        n_w = _to_number(values[0])
        n_u = _to_number(values[1])
        pct = [_to_number(v) for v in values[2:2 + num_cols]]
        mittel = _to_number(values[2 + num_cols]) if has_mittel else None
        return {
            "kreis": "Gesamt",
            "ars5": "03ZGB",
            "n_weighted": n_w,
            "n_unweighted": n_u,
            **{col: val for col, val in zip(range(num_cols), pct)},
            **({"mittel": mittel} if has_mittel else {}),
        }
    return None


def _parse_labelled_rows(lines, allowed_labels, num_cols):
    """Parse rows whose label is in ``allowed_labels`` (a set of strings).

    Unlike :func:`_split_row`, this matches the label by **prefix** to
    support labels that begin with a digit (e.g. ``"14 bis 17 Jahre"``),
    which the numeric tokenizer would otherwise confuse with a value.
    """
    rows = []
    sorted_labels = sorted(allowed_labels, key=len, reverse=True)
    for raw in lines:
        stripped = raw.strip()
        match_label = None
        for lbl in sorted_labels:
            if stripped.startswith(lbl + " "):
                match_label = lbl
                break
        if match_label is None:
            continue
        rest = stripped[len(match_label):].strip()
        values = rest.split()
        if len(values) < 2 + num_cols:
            continue
        n_w = _to_number(values[0])
        n_u = _to_number(values[1])
        pct = [_to_number(v) for v in values[2:2 + num_cols]]
        rows.append({
            "label": match_label,
            "n_weighted": n_w,
            "n_unweighted": n_u,
            **{col: val for col, val in zip(range(num_cols), pct)},
        })
    return rows


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_table(pdf, spec: TableSpec) -> pd.DataFrame:
    text = pdf.pages[spec.page_a - 1].extract_text() or ""
    lines = text.split("\n")

    teilgebiete = _extract_section(lines, "Teilgebiete", SECTION_HEADERS)
    gesamt = _parse_gesamt(lines, len(spec.columns), spec.has_mittel)
    rows = _parse_region_rows(teilgebiete, len(spec.columns), spec.has_mittel)

    if gesamt:
        rows.insert(0, gesamt)

    # Rename numeric column keys to their spec.columns labels.
    renamed = []
    for r in rows:
        out = {"kreis": r["kreis"], "ars5": r["ars5"],
               "n_weighted": r["n_weighted"],
               "n_unweighted": r["n_unweighted"]}
        for i, col in enumerate(spec.columns):
            out[col] = r.get(i)
        if spec.has_mittel:
            out["mittel"] = r.get("mittel")
        renamed.append(out)

    df = pd.DataFrame(renamed)
    return df


def extract_margins(pdf, spec: TableSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Extract the ``Geschlecht`` and ``Alter`` rows of Tabelle A.

    Returns ``(by_sex, by_age)`` with one row per category and the full
    ticket-type column set.
    """
    text = pdf.pages[spec.page_a - 1].extract_text() or ""
    lines = text.split("\n")
    n = len(spec.columns)

    sex_lines = _extract_section(lines, "Geschlecht", SECTION_HEADERS)
    sex_rows = _parse_labelled_rows(sex_lines, set(SEX_LABELS), n)
    by_sex = []
    for r in sex_rows:
        out = {"sex": SEX_LABELS[r["label"]],
               "n_weighted": r["n_weighted"],
               "n_unweighted": r["n_unweighted"]}
        for i, col in enumerate(spec.columns):
            out[col] = r.get(i)
        by_sex.append(out)

    age_lines = _extract_section(lines, "Alter", SECTION_HEADERS)
    age_rows = _parse_labelled_rows(age_lines, set(AGE_BINS), n)
    by_age = []
    for r in age_rows:
        lo, hi = AGE_BINS[r["label"]]
        out = {"age_lo": lo, "age_hi": hi, "label": r["label"],
               "n_weighted": r["n_weighted"],
               "n_unweighted": r["n_unweighted"]}
        for i, col in enumerate(spec.columns):
            out[col] = r.get(i)
        by_age.append(out)

    return pd.DataFrame(by_sex), pd.DataFrame(by_age)


def main() -> int:
    if not PDF.exists():
        sys.stderr.write(f"[mid-extract] PDF not found: {PDF}\n")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with pdfplumber.open(str(PDF)) as pdf:
        for spec in SPECS:
            df = extract_table(pdf, spec)
            out = OUT_DIR / f"mid2023_{spec.code}.csv"
            df.to_csv(out, index=False, encoding="utf-8")
            n_kreise = (df["kreis"] != "Gesamt").sum()
            print(
                f"[mid-extract] {spec.code:6s} page {spec.page_a:3d} -> "
                f"{out.name}  ({len(df)} rows, {n_kreise} Kreise)"
            )
            if spec.extract_margins:
                df_sex, df_age = extract_margins(pdf, spec)
                out_sex = OUT_DIR / f"mid2023_{spec.code}_by_sex.csv"
                out_age = OUT_DIR / f"mid2023_{spec.code}_by_age.csv"
                df_sex.to_csv(out_sex, index=False, encoding="utf-8")
                df_age.to_csv(out_age, index=False, encoding="utf-8")
                print(
                    f"[mid-extract] {spec.code:6s}        -> "
                    f"{out_sex.name}  ({len(df_sex)} rows)"
                )
                print(
                    f"[mid-extract] {spec.code:6s}        -> "
                    f"{out_age.name}  ({len(df_age)} rows)"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
