"""Extract Zensus 2022 pupils and students without paid work, age 20 to 29, per Kreis, Land, Germany.

Issue #140 (student commuting), Phase 0 reference table R3. The Zensus 2022 table ``2000S-2001``
("Persons: age (10-year age bands) ... - activity status (in detail)") counts, per region, the
persons in the category "Schueler/-innen u. Studierende (nicht erwerbsaktiv)" -- pupils and
students who are NOT gainfully active. Students in paid work are in other categories, so the
count is a spatial PATTERN of students, not a level (an assumption of the design of this work,
issue #140, to be recorded in an ADR in the next phase).

Source layout (verified on the files delivered by the census database's data interface, one
file per region level, all in ``--source-dir``):

* ``2000S-2001_ALTGR2_GEOLK4.json`` (Kreise, 5-digit codes), ``..._GEOBL1.json`` (Laender,
  2-digit codes), ``..._GEODL1.json`` (Germany, the single code ``DG``);
* each file is ``{"data": [dataset, ...]}``. A dataset has ``id`` (dimension order), ``size``,
  ``dimension[<name>].category.index`` (category code -> position), a flat row-major ``value``
  array in the order of ``id`` and a ``status`` array of the same length. One dataset crosses the
  detailed activity status ``ERWST4`` (category ``ERW-AKTIV-X`` = students), the other the
  coarse status ``ERWST3`` (category ``%TOTAL%`` = all persons); both cross the age group
  ``ALTGR2`` (category ``ALT020B029`` = 20 to 29 years, so no summing of groups is needed);
* ``2000S-2001_structure.json`` holds the German labels in ``variableValues``.

A withheld cell (statistical secrecy) appears as the value ``0`` with the status flag ``/``; every
published cell carries the flag ``e``. A requested region whose students or all-persons cell is
not a positive count with the flag ``e`` is an error, never a silent zero. A PUBLISHED count of
zero (flag ``e``) is rejected together with the withheld cells (the check is ``value <= 0``): the
census counts are rounded by the publisher, and a region with no students aged 20 to 29 would be
implausible for a Kreis, so a zero signals a broken extraction rather than a real count.

Output CSV (``#``-prefixed provenance header, then): ``region, region_name, region_level,
age_group, students_nonemployed_persons, all_persons``. The file is local-only (``.gitignore``
data policy; Data Registry record ``zensus2022_students_20_29_kreis``).

Usage::

    python scripts/extract_zensus2022_students_by_age.py \\
        --source-dir <folder with the four 2000S-2001 JSON files> \\
        --out eqasim-data/data/braunschweig/popsim/zensus2022_students_20_29_kreis.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("extract_zensus2022_students_by_age")

TABLE_CODE = "2000S-2001"
STATUS_CATEGORY = "Schüler/-innen u. Studierende (nicht erwerbsaktiv)"
REFERENCE_DATE = "2022-05-15"
OUTPUT_PATH = REPO_ROOT / "eqasim-data/data/braunschweig/popsim/zensus2022_students_20_29_kreis.csv"

STUDENT_STATUS_CODE = "ERW-AKTIV-X"
DETAILED_STATUS_VARIABLE = "ERWST4"
COARSE_STATUS_VARIABLE = "ERWST3"
TOTAL_CODE = "%TOTAL%"
AGE_VARIABLE = "ALTGR2"
AGE_CODE_20_29 = "ALT020B029"
AGE_GROUP = "20-29"
COUNT_CONTENT_MARKER = "ID0004"   # the count; the percentage content code carries PO0004
NORMAL_STATUS = "e"
WITHHELD_STATUS = "/"

#: Region level -> (region variable of the file, file suffix, expected code length; ``None`` for
#: Germany, whose one code is checked against :data:`GERMANY_CODE` instead).
LEVELS = {
    "kreis": ("GEOLK4", "GEOLK4", 5),
    "land": ("GEOBL1", "GEOBL1", 2),
    "germany": ("GEODL1", "GEODL1", None),
}
GERMANY_CODE = "DG"
#: The eight ZGB Kreise, Land Niedersachsen and Germany.
DEFAULT_REGIONS = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158", "03", GERMANY_CODE)

_OUTPUT_COLUMNS = ["region", "region_name", "region_level", "age_group",
                   "students_nonemployed_persons", "all_persons"]


def _data_path(source_dir: Path, suffix: str) -> Path:
    return Path(source_dir) / f"{TABLE_CODE}_{AGE_VARIABLE}_{suffix}.json"


def _structure_path(source_dir: Path) -> Path:
    return Path(source_dir) / f"{TABLE_CODE}_structure.json"


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"census source file {path} is missing; fetch table {TABLE_CODE} "
                                "from the census database first.")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _find_dataset(document: dict, status_variable: str, path: Path) -> dict:
    found = [ds for ds in document["data"] if status_variable in ds["id"]]
    if len(found) != 1:
        raise ValueError(f"{path} has {len(found)} datasets with variable {status_variable}, "
                         "expected exactly one; the table layout changed.")
    return found[0]


def _cell(dataset: dict, region_variable: str, status_code: str, region: str, path: Path):
    """(value, flag) of the ``status_code`` x 20-29 x ``region`` cell, or raise if a key is absent."""
    status_variable = DETAILED_STATUS_VARIABLE if DETAILED_STATUS_VARIABLE in dataset["id"] \
        else COARSE_STATUS_VARIABLE
    wanted = {status_variable: status_code, AGE_VARIABLE: AGE_CODE_20_29, region_variable: region}
    position = 0
    for name, size in zip(dataset["id"], dataset["size"]):
        categories = dataset["dimension"][name]["category"]["index"]
        if name in wanted:
            code = wanted[name]
            if code not in categories:
                raise ValueError(f"{path}: category {code!r} of {name} is absent "
                                 f"(region {region}); the table layout changed.")
            step = categories[code]
        elif name == "content":
            counts = [c for c in categories if COUNT_CONTENT_MARKER in c]
            if len(counts) != 1:
                raise ValueError(f"{path}: expected one count content category containing "
                                 f"{COUNT_CONTENT_MARKER}, found {list(categories)}.")
            step = categories[counts[0]]
        else:
            if size != 1:
                raise ValueError(f"{path}: dimension {name} has {size} categories, expected 1.")
            step = 0
        position = position * size + step
    return dataset["value"][position], dataset["status"][position]


def _checked_count(value, flag, region: str, column: str) -> int:
    if flag != NORMAL_STATUS or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"region {region}: {column} is {value!r} with status flag {flag!r}, not a "
                         f"positive count with flag {NORMAL_STATUS!r} -- the census withholds such "
                         "cells (value 0, flag '/') and a reference without it would be wrong.")
    return int(value)


def extract_students_by_age(source_dir, regions: Optional[Iterable[str]]) -> pd.DataFrame:
    """Students without paid work and all persons, age 20-29, for ``regions`` (None = all in the files).

    ``source_dir`` holds the four JSON files of table 2000S-2001 (see the module docstring).
    Raises ``ValueError`` when a requested region is absent, when its students or all-persons cell
    is withheld or not a positive count, or when students exceed all persons;
    ``FileNotFoundError`` when a file is missing.
    """
    source_dir = Path(source_dir)
    structure_document = _load_json(_structure_path(source_dir))
    structure = structure_document["variableValues"]
    labels = {code: entry["label"]["de"] for code, entry in structure.items()}
    if labels.get(STUDENT_STATUS_CODE) != STATUS_CATEGORY:
        raise ValueError(f"{_structure_path(source_dir)}: label of {STUDENT_STATUS_CODE} is "
                         f"{labels.get(STUDENT_STATUS_CODE)!r}, expected {STATUS_CATEGORY!r}; the "
                         "category was renamed or recoded.")
    wanted = None if regions is None else [str(r) for r in regions]

    records = []
    seen = set()
    for level, (variable, suffix, code_length) in LEVELS.items():
        path = _data_path(source_dir, suffix)
        document = _load_json(path)
        detailed = _find_dataset(document, DETAILED_STATUS_VARIABLE, path)
        coarse = _find_dataset(document, COARSE_STATUS_VARIABLE, path)
        available = list(detailed["dimension"][variable]["category"]["index"])
        # Labels come from the region variable's own code list, not from the flat dictionary of
        # all variables, so a code shared by two variables cannot take the wrong label.
        own_codes = set(structure_document["variables"][variable]["variableValues"])
        for code in available:
            if level == "germany" and code != GERMANY_CODE:
                raise ValueError(f"{path}: unexpected Germany code {code!r}, expected {GERMANY_CODE!r}.")
            if code_length is not None and len(code) != code_length:
                raise ValueError(f"{path}: region code {code!r} is not {code_length} characters long.")
        for code in available:
            if wanted is not None and code not in wanted:
                continue
            if code not in own_codes or code not in labels:
                raise ValueError(f"region {code}: no label in the structure file under variable "
                                 f"{variable} ({_structure_path(source_dir)}).")
            seen.add(code)
            students = _checked_count(*_cell(detailed, variable, STUDENT_STATUS_CODE, code, path),
                                      code, "students_nonemployed_persons")
            everyone = _checked_count(*_cell(coarse, variable, TOTAL_CODE, code, path),
                                      code, "all_persons")
            if students > everyone:
                raise ValueError(f"region {code}: students_nonemployed_persons ({students}) exceed "
                                 f"all_persons ({everyone}); a column is mislabelled.")
            records.append((code, labels[code], level, AGE_GROUP, students, everyone))
    if wanted is not None:
        missing = [code for code in wanted if code not in seen]
        if missing:
            raise ValueError(f"regions {missing} are not in the {TABLE_CODE} files under {source_dir}.")
        order = {code: i for i, code in enumerate(wanted)}
        records.sort(key=lambda record: order[record[0]])
    frame = pd.DataFrame(records, columns=_OUTPUT_COLUMNS)
    for column in ("students_nonemployed_persons", "all_persons"):
        frame[column] = frame[column].astype("int64")
    logger.info("[zensus2022 students] %d regions extracted, all cells primary (published, flag %r); "
                "no fallback or exclusion path.", len(frame), NORMAL_STATUS)
    return frame


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_reference(frame: pd.DataFrame, destination, source_path) -> None:
    """Write ``frame`` with a ``#`` header naming the source files (``source_path`` = their folder)."""
    source_dir = Path(source_path)
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    files = [_data_path(source_dir, suffix) for _, suffix, _ in LEVELS.values()]
    files.append(_structure_path(source_dir))
    provenance = [
        f"Zensus 2022 table {TABLE_CODE}: persons in the category '{STATUS_CATEGORY}' and all "
        f"persons, age {AGE_GROUP}, per region (counts, not percentages).",
        f"reference date {REFERENCE_DATE}; the category excludes students in paid work, so it is "
        "a pattern, not a level.",
        "publisher: Statistische Aemter des Bundes und der Laender; licence: Datenlizenz "
        "Deutschland - Namensnennung - Version 2.0 (dl-de/by-2-0).",
    ]
    provenance += [f"source sha256 {path.name}: {_sha256(path)}" for path in files]
    provenance.append("written by scripts/extract_zensus2022_students_by_age.py (#140).")
    with open(destination, "w", encoding="utf-8", newline="") as handle:
        for line in provenance:
            handle.write(f"# {line}\n")
        frame.to_csv(handle, index=False, lineterminator="\n")


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source-dir", required=True,
                        help=f"folder with the four {TABLE_CODE} JSON files")
    parser.add_argument("--out", required=True, help="destination CSV")
    parser.add_argument("--regions", nargs="+", default=list(DEFAULT_REGIONS),
                        help="region codes (default: the 8 ZGB Kreise, Land 03, Germany DG)")
    args = parser.parse_args(argv)
    frame = extract_students_by_age(args.source_dir, args.regions)
    write_reference(frame, args.out, args.source_dir)
    print(f"[extract_zensus2022_students_by_age] wrote {len(frame)} regions to {args.out}")


if __name__ == "__main__":
    main()
