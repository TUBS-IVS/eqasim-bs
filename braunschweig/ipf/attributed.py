"""IPF attributed output - sampled persons + household formation pass.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/attributed.py``.
Adapted for Braunschweig:
- Config keys are ``braunschweig.ipf.*``; the legacy ``bavaria.ipf.*`` mapping
  was removed in Phase 4.3 along with the rest of the bavaria/ tree.
- Log message tags read ``[braunschweig.ipf.attributed]``.
- BUG-008 fix: ``commune_id`` is cast to a stable string before any sort or
  factorize call so household_id assignment is deterministic across runs.
"""
from tqdm import tqdm
import pandas as pd
import numpy as np

import braunschweig.ipf.household_composition as hc_module
from braunschweig.ipf.household_composition import build_bucket_households
from braunschweig.data.education.student_share import (
    build_share_lookup, share_for_age,
)

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

# Mapping from the IPF's hh_size cell label to the integer *target* size used by
# synthesis.population.sampled. The ``6+`` bin maps to a target of 6 (the
# open-ended tail is not modelled explicitly). Note that the *realised* size of a
# household can exceed its target: ``_form_households`` absorbs each bucket's
# trailing remainder into the last household, so a ``6+`` bucket can yield a
# size-7+ household, which ``_assign_household_types`` types from the ``6+``
# distribution (clip-to-6).
_HH_SIZE_INT = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6+": 6}


def _hh_size_to_int(household_size: "pd.Series") -> "pd.Series":
    """Map hh_size bin labels ("1".."5", "6+") to ints with a REACHABLE guard.

    The isna check must run BEFORE the int cast: ``.map(...).astype(np.int64)``
    on an unknown label raises pandas' cryptic ``IntCastingNaNError`` first,
    making the descriptive RuntimeError below dead code.
    """
    mapped = household_size.astype(str).map(_HH_SIZE_INT)
    if mapped.isna().any():
        bad = household_size[mapped.isna()].unique()
        raise RuntimeError(f"Unknown hh_size labels in IPF output: {bad}")
    return mapped.astype(np.int64)


# ---------------------------------------------------------------------------
# Person-attribute reactivation (Task A3): couple / studies / SPC.
#
# These three attributes were previously hardcoded to dead constants
# (couple=False, studies=False, socioprofessional_class=0) although the data to
# derive them already exists in the formed household and the synthesised age /
# employment. The derivations below are gated by the config flag
# ``reactivate_person_attributes`` (default True): with the flag OFF the legacy
# constants are kept, so the pipeline output is byte-identical to before.
# ---------------------------------------------------------------------------

# Household types whose adults form a couple. In a ``couple`` shell the two
# adults ARE the partners; in a ``couple_with_children`` shell the (>=2) adults
# are the partners and the children are not. ``single_parent`` / ``single`` /
# ``other_multi`` carry no couple relationship.
COUPLE_HOUSEHOLD_TYPES = frozenset({"couple", "couple_with_children"})

# eqasim/INSEE CS1 socioprofessional-class codes (see
# eqasim_common.analysis.marginals.SOCIOPROFESIONAL_CLASS_LABELS):
#   0 ??? 1 Agriculture 2 Independent 3 Science 4 Intermediate
#   5 Employee 6 Worker 7 Retired 8 Other (incl. students/children)
SPC_INDEPENDENT = 2
SPC_SCIENCE = 3
SPC_INTERMEDIATE = 4
SPC_EMPLOYEE = 5
SPC_WORKER = 6
SPC_RETIRED = 7
SPC_STUDENT = 8          # students fall in the inactive "Other" class (ENTD CS24 80 -> 8)
SPC_OTHER_INACTIVE = 8   # working-age inactive non-students also map to "Other"

# Statutory retirement age used to separate retired (-> SPC 7) from working-age
# inactive (-> SPC 8). Germany's Regelaltersgrenze is rising toward 67; 65 is a
# conservative threshold for classifying the inactive elderly.
SPC_RETIREMENT_AGE = 65


def derive_couple(hh_type, age, min_adult_age: int = 18):
    """Derive the boolean ``couple`` attribute per person from the realised
    household type (Task A3a).

    A person is in a couple iff they are an adult (``age >= min_adult_age``) AND
    their household type is ``couple`` or ``couple_with_children`` -- exactly the
    adults the household-composition pass paired as partners. Children in a
    couple-with-children household, and everyone in single / single_parent /
    other_multi households, are not in a couple.

    Args:
        hh_type: per-person household-type Series (string / category).
        age: per-person integer age Series.
        min_adult_age: adult threshold (matches the chunking ``min_adult_age``).

    Returns:
        A boolean Series aligned to the inputs.
    """
    hh_type = pd.Series(hh_type).astype(str).reset_index(drop=True)
    age = pd.Series(age).reset_index(drop=True)
    is_couple_household = hh_type.isin(COUPLE_HOUSEHOLD_TYPES)
    is_adult = age >= int(min_adult_age)
    return (is_couple_household & is_adult).reset_index(drop=True)


