"""Check the four HBEFA emission-factor tables before they are used (post-run emissions, #688).

HBEFA tables are licensed and are never downloaded or committed: the user exports them from
the HBEFA software and names the four files in the configuration. This module only inspects
them. It fails early when a table is missing, empty, of the wrong kind, or one of the MATSim
*sample* tables. The sample tables ship inside the run jar
(``test/scenarios/emissions-sampleScenario/``) and hold deliberately altered values; a run on
them looks plausible and is meaningless, so they are refused by file name and by content hash.

Required columns: the ones the emissions contrib reads, as seen in the MATSim sample tables of
HBEFA 4.1. ASSUMPTION: a real 4.1 or 5.x export carries the same column names; if it does not,
the error names the missing column and this list is the place to adapt.

Nothing here reads or reports an emission factor.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Mapping

logger = logging.getLogger("braunschweig.emissions.hbefa_tables")

#: Table kinds; the keys of the mapping handed to :func:`inspect_hbefa_tables`.
TABLE_KINDS = ("cold_average", "warm_average", "cold_detailed", "warm_detailed")

REQUIRED_COLUMNS = {
    "warm_average": ("VehCat", "Component", "TrafficSit", "V_weighted", "EFA_weighted"),
    "cold_average": ("VehCat", "Component", "AmbientCondPattern", "EFA_weighted"),
    "warm_detailed": (
        "VehCat", "Component", "TrafficSit", "Technology", "SizeClasse", "EmConcept", "V", "EFA",
    ),
    "cold_detailed": (
        "VehCat", "Component", "AmbientCondPattern", "Technology", "SizeClasse", "EmConcept", "EFA",
    ),
}

#: File-name prefix of the MATSim sample tables.
SAMPLE_NAME_PREFIX = "sample_"

#: SHA-256 of the five sample tables in the pinned run jar (measured 2026-11-26).
SAMPLE_TABLE_SHA256 = frozenset({
    "2e5fbc18f5cb756bdf3a490494850be5cf68df1daabde6115329a03067b9d8e1",  # ColdStart_SubSegm_2020detailed
    "997a2372fb5b1b651733aa4313449bb1532823b94e5101ecfd9d62f1cddeb980",  # ColdStart_vehcat_2020average
    "8142df9b41008ad3ae70d7e958da5a9f7a7a90a668a47d9d64c2f2623a8381d6",  # HOT_HGV_2020detailed
    "9ad8494d5c23ddf4d2ffa07626375bfa401bb5d5a346a19aa725586ec8708274",  # HOT_SubSegm_2020detailed
    "fd34169f84d3de9a9bf831cf4843e68bb7a7d1d5b967acdc6299dd5c7a9f719d",  # HOT_vehcat_2020average
})

_DELIMITER = ";"
_UTF8_BOM = b"\xef\xbb\xbf"


def inspect_hbefa_tables(paths: Mapping[str, str | Path]) -> dict[str, dict]:
    """Validate the four tables and return, per kind, what identifies the file.

    Per kind: ``path`` (absolute), ``sha256``, ``size_bytes``, ``data_rows`` and ``case`` (the
    ``Case`` value of the first data row, which names the HBEFA export, e.g. its version).
    Raises ``ValueError`` for a missing kind, a table given twice, a sample table, a missing
    column or a table without data rows, and ``FileNotFoundError`` for a missing file.
    """
    missing = [kind for kind in TABLE_KINDS if not paths.get(kind)]
    if missing:
        raise ValueError(
            f"HBEFA table paths missing for {missing}. HBEFA is licensed: export the tables from the "
            "HBEFA software and set the paths; they are never downloaded."
        )
    resolved = {kind: Path(paths[kind]).resolve() for kind in TABLE_KINDS}
    seen: dict[Path, str] = {}
    for kind, path in resolved.items():
        if path in seen:
            raise ValueError(f"HBEFA tables '{seen[path]}' and '{kind}' point at the same file {path}")
        seen[path] = kind
    result: dict[str, dict] = {}
    for kind, path in resolved.items():
        if not path.is_file():
            raise FileNotFoundError(f"HBEFA table '{kind}' not found: {path}")
        digest = _sha256(path)
        if path.name.startswith(SAMPLE_NAME_PREFIX) or digest in SAMPLE_TABLE_SHA256:
            raise ValueError(
                f"HBEFA table '{kind}' ({path}) is a MATSim sample table with deliberately altered "
                "values; emissions computed from it are meaningless. Use a real HBEFA export."
            )
        data_rows, case = _check_columns(kind, path)
        result[kind] = {
            "path": str(path),
            "sha256": digest,
            "size_bytes": path.stat().st_size,
            "data_rows": data_rows,
            "case": case,
        }
        logger.info(
            "[emissions.hbefa_tables] %s: %s (%d rows, case %r, sha256 %s)",
            kind, path, data_rows, case, digest[:12],
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_columns(kind: str, path: Path) -> tuple[int, str]:
    """Return ``(data rows, Case of the first data row)``; raise on a wrong header or no data."""
    # Latin-1 decodes any byte, and the column names are ASCII, so the header can be checked
    # without knowing the encoding of the export (values such as 'TØ' are not interpreted).
    with path.open("rb") as handle:
        header_bytes = handle.readline()
        first_row = handle.readline()
        data_rows = (1 if first_row.strip() else 0) + sum(1 for line in handle if line.strip())
    if header_bytes.startswith(_UTF8_BOM):
        header_bytes = header_bytes[len(_UTF8_BOM):]
    columns = [name.strip() for name in header_bytes.decode("latin-1").split(_DELIMITER)]
    absent = [name for name in REQUIRED_COLUMNS[kind] if name not in columns]
    if absent:
        raise ValueError(
            f"HBEFA table '{kind}' ({path}) lacks the columns {absent}; found {columns[:12]}. "
            f"Is this the right export for '{kind}' (delimiter '{_DELIMITER}')?"
        )
    if data_rows == 0:
        raise ValueError(f"HBEFA table '{kind}' ({path}) has a header but no data rows")
    case = ""
    if "Case" in columns:
        values = first_row.decode("latin-1").split(_DELIMITER)
        index = columns.index("Case")
        case = values[index].strip() if index < len(values) else ""
    return data_rows, case
