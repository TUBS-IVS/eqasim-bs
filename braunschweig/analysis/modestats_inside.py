"""Outside-free MATSim modestats: drop the cordon ``outside`` pseudo-mode and
renormalise the per-iteration mode shares over the real modes, then write a CSV
and a mode-share-evolution plot.

``outside`` is the eqasim scenario-cutter marker for the part of a trip beyond
the cordon (see ``NetworkTripProcessor`` in the Java cutter), NOT a real
transport mode. The native MATSim ``ModeStatsControlerListener`` includes it, so
the ``modestats.csv`` / ``modestats.png`` it writes mix it into the modal split.
This post-processor produces ``modestats_inside.csv`` + ``modestats_inside.png``
reporting the modal split of the real modes inside the study area (shares sum to
1 per iteration). It is the lightweight equivalent of a custom controller
listener -- no Java rebuild, run after the simulation:

    python -m braunschweig.analysis.modestats_inside --sim-output <output_dir>/simulation_output

No-op-safe: if no ``outside`` column is present (cordon off) the inside shares
equal the input (already real modes), so the artefact is still produced.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

OUTSIDE_MODE = "outside"


def renormalise_without_outside(modestats: pd.DataFrame) -> pd.DataFrame:
    """Drop the ``outside`` column and renormalise the per-iteration mode shares.

    ``modestats`` is a MATSim modestats frame: an ``iteration`` column plus one
    column per mode holding that iteration's mode share (fractions summing to 1
    across all modes). Returns a copy without the ``outside`` column where each
    row's remaining mode shares are rescaled to sum to 1 (the modal split of the
    real modes inside the study area).

    Raises ``ValueError`` if there is no ``iteration`` column or no real mode
    column remains after dropping ``outside``.
    """
    if "iteration" not in modestats.columns:
        raise ValueError("modestats frame must have an 'iteration' column")
    mode_cols = [
        c for c in modestats.columns if c not in ("iteration", OUTSIDE_MODE)
    ]
    if not mode_cols:
        raise ValueError("modestats frame has no real mode columns")

    out = modestats[["iteration"] + mode_cols].copy()
    # Rows with a zero real-mode sum (degenerate) keep their zeros instead of
    # dividing by zero; in practice every iteration has trips inside.
    row_sums = out[mode_cols].sum(axis=1).replace(0, 1.0)
    for column in mode_cols:
        out[column] = out[column] / row_sums
    return out


def _plot_evolution(inside: pd.DataFrame, png_path: Path) -> Path:
    """Line plot of the inside modal split over MATSim iterations -> ``png_path``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mode_cols = [c for c in inside.columns if c != "iteration"]
    fig, ax = plt.subplots(figsize=(9, 5))
    for column in mode_cols:
        ax.plot(
            inside["iteration"], inside[column] * 100.0,
            label=column, linewidth=1.6,
        )
    ax.set_xlabel("MATSim iteration")
    ax.set_ylabel("mode share inside study area [%]")
    ax.set_title("Modal split evolution (cordon 'outside' excluded; sums to 100% inside)")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(png_path, dpi=120)
    plt.close(fig)
    return png_path


def write_modestats_inside(sim_output, source_filename: str = "modestats.csv"):
    """Read ``<sim_output>/modestats.csv``, drop/renormalise ``outside``, and write
    ``modestats_inside.csv`` + ``modestats_inside.png`` next to it.

    Returns ``(csv_path, png_path)``. Raises ``FileNotFoundError`` if the source
    modestats file is missing.
    """
    sim_output = Path(sim_output)
    source = sim_output / source_filename
    if not source.exists():
        raise FileNotFoundError(f"no {source_filename} under {sim_output}")

    modestats = pd.read_csv(source, sep=";")
    inside = renormalise_without_outside(modestats)

    csv_path = sim_output / "modestats_inside.csv"
    inside.to_csv(csv_path, sep=";", index=False)
    png_path = _plot_evolution(inside, sim_output / "modestats_inside.png")
    return csv_path, png_path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Write an outside-free modestats CSV + mode-share plot from a "
        "MATSim/eqasim simulation_output directory."
    )
    parser.add_argument(
        "--sim-output", required=True,
        help="MATSim/eqasim output directory containing modestats.csv",
    )
    args = parser.parse_args(argv)
    csv_path, png_path = write_modestats_inside(args.sim_output)
    print(f"[modestats_inside] wrote {csv_path}")
    print(f"[modestats_inside] wrote {png_path}")


if __name__ == "__main__":
    main()
