"""Monte-Carlo sampling-noise bands driver (issue #126).

Runs the synthesis N times with varied random_seed (each draw its own
working_directory + derived YAML, executed through scripts/run_synpp.py so
cache-share priming and the #125 provenance record apply), harvests the
validation metrics per draw, and aggregates empirical mean/q05/q95 bands.
Failed draws are logged and skipped; fewer than --min-draws successes abort
WITHOUT writing bands (no silently thin bands).

A band quantifies how much a validation metric moves under pure re-seeding at
a FIXED sampling rate. It is a triage signal ("this deviation is
indistinguishable from sampling noise"), never a significance test and never
a calibration input (see braunschweig/analysis/noise_bands.py docstring).
"""
from __future__ import annotations

import argparse
import copy
import datetime
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from braunschweig.analysis import noise_bands as nb          # noqa: E402
from braunschweig.provenance import git_commit               # noqa: E402

logger = logging.getLogger("braunschweig.noise_bands")

# Default base config for the sweep. The task design referred to
# "config_local_braunschweig_1pct_allfeat_full.yml" (a 1% all-features config
# with the full synthesis+matsim+analysis `run:` chain) -- that exact file only
# ever existed on the unmerged smoke-run branch `run/smoke-1pct-allfeatures`
# (see docs/runs/2026-06-22_1pct_allfeat_full_smoke_findings.md) and is absent
# from this branch's history, so it cannot be pinned as the default here.
# config_server_braunschweig_1pct_allfeat_popsim.yml is the nearest real,
# committed equivalent (1% sampling rate, all population-synthesis features
# on, the same synthesis.output -> matsim.output -> analysis `run:` shape) and
# is used instead; pass --config to point at a different base config.
DEFAULT_CONFIG = "config_server_braunschweig_1pct_allfeat_popsim.yml"

# Synthesis-only stage list for the Monte-Carlo sweep, pinned by inspecting the
# `run:` section of DEFAULT_CONFIG (config_server_braunschweig_1pct_allfeat_popsim.yml):
#
#   run:
#     - synthesis.output                        # KEPT: pure synthesis; produces the
#                                                #   person/household/trips CSVs that
#                                                #   harvest_draw_metrics needs.
#     - matsim.output                            # DROPPED: MATSim-bound. A noise draw
#                                                #   is synthesis-level only (see the
#                                                #   module docstring above) -- running
#                                                #   MATSim per draw would be both
#                                                #   unnecessary and far too slow for a
#                                                #   ~20-draw sweep.
#     - braunschweig.analysis.cordon_validation  # DROPPED: validates cross-cordon gate
#                                                #   volumes; not a metric harvest_draw_
#                                                #   metrics reads.
#     - braunschweig.analysis.simwrapper_export  # DROPPED: MATSim-bound whenever
#                                                #   config.simwrapper_include_matsim is
#                                                #   true (its `configure()` stages
#                                                #   matsim.simulation.run in that case) --
#                                                #   true in DEFAULT_CONFIG.
#     - braunschweig.analysis.analysis_suite     # DROPPED: its `configure()` gates
#                                                #   matsim.simulation.run on the SAME
#                                                #   config.simwrapper_include_matsim flag
#                                                #   as simwrapper_export above, so
#                                                #   including it here would silently pull
#                                                #   a full MATSim run into every draw
#                                                #   unless that flag were also overridden.
#                                                #   The population-validation metrics it
#                                                #   would otherwise produce are generated
#                                                #   directly in run_draw() below instead,
#                                                #   by calling
#                                                #   run_population_validation.run() --
#                                                #   mirroring how harvest_draw_metrics
#                                                #   already calls run_mid_validation.run()
#                                                #   directly rather than via a synpp
#                                                #   stage. This keeps the sweep strictly
#                                                #   synthesis-only regardless of what the
#                                                #   base config's other flags are set to.
DRAW_RUN_STAGES = ["synthesis.output"]


def build_draw_config(doc: dict, *, seed: int, workdir: str, run_stages: list) -> dict:
    """Derive one Monte-Carlo draw's synpp config from the base ``doc``.

    Deep-copies ``doc`` (so repeated draws from the same base never mutate it
    or each other) and overrides:

    - ``working_directory`` -> ``workdir`` (each draw gets its own synpp cache).
    - ``run`` -> ``run_stages`` (the synthesis-only stage list for the sweep).
    - ``config.random_seed`` -> ``seed``.
    - ``config.output_path`` -> ``"<workdir>/output"``. Every draw needs its OWN
      eqasim output directory (never the base config's shared one), so
      concurrent/sequential draws cannot clobber each other's persons/
      households/trips CSVs. Nesting it under ``workdir`` also means deleting
      the draw's working directory (the default post-harvest cleanup; see
      ``run_draw``) removes both the synpp cache AND the eqasim output in one
      ``shutil.rmtree``. The directory itself is created at draw time by
      ``run_draw`` (this function stays pure / side-effect-free), because
      ``synthesis.output``'s ``validate()`` fails early if it does not already
      exist (see docs/runs/2026-06-22_1pct_allfeat_full_smoke_findings.md BUG-1).

    Does not otherwise touch ``config`` (e.g. ``sampling_rate``, feature
    flags) -- every draw of one sweep must share every parameter except the
    seed, or the resulting bands would not isolate pure sampling noise.
    """
    out = copy.deepcopy(doc)
    out["working_directory"] = workdir
    out["run"] = list(run_stages)
    config = out.setdefault("config", {})
    config["random_seed"] = int(seed)
    # Path(workdir).as_posix() normalises any OS-native separators picked up via
    # str(workdir_root / f"seed_{seed}") on Windows back to the forward-slash
    # convention every other path in these YAML configs uses.
    config["output_path"] = f"{Path(workdir).as_posix()}/output"
    return out


