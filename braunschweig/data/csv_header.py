"""Read ``key=value`` pairs from the ``#`` provenance header of a committed CSV table.

The committed tables of the repository carry their provenance and a few derived scalars (for example
``omega`` of the student level table, ADR-0210 Decision 5) in leading ``#`` lines. This module is the
one reader of such a scalar (and of a list-valued line, :func:`read_header_mapping`);``scripts/build_student_balance.py`` and the PopulationSim stage
(``braunschweig.popsim.student_level``) import it.
"""

from __future__ import annotations

import re
from pathlib import Path


def read_header_value(path, key: str) -> str:
    """The value of ``key=value`` anywhere in the leading ``#`` lines of ``path``.

    A value ends at a comma, a semicolon, a closing bracket or a space. List-valued keys such as
    ``mover_site_kreise`` are written for the reader and are not read back by this function. Raises ``ValueError`` when
    the key is absent or found more than once (the value would be ambiguous).
    """
    pattern = re.compile(r"(?:^|[\s,;(:])" + re.escape(key) + r"=([^\s,;)\]]+)")
    values = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            values.extend(pattern.findall(line))
    if not values:
        raise ValueError("%s: header key %r is absent" % (Path(path).name, key))
    if len(values) > 1:
        raise ValueError("%s: header key %r found more than once (%s)" % (Path(path).name, key, values))
    return values[0]


def read_header_mapping(path, label: str) -> dict[str, tuple[str, ...]]:
    """The list-valued line ``# <label>: name=a,b; name2=c`` of the leading ``#`` lines of ``path``.

    Returns ``{name: (a, b), name2: (c,)}`` with the values in file order. Used for the home areas of the
    SrV by-area table (``# Home areas: braunschweig=03101; surrounding=03102,03151``), which
    :func:`read_header_value` cannot read (it stops a value at a comma). Raises ``ValueError`` when the line
    is absent, found more than once, or malformed (an entry without ``=`` or without a value, a repeated
    name).
    """
    pattern = re.compile(r"^#\s*" + re.escape(label) + r":\s*(.*?)\s*$")
    lines = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("#"):
                break
            match = pattern.match(line)
            if match:
                lines.append(match.group(1))
    name = Path(path).name
    if not lines:
        raise ValueError("%s: header line '# %s: ...' is absent" % (name, label))
    if len(lines) > 1:
        raise ValueError("%s: header line '# %s: ...' found more than once" % (name, label))
    mapping: dict[str, tuple[str, ...]] = {}
    for entry in lines[0].split(";"):
        key, separator, values = entry.partition("=")
        key = key.strip()
        items = tuple(item.strip() for item in values.split(",") if item.strip())
        if not separator or not key or not items:
            raise ValueError("%s: header line '# %s: ...' has the malformed entry %r (expected name=a,b)"
                             % (name, label, entry.strip()))
        if key in mapping:
            raise ValueError("%s: header line '# %s: ...' names %r twice" % (name, label, key))
        mapping[key] = items
    return mapping
