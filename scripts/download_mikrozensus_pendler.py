"""
Pinned downloader for the Mikrozensus "Berufspendler" mode-of-transport tables
(DESTATIS GENESIS-Online) -- the journey-to-work modal-split reference used to
assign + calibrate the FIXED mode of cordon in-commuter agents (cordon model
sub-project 2, see docs/superpowers/specs/2026-06-02-incommuter-agents-v1).

Why this table
--------------
In-commuters are long-distance cross-Kreis commuters (47-90 km). Their mode
split is NOT the regional average and NOT the French HTS (ENTD) donor mode.
The Mikrozensus asks every employed person their **main mode of transport for
the journey to work, by distance band (Entfernung) and time** -- the
authoritative German reference for commute mode by distance.

Tables (GENESIS-Online, https://www-genesis.destatis.de)
--------------------------------------------------------
* ``12251-0006`` -- Erwerbstaetige (Berufspendler) nach benutztem Verkehrsmittel
  fuer den Hinweg zur Arbeit x Entfernung x Zeitaufwand (federal, Germany).
* ``12251-0106`` -- same, per Bundesland (use for Niedersachsen + neighbours).
  (Companion tables 12251-0004/0005 give distance / time only.)

The Mikrozensus commuter module is run every 4 years.

Access
------
GENESIS-Online table *export* (CSV/XLSX) requires a free account, exactly like
the other GENESIS inputs in this repo (13111-*, LSN K3300101, DESTATIS
21311-0007). We therefore do not bake the authenticated HTTP flow into the
script: pass ``--url`` for an already-authenticated export link, or drop the
manually-exported file at ``--dest`` and re-run with no URL to verify the
checksum. Mirror of ``scripts/download_lsn_schulen.py``.

Usage::

    python scripts/download_mikrozensus_pendler.py \
        --dest eqasim-data/data/braunschweig/mikrozensus/12251-0006.csv \
        --url '<authenticated GENESIS CSV export URL for 12251-0006>'

    # verify-only after a manual export (no --url):
    python scripts/download_mikrozensus_pendler.py \
        --dest eqasim-data/data/braunschweig/mikrozensus/12251-0006.csv

    # refresh the pinned digest after a verified update:
    python scripts/download_mikrozensus_pendler.py \
        --dest eqasim-data/data/braunschweig/mikrozensus/12251-0006.csv \
        --url <url> --update-checksums
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path


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
    print(f"[mikrozensus-pendler] downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "eqasim-bs/1.0 (+research)"})
    with urllib.request.urlopen(req) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, type=Path,
                        help="Target file (suggested: "
                             "eqasim-data/data/braunschweig/mikrozensus/12251-0006.csv)")
    parser.add_argument("--url", default=None,
                        help="Authenticated GENESIS CSV/XLSX export URL for the table")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if --dest already exists")
    parser.add_argument("--update-checksums", action="store_true",
                        help="Print refreshed EXPECTED_SHA256 / EXPECTED_SIZE")
    args = parser.parse_args()

    args.dest.parent.mkdir(parents=True, exist_ok=True)

    if args.url and (args.force or not args.dest.exists()):
        _download(args.url, args.dest)
    elif not args.dest.exists():
        print(
            f"[mikrozensus-pendler] {args.dest} is missing and --url was not provided.\n"
            f"  Manual fallback:\n"
            f"    1. Open https://www-genesis.destatis.de (free account / login).\n"
            f"    2. Search for table '12251-0006' (federal) or '12251-0106'\n"
            f"       (per Bundesland) -- Berufspendler nach Verkehrsmittel x\n"
            f"       Entfernung. Restrict / select the distance and mode dims.\n"
            f"    3. Export as CSV (flat / 'Werte') and save to {args.dest}.\n"
            f"    4. Re-run this script (no --url) to verify the checksum.\n",
            file=sys.stderr,
        )
        return 2

    actual_size = args.dest.stat().st_size
    actual_sha = _sha256(args.dest)

    if args.update_checksums:
        print(f"EXPECTED_SHA256 = \"{actual_sha}\"")
        print(f"EXPECTED_SIZE = {actual_size}")
        return 0

    if EXPECTED_SHA256 and actual_sha != EXPECTED_SHA256:
        print(
            f"[mikrozensus-pendler] checksum mismatch for {args.dest}\n"
            f"  expected sha256 {EXPECTED_SHA256}\n"
            f"  actual   sha256 {actual_sha}\n"
            f"  Re-run with --update-checksums after confirming the new file is "
            f"authoritative.",
            file=sys.stderr,
        )
        return 1

    print(f"[mikrozensus-pendler] OK  {args.dest}  ({actual_size} bytes, sha256 {actual_sha[:12]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
