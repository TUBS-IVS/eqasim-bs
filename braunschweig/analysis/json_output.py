"""Strict-JSON output for analysis stages.

``json.dump`` writes ``NaN`` / ``Infinity`` literals by default, which are valid JavaScript but
not valid JSON: a strict parser (``json.loads`` with ``parse_constant`` raising, jq, most
non-Python readers) rejects the file. A missing measurement is exactly what an analysis stage
represents as ``NaN``, so those values must survive the round trip -- they become ``null``,
which every JSON reader understands as "no value", rather than being dropped or replaced by a
number.

Shared by ``braunschweig.analysis.synthesis.work_participation_by_kreis`` (``provenance.json``)
and ``braunschweig.analysis.cordon_validation`` (``commute_day_state_scaling.json``) so the two
cannot drift into two different notions of "safe JSON".
"""
from __future__ import annotations

import json
import math
import os

import numpy as np


def json_safe(value):
    """Recursively map a value to something STRICT JSON can represent.

    Numpy scalars are unwrapped to their Python equivalents on the way; non-finite floats
    (``NaN``, ``+/-Infinity``) become ``None``; anything else that is not a JSON primitive,
    list or dict is rendered with ``str`` rather than silently dropped.
    """
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def write_json(path, payload):
    """Write ``payload`` as strict JSON (``allow_nan=False``), creating the directory.

    Returns the path, so a caller can record it among its outputs in one expression.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(json_safe(payload), handle, indent=2, allow_nan=False)
    return path