def derive_studies(age, share_table, random_seed: int):
    """Synthesise the boolean ``studies`` attribute from age + an
    education-participation share per age band (Task A3b).

    Each person of age ``a`` studies with probability ``share_for_age(a)`` drawn
    from ``share_table`` (Destatis Bildungsbeteiligung; see
    ``braunschweig.data.education.student_share``). The share is exogenous -- it
    is NOT taken from the later campus assignment in the education-gravity stage,
    so there is no chicken-egg dependency. The draw is seeded and deterministic.

    Fallback transparency (CLAUDE.md): ages that fall outside every band receive
    a share of 0.0; the count and rate of such persons is logged so a silent gap
    in the share table is observable.

    Args:
        age: per-person integer age Series.
        share_table: DataFrame with columns ``age_lower, age_upper,
            student_share`` (contiguous bands).
        random_seed: seed for the per-person Bernoulli draw.

    Returns:
        A boolean Series aligned to the input.
    """
    age = pd.Series(age).reset_index(drop=True)
    lookup = build_share_lookup(share_table)
    # Vectorise the band lookup over the unique ages present (cheap; ages are a
    # small integer range), then map back to every person.
    unique_ages = age.unique()
    share_by_age = {int(a): share_for_age(int(a), lookup) for a in unique_ages}
    per_person_share = age.map(share_by_age).to_numpy(dtype=float)

    # Fallback transparency: how many persons hit no band (share 0 because they
    # are outside the table, e.g. the pre-school cohort). Reported as a rate.
    band_lower = min(lo for lo, _, _ in lookup) if lookup else 0
    band_upper = max(up for _, up, _ in lookup) if lookup else 0
    out_of_table = ((age < band_lower) | (age >= band_upper)).to_numpy()
    n = len(age)
    n_out = int(out_of_table.sum())
    if n > 0:
        print(
            f"[braunschweig.ipf.attributed] studies: {n - n_out:,}/{n:,} persons "
            f"({100.0 * (n - n_out) / n:.1f}%) matched an age band; "
            f"{n_out:,} ({100.0 * n_out / n:.1f}%) outside the table -> share 0.0"
        )

    rng = np.random.RandomState(random_seed + 90210)
    draws = rng.random_sample(n)
    return pd.Series(draws < per_person_share)


