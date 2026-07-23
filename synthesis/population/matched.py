from tqdm import tqdm
import itertools
import numpy as np
import pandas as pd
import numba

import data.hts.egt.cleaned
import data.hts.entd.cleaned

import multiprocessing as mp

"""
This stage attaches obervations from the household travel survey to the synthetic
population sample. This is done by statistical matching.
"""

INCOME_CLASS = {
    "egt": data.hts.egt.cleaned.calculate_income_class,
    "entd": data.hts.entd.cleaned.calculate_income_class,
}

DEFAULT_MATCHING_ATTRIBUTES = [
    "sex", "any_cars", "age_class", "socioprofessional_class",
    "departement_id"
]

# Optimization step (1): top of the open-ended household-size class. Sizes are
# binned to 1, 2, 3, 4, "5 or more" so the synthetic target (household_size as a
# string from braunschweig.ipf.attributed) and the HTS side (integer NPERS) share
# one comparable categorical domain. 5+ is collapsed because the HTS donor pool
# for very large households is thin; the relaxing matcher would otherwise have to
# fall back for them anyway.
HOUSEHOLD_SIZE_CLASS_TOP = 5


def household_size_class(values):
    """Bin a household-size column into the comparable matching class
    ``1, 2, 3, 4, 5(+)``.

    Accepts integer (HTS ``NPERS``) or string (synthetic ``household_size``,
    written as ``hh_size.astype(str)`` by ``braunschweig.ipf.attributed``) input
    and returns an integer array where ``HOUSEHOLD_SIZE_CLASS_TOP`` is the
    open-ended "5 or more" class.
    """
    sizes = pd.to_numeric(pd.Series(values).values, errors="raise").astype(int)
    return np.minimum(sizes, HOUSEHOLD_SIZE_CLASS_TOP)


# Optimization step (1): cross-country urban/rural crosswalk for the HTS side.
# The French HTS carries ``urban_type`` from the unite-urbaine (UU2010)
# definition: ville-centre (central_city), banlieue (suburb) and isolated city
# all lie INSIDE an urban unit, while "none" is outside any urban unit (rural).
# Collapsing both this and the German RegioStaR-2 (see
# braunschweig.data.bbsr.regiostar.regiostar2_label) onto the same {urban, rural}
# label set lets the matcher key the urban/rural trip-length gradient across the
# two countries' native urbanity definitions. This is a documented approximation
# (UU2010 != RegioStaR), not an exact equivalence.
URBAN_TYPE_TO_URBAN_CLASS = {
    "central_city": "urban",
    "suburb": "urban",
    "isolated_city": "urban",
    "none": "rural",
}


def urban_class_from_urban_type(values):
    """Map the HTS ``urban_type`` (UU2010) categories onto the ``urban``/``rural``
    class used as a matching key. Raises if an unmapped category appears so a
    changed HTS coding surfaces loudly instead of silently defaulting."""
    series = pd.Series(values).astype(str)
    unknown = set(series.unique()) - set(URBAN_TYPE_TO_URBAN_CLASS)
    if unknown:
        raise ValueError(f"Unmapped HTS urban_type categories: {sorted(unknown)}")
    return series.map(URBAN_TYPE_TO_URBAN_CLASS).to_numpy()


def resolve_matching_columns(configured, reactivate_person_attributes):
    """Resolve the effective list of statistical-matching keys.

    Expands the ``*default*`` sentinel into ``DEFAULT_MATCHING_ATTRIBUTES`` and,
    when ``reactivate_person_attributes`` is True (Task A3b), appends ``studies``
    as the LAST (lowest-priority) key unless it is already configured. With the
    flag False the configured list is returned unchanged (byte-identical
    matching). A fresh list is always returned so the caller never mutates the
    config object in place.
    """
    columns = list(configured)
    try:
        default_index = columns.index("*default*")
        columns[default_index:default_index + 1] = DEFAULT_MATCHING_ATTRIBUTES
    except ValueError:
        pass
    if reactivate_person_attributes and "studies" not in columns:
        columns.append("studies")
    return columns


