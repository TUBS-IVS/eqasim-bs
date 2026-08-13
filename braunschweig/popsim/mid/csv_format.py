"""MiD CSV field-separator detection for the popsim mid stage.

- ``detect_csv_separator``  -- infer ``,`` vs ``;`` from a CSV header line

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

This is a small leaf module (issue #267 task 4 ruling): ``detect_csv_separator``
has call sites in BOTH ``seed_loading.load_mid_seed`` (this task) and the donor
loaders ``load_mid_attributes`` / ``load_mid_wege`` (task 5's ``donor.py``), so
it is a multi-module dependency rather than belonging to a single consumer. It
is therefore extracted here, and both later submodules import it from this
module directly (never from the package ``__init__``); it is also re-exported
from ``__init__.py`` so the public namespace is unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union


def detect_csv_separator(path: Union[str, Path]) -> str:
    """Detect the field separator of a MiD CSV from its header line.

    The MiD 2023 scientific-use delivery has been observed with BOTH separators:
    a comma-separated export (the ZGB regional sample used here) and a
    semicolon-separated German-locale export. Hard-coding one separator silently
    mis-parses the other -- the whole header collapses into a single column,
    which then fails much later with a misleading "missing required columns"
    error (observed for ``MiD2023_Wege.csv``). The separator is therefore
    detected from the header rather than assumed.

    Parameters
    ----------
    path:
        Path to the MiD CSV file.

    Returns
    -------
    str
        ``","`` if the header contains at least as many commas as semicolons,
        otherwise ``";"``.

    Raises
    ------
    ValueError
        If the header line contains neither ``,`` nor ``;`` (so no separator can
        be inferred and a silent mis-parse must be avoided).
    """
    with open(path, "r", encoding="utf-8") as handle:
        header = handle.readline()
    n_comma = header.count(",")
    n_semicolon = header.count(";")
    if n_comma == 0 and n_semicolon == 0:
        raise ValueError(
            f"Cannot detect a ',' or ';' field separator in the header of {path}: "
            f"{header[:120]!r}. The MiD CSV delivery must be comma- or "
            "semicolon-separated."
        )
    return "," if n_comma >= n_semicolon else ";"
