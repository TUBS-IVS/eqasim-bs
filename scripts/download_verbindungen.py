"""
Download the open-data VerBindungen datasets (BMDV FuE 97.421/2019) from
mobilithek.info into ``eqasim-data/data/verbindungen/``.

Source / provenance
-------------------
VerBindungen Abschlussbericht v1.3 (Dec 2024), data products chapter 6.
All files are published by BMDV under the mobilithek open-data licence
``LICENSE_FREE_USE_OPEN_DATA``; reference date of all tables is 31.12.2019
(Gebietsstand 31.12.2019). Direct download URLs follow the pattern
``https://mobilithek.info/mdp-api/files/aux/<offer_id>/<filename>`` and were
verified to work anonymously (2026-07-15).

Downloaded files (original upstream names, kept verbatim for traceability):

- ``Shapefiles_VerBindungen_Zellen.zip``   cell geometry (EPSG:4326) + AGS
- ``QZM-Berufspendler-VerBindungen-Verkehrszellen.csv``  primary OD reference
- ``SvBaGeB_Statisch_WO_Verkehrszellen.csv``   workers at home per cell
- ``SvBaGeB_Statisch_AO_Verkehrszellen.csv``   workers at workplace per cell
- ``SvBaGeB_Relationen_WO_AO_Verkehrszellen.csv``  OD with breakdowns (not
  consumed by the pipeline yet; downloaded for later segment checks)
- ``QZM-Berufspendler-VerBindungen-Verkehrszellen-Einpendler-Ausland.csv``
  foreign in-commuters (not consumed yet)

Usage
-----
    python scripts/download_verbindungen.py
    python scripts/download_verbindungen.py --update-checksums
    python scripts/download_verbindungen.py --force
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import sys
import urllib.request

BASE_URL = "https://mobilithek.info/mdp-api/files/aux"

# (offer_id, filename, expected_sha256 or None, min_size_bytes)
# SHA-256 values are pinned after the first verified download via
# ``--update-checksums`` (they are None until then).
FILES: list[tuple[str, str, str | None, int]] = [
    ("767718144937472000", "Shapefiles_VerBindungen_Zellen.zip",
     "b106a53e39987de27535e23b6fe009762b2b242555e7f8d473b20309991a56a5", 10_000_000),
    ("767413386339078144", "QZM-Berufspendler-VerBindungen-Verkehrszellen.csv",
     "272ff63c97877e06c318a71c249a59ff6846e80d20d295f6187ca065c59c6995", 3_000_000),
    ("767432288091680768", "SvBaGeB_Statisch_WO_Verkehrszellen.csv",
     "90c531e8d237f529779a164bc53b0d400c0ddc01ffd0b6796f60350496c7a60f", 50_000),
    ("767434730673983488", "SvBaGeB_Statisch_AO_Verkehrszellen.csv",
     "4e1a0b5e1de208315a5bc6ee61ceb7f5d45d7c12f03d7f95cc822da84ed74d07", 50_000),
    ("767435815362703360", "SvBaGeB_Relationen_WO_AO_Verkehrszellen.csv",
     "24c61007037bc77bdc93f08785c403929d7f4ea3147ec3f00e56d975f5784dd9", 500_000),
    ("767416426915868672", "QZM-Berufspendler-VerBindungen-Verkehrszellen-Einpendler-Ausland.csv",
     "7727a2f7223d880d417d95d8620ef8e791acaf763983b716d5ff1235c8bfe6db", 5_000),
]

PROVENANCE_NAME = "PROVENANCE.md"


def _sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def needs_download(path: str, expected_sha256: str | None, force: bool) -> bool:
    """Decide whether *path* must be (re-)fetched.

    Missing file or ``force`` -> download. Present file with a pinned hash that
    does not match -> download (stale/corrupt). Present file without a pinned
    hash -> keep (first-acquisition mode; the hash gets pinned afterwards).
    """
    if force or not os.path.exists(path):
        return True
    if expected_sha256 is None:
        return False
    return _sha256_of(path) != expected_sha256


def _download(url: str, target: str) -> None:
    print(f"  GET  {url}")
    print(f"  ->   {target}")
    request = urllib.request.Request(url, headers={"User-Agent": "eqasim-bs data fetch"})
    with urllib.request.urlopen(request) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)


def render_provenance(entries: list[dict]) -> str:
    """Render PROVENANCE.md content for the downloaded files."""
    lines = [
        "# VerBindungen data provenance",
        "",
        "Source: VerBindungen research project (BMDV FuE 97.421/2019),",
        "Abschlussbericht v1.3 (Dec 2024), data products chapter 6.",
        "Publisher: Bundesministerium fuer Digitales und Verkehr (BMDV) via",
        "mobilithek.info. Licence: LICENSE_FREE_USE_OPEN_DATA.",
        "Reference date of all tables: 31.12.2019 (Gebietsstand 31.12.2019).",
        "Files keep their original upstream names for traceability.",
        "Fetched by scripts/download_verbindungen.py.",
        "",
        "| filename | mobilithek offer id | sha256 | size (bytes) | downloaded at |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| {e['filename']} | {e['offer_id']} | {e['sha256']} "
            f"| {e['size_bytes']} | {e['downloaded_at']} |"
        )
    lines += [
        "",
        "Direct URL pattern:",
        "`https://mobilithek.info/mdp-api/files/aux/<offer_id>/<filename>`",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", default=os.path.join("eqasim-data", "data", "verbindungen"),
    )
    parser.add_argument("--update-checksums", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    os.makedirs(args.dest, exist_ok=True)
    entries, failures = [], 0

    for offer_id, filename, expected, min_size in FILES:
        target = os.path.join(args.dest, filename)
        url = f"{BASE_URL}/{offer_id}/{filename}"

        if needs_download(target, expected, args.force):
            _download(url, target)

        size = os.path.getsize(target)
        if size < min_size:
            print(f"  ERROR {filename}: size {size} < {min_size}; truncated download?",
                  file=sys.stderr)
            failures += 1
            continue

        actual = _sha256_of(target)
        if args.update_checksums:
            print(f'  HASH  ("{offer_id}", "{filename}", "{actual}", {min_size}),')
        elif expected is not None and actual != expected:
            print(f"  ERROR {filename}: SHA-256 mismatch\n"
                  f"        expected {expected}\n        got      {actual}",
                  file=sys.stderr)
            failures += 1
            continue
        else:
            print(f"  OK    {filename} ({size:,} B)")

        entries.append(dict(
            filename=filename, offer_id=offer_id, url=url, sha256=actual,
            size_bytes=size,
            downloaded_at=datetime.datetime.now().isoformat(timespec="seconds"),
        ))

    provenance_path = os.path.join(args.dest, PROVENANCE_NAME)
    with open(provenance_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_provenance(entries))
    print(f"  WROTE {provenance_path}")

    if failures:
        print(f"Done with {failures} failure(s).", file=sys.stderr)
        return 2
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
