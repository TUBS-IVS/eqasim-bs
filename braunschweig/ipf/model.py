"""IPF model stage - joint distribution from Kreis x Gemeinde marginals.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/model.py``.
Adapted for Braunschweig:
- Config keys are ``braunschweig.ipf.*``; the legacy ``bavaria.ipf.*`` mapping
  was removed in Phase 4.3 along with the rest of the bavaria/ tree.
- Includes a post-IPF margin-deviation control check (BUG-009 fix).
"""
import os

import pandas as pd
import numpy as np
import itertools

"""
This stage merge prepared datasets of employees from Kreis level
with inhabitants from Gemeinde level using Iterative Proportional Fitting.

When ``braunschweig.ipf.use_household_size_margin`` is enabled (set in
``braunschweig.ipf.prepare``), an additional ``hh_size`` dimension is added to
the joint distribution and a new ``commune × hh_size`` selector block
is appended to the IPF schedule. A hard zero target is enforced for the
physically impossible cell ``age < braunschweig.minimum_age.one_person_household``
∩ ``hh_size == "1"`` (children cannot live in single-person households).

Returns columns:

* legacy mode:  commune_id, departement_id, sex, age_class, employed, license, weight
* hh-size mode: commune_id, departement_id, sex, age_class, employed, license, hh_size, weight
"""

HH_SIZE_BINS = ("1", "2", "3", "4", "5", "6+")

# Empty index selector in the same representation as ``np.nonzero(mask.values)``
# for an all-False mask: a 1-tuple holding an ascending (here: empty) int64
# index array. Used as the lookup default for combinations whose cell does not
# exist in ``df_model`` (the legacy boolean-mask path produced exactly this).
_EMPTY_SELECTOR = (np.array([], dtype=np.int64),)


def _build_group_indices(df_model, key_columns):
    """Map each observed combination of ``key_columns`` to its ascending row indices.

    This is the vectorised replacement for the legacy per-constraint boolean mask
    ``np.nonzero((df_model[c0] == v0) & (df_model[c1] == v1) & ...)``. ``pandas``
    ``DataFrameGroupBy.indices`` returns, per group key, the **ascending** array of
    integer row positions of that group in ``df_model`` -- byte-identical to what
    ``np.nonzero`` returns for the equivalent boolean mask, because both enumerate
    matching rows in ascending order.

    Args:
        df_model: the full IPF cell table (one row per joint-distribution cell).
        key_columns: list of column names whose value tuple identifies a group.

    Returns:
        dict mapping a key tuple (single key columns still produce a 1-tuple, see
        below) to ``(ascending_int64_index_array,)`` -- the exact representation of
        ``np.nonzero(mask.values)``.

    Note:
        For a single key column, ``groupby([col]).indices`` is keyed by the scalar
        value, not a 1-tuple. We normalise every key to a tuple so callers always
        look up with ``(v0, v1, ...)`` regardless of arity.
    """
    raw = df_model.groupby(key_columns, sort=False).indices
    normalised = {}
    single_key = len(key_columns) == 1
    for key, index_array in raw.items():
        key_tuple = (key,) if single_key else tuple(key)
        # Wrap as a 1-tuple to mirror ``np.nonzero`` (which returns ``(array,)``).
        normalised[key_tuple] = (index_array,)
    return normalised


def _build_iteration_blocks(selectors, targets, selector_blocks, n_rows):
    """Prepare the batched representation of each mutually-disjoint selector block.

    A "block" is a (start, end) range into ``selectors`` produced by one margin
    (e.g. all commune x sex x age cells of the population margin). Because every
    df_model row belongs to exactly one cell of a margin, the selectors within a
    block are mutually disjoint, so the sequential per-selector update order
    within the block cannot influence the result -- updating cell A's rows never
    changes cell B's current-weight sum. That is the mathematical precondition
    for replacing the per-selector ``np.sum``/scatter loop with one
    ``np.bincount`` + one vectorised multiply per block.

    Disjointness is VERIFIED here (total appended row count must equal the
    number of distinctly labelled rows); a block that fails the check -- e.g. a
    margin table with duplicated keys -- falls back to the sequential
    per-selector path, so batching can never silently double-update a row.

    Returns a list of ``(kind, start, end, data)`` tuples where ``kind`` is
    ``"batched"`` (data = (member_rows, member_codes, target_array)) or
    ``"sequential"`` (data = None).
    """
    blocks = []
    for start, end in selector_blocks:
        if end - start <= 1:
            blocks.append(("sequential", start, end, None))
            continue
        labels = np.full(n_rows, -1, dtype=np.int64)
        total_rows = 0
        for j in range(start, end):
            rows = selectors[j][0]
            total_rows += len(rows)
            labels[rows] = j - start
        if total_rows != int(np.count_nonzero(labels >= 0)):
            # Overlapping selectors within the block: not a partition.
            blocks.append(("sequential", start, end, None))
            continue
        member_rows = np.flatnonzero(labels >= 0)
        member_codes = labels[member_rows]
        target_array = np.asarray(targets[start:end], dtype=float)
        blocks.append(("batched", start, end, (member_rows, member_codes, target_array)))
    return blocks


