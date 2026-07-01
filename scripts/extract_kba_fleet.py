"""Extract KBA / MiD fleet reference tables into committed tidy CSVs.

Source xlsx files (raw, local-only under ``eqasim-data/data/braunschweig/kba/``,
documented in that directory's ``README.md``):

- ``fz27_202501.xlsx`` (KBA FZ 27 series, stock date 1 January 2025)
    * FZ 27.10 -> segment x powertrain (national)        -> kba_segment_powertrain.csv
    * FZ 27.15 -> Kreis x powertrain (ZGB Kreise only)   -> kba_kreis_powertrain.csv
    * FZ 27.17 -> Gemeinde x private BEV/PHEV (ZGB only)  -> kba_gemeinde_private_bev.csv
    * FZ 27.4  -> Niedersachsen fuel x Euro class         -> kba_fuel_euro_nds.csv
    * FZ 27.7  -> vehicle age band x fuel (Pkw column)    -> kba_age_fuel.csv
    * FZ 27.11 -> brand x powertrain (national)           -> kba_brand_powertrain.csv
- ``fz12_2025.xlsx`` (KBA FZ 12.1)
    * FZ 12.1  -> segment x model (Modellreihe)           -> kba_segment_model.csv
- ``output_mit_2023_bundesland_fahrzeuge.xlsx`` (MiD 2023)
    * segment x economic status, by Bundesland           -> mid2023_segment_by_status_bundesland.csv
- ``output_mit_2023_raumtyp_fahrzeuge.xlsx`` (MiD 2023)
    * segment x economic status, by RegioStaR Raumtyp     -> mid2023_segment_by_status_raumtyp.csv

The xlsx headers are multi-line and the data starts around row 12, so every
sheet is parsed by *explicit column indices* (documented in the README), never
by header autodetection. KBA / MiD placeholder symbols (``-`` none, ``.``
secret/unknown, ``/`` not reliable, ``()`` uncertain, blank) are coerced to 0
or NaN *explicitly*, and the coercion count per file is logged (project
no-silent-fallback rule).

The derived CSVs are written under
``eqasim-data/data/braunschweig/kba/derived/`` and force-committed (the
``eqasim-data`` tree is otherwise gitignored), matching the committed-MiD-CSV
pattern. This script is idempotent: re-running it reproduces the same CSVs from
the (unchanged) raw xlsx.

Usage::

    python scripts/extract_kba_fleet.py

Provenance: see ``eqasim-data/data/braunschweig/kba/README.md``. Do not
hand-edit the derived CSVs; re-run this script instead.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import openpyxl
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("extract_kba_fleet")

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
KBA_DIR = Path("eqasim-data/data/braunschweig/kba")
DERIVED_DIR = KBA_DIR / "derived"
RAW_DIR = KBA_DIR / "raw"

FZ27_PATH = KBA_DIR / "fz27_202501.xlsx"
FZ12_PATH = KBA_DIR / "fz12_2025.xlsx"
MID_BUNDESLAND_PATH = KBA_DIR / "output_mit_2023_bundesland_fahrzeuge.xlsx"
MID_RAUMTYP_PATH = KBA_DIR / "output_mit_2023_raumtyp_fahrzeuge.xlsx"

FUEL_46251_PATH = RAW_DIR / "regionalstatistik_46251_02_fuel_kreis_20250101.csv"
EURO_46251_PATH = RAW_DIR / "regionalstatistik_46251_03_euro_kreis_20250101.csv"
AGE_NATIONAL_PATH = RAW_DIR / "statista_kba_3438_pkw_age_national_2026.xlsx"
GEMEINDE_EV_PATH = RAW_DIR / "kba_ev_gemeinde_timeseries_2023_2026.csv"
MODELLREIHEN_PATH = RAW_DIR / "kba_modellreihen_bestand_2020_2026.csv"

# --------------------------------------------------------------------------- #
# Canonical label sets
# --------------------------------------------------------------------------- #
# Canonical powertrain labels used everywhere downstream.
POWERTRAIN_LABELS = ("petrol", "diesel", "gas", "bev", "phev", "hybrid", "hydrogen", "other")
# Canonical economic-status labels (MiD 5-class oekonomischer Status).
STATUS_LABELS = ("very_low", "low", "medium", "high", "very_high")
# Canonical snake_case segment labels (stable across KBA + MiD).
SEGMENT_LABELS = (
    "minis", "kleinwagen", "kompaktklasse", "mittelklasse", "obere_mittelklasse",
    "oberklasse", "suv", "gelaendewagen", "sportwagen", "mini_vans",
    "grossraum_vans", "utilities", "wohnmobile", "sonstige",
)

# KBA German segment name -> canonical snake_case segment.
KBA_SEGMENT_MAP = {
    "minis": "minis",
    "kleinwagen": "kleinwagen",
    "kompaktklasse": "kompaktklasse",
    "mittelklasse": "mittelklasse",
    "obere mittelklasse": "obere_mittelklasse",
    "oberklasse": "oberklasse",
    "suvs": "suv",
    "gelaendewagen": "gelaendewagen",
    "gelandewagen": "gelaendewagen",
    "sportwagen": "sportwagen",
    "mini-vans": "mini_vans",
    "grossraum-vans": "grossraum_vans",
    "grosraum-vans": "grossraum_vans",
    "utilities": "utilities",
    "wohnmobile": "wohnmobile",
    "sonstige": "sonstige",
}

# MiD German segment name -> canonical snake_case segment.
# MiD uses "Sportgelaendewagen" for what KBA calls SUVs, and
# "nicht zuzuordnen" for the residual ("Sonstige").  MiD has no Wohnmobile block.
MID_SEGMENT_MAP = dict(KBA_SEGMENT_MAP)
MID_SEGMENT_MAP.update({
    "sportgelaendewagen": "suv",
    "nicht zuzuordnen": "sonstige",
})

# MiD region header labels arrive line-wrapped (e.g. "Niedersach sen",
# "Baden- Wuerttember g", "kleinstaedtische r, doerflicher Raum").  Map the
# wrapped (whitespace-collapsed, umlaut-normalised) form to a clean label.
MID_BUNDESLAND_NAME_MAP = {
    "Schleswig- Holstein": "Schleswig-Holstein",
    "Hamburg": "Hamburg",
    "Niedersach sen": "Niedersachsen",
    "Bremen": "Bremen",
    "Nordrhein- Westfalen": "Nordrhein-Westfalen",
    "Hessen": "Hessen",
    "Rheinland- Pfalz": "Rheinland-Pfalz",
    "Baden- Wuerttember g": "Baden-Wuerttemberg",
    "Bayern": "Bayern",
    "Saarland": "Saarland",
    "Berlin": "Berlin",
    "Brandenbu rg": "Brandenburg",
    "Mecklenbur g- Vorpommer n": "Mecklenburg-Vorpommern",
    "Sachsen": "Sachsen",
    "Sachsen- Anhalt": "Sachsen-Anhalt",
    "Thueringen": "Thueringen",
}
MID_RAUMTYP_NAME_MAP = {
    "Stadtregion - Metropole": "Stadtregion - Metropole",
    "Stadtregion - Regiopole und Grossstadt": "Stadtregion - Regiopole und Grossstadt",
    "Stadtregion - Mittelstadt, staedtischer Raum": "Stadtregion - Mittelstadt, staedtischer Raum",
    "Stadtregion - kleinstaedtische r, doerflicher Raum": "Stadtregion - kleinstaedtischer, doerflicher Raum",
    "laendliche Region - zentrale Stadt": "laendliche Region - zentrale Stadt",
    "laendliche Region - Mittelstadt, staedtischer Raum": "laendliche Region - Mittelstadt, staedtischer Raum",
    "laendliche Region - kleinstaedtische r, doerflicher Raum": "laendliche Region - kleinstaedtischer, doerflicher Raum",
}

# MiD German economic-status label -> canonical status.
MID_STATUS_MAP = {
    "sehr niedrig": "very_low",
    "niedrig": "low",
    "mittel": "medium",
    "hoch": "high",
    "sehr hoch": "very_high",
}

# KBA fuel ("Kraftstoffart") label -> canonical powertrain (FZ 27.4 / FZ 27.7).
KBA_FUEL_MAP = {
    "benzin": "petrol",
    "diesel": "diesel",
    "gas insgesamt": "gas",
    "elektro (bev)": "bev",
    "hybrid insgesamt": "hybrid",
    "darunter plug-in": "phev",
    "sonstige": "other",
}

# ZGB Kreise (KBA Kennziffer == Kreis AGS-5 == "03" + Kreis3).
ZGB_KREISE = {
    "03101": "Braunschweig, Stadt",
    "03102": "Salzgitter",
    "03103": "Wolfsburg",
    "03151": "Gifhorn",
    "03153": "Goslar",
    "03154": "Helmstedt",
    "03157": "Peine",
    "03158": "Wolfenbuettel",
}

# German Statista age-band labels -> canonical snake_case band labels (ID 3438).
# These 6 bands cover the full Pkw fleet; they are a VALIDATION control, never
# an IPF dimension.
_AGE_NATIONAL_BANDS = {
    "unter 2 jahre": "under_2",
    "2 bis 4 jahre": "2_to_4",
    "5 bis 9 jahre": "5_to_9",
    "10 bis 14 jahre": "10_to_14",
    "15 bis 29 jahre": "15_to_29",
    "30 und mehr jahre": "30_plus",
}

# KBA placeholder symbols that map to a numeric 0 (count below 0.5 / none).
_ZERO_SYMBOLS = {"-", "0"}
# KBA placeholder symbols that map to NaN (secret / not reliable / uncertain).
_NAN_SYMBOLS = {".", "/", "()", "(", ")", "x", "X"}


@dataclass
class CoercionCounter:
    """Tracks how many placeholder cells were coerced to 0 / NaN per file."""

    file_label: str
    to_zero: int = 0
    to_nan: int = 0
    cells_seen: int = 0
    distinct_symbols: dict = field(default_factory=dict)

    def record(self, raw_value, coerced_value) -> None:
        self.cells_seen += 1
        if isinstance(raw_value, str):
            symbol = raw_value.strip()
        else:
            symbol = raw_value
        if pd.isna(coerced_value):
            self.to_nan += 1
            self.distinct_symbols[str(symbol)] = self.distinct_symbols.get(str(symbol), 0) + 1
        elif coerced_value == 0 and not _looks_numeric_zero(raw_value):
            self.to_zero += 1
            self.distinct_symbols[str(symbol)] = self.distinct_symbols.get(str(symbol), 0) + 1

    def log(self) -> None:
        total = self.to_zero + self.to_nan
        logger.info(
            "[%s] numeric cells=%d, coerced placeholders=%d (-> 0: %d, -> NaN: %d); symbols=%s",
            self.file_label, self.cells_seen, total, self.to_zero, self.to_nan,
            dict(sorted(self.distinct_symbols.items())),
        )


def _looks_numeric_zero(raw_value) -> bool:
    """True if the raw value is a genuine numeric 0, not a placeholder string."""
    return isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool) and raw_value == 0


def _coerce_count(raw_value, counter: CoercionCounter):
    """Coerce a KBA/MiD count cell to float, mapping placeholder symbols explicitly.

    ``- 0`` (string) -> 0; ``. / ()`` / blank -> NaN; numbers pass through.
    Every coercion of a placeholder symbol is recorded in ``counter``.
    """
    if raw_value is None:
        result = np.nan
        counter.record(raw_value, result)
        return result
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        # Genuine numeric value (including a real 0); not a placeholder.
        counter.cells_seen += 1
        return float(raw_value)
    text = str(raw_value).strip()
    if text == "":
        result = np.nan
        counter.record(raw_value, result)
        return result
    if text in _ZERO_SYMBOLS:
        result = 0.0
        counter.record(raw_value, result)
        return result
    if text in _NAN_SYMBOLS:
        result = np.nan
        counter.record(raw_value, result)
        return result
    # German thousands separators (".") inside numbers, decimal comma.
    cleaned = text.replace(".", "").replace(",", ".")
    try:
        value = float(cleaned)
        counter.cells_seen += 1
        return value
    except ValueError:
        # Unknown non-numeric token -> NaN, recorded as a coercion.
        result = np.nan
        counter.record(raw_value, result)
        return result


def _coerce_percent(raw_value, counter: CoercionCounter):
    """Coerce a MiD percent cell like ``'7 %'`` to a float share in percent.

    ``'7 %'`` -> 7.0; placeholders -> NaN (recorded).
    """
    if raw_value is None:
        result = np.nan
        counter.record(raw_value, result)
        return result
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        counter.cells_seen += 1
        return float(raw_value)
    text = str(raw_value).strip()
    if text == "" or text in _NAN_SYMBOLS:
        result = np.nan
        counter.record(raw_value, result)
        return result
    cleaned = text.replace("%", "").replace(",", ".").strip()
    try:
        value = float(cleaned)
        counter.cells_seen += 1
        return value
    except ValueError:
        result = np.nan
        counter.record(raw_value, result)
        return result


def _normalise_label(text: str) -> str:
    """Collapse line-wrap whitespace and German umlauts for label matching."""
    if text is None:
        return ""
    collapsed = " ".join(str(text).split())
    return (
        collapsed.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
        .replace("ß", "ss")
    )


def _cell(row, index):
    """Return ``row[index]`` or None when the row is shorter (ragged xlsx rows)."""
    return row[index] if index < len(row) else None


def _read_sheet(path: Path, sheet_name: str):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook[sheet_name]
    rows = list(worksheet.iter_rows(values_only=True))
    workbook.close()
    return rows


# --------------------------------------------------------------------------- #
# FZ 27.10 -> kba_segment_powertrain.csv
# --------------------------------------------------------------------------- #
def extract_segment_powertrain() -> pd.DataFrame:
    """FZ 27.10: per-segment total + BEV/PHEV/hybrid/gas/hydrogen counts.

    Column indices (0-based, verified against the data rows): col1=segment,
    col2=total, col3=alt total, col5=electro total, col7=BEV (Elektro),
    col8=fuel cell (hydrogen, in the electro group), col9=PHEV, col10=hybrid
    total, col13=gas insgesamt, col14=hydrogen (Wasserstoff column).  Only the
    14 KBA segment rows of the 1 January 2025 block are read (the first block;
    later blocks repeat prior years).
    """
    rows = _read_sheet(FZ27_PATH, "FZ 27.10")
    counter = CoercionCounter("FZ 27.10 segment_powertrain")
    records = []
    started = False
    for row in rows:
        label = _normalise_label(row[1]).lower() if row[1] is not None else ""
        if not started:
            # The 1 January 2025 total row precedes the segment rows.
            if label.startswith("1. januar 2025"):
                started = True
            continue
        if label.startswith("1. januar"):
            break  # next year's block
        canonical = KBA_SEGMENT_MAP.get(label)
        if canonical is None:
            continue
        total = _coerce_count(_cell(row, 2), counter)
        bev = _coerce_count(_cell(row, 7), counter)
        fuel_cell = _coerce_count(_cell(row, 8), counter)
        phev = _coerce_count(_cell(row, 9), counter)
        hybrid = _coerce_count(_cell(row, 10), counter)
        gas = _coerce_count(_cell(row, 13), counter)
        hydrogen_col = _coerce_count(_cell(row, 14), counter)
        hydrogen = np.nansum([fuel_cell, hydrogen_col])
        records.append({
            "segment": canonical,
            "total": total,
            "bev": bev,
            "phev": phev,
            "hybrid": hybrid,
            "gas": gas,
            "hydrogen": hydrogen,
        })
    counter.log()
    frame = pd.DataFrame(records)
    # The "alternative drive" columns are a strict subset of the total; the
    # remaining vehicles are conventional petrol/diesel (not separable in this
    # sheet).  We expose the alternative counts plus their per-segment shares.
    alt_total = frame[["bev", "phev", "hybrid", "gas", "hydrogen"]].sum(axis=1)
    frame["alt_total"] = alt_total
    frame["segment_share"] = frame["total"] / frame["total"].sum()
    for powertrain in ("bev", "phev", "hybrid", "gas", "hydrogen"):
        frame[f"{powertrain}_share"] = frame[powertrain] / frame["total"]
    return frame


# --------------------------------------------------------------------------- #
# FZ 27.15 -> kba_kreis_powertrain.csv (ZGB Kreise only)
# --------------------------------------------------------------------------- #
def extract_kreis_powertrain() -> pd.DataFrame:
    """FZ 27.15: per-Kreis total + alternative-drive + BEV/PHEV/hybrid/gas.

    Column indices (0-based, verified against the data rows): col2=Kennziffer
    (= Kreis AGS-5), col3=name, col4=total, col5=alt total, col7=electro total,
    col9=BEV, col10=PHEV, col11=hybrid total, col14=gas insgesamt (the data
    rows have no hydrogen column, so gas is the last cell).  Filtered to the 8
    ZGB Kreise.
    """
    rows = _read_sheet(FZ27_PATH, "FZ 27.15")
    counter = CoercionCounter("FZ 27.15 kreis_powertrain")
    records = []
    for row in rows:
        kennziffer = _cell(row, 2)
        if kennziffer is None:
            continue
        code = str(kennziffer).strip()
        if code not in ZGB_KREISE:
            continue
        records.append({
            "kreis_ags5": code,
            "kreis_name": ZGB_KREISE[code],
            "total": _coerce_count(_cell(row, 4), counter),
            "alt_total": _coerce_count(_cell(row, 5), counter),
            "bev": _coerce_count(_cell(row, 9), counter),
            "phev": _coerce_count(_cell(row, 10), counter),
            "hybrid": _coerce_count(_cell(row, 11), counter),
            "gas": _coerce_count(_cell(row, 14), counter),
        })
    counter.log()
    frame = pd.DataFrame(records).sort_values("kreis_ags5").reset_index(drop=True)
    frame["bev_share"] = frame["bev"] / frame["total"]
    frame["phev_share"] = frame["phev"] / frame["total"]
    frame["alt_share"] = frame["alt_total"] / frame["total"]
    return frame


# --------------------------------------------------------------------------- #
# FZ 27.17 -> kba_gemeinde_private_bev.csv (ZGB Kreise only)
# --------------------------------------------------------------------------- #
def extract_gemeinde_private_bev() -> pd.DataFrame:
    """FZ 27.17: per-Gemeinde private car total + private BEV/PHEV.

    Column indices (0-based, README): col2=Zulassungsbezirk ("AGS5 NAME",
    filled once per Bezirk), col3=Gemeinde, col7=private total, col8=private
    BEV, col9=private PHEV.  Filtered to Gemeinden whose Bezirk prefix is a ZGB
    Kreis AGS-5.
    """
    rows = _read_sheet(FZ27_PATH, "FZ 27.17")
    counter = CoercionCounter("FZ 27.17 gemeinde_private_bev")
    records = []
    current_kreis: Optional[str] = None
    for row in rows:
        bezirk = _cell(row, 2)
        if bezirk is not None and str(bezirk).strip():
            # e.g. "08111 STUTTGART,STADT" -> the leading 5-digit AGS is the Kreis.
            token = str(bezirk).strip().split()[0]
            if token.isdigit() and len(token) == 5:
                current_kreis = token
        gemeinde = _cell(row, 3)
        if gemeinde is None or not str(gemeinde).strip():
            continue
        if current_kreis not in ZGB_KREISE:
            continue
        records.append({
            "kreis_ags5": current_kreis,
            "kreis_name": ZGB_KREISE[current_kreis],
            "gemeinde": str(gemeinde).strip(),
            "private_total": _coerce_count(_cell(row, 7), counter),
            "private_bev": _coerce_count(_cell(row, 8), counter),
            "private_phev": _coerce_count(_cell(row, 9), counter),
        })
    counter.log()
    frame = pd.DataFrame(records)
    frame = frame.sort_values(["kreis_ags5", "gemeinde"]).reset_index(drop=True)
    frame["private_bev_share"] = frame["private_bev"] / frame["private_total"]
    frame["private_phev_share"] = frame["private_phev"] / frame["private_total"]
    return frame


# --------------------------------------------------------------------------- #
# FZ 27.4 -> kba_fuel_euro_nds.csv (Niedersachsen)
# --------------------------------------------------------------------------- #
def extract_fuel_euro_nds() -> pd.DataFrame:
    """FZ 27.4: Niedersachsen fuel x Euro class (long form).

    Column indices (0-based, README): col1=Land (once per block), col2=fuel,
    col3=Euro1, col4=Euro2, col5=Euro3, col6=Euro4, col7=Euro5,
    col8=Euro6 total, col12=Sonstige, col13=row total.  We keep the headline
    Euro classes (1..6, plus "other" = Sonstige) and skip the Euro-6
    sub-breakdown columns (6d-temp / 6d / 6e) so the per-fuel Euro shares sum
    to one without double counting.
    """
    rows = _read_sheet(FZ27_PATH, "FZ 27.4")
    counter = CoercionCounter("FZ 27.4 fuel_euro_nds")
    euro_columns = {
        "euro1": 3, "euro2": 4, "euro3": 5, "euro4": 6, "euro5": 7,
        "euro6": 8, "other": 12,
    }
    records = []
    in_block = False
    for row in rows:
        land = _normalise_label(row[1]) if row[1] is not None else ""
        if land.lower().startswith("niedersachsen zusammen"):
            break
        if land.lower() == "niedersachsen":
            in_block = True
        if not in_block:
            continue
        fuel_label = _normalise_label(row[2]).lower() if row[2] is not None else ""
        powertrain = KBA_FUEL_MAP.get(fuel_label)
        if powertrain is None:
            continue
        for euro_class, col in euro_columns.items():
            records.append({
                "fuel": powertrain,
                "euro_class": euro_class,
                "count": _coerce_count(_cell(row, col), counter),
            })
    counter.log()
    frame = pd.DataFrame(records)
    # Per-fuel Euro-class share (within powertrain).
    fuel_totals = frame.groupby("fuel")["count"].transform("sum")
    frame["share"] = frame["count"] / fuel_totals
    return frame


# --------------------------------------------------------------------------- #
# FZ 27.7 -> kba_age_fuel.csv (Pkw column)
# --------------------------------------------------------------------------- #
def extract_age_fuel() -> pd.DataFrame:
    """FZ 27.7: vehicle age band x fuel, Pkw (passenger-car) column only.

    Column indices (0-based, README): col1=Fahrzeugalter (filled once per
    block), col2=Kraftstoffart, col4=Personenkraftwagen (the Pkw column).
    "... zusammen" subtotal rows are skipped.
    """
    rows = _read_sheet(FZ27_PATH, "FZ 27.7")
    counter = CoercionCounter("FZ 27.7 age_fuel")
    records = []
    current_age: Optional[str] = None
    for row in rows:
        age_label = _normalise_label(row[1]) if row[1] is not None else ""
        if age_label and "zusammen" in age_label.lower():
            current_age = None
            continue
        if age_label and "Jahre" in age_label:
            current_age = _canonical_age_band(age_label)
        fuel_label = _normalise_label(row[2]).lower() if row[2] is not None else ""
        powertrain = KBA_FUEL_MAP.get(fuel_label)
        if powertrain is None or current_age is None:
            continue
        records.append({
            "age_band": current_age,
            "fuel": powertrain,
            "pkw_count": _coerce_count(_cell(row, 4), counter),
        })
    counter.log()
    frame = pd.DataFrame(records)
    fuel_totals = frame.groupby("fuel")["pkw_count"].transform("sum")
    frame["share"] = frame["pkw_count"] / fuel_totals
    return frame


def _canonical_age_band(german_label: str) -> str:
    """Map a German FZ 27.7 age band to a stable snake_case label."""
    text = german_label.lower()
    mapping = {
        "unter 5 jahre": "under_5",
        "5 bis 9 jahre": "5_to_9",
        "10 bis 14 jahre": "10_to_14",
        "15 bis 19 jahre": "15_to_19",
        "20 bis 24 jahre": "20_to_24",
        "25 bis 29 jahre": "25_to_29",
        "30 jahre und mehr": "30_plus",
    }
    return mapping.get(text, text.replace(" ", "_"))


# --------------------------------------------------------------------------- #
# FZ 27.11 -> kba_brand_powertrain.csv
# --------------------------------------------------------------------------- #
def extract_brand_powertrain() -> pd.DataFrame:
    """FZ 27.11: per-brand total + BEV/PHEV/hybrid/gas counts.

    Same column layout as FZ 27.10: col1=brand, col2=total, col7=BEV,
    col9=PHEV, col10=hybrid total, col13=gas insgesamt.  The trailing
    ``INSGESAMT`` grand-total row is skipped; ``SONSTIGE`` (residual brands) is
    kept.
    """
    rows = _read_sheet(FZ27_PATH, "FZ 27.11")
    counter = CoercionCounter("FZ 27.11 brand_powertrain")
    records = []
    started = False
    for row in rows:
        label = _normalise_label(row[1]) if row[1] is not None else ""
        if not label:
            continue
        if not started:
            # Brand rows begin after the column-header block; the first brand
            # row has a numeric total in col2.
            if isinstance(_cell(row, 2), (int, float)) and label.upper() != "INSGESAMT":
                started = True
            else:
                continue
        if label.upper() == "INSGESAMT":
            break
        if "Kraftfahrt-Bundesamt" in label:
            break
        records.append({
            "brand": label,
            "total": _coerce_count(_cell(row, 2), counter),
            "bev": _coerce_count(_cell(row, 7), counter),
            "phev": _coerce_count(_cell(row, 9), counter),
            "hybrid": _coerce_count(_cell(row, 10), counter),
            "gas": _coerce_count(_cell(row, 13), counter),
        })
    counter.log()
    frame = pd.DataFrame(records)
    frame["brand_share"] = frame["total"] / frame["total"].sum()
    return frame


# --------------------------------------------------------------------------- #
# FZ 12.1 -> kba_segment_model.csv
# --------------------------------------------------------------------------- #
def extract_segment_model() -> pd.DataFrame:
    """FZ 12.1: per-segment model (Modellreihe) counts and within-segment share.

    .. deprecated::
        Superseded by :func:`extract_segment_model_2026` which reads the newer
        Modellreihen bestand CSV (01.01.2026); kept for reference/reversibility.

    Column indices (0-based, README): col1=Segment (filled once per block,
    uppercase), col2=Modellreihe, col3=Anzahl (count).  Segment subtotal rows
    ("... ZUSAMMEN") and the trailing ``BESTAND INSGESAMT`` / footnotes are
    skipped.  The within-segment share is recomputed from the counts so it is
    exact and reproducible.
    """
    rows = _read_sheet(FZ12_PATH, "FZ 12.1")
    counter = CoercionCounter("FZ 12.1 segment_model")
    records = []
    current_segment: Optional[str] = None
    for row in rows:
        seg_label = _normalise_label(row[1]) if row[1] is not None else ""
        if seg_label:
            upper = seg_label.upper().strip()
            if "ZUSAMMEN" in upper:
                current_segment = None
                continue
            # End of the data block (grand total + footnotes).  Match only the
            # exact "BESTAND INSGESAMT" / "HINWEIS:" markers, never the sheet
            # title row ("Bestand an Personenkraftwagen nach Segmenten ...").
            if upper.startswith("BESTAND INSGESAMT") or upper.startswith("HINWEIS"):
                break
            if "Kraftfahrt-Bundesamt" in seg_label:
                break
            canonical = KBA_SEGMENT_MAP.get(seg_label.lower().strip())
            if canonical is not None:
                current_segment = canonical
        model = _cell(row, 2)
        if model is None or not str(model).strip():
            continue
        if current_segment is None:
            continue
        records.append({
            "segment": current_segment,
            "model": str(model).strip(),
            "count": _coerce_count(_cell(row, 3), counter),
        })
    counter.log()
    frame = pd.DataFrame(records)
    segment_totals = frame.groupby("segment")["count"].transform("sum")
    frame["share"] = frame["count"] / segment_totals
    return frame


# --------------------------------------------------------------------------- #
# kba_modellreihen_bestand_2020_2026.csv helpers
# --------------------------------------------------------------------------- #
def _read_modellreihen(path) -> pd.DataFrame:
    """Read the KBA Modellreihen CSV (utf-8-sig), filter to 01.01.2026.

    Returns the raw filtered DataFrame.  The CSV must carry columns:
    ``Berichtszeitpunkt, Segment, Marke, Modellreihe, Anzahl, Diesel,
    Hybrid, Hybrid_Plugin, BEV, gewerblich``.

    Args:
        path: Path-like or str pointing at the raw CSV.

    Returns:
        DataFrame with only the 01.01.2026 rows.
    """
    raw = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    # Normalise column names (strip leading/trailing whitespace).
    raw.columns = [c.strip() for c in raw.columns]
    filtered = raw[raw["Berichtszeitpunkt"].str.strip() == "01.01.2026"].copy()
    return filtered


# --------------------------------------------------------------------------- #
# Modellreihen -> kba_segment_model.csv (2026 refresh)
# --------------------------------------------------------------------------- #
def extract_segment_model_2026(path=None) -> pd.DataFrame:
    """Modellreihen (2026): per-segment model counts + within-segment share.

    Reads the KBA Modellreihen bestand CSV filtered to Berichtszeitpunkt
    ``01.01.2026`` and produces the same schema as the legacy
    ``extract_segment_model`` (``segment, model, count, share``) plus a
    ``stichtag`` column.

    The join key convention matches ``kba_segment_model.csv`` from FZ 12.1:
    ``model = f"{Marke} {Modellreihe}"`` (uppercase as delivered by KBA).

    Rows whose ``Segment`` does not map via ``KBA_SEGMENT_MAP`` are skipped;
    the skip count is logged (no-silent-fallback rule).

    Args:
        path: Path to the raw Modellreihen CSV.  Defaults to
            ``MODELLREIHEN_PATH`` when ``None``.

    Returns:
        DataFrame with columns ``segment, model, count, share, stichtag``.
    """
    if path is None:
        path = MODELLREIHEN_PATH
    counter = CoercionCounter("Modellreihen 2026 segment_model")
    df = _read_modellreihen(path)
    records = []
    n_unmapped = 0
    for _, row in df.iterrows():
        seg_key = str(row["Segment"]).strip().lower()
        canonical = KBA_SEGMENT_MAP.get(seg_key)
        if canonical is None:
            n_unmapped += 1
            continue
        model = f"{str(row['Marke']).strip()} {str(row['Modellreihe']).strip()}"
        count = _coerce_count(row["Anzahl"], counter)
        records.append({
            "segment": canonical,
            "model": model,
            "count": count,
            "stichtag": "2026-01-01",
        })
    counter.log()
    if n_unmapped > 0:
        logger.warning(
            "[extract_segment_model_2026] %d row(s) with unmapped segment skipped "
            "(no KBA_SEGMENT_MAP entry); valid rows: %d",
            n_unmapped, len(records),
        )
    frame = pd.DataFrame(records)
    segment_totals = frame.groupby("segment")["count"].transform("sum")
    frame["share"] = frame["count"] / segment_totals
    return frame


# --------------------------------------------------------------------------- #
# Modellreihen -> kba_model_fuel.csv (per-model fuel weight)
# --------------------------------------------------------------------------- #
def extract_model_fuel(path=None) -> pd.DataFrame:
    """Modellreihen (2026): per-model fuel-type shares.

    Reads the KBA Modellreihen bestand CSV filtered to Berichtszeitpunkt
    ``01.01.2026`` and computes per-model powertrain shares:

    - ``petrol_share``: residual after all electrified/diesel counts,
      ``max(Anzahl - Diesel - Hybrid - BEV, 0) / Anzahl``.
      Note: the raw ``Hybrid`` column already **includes** ``Hybrid_Plugin``,
      so the full ``Hybrid`` is subtracted here (not the split non-plugin value).
    - ``diesel_share``: ``Diesel / Anzahl``.
    - ``hybrid_share``: ``max(Hybrid - Hybrid_Plugin, 0) / Anzahl``
      (non-plugin hybrid only).
    - ``phev_share``: ``Hybrid_Plugin / Anzahl``.
    - ``bev_share``: ``BEV / Anzahl``.

    Rows with ``Anzahl <= 0`` after coercion are skipped (logged).
    Rows whose ``Segment`` does not map via ``KBA_SEGMENT_MAP`` are skipped
    (logged); the skip count is included in the log message (no-silent-fallback).

    The join key convention matches ``kba_segment_model.csv``:
    ``model = f"{Marke} {Modellreihe}"`` (uppercase as delivered by KBA).

    Args:
        path: Path to the raw Modellreihen CSV.  Defaults to
            ``MODELLREIHEN_PATH`` when ``None``.

    Returns:
        DataFrame with columns ``segment, model, stichtag, petrol_share,
        diesel_share, hybrid_share, phev_share, bev_share``.
    """
    if path is None:
        path = MODELLREIHEN_PATH
    counter = CoercionCounter("Modellreihen 2026 model_fuel")
    df = _read_modellreihen(path)
    records = []
    n_unmapped = 0
    n_zero_anzahl = 0
    for _, row in df.iterrows():
        seg_key = str(row["Segment"]).strip().lower()
        canonical = KBA_SEGMENT_MAP.get(seg_key)
        if canonical is None:
            n_unmapped += 1
            continue
        model = f"{str(row['Marke']).strip()} {str(row['Modellreihe']).strip()}"
        anzahl = _coerce_count(row["Anzahl"], counter)
        if pd.isna(anzahl) or anzahl <= 0:
            n_zero_anzahl += 1
            continue
        diesel = np.nan_to_num(_coerce_count(row["Diesel"], counter))
        hybrid_all = np.nan_to_num(_coerce_count(row["Hybrid"], counter))
        hybrid_plugin = np.nan_to_num(_coerce_count(row["Hybrid_Plugin"], counter))
        bev = np.nan_to_num(_coerce_count(row["BEV"], counter))
        # Non-plugin hybrid = total hybrid minus plugin hybrid.
        hybrid_nonplugin = max(hybrid_all - hybrid_plugin, 0.0)
        # Petrol residual: subtract full Hybrid (which already includes plugin).
        petrol = max(anzahl - diesel - hybrid_all - bev, 0.0)
        records.append({
            "segment": canonical,
            "model": model,
            "stichtag": "2026-01-01",
            "petrol_share": petrol / anzahl,
            "diesel_share": diesel / anzahl,
            "hybrid_share": hybrid_nonplugin / anzahl,
            "phev_share": hybrid_plugin / anzahl,
            "bev_share": bev / anzahl,
        })
    counter.log()
    total_skipped = n_unmapped + n_zero_anzahl
    logger.info(
        "[extract_model_fuel] models written=%d, skipped: unmapped segment=%d, "
        "zero/invalid Anzahl=%d (total skipped=%d)",
        len(records), n_unmapped, n_zero_anzahl, total_skipped,
    )
    return pd.DataFrame(records)


# --------------------------------------------------------------------------- #
# FZ 12.1 -> kba_segment_model.csv (DEPRECATED — superseded by 2026 Modellreihen)
# --------------------------------------------------------------------------- #
# Superseded by extract_segment_model_2026(); kept for reference/reversibility.


# --------------------------------------------------------------------------- #
# MiD segment x economic status, by region (Bundesland / Raumtyp)
# --------------------------------------------------------------------------- #
def extract_mid_segment_by_status(path: Path, segment_map: dict, region_name_map: dict,
                                  file_label: str) -> pd.DataFrame:
    """MiD 2023 segment x economic status, column-% per region.

    Each segment block: a "Basis: PKW" title, a region header row (col1 ==
    "Total", col2.. == region names), a "Basis gewichtet" row (weighted base),
    an "oekonomischer Status" marker, then the 5 status rows (sehr niedrig ..
    sehr hoch), each a column-% per region.  Long form output:
    ``region, segment, status, share_pct, base_weighted``.
    """
    # The MiD files contain a single sheet ("MiD Tabellen"); read it by index.
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet_name = workbook.sheetnames[0]
    workbook.close()
    rows = _read_sheet(path, sheet_name)
    counter = CoercionCounter(file_label)

    # Locate block starts (the segment title lines).
    block_starts = []
    for index, row in enumerate(rows):
        cell0 = _normalise_label(row[0]) if row[0] is not None else ""
        if cell0.lower().startswith("pkw-segmentierung nach kba"):
            # The segment name follows the last "- ".
            after = cell0.split("- ")[-1].strip().lower()
            canonical = segment_map.get(after)
            block_starts.append((index, canonical, after))

    records = []
    for index, canonical, raw_name in block_starts:
        if canonical is None:
            logger.warning("[%s] unmapped MiD segment '%s' at row %d (skipped)",
                           file_label, raw_name, index)
            continue
        # Region header: the row that follows the "... | Total | <group> ..."
        # row.  In the "Total" row, col2 carries the group title (e.g.
        # "Bundesland"); the individual region names are in the *next* row's
        # col2 onward (col1 there is blank).
        header_row = None
        base_row = None
        status_rows = {}
        block_end = min(index + 26, len(rows))
        for offset in range(index, block_end):
            row = rows[offset]
            cell0 = _normalise_label(_cell(row, 0)).lower() if _cell(row, 0) is not None else ""
            cell1 = _normalise_label(_cell(row, 1)).lower() if _cell(row, 1) is not None else ""
            if header_row is None and cell1 == "total" and offset + 1 < block_end:
                header_row = rows[offset + 1]
            if cell0.startswith("basis gewichtet"):
                base_row = row
            if cell0 in MID_STATUS_MAP:
                status_rows[MID_STATUS_MAP[cell0]] = row
        if header_row is None or len(status_rows) != len(STATUS_LABELS):
            logger.warning("[%s] incomplete block for segment '%s' at row %d (skipped)",
                           file_label, canonical, index)
            continue
        # Region columns: every non-empty header cell from col2 onward.
        region_columns = []
        for col in range(2, len(header_row)):
            name = _cell(header_row, col)
            if name is not None and str(name).strip():
                wrapped = _normalise_label(name)
                clean = region_name_map.get(wrapped)
                if clean is None:
                    logger.warning("[%s] unmapped region header '%s' (kept verbatim)",
                                   file_label, wrapped)
                    clean = wrapped
                region_columns.append((col, clean))
        for col, region in region_columns:
            base_weighted = (_coerce_count(_cell(base_row, col), counter)
                             if base_row is not None else np.nan)
            for status in STATUS_LABELS:
                share_pct = _coerce_percent(_cell(status_rows[status], col), counter)
                records.append({
                    "region": region,
                    "segment": canonical,
                    "status": status,
                    "share_pct": share_pct,
                    "base_weighted": base_weighted,
                })
    counter.log()
    frame = pd.DataFrame(records)
    return frame


# --------------------------------------------------------------------------- #
# Regionalstatistik 46251-02 -> kba_kreis_fuel.csv
# --------------------------------------------------------------------------- #
def extract_kreis_fuel_46251(path: Path = FUEL_46251_PATH) -> pd.DataFrame:
    """Regionalstatistik 46251-02: per-Kreis Pkw by fuel (Stichtag 01.01.2025).

    Column order after the 3 id columns (stichtag, ags5, name): Insgesamt,
    Benzin, Diesel, Gas, Hybrid, darunter Plug-In-Hybrid, Elektro, sonstige.
    ``Hybrid`` INCLUDES the ``darunter PHEV`` subset, so non-plugin hybrid is
    ``Hybrid - PHEV``. ``Elektro`` == BEV. Dissolved Kreise carry ``-`` -> dropped.
    Only the 8 ZGB Kreise are kept.
    """
    counter = CoercionCounter("46251-02 kreis_fuel")
    raw = pd.read_csv(path, sep=";", skiprows=8, header=None, encoding="latin-1", dtype=str)
    cols = ["stichtag", "ags5", "name", "insg", "benzin", "diesel", "gas",
            "hybrid", "phev", "elektro", "sonstige"]
    raw = raw.iloc[:, :len(cols)]
    raw.columns = cols
    records = []
    for _, r in raw.iterrows():
        code = str(r["ags5"]).strip()
        if code not in ZGB_KREISE:
            continue
        insg = _coerce_count(r["insg"], counter)
        if pd.isna(insg) or insg <= 0:
            continue  # dissolved / suppressed Kreis
        petrol = _coerce_count(r["benzin"], counter)
        diesel = _coerce_count(r["diesel"], counter)
        gas = _coerce_count(r["gas"], counter)
        hybrid_all = _coerce_count(r["hybrid"], counter)
        phev = _coerce_count(r["phev"], counter)
        bev = _coerce_count(r["elektro"], counter)
        other = _coerce_count(r["sonstige"], counter)
        hybrid = max(np.nan_to_num(hybrid_all) - np.nan_to_num(phev), 0.0)
        records.append({
            "kreis_ags5": code, "kreis_name": ZGB_KREISE[code],
            "stichtag": "2025-01-01",
            "petrol": petrol, "diesel": diesel, "gas": gas, "bev": bev,
            "phev": phev, "hybrid": hybrid, "other": other,
        })
    counter.log()
    frame = pd.DataFrame(records).sort_values("kreis_ags5").reset_index(drop=True)
    pts = ["petrol", "diesel", "gas", "bev", "phev", "hybrid", "other"]
    frame["total"] = frame[pts].sum(axis=1)
    for p in pts:
        frame[f"{p}_share"] = frame[p] / frame["total"]
    return frame


# --------------------------------------------------------------------------- #
# Regionalstatistik 46251-03 -> kba_kreis_euro.csv
# --------------------------------------------------------------------------- #
def extract_kreis_euro_46251(path: Path = EURO_46251_PATH) -> pd.DataFrame:
    """Regionalstatistik 46251-03: per-Kreis Pkw by Euro group (01.01.2025).

    Two rows per Kreis: ``insgesamt`` (all fuels) and ``Dieselangetriebener Pkw``.
    Columns after the 4 id cols (stichtag, ags5, name, teil): Insgesamt, Euro 1..5,
    Euro 6, darunter Euro-6d, darunter Euro-6d-temp, Sonstige. The two ``darunter``
    columns are SUBSETS of Euro 6 and are skipped (no double count).
    """
    counter = CoercionCounter("46251-03 kreis_euro")
    raw = pd.read_csv(path, sep=";", skiprows=8, header=None, encoding="latin-1", dtype=str)
    cols = ["stichtag", "ags5", "name", "teil", "insg",
            "e1", "e2", "e3", "e4", "e5", "e6", "e6d", "e6dtemp", "sonstige"]
    raw = raw.iloc[:, :len(cols)]
    raw.columns = cols
    records = []
    for _, r in raw.iterrows():
        code = str(r["ags5"]).strip()
        if code not in ZGB_KREISE:
            continue
        teil_raw = _normalise_label(r["teil"]).lower()
        if teil_raw.startswith("insgesamt"):
            teil = "all"
        elif teil_raw.startswith("dieselangetriebener"):
            teil = "diesel"
        else:
            continue
        euros = {k: _coerce_count(r[v], counter) for k, v in
                 (("euro1", "e1"), ("euro2", "e2"), ("euro3", "e3"),
                  ("euro4", "e4"), ("euro5", "e5"), ("euro6", "e6"),
                  ("other", "sonstige"))}
        rec = {"kreis_ags5": code, "kreis_name": ZGB_KREISE[code],
               "stichtag": "2025-01-01", "teil": teil, **euros}
        records.append(rec)
    counter.log()
    frame = pd.DataFrame(records).sort_values(["kreis_ags5", "teil"]).reset_index(drop=True)
    euro_cols = ["euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other"]
    frame["total"] = frame[euro_cols].sum(axis=1)
    for c in euro_cols:
        frame[f"{c}_share"] = frame[c] / frame["total"]
    return frame


# --------------------------------------------------------------------------- #
# KBA per-Gemeinde EV shares (2026.04) -> kba_gemeinde_ev.csv (ZGB only)
# --------------------------------------------------------------------------- #
def extract_gemeinde_ev(path: Path = GEMEINDE_EV_PATH) -> pd.DataFrame:
    """KBA per-Gemeinde EV shares (Stichtag 2026-04-01), BEV/PHEV/fuel-cell split.

    The KBA per-Gemeinde timeseries CSV (``kba_ev_gemeinde_timeseries_2023_2026.csv``)
    carries one row per Gemeinde per quarterly reporting period.  For ZGB Gemeinden
    the absolute Pkw counts are suppressed by KBA data-protection rules; only the
    ``*_Anteil`` (share) columns carry data -- the downstream tilt consumes shares,
    so this is loss-free for our use.

    This function:
    - Keeps only the latest reporting period (``Berichtszeitpunkt``, e.g. "2026.04").
    - Filters to Gemeinden whose AGS-8 prefix (first 5 digits) is one of the 8 ZGB
      Kreise (``ZGB_KREISE``).
    - Converts German decimal commas to dots and divides share columns by 100
      (percent -> fraction).
    - Logs the count of rows with any missing share values (no-silent-fallback rule);
      NaN shares are kept in the output so the consuming tilt can fall back to the
      Kreis-level value.
    - Applies ``normalize_gemeinde`` from
      ``braunschweig.synthesis.vehicles.fleet_sampling_de`` to produce the
      ``gemeinde_norm`` matching key.

    Args:
        path: Path to the raw KBA Gemeinde EV CSV (utf-8-sig, ``Berichtszeitpunkt``
              column carries the period string like ``"2026.04"``).

    Returns:
        DataFrame with columns: ``kreis_ags5, ags8, gemeinde, gemeinde_norm,
        stichtag, ev_share, bev_share, phev_share, fuelcell_share``.
        Sorted by (``kreis_ags5``, ``gemeinde``); index reset.
    """
    # Local import: the synthesis package is not guaranteed to be on sys.path
    # at script import time (only needed here, not by the other extractor functions).
    from braunschweig.synthesis.vehicles.fleet_sampling_de import normalize_gemeinde

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)

    # Derive the 5-digit Kreis prefix from the 8-digit Gemeinde AGS.
    df["ags5"] = df["AGS"].str.strip().str[:5]

    # Keep only the latest reporting period.
    latest = sorted(df["Berichtszeitpunkt"].dropna().unique())[-1]  # e.g. "2026.04"
    stichtag = f"{latest[:4]}-{latest[5:7]}-01"

    sub = df[
        (df["Berichtszeitpunkt"] == latest) & (df["ags5"].isin(ZGB_KREISE))
    ].copy()

    def _frac(col: str) -> pd.Series:
        """Parse a percent share column (German comma) and divide by 100."""
        return (
            pd.to_numeric(
                sub[col].str.replace(",", ".", regex=False),
                errors="coerce",
            )
            / 100.0
        )

    out = pd.DataFrame(
        {
            "kreis_ags5": sub["ags5"].values,
            "ags8": sub["AGS"].str.strip().values,
            "gemeinde": sub["Gemeinde"].str.strip().values,
            "stichtag": stichtag,
            "ev_share": _frac("Pkw Elektro Anteil").values,
            "bev_share": _frac("Pkw_BEV_Anteil").values,
            "phev_share": _frac("Pkw Plug In Hybrid Anteil").values,
            "fuelcell_share": _frac("Pkw Brennstoffzelle Anteil").values,
        }
    )

    out["gemeinde_norm"] = out["gemeinde"].map(normalize_gemeinde)

    # Log missing-share rows (no-silent-fallback rule): if any share is NaN for a
    # ZGB row that is genuinely unexpected (not a KBA suppression), it is a signal
    # that the column mapping needs attention.  The consumer (EV tilt) will fall
    # back to the Kreis value for such rows.
    share_cols = ["ev_share", "bev_share", "phev_share", "fuelcell_share"]
    n_missing = int(out[share_cols].isna().any(axis=1).sum())
    n_total = len(out)
    if n_missing > 0:
        logger.warning(
            "[extract_gemeinde_ev] %d/%d ZGB Gemeinde rows have at least one missing "
            "share value (NaN kept; consuming tilt falls back to Kreis level).",
            n_missing, n_total,
        )
    else:
        logger.info(
            "[extract_gemeinde_ev] %d ZGB Gemeinde rows, all share columns populated "
            "(stichtag=%s).",
            n_total, stichtag,
        )

    return out.sort_values(["kreis_ags5", "gemeinde"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Statista KBA ID 3438 -> kba_age_national.csv (VALIDATION control)
# --------------------------------------------------------------------------- #
def extract_age_national(path: Path = AGE_NATIONAL_PATH, year: int = 2026) -> pd.DataFrame:
    """KBA/Statista ID 3438: national Pkw age distribution (VALIDATION control).

    Sheet ``Daten``: the band-label row precedes the per-year rows (col1 is
    blank/misc, col2 = year value, cols 3.. = band percentages in the order
    listed in ``_AGE_NATIONAL_BANDS``).  Returns the requested year's 6 bands
    as a tidy DataFrame with columns ``year, stichtag, band, share_pct``.

    This is a national validation anchor (mean_age_years = 10.9 as stated in the
    Statista source); it is never used as an IPF dimension.

    Args:
        path: Path to the Statista xlsx (``Daten`` sheet).
        year: Reference year to extract (default 2026).

    Returns:
        DataFrame with 6 rows and columns ``year, stichtag, band, share_pct``.

    Raises:
        RuntimeError: If ``year`` is not found in the sheet.
    """
    rows = _read_sheet(path, "Daten")
    header: dict[int, str] | None = None
    for row in rows:
        labels = [(_normalise_label(c).lower() if c else "") for c in row]
        if "unter 2 jahre" in labels:
            header = {
                i: _AGE_NATIONAL_BANDS[labels[i]]
                for i in range(len(labels))
                if labels[i] in _AGE_NATIONAL_BANDS
            }
            continue
        if header is not None and row[1] is not None and str(row[1]).strip().isdigit() \
                and int(row[1]) == year:
            recs = [
                {"year": year, "stichtag": f"{year}-01-01",
                 "band": band, "share_pct": float(row[i])}
                for i, band in header.items()
            ]
            df = pd.DataFrame(recs)
            logger.info(
                "[age_national] year=%d, bands=%d, sum_share_pct=%.2f",
                year, len(df), df["share_pct"].sum(),
            )
            return df
    raise RuntimeError(f"age control: year {year} not found in {path}")


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def _write(frame: pd.DataFrame, name: str) -> None:
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    path = DERIVED_DIR / name
    frame.to_csv(path, index=False)
    logger.info("[write] %s (%d rows, %d cols)", path, len(frame), frame.shape[1])


def _write_with_header(frame: pd.DataFrame, name: str, header_line: str) -> None:
    """Write ``frame`` as CSV with a ``# ...`` comment line prepended.

    Mirrors ``_write`` but prefixes the file with ``header_line`` (which must
    start with ``#``) so provenance metadata (e.g. mean_age_years, source ID,
    stichtag) travels with the CSV.  The loaders already tolerate ``# ...``
    comment lines via ``pd.read_csv(..., comment="#")``.

    Args:
        frame: DataFrame to write.
        name: Output filename under ``DERIVED_DIR``.
        header_line: The comment line to prepend (e.g. ``"# key=val key2=val2"``).
    """
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    path = DERIVED_DIR / name
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header_line + "\n")
        fh.write(frame.to_csv(index=False))
    logger.info("[write] %s (%d rows, %d cols, with header)", path, len(frame), frame.shape[1])


def main() -> None:
    for required in (FZ27_PATH, FZ12_PATH, MID_BUNDESLAND_PATH, MID_RAUMTYP_PATH,
                     FUEL_46251_PATH, EURO_46251_PATH, AGE_NATIONAL_PATH,
                     GEMEINDE_EV_PATH, MODELLREIHEN_PATH):
        if not required.exists():
            raise FileNotFoundError(
                f"Required raw KBA/MiD input missing: {required} "
                f"(raw xlsx are local-only; see {KBA_DIR / 'README.md'})."
            )

    _write(extract_segment_powertrain(), "kba_segment_powertrain.csv")
    _write(extract_kreis_powertrain(), "kba_kreis_powertrain.csv")
    _write(extract_gemeinde_private_bev(), "kba_gemeinde_private_bev.csv")
    _write(extract_fuel_euro_nds(), "kba_fuel_euro_nds.csv")
    _write(extract_age_fuel(), "kba_age_fuel.csv")
    _write(extract_brand_powertrain(), "kba_brand_powertrain.csv")
    # kba_segment_model.csv is now produced from the 2026 Modellreihen source
    # (extract_segment_model_2026) instead of the legacy FZ 12.1 xlsx.
    _write(extract_segment_model_2026(), "kba_segment_model.csv")
    _write(extract_model_fuel(), "kba_model_fuel.csv")
    _write(
        extract_mid_segment_by_status(MID_BUNDESLAND_PATH, MID_SEGMENT_MAP,
                                      MID_BUNDESLAND_NAME_MAP,
                                      "MiD bundesland segment_by_status"),
        "mid2023_segment_by_status_bundesland.csv",
    )
    _write(
        extract_mid_segment_by_status(MID_RAUMTYP_PATH, MID_SEGMENT_MAP,
                                      MID_RAUMTYP_NAME_MAP,
                                      "MiD raumtyp segment_by_status"),
        "mid2023_segment_by_status_raumtyp.csv",
    )
    _write(extract_kreis_fuel_46251(), "kba_kreis_fuel.csv")
    _write(extract_kreis_euro_46251(), "kba_kreis_euro.csv")
    _write_with_header(
        extract_age_national(),
        "kba_age_national.csv",
        "# mean_age_years=10.9 source=KBA/Statista ID3438 stichtag=2026-01-01",
    )
    _write(extract_gemeinde_ev(), "kba_gemeinde_ev.csv")
    logger.info("[done] all KBA/MiD fleet reference CSVs written to %s", DERIVED_DIR)


if __name__ == "__main__":
    main()
