"""
Download the BMV/BBSR RegioStaR (Regionalstatistische Raumtypologie)
reference workbook at Gemeinde resolution.

Source
------
mobilithek.info offer 689522949364838400 ("RegioStaR - Regionalstatistische
Raumtypisierung", BMV), file ``2024 RegioStaR-Referenzdateien_Mobilthek.xlsx``
(12.4 MB, resource dated 2026-05-22 in the govdata.de catalogue). The BMV
RegioStaR page links to this offer.

The workbook carries one sheet per Gebietsstand 2015-2024
(``ReferenzGebietsstand20YY``: 8-digit AGS ``gem_YY``, Gemeindename
``name_YY`` and the RegioStaR hierarchy ``RegioStaR2 / 4 / 17 / 7 / 5`` plus
``RegioStaRGem7 / 5``). The pipeline reads ``ReferenzGebietsstand2023``
(``braunschweig.data.bbsr.regiostar.SHEET``); why: ADR-0180. Its
``ReferenzGebietsstand2020`` sheet equals the one of the earlier BMV bundle
(``regiostar_referenzdatei.xlsx``), which the scripts that reproduce
pre-ADR-0180 measurements still read.

License
-------
"Datenlizenz Deutschland - Namensnennung - Version 2.0". Cite as
"BMV: RegioStaR - Regionalstatistische Raumtypologie, Referenzdateien".

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
    "https://mobilithek.info/mdp-api/files/aux/689522949364838400/"
    "2024%20RegioStaR-Referenzdateien_Mobilthek.xlsx"
)
TARGET_NAME = "regiostar_referenzdateien_2024.xlsx"
EXPECTED_SHA256 = (
    "a55b217b3b5b8723bf970e4d89b7df6ea80b9c75846fc626de97320326b4990d"
)
MIN_SIZE = 12_000_000


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, target: str) -> None:
    print(f"  GET  {url}")
    print(f"  ->   {target}")
    # mobilithek rejects requests without a browser-like User-Agent.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request) as response, open(target, "wb") as out:
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
