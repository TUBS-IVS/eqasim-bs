#!/usr/bin/env python
"""Classify the cars of a production run by the BEV:PHEV composition-tilt accounting (issue C5b).

Background. The 100 % production run ``prod100_20261031`` logged, for the residents fleet,
``powertrain Gemeinde BEV/PHEV composition tilt: primary 604514/679187 (89.0%), fallback 74673
(11.0%)``. The composition factor of a kreisfreie Stadt (the only Gemeinde of its Kreis) is 1.0
by construction, so such cars can be neither a real "primary" hit nor a real "missing reference".
This script answers, per distinct home Gemeinde, how many cars fall into which accounting class
and therefore how much of the 11 % is structural and how much a real missing reference.

What it does (read-only on the run directory):

1. Rebuild the ``df_cars`` frame the household stage handed to ``sample_fleet`` with the
   helper of ``replay_composition_clip.py`` in the artifact folder of the run
   ``prod100-fleet-composition-clip-2026-11-04`` (next to this folder).
2. Build the sampler of the run's own ``fleet_sampling_de`` module, or of a patched one
   (``--module-override``), exactly as ``sample_fleet`` does (``FleetSampler.from_data_path``).
3. For every distinct ``(kreis_ags5, gemeinde)`` of the cars call
   ``PowertrainModel.powertrain_probabilities`` ONCE and read which accounting counter moved.
   The class of a car depends only on its home ``(kreis_ags5, gemeinde)``: the lookup keys, the
   applicability test and the counters never read the segment, the grid columns or the pmf values
   (``_apply_gemeinde_tilt``). The class is weighted by the number of cars of that key.
4. Write one row per Gemeinde and a JSON summary whose totals are comparable with the
   ``log_fallback_rate`` lines of the run log.

Usage (on the machine that holds the run, with the run's own Python environment)::

    PYTHONDONTWRITEBYTECODE=1 python decompose_composition_accounting.py \\
        --run-dir ~/prod100_20261031 --out-dir ~/c5b_replay_20261104/decomp_baseline --label baseline
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

# The replay helpers (module loading, rebuilding df_cars) are those of the run
# prod100-fleet-composition-clip-2026-11-04, whose artifact folder lies next to this one.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prod100-fleet-composition-clip-2026-11-04"))
import replay_composition_clip as rcc  # noqa: E402

logger = logging.getLogger("decompose_composition_accounting")

#: The counters read before and after each probe. A counter that does not exist in the module
#: under test (the production module has no ``_gemeinde_composition_not_applicable``) reads 0.
COUNTERS = ("_gemeinde_primary", "_gemeinde_fallback", "_gemeinde_composition_primary",
            "_gemeinde_composition_fallback", "_gemeinde_composition_not_applicable")


def snapshot(model) -> dict[str, int]:
    return {name: int(getattr(model, name, 0)) for name in COUNTERS}


def classify(delta: dict[str, int]) -> str:
    """The accounting class of one probed car, from the counter deltas of its single call."""
    moved = [name for name, value in delta.items() if value]
    if delta["_gemeinde_composition_not_applicable"] == 1:
        return "composition_not_applicable"
    if delta["_gemeinde_composition_primary"] == 1:
        return "composition_primary"
    if delta["_gemeinde_composition_fallback"] == 1:
        return "composition_fallback"
    if delta["_gemeinde_fallback"] == 1 and not delta["_gemeinde_composition_primary"]:
        # No 2026 Gemeinde row for the car's key: the combined tilt itself did not fire, so the
        # composition was never looked up (early return in _apply_gemeinde_tilt).
        return "gemeinde_tilt_unmatched"
    return "other:" + "+".join(moved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--code-dir", type=Path, default=None,
                        help="the run's code worktree (default: RUN_DIR/eqasim-bs)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--module-override", type=Path, default=None,
                        help="patched fleet_sampling_de.py to run instead of the run's copy")
    parser.add_argument("--label", default="baseline")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                        datefmt="%H:%M:%S")
    code_dir = (args.code_dir or args.run_dir / "eqasim-bs").resolve()
    cache_dir = (args.run_dir / "cache").resolve()
    data_path = str(code_dir / "eqasim-data" / "data")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    fleet = rcc.load_fleet_module(code_dir,
                                  args.module_override.resolve() if args.module_override else None)
    logger.info("fleet module: %s", fleet.__file__)
    df_cars, _reference = rcc.rebuild_df_cars(cache_dir, rcc.DEFAULT_STEMS, data_path)
    logger.info("df_cars rebuilt: %d cars in %.0f s", len(df_cars), time.time() - started)

    sampler = fleet.FleetSampler.from_data_path(data_path, size_map=None)
    model = sampler.powertrain_model
    model.gemeinde_bev_composition_tilt = True  # the run's setting (default True)
    probe_segment = sampler.segment_model.segments[0]

    # sample_fleet maps a NaN Gemeinde to None before the call; count those cars under "<none>".
    keyed = df_cars.assign(
        _gemeinde=df_cars["gemeinde"].where(df_cars["gemeinde"].notna(), None))
    counts = (keyed.groupby(["kreis_ags5", "_gemeinde"], dropna=False).size()
              .rename("cars").reset_index())
    rows = []
    for record in counts.itertuples(index=False):
        gemeinde = record._1 if not pd.isna(record._1) else None
        before = snapshot(model)
        model.powertrain_probabilities(probe_segment, str(record.kreis_ags5), gemeinde,
                                       grid_ev_share=None, gemeinde_grid_mean=None)
        after = snapshot(model)
        delta = {name: after[name] - before[name] for name in COUNTERS}
        rows.append({"kreis_ags5": str(record.kreis_ags5),
                     "gemeinde": "<none>" if gemeinde is None else gemeinde,
                     "cars": int(record.cars), "accounting_class": classify(delta)})
    table = pd.DataFrame(rows).sort_values(["accounting_class", "cars"],
                                           ascending=[True, False])
    table.to_csv(args.out_dir / f"classification_{args.label}.csv", index=False)

    by_class = table.groupby("accounting_class")["cars"].sum().to_dict()
    by_kreis_class = (table.groupby(["kreis_ags5", "accounting_class"])["cars"].sum()
                      .unstack(fill_value=0))
    by_kreis_class.to_csv(args.out_dir / f"classification_by_kreis_{args.label}.csv")
    fallback = table[table["accounting_class"] == "composition_fallback"]
    primary = by_class.get("composition_primary", 0)
    fallback_cars = by_class.get("composition_fallback", 0)
    not_applicable = by_class.get("composition_not_applicable", 0)
    applicable = primary + fallback_cars
    summary = {
        "label": args.label,
        "fleet_module": str(fleet.__file__),
        "cars": int(len(df_cars)),
        "cars_by_accounting_class": {k: int(v) for k, v in sorted(by_class.items())},
        "composition_primary": int(primary),
        "composition_fallback": int(fallback_cars),
        "composition_not_applicable": int(not_applicable),
        "composition_counted_total": int(applicable),
        "composition_fallback_rate_of_counted": (fallback_cars / applicable) if applicable else None,
        "fallback_cars_by_kreis": {k: int(v) for k, v in
                                   fallback.groupby("kreis_ags5")["cars"].sum().items()},
        "fallback_cars_top_gemeinden": [
            {"kreis_ags5": r.kreis_ags5, "gemeinde": r.gemeinde, "cars": int(r.cars)}
            for r in fallback.sort_values("cars", ascending=False).head(12).itertuples()],
        "distinct_gemeinde_keys": int(len(table)),
    }
    (args.out_dir / f"summary_{args.label}.json").write_text(json.dumps(summary, indent=2))
    logger.info("SUMMARY: %s", json.dumps(summary))
    logger.info("by Kreis and class:\n%s", by_kreis_class.to_string())
    logger.info("done in %.0f s", time.time() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
