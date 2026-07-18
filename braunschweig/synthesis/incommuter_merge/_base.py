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


def assert_unique_ids(frames, id_col, label):
    """Raise ``ValueError`` if ``id_col`` has any duplicate value once pooled
    across ``frames``.

    Loud safety net against an id-block collision between independently
    offset in-commuter id ranges: SvB in-commuters occupy
    ``[n_residents, n_residents + n_svb)`` (``braunschweig.data.cordon.demand.
    make_incommuter_ids``) and student in-commuters are offset a further fixed
    ``+10_000_000`` above that base
    (``braunschweig.synthesis.student_incommuters._ID_OFFSET_ABOVE_RESIDENTS``,
    a documented ASSUMPTION that the SvB in-commuter count never approaches
    that block size). Nothing else enforces the two ranges stay disjoint, so
    a violation would silently merge two UNRELATED agents or households under
    one MATSim id -- unacceptable for research software (CLAUDE.md
    no-silent-fallbacks / no-silent-corruption). Call this after concatenating
    every configured in-commuter source, on the id-bearing frames specifically
    (``concat_frame`` itself stays generic and is not the right place for a
    persons/households-specific invariant).

    Frames that are empty -- including the zero-column empty ``DataFrame`` a
    skipped in-commuter stage returns on its OFF/skip path (e.g.
    ``braunschweig.synthesis.student_incommuters._empty_frames``) -- contribute
    nothing, so the check is a safe no-op when a source is inactive.
    """
    columns = [frame[id_col] for frame in frames if len(frame) > 0 and id_col in frame.columns]
    if not columns:
        return
    pooled = pd.concat(columns, ignore_index=True)
    duplicated = pooled[pooled.duplicated(keep=False)]
    if duplicated.empty:
        return
    offending = sorted(duplicated.unique())
    shown = offending[:10]
    suffix = " (+more)" if len(offending) > 10 else ""
    raise ValueError(
        f"{label}: duplicate {id_col} values after the in-commuter merge: "
        f"{shown}{suffix}. This indicates an id-block collision between "
        "independently offset in-commuter id ranges (residents / SvB "
        "in-commuters / student in-commuters); check the offset assumptions "
        "(braunschweig.synthesis.student_incommuters._ID_OFFSET_ABOVE_RESIDENTS) "
        "before proceeding."
    )


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
