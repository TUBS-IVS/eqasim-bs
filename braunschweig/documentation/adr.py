"""Load the individual ADR records under docs/decisions/.

The 2026-08-13 migration (ADR-0077) split the monolithic docs/DECISIONS.md into
one file per record, preserving ids, bodies and the historic heading variants
byte-for-byte (only the heading level was normalized to '#'). Three organically
grown heading forms therefore exist and are all parsed:

    # ADR-0040 · 2026-06-28 · Title
    # ADR-0049 — Title                       (date in the body or absent)
    # ADR-0061 — Title (2026-07-14, PR ...)

ADR-0051 is reserved (drafted on the unmerged fleet branch) and has no file;
docs/decisions/README.md carries the numbering notes.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional

from braunschweig.documentation.schema import SchemaError

DECISIONS_DIRECTORY = os.path.join("docs", "decisions")

_FILENAME = re.compile(r"^(ADR-\d{4})-[a-z0-9-]+\.md$")
_HEADING = re.compile(r"^# (ADR-\d{4})\s*[·—-]\s*(.+)$")
_DATE_PREFIX = re.compile(r"^(\d{4}(?:-\d{2}){0,2})\s*·\s*")
_DATE_SUFFIX = re.compile(r"\((\d{4}(?:-\d{2}){0,2})[,)]")
_STATUS = re.compile(r"^- \*\*Status:\*\*\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class AdrRecord:
    """One architecture decision record (pointer + parsed heading facts)."""

    id: str
    title: str
    date: Optional[str]
    status: Optional[str]
    path: str  # repo-relative POSIX path


def parse_adr(text: str, source_file: str) -> AdrRecord:
    first_line = text.split("\n", 1)[0].strip()
    match = _HEADING.match(first_line)
    if not match:
        raise SchemaError(
            f"{source_file}: first line is not an ADR heading ('# ADR-NNNN ...'): "
            f"{first_line!r}")
    adr_id, rest = match.group(1), match.group(2)

    filename_match = _FILENAME.match(os.path.basename(source_file))
    if not filename_match or filename_match.group(1) != adr_id:
        raise SchemaError(
            f"{source_file}: file name must be '{adr_id}-<slug>.md' matching the "
            "heading id")

    date = None
    prefix = _DATE_PREFIX.match(rest)
    if prefix:
        date = prefix.group(1)
        title = rest[prefix.end():].strip()
    else:
        title = rest.strip()
        suffix = _DATE_SUFFIX.search(rest)
        if suffix:
            date = suffix.group(1)

    status_match = _STATUS.search(text)
    status = status_match.group(1).strip() if status_match else None

    return AdrRecord(id=adr_id, title=title, date=date, status=status,
                     path=source_file.replace(os.sep, "/"))


def load_adrs(repo_root: str, directory: str = DECISIONS_DIRECTORY) -> List[AdrRecord]:
    """Load every ADR record, sorted by id; duplicate ids are an error."""
    absolute = os.path.join(repo_root, directory)
    if not os.path.isdir(absolute):
        raise FileNotFoundError(f"ADR directory not found: {absolute}")

    records = {}
    for name in sorted(os.listdir(absolute)):
        if not name.endswith(".md") or name == "README.md":
            continue
        path = os.path.join(absolute, name)
        with open(path, encoding="utf-8") as f:
            record = parse_adr(f.read(), os.path.join(directory, name))
        if record.id in records:
            raise SchemaError(f"duplicate ADR id {record.id} ({name})")
        records[record.id] = record
    return [records[key] for key in sorted(records)]


def adr_ids(repo_root: str) -> set:
    return {record.id for record in load_adrs(repo_root)}
