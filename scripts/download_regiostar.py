"""
Download the BMV/BBSR RegioStaR (Regionalstatistische Raumtypologie)
reference file at Gemeinde resolution.

Source
------
https://www.bmv.de/SharedDocs/DE/Anlage/G/regiostar-referenzdateien.xlsx
(7.7 MB XLSX, public BMV download).

The file contains multiple sheets per Gebietsstand 2015–2020. We use
sheet ``ReferenzGebietsstand2020`` (latest in the bundle) which carries
8-digit AGS (`gem_20`), Gemeindename (`name_20`) and the full RegioStaR
hierarchy: ``RegioStaR2 / 4 / 17 / 7 / 5`` plus ``RegioStaRGem7 / 5``.

License
-------
The BMV publishes the file as public reference material under the
"Datenlizenz Deutschland – Namensnennung – Version 2.0" terms. Cite as
"BMV (2020): RegioStaR – Regionalstatistische Raumtypologie".

Usage
-----
    python scripts/download_regiostar.py
    python scripts/download_regiostar.py --update-checksums
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request

URL = (
    "https://www.bmv.de/SharedDocs/DE/Anlage/G/"
    "regiostar-referenzdateien.xlsx?__blob=publicationFile"
)
TARGET_NAME = "regiostar_referenzdatei.xlsx"
EXPECTED_SHA256 = (
    "550da569e3cd97de11c87859f40a290f200567f63dee4d79c693c7a3393a04e6"
)
MIN_SIZE = 7_000_000


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
        default=os.path.join("eqasim-data", "data", "regiostar"),
    )
    parser.add_argument("--update-checksums", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    os.makedirs(args.dest, exist_ok=True)
    target = os.path.join(args.dest, TARGET_NAME)

    need_download = args.force or not os.path.exists(target)
    if not need_download:
        actual = _sha256_of(target)
        if actual != EXPECTED_SHA256 and not args.update_checksums:
            print(f"  WARN  hash mismatch (have {actual}); re-downloading")
            need_download = True

    if need_download:
        _download(URL, target)

    size = os.path.getsize(target)
    if size < MIN_SIZE:
        print(
            f"  ERROR file size {size} < {MIN_SIZE}; download truncated?",
            file=sys.stderr,
        )
        return 2

    actual = _sha256_of(target)
    if args.update_checksums:
        print(f"  HASH  {TARGET_NAME} = {actual}")
    elif actual != EXPECTED_SHA256:
        print(
            f"  ERROR SHA-256 mismatch:\n"
            f"        expected {EXPECTED_SHA256}\n"
            f"        got      {actual}",
            file=sys.stderr,
        )
        return 3
    else:
        print(f"  OK    {TARGET_NAME} ({size:,} B)")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
