"""Dual-source loader for the synthetic population: either a finished eqasim run
output directory or the synpp simulation cache. MATSim does not change person /
household attributes, so both sources carry the synthesized attributes; the run
output additionally carries realised trips (not needed here)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.population_source")


@dataclass(frozen=True)
class PopulationFrames:
    persons: pd.DataFrame
    households: pd.DataFrame
    homes: gpd.GeoDataFrame
    vehicles: pd.DataFrame | None
    source_kind: str
    source_path: str
    prefix: str


def _detect_prefix(directory: Path) -> str:
    candidates = sorted(directory.glob("*_persons.csv"))
    if not candidates:
        raise FileNotFoundError(
            f"No *_persons.csv in {directory}; pass prefix explicitly."
        )
    return candidates[0].name[: -len("persons.csv")]


def _read_dir(directory: Path, prefix: str, kind: str) -> PopulationFrames:
    persons = pd.read_csv(directory / f"{prefix}persons.csv", sep=";")
    households = pd.read_csv(directory / f"{prefix}households.csv", sep=";")
    homes = gpd.read_file(directory / f"{prefix}homes.gpkg")
    vehicles_path = directory / f"{prefix}vehicles.csv"
    vehicles = pd.read_csv(vehicles_path, sep=";") if vehicles_path.exists() else None
    LOGGER.info(
        "Loaded population from %s (%s): persons=%d households=%d homes=%d vehicles=%s",
        directory, kind, len(persons), len(households), len(homes),
        "absent" if vehicles is None else len(vehicles),
    )
    return PopulationFrames(persons, households, homes, vehicles, kind, str(directory), prefix)


def _resolve_cache_dir(sim_cache: Path) -> Path:
    """Return the directory inside the synpp cache that holds the eqasim output
    files, picking the newest *_persons.csv match anywhere under the cache."""
    matches = sorted(sim_cache.rglob("*_persons.csv"), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(
            f"No *_persons.csv found anywhere under sim cache {sim_cache}."
        )
    return matches[-1].parent


def load_population(
    run_output_dir: str | None = None,
    sim_cache: str | None = None,
    prefix: str | None = None,
) -> PopulationFrames:
    """Load the synthetic population from exactly one source.

    Pass either ``run_output_dir`` (finished eqasim run) or ``sim_cache`` (synpp
    cache). Raises ``ValueError`` if neither or both are given.
    """
    if (run_output_dir is None) == (sim_cache is None):
        raise ValueError(
            "Pass exactly ONE of run_output_dir / sim_cache "
            f"(got run_output_dir={run_output_dir!r}, sim_cache={sim_cache!r})."
        )
    if run_output_dir is not None:
        directory = Path(run_output_dir).resolve()
        if not directory.is_dir():
            raise FileNotFoundError(f"run_output_dir does not exist: {directory}")
        return _read_dir(directory, prefix or _detect_prefix(directory), "run_output")

    cache_root = Path(sim_cache).resolve()
    if not cache_root.is_dir():
        raise FileNotFoundError(f"sim_cache does not exist: {cache_root}")
    directory = _resolve_cache_dir(cache_root)
    return _read_dir(directory, prefix or _detect_prefix(directory), "sim_cache")
