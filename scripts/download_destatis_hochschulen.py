"""
Pinned downloader for the DESTATIS Hochschulstatistik —
TASK-002 of ``plan/feature-education-gravity-bs-1.md``.

Targets per-Hochschulort head counts of students (Studierende). The
authoritative dataset is published in DESTATIS GENESIS-Online:

* Table ``21311-0001`` — *Studierende: Bundesländer, Semester,
  Nationalität, Geschlecht*. Year-aggregated counts per Hochschule
  including its seat (Hochschulort).

* Table ``21311-0007`` — *Studierende an Hochschulen — Studienorte und
  Hochschulen* (more granular: per Hochschule × Studienort). This is
  the table we ultimately consume in
  ``braunschweig.data.education.capacity`` because it resolves
  multi-campus institutions (e.g. Ostfalia → Wolfenbüttel, Wolfsburg,
  Salzgitter, Suderburg) to their respective municipalities.

DESTATIS GENESIS exposes a free REST API (``/api/rest/2020`` endpoint)
for registered users. As with the LSN downloader we keep the network
layer optional: pass ``--url`` for an authenticated CSV link, or drop
the manually-exported file at ``--dest`` and re-run for verification.

Usage::

    python scripts/download_destatis_hochschulen.py \
        --dest eqasim-data/data/braunschweig/destatis/hochschulen_2024.csv \
        --url 'https://www-genesis.destatis.de/genesisWS/.../21311-0007.csv'

    # Verify-only after manual export (no --url):
    python scripts/download_destatis_hochschulen.py \
        --dest eqasim-data/data/braunschweig/destatis/hochschulen_2024.csv

    python scripts/download_destatis_hochschulen.py \
        --dest eqasim-data/data/braunschweig/destatis/hochschulen_2024.csv \
        --url <url> --update-checksums
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path


EXPECTED_SHA256 = ""
EXPECTED_SIZE = 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    print(f"[destatis-hs] downloading {url}")
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
                             "eqasim-data/data/braunschweig/destatis/"
                             "hochschulen_<year>.csv)")
    parser.add_argument("--url", default=None,
                        help="Authenticated GENESIS REST CSV URL")
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
            f"[destatis-hs] {args.dest} is missing and --url was not provided.\n"
            f"  Manual fallback:\n"
            f"    1. Open https://www-genesis.destatis.de/genesis/online\n"
            f"    2. Search for table '21311-0007'\n"
            f"       (Studierende an Hochschulen — Studienorte und Hochschulen).\n"
            f"    3. Restrict the regional filter to Niedersachsen (or all\n"
            f"       Bundesländer if you also need out-commuting students).\n"
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
            f"[destatis-hs] checksum mismatch for {args.dest}\n"
            f"  expected sha256 {EXPECTED_SHA256}\n"
            f"  actual   sha256 {actual_sha}\n"
            f"  Re-run with --update-checksums after confirming the new "
            f"file is authoritative.",
            file=sys.stderr,
        )
        return 1

    print(f"[destatis-hs] OK  {args.dest}  ({actual_size} bytes, sha256 {actual_sha[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