def _draw_workdir(workdir_root: Path, seed: int) -> Path:
    return workdir_root / f"seed_{seed}"


def _cleanup_draw_workdir(workdir: Path, workdir_root: Path) -> None:
    """Delete a draw's working/output directory after a successful harvest.

    Refuses (WARN, no-op) unless ``workdir`` resolves to a path STRICTLY
    UNDER ``workdir_root`` -- i.e. ``workdir`` must differ from ``workdir_root``
    AND ``workdir_root`` must be one of its parents. Both conditions are
    required: without the equality check, calling this with ``workdir ==
    workdir_root`` (e.g. a caller bug that resolves a draw's workdir to the
    root itself) would fall through and recursively delete the shared
    ``--workdir-root`` -- along with every other draw's not-yet-cleaned-up
    output -- instead of refusing. Never raises: a failed cleanup must not
    abort an otherwise-successful sweep, but any deletion failure is logged
    as a WARNING naming the path so it is never silent.
    """
    resolved_workdir = workdir.resolve()
    resolved_root = workdir_root.resolve()
    is_strict_descendant = (
        resolved_workdir != resolved_root and resolved_root in resolved_workdir.parents
    )
    if not is_strict_descendant:
        logger.warning(
            "[noise_bands] refusing to delete %s: not strictly under --workdir-root %s.",
            resolved_workdir, resolved_root)
        return
    try:
        shutil.rmtree(resolved_workdir)
    except OSError as exc:
        logger.warning(
            "[noise_bands] failed to delete %s during draw cleanup: %s",
            resolved_workdir, exc)