def configure(context):
    # Execution detail, not scientific config: changing it must not devalidate cached stages (upstream eqasim-france #438)
    context.config("processes", volatile = True)
    context.config("random_seed")
    context.config("matching_minimum_observations", 20)
    context.config("matching_attributes", DEFAULT_MATCHING_ATTRIBUTES)
    # Task A3b: when the synthesised ``studies`` attribute is reactivated, add it
    # as a matching key so students receive HTS donors with a student activity
    # day. Default ON; OFF leaves ``matching_attributes`` untouched
    # (byte-identical matching). The synthetic ``studies`` is produced by
    # ``braunschweig.ipf.attributed`` and the HTS side already carries ``studies``
    # (data.hts.*.cleaned), so the key is available on both sides.
    context.config("reactivate_person_attributes", True)

    # Optimization step (4): within-cell kNN-style similarity weighting on a
    # continuous secondary attribute (default exact ``age``). Default OFF ->
    # legacy shared-CDF draw, byte-identical. ``matching_similarity_bandwidth``
    # is in the same unit as the attribute (years for age); a cell with more than
    # ``matching_similarity_max_donors`` donors falls back to the shared-CDF draw
    # (the cost-bound; the fallback rate is logged).
    context.config("matching_similarity", False)
    context.config("matching_similarity_attribute", "age")
    context.config("matching_similarity_bandwidth", 5.0)
    context.config("matching_similarity_max_donors", 2000)

    context.stage("synthesis.population.sampled")
    context.stage("synthesis.population.income.selected")

    hts = context.config("hts")
    context.stage("data.hts.selected", alias = "hts")

# Optimization step (4): within-cell kNN-style similarity weighting. After the
# demographic keys define a (relaxed) matching cell, donors are drawn not just by
# their survey weight but additionally weighted by proximity to the target person
# in a continuous secondary attribute (standardised exact age). This refines the
# choice WITHIN the cell only -- the cell membership (and thus the anti-overfitting
# relaxing) is unchanged. Flag-gated, default OFF (-> the legacy shared-CDF draw,
# byte-identical).
SIMILARITY_TARGET_CHUNK = 4096  # bound the kernel matrix memory to chunk x n_donors


def kernel_weighted_sample(uniform, donor_weights, donor_values, target_values,
                           bandwidth):
    """Draw one donor per target with probability proportional to
    ``donor_weight * exp(-|target_value - donor_value| / bandwidth)``.

    ``uniform`` is one U[0,1) draw per target (so the result is deterministic for
    a fixed RNG stream). Returns a local donor index per target (into the
    ``donor_*`` arrays). Targets are processed in chunks so the dense kernel
    matrix never exceeds ``SIMILARITY_TARGET_CHUNK x n_donors``.
    """
    donor_weights = np.asarray(donor_weights, dtype=float)
    donor_values = np.asarray(donor_values, dtype=float)
    target_values = np.asarray(target_values, dtype=float)
    uniform = np.asarray(uniform, dtype=float)

    n_target = len(target_values)
    out = np.empty(n_target, dtype=int)
    for start in range(0, n_target, SIMILARITY_TARGET_CHUNK):
        stop = min(start + SIMILARITY_TARGET_CHUNK, n_target)
        tv = target_values[start:stop]
        # kernel[t, d] = w_d * exp(-|tv_t - dv_d| / bandwidth)
        diff = np.abs(tv[:, None] - donor_values[None, :])
        kernel = donor_weights[None, :] * np.exp(-diff / bandwidth)
        cdf = np.cumsum(kernel, axis=1)
        totals = cdf[:, -1]
        thresholds = uniform[start:stop] * totals
        out[start:stop] = (cdf < thresholds[:, None]).sum(axis=1)
    return out


@numba.jit(nopython = True) # Already parallelized parallel = True)
def sample_indices(uniform, cdf, selected_indices):
    indices = np.arange(len(uniform))

    for i, u in enumerate(uniform):
        indices[i] = np.count_nonzero(cdf < u)

    return selected_indices[indices]

