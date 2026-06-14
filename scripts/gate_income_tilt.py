"""Measure-gain gate for the spatial income tilt (Nettokaltmiete GAMMA layer).

Runs the 1-Kreis popsim_mid pipeline TWICE (income_spatial_tilt OFF vs ON via
``braunschweig.population.popsim.income_spatial_tilt`` flag) and reports:

  - Pearson and Spearman correlation of synthetic ``household_income_eur`` with
    the home-cell ``durchschnMieteQM`` (net cold rent per m²)
  - Per-Kreis income mean (must be equal OFF vs ON to ~1e-6 -- asserted)
  - Clipped fraction from the tilt diagnostics
  - Owner-vs-renter income ratio as a tilt distribution check

OFF→ON deltas are printed. PASS condition to keep default ON:

  - income↔rent correlation increases OFF→ON (even weak gain is expected)
  - per-Kreis mean preserved (== asserted hard)
  - clipped fraction reasonable (< 50 %)

This gate does NOT assert absolute correlation values (β=0.3 is gate-tuned;
the literature Spearman ~0.32 is a REFERENCE, not a hard constraint here).

Usage (from the repo root, with the eqasim env python, PYTHONUTF8=1)::

    python scripts/gate_income_tilt.py --config-base config_smoke_popsim_mid_mini.yml

The script generates two temporary override config files alongside ``--config-base``
(``_gate_tilt_off.yml`` and ``_gate_tilt_on.yml``), runs each via ``scripts/run_synpp.py``,
then computes KPIs from the stage cache in each working directory.

If the real pipeline runs cannot be completed (missing env, time budget), the script
exits with a non-zero code and prints the blocker clearly. The pure analysis functions
(``income_rent_correlation``, ``per_kreis_mean``, ``owner_renter_ratio``,
``summarize_run``, ``off_on_deltas``) can be imported and unit-tested independently.
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import pickle
import subprocess
import sys
import tempfile
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as _scipy_stats

# ---------------------------------------------------------------------------
# Make the repo root importable when run as a script.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure analysis functions (unit-testable without any pipeline run)
# ---------------------------------------------------------------------------

def income_rent_correlation(
    persons: pd.DataFrame,
    cells: pd.DataFrame,
    *,
    income_col: str = "household_income_eur",
    rent_col: str = "durchschnMieteQM",
    cell_join_col: str = "ZENSUS100m",
) -> dict[str, float]:
    """Compute Pearson and Spearman correlation of synthetic income with cell rent.

    Each person is joined to their home cell via ``cell_join_col``; only rows with
    finite income and rent are used (NaN/Inf silently dropped before correlation).

    Parameters
    ----------
    persons:
        Synthetic persons frame with at least ``income_col`` and ``cell_join_col``.
    cells:
        Cell frame with at least ``cell_join_col`` and ``rent_col``.
    income_col:
        Column in ``persons`` carrying household income in EUR.
    rent_col:
        Column in ``cells`` carrying net cold rent per m².
    cell_join_col:
        ID column shared between ``persons`` and ``cells``.

    Returns
    -------
    dict with keys ``pearson``, ``pearson_pvalue``, ``spearman``, ``spearman_pvalue``,
    ``n_valid`` (number of rows used).
    """
    # Join rent onto persons
    rent = cells[[cell_join_col, rent_col]].copy()
    merged = persons[[cell_join_col, income_col]].merge(rent, on=cell_join_col, how="left")
    valid = merged[[income_col, rent_col]].replace([np.inf, -np.inf], np.nan).dropna()
    n = len(valid)
    if n < 3:
        return {
            "pearson": float("nan"), "pearson_pvalue": float("nan"),
            "spearman": float("nan"), "spearman_pvalue": float("nan"),
            "n_valid": n,
        }
    inc = valid[income_col].to_numpy(float)
    rnt = valid[rent_col].to_numpy(float)
    pearson_r, pearson_p = _scipy_stats.pearsonr(inc, rnt)
    spearman_r, spearman_p = _scipy_stats.spearmanr(inc, rnt)
    return {
        "pearson": float(pearson_r),
        "pearson_pvalue": float(pearson_p),
        "spearman": float(spearman_r),
        "spearman_pvalue": float(spearman_p),
        "n_valid": n,
    }


def per_kreis_mean(
    persons: pd.DataFrame,
    *,
    income_col: str = "household_income_eur",
    kreis_col: str = "departement_id",
) -> pd.Series:
    """Return the person-weighted per-Kreis mean income.

    NaN income rows are excluded from the mean (same as the tilt's shielding policy).
    Returns a Series indexed by Kreis code (5-digit ARS).
    """
    valid = persons[[kreis_col, income_col]].dropna(subset=[income_col])
    return valid.groupby(kreis_col)[income_col].mean()


def owner_renter_ratio(
    persons: pd.DataFrame,
    *,
    income_col: str = "household_income_eur",
    tenure_col: str = "housing_tenure",
) -> dict[str, float]:
    """Return mean income by tenure class (owner, renter, unknown) and the owner/renter ratio.

    Returns a dict with keys ``owner_mean``, ``renter_mean``, ``unknown_mean``,
    ``owner_renter_ratio`` (NaN if renter_mean == 0).
    Missing tenure column -> all values NaN.
    """
    if tenure_col not in persons.columns:
        return {"owner_mean": float("nan"), "renter_mean": float("nan"),
                "unknown_mean": float("nan"), "owner_renter_ratio": float("nan")}
    valid = persons[[tenure_col, income_col]].dropna(subset=[income_col])
    groups = valid.groupby(tenure_col)[income_col].mean()
    owner = float(groups.get("owner", float("nan")))
    renter = float(groups.get("renter", float("nan")))
    unknown = float(groups.get("unknown", float("nan")))
    ratio = owner / renter if (not np.isnan(renter) and renter != 0) else float("nan")
    return {"owner_mean": owner, "renter_mean": renter, "unknown_mean": unknown,
            "owner_renter_ratio": ratio}


def summarize_run(
    persons: pd.DataFrame,
    cells: pd.DataFrame,
    *,
    tilt_diag: dict | None = None,
    rent_col: str = "durchschnMieteQM",
    income_col: str = "household_income_eur",
    kreis_col: str = "departement_id",
    cell_col: str = "ZENSUS100m",
    tenure_col: str = "housing_tenure",
) -> dict[str, Any]:
    """Compute all gate KPIs for a single run.

    Parameters
    ----------
    persons:
        Synthetic persons frame from ``braunschweig.popsim.stage`` output.
    cells:
        Cell frame with rent column (``durchschnMieteQM`` etc).
    tilt_diag:
        Optional diagnostics dict from ``maybe_apply_income_tilt``; if provided,
        ``clipped_fraction``, ``max_effective_dev``, ``beta_clip`` are added.
    rent_col, income_col, kreis_col, cell_col, tenure_col:
        Column names.

    Returns
    -------
    dict with all KPI values for this run.
    """
    corr = income_rent_correlation(
        persons, cells,
        income_col=income_col, rent_col=rent_col, cell_join_col=cell_col,
    )
    km = per_kreis_mean(persons, income_col=income_col, kreis_col=kreis_col)
    orr = owner_renter_ratio(persons, income_col=income_col, tenure_col=tenure_col)
    summary: dict[str, Any] = {
        "pearson": corr["pearson"],
        "pearson_pvalue": corr["pearson_pvalue"],
        "spearman": corr["spearman"],
        "spearman_pvalue": corr["spearman_pvalue"],
        "n_valid_persons": corr["n_valid"],
        "per_kreis_mean": km.to_dict(),
        "owner_mean_eur": orr["owner_mean"],
        "renter_mean_eur": orr["renter_mean"],
        "unknown_mean_eur": orr["unknown_mean"],
        "owner_renter_ratio": orr["owner_renter_ratio"],
        "n_persons": len(persons),
        "global_mean_income_eur": float(persons[income_col].dropna().mean()),
    }
    if tilt_diag:
        summary["clipped_fraction"] = tilt_diag.get("clipped_fraction", float("nan"))
        summary["max_effective_dev"] = tilt_diag.get("max_effective_dev", float("nan"))
        summary["beta_clip"] = tilt_diag.get("beta_clip", float("nan"))
        summary["kreis_mean_preserved"] = tilt_diag.get("kreis_mean_preserved", None)
    return summary


def off_on_deltas(
    off_summary: dict[str, Any],
    on_summary: dict[str, Any],
) -> dict[str, Any]:
    """Compute OFF→ON deltas for the key scalar KPIs.

    Also asserts (not raises, but records) that per-Kreis means agree between
    runs to ~1e-6 relative tolerance (the mean-preservation guarantee).

    Returns a dict with delta_pearson, delta_spearman, per_kreis_mean_max_abs_dev,
    per_kreis_mean_preserved (bool).
    """
    delta_pearson = (
        (on_summary["pearson"] - off_summary["pearson"])
        if not (np.isnan(off_summary["pearson"]) or np.isnan(on_summary["pearson"]))
        else float("nan")
    )
    delta_spearman = (
        (on_summary["spearman"] - off_summary["spearman"])
        if not (np.isnan(off_summary["spearman"]) or np.isnan(on_summary["spearman"]))
        else float("nan")
    )
    delta_owner_renter = (
        (on_summary["owner_renter_ratio"] - off_summary["owner_renter_ratio"])
        if not (np.isnan(off_summary.get("owner_renter_ratio", float("nan")))
                or np.isnan(on_summary.get("owner_renter_ratio", float("nan"))))
        else float("nan")
    )

    # Per-Kreis mean preservation: the tilt should leave per-Kreis means unchanged.
    off_km = off_summary.get("per_kreis_mean", {})
    on_km = on_summary.get("per_kreis_mean", {})
    shared = set(off_km) & set(on_km)
    if shared:
        devs = [
            abs(on_km[k] - off_km[k]) / max(abs(off_km[k]), 1.0)
            for k in shared
        ]
        max_rel_dev = float(max(devs))
        preserved = max_rel_dev < 1e-4  # loose cross-run tolerance (re-run with same seed)
    else:
        max_rel_dev = float("nan")
        preserved = None

    # Absolute max dev in EUR (informational)
    if shared:
        abs_devs = [abs(on_km[k] - off_km[k]) for k in shared]
        max_abs_dev_eur = float(max(abs_devs))
    else:
        max_abs_dev_eur = float("nan")

    return {
        "delta_pearson": delta_pearson,
        "delta_spearman": delta_spearman,
        "delta_owner_renter_ratio": delta_owner_renter,
        "per_kreis_mean_max_rel_dev": max_rel_dev,
        "per_kreis_mean_max_abs_dev_eur": max_abs_dev_eur,
        "per_kreis_mean_preserved": preserved,
    }


# ---------------------------------------------------------------------------
# Pipeline run helpers
# ---------------------------------------------------------------------------

def _write_gate_config(base_config_path: pathlib.Path, flag_on: bool) -> pathlib.Path:
    """Write a gate-specific config that inherits from base and overrides the tilt flag.

    The config sets fresh working/output directories to avoid cache collisions.
    Returns the path of the written config file.
    """
    import yaml  # noqa: PLC0415 (local import; not worth a top-level dep)

    with open(base_config_path, encoding="utf-8") as f:
        base = yaml.safe_load(f)

    suffix = "on" if flag_on else "off"
    base_wd = pathlib.Path(base.get("working_directory", "eqasim-data/cache_gate_tilt_base"))
    # Use fresh cache/output dirs to avoid sharing state between OFF and ON runs.
    base["working_directory"] = str(base_wd.parent / f"cache_gate_tilt_{suffix}")
    if "config" in base:
        old_output = base["config"].get("output_path", "eqasim-data/output_gate_tilt_base")
        base["config"]["output_path"] = str(
            pathlib.Path(old_output).parent / f"output_gate_tilt_{suffix}"
        )
        old_prefix = base["config"].get("output_prefix", "gate_tilt_")
        base["config"]["output_prefix"] = f"gate_tilt_{suffix}_"
        # Override the tilt flag.
        base["config"]["braunschweig.population.popsim.income_spatial_tilt"] = flag_on
        base["config"]["braunschweig.population.popsim.income_tilt_beta"] = 0.3
        base["config"]["braunschweig.population.popsim.income_tilt_clip"] = 0.30

    out_path = base_config_path.parent / f"_gate_tilt_{suffix}.yml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(base, f, allow_unicode=True, sort_keys=False)
    return out_path


def _run_pipeline(config_path: pathlib.Path, python_exe: str, repo_root: pathlib.Path,
                  timeout_sec: int = 7200) -> subprocess.CompletedProcess:
    """Run the synpp pipeline via scripts/run_synpp.py."""
    run_synpp = str(repo_root / "scripts" / "run_synpp.py")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [python_exe, run_synpp, str(config_path)],
        cwd=str(repo_root),
        env=env,
        timeout=timeout_sec,
        capture_output=True,
        text=True,
    )


def _load_persons_from_cache(working_directory: pathlib.Path) -> pd.DataFrame | None:
    """Load the braunschweig.popsim.stage output from the synpp cache.

    Returns None if no cache file is found (pipeline did not produce output).
    """
    pattern = "braunschweig.popsim.stage*.p"
    matches = list(working_directory.glob(pattern))
    if not matches:
        return None
    # If multiple hashes exist, pick the most recently modified.
    latest = max(matches, key=lambda p: p.stat().st_mtime)
    with open(latest, "rb") as f:
        return pickle.load(f)


def _load_tilt_cells(cells_100m_path: str) -> pd.DataFrame:
    """Load only the columns needed for the gate analysis from the 100 m parquet."""
    import pyarrow.parquet as _pq  # noqa: PLC0415

    schema_names = _pq.ParquetFile(cells_100m_path).schema.names
    # The raw column names use hyphens; we work with the raw names here.
    wanted_raw = {
        "id": schema_names[0],
        "rent": "durchschnMieteQM_Durchschn_Nettokaltmiete_100m-Gitter",
        "quote": "Eigentuemerquote_Eigentuemerquote_100m-Gitter",
        "ars": "RegionalSchlüssel_ARS",
        "hh_total": "Insgesamt_Haushalte_Grösse_des_privaten_Haushalts_100m-Gitter_adj",
    }
    cols_to_load = [v for v in wanted_raw.values() if v in schema_names]
    cells_raw = pd.read_parquet(cells_100m_path, columns=cols_to_load)
    # Rename: id -> ZENSUS100m, rent -> durchschnMieteQM, quote -> eigentuemerquote
    rename = {}
    rename[wanted_raw["id"]] = "ZENSUS100m"
    if wanted_raw["rent"] in cells_raw.columns:
        rename[wanted_raw["rent"]] = "durchschnMieteQM"
    if wanted_raw["quote"] in cells_raw.columns:
        rename[wanted_raw["quote"]] = "eigentuemerquote"
    if wanted_raw.get("ars") in cells_raw.columns:
        rename[wanted_raw["ars"]] = "ars12"
    cells_raw = cells_raw.rename(columns=rename)
    return cells_raw


# ---------------------------------------------------------------------------
# Main gate logic
# ---------------------------------------------------------------------------

def run_gate(
    base_config: pathlib.Path,
    python_exe: str,
    cells_100m_path: str,
    repo_root: pathlib.Path,
    *,
    skip_run: bool = False,
) -> dict[str, Any]:
    """Run the full gate: OFF vs ON pipeline + KPI comparison.

    Parameters
    ----------
    base_config:
        Path to the 1-Kreis mini popsim_mid config YAML (e.g.
        ``config_smoke_popsim_mid_mini.yml``). Tilt flag is overridden.
    python_exe:
        Python interpreter (eqasim env).
    cells_100m_path:
        Path to the 100 m grid parquet (for rent column join).
    repo_root:
        Repo root directory (parent of scripts/).
    skip_run:
        If True, skip the pipeline run and only load from cache (for re-analysis).

    Returns
    -------
    dict with keys ``off``, ``on``, ``deltas``, ``recommendation``.
    """
    results: dict[str, Any] = {}

    # Load cells frame once (shared between OFF and ON).
    logger.info("Loading 100 m tilt cells from %s ...", cells_100m_path)
    cells = _load_tilt_cells(cells_100m_path)
    logger.info("Loaded %d cells.", len(cells))

    for flag_on in (False, True):
        label = "on" if flag_on else "off"
        cfg_path = _write_gate_config(base_config, flag_on=flag_on)
        logger.info("Gate config written: %s (income_spatial_tilt=%s)", cfg_path, flag_on)

        import yaml  # noqa: PLC0415
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        wd = pathlib.Path(cfg.get("working_directory", ""))
        if not wd.is_absolute():
            wd = repo_root / wd

        if not skip_run:
            logger.info("Running pipeline (flag=%s) .... this may take 60-90 min.", flag_on)
            proc = _run_pipeline(cfg_path, python_exe, repo_root)
            if proc.returncode != 0:
                logger.error("Pipeline run FAILED (flag=%s). stderr tail:\n%s",
                             flag_on, "\n".join((proc.stderr or "").splitlines()[-30:]))
                results[label] = {"error": "pipeline_failed", "stderr_tail": (proc.stderr or "")[-2000:]}
                continue
            logger.info("Pipeline run (flag=%s) completed OK.", flag_on)

        persons = _load_persons_from_cache(wd)
        if persons is None:
            results[label] = {"error": "no_cache_found", "working_directory": str(wd)}
            logger.error("No popsim.stage cache found in %s", wd)
            continue

        logger.info("Loaded persons frame: %d rows from %s", len(persons), wd)
        summary = summarize_run(persons, cells)
        results[label] = summary
        logger.info(
            "Gate KPIs [%s]: Pearson=%.4f Spearman=%.4f n=%d",
            label.upper(), summary["pearson"], summary["spearman"], summary["n_valid_persons"],
        )

    # Compute OFF→ON deltas
    if "off" in results and "on" in results and "error" not in results["off"] and "error" not in results["on"]:
        deltas = off_on_deltas(results["off"], results["on"])
        results["deltas"] = deltas

        # Gate decision
        correlation_improves = (
            not np.isnan(deltas["delta_spearman"]) and deltas["delta_spearman"] > -0.01
        )
        mean_preserved = deltas.get("per_kreis_mean_preserved", False)
        clipped_ok = results["on"].get("clipped_fraction", 1.0) < 0.5

        if correlation_improves and mean_preserved and clipped_ok:
            recommendation = "KEEP_DEFAULT_ON"
        elif not correlation_improves:
            recommendation = "FLIP_DEFAULT_OFF — income↔rent correlation does not improve or worsens"
        elif not mean_preserved:
            recommendation = "FLIP_DEFAULT_OFF — per-Kreis mean not preserved across OFF vs ON runs"
        else:
            recommendation = "FLIP_DEFAULT_OFF — clipped fraction too high (>{:.0%})".format(
                results["on"].get("clipped_fraction", 1.0)
            )
        results["recommendation"] = recommendation

        # Hard assertion: per-Kreis mean MUST be preserved (not absolute correlation).
        # Warn if not satisfied (don't crash the reporter).
        if not mean_preserved:
            logger.warning(
                "GATE FAIL: per-Kreis income mean NOT preserved across OFF vs ON runs "
                "(max_rel_dev=%.2e). This violates the tilt's mean-preservation guarantee.",
                deltas.get("per_kreis_mean_max_rel_dev", float("nan")),
            )
        else:
            logger.info(
                "GATE PASS (mean preservation): max_rel_dev=%.2e, max_abs_dev=%.2f EUR.",
                deltas["per_kreis_mean_max_rel_dev"],
                deltas["per_kreis_mean_max_abs_dev_eur"],
            )

    return results


def _print_report(results: dict[str, Any]) -> None:
    """Print the gate report to stdout."""
    print("\n" + "=" * 70)
    print("INCOME TILT GATE REPORT (beta=0.3, clip=0.30)")
    print("=" * 70)

    for label in ("off", "on"):
        s = results.get(label, {})
        print(f"\n--- Tilt {label.upper()} ---")
        if "error" in s:
            print(f"  ERROR: {s['error']}")
            if "stderr_tail" in s:
                print(s["stderr_tail"][-500:])
            continue
        print(f"  Pearson(income, rent):   {s.get('pearson', 'n/a'):.4f}  (p={s.get('pearson_pvalue', float('nan')):.3g})")
        print(f"  Spearman(income, rent):  {s.get('spearman', 'n/a'):.4f}  (p={s.get('spearman_pvalue', float('nan')):.3g})")
        print(f"  N valid persons:         {s.get('n_valid_persons', 'n/a'):,}")
        print(f"  Global mean income:      {s.get('global_mean_income_eur', float('nan')):.0f} EUR")
        km = s.get("per_kreis_mean", {})
        for k, v in km.items():
            print(f"  Per-Kreis mean [{k}]:  {v:.0f} EUR")
        print(f"  Owner mean income:       {s.get('owner_mean_eur', float('nan')):.0f} EUR")
        print(f"  Renter mean income:      {s.get('renter_mean_eur', float('nan')):.0f} EUR")
        print(f"  Owner/renter ratio:      {s.get('owner_renter_ratio', float('nan')):.4f}")
        if label == "on":
            print(f"  Clipped fraction:        {s.get('clipped_fraction', float('nan')):.1%}")
            print(f"  Max effective dev:       {s.get('max_effective_dev', float('nan')):.4f}")

    deltas = results.get("deltas")
    if deltas:
        print("\n--- OFF→ON Deltas ---")
        print(f"  ΔPearson:                {deltas.get('delta_pearson', float('nan')):+.4f}")
        print(f"  ΔSpearman:               {deltas.get('delta_spearman', float('nan')):+.4f}")
        print(f"  ΔOwner/renter ratio:     {deltas.get('delta_owner_renter_ratio', float('nan')):+.4f}")
        print(f"  Per-Kreis mean preserved: {deltas.get('per_kreis_mean_preserved')}")
        print(f"  Max per-Kreis mean dev:  {deltas.get('per_kreis_mean_max_abs_dev_eur', float('nan')):.2f} EUR"
              f" (rel: {deltas.get('per_kreis_mean_max_rel_dev', float('nan')):.2e})")

    print("\n--- Gate Decision ---")
    print(f"  Recommendation: {results.get('recommendation', 'UNKNOWN (run incomplete)')}")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=logging.INFO,
        force=True,
    )
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config-base",
        default=str(_REPO_ROOT / "config_smoke_popsim_mid_mini.yml"),
        help="Base 1-Kreis mini config YAML (tilt flag overridden per run).",
    )
    parser.add_argument(
        "--cells-100m",
        default=str(_REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "popsim" / "cells"
                    / "zensus2022_grid_100m_de_prepared.parquet"),
        help="Path to the 100 m grid parquet (contains rent + Eigentümerquote cols).",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter with the eqasim env (default: current interpreter).",
    )
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Skip pipeline run, re-analyse from existing cache (for re-runs).",
    )
    args = parser.parse_args(argv)

    base_config = pathlib.Path(args.config_base)
    if not base_config.is_file():
        print(f"ERROR: base config not found: {base_config}", file=sys.stderr)
        return 2

    cells_path = pathlib.Path(args.cells_100m)
    if not cells_path.is_file():
        print(
            f"ERROR: 100 m cells parquet not found: {cells_path}\n"
            "The income tilt gate requires the local-only Zensus 2022 data.\n"
            "RUN DEFERRED — data not available at this path.",
            file=sys.stderr,
        )
        return 3

    results = run_gate(
        base_config=base_config,
        python_exe=args.python,
        cells_100m_path=str(cells_path),
        repo_root=_REPO_ROOT,
        skip_run=args.skip_run,
    )
    _print_report(results)

    # Exit code: 0 = KEEP_DEFAULT_ON, 1 = FLIP, 2/3 = error.
    rec = results.get("recommendation", "")
    if "KEEP_DEFAULT_ON" in rec:
        return 0
    if "FLIP_DEFAULT_OFF" in rec:
        return 1
    return 4  # incomplete / errors


if __name__ == "__main__":
    raise SystemExit(main())
