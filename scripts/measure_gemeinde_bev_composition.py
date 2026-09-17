"""Measure the per-Gemeinde BEV:PHEV composition tilt against its reference.

Diagnostic for issue #317 / ADR-0125. The 2026 per-Gemeinde EV export publishes
only the COMBINED electric share, so ADR-0086 tilts both electric powertrains by
one factor; the composition tilt restores the BEV-vs-PHEV structure from the FZ
27.17 private-car table. This script reports, per ZGB Kreis:

* how many Gemeinden carry a usable BEV:PHEV ratio and which do not (the
  no-silent-fallback rate for the composition, at reference level);
* the electric-stock-weighted Kreis BEV fraction the factors are relative to,
  next to the all-ownership BEV fraction of the 46251-02 per-Kreis marginal the
  tilt is applied to -- the gap between these two is the only quantity the
  design does not preserve exactly;
* the realised per-Kreis BEV and PHEV aggregate SHIFT the tilt produces, in
  percentage points of the all-car fleet, measured by running the REAL
  :class:`~braunschweig.synthesis.vehicles.fleet_sampling_de.PowertrainModel`
  with the tilt ON and OFF over every (Gemeinde, segment) pair. The shift is
  segment-weighted on purpose: per-segment BEV:PHEV splits are far more
  dispersed than the Kreis aggregate (``minis`` is ~100 % BEV, ``gelaendewagen``
  ~33 %), so a Kreis-level approximation understates the worst case by about an
  order of magnitude;
* the electric TOTAL under both settings, which must be identical -- the
  composition redistributes within the electric mass and may never change its
  level;
* the per-Gemeinde factor extremes, so a new KBA vintage with a more extreme
  composition becomes visible rather than silently clipping.

IMPORTANT -- what the reported shift is, and is not. It is the UNCORRECTED
baseline: the aggregate movement the composition factors alone would cause. It is
NOT what a production run realises. ``sample_fleet`` additionally neutralises the
aggregate on the drawn population
(:func:`~braunschweig.synthesis.vehicles.fleet_sampling_de._neutralise_composition_aggregate`,
ADR-0125 decision 8), because these factors are centred on FZ 27.17 electric-stock
weights while the ADR-0085 rake targets the unweighted mean of the cars that
actually exist. This script deliberately reports the pre-correction number, so the
size of what the correction removes stays visible instead of being hidden by it.

Usage (from the repository root):

    python scripts/measure_gemeinde_bev_composition.py
    python scripts/measure_gemeinde_bev_composition.py --data-path PATH

Inputs are the committed derived tables
``eqasim-data/data/braunschweig/kba/derived/kba_gemeinde_private_bev.csv``
(FZ 27.17, the composition reference) and ``kba_kreis_fuel.csv`` (46251-02, the
per-Kreis powertrain marginal the tilt acts on).

This is a diagnostic, not a pipeline stage: it reads reference data only and
writes nothing.
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
from braunschweig.synthesis.vehicles.fleet_sampling_de import (  # noqa: E402
    GEMEINDE_TILT_CLIP,
    PowertrainModel,
    _gemeinde_electric_composition,
    normalize_gemeinde_name,
)

logger = logging.getLogger("measure_gemeinde_bev_composition")

DEFAULT_DATA_PATH = REPO_ROOT / "eqasim-data" / "data"

#: The 8 Kreise of the Zweckverband Grossraum Braunschweig (AGS-5).
ZGB_KREISE = ("03101", "03102", "03103", "03151", "03153", "03154", "03157",
              "03158")


def _load(data_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read the FZ 27.17 composition reference and the 46251-02 Kreis marginal."""
    derived = data_path / "braunschweig" / "kba" / "derived"
    fz_path = derived / "kba_gemeinde_private_bev.csv"
    kreis_path = derived / "kba_kreis_fuel.csv"
    for path in (fz_path, kreis_path):
        if not path.exists():
            raise SystemExit(
                f"required reference table not found: {path}\n"
                "Run scripts/extract_kba_fleet.py or check --data-path."
            )
    df_gemeinde = pd.read_csv(fz_path, dtype={"kreis_ags5": str})
    df_kreis = pd.read_csv(kreis_path, dtype={"kreis_ags5": str})
    return df_gemeinde, df_kreis