def run_ipf_iterations(selectors, targets, weights, *, max_iterations, tolerance,
                       selector_blocks=None, batched=False, log=print):
    """Run the IPF update loop, optionally batching disjoint selector blocks.

    Sequential mode (``batched=False``) is the verbatim legacy loop: one
    ``np.sum`` gather + one scatter-multiply per selector per iteration --
    byte-identical to all prior runs.

    Batched mode computes, per disjoint block, all cell sums in ONE
    ``np.bincount`` pass and applies all update factors in ONE vectorised
    multiply. The per-row multiplication is the identical operation (each row
    is multiplied exactly once by its cell's factor); only the SUM accumulation
    order differs (bincount accumulates sequentially, ``np.sum`` pairwise), so
    results agree to floating-point round-off (~1e-15 relative) but are NOT
    bit-identical -- enabling this is a deliberate re-baseline of the seeded
    synthesis. Blocks that fail the runtime disjointness check (and the
    single-selector zero constraints) always use the sequential update.

    Returns ``(weights, iteration, converged, iteration_factors)``.
    """
    if batched:
        if selector_blocks is None:
            raise ValueError(
                "[braunschweig.ipf.model] batched=True requires selector_blocks "
                "(the (start, end) ranges of the margin blocks)."
            )
        covered = sum(end - start for start, end in selector_blocks)
        if covered != len(selectors):
            raise ValueError(
                f"[braunschweig.ipf.model] selector_blocks cover {covered} of "
                f"{len(selectors)} selectors; the block bookkeeping is out of "
                "sync with the constraint generation."
            )
        blocks = _build_iteration_blocks(selectors, targets, selector_blocks, len(weights))
        n_batched = sum(1 for kind, *_ in blocks if kind == "batched")
        log(
            f"[braunschweig.ipf.model] batched IPF iteration: "
            f"{n_batched}/{len(blocks)} blocks batched (disjointness verified), "
            f"{len(blocks) - n_batched} sequential."
        )
    else:
        blocks = [("sequential", 0, len(selectors), None)]

    iteration = 0
    converged = False
    iteration_factors = []

    while iteration < max_iterations:
        iteration_factors = []

        for kind, start, end, data in blocks:
            if kind == "sequential":
                for j in range(start, end):
                    f = selectors[j]
                    target_weight = targets[j]
                    current_weight = np.sum(weights[f])

                    if current_weight > 0:
                        update_factor = target_weight / current_weight
                        weights[f] *= update_factor
                        iteration_factors.append(update_factor)
                    elif target_weight > 0:
                        # Cell has zero current weight but a positive target:
                        # re-seed with a tiny epsilon to allow recovery (see
                        # the hh-size hard zero interaction at small communes).
                        weights[f] = 1e-9
                        iteration_factors.append(target_weight / np.sum(weights[f]))
            else:
                member_rows, member_codes, target_array = data
                sums = np.bincount(
                    member_codes, weights=weights[member_rows],
                    minlength=end - start,
                )
                positive = sums > 0
                factors = np.zeros(end - start)
                factors[positive] = target_array[positive] / sums[positive]
                apply_mask = positive[member_codes]
                rows_apply = member_rows[apply_mask]
                weights[rows_apply] *= factors[member_codes[apply_mask]]
                iteration_factors.extend(factors[positive].tolist())
                # Zero-current, positive-target cells: epsilon re-seed (rare).
                for j in np.flatnonzero(~positive & (target_array > 0)):
                    f = selectors[start + int(j)]
                    weights[f] = 1e-9
                    iteration_factors.append(target_array[j] / np.sum(weights[f]))

        if iteration % 50 == 0 or iteration == max_iterations - 1:
            log(
                "Iteration: {} factors: {} mean: {} min: {} max: {}".format(
                    iteration, len(iteration_factors),
                    np.mean(iteration_factors),
                    np.min(iteration_factors), np.max(iteration_factors),
                )
            )

        if np.max(iteration_factors) - 1 < tolerance:
            if np.min(iteration_factors) > 1 - tolerance:
                converged = True
                break

        iteration += 1

    return weights, iteration, converged, iteration_factors


