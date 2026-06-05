"""
Extract regional commute tables from the MiD 2023 Grossraum Braunschweig PDF.

Source: Ergebnistabellen_MiD2023_Version2_infas_7555_Grossraum_Braunschweig.pdf
        (infas 7555, non-public)

Tables extracted (all Tabelle A, regional breakdown):

  P38.4  Ueberwiegend genutztes Verkehrsmittel auf regelmaessigen beruflichen
         Wegen am Stichtag
         Pages 209 + 211 (1-based), i.e. 0-indexed 208 + 210.  The table
         spans two pages (15 mode columns total, 8 on part 1/2 and 7 on
         part 2/2).  We concatenate the mode columns.
         Output: mid2023zgb_P38_4_commute_mode.csv

  P38.2  Gesamtstrecke der regelmaessigen beruflichen Wege am Stichtag
         Page 205 (1-based), 0-indexed 204.
         10 distance-band percentage columns + arithmetic mean column.
         Output: mid2023zgb_P38_2_commute_distance.csv

  W12    Wegelaenge by Hauptwegezweck
         Page 275 (1-based), 0-indexed 274.
         9 distance-band percentage columns + arithmetic mean column.
         We extract the Hauptwegezweck section rows only
         (Arbeit, dienstlich, Ausbildung, Einkauf, Erledigung, Freizeit,
         Begleitung).
         Output: mid2023zgb_W12_triplength_by_purpose.csv

Design notes
------------
The PDF page text is extracted with pdfplumber and parsed line-by-line.
The pure function ``parse_zgb_percent_row`` handles the row format:

    <label> <n_weighted> <n_unweighted> <pct_col_1> ... <pct_col_n>

where the first two numeric tokens are base counts (dropped) and the
trailing ``n_value_cols`` tokens are the percentage (or decimal mean)
columns.  Suppressed cells (``/``, ``*``, ``-``) become 0.

All three CSVs are sanity/documentation artefacts committed alongside the
code so another researcher can verify the extraction without the PDF.
They are NOT consumed by the mode-reference builder (which relies on
national MiD data from mikrozensus files).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pdfplumber


REPO = Path(__file__).resolve().parents[1]
PDF = (
    REPO
    / "eqasim-data"
    / "data"
    / "braunschweig"
    / "Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf"
)
OUT_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "mikrozensus"

# ZGB Teilgebiete labels as they appear in the PDF.
TEILGEBIETE = [
    "Braunschweig",
    "Wolfsburg",
    "Salzgitter",
    "Landkreis Gifhorn",
    "Landkreis Peine",
    "Landkreis Helmstedt",
    "Landkreis Wolfenbüt tel",  # PDF may split the umlaut word
    "Landkreis Wolfenbüt-tel",
    "Landkreis Wolfenbüttel",
    "Landkreis Goslar",
]

# Canonical label mapping (normalise hyphenated / space-split variants).
TEILGEBIETE_CANONICAL = {
    "Braunschweig":                  "Braunschweig",
    "Wolfsburg":                     "Wolfsburg",
    "Salzgitter":                    "Salzgitter",
    "Landkreis Gifhorn":             "Landkreis Gifhorn",
    "Landkreis Peine":               "Landkreis Peine",
    "Landkreis Helmstedt":           "Landkreis Helmstedt",
    "Landkreis Wolfenbüt tel":  "Landkreis Wolfenbüttel",
    "Landkreis Wolfenbüt-tel":  "Landkreis Wolfenbüttel",
    "Landkreis Wolfenbüttel":   "Landkreis Wolfenbüttel",
    "Landkreis Goslar":              "Landkreis Goslar",
}

# Stop markers that end a section in Tabelle A pages.
SECTION_STOP_KEYWORDS = {
    "regionalstatistischer",
    "Raumtyp",
    "Kreistyp",
    "Geschlecht",
    "Alter",
    "Hauptwegezweck",
    "Hauptverkehrsmittel",
    "Wegelaenge",
    "Wegelange",
    "Bundesland",
    "Startzeit",
    "wegkm_imp",
    "Höchster",
    "Haupttätigkeit",
    "Ökonomischer",
    "Haushaltstyp",
    "Haushaltsgröße",
    "Mobiltätssegmente",
    "PKW-Führerscheinbesitz",
    "Mobiltätsausstattung",
    "Stichtag",
    "P_RBW",
}

# Hauptwegezweck labels in the W12 table.
HAUPTWEGEZWECK = {
    "Arbeit",
    "dienstlich",
    "Ausbildung",
    "Einkauf",
    "Erledigung",
    "Freizeit",
    "Begleitung",
}


# ---------------------------------------------------------------------------
# Pure parser (unit-tested)
# ---------------------------------------------------------------------------

def parse_zgb_percent_row(line: str, n_value_cols: int) -> tuple[str, list[int]]:
    """Parse a MiD ZGB table row '<label> <n_weighted> <n_unweighted> <pct...>'.

    The first two numeric tokens after the label are base counts; the trailing
    ``n_value_cols`` numeric tokens are the percentage columns. Suppressed cells
    '/' or '*' become 0. Returns (label, [int, ...]).

    The label is reconstructed by greedily consuming all trailing tokens that
    look like numbers or suppressed-cell markers ('/', '*', '-'), then joining
    whatever is left as the label.  This works correctly for labels that
    contain spaces (e.g. 'Landkreis Gifhorn') and for the Gesamt row.
    """
    tokens = line.split()
    # Walk backwards from the end collecting numeric / suppressed tokens.
    i = len(tokens)
    while i > 0 and re.fullmatch(r"[\d.,/*-]+", tokens[i - 1]):
        i -= 1
    label = " ".join(tokens[:i])
    numeric = tokens[i:]
    value_tokens = numeric[2:]  # drop the two base-count columns
    nums = []
    for t in value_tokens[-n_value_cols:]:
        nums.append(0 if t in ("/", "*", "-") else int(round(float(t.replace(",", ".")))))
    return label, nums


def parse_zgb_decimal_row(line: str, n_pct_cols: int) -> tuple[str, list[int], float | None]:
    """Like ``parse_zgb_percent_row`` but expects a trailing decimal mean column.

    Returns (label, [int pct ...], mean_float | None).  The mean column uses a
    comma as decimal separator in the PDF; ``*`` becomes None.
    """
    tokens = line.split()
    i = len(tokens)
    while i > 0 and re.fullmatch(r"[\d.,/*-]+", tokens[i - 1]):
        i -= 1
    label = " ".join(tokens[:i])
    numeric = tokens[i:]
    # Last token is the mean; the n_pct_cols before it are the bands;
    # the first two are base counts.
    if len(numeric) < 2 + n_pct_cols + 1:
        # Mittel column may be missing for suppressed rows.
        mean_val = None
        value_tokens = numeric[2:]
    else:
        mean_tok = numeric[-1]
        mean_val = None if mean_tok in ("*", "/", "-") else float(mean_tok.replace(",", "."))
        value_tokens = numeric[2:-1]
    nums = []
    for t in value_tokens[-n_pct_cols:]:
        nums.append(0 if t in ("/", "*", "-") else int(round(float(t.replace(",", ".")))))
    return label, nums, mean_val


# ---------------------------------------------------------------------------
# Page-text helpers
# ---------------------------------------------------------------------------

def _lines(pdf, page_index: int) -> list[str]:
    """Return non-empty stripped lines from the PDF page (0-indexed)."""
    text = pdf.pages[page_index].extract_text() or ""
    return [ln for ln in text.split("\n") if ln.strip()]


def _section_lines(all_lines: list[str], start_keyword: str) -> list[str]:
    """Return the lines of the section that begins after ``start_keyword``."""
    start = None
    for i, ln in enumerate(all_lines):
        if ln.strip() == start_keyword or ln.strip().startswith(start_keyword + " "):
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for ln in all_lines[start:]:
        stripped = ln.strip()
        if any(stripped == kw or stripped.startswith(kw + " ") or stripped.startswith(kw + "-")
               for kw in SECTION_STOP_KEYWORDS):
            break
        out.append(ln)
    return out


def _is_region_label(label: str) -> bool:
    return label in TEILGEBIETE_CANONICAL or label == "Gesamt"


def _canonical(label: str) -> str:
    return TEILGEBIETE_CANONICAL.get(label, label)


# ---------------------------------------------------------------------------
# P38.4 commute mode (pages 208 + 210, 0-indexed)
# ---------------------------------------------------------------------------

# Part 1/2: 8 mode columns.
P38_4_PART1_MODES = [
    "zu_fuss",
    "elektrofahrrad_pedelec",
    "fahrrad",
    "moped_mofa",
    "privater_pkw",
    "gewerblicher_pkw",
    "lkw_bis_3_5t",
    "lkw_ueber_3_5t",
]

# Part 2/2: 7 mode columns.
P38_4_PART2_MODES = [
    "sattelzug_zugmaschine",
    "kleinbus_max_9_sitze",
    "bus_ueber_9_sitze",
    "bahn_strassenbahn_sbahn",
    "flugzeug",
    "anderes_verkehrsmittel",
    "keine_angabe",
]


def extract_p38_4(pdf) -> pd.DataFrame:
    """Extract P38.4 commute mode table (both page halves)."""
    rows_part1: dict[str, list[int]] = {}
    rows_part2: dict[str, list[int]] = {}

    # Part 1/2: page index 208.
    lines1 = _lines(pdf, 208)
    for ln in lines1:
        label, nums = parse_zgb_percent_row(ln, n_value_cols=8)
        if _is_region_label(label) and len(nums) == 8:
            rows_part1[_canonical(label)] = nums

    # Part 2/2: page index 210.
    lines2 = _lines(pdf, 210)
    for ln in lines2:
        label, nums = parse_zgb_percent_row(ln, n_value_cols=7)
        if _is_region_label(label) and len(nums) == 7:
            rows_part2[_canonical(label)] = nums

    all_labels = ["Gesamt"] + [_canonical(t) for t in [
        "Braunschweig", "Wolfsburg", "Salzgitter",
        "Landkreis Gifhorn", "Landkreis Peine", "Landkreis Helmstedt",
        "Landkreis Wolfenbüttel", "Landkreis Goslar",
    ]]

    records = []
    for lbl in all_labels:
        if lbl not in rows_part1:
            continue
        p1 = rows_part1[lbl]
        p2 = rows_part2.get(lbl, [0] * 7)
        rec = {"region": lbl}
        for col, val in zip(P38_4_PART1_MODES, p1):
            rec[col] = val
        for col, val in zip(P38_4_PART2_MODES, p2):
            rec[col] = val
        records.append(rec)
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# P38.2 commute distance (page 204, 0-indexed)
# ---------------------------------------------------------------------------

P38_2_BANDS = [
    "d_unter_5km",
    "d_5_10km",
    "d_10_20km",
    "d_20_30km",
    "d_30_50km",
    "d_50_100km",
    "d_100_200km",
    "d_200_300km",
    "d_300km_plus",
    "d_unplausibel_keine_angabe",
]


def extract_p38_2(pdf) -> pd.DataFrame:
    """Extract P38.2 commute total-distance table (page index 204)."""
    lines = _lines(pdf, 204)
    records = []
    all_labels = ["Gesamt", "Braunschweig", "Wolfsburg", "Salzgitter",
                  "Landkreis Gifhorn", "Landkreis Peine", "Landkreis Helmstedt",
                  "Landkreis Wolfenbüttel", "Landkreis Goslar"]
    for ln in lines:
        label, nums, mean_val = parse_zgb_decimal_row(ln, n_pct_cols=10)
        canonical = _canonical(label)
        if canonical not in all_labels or len(nums) != 10:
            continue
        rec = {"region": canonical}
        for col, val in zip(P38_2_BANDS, nums):
            rec[col] = val
        rec["mittel_km"] = mean_val
        records.append(rec)
    # Deduplicate (the first occurrence per label wins).
    seen: set[str] = set()
    deduped = []
    for r in records:
        if r["region"] not in seen:
            seen.add(r["region"])
            deduped.append(r)
    # Sort by all_labels order.
    order = {lbl: i for i, lbl in enumerate(all_labels)}
    deduped.sort(key=lambda r: order.get(r["region"], 999))
    return pd.DataFrame(deduped)


# ---------------------------------------------------------------------------
# W12 trip length by purpose (page 274, 0-indexed)
# ---------------------------------------------------------------------------

W12_BANDS = [
    "d_unter_0_5km",
    "d_0_5_1km",
    "d_1_2km",
    "d_2_5km",
    "d_5_10km",
    "d_10_20km",
    "d_20_50km",
    "d_50_100km",
    "d_100km_plus",
]


def extract_w12(pdf) -> pd.DataFrame:
    """Extract W12 trip-length distribution by Hauptwegezweck (page index 274)."""
    lines = _lines(pdf, 274)
    section = _section_lines(lines, "Hauptwegezweck")
    records = []
    for ln in section:
        label, nums, mean_val = parse_zgb_decimal_row(ln, n_pct_cols=9)
        if label in HAUPTWEGEZWECK and len(nums) == 9:
            rec = {"hauptwegezweck": label}
            for col, val in zip(W12_BANDS, nums):
                rec[col] = val
            rec["mittel_km"] = mean_val
            records.append(rec)
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# CSV writing with provenance comment
# ---------------------------------------------------------------------------

def _write_csv(df: pd.DataFrame, path: Path, comment: str) -> None:
    """Write ``df`` to ``path`` with a leading ``#``-comment provenance line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(f"# {comment}\n")
        df.to_csv(fh, index=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not PDF.exists():
        sys.stderr.write(f"[extract-zgb] PDF not found: {PDF}\n")
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with pdfplumber.open(str(PDF)) as pdf:
        # --- P38.4 commute mode ---
        df_mode = extract_p38_4(pdf)
        out_mode = OUT_DIR / "mid2023zgb_P38_4_commute_mode.csv"
        _write_csv(
            df_mode,
            out_mode,
            "Source: MiD 2023 Grossraum Braunschweig (infas 7555), "
            "Tabelle A P38.4 (pages 209+211 / 0-indexed 208+210). "
            "Row% of predominantly used transport mode on regular commute trips.",
        )
        print(f"[extract-zgb] P38.4 -> {out_mode.name}  ({len(df_mode)} rows)")

        # --- P38.2 commute distance ---
        df_dist = extract_p38_2(pdf)
        out_dist = OUT_DIR / "mid2023zgb_P38_2_commute_distance.csv"
        _write_csv(
            df_dist,
            out_dist,
            "Source: MiD 2023 Grossraum Braunschweig (infas 7555), "
            "Tabelle A P38.2 (page 205 / 0-indexed 204). "
            "Row% of total commute distance bands + arithmetic mean (km).",
        )
        print(f"[extract-zgb] P38.2 -> {out_dist.name}  ({len(df_dist)} rows)")

        # --- W12 trip length by purpose ---
        df_w12 = extract_w12(pdf)
        out_w12 = OUT_DIR / "mid2023zgb_W12_triplength_by_purpose.csv"
        _write_csv(
            df_w12,
            out_w12,
            "Source: MiD 2023 Grossraum Braunschweig (infas 7555), "
            "Tabelle A W12 (page 275 / 0-indexed 274), Hauptwegezweck section. "
            "Row% of trip length bands + arithmetic mean (km) per trip purpose.",
        )
        print(f"[extract-zgb] W12   -> {out_w12.name}  ({len(df_w12)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
