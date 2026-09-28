"""Tests for braunschweig.popsim.unused_mid_wege_columns (ADR-0138).

The list names MiD 2023 Wege columns that no first-party code reads, so the trip build may drop
them before the persons x Wege join copies every Wege column once per synthetic trip. Two
properties make that safe, and both are pinned here: the drop removes exactly the listed columns
and nothing else, and no listed column is read anywhere in the repository (the repository scan
below fails the moment code starts reading one, naming the file and the column).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from braunschweig.popsim import unused_mid_wege_columns as unused

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFINING_MODULE = Path("braunschweig", "popsim", "unused_mid_wege_columns.py")
#: Directories that hold no first-party code the pipeline runs: tests (which may name listed
#: columns in fixtures), version control and caches, data, documentation, local worktrees.
SCAN_EXCLUDED_PARTS = frozenset({
    "tests", ".git", "__pycache__", ".claude", ".superpowers", "eqasim-data", "docs",
    "notebooks_archive", "node_modules", ".pytest_cache",
})
#: Code AND configuration: a config value naming a column is a read the Python scan cannot see.
SCAN_SUFFIXES = frozenset({".py", ".yml", ".yaml", ".toml", ".json", ".ipynb", ".cfg", ".ini",
                           ".sh", ".ps1"})
SCAN_MAX_FILE_BYTES = 5_000_000
#: A merge that suffixes a column (pandas' ``_x``/``_y``, the trip build's ``_weg``) still reads it.
MERGE_SUFFIXES = ("_weg", "_x", "_y")


def _first_party_files():
    for path in REPO_ROOT.rglob("*"):
        relative = path.relative_to(REPO_ROOT)
        if any(part in SCAN_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.suffix not in SCAN_SUFFIXES or not path.is_file():
            continue
        if relative == DEFINING_MODULE or path.stat().st_size > SCAN_MAX_FILE_BYTES:
            continue
        yield relative, path


def _wege_with(columns):
    return pd.DataFrame({column: [index, index + 1] for index, column in enumerate(columns)})


def test_drop_removes_the_listed_columns_and_keeps_every_other_column_in_order():
    listed = list(unused.UNUSED_MID_WEGE_COLUMNS[:3])
    kept = ["H_ID", "P_ID", "W_ID", "W_ZWECK", "hvm_imp", "wegkm_imp"]
    interleaved = [kept[0], listed[0], kept[1], kept[2], listed[1], kept[3], kept[4], listed[2],
                   kept[5]]
    wege = _wege_with(interleaved)

    narrowed = unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    assert list(narrowed.columns) == kept
    pd.testing.assert_frame_equal(narrowed, wege[kept])


def test_drop_leaves_the_input_frame_untouched():
    columns = ["H_ID", unused.UNUSED_MID_WEGE_COLUMNS[0], "W_ID"]
    wege = _wege_with(columns)
    before = wege.copy()

    unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    pd.testing.assert_frame_equal(wege, before)


def test_drop_ignores_listed_columns_the_delivery_does_not_carry():
    # A delivery (or a unit fixture) without any listed column passes through unchanged: the
    # list says which columns MAY be dropped, it is not a schema the input must satisfy.
    wege = _wege_with(["H_ID", "P_ID", "W_ID", "W_ZWECK"])

    narrowed = unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    pd.testing.assert_frame_equal(narrowed, wege)


def test_drop_logs_how_many_columns_it_dropped_and_how_many_it_kept(caplog):
    listed = list(unused.UNUSED_MID_WEGE_COLUMNS[:2])
    wege = _wege_with(["H_ID", listed[0], "P_ID", listed[1], "W_ID"])

    with caplog.at_level(logging.INFO, logger=unused.__name__):
        unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("[test]") and "dropped 2/5" in message and "kept 3" in message
               for message in messages), messages


def test_the_list_names_each_column_once():
    names = unused.UNUSED_MID_WEGE_COLUMNS
    assert len(names) == len(set(names))
    assert all(isinstance(name, str) and name for name in names)


def test_no_listed_column_is_read_by_first_party_code():
    """The safety argument of ADR-0138: a column nobody reads cannot change a result.

    Scans every first-party code and configuration file for an exact token of a listed column
    (also with a merge suffix). A hit means code now reads that column, so dropping it before the
    trip build would hand that code a frame without it: remove the column from
    ``UNUSED_MID_WEGE_COLUMNS`` in the same change.
    """
    names = "|".join(re.escape(name) for name in unused.UNUSED_MID_WEGE_COLUMNS)
    suffixes = "|".join(re.escape(suffix) for suffix in MERGE_SUFFIXES)
    token = re.compile(rf"(?<![A-Za-z0-9_])({names})(?:{suffixes})?(?![A-Za-z0-9_])")

    scanned_python_files = 0
    hits = []
    for relative, path in _first_party_files():
        scanned_python_files += path.suffix == ".py"
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for match in token.finditer(line):
                hits.append(f"{relative}:{number}: {match.group(1)!r} in {line.strip()[:100]!r}")

    # A scan that silently found nothing (wrong root, everything excluded) would pass vacuously.
    assert scanned_python_files > 300, scanned_python_files
    assert not hits, (
        "listed MiD Wege columns are read by first-party code; remove them from "
        "braunschweig.popsim.unused_mid_wege_columns.UNUSED_MID_WEGE_COLUMNS:\n"
        + "\n".join(hits))