def statistical_matching(progress, df_source, source_identifier, weight, df_target, target_identifier, columns, random_seed = 0, minimum_observations = 0,
                         similarity_column = None, similarity_bandwidth = 5.0, similarity_max_donors = 2000):
    random = np.random.RandomState(random_seed)

    # Optimization step (4): the optional continuous similarity attribute (e.g.
    # exact ``age``) is carried as an extra column so it is sorted/filtered in
    # lockstep with the matching keys (no separate index alignment to get wrong).
    extra = []
    if similarity_column is not None and similarity_column not in columns:
        extra = [similarity_column]

    # Reduce data frames
    df_source = df_source[[source_identifier, weight] + columns + extra].copy()
    df_target = df_target[[target_identifier] + columns + extra].copy()

    # Sort data frames. The identifier (and weight) tie-breakers make the sort
    # total, so the assignment cannot depend on the input row order (upstream
    # eqasim-france #414). NOTE: this changes the tie-break order versus the
    # previous partial sort, so matched donors (and downstream results) can differ.
    df_source = df_source.sort_values(by = columns + [source_identifier, weight])
    df_target = df_target.sort_values(by = columns + [target_identifier])

    # Continuous similarity values aligned to the now-sorted frames.
    source_similarity = (df_source[similarity_column].values.astype(float)
                         if similarity_column is not None else None)
    target_similarity = (df_target[similarity_column].values.astype(float)
                         if similarity_column is not None else None)
    n_similarity_applied = 0
    n_similarity_skipped = 0

    # Find unique values for all columns
    unique_values = {}

    for column in columns:
        unique_values[column] = list(sorted(set(df_source[column].unique()) | set(df_target[column].unique())))

    # Generate filters for all columns and values
    source_filters, target_filters = {}, {}

    for column, column_unique_values in unique_values.items():
        source_filters[column] = [df_source[column].values == value for value in column_unique_values]
        target_filters[column] = [df_target[column].values == value for value in column_unique_values]

    # Define search order
    source_filters = [source_filters[column] for column in columns]
    target_filters = [target_filters[column] for column in columns]

    # Perform matching
    weights = df_source[weight].values
    assigned_indices = np.ones((len(df_target),), dtype = int) * -1
    unassigned_mask = np.ones((len(df_target),), dtype = bool)
    assigned_levels = np.ones((len(df_target),), dtype = int) * -1
    uniform = random.random_sample(size = (len(df_target),))

    column_indices = [np.arange(len(unique_values[column])) for column in columns]

    for level in range(1, len(column_indices) + 1)[::-1]:
        level_column_indices = column_indices[:level]

        if np.count_nonzero(unassigned_mask) > 0:
            for column_index in itertools.product(*level_column_indices):
                f_source = np.logical_and.reduce([source_filters[i][k] for i, k in enumerate(column_index)])
                f_target = np.logical_and.reduce([target_filters[i][k] for i, k in enumerate(column_index)] + [unassigned_mask])

                selected_indices = np.nonzero(f_source)[0]
                requested_samples = np.count_nonzero(f_target)

                if requested_samples == 0:
                    continue

                if len(selected_indices) < minimum_observations:
                    continue

                selected_weights = weights[f_source]
                cdf = np.cumsum(selected_weights)
                cdf /= cdf[-1]

                # Optimization step (4): within-cell similarity weighting. Applied
                # only when enabled AND the donor pool is small enough to bound the
                # cost (large, heavily-relaxed cells fall back to the legacy
                # shared-CDF draw; the fallback count is logged -- no silent
                # fallback). When disabled (column None) this branch is never taken
                # and the draw is byte-identical to the legacy path.
                if source_similarity is not None and len(selected_indices) <= similarity_max_donors:
                    local = kernel_weighted_sample(
                        uniform[f_target], selected_weights,
                        source_similarity[f_source], target_similarity[f_target],
                        similarity_bandwidth)
                    assigned_indices[f_target] = selected_indices[local]
                    n_similarity_applied += requested_samples
                else:
                    assigned_indices[f_target] = sample_indices(uniform[f_target], cdf, selected_indices)
                    if source_similarity is not None:
                        n_similarity_skipped += requested_samples

                assigned_levels[f_target] = level
                unassigned_mask[f_target] = False

                progress.update(np.count_nonzero(f_target))

    # Randomly assign unmatched observations
    cdf = np.cumsum(weights)
    cdf /= cdf[-1]

    assigned_indices[unassigned_mask] = sample_indices(uniform[unassigned_mask], cdf, np.arange(len(weights)))
    assigned_levels[unassigned_mask] = 0

    progress.update(np.count_nonzero(unassigned_mask))

    if np.count_nonzero(unassigned_mask) > 0:
        raise RuntimeError("Some target observations could not be matched. Minimum observations configured too high?")

    assert np.count_nonzero(unassigned_mask) == 0
    assert np.count_nonzero(assigned_indices == -1) == 0

    # Optimization step (4): report how many target persons were matched with the
    # within-cell similarity weighting vs. fell back to the legacy shared-CDF draw
    # (large/relaxed cells above similarity_max_donors). A high fallback share is a
    # signal that the donor cells are too coarse for similarity to act.
    if source_similarity is not None:
        total_sim = n_similarity_applied + n_similarity_skipped
        applied_pct = 100.0 * n_similarity_applied / max(total_sim, 1)
        print(
            "[matched.similarity] within-cell similarity on '%s' applied to "
            "%d/%d (%.1f%%); shared-CDF fallback %d (cells > %d donors)."
            % (similarity_column, n_similarity_applied, total_sim, applied_pct,
               n_similarity_skipped, similarity_max_donors)
        )

    # Write back indices
    df_target[source_identifier] = df_source[source_identifier].values[assigned_indices]
    df_target = df_target[[target_identifier, source_identifier]]

    return df_target, assigned_levels

