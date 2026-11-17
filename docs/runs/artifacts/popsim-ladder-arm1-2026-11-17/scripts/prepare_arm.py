"""Prepare one ladder arm of issue #645 (WP2): copy production batch inputs, set the fork's options.

For every requested batch of a production work dir, the batch's ``configs/`` and ``data/`` are copied
unchanged into ``<arm_dir>/<batch>/``; the batch ``settings.yaml`` then gets the PopulationSim options
through the same eqasim-bs functions the pipeline stage uses
(``braunschweig.popsim.folders.populationsim_overrides_from_config``,
``apply_populationsim_overrides`` and, when the draw is on, ``batch_integerizer_seed`` +
``with_integerizer_seed``), so the arm runs exactly the settings a pipeline run with these config
values would write. ``output/`` is created empty. Refuses to overwrite an existing batch folder.

Usage (eqasim-bs code on PYTHONPATH):
  python prepare_arm.py --src <work_dir> --arm-dir <dir> --batches batch_000 batch_032 \
      --draw pareto --keep-fractional --timeout 7200 --random-seed 1234
  python prepare_arm.py ... --neutral --timeout 7200      # fork defaults, only the time limit set
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from braunschweig.popsim import folders


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--arm-dir", required=True, type=Path)
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--draw", choices=folders.INTEGERIZER_DRAWS, default="off")
    parser.add_argument("--keep-fractional", action="store_true")
    parser.add_argument("--timeout", type=int, required=True)
    parser.add_argument("--random-seed", type=int, default=None)
    parser.add_argument("--neutral", action="store_true",
                        help="append only INTEGERIZER_TIMEOUT (all fork options at their defaults)")
    args = parser.parse_args(argv)

    record = {"src": str(args.src), "batches": {}}
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
        settings_path = dst / "configs" / "settings.yaml"
        text = settings_path.read_text(encoding="utf-8")
        if args.neutral:
            if args.draw != "off" or args.keep_fractional or args.random_seed is not None:
                print("refuse: --neutral takes no fork option", file=sys.stderr)
                return 1
            text = text + ("" if text.endswith("\n") else "\n") + f"INTEGERIZER_TIMEOUT: {args.timeout}\n"
            seed = None
        else:
            overrides = folders.populationsim_overrides_from_config(
                keep_fractional_sample_weights=bool(args.keep_fractional),
                integerizer_draw=args.draw,
                integerizer_timeout_s=args.timeout,
            )
            text = folders.apply_populationsim_overrides(text, overrides)
            seed = None
            if args.draw != "off":
                if args.random_seed is None:
                    print("refuse: --random-seed is required when the draw is on", file=sys.stderr)
                    return 1
                seed = folders.batch_integerizer_seed(args.random_seed, batch)
                text = folders.with_integerizer_seed(text, seed)
        settings_path.write_text(text, encoding="utf-8")
        record["batches"][batch] = {"integerizer_seed": seed}
    record.update({"draw": args.draw, "keep_fractional": bool(args.keep_fractional),
                   "timeout": args.timeout, "random_seed": args.random_seed,
                   "neutral": bool(args.neutral)})
    args.arm_dir.mkdir(parents=True, exist_ok=True)
    (args.arm_dir / "arm_settings.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