def derive_socioprofessional_class(employed, age, studies):
    """Map the eqasim/INSEE ``socioprofessional_class`` from broad activity
    status (Task A3c).

    No occupation data exists upstream of the HTS in this fork, so SPC is derived
    from the activity status that the IPF + age inflation DO carry:

    - ``studies`` -> 8 (Other; students are inactive in the CS1 sense, matching
      ENTD CS24 code 80 -> 8).
    - inactive (not employed, not studying) and ``age >= SPC_RETIREMENT_AGE``
      -> 7 (Retired).
    - inactive working-age -> 8 (Other inactive).
    - employed -> an age-proxied active occupational class. Because real
      occupation is unavailable, age is used as a COARSE seniority proxy
      (documented assumption, NOT measured occupation): young employed lean
      toward Worker/Employee, mid-career toward Intermediate, older toward
      Science (cadres). This keeps the active SPC non-degenerate so the HTS
      matching does not collapse all employed persons onto one donor class.

    The mapping is a pure deterministic function of (employed, age, studies);
    studies takes precedence over employment (working students count as
    students for the activity-status attribute).

    Returns:
        An integer Series aligned to the inputs.
    """
    employed = pd.Series(employed).reset_index(drop=True)
    age = pd.Series(age).reset_index(drop=True)
    studies = pd.Series(studies).reset_index(drop=True)

    spc = pd.Series(np.full(len(age), SPC_OTHER_INACTIVE, dtype=int))

    # Retired: inactive elderly (overwritten by studies/employed below).
    spc[age >= SPC_RETIREMENT_AGE] = SPC_RETIRED

    # Employed -> age-proxied active class. Boundaries are a documented coarse
    # seniority proxy, not measured occupation.
    emp = employed.astype(bool).to_numpy()
    a = age.to_numpy()
    active = np.full(len(age), SPC_INTERMEDIATE, dtype=int)
    active[a < 25] = SPC_EMPLOYEE        # entry-level / apprenticeship-aged
    active[(a >= 25) & (a < 45)] = SPC_INTERMEDIATE
    active[a >= 45] = SPC_SCIENCE        # senior / cadre-aged
    spc[emp] = active[emp]

    # Students take precedence over employment for the activity-status attribute.
    spc[studies.astype(bool)] = SPC_STUDENT
    return spc.reset_index(drop=True)


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
    4. Absorb the trailing remainder (the ≤ N-1 leftover persons that do not
       fill a complete group) into the bucket's last household instead of
       dropping it, so the population total is preserved exactly. A bucket
       with fewer than N persons forms a single below-target household.
       Demographic plausibility of the grouping is left to downstream steps.
    5. Assign a fresh contiguous ``household_id``; set ``household_size`` to
       the *realised* member count (N for full households, N+k for the
       absorbing last household, <N for an undersized bucket); set ``weight``
       to 1.0 (one discrete person per row).

    The output is sorted by ``household_id`` so consecutive rows belong to
    the same household — required by ``synthesis.population.sampled``.
    """
    rng = np.random.RandomState(random_seed)
    weights = df["weight"].to_numpy()
    floor = np.floor(weights).astype(np.int64)
    frac = weights - floor
    counts = floor + (rng.random_sample(len(weights)) < frac).astype(np.int64)
    repeated = df.iloc[np.repeat(np.arange(len(df)), counts)].reset_index(drop=True)

    # BUG-008 fix: ensure commune_id is a stable string before any sort or
    # factorize. If df["commune_id"] arrives as a categorical or numeric type
    # the sort below would honour category-definition order (which depends on
    # IPF input order), making household_id assignment non-reproducible.
    repeated["commune_id"] = repeated["commune_id"].astype(str)

    repeated["_hh_size_int"] = _hh_size_to_int(repeated["household_size"])

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
    bucket_size = repeated.groupby(
        ["commune_id", "_hh_size_int"], sort=False
    )["_hh_size_int"].transform("size").to_numpy()
    # Absorb the trailing remainder into the bucket's last household rather than
    # dropping it, so no synthetic person is lost and the population total stays
    # anchored to the DESTATIS-derived input. Every non-empty bucket forms at
    # least one household; the last household takes any persons that do not fill
    # a complete group of N (and a bucket smaller than N becomes one
    # below-target household). The realised household_size is therefore N for
    # full households, N+k for the absorbing last household, and <N for an
    # undersized bucket.
    n_chunks = np.maximum(1, bucket_size // size_int)
    chunk_idx = np.minimum(idx_in_bucket // size_int, n_chunks - 1)

    # household_id is unique per (commune, hh_size, chunk_index) tuple.
    bucket_id = pd.factorize(
        list(zip(repeated["commune_id"].astype(str).to_numpy(), size_int))
    )[0]
    # Compose a household_id that is unique across buckets.
    # Cumulative offset per bucket using first-occurrence ordering.
    first_index_of_bucket = pd.Series(bucket_id).drop_duplicates().index.to_numpy()
    bucket_chunk_count = (n_chunks[first_index_of_bucket]).astype(np.int64)
    bucket_offset = np.concatenate([[0], np.cumsum(bucket_chunk_count)[:-1]])
    repeated["household_id"] = bucket_offset[bucket_id] + chunk_idx
    # Realised size = actual member count per household (see absorption note).
    repeated["household_size"] = (
        repeated.groupby("household_id")["household_id"]
        .transform("size").astype(np.int64)
    )
    repeated["weight"] = 1.0
    # Quantify the household-size-tail distortion from absorption before
    # _hh_size_int is dropped: count households whose realised size exceeds their
    # target N, and how many persons were shifted into the tail (sum of the
    # per-household excess, realised - target). size_int is the per-row target N.
    realised = repeated["household_size"].to_numpy()
    hh_first_row = ~repeated["household_id"].duplicated().to_numpy()
    oversized_hh_row = hh_first_row & (realised > size_int)
    n_oversized = int(oversized_hh_row.sum())
    n_absorbed_persons = int((realised - size_int)[oversized_hh_row].sum())
    repeated = repeated.drop(columns=["_hh_size_int"])
    repeated = repeated.sort_values("household_id", kind="mergesort").reset_index(drop=True)
    repeated["person_id"] = np.arange(len(repeated))

    # Diagnostic — easy to grep in pipeline logs. No person is dropped; the
    # households that absorbed a remainder and the persons thereby shifted into
    # the size tail are reported for traceability of the size-tail distortion.
    n_hh = repeated["household_id"].nunique()
    n_persons = len(repeated)
    print(
        f"[braunschweig.ipf.attributed] formed {n_hh:,} households from "
        f"{n_persons:,} persons; 0 dropped; {n_oversized:,} households absorbed a "
        f"trailing remainder ({n_absorbed_persons:,} persons shifted into the "
        f"size tail, {n_absorbed_persons / max(n_persons, 1):.2%})."
    )
    return repeated


# Mapping from the integer household_size emitted by ``_form_households``
# back to the canonical 6-bin label space used by Zensus 1000A-2081 /
# 1000A-3082 (with the open "6+" bin). Must stay aligned with
# ``_HH_SIZE_INT`` above.
_INT_TO_HH_SIZE_BIN = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6+"}


# Sentinel returned by the bucket index for (commune_id, hh_size) keys that have
# no rows in the Zensus type frame. Sharing one empty frame avoids re-allocating
# per missing bucket; its ``weight`` sum is 0.0, so the lookup behaves exactly
# like the previous empty boolean-mask result (-> hh_size-only fallback).
_EMPTY_TYPE_SHARES = pd.DataFrame(
    {"commune_id": [], "hh_size": [], "hh_type": [], "weight": []}
)


def _index_type_shares(df_zt: pd.DataFrame) -> dict:
    """Pre-index the Zensus 1000A-2081 type frame by ``(commune_id, hh_size)``.

    Returns a dict mapping each ``(commune_id, hh_size)`` key to its sub-frame so
    per-bucket lookups are O(1) instead of a full boolean scan of ``df_zt`` per
    bucket. ``pandas`` groupby preserves the original row order within each group,
    so the sub-frames are row-identical to the previous
    ``df_zt[(df_zt["commune_id"] == cid) & (df_zt["hh_size"] == hsb)]`` filter
    (boolean masking also preserves original row order). Buckets with no matching
    rows are simply absent from the dict (the caller substitutes an empty frame),
    reproducing the previous empty-mask behaviour.
    """
    return {
        key: group
        for key, group in df_zt.groupby(
            ["commune_id", "hh_size"], sort=False, observed=True
        )
    }


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

    # Pre-index the Zensus type frame once by (commune_id, hh_size) so the
    # per-bucket lookup below is O(1) instead of a full boolean scan of df_zt for
    # each of the ~123 x 6 buckets over the full-population formation. groupby
    # preserves the original row order within each group, so the sub-frame handed
    # to the sampler is row-identical to the previous boolean-mask filter and the
    # RNG draw order is unchanged.
    type_shares_by_bucket = _index_type_shares(df_zt)
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
    # Realised household sizes can exceed 6 when the last household of a bucket
    # absorbed a trailing remainder; clip to the Zensus open "6+" bin before
    # mapping so these households are typed from the 6+ distribution.
    hh_df["hh_size_bin"] = (
        hh_df["household_size"].astype(int).clip(upper=6).map(_INT_TO_HH_SIZE_BIN)
    )
    if hh_df["hh_size_bin"].isna().any():
        bad = sorted(hh_df.loc[hh_df["hh_size_bin"].isna(), "household_size"].unique())
        raise RuntimeError(
            f"[braunschweig.ipf.attributed] unmapped household_size in hh_type "
            f"assignment: {bad} (expected ints >= 1)"
        )

    types_arr = np.empty(len(hh_df), dtype=object)
    n_fallback = 0
    n_default = 0
    for (cid, hsb), g in hh_df.groupby(
        ["commune_id", "hh_size_bin"], sort=False, observed=True,
    ):
        local = type_shares_by_bucket.get((cid, hsb), _EMPTY_TYPE_SHARES)
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


def _prepare_type_shares(df_household_type: pd.DataFrame):
    """Normalise the Zensus 1000A-2081 household-type frame and build the
    scope-wide per-hh_size fallback plus a ``(commune_id, hh_size)`` index.

    Returns ``(df_zt, fallback, type_shares_by_bucket)``. The index is built once
    here so the age-aware path does an O(1) dict lookup per bucket instead of a
    full boolean scan of ``df_zt`` for every ``(commune, hh_size)`` bucket over
    the full-population formation (see ``_index_type_shares``)."""
    df_zt = df_household_type.copy()
    df_zt["commune_id"] = df_zt["commune_id"].astype(str)
    df_zt["hh_size"] = df_zt["hh_size"].astype(str)
    df_zt["hh_type"] = df_zt["hh_type"].astype(str)
    df_zt["weight"] = df_zt["weight"].astype(float)
    fallback = (
        df_zt.groupby(["hh_size", "hh_type"], observed=True)["weight"]
        .sum().reset_index()
    )
    type_shares_by_bucket = _index_type_shares(df_zt)
    return df_zt, fallback, type_shares_by_bucket


def _largest_remainder(choices: np.ndarray, weights: np.ndarray, n: int) -> list[str]:
    """Allocate ``n`` items to ``choices`` proportional to ``weights`` using the
    largest-remainder (Hare-quota) method: deterministic integer counts that sum
    to exactly ``n`` and reproduce the share vector as closely as integers allow.
    Choices are processed in name order so the result is reproducible regardless
    of input row order."""
    order = np.argsort(choices.astype(str), kind="mergesort")
    choices = choices[order]
    probs = weights[order].astype(float)
    probs = probs / probs.sum()
    raw = probs * n
    counts = np.floor(raw).astype(int)
    remainder = int(n - counts.sum())
    if remainder > 0:
        frac = raw - counts
        # Largest fractional remainder first; ties broken by name order (stable).
        take = np.lexsort((np.arange(len(choices)), -frac))[:remainder]
        counts[take] += 1
    out: list[str] = []
    for c, k in zip(choices, counts):
        out.extend([str(c)] * int(k))
    return out


def _allocate_types_for_bucket(type_shares_by_bucket, fallback, commune_id,
                               hh_size_bin, n):
    """Deterministic exact-count hh_type allocation for a (commune, hh_size_bin)
    bucket from the Zensus 1000A-2081 shares (scope-wide fallback, then the
    ``other_multi`` default). Unlike per-household sampling this reproduces the
    Zensus type marginal exactly per bucket (up to integer rounding), removing
    sampling noise; only the downstream feasibility relaxation can still shift a
    type (and that is logged).

    ``type_shares_by_bucket`` is the pre-built ``(commune_id, hh_size)`` -> sub-frame
    index from ``_prepare_type_shares``; the lookup is O(1) and row-order-identical
    to the previous boolean-mask filter, so the allocation is unchanged."""
    local = type_shares_by_bucket.get((commune_id, hh_size_bin),
                                      _EMPTY_TYPE_SHARES)
    if float(local["weight"].sum()) > 0:
        return _largest_remainder(
            local["hh_type"].to_numpy(), local["weight"].to_numpy(), n)
    fb = fallback[fallback["hh_size"] == hh_size_bin]
    if float(fb["weight"].sum()) > 0:
        return _largest_remainder(
            fb["hh_type"].to_numpy(), fb["weight"].to_numpy(), n)
    return ["other_multi"] * n


# Share of composition slots that may be relaxed (see build_bucket_households)
# before the age-aware pass is reported as a warning rather than plain info. A
# nonzero rate is expected: the requested hh_type shares come from Zensus
# 1000A-2081 while the adult/child pool comes from the IPF, and the two need not
# agree exactly inside every (commune, hh_size) bucket. A LARGE share means the
# type shares and the age structure disagree systematically -- the requested
# composition is then mostly not what the run actually produced, which must be
# visible rather than silently absorbed into free fill slots.
COMPOSITION_RELAXATION_WARN_FRACTION: float = 0.20


def form_households_age_aware(df: pd.DataFrame, random_seed: int,
                              df_household_type: pd.DataFrame,
                              cfg: dict) -> pd.DataFrame:
    """Age-aware household formation (#3b): replaces the random chunk + the
    independent hh_type draw with one coupled pass.

    Per ``(commune_id, hh_size)`` bucket: form household shells (same realised
    sizes as ``_form_households`` -- complete groups of N with the trailing
    remainder absorbed into the last household), sample one ``hh_type`` per shell
    from the Zensus 1000A-2081 shares, then assign the bucket's persons to the
    shells with ``braunschweig.ipf.household_composition.build_bucket_households``
    so the optimisation produces age-plausible, ``hh_type``-consistent
    households. Returns the persons frame with ``household_id``,
    ``household_size`` (realised), ``hh_type`` and ``person_id`` set; ``weight``
    is 1.0. Deterministic for a fixed seed.

    The hh_type counts per bucket are allocated EXACTLY from the Zensus
    1000A-2081 shares by the largest-remainder method (no per-household
    sampling), so the type marginal is reproduced exactly per bucket up to
    integer rounding. Only ``normalize_type`` (a 'couple' shell of size != 2)
    and the feasibility relaxation can still shift a type to ``other_multi``;
    both are rare with the joint age x size IPF margin (#3) on and are
    observable via ``braunschweig.analysis.run_household_composition``.
    """
    rng = np.random.RandomState(random_seed)
    weights = df["weight"].to_numpy()
    floor = np.floor(weights).astype(np.int64)
    frac = weights - floor
    counts = floor + (rng.random_sample(len(weights)) < frac).astype(np.int64)
    repeated = df.iloc[np.repeat(np.arange(len(df)), counts)].reset_index(drop=True)
    repeated["commune_id"] = repeated["commune_id"].astype(str)
    repeated["_hh_size_int"] = _hh_size_to_int(repeated["household_size"])

    # Deterministic bucket order.
    repeated = repeated.sort_values(
        ["commune_id", "_hh_size_int"], kind="mergesort"
    ).reset_index(drop=True)

    df_zt, fallback, type_shares_by_bucket = _prepare_type_shares(
        df_household_type)

    ages_all = repeated["age"].to_numpy()
    # Per-person sex vector for sex-aware couple pairing (only consumed when
    # braunschweig.chunking.sex_aware_couples is on; the IPF emits sex as a
    # "male"/"female" category, see braunschweig.ipf.model). None otherwise so the
    # bucket builder keeps its legacy sex-blind pairing.
    is_female_all = (
        (repeated["sex"].to_numpy() == "female")
        if cfg.get("sex_aware_couples", False) and "sex" in repeated.columns
        else None
    )
    n = len(repeated)
    household_id = np.full(n, -1, dtype=np.int64)
    hh_type = np.empty(n, dtype=object)
    household_size = np.zeros(n, dtype=np.int64)
    next_hid = 0
    # Fallback observability (CLAUDE.md "no silent fallbacks"): the composition
    # relaxation inside build_bucket_households fires per bucket whose adult/child
    # pool cannot serve the requested hh_type composition. Accumulate it here and
    # report ONE rate after the loop.
    relaxation_stats: dict = {}

    for (cid, size_int), g in repeated.groupby(
        ["commune_id", "_hh_size_int"], sort=True, observed=True,
    ):
        idx = g.index.to_numpy()
        bucket_size = len(idx)
        N = int(size_int)
        n_chunks = max(1, bucket_size // N)
        # Realised sizes: complete groups of N, remainder absorbed into the last.
        sizes = [N] * (n_chunks - 1) + [bucket_size - N * (n_chunks - 1)]
        hh_size_bin = _INT_TO_HH_SIZE_BIN[min(N, 6)]
        types = _allocate_types_for_bucket(
            type_shares_by_bucket, fallback, cid, hh_size_bin, n_chunks)

        bucket_ages = ages_all[idx]
        bucket_is_female = None if is_female_all is None else is_female_all[idx]
        local_of_person, realised_types = build_bucket_households(
            bucket_ages, types, sizes, cfg, rng=rng, is_female=bucket_is_female,
            relaxation_stats=relaxation_stats)

        for local_h in range(n_chunks):
            sel = idx[local_of_person == local_h]
            household_id[sel] = next_hid + local_h
            hh_type[sel] = realised_types[local_h]
            household_size[sel] = len(sel)
        next_hid += n_chunks

    # Primary-vs-fallback rate of the composition relaxation over ALL buckets.
    # Reported unconditionally (also at 0%) so a run log always states how much of
    # the requested composition was actually met.
    relaxed_slots = relaxation_stats.get("relaxed_slots", 0)
    relaxed_persons = relaxation_stats.get("persons", 0)
    if relaxed_persons:
        relaxed_share = relaxed_slots / relaxed_persons
        message = (
            "[braunschweig.ipf.attributed] composition slots: primary "
            "{:,}/{:,} ({:.1%}), relaxed {:,} ({:.1%}) in {:,}/{:,} buckets".format(
                relaxed_persons - relaxed_slots, relaxed_persons,
                1.0 - relaxed_share, relaxed_slots, relaxed_share,
                relaxation_stats.get("buckets_relaxed", 0),
                relaxation_stats.get("buckets", 0)))
        print(message)
        if relaxed_share > COMPOSITION_RELAXATION_WARN_FRACTION:
            print("[braunschweig.ipf.attributed] WARNING: composition relaxation "
                  "rate {:.1%} exceeds the {:.0%} threshold; the requested Zensus "
                  "hh_type composition is largely NOT being met -- check that the "
                  "IPF age structure and the 1000A-2081 type shares are "
                  "consistent.".format(relaxed_share,
                                       COMPOSITION_RELAXATION_WARN_FRACTION))

    # Global hard rule: eliminate any remaining all-children household (a bucket
    # that had no adult at all -- e.g. a tiny rural commune-size cell that
    # stochastically rounded to only children). Merge its children into the
    # same-commune adult household with the best parent-child age fit, so no
    # household is adult-less (as long as the commune has any adult). Cheap
    # vectorised check; the merge only runs in the rare event that orphans exist.
    min_adult = int(cfg.get("min_adult_age", 18))
    gap_mean = float(cfg.get("parent_child_gap_years",
                             hc_module.DEFAULT_PARENT_CHILD_GAP_YEARS))
    is_adult = ages_all >= min_adult
    nh = int(household_id.max()) + 1
    has_adult = np.zeros(nh, dtype=bool)
    np.logical_or.at(has_adult, household_id, is_adult)
    used = np.zeros(nh, dtype=bool)
    used[household_id] = True
    orphan_hids = np.nonzero((~has_adult) & used)[0]
    n_merged = 0
    if orphan_hids.size:
        communes = repeated["commune_id"].to_numpy()
        commune_of = np.empty(nh, dtype=object)
        commune_of[household_id] = communes
        young = np.full(nh, np.inf)
        adult_idx = np.nonzero(is_adult)[0]
        np.minimum.at(young, household_id[adult_idx], ages_all[adult_idx])
        for hid in orphan_hids:
            cand = np.nonzero((young < np.inf) & (commune_of == commune_of[hid]))[0]
            if cand.size == 0:
                continue
            persons = np.nonzero(household_id == hid)[0]
            mean_child_age = float(ages_all[persons].mean())
            target = cand[np.argmin(np.abs(young[cand] - (mean_child_age + gap_mean)))]
            household_id[persons] = target
            n_merged += len(persons)
        if n_merged:
            # Rebuild realised size + hh_type for the (rarely) changed households.
            diag = pd.DataFrame({"hid": household_id, "age": ages_all})
            household_size = diag.groupby("hid")["hid"].transform("size").to_numpy()
            types = diag.groupby("hid")["age"].apply(
                lambda a: hc_module._realised_type(list(a.to_numpy()), min_adult))
            hh_type = pd.Series(household_id).map(types).to_numpy()

    repeated["household_id"] = household_id
    repeated["hh_type"] = np.asarray(hh_type).astype(str)
    repeated["household_size"] = np.asarray(household_size).astype(np.int64)
    repeated["weight"] = 1.0
    repeated = repeated.drop(columns=["_hh_size_int"])
    repeated = repeated.sort_values("household_id", kind="mergesort").reset_index(drop=True)
    repeated["person_id"] = np.arange(len(repeated))
    repeated["hh_type"] = repeated["hh_type"].astype("category")

    n_hh = int(repeated["household_id"].nunique())
    print(
        f"[braunschweig.ipf.attributed] age-aware chunking: formed {n_hh:,} "
        f"age-plausible, hh_type-consistent households from {len(repeated):,} "
        f"persons; 0 dropped."
    )
    return repeated


# Sentinel for synthetic persons whose home commune has no RegioStaR-7 code in
# the regiostar reference (optimization step 1). Kept as an explicit category so
# such persons can only relax during HTS matching instead of being silently
# defaulted to "urban"/"rural" (CLAUDE.md: no silent fallback).
URBAN_CLASS_UNKNOWN = "unknown"


def attach_urban_class(df, df_regiostar):
    """Attach an ``urban_class`` column ({urban, rural, unknown}) to the synthetic
    population from each person's home ``commune_id`` via the RegioStaR-2 collapse
    (commune_id -> AGS-8 -> RegioStaR-7 -> urban/rural). Communes absent from the
    regiostar reference receive ``URBAN_CLASS_UNKNOWN``. The mapped/unmapped rate
    is logged so a broken commune join surfaces loudly.

    Used by the HTS statistical matching (optimization step 1): the synthetic
    target needs an ``urban_class`` key that is label-comparable with the French
    HTS unite-urbaine urban/rural split.
    """
    from braunschweig.data.bbsr.regiostar import urban_class_by_commune

    rs7_by_ags8 = dict(zip(
        df_regiostar["commune_id"].astype(str),
        df_regiostar["regiostar7"].astype("Int64"),
    ))
    labels = urban_class_by_commune(df["commune_id"], rs7_by_ags8)

    n_total = len(labels)
    n_mapped = int(labels.notna().sum())
    rate = 100.0 * n_mapped / max(n_total, 1)
    print(
        f"[braunschweig.ipf.attributed] urban_class: mapped {n_mapped:,}/{n_total:,} "
        f"({rate:.1f}%) via commune_id -> RegioStaR-2; "
        f"unmapped {n_total - n_mapped:,} -> '{URBAN_CLASS_UNKNOWN}'."
    )
    if n_total and (n_total - n_mapped) / n_total > 0.10:
        print(
            "[braunschweig.ipf.attributed] WARNING: urban_class unmapped rate > 10% "
            "- the commune_id -> AGS-8 -> RegioStaR-7 join is likely broken."
        )

    df["urban_class"] = labels.fillna(URBAN_CLASS_UNKNOWN).to_numpy()
    return df


def configure(context):
    context.stage("braunschweig.ipf.model")
    context.config("random_seed")
    # Optimization step (1): when ``urban_class`` is a configured HTS matching key,
    # the synthetic target needs an ``urban_class`` column (commune_id ->
    # RegioStaR-2). The regiostar stage is staged ONLY when the key is actually
    # requested, so the default (key absent) keeps the legacy dependency graph and
    # byte-identical output.
    if "urban_class" in context.config("matching_attributes", []):
        context.stage("braunschweig.data.bbsr.regiostar")
    context.config("braunschweig.ipf.use_household_size_margin", False)
    # Optional household-type assignment from Zensus 2022 1000A-2081
    # (commune × hh_size × hh_type, in households). Drawn after the
    # household-formation pass; one ``hh_type`` per ``household_id`` is
    # sampled proportional to the Zensus shares within its
    # (commune_id, hh_size) bucket. Requires use_household_size_margin.
    context.config("braunschweig.ipf.use_household_type_margin", False)
    if context.config("braunschweig.ipf.use_household_type_margin"):
        context.stage("braunschweig.data.census.households_type")

    # Optional age-aware household chunking (#3b). When on (requires
    # use_household_size_margin), the random within-bucket chunk + independent
    # hh_type draw are replaced by one coupled, optimisation-based pass that
    # produces age-plausible, hh_type-consistent households. Default off ->
    # byte-identical to the legacy formation. Needs the Zensus household-type
    # shares regardless of use_household_type_margin.
    context.config("braunschweig.ipf.age_aware_chunking", False)
    context.config("braunschweig.chunking.minimum_adult_age", 18)
    context.config("braunschweig.chunking.couple_age_weight", 1.0)
    context.config("braunschweig.chunking.couple_age_std", 4.0)
    context.config("braunschweig.chunking.parent_child_weight", 1.0)
    context.config("braunschweig.chunking.parent_child_gap_years", 31.8)
    context.config("braunschweig.chunking.parent_child_gap_std", 5.5)
    # Parent-age targeting weight: 0 -> child households get the youngest adults
    # (realised mean gap collapses to ~26 y, below the target); 1 -> adults centred
    # on (child + gap), mean ~33 y. The default 0.85 is calibrated on the real ZGB
    # IPF (25%) so the realised mean parent-child gap is 31.8 y, equal to
    # parent_child_gap_years (Destatis 2024 mean mother age at birth).
    context.config("braunschweig.chunking.child_parent_age_target_weight", 0.85)
    # Sex-aware couple formation: pair couples opposite-sex with a small
    # calibrated same-sex share (Destatis Mikrozensus 2025, ~1.1 % of couples).
    # Default off -> the legacy sex-blind age-adjacent pairing.
    context.config("braunschweig.chunking.sex_aware_couples", False)
    context.config("braunschweig.chunking.same_sex_couple_share",
                   hc_module.DEFAULT_SAME_SEX_COUPLE_SHARE)
    if context.config("braunschweig.ipf.age_aware_chunking"):
        context.stage("braunschweig.data.census.households_type")

    # Person-attribute reactivation (Task A3): derive couple / studies /
    # socioprofessional_class instead of the dead constants. Default ON; OFF
    # keeps the legacy constants (couple=False, studies=False, SPC=0) so the
    # output is byte-identical. The studies share table is only staged when the
    # flag is on, so OFF does not change the synpp dependency graph.
    context.config("reactivate_person_attributes", True)
    if context.config("reactivate_person_attributes"):
        context.stage("braunschweig.data.education.student_share")

def execute(context):
    from braunschweig.ipf.config_validation import (
        ATTRIBUTED_REQUIREMENTS, validate_household_realism_config)

    # Fail fast on an invalid household-realism flag combination (this stage's
    # subset: hh_type / age-aware require the size margin, sex-aware couples require
    # age-aware chunking). Single source of truth: braunschweig.ipf.config_validation.
    validate_household_realism_config(context.config, ATTRIBUTED_REQUIREMENTS)

    df = context.stage("braunschweig.ipf.model")
    use_hh_size = context.config("braunschweig.ipf.use_household_size_margin")
    use_hh_type = context.config("braunschweig.ipf.use_household_type_margin")

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

    # Placeholder values. When ``reactivate_person_attributes`` is ON these are
    # overwritten at the end of execute() (after age inflation + household
    # formation, which provide the integer age / hh_type the derivations need);
    # when OFF the legacy dead constants are kept (byte-identical output).
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

    # Flag interdependencies are validated at the top of execute()
    # (validate_household_realism_config); age_aware implies use_hh_size here.
    age_aware = context.config("braunschweig.ipf.age_aware_chunking")

    if use_hh_size and age_aware:
        # Age-aware path: couple formation + hh_type in one optimisation pass.
        df_household_type = context.stage(
            "braunschweig.data.census.households_type"
        )
        cfg = {
            "min_adult_age": context.config("braunschweig.chunking.minimum_adult_age"),
            "couple_age_weight": context.config("braunschweig.chunking.couple_age_weight"),
            "couple_age_std": context.config("braunschweig.chunking.couple_age_std"),
            "parent_child_weight": context.config("braunschweig.chunking.parent_child_weight"),
            "parent_child_gap_years": context.config("braunschweig.chunking.parent_child_gap_years"),
            "parent_child_gap_std": context.config("braunschweig.chunking.parent_child_gap_std"),
            "sex_aware_couples": context.config("braunschweig.chunking.sex_aware_couples"),
            "same_sex_couple_share": context.config("braunschweig.chunking.same_sex_couple_share"),
            "child_parent_age_target_weight": context.config(
                "braunschweig.chunking.child_parent_age_target_weight"),
        }
        df = form_households_age_aware(
            df, context.config("random_seed"), df_household_type, cfg
        )
        # hh_type is already assigned by the age-aware pass -> no separate draw.
    elif use_hh_size:
        # Group persons into real households according to the IPF hh_size cell.
        df = _form_households(df, context.config("random_seed"))

        if use_hh_type:
            df_household_type = context.stage(
                "braunschweig.data.census.households_type"
            )
            df = _assign_household_types(
                df, df_household_type, context.config("random_seed")
            )

    # Person-attribute reactivation (Task A3): override the dead placeholder
    # constants with the derived couple / studies / socioprofessional_class.
    # Gated by ``reactivate_person_attributes``; OFF -> the constants above are
    # kept and the output is byte-identical to the legacy behaviour.
    if context.config("reactivate_person_attributes"):
        random_seed = context.config("random_seed")
        min_adult_age = int(context.config("braunschweig.chunking.minimum_adult_age"))

        # couple requires the realised household type from the formation pass.
        # Without it (household formation disabled, no hh_type column) the couple
        # relationship is undefined -> keep False and log that it stayed dead.
        if "hh_type" in df.columns:
            df["couple"] = derive_couple(
                df["hh_type"], df["age"], min_adult_age=min_adult_age
            ).to_numpy()
            n_couple = int(df["couple"].sum())
            print(
                f"[braunschweig.ipf.attributed] couple: derived True for "
                f"{n_couple:,}/{len(df):,} persons "
                f"({100.0 * n_couple / max(len(df), 1):.1f}%) from hh_type."
            )
        else:
            print(
                "[braunschweig.ipf.attributed] couple: no hh_type column "
                "(household formation disabled) -> couple stays False."
            )

        share_table = context.stage("braunschweig.data.education.student_share")
        df["studies"] = derive_studies(
            df["age"], share_table, random_seed
        ).to_numpy()

        df["socioprofessional_class"] = derive_socioprofessional_class(
            df["employed"], df["age"], df["studies"]
        ).to_numpy()
        spc_counts = (
            df["socioprofessional_class"].value_counts(normalize=True)
            .sort_index().to_dict()
        )
        print(
            "[braunschweig.ipf.attributed] socioprofessional_class shares: "
            + ", ".join(f"{k}={v:.1%}" for k, v in spc_counts.items())
        )

    # Optimization step (1): attach the urban/rural matching key when requested.
    # Gated by the presence of "urban_class" in matching_attributes so the default
    # path adds no column (byte-identical) and stages no extra dependency. The key
    # is registered with a default in configure(); the execute context takes no
    # default, so it is read here without one.
    if "urban_class" in context.config("matching_attributes"):
        df = attach_urban_class(df, context.stage("braunschweig.data.bbsr.regiostar"))

    return df

