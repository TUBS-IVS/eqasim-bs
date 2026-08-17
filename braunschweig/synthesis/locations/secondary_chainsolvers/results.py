"""Result mapping: chainsolvers solver output back to the eqasim schema.

``_extract_locations`` maps the solver's per-leg result rows back to
``(person_id, activity_index, location_id, geometry)``, folding every
internal subtype / escort / SrV category activity name back to its public
eqasim purpose and accounting per-problem placement completeness for the
fallback bookkeeping.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from .activity_types import (
    ESCORT_LOCATION_ACTIVITIES,
    LEISURE_SUBTYPE_ACTIVITIES,
    OTHER_SUBTYPE_ACTIVITIES,
    SHOP_SUBTYPE_ACTIVITIES,
)
from .plans import SECONDARY_PURPOSES
from .srv_location_types import SRV_LEISURE_CATEGORIES, SRV_OTHER_CATEGORIES


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------

def _extract_locations(result_df: pd.DataFrame,
                       problem_meta: List[Dict[str, Any]],
                       df_secondary: pd.DataFrame,
                       crs) -> Tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Convert chainsolvers result rows back to eqasim's output shape.

    Pure transform (no randomness): the returned ``df_locations`` (row order,
    person_id, activity_index, location_id, geometry) and ``df_convergence``
    (valid, size, order) are byte-identical to the previous per-row loop. The
    body is fully vectorised because at full population scale the per-row Python
    loop (str.split, int casts, pd.isna, Point construction over millions of
    result rows) was the dominant single-core cost of this stage.
    """
    # Build location_id -> (x, y) lookup; here only its key set is needed to
    # decide whether the solver's candidate id is a known facility (the
    # canonical id is kept verbatim) or must fall back to a synthesised id.
    known_location_ids = set(df_secondary["location_id"].astype(str))

    # Index meta by problem_idx for activity_index / person_id recovery.
    meta_by_idx = {m["problem_idx"]: m for m in problem_meta}

    n_rows = len(result_df)
    # Empty-result fast path: ``pd.DataFrame.from_records([], columns=...)``
    # yields all-object columns (no rows to infer dtypes from). Reproduce that
    # exact (dtype-included) empty shape so the output stays byte-identical.
    if n_rows == 0:
        df_locations = gpd.GeoDataFrame(
            pd.DataFrame.from_records(
                [],
                columns=["person_id", "activity_index", "location_id", "geometry"],
            ),
            crs=crs,
        )
        df_convergence = pd.DataFrame.from_records(
            [(0 == m["n_secondary"], m["n_secondary"]) for m in problem_meta],
            columns=["valid", "size"],
        )
        return df_locations, df_convergence

    leg_ids = result_df["unique_leg_id"].to_numpy()
    to_act = result_df["to_act_type"].to_numpy()
    to_x = pd.to_numeric(result_df["to_x"], errors="coerce").to_numpy()
    to_y = pd.to_numeric(result_df["to_y"], errors="coerce").to_numpy()
    if "to_act_identifier" in result_df.columns:
        identifiers = result_df["to_act_identifier"].to_numpy()
    else:
        identifiers = np.array([None] * n_rows, dtype=object)

    # Split "{person_id}#{problem_idx}#{leg_index}" into its three fields. The
    # previous loop unpacked ``leg_id.split("#")`` into exactly three targets
    # and skipped (ValueError) any id that did not have exactly three fields, so
    # ``rsplit("#", 2)`` (three fields from the right) is combined with a hard
    # "exactly two '#'" mask to reproduce that filtering precisely.
    leg_id_series = pd.Series(leg_ids, dtype=object)
    hash_count = leg_id_series.str.count("#").to_numpy()
    split = leg_id_series.str.rsplit("#", n=2, expand=True)
    prob_idx_str = split[1].to_numpy()
    leg_idx_str = split[2].to_numpy()

    # Row-level keep mask, applied as the conjunction of every per-row skip in
    # the original loop, evaluated in result-frame order so the surviving rows
    # keep their original order.
    valid_split = hash_count == 2  # exactly three fields -> no ValueError

    # int() casts only on the rows that survived the split filter; map remaining
    # values to NaN so a downstream cast cannot raise on the skipped rows.
    prob_idx_num = pd.to_numeric(pd.Series(prob_idx_str), errors="coerce").to_numpy()
    leg_idx_num = pd.to_numeric(pd.Series(leg_idx_str), errors="coerce").to_numpy()
    # A field that does not parse as an int would have raised in the old loop;
    # such ids never occur for solver output but are excluded for safety so the
    # behaviour is at least as strict (skip rather than crash on the bad row).
    valid_split = valid_split & ~np.isnan(prob_idx_num) & ~np.isnan(leg_idx_num)

    prob_idx_int = np.where(valid_split, prob_idx_num, -1).astype(np.int64)
    leg_idx_int = np.where(valid_split, leg_idx_num, -1).astype(np.int64)

    # meta lookup (skip rows whose problem_idx is unknown), secondary-purpose
    # filter, and the NaN-coordinate filter -- all the original per-row skips.
    known_prob = np.array(
        [valid_split[i] and (prob_idx_int[i] in meta_by_idx) for i in range(n_rows)]
    )
    # Tier 2 / Task 4: the internal subtype activities (shop_daily/non_daily;
    # leisure_local/visit/activity/excursion; other_errand_short/long,
    # other_escort) are secondary too -- they map back to the eqasim "shop" /
    # "leisure" / "other" purpose respectively. Include them here so a
    # subtype-tagged leg is not silently dropped at extraction. The subtype
    # label never reaches the output schema (which carries no purpose:
    # [person_id, activity_index, location_id, geometry]); this is the implicit
    # map-back ("other_rest" needs no entry here: it is never a chainsolver
    # activity name, see _build_other_subtype_decider). Issue #201:
    # ESCORT_LOCATION_ACTIVITIES (the drawn location-TYPE names) map back to
    # the eqasim "escort" purpose the same implicit way. Issue #262: the drawn
    # SrV location categories (SRV_LEISURE_CATEGORIES / SRV_OTHER_CATEGORIES)
    # map back to "leisure" / "other" by exactly the same mechanism -- they are
    # chainsolver-internal placement activities that never reach the output
    # schema. The two aggregate-placement categories ("leisure_misc",
    # "other_misc") never appear as a to_act_type (SRV_AGGREGATE_PLACEMENT
    # resolves them to the plain purpose in the leg loop), so their membership
    # here is inert -- listing the full vocabulary keeps this set in lockstep
    # with the category constants instead of encoding that indirection twice.
    secondary_acts = (
        set(SECONDARY_PURPOSES)
        | set(SHOP_SUBTYPE_ACTIVITIES)
        | set(LEISURE_SUBTYPE_ACTIVITIES)
        | set(OTHER_SUBTYPE_ACTIVITIES)
        | set(ESCORT_LOCATION_ACTIVITIES)
        | set(SRV_LEISURE_CATEGORIES)
        | set(SRV_OTHER_CATEGORIES)
    )
    is_secondary = pd.Series(to_act, dtype=object).isin(secondary_acts).to_numpy()
    coords_present = ~(np.isnan(to_x) | np.isnan(to_y))

    keep = known_prob & is_secondary & coords_present

    if not keep.any():
        df_locations = gpd.GeoDataFrame(
            pd.DataFrame.from_records(
                [],
                columns=["person_id", "activity_index", "location_id", "geometry"],
            ),
            crs=crs,
        )
        df_convergence = pd.DataFrame.from_records(
            [(0 == m["n_secondary"], m["n_secondary"]) for m in problem_meta],
            columns=["valid", "size"],
        )
        return df_locations, df_convergence

    kept_prob_idx = prob_idx_int[keep]
    kept_leg_idx = leg_idx_int[keep]

    # person_id and activity_index come from the per-problem meta (person_id
    # and activity_index + leg_index). Python ints from meta keep the int64
    # output dtype identical to the old ``from_records`` path.
    person_id = np.array(
        [meta_by_idx[p]["person_id"] for p in kept_prob_idx], dtype=np.int64
    )
    activity_index = np.array(
        [meta_by_idx[p]["activity_index"] for p in kept_prob_idx], dtype=np.int64
    ) + kept_leg_idx

    # Recover the canonical eqasim location_id from the solver's candidate
    # identifier; fall back to a synthesised "cs_{prob}_{leg}" id when the id is
    # not a string or is not a known facility (identical to the loop's rule).
    kept_cand = identifiers[keep]
    location_id = [
        cand if (isinstance(cand, str) and cand in known_location_ids)
        else f"cs_{kept_prob_idx[i]}_{kept_leg_idx[i]}"
        for i, cand in enumerate(kept_cand)
    ]

    # Geometry built in one shot from the float coordinate arrays.
    geometry = gpd.points_from_xy(to_x[keep], to_y[keep])

    df_locations = gpd.GeoDataFrame(
        {
            "person_id": person_id,
            "activity_index": activity_index,
            "location_id": np.asarray(location_id, dtype=object),
            "geometry": geometry,
        },
        crs=crs,
    )

    # Placed secondary legs per problem index, via a single groupby/size over
    # the kept rows (each problem's secondary legs have distinct activity
    # indices, so the count equals the number of distinct placed activities --
    # identical to the previous per-row accumulation).
    placed_per_prob: Dict[int, int] = (
        pd.Series(kept_prob_idx).value_counts().to_dict()
    )

    # Convergence flag in problem_meta order: valid iff all secondary legs of
    # the problem were placed. ``from_records`` keeps the bool/int64 dtypes
    # identical to the previous implementation.
    df_convergence = pd.DataFrame.from_records(
        [
            (placed_per_prob.get(m["problem_idx"], 0) == m["n_secondary"],
             m["n_secondary"])
            for m in problem_meta
        ],
        columns=["valid", "size"],
    )
    return df_locations, df_convergence
