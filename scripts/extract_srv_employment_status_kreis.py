"""Rebuild the P9 `employment_status` taxonomy on the SrV 2023 Braunschweig+RGB
persons microdata (variable V_ERW) and write a per-Kreis aggregate, for a later
task to blend against the MiD-side employment_status target into a per-Kreis
popsim control (see docs/features/, feature "srv-employment-status-control").

Class taxonomy: imported from
``braunschweig.popsim.attributes.EMPLOYMENT_STATUS_BY_P_BKAT`` -- the same 7
classes used for the MiD donor's ``employment_status`` attribute (vollzeit,
teilzeit, geringfuegig, sonstiges, erwerbstaetig_unspec, in_ausbildung,
nicht_erwerbstaetig) -- NOT re-hardcoded here, so the two sides of the future
blend are guaranteed to share one class list.

Mapping (SrV codeplan ``SrV2023_Datenkodierung_SciUse.xlsx``, variable V_ERW
"Taetigkeit/Erwerbstaetigkeit", verified 2026-07-13 against both the codeplan
and the raw microdata distribution):

    1  Kind (noch nicht eingeschult)             -- never occurs at age >= 14
    2  Hausfrau/-mann                            -> nicht_erwerbstaetig
    3  In Rente/Pension/Vorruhestand              -> nicht_erwerbstaetig
    4  Im Freiwilligendienst                      -> nicht_erwerbstaetig
    5  Zurzeit arbeitslos / Null-Kurzarbeit        -> nicht_erwerbstaetig
    6  Schueler/Schuelerin                        -> nicht_erwerbstaetig
    7  Student/Studentin                          -> nicht_erwerbstaetig
    8  In Ausbildung, Lehre oder Umschulung        -> in_ausbildung
    9  Vollzeit beschaeftigt (>= 35h/Woche)        -> vollzeit
    10 18-34h/Woche beschaeftigt                   -> teilzeit
    11 < 18h/Woche beschaeftigt                    -> geringfuegig
    12 Voruebergehend freigestellt/beurlaubt       -> nicht_erwerbstaetig
    70 Sonstige Taetigkeit                         -> sonstiges

V_ERW asks about the EXTENT of employment ("Umfang der Erwerbstaetigkeit"),
mirroring the MiD P_BKAT variable this table is meant to be blended with: codes
6 (Schueler/in) and 7 (Student/in) are full-time education WITHOUT an
employment relationship and therefore fall under the not-employed catch-all,
while code 8 is specifically an apprenticeship/Lehre WITH an employment
contract and maps to in_ausbildung -- the same distinction the MiD P_BKAT
taxonomy makes (see braunschweig/popsim/attributes.py map_employment_status
docstring). ``erwerbstaetig_unspec`` has NO SrV analogue and is therefore
always 0.0 on the SrV side; it is kept as a column purely for schema parity
with the P_BKAT taxonomy so the later blend can align columns positionally.

Universe: persons aged >= 14 (V_ALTER) with a valid V_ERW response (missing
codes -10 "Unplausibel" and -8 "Nicht erhoben" dropped; V_ERW code 1 "Kind,
noch nicht eingeschult" never occurs in this age-eligible universe -- verified
against the raw microdata, 0/15746 rows -- so it is deliberately absent from
the mapping dict rather than papered over with a fallback). Weight:
GEWICHT_P_ZENSUS (population expansion to Zensus 2022 counts per
municipality; the stratum-internal standard weight GEWICHT_P is mean ~1 WITHIN
each ST_CODE stratum and biases any cross-stratum aggregate, so it is not used
here -- same fix as documented in extract_srv_kreis_tables.py /
extract_srv_economic_status_kreis.py, docs/DECISIONS.md).

Kreis derivation: REUSES ``load_households``/``load_persons`` from
``scripts.extract_srv_kreis_tables`` (the household AGS -> HHNR join), not a
direct ST_CODE dict -- ST_CODE is the survey's sampling stratum and is NOT 1:1
with Kreis in general (e.g. ST_CODE 101/102/197 each span 3-4 Kreise); only
the household file's AGS gives an unambiguous per-person Kreis.

Inputs (local-only raw): SrV2023_Haushalte.csv + SrV2023_Personen.csv.
Writes: eqasim-data/data/braunschweig/srv/srv2023_employment_status_by_kreis.csv

Usage:
    python scripts/extract_srv_employment_status_kreis.py [--raw <dir>] [--out <path>]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

# Allow running this script directly (python scripts/extract_srv_employment_status_kreis.py):
# in that mode sys.path[0] is the scripts/ directory, not the repo root, so the repo root must
# be added explicitly before importing the braunschweig package (same pattern as
# scripts/build_blended_kreis_targets.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_CATEGORIES  # noqa: E402
from scripts.extract_srv_kreis_tables import load_households, load_persons  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("extract_srv_employment_status_kreis")

REPO = Path(__file__).resolve().parents[1]
RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = (REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
               / "srv2023_employment_status_by_kreis.csv")

GESAMT_LABEL = "Gesamt"

# SrV V_ERW ("Taetigkeit/Erwerbstaetigkeit") -> braunschweig.popsim.attributes
# EMPLOYMENT_STATUS_BY_P_BKAT class, per the codeplan mapping documented above.
V_ERW_TO_EMPLOYMENT_STATUS: dict = {
    9: "vollzeit", 10: "teilzeit", 11: "geringfuegig", 8: "in_ausbildung", 70: "sonstiges",
    2: "nicht_erwerbstaetig", 3: "nicht_erwerbstaetig", 4: "nicht_erwerbstaetig",
    5: "nicht_erwerbstaetig", 6: "nicht_erwerbstaetig", 7: "nicht_erwerbstaetig",
    12: "nicht_erwerbstaetig",
}

# V_ERW missing codes excluded from the universe before mapping.
V_ERW_MISSING_CODES = frozenset({-10, -8})

MINIMUM_AGE_YEARS = 14

HEADER_TEMPLATE = """\
# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig (RGB)
#   scientific-use microdata, file SciUse_v4 (delivered 2026-07). See
#   eqasim-data/data/braunschweig/srv/srv2023_raw/README.md.
# Construct: P9 employment_status taxonomy (7 classes imported from
#   braunschweig.popsim.attributes.EMPLOYMENT_STATUS_BY_P_BKAT, NOT
#   re-hardcoded here) rebuilt from SrV V_ERW ("Taetigkeit/Erwerbstaetigkeit")
#   via the codeplan mapping documented in the header of
#   scripts/extract_srv_employment_status_kreis.py (verified 2026-07-13
#   against SrV2023_Datenkodierung_SciUse.xlsx and the raw microdata
#   distribution).
# Universe: persons aged >= 14 (V_ALTER) with a valid V_ERW response;
#   V_ERW in {{-10 Unplausibel, -8 Nicht erhoben}} excluded (rate logged at
#   generation time). erwerbstaetig_unspec has no SrV analogue and is always
#   0.0 here (kept only for column parity with the P_BKAT taxonomy).
# Weight: GEWICHT_P_ZENSUS (population expansion to Zensus 2022 counts per
#   municipality; the stratum-internal GEWICHT_P is mean ~1 per ST_CODE
#   stratum and would bias any cross-stratum aggregate, so it is not used
#   here -- same fix as srv2023_economic_status_by_kreis.csv,
#   docs/DECISIONS.md). Shares are fractions and sum to 1 per row.
# Coverage: 7 of the 8 ZGB Kreise; Wolfsburg (03103) is NOT covered by this
#   survey and therefore never appears in this table. The "{gesamt}" row is
#   the region-wide (7-Kreise) weighted aggregate.
# Kreis derivation: household AGS -> HHNR join (scripts.extract_srv_kreis_tables
#   load_households/load_persons), NOT a direct ST_CODE dict -- ST_CODE (the
#   survey's sampling stratum) is not 1:1 with Kreis in general.
# n_unweighted: raw respondent count per Kreis in the universe above (not
#   weight-expanded).
# Generated by: scripts/extract_srv_employment_status_kreis.py
""".format(gesamt=GESAMT_LABEL)


def build_employment_status_table(persons_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate weighted employment_status (P9 taxonomy) shares per Kreis.

    Pure function: expects ``persons_df`` to already carry ``V_ERW`` (raw SrV
    code), ``V_ALTER`` (age in years), ``GEWICHT_P_ZENSUS`` (population
    expansion weight), and ``kreis`` (5-digit ARS, already resolved -- see
    :func:`load_persons_with_kreis` for how the real pipeline builds it via
    the household AGS -> HHNR join).

    Universe: age >= 14 with a valid (non-missing) V_ERW code. No silent
    fallback: the age-eligible-but-missing-V_ERW drop rate is logged, and an
    empty resulting group raises rather than silently producing NaN shares.

    Returns a frame with columns ``["code", *EMPLOYMENT_STATUS_CATEGORIES,
    "n_unweighted"]``: one row per Kreis present in ``persons_df`` (each row's
    7 class shares summing to 1.0) plus a region-aggregate row labelled
    ``"Gesamt"``.
    """
    df = persons_df.copy()
    df["age"] = pd.to_numeric(df["V_ALTER"], errors="coerce")
    df["erw"] = pd.to_numeric(df["V_ERW"], errors="coerce")
    df["weight"] = pd.to_numeric(df["GEWICHT_P_ZENSUS"], errors="coerce")

    age_eligible = df[df["age"] >= MINIMUM_AGE_YEARS].copy()
    n_age_eligible = len(age_eligible)

    valid = age_eligible[~age_eligible["erw"].isin(V_ERW_MISSING_CODES)].copy()
    n_dropped_missing = n_age_eligible - len(valid)
    if n_age_eligible > 0:
        rate = 100.0 * n_dropped_missing / n_age_eligible
        _log = logger.warning if n_dropped_missing else logger.info
        _log(
            "[employment_status] age >= %d universe: %d/%d (%.2f%%) rows dropped "
            "for a missing V_ERW code (-10/-8); %d rows retained",
            MINIMUM_AGE_YEARS, n_dropped_missing, n_age_eligible, rate, len(valid),
        )

    unmapped = valid[~valid["erw"].isin(V_ERW_TO_EMPLOYMENT_STATUS)]
    if not unmapped.empty:
        raise ValueError(
            f"build_employment_status_table: {len(unmapped)} rows have a V_ERW code "
            f"outside the codeplan mapping ({sorted(unmapped['erw'].unique().tolist())}); "
            "the mapping is expected to be exhaustive for the age >= 14, non-missing "
            "universe -- extend V_ERW_TO_EMPLOYMENT_STATUS after re-checking the codeplan, "
            "do not fall back silently."
        )
    valid["employment_status"] = valid["erw"].map(V_ERW_TO_EMPLOYMENT_STATUS)

    def _one_group(group: pd.DataFrame, code: str) -> dict:
        total_weight = group["weight"].sum()
        if total_weight <= 0:
            raise ValueError(
                f"build_employment_status_table: empty or non-positive weighted group "
                f"for code={code!r} ({len(group)} rows); cannot compute shares."
            )
        dist = (
            group.groupby("employment_status")["weight"].sum()
            .reindex(EMPLOYMENT_STATUS_CATEGORIES, fill_value=0.0)
        )
        row = {"code": code, "n_unweighted": int(len(group))}
        row.update({cat: float(dist[cat] / total_weight) for cat in EMPLOYMENT_STATUS_CATEGORIES})
        return row

    rows = [_one_group(group, str(kreis)) for kreis, group in sorted(valid.groupby("kreis"))]
    rows.append(_one_group(valid, GESAMT_LABEL))

    columns = ["code", *EMPLOYMENT_STATUS_CATEGORIES, "n_unweighted"]
    return pd.DataFrame(rows)[columns]


