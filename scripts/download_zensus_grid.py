"""
Download the Zensus 2022 100m population grid (and INSPIRE 100m cell
manifest) from the ``z22data`` GitHub mirror in digestible parquet
chunks.

Source repository
-----------------
https://github.com/JsLth/z22data — companion data repo for the ``z22``
R package.  Contains the official Zensus 2022 grid data sourced from
the Statistisches Bundesamt and the BKG INSPIRE 100 m geographic grid,
re-packed into compact parquet chunks.

License
-------
Data licence Germany — attribution — version 2.0 (dl-de/by-2-0).
Attribution: "© Statistisches Bundesamt (Destatis), 2024" plus
"GeoBasis-DE / BKG 2025" for the underlying INSPIRE grid.

Pinned SHA-256 checksums protect the pipeline against silent upstream
changes.  Run with ``--update-checksums`` to re-pin after a deliberate
upstream refresh.

Usage
-----
    python scripts/download_zensus_grid.py
    python scripts/download_zensus_grid.py --dest eqasim-data/data/zensus_grid
    python scripts/download_zensus_grid.py --update-checksums
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request

BASE_URL = "https://raw.githubusercontent.com/JsLth/z22data/main"

ARTEFACTS = (
    {
        "url": f"{BASE_URL}/z22_data_100m/population_0.parquet",
        "name": "population_100m.parquet",
        "sha256": "5b3a350ee85e454ae487a4e233acf5310964586fc175fdff6a98f616b6cc0a03",
        "min_size": 9_000_000,
    },
    {
        "url": f"{BASE_URL}/grids/grid_2019_100m.parquet",
        "name": "grid_100m.parquet",
        "sha256": "80fc96f28afca2fda5c0c97f13d536a70ccd6715cd15480fcf73a08bf21af0cf",
        "min_size": 1_000_000,
    },
)


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, target: str) -> None:
    print(f"  GET  {url}")
    print(f"  ->   {target}")
    with urllib.request.urlopen(url) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        default=os.path.join("eqasim-data", "data", "zensus_grid"),
        help="Output directory (default: eqasim-data/data/zensus_grid)",
    )
    parser.add_argument(
        "--update-checksums",
        action="store_true",
        help="Print fresh SHA-256 hashes of downloaded files and exit.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the file already exists with a matching hash.",
    )
    args = parser.parse_args(argv)

    os.makedirs(args.dest, exist_ok=True)

    fresh_hashes: dict[str, str] = {}
    for art in ARTEFACTS:
        target = os.path.join(args.dest, art["name"])
        need_download = args.force or not os.path.exists(target)
        if not need_download:
            actual = _sha256_of(target)
            if actual != art["sha256"] and not args.update_checksums:
                print(
                    f"  WARN  {art['name']} hash mismatch (have {actual}); "
                    "re-downloading"
                )
                need_download = True

        if need_download:
            _download(art["url"], target)

        size = os.path.getsize(target)
        if size < art["min_size"]:
            print(
                f"  ERROR {art['name']} size {size} < {art['min_size']}; "
                "download truncated?",
                file=sys.stderr,
            )
            return 2

        actual = _sha256_of(target)
        fresh_hashes[art["name"]] = actual
        if args.update_checksums:
            print(f"  HASH  {art['name']} = {actual}")
        elif actual != art["sha256"]:
            print(
                f"  ERROR {art['name']} SHA-256 mismatch:\n"
                f"        expected {art['sha256']}\n"
                f"        got      {actual}",
                file=sys.stderr,
            )
            return 3
        else:
            print(f"  OK    {art['name']} ({size:,} B)")

    if args.update_checksums:
        print("\n# Update ARTEFACTS in scripts/download_zensus_grid.py:")
        for name, h in fresh_hashes.items():
            print(f"#   {name}: {h}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
