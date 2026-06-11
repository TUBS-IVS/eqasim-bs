"""Download the VSP german-wide-freight v3 input files (local-only, gitignored).

Provenance:
  https://svn.vsp.tu-berlin.de/repos/public-svn/matsim/scenarios/countries/de/german-wide-freight/v3/
  Lu, Martins-Turner, Nagel (2022): Creating an agent-based long-haul freight
  transport model for Germany. Procedia Computer Science 201, 614-620.
  doi:10.1016/j.procs.2022.03.080

Usage:
  python scripts/download_german_wide_freight.py \
      [--target-dir eqasim-data/data/braunschweig/freight/german-wide-freight-v3]

The files are several hundred MB; existing non-empty files are skipped, so the
script is idempotent. A sha256 checksum log and a provenance README are written
next to the downloads.
"""
import argparse
import hashlib
import logging
import os
import shutil
import sys
import urllib.request
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = ("https://svn.vsp.tu-berlin.de/repos/public-svn/matsim/scenarios/"
            "countries/de/german-wide-freight/v3/")
FILES = (
    "german_freight.100pct.plans.xml.gz",
    "germany-europe-network.xml.gz",
    "updates.txt",
)
# Conservative truncation floors in bytes: a complete download is at least this
# large. These are NOT exact sizes (the real files are larger) -- they only
# catch partial downloads left by a network drop or crash mid-stream.
MIN_SIZES = {
    "german_freight.100pct.plans.xml.gz": 50_000_000,
    "germany-europe-network.xml.gz": 1_000_000,
    "updates.txt": 10,
}
DOWNLOAD_TIMEOUT_SECONDS = 120
DEFAULT_TARGET = Path("eqasim-data/data/braunschweig/freight/german-wide-freight-v3")


def plan_downloads(target_dir, force=False):
    """(url, target) pairs for files to download.

    Files already present (non-empty) are skipped unless ``force`` is True, in
    which case every file is (re-)downloaded.
    """
    target_dir = Path(target_dir)
    plans = []
    for name in FILES:
        target = target_dir / name
        if not force and target.exists() and target.stat().st_size > 0:
            logger.info("skip (exists): %s", target)
            continue
        plans.append((BASE_URL + name, target))
    return plans


def is_truncated(size_bytes, min_size_bytes):
    """True when a downloaded file is below its truncation floor (partial download)."""
    return size_bytes < min_size_bytes


def sha256_of(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_readme(target_dir):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "README.md").write_text(
        "# german-wide-freight v3 (local-only raw data)\n\n"
        "Source: {url}\n\n"
        "Method: Lu, Martins-Turner, Nagel (2022), Procedia Computer Science 201,\n"
        "614-620, doi:10.1016/j.procs.2022.03.080. Long-haul freight trips for\n"
        "Germany from BMVI Verkehrsprognose 2030 NUTS-3 goods flows, calibrated\n"
        "against BASt HGV counts (average truck load 13 t). v3 departure times\n"
        "follow a BASt-derived start-time distribution (updates.txt, 2026-05-21).\n\n"
        "Downloaded by scripts/download_german_wide_freight.py on {today}.\n"
        "Do not commit these files; the eqasim-data tree is gitignored.\n".format(
            url=BASE_URL, today=date.today().isoformat()),
        encoding="utf-8")


def _download_atomic(url, target):
    """Download ``url`` to ``target`` atomically.

    The body is streamed to ``target + ".part"`` and only renamed onto the final
    path via ``os.replace`` after the write completes, so a crash or network drop
    mid-stream never leaves a partial file where the idempotency check would
    later treat it as a complete download. ``urlopen`` is time-bounded so a
    stalled connection cannot hang the run indefinitely.
    """
    part = target.with_name(target.name + ".part")
    logger.info("downloading %s -> %s", url, target)
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, \
            open(part, "wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)
    os.replace(part, target)
    logger.info("done: %s (%.1f MB)", target.name, target.stat().st_size / 1e6)


def download(target_dir=DEFAULT_TARGET):
    """Thin wrapper retained for the established call signature.

    Delegates to :func:`main` so the README, atomic download, truncation check
    and checksum rewrite all run. Returns the process exit code (0 on success,
    2 when a file is truncated).
    """
    return main(["--target-dir", str(target_dir)])


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET))
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download every file even if it already exists.",
    )
    args = parser.parse_args(argv)

    target_dir = Path(args.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    write_readme(target_dir)

    for url, target in plan_downloads(target_dir, force=args.force):
        _download_atomic(url, target)

    # Validate the truncation floor on EVERY file on EVERY run -- not only the
    # freshly downloaded ones -- so a pre-existing partial file from a prior
    # crashed run (which plan_downloads skipped because it is non-empty) is still
    # detected. On truncation, fail loudly with a non-zero exit code.
    truncated = False
    for name in FILES:
        target = target_dir / name
        if not target.exists():
            continue
        size = target.stat().st_size
        if is_truncated(size, MIN_SIZES[name]):
            print(
                "ERROR %s size %d < %d; download truncated?"
                % (name, size, MIN_SIZES[name]),
                file=sys.stderr,
            )
            truncated = True

    # Rewrite (truncate) the checksum log so it never accumulates stale or
    # duplicate lines: one line per file in FILES that currently exists.
    checksums = []
    for name in FILES:
        target = target_dir / name
        if target.exists():
            checksums.append("%s  %s" % (sha256_of(target), name))
    with open(target_dir / "sha256.txt", "w", encoding="utf-8") as f:
        if checksums:
            f.write("\n".join(checksums) + "\n")

    if truncated:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