def load_persons_with_kreis(raw_dir: Path) -> pd.DataFrame:
    """Load the SrV persons file with a resolved per-person ``kreis`` (ARS5) column.

    Reuses ``load_households``/``load_persons`` from
    ``scripts.extract_srv_kreis_tables`` verbatim: the Kreis is derived from the
    household file's AGS (NOT from the person-level ``ST_CODE`` sampling
    stratum, which spans multiple Kreise for several strata -- verified via a
    ST_CODE x Kreis cross-tabulation on the raw household file, e.g. ST_CODE
    101/102/197 each cover 3-4 Kreise) and attached to each person via the
    ``HHNR`` join. Also applies the ``GEWICHT_P_ZENSUS`` weight-validity filter
    from that loader (0 rows dropped in the delivered SrV file, verified).
    """
    households_valid, kreis_by_hhnr = load_households(raw_dir)
    return load_persons(raw_dir, kreis_by_hhnr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT,
                        help="Directory containing the raw SrV CSV files.")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT,
                        help="Output path for the derived per-Kreis employment_status CSV.")
    args = parser.parse_args(argv)

    if not args.raw.exists():
        sys.stderr.write(f"[srv-employment-status] Raw data directory not found: {args.raw}\n")
        return 2

    persons = load_persons_with_kreis(args.raw)
    table = build_employment_status_table(persons)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        handle.write(HEADER_TEMPLATE)
        table.to_csv(handle, index=False)

    print(f"wrote {args.out} ({len(table)} rows)")
    print(table.round(4).to_string(index=False))
    gesamt_ausbildung = table.loc[table["code"] == GESAMT_LABEL, "in_ausbildung"].iloc[0]
    print(f"\nregional (Gesamt) in_ausbildung share: {gesamt_ausbildung:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