def load_employment_by_hhsize_targets(path_config_value, data_path):
    """Load the (Kreis x hh_size x employed) cross-tab for the TASK-010 margin.

    The margin has exactly one source: a long-form CSV with the columns
    ``departement_id, hh_size, employed, weight`` (Zensus 2022 13111-06-02-4 or
    an equivalent register cross-tab), configured through
    ``braunschweig.ipf.employment_by_hhsize_path``.

    There is deliberately NO substitute. Until ADR-0080 an absent cross-tab fell
    back to the outer product of the existing employment and hh_size marginals,
    described in the code as adding no information beyond what the IPF already
    enforced. That is false: a product of marginals is not implied by those
    marginals, so the block imposed statistical independence of employment and
    household size, overwriting the correlation carried by the donor seed. Every
    base margin stayed satisfied, so neither convergence nor the post-IPF margin
    check could reveal it (issue #252). Failing loudly is the honest alternative
    -- the flag is only meaningful with real data behind it.

    :param path_config_value: ``braunschweig.ipf.employment_by_hhsize_path``
        (``None`` when unset); relative values resolve against ``data_path``.
    :param data_path: the run's ``data_path``.
    :returns: DataFrame with ``departement_id`` (str), ``hh_size`` (str),
        ``employed`` (bool) and ``weight``.
    :raises RuntimeError: when no path is configured, the file does not exist,
        or the required columns are missing.
    """
    if not path_config_value:
        raise RuntimeError(
            "[braunschweig.ipf.model] braunschweig.ipf.use_employment_margin is "
            "enabled but braunschweig.ipf.employment_by_hhsize_path is unset. "
            "The (Kreis x hh_size x employed) margin requires a real cross-tab "
            "in long form with columns departement_id, hh_size, employed, "
            "weight (Zensus 2022 13111-06-02-4 or equivalent). Configure that "
            "path, or set braunschweig.ipf.use_employment_margin: false. No "
            "substitute margin is constructed -- see ADR-0080."
        )

    full_path = (
        path_config_value if os.path.isabs(path_config_value)
        else os.path.join(data_path, path_config_value)
    )
    if not os.path.exists(full_path):
        raise RuntimeError(
            "[braunschweig.ipf.model] braunschweig.ipf.employment_by_hhsize_path "
            f"resolves to {full_path}, which does not exist. Provide the "
            "(Kreis x hh_size x employed) cross-tab there, or set "
            "braunschweig.ipf.use_employment_margin: false."
        )

    emp_targets_long = pd.read_csv(full_path, dtype={
        "departement_id": str,
        "hh_size": str,
    })
    required_columns = {"departement_id", "hh_size", "employed", "weight"}
    missing = required_columns - set(emp_targets_long.columns)
    if missing:
        raise RuntimeError(
            f"[braunschweig.ipf.model] {full_path} missing columns: "
            f"{sorted(missing)}"
        )
    emp_targets_long["employed"] = emp_targets_long["employed"].astype(bool)

    print(
        "[braunschweig.ipf.model] Loaded {:,} (Kreis x hh_size x employed) "
        "targets from {}".format(len(emp_targets_long), full_path)
    )
    return emp_targets_long


def _map_departement_index(emp_targets_long, dep_id_to_index):
    """Map the employment-margin ``departement_id`` onto the IPF Kreis index.

    Join-coverage transparency (CLAUDE.md no-silent-fallback): rows whose
    ``departement_id`` is absent from the population's Kreis set are dropped
    (legitimate when the CSV covers more Kreise than the scope), but the drop
    is COUNTED and logged -- and if NOTHING matches, the configured margin
    would be silently inert (zero constraints appended, feature "on" doing
    nothing), which raises instead.
    """
    emp_targets_long = emp_targets_long.copy()
    emp_targets_long["departement_index"] = (
        emp_targets_long["departement_id"].astype(str).map(dep_id_to_index)
    )
    dropped = emp_targets_long[emp_targets_long["departement_index"].isna()]
    kept = emp_targets_long.dropna(subset=["departement_index"]).copy()
    if len(kept) == 0:
        raise RuntimeError(
            "[braunschweig.ipf.model] employment-by-hhsize margin: 0 of "
            f"{len(emp_targets_long)} target rows matched the population Kreis "
            f"set {sorted(dep_id_to_index)} (CSV departement_id sample: "
            f"{sorted(dropped['departement_id'].astype(str).unique())[:5]}). "
            "The configured margin would be silently inert -- most likely a "
            "key-format mismatch (e.g. un-padded Kreis codes)."
        )
    if len(dropped):
        print(
            "[braunschweig.ipf.model] employment-by-hhsize margin: matched "
            f"{len(kept)}/{len(emp_targets_long)} target rows; dropped "
            f"{len(dropped)} rows outside the population Kreis set: "
            f"{sorted(dropped['departement_id'].astype(str).unique())[:5]}"
        )
    kept["departement_index"] = kept["departement_index"].astype(int)
    return kept


