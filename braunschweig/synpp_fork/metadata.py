# braunschweig/synpp_fork/metadata.py
"""Read and write synpp's ``pipeline.json`` with synpp's own backup convention."""
from __future__ import annotations

import json
import os

from synpp.pipeline import update_json


def read_meta(working_directory: str) -> dict:
    path = os.path.join(working_directory, "pipeline.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_meta(meta: dict, working_directory: str) -> None:
    update_json(meta, working_directory)
