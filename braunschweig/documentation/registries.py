"""Load the model registries and run manifests from their YAML directories.

Directory layout (repo-relative, one record per file, filename == record id):

    docs/registry/features/<feature>.yml
    docs/registry/stages/<stage>.yml
    docs/registry/data/<dataset>.yml
    docs/runs/<run_id>.yml

Loading is strict: any structural violation raises
:class:`braunschweig.documentation.schema.SchemaError` naming the offending file
and key, and a duplicate record id across files is an error. A missing directory
raises ``FileNotFoundError`` -- the registries are part of the repository, so an
absent directory means a broken checkout, not an empty registry.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, List

import yaml

from braunschweig.documentation import schema

logger = logging.getLogger("braunschweig")

FEATURES_DIRECTORY = os.path.join("docs", "registry", "features")
STAGES_DIRECTORY = os.path.join("docs", "registry", "stages")
DATA_DIRECTORY = os.path.join("docs", "registry", "data")
RUNS_DIRECTORY = os.path.join("docs", "runs")


def _load_directory(repo_root: str, directory: str, parser: Callable, id_key: str) -> List[dict]:
    absolute = os.path.join(repo_root, directory)
    if not os.path.isdir(absolute):
        raise FileNotFoundError(f"registry directory not found: {absolute}")

    records = []
    seen = {}
    for name in sorted(os.listdir(absolute)):
        if not name.endswith(".yml"):
            continue
        path = os.path.join(absolute, name)
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        record = parser(doc, os.path.join(directory, name).replace(os.sep, "/"))
        record_id = record[id_key]
        if record_id in seen:
            raise schema.SchemaError(
                f"duplicate {id_key} '{record_id}' in {path} and {seen[record_id]}")
        seen[record_id] = path
        records.append(record)

    logger.info("[documentation] loaded %d record(s) from %s", len(records), directory)
    return records


def load_features(repo_root: str, directory: str = FEATURES_DIRECTORY) -> List[dict]:
    """Load every feature declaration, sorted by file name."""
    return _load_directory(repo_root, directory, schema.parse_feature, "feature")


def load_stages(repo_root: str, directory: str = STAGES_DIRECTORY) -> List[dict]:
    """Load every stage record, sorted by file name."""
    return _load_directory(repo_root, directory, schema.parse_stage, "stage")


def load_data(repo_root: str, directory: str = DATA_DIRECTORY) -> List[dict]:
    """Load every dataset record, sorted by file name."""
    return _load_directory(repo_root, directory, schema.parse_dataset, "dataset")


def load_manifests(repo_root: str, directory: str = RUNS_DIRECTORY) -> List[dict]:
    """Load every run manifest, sorted by file name."""
    return _load_directory(repo_root, directory, schema.parse_manifest, "id")