def configure(context):
    context.stage("braunschweig.ipf.prepare")
    context.config("braunschweig.minimum_age.employment", 16)
    context.config("braunschweig.minimum_age.one_person_household", 16)
    context.config("braunschweig.ipf.use_household_size_margin", False)
    # Optional joint age x hh_size margin (Zensus 2022 1000A-3082). Adds a
    # per-Kreis (age_group x hh_size) selector block so the IPF reproduces the
    # observed age x size correlation, not just the two independent marginals.
    # Requires use_household_size_margin. Default off -> IPF unchanged.
    context.config("braunschweig.ipf.use_joint_age_size_margin", False)
    if context.config("braunschweig.ipf.use_joint_age_size_margin"):
        # Coarse age-group lower bounds, tracked as config so a change invalidates
        # the cache. Must match braunschweig.ipf.prepare's value.
        from braunschweig.ipf.joint_age_size import DEFAULT_AGE_GROUP_BOUNDS
        context.config("braunschweig.ipf.joint_age_group_bounds",
                       list(DEFAULT_AGE_GROUP_BOUNDS))
    context.config("braunschweig.ipf.max_iterations", 1500)
    context.config("braunschweig.ipf.tolerance", 1e-2)
    # Batched IPF iteration: compute each disjoint margin block's cell sums in
    # one np.bincount pass instead of one np.sum per selector (the per-selector
    # call overhead dominates at ~30k selectors x 1500 iterations). Results
    # agree to FP round-off but are NOT bit-identical to the sequential loop
    # (bincount accumulates sequentially, np.sum pairwise) -- enabling this is
    # a deliberate re-baseline. False restores the byte-identical legacy loop.
    context.config("braunschweig.ipf.batched_iteration", True)
    # TASK-010 — additional 4-way (Kreis × hh_size × employed) joint
    # margin sourced from a Kreis-level cross-tab (e.g. Zensus 2022
    # 13111-06-02-4 reshaped to long form). Requires
    # ``use_household_size_margin`` to be on, and requires
    # ``employment_by_hhsize_path`` to name an existing cross-tab: with
    # the flag on and no cross-tab the stage RAISES (ADR-0080). It used
    # to substitute the outer product of the existing employment- and
    # hh_size-margins, which is not the no-op its comment claimed — a
    # product of marginals is not implied by those marginals, so the
    # block forced employment and household size to be independent and
    # overwrote the donor seed's correlation (issue #252). Default off.
    context.config("braunschweig.ipf.use_employment_margin", False)
    context.config(
        "braunschweig.ipf.employment_by_hhsize_path",
        None,
    )
    # TASK-011 — symmetric-Dirichlet prior strength α (pseudo-counts
    # added uniformly to the IPF seed). 0.0 disables the prior (=
    # bit-identical to legacy behaviour). Typical calibrated values are
    # in the range 0.01..1.0; higher α more strongly damps the
    # contribution of zero/near-zero source cells in sparse Kreise.
    context.config("braunschweig.ipf.dirichlet_prior_strength", 0.0)
    # Per-cell margin-deviation tolerance for the post-IPF control check.
    # Any cell whose achieved weight differs from its target by more than
    # this fraction triggers a hard failure. Defaults to 1 % which is
    # roughly 5x the iteration tolerance — tight enough to catch
    # systematic infeasibilities but loose enough to absorb the residual
    # IPF wiggle.
    context.config("braunschweig.ipf.margin_validation_tolerance", 0.01)


