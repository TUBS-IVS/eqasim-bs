"""Measure the realised per-Kreis powertrain split against the KBA reference.

Reproduces the evidence of ADR-0085. The synthetic fleet's powertrain
distribution must match the committed per-Kreis reference (Destatis
Regionalstatistik 46251-02, ``kba_kreis_fuel.csv``); the realised-margin
validator cannot see a deviation from it, because it deliberately compares
against the EFFECTIVE (post-mask, post-weight) targets rather than the raw KBA
marginal (ADR-0082 finding 2, to avoid crying wolf on the segment dimension).

The script reports the petrol share of combustion cars for four configurations,
so a drift can be attributed to the feature that causes it:

  * both the feasible-fuels mask and the per-model fuel weights active
    (production),
  * either one disabled,
  * both disabled (the pure raked marginal).

Historical measurement (issue #277): with the per-Kreis powertrain rake limited to
the ELECTRIC mass, production drifted to +10.2pp petrol against the ZGB reference
(0.772 vs 0.670) -- the model-fuel weights biased the surviving combustion mass and
nothing corrected it. Raking every powertrain (ADR-0085) brings it back to -0.3pp.

Usage (from the repository root):

    python scripts/measure_combustion_split.py [--n-cars N] [--seed S]

This is a diagnostic: it reads reference data and writes nothing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

logger = logging.getLogger("measure_combustion_split")

DEFAULT_DATA_PATH = REPO_ROOT / "eqasim-data" / "data"


def reference_petrol_share(data_path: str) -> float:
    """Petrol share of combustion cars in the ZGB aggregate of 46251-02."""
    df = ft.load_kreis_fuel(data_path)
    zgb = df[df["kreis_ags5"].isin(ft.ZGB_KREISE_AGS5)]
    petrol = float(zgb["petrol"].sum())
    diesel = float(zgb["diesel"].sum())
    if petrol + diesel <= 0:
        raise ValueError(
            "the ZGB rows of kba_kreis_fuel.csv carry no petrol/diesel mass")
    return petrol / (petrol + diesel)


def build_cars(n_cars: int, seed: int) -> pd.DataFrame:
    """A synthetic car frame spread over the ZGB Kreise and status classes."""
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    return pd.DataFrame([{
        "economic_status": statuses[i % len(statuses)],
        "kreis_ags5": ft.ZGB_KREISE_AGS5[i % len(ft.ZGB_KREISE_AGS5)],
        "gemeinde": float("nan"),
        "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
    } for i in range(n_cars)])


def realised_petrol_share(data_path: str, df_cars: pd.DataFrame, seed: int,
                          *, model_fuel: bool, feasible: bool) -> tuple[float, int]:
    """Draw a fleet with the two features toggled and return (share, n)."""
    sampler = fs.FleetSampler.from_data_path(data_path)
    if not model_fuel:
        sampler.model_fuel = None
    if not feasible:
        sampler.feasible_fuels = None
    spec, _types, _summary = fs.sample_fleet(
        df_cars, data_path, random_seed=seed, sampler=sampler)
    petrol = int((spec["powertrain"] == "petrol").sum())
    diesel = int((spec["powertrain"] == "diesel").sum())
    if petrol + diesel == 0:
        raise ValueError("no combustion cars drawn")
    return petrol / (petrol + diesel), petrol + diesel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--n-cars", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data_path = str(args.data_path)

    reference = reference_petrol_share(data_path)
    logger.info("reference (46251-02, ZGB aggregate): petrol/(petrol+diesel) = "
                "%.4f", reference)

    df_cars = build_cars(args.n_cars, args.seed)
    configurations = (
        ("model_fuel ON,  feasibility ON  (production)", True, True),
        ("model_fuel OFF, feasibility ON", False, True),
        ("model_fuel ON,  feasibility OFF", True, False),
        ("model_fuel OFF, feasibility OFF", False, False),
    )
    for label, model_fuel, feasible in configurations:
        share, n = realised_petrol_share(
            data_path, df_cars, args.seed, model_fuel=model_fuel,
            feasible=feasible)
        logger.info("%-46s %.4f  (deviation %+.1fpp, n=%d)",
                    label, share, 100.0 * (share - reference), n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