class _NullProgress:
    """No-op progress sink so the reusable entry point ``match_donors`` can call
    ``statistical_matching`` outside a synpp pipeline context (which provides a
    real progress object with the same ``update`` interface)."""

    def update(self, n = 1):
        pass


def match_donors(df_target, df_source, *, matching_columns, minimum_observations,
                 random_seed, weight_column = "weight"):
    """Reusable hierarchical-relaxation donor matching (public API).

    Thin wrapper around the single-process core ``statistical_matching`` used by
    this stage, so other stages (e.g. assigning activity chains to popsim_open
    persons whose donor has no travel diary) can reuse the exact same algorithm
    without going through the synpp stage machinery.

    Algorithm (relaxation semantics): targets and donors are grouped into cells
    by ``matching_columns``. Matching starts on the full key tuple and relaxes by
    dropping keys FROM THE END of the list, one level at a time, whenever a
    target's cell has fewer than ``minimum_observations`` donors -- so the order
    of ``matching_columns`` encodes priority (first = never relaxed). Within a
    cell, one donor is drawn per target with probability proportional to the
    donor's survey weight (shared-CDF draw).

    Parameters
    ----------
    df_target : pd.DataFrame
        Synthetic persons; must contain ``person_id`` and all
        ``matching_columns``. Column dtypes must be directly comparable to the
        donor side (same categories / value domain per key).
    df_source : pd.DataFrame
        Donor (HTS) persons; must contain ``hts_id``, ``weight_column`` (a
        positive numeric survey weight) and all ``matching_columns``.
    matching_columns : list of str
        Matching keys in priority order (relaxed from the end).
    minimum_observations : int
        Minimum donor count for a cell to be used at a given relaxation level.
    random_seed : int
        Seed for the internal ``np.random.RandomState``; the assignment is fully
        deterministic for fixed inputs and seed.
    weight_column : str, default ``"weight"``
        Name of the donor survey-weight column (the stage itself uses
        ``person_weight``).

    Returns
    -------
    pd.DataFrame
        One row per target person with columns ``[person_id, hts_id]``. Row
        order follows the internal sort by ``matching_columns``, not the input
        order.

    Raises
    ------
    RuntimeError
        If any target cannot be matched even at full relaxation (i.e. its value
        of the FIRST matching key has fewer than ``minimum_observations``
        donors). This mirrors the stage behaviour: unmatched targets are never
        returned silently.
    """
    df_assignment, _levels = statistical_matching(
        _NullProgress(), df_source, "hts_id", weight_column,
        df_target, "person_id", list(matching_columns),
        random_seed = random_seed, minimum_observations = minimum_observations)

    return df_assignment


def _run_parallel_statistical_matching(context, args):
    # Pass arguments
    df_target, random_seed = args

    # Pass data
    df_source = context.data("df_source")
    source_identifier = context.data("source_identifier")
    weight = context.data("weight")
    target_identifier = context.data("target_identifier")
    columns = context.data("columns")
    minimum_observations = context.data("minimum_observations")
    similarity_column = context.data("similarity_column")
    similarity_bandwidth = context.data("similarity_bandwidth")
    similarity_max_donors = context.data("similarity_max_donors")

    return statistical_matching(context.progress, df_source, source_identifier, weight, df_target, target_identifier, columns, random_seed, minimum_observations,
                                similarity_column = similarity_column,
                                similarity_bandwidth = similarity_bandwidth,
                                similarity_max_donors = similarity_max_donors)

def parallel_statistical_matching(context, df_source, source_identifier, weight, df_target, target_identifier, columns, minimum_observations = 0,
                                  similarity_column = None, similarity_bandwidth = 5.0, similarity_max_donors = 2000):
    random_seed = context.config("random_seed")
    processes = context.config("processes")

    random = np.random.RandomState(random_seed)
    chunks = np.array_split(df_target, processes)

    with context.progress(label = "Statistical matching ...", total = len(df_target)):
        with context.parallel({
            "df_source": df_source, "source_identifier": source_identifier, "weight": weight,
            "target_identifier": target_identifier, "columns": columns,
            "minimum_observations": minimum_observations,
            "similarity_column": similarity_column,
            "similarity_bandwidth": similarity_bandwidth,
            "similarity_max_donors": similarity_max_donors
        }) as parallel:
                random_seeds = random.randint(10000, size = len(chunks))
                results = parallel.map(_run_parallel_statistical_matching, zip(chunks, random_seeds))

                levels = np.hstack([r[1] for r in results])
                df_target = pd.concat([r[0] for r in results])

                return df_target, levels