def execute(context):
    from braunschweig.ipf.config_validation import (
        MODEL_REQUIREMENTS, validate_household_realism_config)

    # Fail fast on an invalid household-realism flag combination (this stage's
    # subset: joint / employment margins require the size margin), before any
    # expensive IPF work. Single source of truth: braunschweig.ipf.config_validation.
    validate_household_realism_config(context.config, MODEL_REQUIREMENTS)

    (df_population, df_employment, df_licenses_country, df_licenses_kreis,
     df_household_size, df_joint_age_size) = context.stage("braunschweig.ipf.prepare")

    use_hh_size = context.config("braunschweig.ipf.use_household_size_margin")
    use_joint_age_size = context.config("braunschweig.ipf.use_joint_age_size_margin")

    # Construct a combined age class
    population_age_classes = np.sort(df_population["age_class"].unique())
    population_age_upper = list(population_age_classes[1:]) + [9999]

    employment_age_classes = np.sort(df_employment["age_class"].unique())
    employment_age_upper = list(employment_age_classes[1:]) + [9999]

    minimum_employment_age = context.config("braunschweig.minimum_age.employment")

    license_age_classes = np.sort(df_licenses_country["age_class"].unique())
    license_age_upper = list(license_age_classes[1:]) + [9999]
    
    combined_age_classes = np.array(np.sort(list(
        set(population_age_classes) | 
        set(employment_age_classes) |
        set(license_age_classes) | 
        set([minimum_employment_age]))))
    
    population_age_mapping = {}
    employment_age_mapping = {}
    license_age_mapping = {}

    for age_class in combined_age_classes:
        population_age_mapping[age_class] = population_age_classes[np.count_nonzero(population_age_upper <= age_class)]
        employment_age_mapping[age_class] = employment_age_classes[np.count_nonzero(employment_age_upper <= age_class)]
        license_age_mapping[age_class] = license_age_classes[np.count_nonzero(license_age_upper <= age_class)]

    # Construct other unique values
    unique_sexes = np.sort(list(set(df_population["sex"]) | set(df_employment["sex"])))
    unique_employed = [True, False]
    unique_communes = np.sort(df_population["commune_index"].unique())
    unique_departements = np.sort(df_employment["departement_index"].unique())
    unique_license = [True, False]

    # Initialize the seed with all combinations of values
    if use_hh_size:
        unique_hh_sizes = list(HH_SIZE_BINS)
        index = pd.MultiIndex.from_product([
            unique_communes, unique_sexes, combined_age_classes, unique_employed,
            unique_license, unique_hh_sizes
        ], names=["commune_index", "sex", "combined_age_class",
                  "employed", "license", "hh_size"])
    else:
        index = pd.MultiIndex.from_product([
            unique_communes, unique_sexes, combined_age_classes, unique_employed, unique_license
        ], names = ["commune_index", "sex", "combined_age_class", "employed", "license"])

    df_model = pd.DataFrame(index = index).reset_index()
    df_model["weight"] = 1.0

    # Provide a prior based on the size of the age classes
    combined_age_classes_sizes = {
        lower: upper - lower for
        lower, upper in zip(combined_age_classes[:-1], combined_age_classes[1:])
    }
    combined_age_classes_sizes[combined_age_classes[-1]] = 1.0
    df_model["weight"] *= df_model["combined_age_class"].apply(lambda c: combined_age_classes_sizes[c])

    # TASK-011 — symmetric-Dirichlet smoothing (sparse-cell prior).
    # Adds α pseudo-counts uniformly to every seed cell. The IPF then
    # rescales these to satisfy the margins, so very sparse Gemeinden
    # (rural Goslar/Helmstedt) cannot collapse a cell weight to ~0
    # before the margins have a chance to lift it. With α = 0 (default)
    # this branch is a no-op and the iteration is bit-identical to the
    # legacy formulation.
    dirichlet_alpha = float(context.config("braunschweig.ipf.dirichlet_prior_strength"))
    if dirichlet_alpha > 0.0:
        df_model["weight"] = df_model["weight"] + dirichlet_alpha
        print(
            f"[braunschweig.ipf.model] Dirichlet prior α = {dirichlet_alpha:g} "
            f"applied to {len(df_model):,} seed cells "
            f"(seed weight now Σ = {df_model['weight'].sum():,.1f})"
        )

    # Attach departement indices
    df_spatial = df_population[["commune_index", "departement_index"]].drop_duplicates()
    df_model["departement_index"] = df_model["commune_index"].replace(dict(zip(
        df_spatial["commune_index"], df_spatial["departement_index"]
    )))

    # Attach individual age classes
    df_model["age_class_population"] = df_model["combined_age_class"].replace(population_age_mapping)
    df_model["age_class_employment"] = df_model["combined_age_class"].replace(employment_age_mapping)
    df_model["age_class_license"] = df_model["combined_age_class"].replace(license_age_mapping)

    # Initialize weighting selectors and targets.
    #
    # PERFORMANCE: the per-constraint row-index arrays into ``df_model`` are no
    # longer built by scanning the full ``df_model`` with chained boolean masks
    # (O(constraints x |df_model|), the dominant cost at 100 %). Instead each
    # constraint group does ONE ``groupby(...).indices`` pass over ``df_model``
    # and every combination is an O(1) dict lookup. ``groupby().indices`` returns
    # the ascending row positions per group key -- byte-identical to the array
    # ``np.nonzero(mask.values)`` produced for the equivalent boolean mask. Target
    # weights still come from the SMALL reference tables via the original
    # ``df_reference.loc[mask, "weight"].sum()`` call, preserving the exact
    # floating-point summation (groupby-sum is NOT bit-identical to Series.sum).
    selectors = []
    targets = []
    # Block bookkeeping for the batched IPF iteration: each entry is a
    # (start, end) range into ``selectors`` whose members come from ONE margin
    # and are therefore expected to be mutually disjoint (a margin partitions
    # df_model). Disjointness is re-verified at runtime in
    # _build_iteration_blocks; failing blocks fall back to the sequential
    # per-selector update. Single-selector zero constraints are their own
    # (trivially sequential) blocks.
    selector_blocks = []

    # Population constraints (commune x sex x population-age-class).
    _block_start = len(selectors)
    population_indices = _build_group_indices(
        df_model, ["commune_index", "sex", "age_class_population"])
    combinations = list(itertools.product(unique_communes, unique_sexes, population_age_classes))
    for combination in context.progress(combinations, total = len(combinations), label = "Generating population constraints"):
        f_reference = df_population["commune_index"] == combination[0]
        f_reference &= df_population["sex"] == combination[1]
        f_reference &= df_population["age_class"] == combination[2]

        selectors.append(population_indices.get(tuple(combination), _EMPTY_SELECTOR))

        target_weight = df_population.loc[f_reference, "weight"].sum()
        targets.append(target_weight)

    selector_blocks.append((_block_start, len(selectors)))

    # Employment constraints (departement x sex x employment-age-class, employed only).
    # The legacy ``& df_model["employed"]`` filter is folded into the groupby key
    # so the (..., True) sub-key holds exactly the employed rows of each cell.
    _block_start = len(selectors)
    employment_indices = _build_group_indices(
        df_model, ["departement_index", "sex", "age_class_employment", "employed"])
    combinations = list(itertools.product(unique_departements, unique_sexes, employment_age_classes))
    for combination in context.progress(combinations, total = len(combinations), label = "Generating employment constraints"):
        f_reference = df_employment["departement_index"] == combination[0]
        f_reference &= df_employment["sex"] == combination[1]
        f_reference &= df_employment["age_class"] == combination[2]

        selectors.append(
            employment_indices.get((combination[0], combination[1], combination[2], True),
                                   _EMPTY_SELECTOR))

        target_weight = df_employment.loc[f_reference, "weight"].sum()
        targets.append(target_weight)

    selector_blocks.append((_block_start, len(selectors)))

    # Minimum employment age (single condition, cheap: kept as a boolean mask).
    f_model = df_model["combined_age_class"] < minimum_employment_age
    f_model &= df_model["employed"]
    selectors.append(f_model)
    targets.append(0.0)
    selector_blocks.append((len(selectors) - 1, len(selectors)))

    # License country constraints (sex x license-age-class, license owners only).
    _block_start = len(selectors)
    license_country_indices = _build_group_indices(
        df_model, ["sex", "age_class_license", "license"])
    combinations = list(itertools.product(unique_sexes, license_age_classes))
    for combination in context.progress(combinations, total = len(combinations), label = "Generating license constraints"):
        f_reference = df_licenses_country["sex"] == combination[0]
        f_reference &= df_licenses_country["age_class"] == combination[1]

        selectors.append(
            license_country_indices.get((combination[0], combination[1], True), _EMPTY_SELECTOR))

        target_weight = df_licenses_country.loc[f_reference, "weight"].sum()
        targets.append(target_weight)

    selector_blocks.append((_block_start, len(selectors)))

    # License Kreis constraints (departement, license owners only).
    _block_start = len(selectors)
    license_kreis_indices = _build_group_indices(
        df_model, ["departement_index", "license"])
    for departement_index in context.progress(unique_departements, total = len(unique_departements), label = "Generating license constraints per Kreis"):
        f_reference = df_licenses_kreis["departement_index"] == departement_index

        selectors.append(
            license_kreis_indices.get((departement_index, True), _EMPTY_SELECTOR))

        target_weight = df_licenses_kreis.loc[f_reference, "weight"].sum()
        targets.append(target_weight)

    selector_blocks.append((_block_start, len(selectors)))

    # Household-size constraints (commune × hh_size).
    # Adds one selector per (commune, hh_size) cell with target = persons
    # in that bin from Zensus 2022 1000A-2081 (rescaled to match the
    # commune population total in braunschweig.ipf.prepare).
    if use_hh_size:
        # Build commune_id -> commune_index mapping (from df_population)
        commune_id_to_index = dict(zip(
            df_population["commune_id"].astype(str),
            df_population["commune_index"],
        ))
        df_hh = df_household_size.copy()
        df_hh["commune_index"] = df_hh["commune_id"].astype(str).map(commune_id_to_index)
        df_hh = df_hh.dropna(subset=["commune_index"]).copy()
        df_hh["commune_index"] = df_hh["commune_index"].astype(int)

        hh_combinations = list(
            itertools.product(unique_communes, unique_hh_sizes)
        )
        hh_targets = (
            df_hh.set_index(["commune_index", "hh_size"])["weight"].to_dict()
        )
        hh_size_indices = _build_group_indices(
            df_model, ["commune_index", "hh_size"])
        _block_start = len(selectors)
        for combination in context.progress(
            hh_combinations, total=len(hh_combinations),
            label="Generating household-size constraints",
        ):
            target_weight = hh_targets.get(combination, 0.0)
            selectors.append(hh_size_indices.get(tuple(combination), _EMPTY_SELECTOR))
            targets.append(float(target_weight))
        selector_blocks.append((_block_start, len(selectors)))

        # Hard zero: persons younger than ``minimum_age_one_person_household``
        # cannot live in a 1-person household.
        minimum_one_person_age = context.config(
            "braunschweig.minimum_age.one_person_household"
        )
        f_model = df_model["combined_age_class"] < minimum_one_person_age
        f_model &= df_model["hh_size"] == "1"
        selectors.append(f_model)
        targets.append(0.0)
        selector_blocks.append((len(selectors) - 1, len(selectors)))

    # Joint age × household-size constraints (departement × age_group × hh_size).
    # Sourced from the Kreis-level raked joint (braunschweig.ipf.joint_age_size),
    # which is consistent with the population age marginal and the size marginal,
    # so it ties the two together with the observed Zensus correlation without
    # making the IPF infeasible (its 1D marginals equal the existing margins).
    if use_joint_age_size:
        # use_hh_size is guaranteed on here (validate_household_realism_config).
        from braunschweig.ipf.joint_age_size import age_group_lower
        bounds = tuple(context.config("braunschweig.ipf.joint_age_group_bounds"))
        df_model["age_group"] = df_model["combined_age_class"].map(
            lambda a: age_group_lower(a, bounds))
        dep_id_to_index = dict(zip(
            df_population["departement_id"].astype(str),
            df_population["departement_index"],
        ))
        dj = df_joint_age_size.copy()
        dj["departement_index"] = (
            dj["departement_id"].astype(str).map(dep_id_to_index)
        )
        dj = dj.dropna(subset=["departement_index"]).copy()
        dj["departement_index"] = dj["departement_index"].astype(int)
        joint_indices = _build_group_indices(
            df_model, ["departement_index", "age_group", "hh_size"])
        _block_start = len(selectors)
        for _, row in context.progress(
            list(dj.iterrows()), total=len(dj),
            label="Generating joint age x hh_size constraints",
        ):
            key = (int(row["departement_index"]), int(row["age_group_lower"]),
                   row["hh_size"])
            selectors.append(joint_indices.get(key, _EMPTY_SELECTOR))
            targets.append(float(row["weight"]))
        selector_blocks.append((_block_start, len(selectors)))

    # TASK-010 — optional Kreis × hh_size × employed joint margin.
    # Sourced exclusively from a long-form CSV with columns
    # ``departement_id, hh_size, employed, weight`` (Zensus 2022
    # 13111-06-02-4 or equivalent) named by
    # ``braunschweig.ipf.employment_by_hhsize_path``. An absent cross-tab
    # raises: constructing a substitute from the existing marginals would
    # impose an independence assumption the data does not support
    # (issue #252, ADR-0080).
    use_employment_margin = bool(
        context.config("braunschweig.ipf.use_employment_margin")
    )
    # use_hh_size is guaranteed on when use_employment_margin is set
    # (validate_household_realism_config at the top of execute()).
    if use_employment_margin:
        emp_targets_long = load_employment_by_hhsize_targets(
            context.config("braunschweig.ipf.employment_by_hhsize_path"),
            context.config("data_path"),
        )

        # Map departement_id -> departement_index
        dep_id_to_index = dict(zip(
            df_population["departement_id"].astype(str),
            df_population["departement_index"],
        ))
        emp_targets_long = _map_departement_index(emp_targets_long, dep_id_to_index)

        emp_margin_indices = _build_group_indices(
            df_model, ["departement_index", "hh_size", "employed"])
        _block_start = len(selectors)
        matched_cells = 0
        for _, row in context.progress(
            list(emp_targets_long.iterrows()),
            total=len(emp_targets_long),
            label="Generating employment-by-hhsize constraints",
        ):
            key = (int(row["departement_index"]), row["hh_size"], bool(row["employed"]))
            selector = emp_margin_indices.get(key)
            if selector is None:
                selector = _EMPTY_SELECTOR
            else:
                matched_cells += 1
            selectors.append(selector)
            targets.append(float(row["weight"]))
        selector_blocks.append((_block_start, len(selectors)))

        # Cell-level join coverage (CLAUDE.md no-silent-fallback): a target cell
        # with no matching df_model rows contributes an EMPTY selector, i.e. an
        # inert constraint. That is legitimate for genuinely unoccupied cells but
        # indistinguishable from a key-format mismatch unless the rate is stated.
        n_cells = len(emp_targets_long)
        print(
            "[braunschweig.ipf.model] employment-by-hhsize margin: matched "
            "{:,}/{:,} target cells ({:.1%}), {:,} inert ({:.1%}).".format(
                matched_cells, n_cells, matched_cells / max(n_cells, 1),
                n_cells - matched_cells,
                (n_cells - matched_cells) / max(n_cells, 1),
            )
        )
        if matched_cells == 0:
            raise RuntimeError(
                "[braunschweig.ipf.model] employment-by-hhsize margin: none of "
                f"the {n_cells} target cells matched a (Kreis, hh_size, "
                "employed) combination in the model -- every appended "
                "constraint would be inert, so the enabled margin would do "
                "nothing. Most likely an hh_size bin-label mismatch (expected "
                f"{list(HH_SIZE_BINS)})."
            )

    # Transform to index-based. Vectorised constraint groups already appended
    # ``(ascending_int64_index_array,)`` tuples (the exact representation of
    # ``np.nonzero(mask.values)``); the remaining single-condition selectors are
    # still boolean Series and are converted here. This keeps every selector in
    # the identical ``np.nonzero``-tuple form the IPF loop expects.
    selectors = [
        s if isinstance(s, tuple) else np.nonzero(s.values)
        for s in selectors
    ]

    # Perform IPF
    weights = df_model["weight"].values

    max_iterations = context.config("braunschweig.ipf.max_iterations")
    tolerance = context.config("braunschweig.ipf.tolerance")
    batched = bool(context.config("braunschweig.ipf.batched_iteration"))

    weights, iteration, converged, iteration_factors = run_ipf_iterations(
        selectors, targets, weights,
        max_iterations=max_iterations, tolerance=tolerance,
        selector_blocks=selector_blocks, batched=batched,
    )

    df_model["weight"] = weights

    assert converged, (
        f"IPF did not converge in {max_iterations} iterations "
        f"(last factor range: [{np.min(iteration_factors):.6f}, "
        f"{np.max(iteration_factors):.6f}], tolerance: {tolerance})"
    )

    # ------------------------------------------------------------------
    # Post-IPF margin validation (control variables).
    #
    # IPF is iterative: the loop terminates when the per-iteration
    # update factors stay within ``tolerance`` of 1.0, but that does NOT
    # guarantee that every individual margin matches its target — only
    # that the *worst-cell update step* is small. This block performs an
    # independent end-of-run check by comparing the achieved per-cell
    # weight sums to the targets, raising a hard error if any cell
    # diverges by more than the configured tolerance. Without this, a
    # systematically infeasible target table (e.g. a malformed Zensus
    # extract) can silently produce a "converged" but biased population.
    # ------------------------------------------------------------------
    try:
        margin_tolerance = float(context.config("braunschweig.ipf.margin_validation_tolerance"))
    except Exception:
        margin_tolerance = 0.01
    achieved = np.array([weights[f].sum() for f in selectors])
    targets_arr = np.array(targets, dtype=float)
    nonzero = targets_arr > 0
    rel_err = np.zeros_like(targets_arr)
    rel_err[nonzero] = np.abs(
        achieved[nonzero] - targets_arr[nonzero]
    ) / targets_arr[nonzero]
    abs_err_zero = np.abs(achieved[~nonzero])
    n_persons_total = float(df_model["weight"].sum())
    print(
        f"[braunschweig.ipf.model] post-IPF margin check: {len(targets)} cells, "
        f"max relative deviation on positive targets = {rel_err.max():.4%}, "
        f"max absolute deviation on zero targets = "
        f"{abs_err_zero.max() if len(abs_err_zero) else 0.0:.4f} "
        f"(of {n_persons_total:,.0f} total weight)."
    )
    bad = np.where(nonzero & (rel_err > margin_tolerance))[0]
    if len(bad) > 0:
        worst = bad[np.argsort(-rel_err[bad])[:5]]
        details = "\n  ".join(
            f"selector #{i}: target={targets_arr[i]:.2f}, "
            f"achieved={achieved[i]:.2f}, rel_err={rel_err[i]:.2%}"
            for i in worst
        )
        raise RuntimeError(
            f"IPF margin validation failed: {len(bad)} cells exceed "
            f"tolerance ({margin_tolerance:.2%}). Worst offenders:\n  "
            + details
        )
    # Zero-target violations: any cell that should be empty (e.g. minors
    # in 1-person households) but ends up with significant weight is a
    # hard data integrity bug.
    zero_thresh = max(1.0, 1e-6 * n_persons_total)
    zero_idx = np.where(~nonzero)[0]
    zero_bad_local = np.where(abs_err_zero > zero_thresh)[0]
    if len(zero_bad_local) > 0:
        zero_bad = zero_idx[zero_bad_local]
        worst = zero_bad[np.argsort(-abs_err_zero[zero_bad_local])[:5]]
        details = "\n  ".join(
            f"selector #{i}: target=0.0, achieved={achieved[i]:.4f}"
            for i in worst
        )
        raise RuntimeError(
            f"IPF zero-target violation: {len(zero_bad)} cells with "
            f"target=0 exceed threshold {zero_thresh:.4f}.\n  " + details
        )

    # Reestablish sex categories
    df_model["sex"] = df_model["sex"].replace({ 1: "male", 2: "female" }).astype("category")

    # Add identifiers
    df_model = pd.merge(df_model, df_population[["commune_index", "commune_id"]].drop_duplicates(), on = "commune_index", how = "left")
    assert np.count_nonzero(df_model["commune_id"].isna()) == 0

    df_model = pd.merge(df_model, df_population[["departement_index", "departement_id"]].drop_duplicates(), on = "departement_index", how = "left")
    assert np.count_nonzero(df_model["departement_id"].isna()) == 0

    df_model = df_model.rename(columns = { "combined_age_class": "age_class" })

    output_columns = ["commune_id", "departement_id", "sex", "age_class",
                      "employed", "license", "weight"]
    if use_hh_size:
        df_model["hh_size"] = df_model["hh_size"].astype("category")
        output_columns.insert(-1, "hh_size")
    return df_model[output_columns]
