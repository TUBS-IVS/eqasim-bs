"""Keep the stage results an A/B comparison needs before a run cache is deleted (issue #679, task C7).

Copies, for each named stage, the newest ``pipeline.json`` entry's ``<key>.p``, ``<key>.cache/`` and
``<key>.metadata.json`` from ``--cache`` into ``--out`` (physical copies, ``shutil.copy2``), and writes a
``pipeline.json`` holding only those entries. ``i679_ab_metrics.py`` and ``i679_off_identity.py`` read such
a folder exactly like a full cache (``resolve_stage`` picks the newest entry whose pickle exists).

Usage: python i679_keep_stage_pickles.py --cache RUN/cache --out KEEP_DIR
"""
import argparse
import json
import os
import shutil
import sys

from i679_ab_metrics import STAGES, resolve_stage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--extra-stage", action="append", default=[])
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=False)
    with open(os.path.join(args.cache, "pipeline.json"), encoding="utf-8") as handle:
        meta = json.load(handle)
    kept = {}
    for stage in list(STAGES.values()) + args.extra_stage:
        _path, key, entry = resolve_stage(args.cache, stage)
        kept[key] = entry
        shutil.copy2(os.path.join(args.cache, key + ".p"), os.path.join(args.out, key + ".p"))
        for suffix in (".metadata.json",):
            source = os.path.join(args.cache, key + suffix)
            if os.path.isfile(source):
                shutil.copy2(source, os.path.join(args.out, key + suffix))
        folder = os.path.join(args.cache, key + ".cache")
        if os.path.isdir(folder):
            shutil.copytree(folder, os.path.join(args.out, key + ".cache"), symlinks=True)
        print("kept", key)
    with open(os.path.join(args.out, "pipeline.json"), "w", encoding="utf-8") as handle:
        json.dump(kept, handle, indent=1, sort_keys=True)
    assert set(kept) <= set(meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
