"""Read ``key=value`` pairs from the ``#`` provenance header of a committed CSV table.

The committed tables of the repository carry their provenance and a few derived scalars (for example
``omega`` of the student level table, ADR-0210 Decision 5) in leading ``#`` lines. This module is the
one reader of such a scalar; ``scripts/build_student_balance.py`` and the PopulationSim stage
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
