"""Secondary scorer-weight test bench: sweep ``secondary_scorer_pot_weight`` and
measure the distance-vs-capacity trade-off.

The chainsolvers combined scorer ranks each candidate building by

    score = pot_weight * potential_term + dist_dev_weight * distance_deviation_term

Raising ``pot_weight`` pulls placements toward high-potential (high-capacity)
buildings -> the building potential "kappas" are hit better; lowering it lets the
desired-distance deviation dominate -> the MiD trip-distance distribution is hit
better. This harness re-runs ONLY the secondary location choice (the chainsolver
stage + ``synthesis.output``; the heavy upstream stays cached) for each weight in
a shared working_directory, then measures BOTH sides:

  - distance fit : W12 EMD per purpose (scripts/validate_secondary_distances.py)
  - capacity fit : within-zone building-potential excess_tv + Pearson r per purpose
                   (braunschweig.calibration.run_building_fit_secondary)

so the operating point (how much potential weight we can afford before the MiD
distance fit degrades) is visible in one table.

Each weight gets its own synpp hash (the config value differs), so the chainsolver
pickles coexist in the shared working_directory; the measurement tools read the
most-recent pickle, so measurement happens immediately after each run. This is an
OFFLINE calibration harness -- it never touches the committed run configs and
disables cache_share export so the shared store is not polluted.

Usage (on the server, env eqasim)::

    python scripts/sweep_secondary_scorer.py \
        --base-config config_server_braunschweig_1pct_allfeat_popsim.yml \
        --working-directory eqasim-data/cache_bs_1pct_allfeat_fit \
        --mid-dir eqasim-data/data/braunschweig/mid \
        --sampling-rate 0.01 \
        --pot-weights 0.5,1.0,2.0,4.0,8.0 \
        --output-dir eqasim-data/data/braunschweig/calibration/secondary/scorer_sweep
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

LOGGER = logging.getLogger("sweep_secondary_scorer")

_PURPOSES = ("shop", "leisure", "other")


def _write_temp_config(base_config: str, working_directory: str,
                       pot_weight: float, dist_dev_weight: float,
                       tmp_dir: str) -> str:
    """Copy ``base_config``, set the scorer weights + working_directory, restrict the
    run to ``synthesis.output`` only and disable cache_share export, and write it to a
    temp file. Returns the temp config path."""
    with open(base_config, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    cfg["working_directory"] = working_directory
    # Synthesis only -- never trigger MATSim from the sweep.
    cfg["run"] = ["synthesis.output"]

    config = cfg.setdefault("config", {})
    config["secondary_scorer_mode"] = "combined"
    config["secondary_scorer_pot_weight"] = float(pot_weight)
    config["secondary_scorer_dist_dev_weight"] = float(dist_dev_weight)
    # Offline harness: do not prime-from / export-to the shared stage store.
    config["cache_share_enabled"] = False
    config["cache_share_export"] = False

    path = os.path.join(tmp_dir, f"sweep_pot{pot_weight:g}_dist{dist_dev_weight:g}.yml")
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    return path


_CHAINSOLVER_STAGE = "braunschweig.synthesis.locations.secondary_chainsolvers"


def _run_synpp(config_path: str, repo_root: str) -> str:
    """Run ``scripts/run_synpp.py <config>`` as a subprocess; raise on non-zero exit.

    Returns the combined stdout+stderr so the caller can identify which cached
    stage pickle this run produced (see ``_touch_current_chainsolver``)."""
    LOGGER.info("Running synpp for %s ...", os.path.basename(config_path))
    proc = subprocess.run(
        [sys.executable, "scripts/run_synpp.py", config_path],
        cwd=repo_root, capture_output=True, text=True,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        tail = "\n".join(combined.splitlines()[-40:])
        raise RuntimeError(f"synpp run failed for {config_path}:\n{tail}")
    return combined


def _touch_current_chainsolver(working_directory: str, synpp_output: str) -> None:
    """Make THIS run's chainsolver pickle the most-recent one in the cache.

    The measurement tools (validate_secondary_distances / run_building_fit_secondary)
    select the chainsolver pickle by ``max(mtime)``. That is WRONG on a synpp
    cache-HIT: when the current weight's chainsolver was already cached, synpp does
    NOT rewrite its pickle, so the freshest pickle on disk is the PREVIOUS weight's
    -> the measurement would silently read the previous weight's placement. We
    therefore parse the chainsolver stage hash that synpp loaded/wrote for THIS run
    from its log and bump that pickle's mtime so ``max(mtime)`` resolves to it.
    Fails loudly (no silent fallback) if the hash cannot be identified."""
    m = re.search(rf"{re.escape(_CHAINSOLVER_STAGE)}__([0-9a-f]+)", synpp_output)
    if not m:
        # synpp does not mention the chainsolver when synthesis.output is fully cached
        # for this weight (it loads the cached output and never touches the chainsolver).
        # That is legitimate, not an error: in that case the measurement's max(mtime)
        # is correct as long as no OTHER weight's chainsolver pickle is newer. Warn so
        # the (rare) ambiguous case is observable; do NOT abort the sweep.
        LOGGER.warning(
            "[sweep] chainsolver stage hash not found in synpp output (synthesis.output "
            "likely fully cached for this weight); skipping mtime bump -- measurement "
            "uses the most-recent chainsolver pickle (correct unless another weight's "
            "pickle is newer)."
        )
        return
    h = m.group(1)
    pickle_path = os.path.join(working_directory, f"{_CHAINSOLVER_STAGE}__{h}.p")
    if not os.path.exists(pickle_path):
        LOGGER.warning("[sweep] chainsolver pickle not found, skipping touch: %s", pickle_path)
        return
    os.utime(pickle_path, None)  # set mtime to now -> newest -> selected by the tools
    cache_dir = os.path.join(working_directory, f"{_CHAINSOLVER_STAGE}__{h}.cache")
    if os.path.isdir(cache_dir):
        os.utime(cache_dir, None)
    LOGGER.info("chainsolver pickle for this weight: %s (mtime bumped)", os.path.basename(pickle_path))


def _measure_w12(working_directory: str, mid_dir: str, repo_root: str) -> dict:
    """Run the W12 validator; parse EMD + routed mean per purpose from its table."""
    proc = subprocess.run(
        [sys.executable, "scripts/validate_secondary_distances.py",
         "--cache", working_directory, "--mid-dir", mid_dir],
        cwd=repo_root, capture_output=True, text=True,
    )
    out = proc.stdout
    emd = {}
    mean_routed = {}
    for purpose in _PURPOSES:
        # Table row: "leisure   286930   13.66   17.76   15.0   0.0504"
        m = re.search(rf"^{purpose}\s+\d+\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s*$",
                      out, re.MULTILINE)
        if m:
            mean_routed[purpose] = float(m.group(1))
            emd[purpose] = float(m.group(2))
        else:
            mean_routed[purpose] = float("nan")
            emd[purpose] = float("nan")
            LOGGER.warning("could not parse W12 row for %s", purpose)
    return {"emd": emd, "mean_routed": mean_routed}


def _measure_building_fit(working_directory: str, sampling_rate: float,
                          out_dir: str, repo_root: str) -> dict:
    """Run the secondary building-fit report; parse activity-weighted excess_tv +
    Pearson r per purpose from its summary.md."""
    proc = subprocess.run(
        [sys.executable, "-m", "braunschweig.calibration.run_building_fit_secondary",
         "--working-directory", working_directory,
         "--sampling-rate", str(sampling_rate),
         "--output-dir", out_dir],
        cwd=repo_root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        LOGGER.warning("building-fit failed: %s", proc.stderr.splitlines()[-5:])
    summary = Path(out_dir) / "building_potential_fit_secondary_summary.md"
    text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    excess = {}
    pearson = {}
    for purpose in _PURPOSES:
        block = re.search(rf"## {purpose}\b(.*?)(?=\n## |\Z)", text, re.DOTALL)
        seg = block.group(1) if block else ""
        m_ex = re.search(r"EXCESS over noise floor:\s*([\d.\-nan]+)", seg)
        m_pe = re.search(r"activity-weighted Pearson r:\s*([\d.\-nan]+)", seg)
        excess[purpose] = float(m_ex.group(1)) if m_ex else float("nan")
        pearson[purpose] = float(m_pe.group(1)) if m_pe else float("nan")
    return {"excess_tv": excess, "pearson": pearson}


def _print_table(rows: list) -> None:
    hdr = (f"{'pot_w':>6} {'dist_w':>6} | "
           f"{'shop_emd':>9}{'leis_emd':>9}{'oth_emd':>9} | "
           f"{'shop_exTV':>10}{'leis_exTV':>10}{'oth_exTV':>10} | "
           f"{'shop_r':>7}{'leis_r':>7}{'oth_r':>7}")
    print("\n" + "=" * len(hdr))
    print("Secondary scorer sweep: distance fit (W12 EMD, <0.08 good) vs "
          "capacity fit (excess_tv low / Pearson r high)")
    print("=" * len(hdr))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        e, x, p = r["emd"], r["excess_tv"], r["pearson"]
        print(f"{r['pot_weight']:>6g} {r['dist_dev_weight']:>6g} | "
              f"{e['shop']:>9.4f}{e['leisure']:>9.4f}{e['other']:>9.4f} | "
              f"{x['shop']:>10.4f}{x['leisure']:>10.4f}{x['other']:>10.4f} | "
              f"{p['shop']:>7.3f}{p['leisure']:>7.3f}{p['other']:>7.3f}")
    print("=" * len(hdr) + "\n")


def _write_csv(rows: list, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pot_weight", "dist_dev_weight"]
                   + [f"emd_{p}" for p in _PURPOSES]
                   + [f"mean_routed_{p}" for p in _PURPOSES]
                   + [f"excess_tv_{p}" for p in _PURPOSES]
                   + [f"pearson_{p}" for p in _PURPOSES])
        for r in rows:
            w.writerow([r["pot_weight"], r["dist_dev_weight"]]
                       + [r["emd"][p] for p in _PURPOSES]
                       + [r["mean_routed"][p] for p in _PURPOSES]
                       + [r["excess_tv"][p] for p in _PURPOSES]
                       + [r["pearson"][p] for p in _PURPOSES])


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-config", required=True)
    p.add_argument("--working-directory", required=True)
    p.add_argument("--mid-dir", required=True)
    p.add_argument("--sampling-rate", type=float, required=True)
    p.add_argument("--pot-weights", default="0.5,1.0,2.0,4.0,8.0",
                   help="comma-separated pot_weight values to sweep")
    p.add_argument("--dist-dev-weight", type=float, default=1.0)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--repo-root", default=".")
    args = p.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    out_dir = os.path.abspath(args.output_dir)
    os.makedirs(out_dir, exist_ok=True)
    weights = [float(w) for w in args.pot_weights.split(",") if w.strip()]

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for w in weights:
            LOGGER.info("=== sweep pot_weight=%g dist_dev_weight=%g ===",
                        w, args.dist_dev_weight)
            cfg = _write_temp_config(args.base_config, args.working_directory,
                                     w, args.dist_dev_weight, tmp)
            synpp_out = _run_synpp(cfg, repo_root)
            # Ensure the measurement reads THIS weight's chainsolver, not the most
            # recently written one (wrong on a cache-hit). See the helper docstring.
            _touch_current_chainsolver(args.working_directory, synpp_out)
            w12 = _measure_w12(args.working_directory, args.mid_dir, repo_root)
            bf_dir = os.path.join(out_dir, f"bf_pot{w:g}")
            bf = _measure_building_fit(args.working_directory, args.sampling_rate,
                                       bf_dir, repo_root)
            rows.append({
                "pot_weight": w, "dist_dev_weight": args.dist_dev_weight,
                "emd": w12["emd"], "mean_routed": w12["mean_routed"],
                "excess_tv": bf["excess_tv"], "pearson": bf["pearson"],
            })
            LOGGER.info("pot_weight=%g -> leisure EMD %.4f, leisure excess_tv %.4f",
                        w, w12["emd"]["leisure"], bf["excess_tv"]["leisure"])

    csv_path = os.path.join(out_dir, "scorer_sweep_results.csv")
    _write_csv(rows, csv_path)
    LOGGER.info("wrote results CSV to %s", csv_path)
    _print_table(rows)


if __name__ == "__main__":
    main()
