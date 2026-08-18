"""
Build the blended per-Kreis control target tables (target2026_*) from the
committed MiD / SrV / LSN reference tables, per the 2026-07-08
control-sourcing rules (docs/superpowers/specs/, gitignored).

Attributes built:
- economic_status: MiD H4 x SrV rebuilt status, LSN mean-GdE register arbiter
  (rank agreement on high+very_high).
- number_of_cars {0,1,2,3+}: MiD H7 x SrV cars; no arbiter (KBA is
  registration-by-holder, directional only) -> disagreement shrinks MiD.
  ASSUMPTION: H7 carries no per-Kreis n; MiD precision weight uses the H4
  n_unweighted of the same Kreis (same survey households).
- has_ebike {yes,no}: SrV only (no MiD per-Kreis source exists).
  ASSUMPTION: Wolfsburg uses the SrV region-total share.
- number_of_bicycles {0..4+}: MiD H12.3 x SrV bikes; no arbiter.
- pt_ticket_group {deutschlandticket,other_flatrate,not_flatrate}: MiD P24.1 only
  (issue #321). SrV is NOT blended in: ADR-0060 found its PT question to be a
  usage-conditional ticket TYPE rather than a population ownership rate, and the
  committed SrV Deutschlandticket table is all-ages against MiD's 14+ base. SrV stays
  a corridor CHECK recorded in the ADR.
- employment_status {vollzeit,teilzeit,geringfuegig,sonstiges,erwerbstaetig_unspec,
  in_ausbildung,nicht_erwerbstaetig}: MiD P9 x SrV V_ERW (feature #172); no arbiter
  -> disagreement shrinks MiD. Wolfsburg (03103, not covered by SrV) and Gesamt
  fall back to MiD automatically.

Outputs (committed): eqasim-data/data/braunschweig/targets/target2026_*.csv
with columns ars5,source,n_effective,<categories> (fractions, rows = 8 Kreise
+ Gesamt). These are FINAL targets: the kreis_attribute_control registry must
consume them with prior_n = 0 (blending/shrinkage already applied here).

Usage:
    python scripts/build_blended_kreis_targets.py [--data <eqasim-data/data/braunschweig>] [--out-dir <targets dir>]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_BY_P_BKAT  # noqa: E402
from braunschweig.popsim.blended_targets import BlendConfig, blend_kreis_target  # noqa: E402
from braunschweig.popsim.mid_p9 import mid_p9_employment_status_by_kreis  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_targets")

REPO = Path(__file__).resolve().parents[1]
DATA_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig"

STATUS_CATS = ["very_low", "low", "medium", "high", "very_high"]

# Class taxonomy imported from the source of truth (feature #172), never
# re-listed literally: vollzeit, teilzeit, geringfuegig, sonstiges,
# erwerbstaetig_unspec, in_ausbildung, nicht_erwerbstaetig (P_BKAT code order).
EMPLOYMENT_STATUS_CATS = list(EMPLOYMENT_STATUS_BY_P_BKAT.values())

HEADER_COMMON = """\
# Blended per-Kreis control target, built by scripts/build_blended_kreis_targets.py
# from COMMITTED reference tables only (no raw microdata). Rules: two-survey
# agreement (<= {tol} pp per category) -> precision blend; disagreement with a
# register arbiter -> rank-agreement pick; disagreement without arbiter ->
# MiD shrunk toward Gesamt (lambda = {lam}); Wolfsburg (03103) and Gesamt
# always MiD{ebike_note}. Column `source` records the rule per row;
# shares are fractions summing to 1.
# CONSUMER NOTE: FINAL target - use with kreis_attribute_control prior_n = 0.
# ASSUMPTION (SrV side): stratified PSU design over selected municipalities;
# per-Kreis SrV inputs are assumption-grade (see srv2023_* headers).
"""


def read_csv(path: Path, **kw) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"required committed input missing: {path}")
    return pd.read_csv(path, comment="#", **kw)


def srv_kreis_rows(path: Path, rename: dict) -> pd.DataFrame:
    df = read_csv(path, dtype={"code": str})
    df = df[df["level"] == "kreis"].rename(columns=rename)
    return df[["code", "n_unweighted", *rename.values()]] if rename else df


def build_economic_status(data: Path, config: BlendConfig) -> pd.DataFrame:
    mid = read_csv(data / "mid" / "mid2023_H4_status_by_kreis.csv",
                   dtype={"ars5": str})
    mid["ars5"] = mid["ars5"].replace({"03ZGB": "Gesamt"})
    mid[STATUS_CATS] = mid[STATUS_CATS].astype(float) / 100.0  # row-% -> fractions
    srv = read_csv(data / "srv" / "srv2023_economic_status_by_kreis.csv",
                   dtype={"code": str})
    srv = srv[srv["level"] == "kreis"]
    lsn = read_csv(data / "lsn" / "lsn2022_income_tax_by_kreis.csv",
                   dtype={"ars5": str}).set_index("ars5")
    arbiter = lsn.loc[lsn.index != "03NDS", "mean_gde_eur"]
    return blend_kreis_target(
        mid[["ars5", "n_unweighted", *STATUS_CATS]],
        srv[["code", "n_unweighted", *STATUS_CATS]],
        STATUS_CATS, arbiter=arbiter,
        rank_score_columns=["high", "very_high"], config=config)


def build_number_of_cars(data: Path, config: BlendConfig) -> pd.DataFrame:
    cats = ["cars_0", "cars_1", "cars_2", "cars_3plus"]
    mid = read_csv(data / "mid" / "mid2023_H7_cars_by_kreis.csv",
                   dtype={"ars5": str}).rename(
        columns={"0": "cars_0", "1": "cars_1", "2": "cars_2", "3": "cars_3plus"})
    # ASSUMPTION: H7 has no n column; take the same-survey household n from H4.
    h4 = read_csv(data / "mid" / "mid2023_H4_status_by_kreis.csv",
                  dtype={"ars5": str})
    h4["ars5"] = h4["ars5"].replace({"03ZGB": "Gesamt"})
    mid = mid.merge(h4[["ars5", "n_unweighted"]], on="ars5", how="left")
    srv = srv_kreis_rows(data / "srv" / "srv2023_cars_by_kreis.csv", {})
    return blend_kreis_target(mid[["ars5", "n_unweighted", *cats]],
                              srv[["code", "n_unweighted", *cats]],
                              cats, config=config)


def build_number_of_bicycles(data: Path, config: BlendConfig) -> pd.DataFrame:
    cats = ["bikes_0", "bikes_1", "bikes_2", "bikes_3", "bikes_4plus"]
    mid = read_csv(data / "mid" / "mid2023_H12_3_bikes_by_kreis.csv",
                   dtype={"ars5": str}).rename(
        columns={"0": "bikes_0", "1": "bikes_1", "2": "bikes_2",
                 "3": "bikes_3", "4": "bikes_4plus"})
    h4 = read_csv(data / "mid" / "mid2023_H4_status_by_kreis.csv",
                  dtype={"ars5": str})
    h4["ars5"] = h4["ars5"].replace({"03ZGB": "Gesamt"})
    mid = mid.merge(h4[["ars5", "n_unweighted"]], on="ars5", how="left")
    srv = srv_kreis_rows(data / "srv" / "srv2023_bikes_incl_ebikes_by_kreis.csv", {})
    return blend_kreis_target(mid[["ars5", "n_unweighted", *cats]],
                              srv[["code", "n_unweighted", *cats]],
                              cats, config=config)


def build_employment_status(data: Path, config: BlendConfig) -> pd.DataFrame:
    # mid_p9_employment_status_by_kreis expects the data_path ONE LEVEL ABOVE
    # `data` (it joins "braunschweig/mid/mid2023_P9.csv" itself), unlike the
    # other build_* helpers here, which already take the "braunschweig" dir.
    mid = mid_p9_employment_status_by_kreis(str(data.parent)).rename(
        columns={"code": "ars5"})
    srv = read_csv(data / "srv" / "srv2023_employment_status_by_kreis.csv",
                   dtype={"code": str})
    return blend_kreis_target(
        mid[["ars5", "n_unweighted", *EMPLOYMENT_STATUS_CATS]],
        srv[["code", "n_unweighted", *EMPLOYMENT_STATUS_CATS]],
        EMPLOYMENT_STATUS_CATS, config=config)


def build_has_ebike(data: Path) -> pd.DataFrame:
    # SrV is the ONLY per-Kreis source; no blending. Wolfsburg = region total.
    srv = read_csv(data / "srv" / "srv2023_ebike_household_by_kreis.csv",
                   dtype={"code": str})
    kreis = srv[srv["level"] == "kreis"]
    total = float(srv[srv["level"] == "total"]["share_hh_with_ebike"].iloc[0])
    total_n = int(srv[srv["level"] == "total"]["n_unweighted"].iloc[0])
    rows = [{"ars5": r["code"], "source": "srv",
             "n_effective": int(r["n_unweighted"]),
             "ebike_yes": float(r["share_hh_with_ebike"]),
             "ebike_no": 1.0 - float(r["share_hh_with_ebike"])}
            for _, r in kreis.iterrows()]
    rows.append({"ars5": "03103", "source": "srv_region_total_assumption",
                 "n_effective": total_n, "ebike_yes": total,
                 "ebike_no": 1.0 - total})
    rows.append({"ars5": "Gesamt", "source": "srv", "n_effective": total_n,
                 "ebike_yes": total, "ebike_no": 1.0 - total})
    return pd.DataFrame(rows)


def build_pt_ticket_group(data: Path) -> pd.DataFrame:
    """Three-group PT ticket target from MiD P24.1 (issue #321).

    MiD-ONLY, deliberately: ADR-0060 already rejected an SrV PT-subscription control
    because the SrV question is usage-conditional (the ticket a PT USER travels with),
    not an ownership rate over the population, "and MiD is the better source". The
    committed SrV Deutschlandticket table additionally reports over ALL ages while MiD
    P24.1 is a 14+ base, so precision-blending the two would mix universes -- the
    #96 / #169 error class. SrV therefore stays a documented corridor CHECK
    (srv2023_dticket_by_kreis.csv: 6.08% region / 8.78% Braunschweig, all ages) and is
    NOT blended into this target.

    The nine published categories are collapsed onto attributes.PT_TICKET_GROUPS and each
    Kreis row is renormalised, because the published integer percentages sum to 99-101.
    Collapsing to three groups also makes each cell far thicker than the 9-way split, so
    no Dirichlet shrinkage is applied (consumer uses prior_n = 0, as for every target here).
    """
    from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES
    from braunschweig.popsim.attributes import (
        PT_TICKET_DEUTSCHLANDTICKET, PT_TICKET_OTHER_FLATRATE)

    mid = read_csv(data / "mid" / "mid2023_P24_1.csv", dtype={"ars5": str})
    cats = list(PT_TICKET_CATEGORIES)
    other = [c for c in cats if c in PT_TICKET_OTHER_FLATRATE]
    rest = [c for c in cats
            if c != PT_TICKET_DEUTSCHLANDTICKET and c not in PT_TICKET_OTHER_FLATRATE]
    rows = []
    for _, r in mid.iterrows():
        total = float(sum(float(r[c]) for c in cats))
        if total <= 0:
            raise ValueError(f"build_pt_ticket_group: P24.1 row {r['ars5']} sums to {total}")
        rows.append({
            "ars5": "Gesamt" if str(r["ars5"]) == "03ZGB" else str(r["ars5"]),
            "source": "mid",
            "n_effective": int(r["n_unweighted"]),
            PT_TICKET_DEUTSCHLANDTICKET: float(r[PT_TICKET_DEUTSCHLANDTICKET]) / total,
            "other_flatrate": sum(float(r[c]) for c in other) / total,
            "not_flatrate": sum(float(r[c]) for c in rest) / total,
        })
    return pd.DataFrame(rows)


def write_target(df: pd.DataFrame, out_path: Path, config: BlendConfig,
                 ebike: bool = False, note: str = "") -> None:
    """Write one target table. ``note`` records a per-file exception to the common
    blending rules in the header, so a single-source file cannot be mistaken for a
    blended one (``ebike`` is the pre-existing shorthand for its own exception)."""
    if ebike:
        note = ("; EXCEPTION this file: SrV-only attribute, Wolfsburg uses the "
                "SrV region total (ASSUMPTION)")
    header = HEADER_COMMON.format(tol=config.tolerance_pp,
                                  lam=config.disagreement_shrink_lambda,
                                  ebike_note=note)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(header)
        df.round(4).to_csv(f, index=False)
    log.info("wrote %s (%d rows; sources: %s)", out_path, len(df),
             df["source"].value_counts().to_dict())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DEFAULT)
    parser.add_argument("--out-dir", type=Path,
                        default=DATA_DEFAULT / "targets")
    parser.add_argument("--tolerance-pp", type=float, default=5.0)
    parser.add_argument("--shrink-lambda", type=float, default=0.3)
    args = parser.parse_args(argv)
    config = BlendConfig(tolerance_pp=args.tolerance_pp,
                         disagreement_shrink_lambda=args.shrink_lambda)
    write_target(build_economic_status(args.data, config),
                 args.out_dir / "target2026_economic_status_by_kreis.csv", config)
    write_target(build_number_of_cars(args.data, config),
                 args.out_dir / "target2026_number_of_cars_by_kreis.csv", config)
    write_target(build_has_ebike(args.data),
                 args.out_dir / "target2026_has_ebike_by_kreis.csv", config,
                 ebike=True)
    write_target(build_number_of_bicycles(args.data, config),
                 args.out_dir / "target2026_number_of_bicycles_by_kreis.csv", config)
    write_target(build_pt_ticket_group(args.data),
                 args.out_dir / "target2026_pt_ticket_group_by_kreis.csv", config,
                 note=("; EXCEPTION this file: MiD P24.1 ONLY, no blending and no "
                       "shrinkage. SrV is not a valid target for PT-subscription "
                       "ownership (ADR-0060: usage-conditional ticket TYPE, not an "
                       "ownership rate) and its Deutschlandticket table is all-ages "
                       "against MiD's 14+ base; SrV stays a corridor CHECK only"))
    write_target(build_employment_status(args.data, config),
                 args.out_dir / "target2026_employment_status_by_kreis.csv", config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
