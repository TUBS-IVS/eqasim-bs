"""CLI to export/prime shared synpp stage caches.

Examples:
  # store the freight chain computed in a local working_directory
  python scripts/cache_share.py export \
      --working-directory eqasim-data/cache_bs \
      --store eqasim-data/cache_shared \
      --modules braunschweig.data.freight.german_wide,braunschweig.freight.extraction

  # prime a fresh working_directory from the store (skip 'foo' so it recomputes)
  python scripts/cache_share.py prime \
      --working-directory eqasim-data/cache_bs_1pct_allfeat_popsim \
      --store eqasim-data/cache_shared \
      --modules braunschweig.freight.extraction --recompute ""

See braunschweig.cache_share for the mechanism (synpp validates the hash on load;
a miss recomputes -- never corruption).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from braunschweig import cache_share


def _split(value):
    """Comma-separated CLI list -> stripped, non-empty items."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("export", "prime"):
        sp = sub.add_parser(name)
        sp.add_argument("--working-directory", required=True)
        sp.add_argument("--store", required=True)
        sp.add_argument("--modules", required=True,
                        help="comma-separated stage module names")
        if name == "prime":
            sp.add_argument("--recompute", default="",
                            help="comma-separated modules to skip priming ('*' = all)")

    args = parser.parse_args(argv)
    modules = _split(args.modules)
    if args.cmd == "export":
        cache_share.export(args.working_directory, modules, args.store)
    else:
        cache_share.prime(args.working_directory, modules, args.store, _split(args.recompute))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
