"""
Pinned downloader for the Bundesagentur für Arbeit
"Pendler nach Wirtschaftsabschnitten" Kreis-level CSV (TASK-015).

The BA Statistik-Service publishes the file at:

    https://statistik.arbeitsagentur.de/Statistikdaten/Detail/...

The exact download URL changes per quarterly snapshot and the BA
session sets cookies. Therefore this script does NOT auto-download
unless ``--url`` is passed explicitly. By default it verifies an
already-present local file against the configured SHA-256.

Usage::

    python scripts/download_ba_pendler_detailed.py \
        --dest eqasim-data/data/braunschweig/ba_pendler_wz.csv

    python scripts/download_ba_pendler_detailed.py \
        --dest eqasim-data/data/braunschweig/ba_pendler_wz.csv \
        --url 'https://statistik.arbeitsagentur.de/.../Pendler_WZ_2025q1.csv' \
        --update-checksums
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import urllib.request


# Update via --update-checksums after a verified manual download.
EXPECTED_SHA256 = ""
EXPECTED_SIZE = 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    print(f"[ba-pendler] downloading {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "eqasim-bs/1.0 (+research)"},
    )
    with urllib.request.urlopen(req) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dest", required=True, type=Path)
    p.add_argument("--url", default=None, help="Optional download URL")
    p.add_argument("--force", action="store_true",
                   help="Re-download even if dest exists")
    p.add_argument("--update-checksums", action="store_true",
                   help="Print updated EXPECTED_SHA256/EXPECTED_SIZE for the script")
    args = p.parse_args()

    args.dest.parent.mkdir(parents=True, exist_ok=True)

    if args.url and (args.force or not args.dest.exists()):
        _download(args.url, args.dest)
    elif not args.dest.exists():
        print(
            f"[ba-pendler] {args.dest} not present and --url not given. "
            f"Manually download the BA 'Pendler nach Wirtschaftsabschnitten' "
            f"CSV (Kreis-level) into the destination, then rerun for verify."
        )
        return 2

    actual_sha = _sha256(args.dest)
    actual_size = args.dest.stat().st_size

    if args.update_checksums:
        print(json.dumps({
            "EXPECTED_SHA256": actual_sha,
            "EXPECTED_SIZE": actual_size,
        }, indent=2))
        return 0

    if not EXPECTED_SHA256:
        print(
            f"[ba-pendler] No SHA-256 baseline configured. "
            f"Computed hash: {actual_sha} ({actual_size} B). "
            f"Pin this by re-running with --update-checksums and pasting "
            f"into EXPECTED_SHA256."
        )
        return 0

    if actual_sha != EXPECTED_SHA256 or actual_size != EXPECTED_SIZE:
        print(
            f"[ba-pendler] CHECKSUM MISMATCH\n"
            f"  expected: {EXPECTED_SHA256} ({EXPECTED_SIZE} B)\n"
            f"  got:      {actual_sha} ({actual_size} B)"
        )
        return 1

    print(f"[ba-pendler] OK — {args.dest} matches pinned hash.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
