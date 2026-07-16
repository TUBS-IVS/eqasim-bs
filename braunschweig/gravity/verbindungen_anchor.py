"""Inner VerBindungen calibration anchor for the work OD (#193).

Nested, block-preserving, ROW-CONDITIONAL anchoring below the Pendleratlas:
the 2025 Pendleratlas WINS across Kreise (outer ``_calibrate`` block scaling,
untouched); the 2019 VerBindungen QZM only refines destination structure
WITHIN a Kreis pair. For each origin comparison zone ``o`` and destination
Kreis ``K``, the flows to the OBSERVED destination zones are re-weighted so
their conditional shares match the reference, PRESERVING the row-observed
mass -- censored (unobserved, < 10 in 2019) relations and the observed-vs-
censored split stay gravity-driven (censoring rule A). Block totals are
conserved exactly, so the outer anchor cannot be violated; the procedure is
one-shot and idempotent.

Division of labour: production margins belong to popsim (validated r 0.997);
this anchor only reshapes P(destination | origin) -- exactly the quantity the
downstream location choice consumes and check B measures.

Fit-vs-independent: with the anchor ON, the VerBindungen validation (check B)
is a FIT metric; independent validation moves to the MiD distance axes.

All fallbacks/skips are counted and logged (CLAUDE.md fallback transparency);
conservation violations RAISE (never warn).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Relative tolerance for the mass-conservation assertions.
CONSERVATION_RTOL = 1e-9
# Model mass below this is "zero" for the zero-mass guard (flows are floats).
ZERO_MASS_EPS = 1e-12


def collapse_od_to_zones(df_od_cells: pd.DataFrame,
                         df_cell_zone: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a cell-level OD to comparison zones; raise on unmapped ids."""
    zone = df_cell_zone.set_index("cell_id")["zone_id"]
    out = df_od_cells.copy()
    out["origin_zone_id"] = out["origin_cell_id"].map(zone)
    out["destination_zone_id"] = out["destination_cell_id"].map(zone)
    unmapped = out["origin_zone_id"].isna() | out["destination_zone_id"].isna()
    if unmapped.any():
        bad = pd.concat([
            out.loc[out["origin_zone_id"].isna(), "origin_cell_id"],
            out.loc[out["destination_zone_id"].isna(), "destination_cell_id"],
        ]).unique()
        raise RuntimeError(
            "[braunschweig.gravity.verbindungen_anchor] unmapped cell id(s) in "
            f"OD frame: {sorted(map(str, bad))[:5]}"
        )
    return (out.groupby(["origin_zone_id", "destination_zone_id"])["commuters"]
            .sum().reset_index())


def build_anchor_targets(df_ref_od_zones: pd.DataFrame,
                         df_zones,
                         min_observed_commuters: float):
    """Row-conditional targets per (origin zone, destination Kreis).

    Rows whose observed reference mass is below *min_observed_commuters* are
    excluded (coverage guard against censoring-edge noise) and counted.
    Returns ``(df_targets, stats)``; ``target_share`` sums to 1.0 within each
    anchorable row.
    """
    kreis = df_zones.set_index("zone_id")["kreis_id"]
    ref = df_ref_od_zones.copy()
    ref["dest_kreis"] = ref["destination_zone_id"].map(kreis)
    if ref["dest_kreis"].isna().any():
        bad = ref.loc[ref["dest_kreis"].isna(), "destination_zone_id"].unique()
        raise RuntimeError(
            "[braunschweig.gravity.verbindungen_anchor] reference destination "
            f"zone(s) without a Kreis: {sorted(map(str, bad))[:5]}"
        )

    row_mass = (ref.groupby(["origin_zone_id", "dest_kreis"])["commuters"]
                .sum().rename("row_observed_commuters"))
    ref = ref.merge(row_mass.reset_index(),
                    on=["origin_zone_id", "dest_kreis"], how="left")

    n_rows_total = len(row_mass)
    anchorable = ref["row_observed_commuters"] >= float(min_observed_commuters)
    targets = ref[anchorable].copy()
    targets["target_share"] = targets["commuters"] / targets["row_observed_commuters"]

    n_rows_anchorable = targets.groupby(
        ["origin_zone_id", "dest_kreis"]).ngroups
    stats = dict(
        n_rows_total=int(n_rows_total),
        n_rows_anchorable=int(n_rows_anchorable),
        n_rows_skipped_coverage=int(n_rows_total - n_rows_anchorable),
        observed_commuters_quantiles={
            q: float(row_mass.quantile(q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)
        } if n_rows_total else {},
    )
    print(
        "[braunschweig.gravity.verbindungen_anchor] targets: "
        f"{stats['n_rows_anchorable']}/{stats['n_rows_total']} rows anchorable "
        f"(min_observed_commuters={min_observed_commuters}), "
        f"{stats['n_rows_skipped_coverage']} skipped by coverage"
    )
    return (targets[["origin_zone_id", "dest_kreis", "destination_zone_id",
                     "target_share", "row_observed_commuters"]]
            .reset_index(drop=True), stats)
