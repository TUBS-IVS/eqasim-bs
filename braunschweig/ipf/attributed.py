"""IPF attributed output - sampled persons + household formation pass.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/attributed.py``.
Adapted for Braunschweig:
- Config keys renamed from ``bavaria.ipf.*`` to ``braunschweig.ipf.*``.
- Log message tags now read ``[braunschweig.ipf.attributed]`` (mechanical rename).
- No behavioural change otherwise (Phase 2.6 is relocation-only per D-5).
"""
from tqdm import tqdm
import pandas as pd
import numpy as np

"""
This stage adds additional attributes to the generated synthetic population from IPF.

When the IPF was run with the ``hh_size`` margin enabled
(``braunschweig.ipf.use_household_size_margin``), this stage also performs a
**household-formation pass**: the per-person ``hh_size`` cell label
("1", "2", … "6+") is consumed to group consecutive persons inside the
same ``(commune_id, hh_size)`` bucket into actual households of the
target size. Output rows are sorted by ``household_id`` with all members
of a household sharing identical ``weight`` so that the downstream
stochastic rounding in ``synthesis.population.sampled`` keeps the
household intact.
"""

# Mapping from the IPF's hh_size cell label to the integer count used by
# synthesis.population.sampled. The ``6+`` bin is mapped to 6 — this is a
# slight under-count of >6-person households (~0.5 % of all households in
# ZGB) but avoids modelling the open-ended tail.
_HH_SIZE_INT = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6+": 6}


