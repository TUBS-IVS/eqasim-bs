#!/usr/bin/env python
"""Read the eight empty FZ 27.17 cells at their source and check the workbook against the committed tables.

The committed ``kba_gemeinde_private_bev.csv`` has eight empty BEV or PHEV cells. This script answers, from the
KBA workbook ``fz27_202501.xlsx`` itself and read-only (``openpyxl`` ``read_only`` and ``data_only``, nothing is
written next to the workbook and no formula runs):

1. Which file is it (name, size, sha256)?
2. What does the workbook's own legend ("Zeichenerklaerung") say each placeholder symbol means?
3. What is in the raw cells of the eight rows, and of the three kreisfreie Staedte?
4. Is it the workbook the committed tables came from? Every FZ 27 based extractor of
   ``scripts/extract_kba_fleet.py`` is re-run on it (in memory; the repository is not written) and its frame is
   compared, line endings normalised, with the committed CSV. The extractor's own coercion log (which placeholder
   symbols became NaN or 0, and how many) is part of the output.

Usage (from the repository root, with the project's Python environment, isolated so that nothing in the working
directory can shadow a module)::

    python -I docs/runs/artifacts/prod100-fleet-composition-accounting-2026-11-04/check_fz27_workbook.py <path to fz27_202501.xlsx>
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import logging
import sys
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def _quiet_load_workbook(*args, **kwargs):
    """``openpyxl.load_workbook`` without the stdout noise of openpyxl 3.1.0.

    That release prints the titles of five sheets of this workbook to stdout on every open (a stray print
    in the library: no warning is raised and it says nothing about the cells). It would repeat once per
    extractor in the output, so the print is swallowed; exceptions and the workbook are untouched.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        return _load_workbook(*args, **kwargs)


_load_workbook = openpyxl.load_workbook
openpyxl.load_workbook = _quiet_load_workbook

#: The eight rows with an empty cell in the committed table, and the three kreisfreie Staedte.
EMPTY_CELL_GEMEINDEN = ("WOLFSBURG", "HEDEPER", "BEIERSTEDT", "JERXHEIM", "QUERENHORST", "DAHLUM",
                        "UEHRDE", "WINNIGSTEDT")
KREISFREIE_STAEDTE = ("BRAUNSCHWEIG", "SALZGITTER", "WOLFSBURG")


def show_file(path: Path) -> None:
    data = path.read_bytes()
    print("workbook: %s, %d bytes, sha256 %s" % (path.name, len(data), hashlib.sha256(data).hexdigest()))


def show_legend(workbook) -> None:
    """The symbol legend of the sheet ``Inhaltsverzeichnis`` (rows after the heading)."""
    rows = list(workbook["Inhaltsverzeichnis"].iter_rows(values_only=True))
    for number, row in enumerate(rows):
        if any(isinstance(cell, str) and "zeichenerkl" in cell.lower() for cell in row):
            print("legend of the placeholder symbols (sheet Inhaltsverzeichnis):")
            for shown in rows[number + 1:number + 8]:
                cells = [cell for cell in shown if cell is not None]
                if cells:
                    print("  %r = %s" % (cells[0], cells[1] if len(cells) > 1 else ""))
            return
    print("legend not found")


def show_raw_cells(workbook) -> None:
    """Raw cells of col2 (Zulassungsbezirk), col3 (Gemeinde), col7 (private total), col8 (BEV), col9 (PHEV)."""
    print("sheet FZ 27.17, raw cells (sheet row: col3 Gemeinde | col7 private total, col8 private BEV, col9 private PHEV):")
    rows = list(workbook["FZ 27.17"].iter_rows(values_only=True))
    for wanted in KREISFREIE_STAEDTE + tuple(n for n in EMPTY_CELL_GEMEINDEN if n not in KREISFREIE_STAEDTE):
        hits = [(number, row) for number, row in enumerate(rows, start=1)
                if len(row) > 9 and isinstance(row[3], str) and row[3].strip().upper() == wanted]
        print("  %-13s %d row(s) in the sheet with this Gemeinde name" % (wanted, len(hits)))
        for number, row in hits:
            print("    row %d: col2=%r col3=%r | col7=%r col8=%r col9=%r" % (number, row[2], row[3], row[7], row[8], row[9]))


def compare_with_committed_tables(workbook_path: Path) -> None:
    """Re-run the FZ 27 extractors in memory and compare with the committed derived CSVs."""
    spec = importlib.util.spec_from_file_location("extract_kba_fleet",
                                                  REPO_ROOT / "scripts" / "extract_kba_fleet.py")
    ekf = importlib.util.module_from_spec(spec)
    sys.modules["extract_kba_fleet"] = ekf
    spec.loader.exec_module(ekf)
    # The extractor logs through logging.basicConfig (stderr); put its coercion log on the same stream as
    # the printed lines so the output keeps its order.
    for handler in logging.getLogger().handlers:
        handler.setStream(sys.stdout)
    ekf.FZ27_PATH = workbook_path
    derived = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "kba" / "derived"
    jobs = (
        ("kba_segment_powertrain.csv", ekf.extract_segment_powertrain),
        ("kba_kreis_powertrain.csv", ekf.extract_kreis_powertrain),
        ("kba_gemeinde_private_bev.csv", ekf.extract_gemeinde_private_bev),
        ("kba_fuel_euro_nds.csv", ekf.extract_fuel_euro_nds),
        ("kba_fuel_euro6_substage_nds.csv", ekf.extract_fuel_euro6_substage_nds),
        ("kba_age_fuel.csv", ekf.extract_age_fuel),
        ("kba_brand_powertrain.csv", ekf.extract_brand_powertrain),
    )
    print("re-run of the FZ 27 extractors on this workbook against the committed tables:")
    for name, function in jobs:
        frame = function()
        produced = frame.to_csv(index=False).replace("\r\n", "\n")
        committed = (derived / name).read_bytes().decode("utf-8").replace("\r\n", "\n")
        print("COMPARE %-34s %s (%d rows)" % (name, "IDENTICAL" if produced == committed else "DIFFERENT",
                                              len(frame)))


def main(path: str) -> None:
    workbook_path = Path(path)
    show_file(workbook_path)
    workbook = openpyxl.load_workbook(workbook_path, read_only=True, data_only=True)
    try:
        show_legend(workbook)
        show_raw_cells(workbook)
    finally:
        workbook.close()
    compare_with_committed_tables(workbook_path)


if __name__ == "__main__":
    main(sys.argv[1])
