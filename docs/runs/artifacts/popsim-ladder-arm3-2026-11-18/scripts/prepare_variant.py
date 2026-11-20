"""Prepare PopulationSim batch folders for one settings variant (issues #646 and #656).

For every requested batch of a source arm (folders written for ladder arm 2), ``configs/`` and
``data/`` are copied unchanged into ``<arm_dir>/<batch>/`` and ``output/`` is created empty. The batch
``settings.yaml`` is then rebuilt the way the pipeline stage writes it: the block of tracked options at
the end of the source file is removed, which gives the text of the server-local settings file, and the
variant's options are appended with the stage's own functions
(``braunschweig.popsim.folders.populationsim_overrides_from_config``,
``require_fork_version_for_run_time_options``, ``apply_populationsim_overrides``,
``batch_integerizer_seed`` and ``with_integerizer_seed``). So a run on these folders uses exactly the
settings a pipeline run with these config values would write.

The script records, per batch, the sha256 of every copied file and of the source file it came from
(they must be equal), the removed and the appended settings lines, and the variant's values, in
``<arm_dir>/prepare_variant.json``. It refuses to overwrite an existing batch folder.

It needs eqasim-bs code that knows the two run-time option keys of issue #656
(``store_positive_weight_rows_only``, ``fast_paths``): that code writes both options into every
settings file, as ``false`` unless the flags below are given.

Usage (eqasim-bs code on PYTHONPATH, eqasim environment):
  python prepare_variant.py --src <arm-2 dir> --arm-dir <new dir> --batches batch_000 batch_032 \
      --random-seed 1234 --rounding controlled --populationsim-version 0.10.0+bs.2 \
      [--controlled-method search] [--store-positive-rows] [--fast-paths] [--timeout 7200]
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from braunschweig.popsim import folders

TRACKED_BLOCK_MARKER = "# Set by the tracked config (braunschweig.population.popsim.*)"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_tracked_block(settings_text: str) -> tuple:
    """Return ``(server_local_text, removed_lines)``: the text before the tracked block and that block."""
    lines = settings_text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith(TRACKED_BLOCK_MARKER)]
    if len(starts) != 1:
        raise ValueError(f"expected exactly one tracked-options block, found {len(starts)}")
    return "".join(lines[: starts[0]]), [line.rstrip("\n") for line in lines[starts[0]:]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--arm-dir", required=True, type=Path)
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--random-seed", type=int, required=True)
    parser.add_argument("--rounding", choices=("round", "controlled"), required=True)
    parser.add_argument("--populationsim-version", required=True)
    parser.add_argument("--controlled-method", default="search")
    parser.add_argument("--search-max-sweeps", type=int, default=200)
    parser.add_argument("--search-shortlist", type=int, default=24)
    parser.add_argument("--penalty-floor", type=float, default=100.0)
    parser.add_argument("--penalty-cap", type=float, default=100000.0)
    parser.add_argument("--parent-total-factor", type=float, default=10.0)
    parser.add_argument("--conserve-penalty", type=float, default=100000.0)
    parser.add_argument("--store-positive-rows", action="store_true")
    parser.add_argument("--fast-paths", action="store_true")
    parser.add_argument("--draw", choices=folders.INTEGERIZER_DRAWS, default="gumbel")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args(argv)

    overrides = folders.populationsim_overrides_from_config(
        keep_fractional_sample_weights=True,
        integerizer_draw=args.draw,
        integerizer_timeout_s=args.timeout,
        integerizer_rounding=args.rounding,
        integerizer_penalty_floor=args.penalty_floor,
        integerizer_penalty_cap=args.penalty_cap,
        integerizer_parent_total_factor=args.parent_total_factor,
        integerizer_conserve_penalty=args.conserve_penalty,
        integerizer_controlled_method=args.controlled_method,
        integerizer_search_max_sweeps=args.search_max_sweeps,
        integerizer_search_shortlist=args.search_shortlist,
        store_positive_weight_rows_only=bool(args.store_positive_rows),
        fast_paths=bool(args.fast_paths),
    )
    folders.require_fork_version_for_run_time_options(overrides, args.populationsim_version)

    record = {"src": str(args.src), "random_seed": args.random_seed,
              "populationsim_version": args.populationsim_version,
              "overrides": {key: value for key, value in overrides.items()}, "batches": {}}
    for batch in args.batches:
        src = args.src / batch
        dst = args.arm_dir / batch
        if dst.exists():
            print(f"refuse: {dst} exists", file=sys.stderr)
            return 1
        dst.mkdir(parents=True)
        shutil.copytree(src / "configs", dst / "configs")
        shutil.copytree(src / "data", dst / "data")
        (dst / "output").mkdir()
        files = {}
        for path in sorted(p for p in dst.rglob("*") if p.is_file()):
            relative = path.relative_to(dst).as_posix()
            copied, original = _sha256(path), _sha256(src / relative)
            if copied != original:
                raise RuntimeError(f"{batch}/{relative}: copy differs from its source")
            files[relative] = copied
        settings_path = dst / "configs" / "settings.yaml"
        server_local_text, removed = split_tracked_block(settings_path.read_text(encoding="utf-8"))
        text = folders.apply_populationsim_overrides(server_local_text, overrides)
        seed = None
        if args.draw != "off":
            seed = folders.batch_integerizer_seed(args.random_seed, batch)
            text = folders.with_integerizer_seed(text, seed)
        settings_path.write_text(text, encoding="utf-8")
        appended = text[len(server_local_text):].splitlines()
        if not text.startswith(server_local_text):
            raise RuntimeError(f"{batch}: the rebuilt settings do not start with the server-local text")
        record["batches"][batch] = {
            "integerizer_seed": seed, "files_sha256_before_settings_rewrite": files,
            "settings_sha256": _sha256(settings_path), "removed_lines": removed, "appended_lines": appended}
    args.arm_dir.mkdir(parents=True, exist_ok=True)
    (args.arm_dir / "prepare_variant.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"arm_dir": str(args.arm_dir), "batches": args.batches,
                      "overrides": record["overrides"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
