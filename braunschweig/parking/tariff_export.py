"""Build the parking tariff model JSON (schema 1) that the Java parking cost model reads (issue #249).

The tariff table (design spec section 5.3: money in euros, fee hours as decimal hours of the weekday) is
converted ONCE, here, into integer euro cents and integer seconds after midnight; the Java side reads only
the result (spec 5.4 contract, pinned by ``tests/test_parking_tariff_export.py``). Every row becomes a
``braunschweig.parking.cost.ZoneTariff``, whose construction validates it against its zone type, so an
inconsistent table fails at export instead of being priced wrongly in the simulation.

The model carries the assumptions register of spec section 7 as ``ASSUMPTION <id>: ...`` texts and the
provenance of its inputs (``sources``: path and LF-normalised sha256 of every input file, supplied by the
caller, which knows the paths). Nothing here reads the tariff table itself; the functions are pure except
``content_sha256`` (reads a file) and the two writers.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig.parking.cost import SECONDS_PER_DAY, ZONE_TYPES, ZoneTariff, not_applicable_fields

SCHEMA_VERSION = 1
CURRENCY = "EUR"
#: Assumption T1. The only rule implemented; the config key ``parking_terminal_stay_rule`` is reserved.
TERMINAL_STAY_RULE_UNTIL_FEE_END = "until_fee_end"
SUPPORTED_TERMINAL_STAY_RULES = (TERMINAL_STAY_RULE_UNTIL_FEE_END,)

SECONDS_PER_HOUR = 3600
CENTS_PER_EURO = 100
# A euro amount is a whole number of cents when euros * 100 lies this close to an integer; the slack only
# absorbs binary floating-point noise (1.80 * 100 = 180.00000000000003), never a real sub-cent amount.
_WHOLE_CENT_TOLERANCE = 1e-6

# Table column (euros) -> ZoneTariff field (cents).
EURO_COLUMNS = {"hourly_rate_eur": "hourly_rate_cents", "first_period_eur": "first_period_cents",
                "daily_cap_eur": "daily_cap_cents", "long_stay_product_eur": "long_stay_product_cents",
                "member_day_eur": "member_day_cents", "guest_day_eur": "guest_day_cents"}
# Table columns in whole minutes, same name in the table and in ZoneTariff.
MINUTE_COLUMNS = ("billing_unit_min", "free_if_stay_at_most_min", "first_period_min", "max_stay_min")
# Table column (decimal hours of the day) -> ZoneTariff field (seconds after midnight).
HOUR_COLUMNS = {"fee_start_h": "fee_start_s", "fee_end_h": "fee_end_s"}
#: The spec 5.3 columns this module reads. The others are not part of the model: workplace_class serves the
#: zone attachment (spec 3.4); name, municipality_ags, source_url, source_date, valid_from, fee_window_source
#: and notes document provenance in the table itself.
TARIFF_COLUMNS = ("zone_id", "zone_type", *EURO_COLUMNS, *MINUTE_COLUMNS, *HOUR_COLUMNS, "resident_exempt")
# ZoneTariff money/minute field -> the table column it is read from, so that row errors name the column.
_COLUMN_OF_FIELD = {**{field: column for column, field in EURO_COLUMNS.items()},
                    **{column: column for column in MINUTE_COLUMNS}}

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Assumption:
    """One row of the assumptions register (design spec section 7)."""

    assumption_id: str
    statement: str
    where_it_bites: str
    sensitivity: str

    def render(self) -> str:
        return (f"ASSUMPTION {self.assumption_id}: {self.statement}. Where it bites: {self.where_it_bites}. "
                f"Sensitivity: {self.sensitivity}.")


#: The assumptions register of the design spec section 7, in its order. The model JSON carries the rendered
#: texts, so every tariff file states the assumptions it is priced under.
ASSUMPTIONS_REGISTER = (
    Assumption("Z1", "Outside every zone parking is free", "Municipalities marked not_audited",
               "Register status; A/B by adding zones"),
    Assumption("D1", "The simulated day is an average weekday; Saturday/holiday windows are not modelled",
               "All zones", "none"),
    Assumption("T1", "A terminal stay pays until the fee window of the arrival day ends",
               "Non-home last activities in zones (rare)", "parking_terminal_stay_rule reserved"),
    Assumption("M1", "Maximum stay compares the chargeable duration; a longer stay buys the zone's long-stay "
               "product", "Ia, Peine, resident zones", "long_stay_product_eur per zone"),
    Assumption("A1", "The free share observed for current SrV car commuters applies to all workers/students "
               "of the class", "Work/education in paid zones", "parking_workplace_free_share_shift"),
    Assumption("C1", "Members (work/education on campus) pay the day product; passes are not modelled",
               "TU zones", "set member_day_eur to 0 in an arm"),
    Assumption("R1", "Residence inside a resident zone equals permit possession",
               "Non-home activities of residents in their own zone", "none (small)"),
    Assumption("H1", "Home activities are free everywhere", "All", "none"),
    Assumption("F1", "Fee windows without an ordinance/signage source are marked 'assumption' in the table",
               "Per zone", "documented per row"),
    Assumption("S1", "Each zone has ONE regime; mixed streets are digitised as separate zones or take the "
               "dominant regime with a note", "Per zone", "notes"),
)


def assumption_texts() -> list[str]:
    """The register as the ``assumptions`` list of the model: ``ASSUMPTION <id>: ...`` strings."""
    return [assumption.render() for assumption in ASSUMPTIONS_REGISTER]


def content_sha256(path) -> str:
    """sha256 hex digest of a committed text file as git stores it (``\\r\\n`` replaced by ``\\n``).

    Same convention as ``braunschweig.data.vrb.fare_model_export.content_sha256``: a Windows checkout with
    core.autocrlf holds CRLF, and hashing the LF-normalised bytes keeps the recorded provenance identical on
    every platform and equal to the committed content.
    """
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _is_empty(value) -> bool:
    """None, pandas NA, NaN or blank text: an empty tariff-table cell ("not applicable")."""
    if value is None or value is pd.NA:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (float, np.floating)) and math.isnan(value)


# The helpers below take ``where`` ("tariff row 'fx_sz'") so every message names the row and the column.
def _number(value, column: str, where: str) -> float | None:
    """A finite number from a table cell (numeric or text), None for an empty cell."""
    if _is_empty(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{where}: {column} must be a number, got the boolean {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {column} must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{where}: {column} must be finite, got {value!r}")
    return number


def _euros_to_cents(value, column: str, where: str) -> int | None:
    euros = _number(value, column, where)
    if euros is None:
        return None
    cents = int(round(euros * CENTS_PER_EURO))
    if abs(euros * CENTS_PER_EURO - cents) > _WHOLE_CENT_TOLERANCE:
        raise ValueError(f"{where}: {column} = {euros!r} EUR is not a whole number of cents")
    return cents


def _whole_minutes(value, column: str, where: str) -> int | None:
    minutes = _number(value, column, where)
    if minutes is None:
        return None
    if not minutes.is_integer():
        raise ValueError(f"{where}: {column} must be a whole number of minutes, got {value!r}")
    return int(minutes)


def _hours_to_seconds(value, column: str, where: str) -> int:
    hours = _number(value, column, where)
    if hours is None:
        raise ValueError(f"{where}: {column} is required (decimal hours of the day)")
    # Decimal hours cannot hold every minute exactly (08:20 = 8.3333...), so the fee boundary is the nearest
    # second; ZoneTariff then checks 0 <= start < end <= 86400.
    return int(round(hours * SECONDS_PER_HOUR))


def _flag(value, column: str, where: str) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError(f"{where}: {column} must be true or false, got {value!r}")


def _identifier(value, column: str, where: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{where}: {column} must be a non-empty text without surrounding whitespace, "
                         f"got {value!r}")
    return value


def _check_fee_window_columns(row: Mapping, fields: Mapping, where: str) -> None:
    """Report an invalid fee window in the table's decimal-hour columns; ``ZoneTariff`` reports seconds.

    Checked on the converted seconds, the values the model carries, so a window that rounding to whole seconds
    empties is caught as well.
    """
    if not 0 <= fields["fee_start_s"] < fields["fee_end_s"] <= SECONDS_PER_DAY:
        start_h = _number(row["fee_start_h"], "fee_start_h", where)
        end_h = _number(row["fee_end_h"], "fee_end_h", where)
        raise ValueError(f"{where}: fee_start_h = {start_h:g} h and fee_end_h = {end_h:g} h do not form a fee "
                         "window; the decimal hours of the weekday must satisfy 0 <= fee_start_h < fee_end_h <= 24 "
                         "(compared after rounding to whole seconds)")


def _check_empty_cells(fields: Mapping, where: str) -> None:
    """A column the zone type does not have (``cost.not_applicable_fields``) must be an empty cell."""
    zone_type = fields["zone_type"]
    if zone_type not in ZONE_TYPES:
        return  # not a fallback: ZoneTariff rejects the unknown zone type right after this check
    for field in not_applicable_fields(zone_type):
        if fields[field] is not None:
            raise ValueError(f"{where}: {_COLUMN_OF_FIELD[field]} does not apply to zone_type {zone_type!r}; "
                             "leave the cell empty")


def tariff_row_to_zone(row: Mapping) -> ZoneTariff:
    """Convert one tariff-table row (spec 5.3 columns; a dict or a pandas Series) into a ``ZoneTariff``.

    Euros become integer cents with ``int(round(eur * 100))`` (a value that is not a whole number of cents
    raises), minutes must be whole numbers, decimal hours become seconds after midnight with
    ``int(round(h * 3600))``, ``resident_exempt`` accepts booleans or the texts true/false (any case), and
    an empty cell (None, NaN or blank text) becomes None. Text cells are parsed as numbers, so a table read
    with ``dtype=str`` converts the same way. Raises ``ValueError`` for a missing column or a cell it cannot
    convert exactly; ``ZoneTariff`` then validates the tariff against its zone type. Two of those errors are
    reported in the table's own terms before ``ZoneTariff`` sees the row: an invalid fee window names
    ``fee_start_h`` and ``fee_end_h`` in hours, and a filled cell of a column the zone type does not have names
    that column and says to leave the cell empty.
    """
    missing = [column for column in TARIFF_COLUMNS if column not in row]
    if missing:
        raise ValueError(f"tariff row is missing the columns {missing}; spec 5.3 requires {list(TARIFF_COLUMNS)}")
    zone_id = _identifier(row["zone_id"], "zone_id", "tariff row")
    where = f"tariff row {zone_id!r}"
    fields = {"zone_id": zone_id, "zone_type": _identifier(row["zone_type"], "zone_type", where)}
    for column, field in EURO_COLUMNS.items():
        fields[field] = _euros_to_cents(row[column], column, where)
    for column in MINUTE_COLUMNS:
        fields[column] = _whole_minutes(row[column], column, where)
    for column, field in HOUR_COLUMNS.items():
        fields[field] = _hours_to_seconds(row[column], column, where)
    fields["resident_exempt"] = _flag(row["resident_exempt"], "resident_exempt", where)
    _check_fee_window_columns(row, fields, where)
    _check_empty_cells(fields, where)
    return ZoneTariff(**fields)


def zone_to_json(zone: ZoneTariff) -> dict:
    """The zone entry of the model JSON: every ``ZoneTariff`` field except ``zone_id``, which is its key."""
    fields = asdict(zone)
    del fields["zone_id"]
    return fields


def _check_snapshot_date(snapshot_date) -> str:
    try:
        parsed = datetime.date.fromisoformat(snapshot_date)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or parsed.isoformat() != snapshot_date:
        raise ValueError(f"snapshot_date must be an ISO date YYYY-MM-DD, got {snapshot_date!r}")
    return snapshot_date


def _check_sources(sources: Sequence[Mapping]) -> list[dict]:
    if not sources:
        raise ValueError("at least one source is required: the model must name the files it was built from")
    checked, seen = [], set()
    for source in sources:
        for key in ("source_id", "path", "sha256"):
            if not isinstance(source.get(key), str) or not source[key]:
                raise ValueError(f"source {dict(source)} needs a non-empty text {key!r}")
        if not _SHA256_HEX.fullmatch(source["sha256"]):
            raise ValueError(f"source {source['source_id']!r}: sha256 must be 64 lowercase hex characters, "
                             f"got {source['sha256']!r}")
        if "\\" in source["path"]:
            raise ValueError(f"source {source['source_id']!r}: path must use POSIX separators, got {source['path']!r}")
        if source["source_id"] in seen:
            raise ValueError(f"duplicate source_id {source['source_id']!r}")
        seen.add(source["source_id"])
        checked.append(dict(source))
    return checked


def build_tariff_model(tariffs: pd.DataFrame, *, snapshot_date: str, sources: Sequence[Mapping],
                       terminal_stay_rule: str = TERMINAL_STAY_RULE_UNTIL_FEE_END) -> dict:
    """The tariff model of spec 5.4 (schema 1) for a tariff table with the spec 5.3 columns.

    ``snapshot_date`` (ISO date) names the tariff state the table records. ``sources`` lists the input
    files as ``{"source_id", "path" (POSIX, repository-relative), "sha256" (see ``content_sha256``)}``;
    extra keys are kept. Only the terminal-stay rule ``until_fee_end`` (T1) exists. Raises ``ValueError``
    for an empty table, a missing column, a duplicate zone id, an invalid row or invalid sources. The
    returned dict is plain JSON data: integer cents and seconds, None for "not applicable".
    """
    if terminal_stay_rule not in SUPPORTED_TERMINAL_STAY_RULES:
        raise ValueError(f"terminal_stay_rule {terminal_stay_rule!r} is not implemented; supported: "
                         f"{SUPPORTED_TERMINAL_STAY_RULES}")
    _check_snapshot_date(snapshot_date)
    checked_sources = _check_sources(sources)
    missing = [column for column in TARIFF_COLUMNS if column not in tariffs.columns]
    if missing:
        raise ValueError(f"tariff table is missing the columns {missing}; spec 5.3 requires {list(TARIFF_COLUMNS)}")
    if tariffs.empty:
        raise ValueError("tariff table has no rows")
    zones: dict[str, dict] = {}
    for row in tariffs.to_dict(orient="records"):
        zone = tariff_row_to_zone(row)
        if zone.zone_id in zones:
            raise ValueError(f"tariff table has a duplicate zone_id {zone.zone_id!r}")
        zones[zone.zone_id] = zone_to_json(zone)
    return {
        "schema_version": SCHEMA_VERSION,
        "tariff_snapshot_date": snapshot_date,
        "currency": CURRENCY,
        "terminal_stay_rule": terminal_stay_rule,
        # D1: the fee windows are those of an average weekday.
        "weekday_only": True,
        "assumptions": assumption_texts(),
        "sources": checked_sources,
        "zones": zones,
    }


def write_json_document(path, document: Mapping) -> Path:
    """Write ``document`` as sorted-key, two-space-indented ASCII JSON with LF line endings.

    Bytes are written directly, so the file is identical on every platform; NaN or infinity raise instead of
    producing invalid JSON. An existing file at ``path`` is replaced; the parent directory must exist.
    """
    path = Path(path)
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    path.write_bytes(text.encode("ascii"))
    return path


def write_tariff_model(path, model: Mapping) -> Path:
    """Write the tariff model (see ``write_json_document`` for the byte format)."""
    return write_json_document(path, model)


def tariff_model_file_name(prefix: str, snapshot_date: str) -> str:
    """File name of the tariff model next to ``<prefix>config.xml``; it carries the tariff snapshot date."""
    return f"{prefix}parking_tariffs_{_check_snapshot_date(snapshot_date)}.json"


def inputs_report_name(prefix: str) -> str:
    """File name of the preparation report that lists the parking input files."""
    return f"{prefix}parking_inputs_report.json"