def _segment_weights(data_path: Path) -> tuple[list[str], np.ndarray]:
    """The KBA FZ 27.10 segment marginal ``P(segment)``, normalised."""
    df_segment = ft.load_segment_powertrain(str(data_path))
    segments = list(df_segment["segment"].unique())
    share = (df_segment.drop_duplicates("segment")
             .set_index("segment")["segment_share"].astype(float))
    weights = share.reindex(segments).to_numpy(dtype=float)
    return segments, weights / weights.sum()


def _realised_shift(model_on: PowertrainModel, model_off: PowertrainModel,
                    segments: list[str], segment_share: np.ndarray,
                    rows: pd.DataFrame, kreis_ags5: str,
                    ) -> tuple[float, float, float, float]:
    """Segment-weighted Kreis aggregate under the tilt ON vs OFF.

    Evaluates the REAL per-car pmf for every (Gemeinde, segment) pair rather
    than approximating with the Kreis-aggregate split. That matters: per-segment
    BEV:PHEV splits are far more dispersed than the Kreis one, and the residual
    the tilt leaves is nonlinear in that split, so the Kreis-level approximation
    understates the worst case by roughly an order of magnitude.

    Each pair is weighted by ``private_total(Gemeinde) * P(segment)``, i.e. the
    car count the Gemeinde contributes to that segment.

    Returns:
        ``(delta_bev_pp, delta_phev_pp, electric_on, electric_off)`` -- the first
        two in percentage points of the all-car fleet, the last two as shares so
        the caller can assert the electric LEVEL is untouched.
    """
    index = {p: i for i, p in enumerate(model_on.powertrains)}
    accumulated_on = np.zeros(len(model_on.powertrains))
    accumulated_off = np.zeros(len(model_on.powertrains))
    weight_total = 0.0
    for _, row in rows.iterrows():
        cars = float(row["private_total"])
        gemeinde_norm = row["gemeinde_norm"]
        for position, segment in enumerate(segments):
            weight = cars * segment_share[position]
            accumulated_on += weight * model_on.powertrain_probabilities(
                segment, kreis_ags5, gemeinde_norm)
            accumulated_off += weight * model_off.powertrain_probabilities(
                segment, kreis_ags5, gemeinde_norm)
            weight_total += weight
    if weight_total <= 0.0:
        return 0.0, 0.0, 0.0, 0.0
    accumulated_on /= weight_total
    accumulated_off /= weight_total
    electric_on = accumulated_on[index["bev"]] + accumulated_on[index["phev"]]
    electric_off = accumulated_off[index["bev"]] + accumulated_off[index["phev"]]
    return (100.0 * (accumulated_on[index["bev"]] - accumulated_off[index["bev"]]),
            100.0 * (accumulated_on[index["phev"]] - accumulated_off[index["phev"]]),
            float(electric_on), float(electric_off))


