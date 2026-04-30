"""
Pinned downloader for Niedersächsisches Landesamt für Statistik (LSN) /
Regionaldatenbank Deutschland school statistics — TASK-001 of
``plan/feature-education-gravity-bs-1.md``.

Targets per-Gemeinde (8-digit AGS) head counts of pupils by school type
(Schulform) for ZGB-8 (AGS prefixes 03101..03158). The LSN publishes
this through the open GENESIS-Online portal; mirrors are also available
via the Regionaldatenbank Deutschland (RDB).

Recommended source tables (latest school year reported):

* GENESIS-Online (LSN), table ``K3300101`` —
  *Schüler/innen an allgemein bildenden Schulen nach Schulart, Geschlecht
  und Gemeinde*
  https://www1.nls.niedersachsen.de/statistik/

* Regionaldatenbank Deutschland (DESTATIS-mirrored), table
  ``21111-04-01-4`` — *Schüler/innen an allgemein bildenden Schulen
  nach Schularten — Gemeinden*
  https://www.regionalstatistik.de/genesis/online/

* Berufsbildende Schulen: GENESIS table ``K3320101`` (LSN) /
  ``21121-04-01-4`` (RDB).

Both portals provide a ``CSV`` flatfile export and require a free
account for unattended downloads. We therefore do not bake the HTTP
flow into the script: pass ``--url`` for an already-authenticated link,
or drop the manually-exported CSV at ``--dest`` and re-run with no URL
to verify the checksum.

Usage::

    python scripts/download_lsn_schulen.py \
        --dest eqasim-data/data/braunschweig/lsn/lsn_schulen_2024.csv \
        --url 'https://www1.nls.niedersachsen.de/.../K3300101_2024.csv'

    # Verify-only after manual download (no --url):
    python scripts/download_lsn_schulen.py \
        --dest eqasim-data/data/braunschweig/lsn/lsn_schulen_2024.csv

    # Refresh pinned digest after a verified update:
    python scripts/download_lsn_schulen.py \
        --dest eqasim-data/data/braunschweig/lsn/lsn_schulen_2024.csv \
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
    print(f"[lsn-schulen] downloading {url}")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, type=Path,
                        help="Target CSV path (suggested: "
                             "eqasim-data/data/braunschweig/lsn/lsn_schulen_<year>.csv)")
    parser.add_argument("--url", default=None,
                        help="Authenticated GENESIS / RDB CSV download URL")
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
            f"[lsn-schulen] {args.dest} is missing and --url was not provided.\n"
            f"  Manual fallback:\n"
            f"    1. Open https://www1.nls.niedersachsen.de/statistik/\n"
            f"    2. Search for table 'K3300101' (allgemein bildende Schulen)\n"
            f"       or 'K3320101' (berufsbildende Schulen).\n"
            f"    3. Restrict the regional filter to 'Niedersachsen' /\n"
            f"       sub-select Gemeinden of ZGB-8 (AGS 03101..03158).\n"
            f"    4. Export as CSV and save to {args.dest}.\n"
            f"    5. Re-run this script (no --url) to verify the checksum.\n",
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
            f"[lsn-schulen] checksum mismatch for {args.dest}\n"
            f"  expected sha256 {EXPECTED_SHA256}\n"
            f"  actual   sha256 {actual_sha}\n"
            f"  Re-run with --update-checksums after confirming the "
            f"new file is authoritative.",
            file=sys.stderr,
        )
        return 1

    print(f"[lsn-schulen] OK  {args.dest}  ({actual_size} bytes, sha256 {actual_sha[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