def _form_households(df: pd.DataFrame, random_seed: int) -> pd.DataFrame:
    """Group persons into households of size N from the IPF ``hh_size`` cell.

    The IPF emits one row per (commune, sex, age, employed, license,
    hh_size) cell with fractional ``weight`` (= persons in the cell). To
    materialise discrete households we:

    1. Stochastic-round ``weight`` so each row represents an integer
       number of identical persons.
    2. Replicate each row by its integer count.
    3. Within every (commune_id, hh_size) bucket, shuffle deterministically
       (seeded) and chunk into groups of ``N`` consecutive rows.
    4. Drop the trailing partial chunk (≤ N-1 leftover persons per bucket
       — typically <0.5 % of the bucket); demographic plausibility of the
       grouping is left to downstream steps.
    5. Assign a fresh contiguous ``household_id``; set ``household_size``
       to the integer N; set ``weight`` to 1.0 (one discrete person per
       row).

    The output is sorted by ``household_id`` so consecutive rows belong to
    the same household — required by ``synthesis.population.sampled``.
    """
    rng = np.random.RandomState(random_seed)
    weights = df["weight"].to_numpy()
    floor = np.floor(weights).astype(np.int64)
    frac = weights - floor
    counts = floor + (rng.random_sample(len(weights)) < frac).astype(np.int64)
    repeated = df.iloc[np.repeat(np.arange(len(df)), counts)].reset_index(drop=True)

    repeated["_hh_size_int"] = (
        repeated["household_size"].astype(str).map(_HH_SIZE_INT).astype(np.int64)
    )
    if repeated["_hh_size_int"].isna().any():
        bad = repeated.loc[repeated["_hh_size_int"].isna(), "household_size"].unique()
        raise RuntimeError(f"Unknown hh_size labels in IPF output: {bad}")

    # Deterministic shuffle within each (commune_id, hh_size) bucket so
    # that the chunking does not produce e.g. a 6-person all-children
    # household just because the IPF emitted ages in sorted order.
    order = rng.permutation(len(repeated))
    repeated = repeated.iloc[order].reset_index(drop=True)
    repeated = repeated.sort_values(
        ["commune_id", "_hh_size_int"], kind="mergesort"
    ).reset_index(drop=True)

    idx_in_bucket = repeated.groupby(
        ["commune_id", "_hh_size_int"], sort=False
    ).cumcount().to_numpy()
    size_int = repeated["_hh_size_int"].to_numpy()
    # Keep only persons that fit into a complete household.
    bucket_size = repeated.groupby(
        ["commune_id", "_hh_size_int"], sort=False
    )["_hh_size_int"].transform("size").to_numpy()
    keep = idx_in_bucket < (bucket_size // size_int) * size_int
    dropped = int((~keep).sum())
    repeated = repeated.loc[keep].reset_index(drop=True)
    idx_in_bucket = idx_in_bucket[keep]
    size_int = size_int[keep]

    # household_id is unique per (commune, hh_size, chunk_index) tuple.
    chunk_idx = idx_in_bucket // size_int
    bucket_id = pd.factorize(
        list(zip(repeated["commune_id"].astype(str).to_numpy(), size_int))
    )[0]
    # Compose a household_id that is unique across buckets.
    n_chunks_per_bucket = (bucket_size[keep] // size_int)
    # Cumulative offset per bucket using first-occurrence ordering.
    first_index_of_bucket = pd.Series(bucket_id).drop_duplicates().index.to_numpy()
    bucket_chunk_count = (n_chunks_per_bucket[first_index_of_bucket]).astype(np.int64)
    bucket_offset = np.concatenate([[0], np.cumsum(bucket_chunk_count)[:-1]])
    repeated["household_id"] = bucket_offset[bucket_id] + chunk_idx
    repeated["household_size"] = size_int.astype(np.int64)
    repeated["weight"] = 1.0
    repeated = repeated.drop(columns=["_hh_size_int"])
    repeated = repeated.sort_values("household_id", kind="mergesort").reset_index(drop=True)
    repeated["person_id"] = np.arange(len(repeated))

    # Diagnostic — easy to grep in pipeline logs.
    n_hh = repeated["household_id"].nunique()
    n_persons = len(repeated)
    print(
        f"[braunschweig.ipf.attributed] formed {n_hh:,} households from "
        f"{n_persons:,} persons; dropped {dropped:,} persons in incomplete trailing chunks "
        f"({dropped / (n_persons + dropped):.2%})."
    )
    return repeated


# Mapping from the integer household_size emitted by ``_form_households``
# back to the canonical 6-bin label space used by Zensus 1000A-2081 /
# 1000A-3082 (with the open "6+" bin). Must stay aligned with
# ``_HH_SIZE_INT`` above.
_INT_TO_HH_SIZE_BIN = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6+"}


def _assign_household_types(
    df: pd.DataFrame,
    df_household_type: pd.DataFrame,
    random_seed: int,
) -> pd.DataFrame:
    """Draw an ``hh_type`` per household from Zensus 2022 1000A-2081 shares.

    ``df_household_type`` schema: ``commune_id, hh_size, hh_type, weight``
    (households per cell, ``hh_type`` ∈ ``{single, couple,
    couple_with_children, single_parent, other_multi}``).

    For each household formed in ``_form_households`` we sample
    ``hh_type`` proportional to ``weight`` within its
    ``(commune_id, hh_size)`` bucket. Households whose bucket has zero
    total weight in Zensus (very small communes with all cells
    suppressed) fall back to the scope-wide hh_size-only distribution;
    if even that is empty, the safe default ``"other_multi"`` is used.

    The same ``hh_type`` is replicated to all members of the household
    so that downstream stages can read the attribute from any person row.
    """
    rng = np.random.RandomState(random_seed + 31337)

    df_zt = df_household_type.copy()
    df_zt["commune_id"] = df_zt["commune_id"].astype(str)
    df_zt["hh_size"] = df_zt["hh_size"].astype(str)
    df_zt["hh_type"] = df_zt["hh_type"].astype(str)
    df_zt["weight"] = df_zt["weight"].astype(float)

    # Scope-wide fallback shares per hh_size (used when a commune cell
    # is fully suppressed in 1000A-2081).
    fallback = (
        df_zt.groupby(["hh_size", "hh_type"], observed=True)["weight"]
        .sum().reset_index()
    )
    fb_total = fallback.groupby("hh_size", observed=True)["weight"].transform("sum")
    fallback["share"] = np.where(fb_total > 0, fallback["weight"] / fb_total, 0.0)

    # One row per household, indexed contiguously 0..N-1.
    hh_df = (
        df.drop_duplicates("household_id")[
            ["household_id", "commune_id", "household_size"]
        ]
        .copy()
        .reset_index(drop=True)
    )
    hh_df["commune_id"] = hh_df["commune_id"].astype(str)
    hh_df["hh_size_bin"] = (
        hh_df["household_size"].astype(int).map(_INT_TO_HH_SIZE_BIN)
    )
    if hh_df["hh_size_bin"].isna().any():
        bad = sorted(hh_df.loc[hh_df["hh_size_bin"].isna(), "household_size"].unique())
        raise RuntimeError(
            f"[braunschweig.ipf.attributed] unmapped household_size in hh_type "
            f"assignment: {bad} (expected ints 1..6)"
        )

    types_arr = np.empty(len(hh_df), dtype=object)
    n_fallback = 0
    n_default = 0
    for (cid, hsb), g in hh_df.groupby(
        ["commune_id", "hh_size_bin"], sort=False, observed=True,
    ):
        local = df_zt[(df_zt["commune_id"] == cid) & (df_zt["hh_size"] == hsb)]
        local_sum = float(local["weight"].sum())
        if local_sum > 0:
            probs = local["weight"].to_numpy(dtype=float)
            probs /= probs.sum()
            choices = local["hh_type"].to_numpy()
        else:
            fb = fallback[fallback["hh_size"] == hsb]
            fb_sum = float(fb["weight"].sum())
            if fb_sum > 0:
                probs = fb["weight"].to_numpy(dtype=float)
                probs /= probs.sum()
                choices = fb["hh_type"].to_numpy()
                n_fallback += len(g)
            else:
                probs = None
                choices = None
                n_default += len(g)
        if choices is None:
            sampled = np.array(["other_multi"] * len(g), dtype=object)
        else:
            sampled = rng.choice(choices, size=len(g), p=probs)
        types_arr[g.index.to_numpy()] = sampled

    hh_df["hh_type"] = types_arr.astype(str)

    df = df.merge(hh_df[["household_id", "hh_type"]], on="household_id", how="left")
    if df["hh_type"].isna().any():
        n_missing = int(df["hh_type"].isna().sum())
        raise RuntimeError(
            f"[braunschweig.ipf.attributed] {n_missing:,} persons missing hh_type "
            "after merge — check household_id integrity."
        )
    df["hh_type"] = df["hh_type"].astype("category")

    n_hh = len(hh_df)
    type_counts = (
        hh_df["hh_type"].value_counts(normalize=True).sort_index().to_dict()
    )
    print(
        f"[braunschweig.ipf.attributed] assigned hh_type to {n_hh:,} households; "
        f"{n_fallback:,} drawn from scope-wide fallback, "
        f"{n_default:,} forced to 'other_multi'; share = "
        + ", ".join(f"{k}={v:.1%}" for k, v in type_counts.items())
    )
    return df


def configure(context):
    context.stage("braunschweig.ipf.model")
    context.config("random_seed")
    context.config("braunschweig.ipf.use_household_size_margin", False)
    # Optional household-type assignment from Zensus 2022 1000A-2081
    # (commune × hh_size × hh_type, in households). Drawn after the
    # household-formation pass; one ``hh_type`` per ``household_id`` is
    # sampled proportional to the Zensus shares within its
    # (commune_id, hh_size) bucket. Requires use_household_size_margin.
    context.config("braunschweig.ipf.use_household_type_margin", False)
    if context.config("braunschweig.ipf.use_household_type_margin"):
        context.stage("braunschweig.data.census.households_type")

def execute(context):
    df = context.stage("braunschweig.ipf.model")
    use_hh_size = context.config("braunschweig.ipf.use_household_size_margin")
    use_hh_type = context.config("braunschweig.ipf.use_household_type_margin")
    if use_hh_type and not use_hh_size:
        raise RuntimeError(
            "[braunschweig.ipf.attributed] use_household_type_margin requires "
            "use_household_size_margin to be enabled (hh_type is drawn "
            "within each (commune_id, hh_size) bucket)."
        )

    # Identifiers
    df["person_id"] = np.arange(len(df))
    df["household_id"] = np.arange(len(df))

    # Spatial
    df["commune_id"] = df["commune_id"].astype(str)
    df["iris_id"] = df["commune_id"] + "0000"
    df["iris_id"] = df["iris_id"].astype("category")

    # Fixed attributes
    df["work_outside_region"] = False
    df["education_outside_region"] = False
    df["consumption_units"] = 1.0

    # Household size: when the IPF balanced a hh_size margin, propagate the
    # cell label as the (per-person) household-size attribute. Otherwise
    # fall back to the legacy placeholder value of 1 — the placeholder is
    # subsequently overwritten by ``bavaria.synthesis.population.enriched``
    # which samples from the regions-aggregated distribution.
    if "hh_size" in df.columns:
        df["household_size"] = df["hh_size"].astype(str)
        df = df.drop(columns=["hh_size"])
    else:
        df["household_size"] = 1

    df["couple"] = False
    df["studies"] = False
    df["socioprofessional_class"] = 0

    # License
    df["has_license"] = df["license"]

    # Don't consider vehicle availability
    df["number_of_cars"] = 1
    df["number_of_bicycles"] = 1

    # Ignore PT subscription
    df["has_pt_subscription"] = False

    # Commute mode (is this important?)
    df["commute_mode"] = np.nan

    # Age distribution (we inflate the categories and distribute the ages uniformly in each group)
    initial_weight = df["weight"].sum()

    age_values = np.sort(df["age_class"].unique())
    MAXIMUM_AGE = 100

    df_age = []
    for k in range(len(age_values)):
        lower = age_values[k]
        upper = MAXIMUM_AGE if k == len(age_values) - 1 else age_values[k + 1]
        count = upper - lower

        df_age.append(pd.DataFrame({ 
            "age_class": [lower] * count,
            "age": lower + np.arange(count),
            "age_factor": [1.0 / count] * count
        }))

    df_age = pd.concat(df_age)

    df = pd.merge(df, df_age, on = "age_class")
    df["weight"] *= df["age_factor"]
    df = df.drop(columns = ["age_class", "age_factor"])

    df["person_id"] = np.arange(len(df))
    df["household_id"] = np.arange(len(df))

    final_weight = df["weight"].sum()
    assert np.abs(initial_weight - final_weight) < 1e-6

    if use_hh_size:
        # Group persons into real households according to the IPF hh_size cell.
        df = _form_households(df, context.config("random_seed"))

        if use_hh_type:
            df_household_type = context.stage(
                "braunschweig.data.census.households_type"
            )
            df = _assign_household_types(
                df, df_household_type, context.config("random_seed")
            )

    return df

