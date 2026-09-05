"""Report which synpp cache entries a run config would hit or miss under deterministic hashing.

Builds the stage registry for ``<base.yml> [<overlay.yml>]`` with the deterministic
implicit-config propagation of ``braunschweig.synpp_deterministic`` installed, and compares the
resulting ``<stage>__<hash>`` names with the entries present in a cache directory listing. Use it
BEFORE the first run after installing the patch (or after a config change) to know which stages
will recompute:

    python scripts/report_stage_hash_impact.py configs/base_bs.yml configs/overlays/test_100pct.yml \
        --cache-listing cache_entries.txt [--unpatched]

``--cache-listing`` is a text file with one cache basename per line (``ls <working_directory> |
sed 's/\\.p$//'`` on the run server, or ``ls <cache_shared>``); when omitted, only the hashes are
printed. ``--unpatched`` computes the hashes with synpp's own (order-dependent) propagation for a
side-by-side comparison; those hashes vary with PYTHONHASHSEED and are shown for information only.

Read-only: nothing is written to any working directory.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def build_registry(base: str, overlay: str | None, patched: bool) -> dict:
    from braunschweig import config_compose, synpp_deterministic

    if patched:
        synpp_deterministic.install()
    from synpp.pipeline import process_stages

    if overlay:
        settings = config_compose.compose(base, overlay)
    else:
        import yaml
        with open(base, encoding="utf-8") as fh:
            settings = yaml.safe_load(fh)
    definitions = []
    for item in settings["run"]:
        parameters = {}
        if isinstance(item, dict):
            key = list(item.keys())[0]
            parameters = item[key]
            item = key
        definitions.append({"descriptor": item, "config": parameters})
    previous = os.getcwd()
    os.chdir(REPO)
    try:
        return process_stages(definitions, settings.get("config", {}) or {},
                              settings.get("externals", {}) or {}, settings.get("aliases", {}) or {})
    finally:
        os.chdir(previous)


def read_listing(path: str | None) -> set[str]:
    if path is None:
        return set()
    with open(path, encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip() and not line.startswith("-")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base")
    parser.add_argument("overlay", nargs="?")
    parser.add_argument("--cache-listing", help="text file with one <stage>__<hash> basename per line")
    parser.add_argument("--unpatched", action="store_true", help="use synpp's own propagation (order-dependent)")
    args = parser.parse_args(argv)

    registry = build_registry(args.base, args.overlay, patched=not args.unpatched)
    listing = read_listing(args.cache_listing)
    present_by_stage: dict[str, list[str]] = {}
    for entry in listing:
        if "__" in entry:
            name, digest = entry.rsplit("__", 1)
            present_by_stage.setdefault(name, []).append(digest)

    rows = []
    for stage_hash, stage in sorted(registry.items(), key=lambda kv: kv[1]["wrapper"].name):
        name = stage["wrapper"].name
        digest = stage_hash.rsplit("__", 1)[1] if "__" in stage_hash else ""
        variants = present_by_stage.get(name, [])
        status = "-" if not listing else ("HIT" if digest in variants or (not digest and name in listing) else "MISS")
        rows.append((name, digest[:12], len(stage["config"]), status, len(variants)))

    mode = "unpatched (order-dependent, PYTHONHASHSEED=%s)" % os.environ.get("PYTHONHASHSEED", "random") \
        if args.unpatched else "deterministic"
    print(f"# stage hashes for {args.base} + {args.overlay or '-'} [{mode}]")
    print(f"{'stage':70s} {'hash':13s} {'n_cfg':>5s} {'cache':5s} {'variants_present':>16s}")
    for name, digest, n_cfg, status, n_var in rows:
        print(f"{name:70s} {digest:13s} {n_cfg:5d} {status:5s} {n_var:16d}")
    if listing:
        misses = [r for r in rows if r[3] == "MISS"]
        print(f"\n# {len(rows)} stages, {len(rows) - len(misses)} cache hits, {len(misses)} misses")
        for name, *_ in misses:
            print(f"#   MISS {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