def report(data_path: Path) -> None:
    """Print the composition diagnostic for every ZGB Kreis."""
    df_gemeinde, df_kreis = _load(data_path)
    per_gemeinde, per_kreis = _gemeinde_electric_composition(df_gemeinde)

    df_gemeinde = df_gemeinde.copy()
    df_gemeinde["gemeinde_norm"] = df_gemeinde["gemeinde"].map(normalize_gemeinde_name)
    kreis_indexed = df_kreis.set_index("kreis_ags5")

    segments, segment_share = _segment_weights(data_path)
    model_on = PowertrainModel.from_data_path(str(data_path), segments)
    model_off = PowertrainModel.from_data_path(str(data_path), segments)
    model_off.gemeinde_bev_composition_tilt = False

    print("")
    print("Per-Gemeinde BEV:PHEV composition tilt (issue #317 / ADR-0125)")
    print("  reference : FZ 27.17 kba_gemeinde_private_bev.csv (private cars, 2025-01-01)")
    print("  applied to: 46251-02 kba_kreis_fuel.csv (all ownership, 2025-01-01)")
    print("  clip band : %s, renormalised per Kreis after clipping" % (GEMEINDE_TILT_CLIP,))
    print("  shift     : real PowertrainModel ON vs OFF, weighted by "
          "private_total x P(segment)")
    print("")
    header = ("%-7s %5s %5s | %8s %8s %7s | %10s %10s | %6s %6s"
              % ("kreis", "used", "all", "bevFZ", "bev4625", "gap_pp",
                 "dBEV_pp", "dPHEV_pp", "f_min", "f_max"))
    print(header)
    print("-" * len(header))

    total_used = 0
    total_all = 0
    excluded_names: list[str] = []
    worst_shift = 0.0
    worst_electric_drift = 0.0
    for kreis_ags5 in ZGB_KREISE:
        rows = df_gemeinde[df_gemeinde["kreis_ags5"] == kreis_ags5]
        n_all = len(rows)
        total_all += n_all
        keys = [(kreis_ags5, name) for name in rows["gemeinde_norm"]]
        used = [key for key in keys if key in per_gemeinde]
        total_used += len(used)
        excluded_names.extend(
            "%s/%s" % (kreis_ags5, name)
            for (_, name) in keys if (kreis_ags5, name) not in per_gemeinde
        )

        if not used or kreis_ags5 not in per_kreis or kreis_ags5 not in kreis_indexed.index:
            print("%-7s %5d %5d | %8s %8s %7s | %10s %10s | %6s %6s"
                  % (kreis_ags5, len(used), n_all, "--", "--", "--", "--", "--",
                     "--", "--"))
            continue

        kreis_row = kreis_indexed.loc[kreis_ags5]
        base_bev = float(kreis_row["bev"])
        base_phev = float(kreis_row["phev"])
        bev_frac_46251 = base_bev / (base_bev + base_phev)

        delta_bev, delta_phev, electric_on, electric_off = _realised_shift(
            model_on, model_off, segments, segment_share, rows, kreis_ags5)
        worst_shift = max(worst_shift, abs(delta_bev), abs(delta_phev))
        worst_electric_drift = max(worst_electric_drift,
                                   abs(electric_on - electric_off))

        factors = np.array([[per_gemeinde[key][pt] for pt in ("bev", "phev")]
                            for key in used])
        print("%-7s %5d %5d | %8.4f %8.4f %+7.2f | %+10.5f %+10.5f | %6.3f %6.3f"
              % (kreis_ags5, len(used), n_all, per_kreis[kreis_ags5],
                 bev_frac_46251, 100.0 * (per_kreis[kreis_ags5] - bev_frac_46251),
                 delta_bev, delta_phev, factors.min(), factors.max()))

    print("")
    print("Coverage: %d/%d Gemeinden carry a usable BEV:PHEV ratio (%.1f%%)."
          % (total_used, total_all,
             100.0 * total_used / total_all if total_all else 0.0))
    if excluded_names:
        print("Without a usable ratio (composition factor 1.0, counted at run time):")
        for name in sorted(excluded_names):
            print("  %s" % name)
    print("")
    print("Largest realised per-Kreis BEV:PHEV shift : %.5f pp of the all-car fleet."
          % worst_shift)
    print("Largest electric-TOTAL drift             : %.3e (must be ~0: the "
          "composition may not change the electric LEVEL)." % worst_electric_drift)
    print("")
    print("This is the UNCORRECTED baseline: production additionally neutralises the")
    print("aggregate on the drawn population (ADR-0125 decision 8), so a real run does")
    print("NOT carry this shift. It is reported to keep the size of what the correction")
    print("removes visible. It is NOT a calibration target.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-path", type=Path, default=DEFAULT_DATA_PATH,
        help="synpp data_path (parent of braunschweig/kba/derived); "
             "default: %(default)s")
    parser.add_argument(
        "--log-level", default="WARNING",
        help="logging level for the builder's own coverage logs "
             "(default: %(default)s; use INFO to see them)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(),
                        format="%(levelname)s %(message)s")
    report(args.data_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
