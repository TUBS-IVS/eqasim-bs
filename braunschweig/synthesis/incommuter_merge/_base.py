"""Shared concat logic for the in-commuter merge wrappers.

Each wrapper aliases a resident stage and appends the matching in-commuter frame
from ``braunschweig.synthesis.incommuters``. The in-commuter frame is aligned to the
resident columns (extra in-commuter columns dropped; resident-only columns become
NaN for in-commuter rows), so the combined frame keeps the schema downstream stages
expect. When ``cordon_enabled`` is False the in-commuter frame is empty -> the output
is the resident frame unchanged (byte-identical baseline). Wrappers reference RAW
upstream stage names to avoid an alias dependency cycle.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd


def concat_frame(resident, incommuter, sort_col):
    """Append ``incommuter`` (aligned to ``resident`` columns) and sort by ``sort_col``."""
    if incommuter is None or len(incommuter) == 0:
        return resident
    aligned = incommuter.reindex(columns=resident.columns)
    combined = pd.concat([resident, aligned], ignore_index=True)
    if isinstance(resident, gpd.GeoDataFrame):
        combined = gpd.GeoDataFrame(combined, geometry=resident.geometry.name,
                                    crs=resident.crs)
    return combined.sort_values(sort_col).reset_index(drop=True)


def make_wrapper(raw_stage, frame_key, sort_col, tuple_index=None):
    """Build (configure, execute) for a flag-gated concat-wrapper of ``raw_stage``.

    Appends ``braunschweig.synthesis.incommuters[frame_key]`` to the resident frame
    returned by ``raw_stage`` (or, if the stage returns a tuple, to element
    ``tuple_index``). OFF -> the resident stage is returned untouched.
    """
    def configure(context):
        context.config("cordon_enabled", False)
        context.stage(raw_stage)
        if context.config("cordon_enabled"):
            context.stage("braunschweig.synthesis.incommuters")

    def execute(context):
        resident = context.stage(raw_stage)
        if not context.config("cordon_enabled"):
            return resident
        incommuter = context.stage("braunschweig.synthesis.incommuters")[frame_key]
        if tuple_index is not None:
            parts = list(resident)
            parts[tuple_index] = concat_frame(parts[tuple_index], incommuter, sort_col)
            return tuple(parts)
        return concat_frame(resident, incommuter, sort_col)

    return configure, execute