def run_draw(base_doc: dict, seed: int, workdir_root: Path) -> pd.DataFrame | None:
    """Run one Monte-Carlo draw end-to-end and return its harvested metrics.

    Returns ``None`` (after a WARN log) when the synpp subprocess fails (rc !=
    0) or when the metric harvest raises -- the failure-policy contract with
    ``main()``: a broken draw is skipped, never allowed to abort the sweep.
    """
    workdir = _draw_workdir(workdir_root, seed)
    workdir.mkdir(parents=True, exist_ok=True)
    draw_yaml = workdir_root / f"config_seed_{seed}.yml"
    draw_doc = build_draw_config(base_doc, seed=seed, workdir=str(workdir),
                                 run_stages=DRAW_RUN_STAGES)
    draw_yaml.write_text(yaml.safe_dump(draw_doc), encoding="utf-8")

    output_dir = Path(draw_doc["config"]["output_path"])
    # synthesis.output.validate() fails early ("Output directory must exist")
    # if this is absent -- create it before invoking synpp (see BUG-1 in
    # docs/runs/2026-06-22_1pct_allfeat_full_smoke_findings.md).
    output_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "run_synpp.py"), str(draw_yaml)],
        cwd=_REPO_ROOT,
    )
    if result.returncode != 0:
        logger.warning("[noise_bands] draw seed=%d FAILED (rc=%d) -- skipped.",
                       seed, result.returncode)
        return None

    try:
        # DRAW_RUN_STAGES deliberately excludes braunschweig.analysis.analysis_suite
        # (see its module-level comment above), so the quality_summary.csv that
        # harvest_draw_metrics reads is not produced by the synpp run itself.
        # Generate it directly here -- the same "call the validation module
        # directly" pattern noise_bands._harvest_mid_validation already uses for
        # run_mid_validation.
        from braunschweig.analysis.population_validation import (
            run_population_validation as RPV,
        )
        RPV.run(RPV._parse_args(["--run-output-dir", str(output_dir)]))
        return nb.harvest_draw_metrics(str(output_dir), draw_seed=seed)
    except Exception as exc:  # noqa: BLE001 - one broken draw must not abort the sweep
        logger.warning("[noise_bands] draw seed=%d harvest FAILED (%s) -- skipped.",
                       seed, exc)
        return None


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                    help=f"Base synpp YAML config to derive draws from (default: {DEFAULT_CONFIG}).")
    ap.add_argument("--n-draws", type=int, default=20,
                    help="Number of Monte-Carlo re-seeded draws to run (default: 20).")
    ap.add_argument("--min-draws", type=int, default=15,
                    help="Minimum successful draws required to write a band file; "
                         "fewer aborts the sweep without writing one (default: 15).")
    ap.add_argument("--base-seed", type=int, default=None,
                    help="First draw seed; draw i uses base_seed + i (default: the "
                         "base config's config.random_seed).")
    ap.add_argument("--workdir-root", default=None,
                    help="Root directory for per-draw working/output dirs, the "
                         "per-draw metric CSVs and the aggregated band CSV "
                         "(default: '<working_directory>_noise').")
    ap.add_argument("--keep-draw-outputs", action="store_true", default=False,
                    help="Keep each draw's working/output directory after a "
                         "successful harvest instead of deleting it (default: delete).")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = _REPO_ROOT / config_path
    if not config_path.is_file():
        print(f"[noise_bands] config not found: {config_path}", file=sys.stderr)
        return 1
    with open(config_path, encoding="utf-8") as f:
        base_doc = yaml.safe_load(f) or {}

    base_config = base_doc.get("config", {}) or {}
    sampling_rate = base_config.get("sampling_rate")
    if sampling_rate is None:
        print(f"[noise_bands] {config_path} has no config.sampling_rate; cannot "
              "stamp band provenance.", file=sys.stderr)
        return 1

    if args.base_seed is not None:
        base_seed = args.base_seed
    else:
        base_random_seed = base_config.get("random_seed")
        if base_random_seed is None:
            print(f"[noise_bands] {config_path} has no config.random_seed; pass "
                  "--base-seed explicitly.", file=sys.stderr)
            return 1
        base_seed = int(base_random_seed)

    if args.workdir_root:
        workdir_root = Path(args.workdir_root)
    else:
        base_working_directory = base_doc.get("working_directory")
        if not base_working_directory:
            print(f"[noise_bands] {config_path} has no working_directory; pass "
                  "--workdir-root explicitly.", file=sys.stderr)
            return 1
        workdir_root = Path(f"{base_working_directory}_noise")
    workdir_root.mkdir(parents=True, exist_ok=True)

    from braunschweig.logging_setup import setup_logging
    setup_logging(level="INFO")

    logger.info(
        "[noise_bands] starting sweep: config=%s n_draws=%d min_draws=%d "
        "base_seed=%d workdir_root=%s keep_draw_outputs=%s",
        config_path, args.n_draws, args.min_draws, base_seed, workdir_root,
        args.keep_draw_outputs,
    )

    frames: list[pd.DataFrame] = []
    first_keyset: frozenset | None = None
    for i in range(args.n_draws):
        seed = base_seed + i
        frame = run_draw(base_doc, seed, workdir_root)
        if frame is None:
            continue
        keyset = nb.metric_keyset(frame)
        if first_keyset is None:
            first_keyset = keyset
        elif keyset != first_keyset:
            # Fail fast PER DRAW: catching this here (right after the harvest,
            # before the workdir is deleted) means the draw that produced the
            # inconsistent keyset is still on disk for inspection, and the
            # sweep can keep going with min-draws still governing. Without
            # this check the mismatch would only surface once every workdir
            # had already been cleaned up and aggregate_draw_metrics raised
            # its own ValueError at the very end -- by which point there is
            # nothing left to inspect. aggregate_draw_metrics keeps its own
            # check as the final backstop in case a caller invokes it directly.
            symmetric_difference = sorted(keyset ^ first_keyset, key=str)
            logger.error(
                "[noise_bands] draw seed=%d metric/group keyset differs from the "
                "first successful draw's by %d entries %s -- treating this draw "
                "as failed (skipped).",
                seed, len(symmetric_difference), symmetric_difference)
            continue
        draw_csv = workdir_root / f"draw_metrics_seed_{seed}.csv"
        frame.to_csv(draw_csv, index=False)
        frames.append(frame)
        if not args.keep_draw_outputs:
            _cleanup_draw_workdir(_draw_workdir(workdir_root, seed), workdir_root)

    n_success = len(frames)
    logger.info("[noise_bands] %d/%d draws succeeded.", n_success, args.n_draws)

    if n_success < args.min_draws:
        logger.error(
            "[noise_bands] only %d/%d draws succeeded (< --min-draws=%d); aborting "
            "WITHOUT writing a band file -- a thin sweep would silently "
            "under-represent sampling noise.", n_success, args.n_draws, args.min_draws)
        return 1

    bands = nb.aggregate_draw_metrics(frames)
    # Provenance columns: sampling_rate identifies WHICH fixed rate this band
    # quantifies noise for; pipeline_commit + created_utc make the band
    # traceable to the exact code state and wall-clock time it was produced at
    # (CLAUDE.md "Scientific reproducibility" / "Data provenance").
    bands["sampling_rate"] = sampling_rate
    bands["pipeline_commit"] = git_commit(str(_REPO_ROOT))
    bands["created_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    band_path = workdir_root / (
        f"noise_bands_{config_path.stem}_rate{sampling_rate}_N{n_success}.csv")
    bands.to_csv(band_path, index=False)
    logger.info("[noise_bands] wrote %s (%d metric/group rows from %d draws).",
               band_path, len(bands), n_success)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
