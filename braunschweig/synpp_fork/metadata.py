# braunschweig/synpp_fork/metadata.py
"""Read and write synpp's ``pipeline.json`` with synpp's backup convention (``pipeline.json.bk``).

``write_meta`` is atomic (spec §4.7): it writes ``pipeline.json.new`` completely, copies the old
file to ``pipeline.json.bk`` and then replaces ``pipeline.json`` with ``os.replace``, so a
``pipeline.json`` exists at every moment. synpp's own ``update_json`` moves the old file away
BEFORE writing the new one: a crash or a serialisation error in between leaves no
``pipeline.json`` at all, and synpp would then recompute every stage. The file content is the
same ``json.dump(meta)`` that synpp writes.
"""
from __future__ import annotations

import json
import os
import shutil


def read_meta(working_directory: str) -> dict:
    path = os.path.join(working_directory, "pipeline.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_meta(meta: dict, working_directory: str) -> None:
    path = os.path.join(working_directory, "pipeline.json")
    new_path = path + ".new"
    try:
        with open(new_path, "w", encoding="utf-8") as handle:
            json.dump(meta, handle)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if os.path.exists(new_path):
            os.remove(new_path)
        raise
    if os.path.exists(path):
        shutil.copy2(path, path + ".bk")
    os.replace(new_path, path)