def execute(context):
    hts = context.config("hts")

    # Load data
    df_source_households, df_source_persons, df_source_trips = context.stage("hts")
    df_source = pd.merge(df_source_persons, df_source_households)

    df_target = context.stage("synthesis.population.sampled")

    # Resolve matching keys: expand ``*default*`` and (Task A3b) append the
    # reactivated ``studies`` key as the lowest-priority fallback key when the
    # flag is ON. OFF -> the configured list is used unchanged (byte-identical).
    columns = resolve_matching_columns(
        context.config("matching_attributes"),
        context.config("reactivate_person_attributes"),
    )

    # Define matching attributes
    AGE_BOUNDARIES = [14, 29, 44, 59, 74, 1000]

    if "age_class" in columns:
        df_target["age_class"] = np.digitize(df_target["age"], AGE_BOUNDARIES, right = True)
        df_source["age_class"] = np.digitize(df_source["age"], AGE_BOUNDARIES, right = True)

    if "income_class" in columns:
        df_income = context.stage("synthesis.population.income.selected")[["household_id", "household_income"]]

        df_target = pd.merge(df_target, df_income)
        df_target["income_class"] = INCOME_CLASS[hts](df_target)

    if "any_cars" in columns:
        df_target["any_cars"] = df_target["number_of_cars"] > 0
        df_source["any_cars"] = df_source["number_of_cars"] > 0

    # Optimization step (1): household-size class as a matching key. The
    # synthetic target carries household_size as a string, the HTS side as
    # integer NPERS; household_size_class harmonises both into 1..5(+).
    if "household_size_class" in columns:
        df_target["household_size_class"] = household_size_class(df_target["household_size"])
        df_source["household_size_class"] = household_size_class(df_source["household_size"])

    # Optimization step (1): urban/rural class as a matching key. The HTS side is
    # derived here from the unite-urbaine ``urban_type``; the synthetic target
    # carries ``urban_class`` already (produced upstream by
    # braunschweig.ipf.attributed via commune_id -> RegioStaR-2). Both use the
    # same {urban, rural} labels. The guard loop below raises if either side is
    # missing the column.
    if "urban_class" in columns:
        df_source["urban_class"] = urban_class_from_urban_type(df_source["urban_type"])

    # Perform statistical matching
    df_source = df_source.rename(columns = { "person_id": "hts_id" })

    for column in columns:
        if not column in df_source:
            raise RuntimeError("Attribute not available in source (HTS) for matching: {}".format(column))

        if not column in df_target:
            raise RuntimeError("Attribute not available in target (census) for matching: {}".format(column))

    # Optimization step (4): resolve the optional within-cell similarity attribute.
    # When enabled, the attribute (default exact ``age``) must exist on both sides;
    # OFF -> similarity_column stays None and the matching is byte-identical.
    similarity_column = None
    if context.config("matching_similarity"):
        similarity_column = context.config("matching_similarity_attribute")
        for side, frame in (("source (HTS)", df_source), ("target (census)", df_target)):
            if similarity_column not in frame:
                raise RuntimeError(
                    "matching_similarity is on but the similarity attribute '{}' "
                    "is not available in {}.".format(similarity_column, side))
        print("[matched.similarity] enabled on '%s' (bandwidth=%.1f, max_donors=%d)."
              % (similarity_column, context.config("matching_similarity_bandwidth"),
                 context.config("matching_similarity_max_donors")))

    df_assignment, levels = parallel_statistical_matching(
        context,
        df_source, "hts_id", "person_weight",
        df_target, "person_id",
        columns,
        minimum_observations = context.config("matching_minimum_observations"),
        similarity_column = similarity_column,
        similarity_bandwidth = context.config("matching_similarity_bandwidth"),
        similarity_max_donors = context.config("matching_similarity_max_donors"))

    df_target = pd.merge(df_target, df_assignment, on = "person_id")
    assert len(df_target) == len(df_assignment)

    context.set_info("matched_counts", {
        count: np.count_nonzero(levels >= count) for count in range(len(columns) + 1)
    })

    for count in range(len(columns) + 1):
        print("%d matched levels:" % count, np.count_nonzero(levels >= count), "%.2f%%" % (100 * np.count_nonzero(levels >= count) / len(df_target),))

    return df_target[["person_id", "hts_id"]]
