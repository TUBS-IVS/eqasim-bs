"""IPF model stage - joint distribution from Kreis x Gemeinde marginals.

Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/model.py``.
Adapted for Braunschweig:
- Config keys are ``braunschweig.ipf.*``; the legacy ``bavaria.ipf.*`` mapping
  was removed in Phase 4.3 along with the rest of the bavaria/ tree.
- Includes a post-IPF margin-deviation control check (BUG-009 fix).
"""
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
    # TASK-010 — additional 4-way (Kreis × hh_size × employed) joint
    # margin sourced from a Kreis-level cross-tab (e.g. Zensus 2022
    # 13111-06-02-4 reshaped to long form). Requires
    # ``use_household_size_margin`` to be on. When the flag is enabled
    # but no supporting CSV is configured, the seed-prior is computed
    # via outer product of the existing employment- and hh_size-margins
    # (a pure marginal-consistency check that does not add information
    # beyond what the existing IPF already enforces — useful as a
    # smoke test before wiring real Zensus cross-tabs).
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

    # Population constraints (commune x sex x population-age-class).
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

    # Employment constraints (departement x sex x employment-age-class, employed only).
    # The legacy ``& df_model["employed"]`` filter is folded into the groupby key
    # so the (..., True) sub-key holds exactly the employed rows of each cell.
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

    # Minimum employment age (single condition, cheap: kept as a boolean mask).
    f_model = df_model["combined_age_class"] < minimum_employment_age
    f_model &= df_model["employed"]
    selectors.append(f_model)
    targets.append(0.0)

    # License country constraints (sex x license-age-class, license owners only).
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

    # License Kreis constraints (departement, license owners only).
    license_kreis_indices = _build_group_indices(
        df_model, ["departement_index", "license"])
    for departement_index in context.progress(unique_departements, total = len(unique_departements), label = "Generating license constraints per Kreis"):
        f_reference = df_licenses_kreis["departement_index"] == departement_index

        selectors.append(
            license_kreis_indices.get((departement_index, True), _EMPTY_SELECTOR))

        target_weight = df_licenses_kreis.loc[f_reference, "weight"].sum()
        targets.append(target_weight)

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
        for combination in context.progress(
            hh_combinations, total=len(hh_combinations),
            label="Generating household-size constraints",
        ):
            target_weight = hh_targets.get(combination, 0.0)
            selectors.append(hh_size_indices.get(tuple(combination), _EMPTY_SELECTOR))
            targets.append(float(target_weight))

        # Hard zero: persons younger than ``minimum_age_one_person_household``
        # cannot live in a 1-person household.
        minimum_one_person_age = context.config(
            "braunschweig.minimum_age.one_person_household"
        )
        f_model = df_model["combined_age_class"] < minimum_one_person_age
        f_model &= df_model["hh_size"] == "1"
        selectors.append(f_model)
        targets.append(0.0)

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
        for _, row in context.progress(
            list(dj.iterrows()), total=len(dj),
            label="Generating joint age x hh_size constraints",
        ):
            key = (int(row["departement_index"]), int(row["age_group_lower"]),
                   row["hh_size"])
            selectors.append(joint_indices.get(key, _EMPTY_SELECTOR))
            targets.append(float(row["weight"]))

    # TASK-010 — optional Kreis × hh_size × employed joint margin.
    # Sourced from a long-form CSV with columns
    # ``departement_id, hh_size, employed, weight`` (Zensus 2022
    # 13111-06-02-4 or equivalent). When the path is ``None`` or the
    # file does not exist, fall back to an outer-product proxy derived
    # from the existing employment- and hh_size-margins; this smoke
    # tests the wiring without changing IPF behaviour materially.
    use_employment_margin = bool(
        context.config("braunschweig.ipf.use_employment_margin")
    )
    # use_hh_size is guaranteed on when use_employment_margin is set
    # (validate_household_realism_config at the top of execute()).
    if use_employment_margin:
        emp_path_cfg = context.config("braunschweig.ipf.employment_by_hhsize_path")
        emp_targets_long = None
        if emp_path_cfg:
            import os as _os
            full_path = (
                emp_path_cfg if _os.path.isabs(emp_path_cfg)
                else _os.path.join(context.config("data_path"), emp_path_cfg)
            )
            if _os.path.exists(full_path):
                emp_targets_long = pd.read_csv(full_path, dtype={
                    "departement_id": str,
                    "hh_size": str,
                })
                required_cols = {"departement_id", "hh_size", "employed", "weight"}
                missing = required_cols - set(emp_targets_long.columns)
                if missing:
                    raise RuntimeError(
                        f"[braunschweig.ipf.model] {full_path} missing columns: "
                        f"{sorted(missing)}"
                    )
                emp_targets_long["employed"] = (
                    emp_targets_long["employed"].astype(bool)
                )
                print(
                    "[braunschweig.ipf.model] Loaded {:,} (Kreis × hh_size × "
                    "employed) targets from {}".format(
                        len(emp_targets_long), full_path
                    )
                )
        if emp_targets_long is None:
            # Outer-product proxy from existing margins (informative
            # smoke test only; preserves marginals).
            emp_marginal = (
                df_employment.groupby("departement_id", observed=True)
                ["weight"].sum()
                .rename("emp_total")
            )
            hhsize_marginal = (
                df_household_size.assign(
                    departement_id=df_household_size["commune_id"].astype(str).str[:5]
                )
                .groupby(["departement_id", "hh_size"], observed=True)
                ["weight"].sum()
                .rename("hhsize_total")
            )
            kreis_pop_total = hhsize_marginal.groupby(level=0).sum()
            rows = []
            for (dep_id, hh_size), n_in_hh in hhsize_marginal.items():
                share = n_in_hh / max(kreis_pop_total.get(dep_id, 0.0), 1.0)
                emp_in_hh = float(emp_marginal.get(dep_id, 0.0)) * share
                rows.append({
                    "departement_id": dep_id,
                    "hh_size": hh_size,
                    "employed": True,
                    "weight": emp_in_hh,
                })
                rows.append({
                    "departement_id": dep_id,
                    "hh_size": hh_size,
                    "employed": False,
                    "weight": float(n_in_hh) - emp_in_hh,
                })
            emp_targets_long = pd.DataFrame(rows)
            print(
                "[braunschweig.ipf.model] No employment-by-hhsize CSV configured; "
                f"using outer-product proxy ({len(emp_targets_long)} cells)."
            )

        # Map departement_id -> departement_index
        dep_id_to_index = dict(zip(
            df_population["departement_id"].astype(str),
            df_population["departement_index"],
        ))
        emp_targets_long = emp_targets_long.copy()
        emp_targets_long["departement_index"] = (
            emp_targets_long["departement_id"].astype(str).map(dep_id_to_index)
        )
        emp_targets_long = emp_targets_long.dropna(subset=["departement_index"])
        emp_targets_long["departement_index"] = (
            emp_targets_long["departement_index"].astype(int)
        )

        emp_margin_indices = _build_group_indices(
            df_model, ["departement_index", "hh_size", "employed"])
        for _, row in context.progress(
            list(emp_targets_long.iterrows()),
            total=len(emp_targets_long),
            label="Generating employment-by-hhsize constraints",
        ):
            key = (int(row["departement_index"]), row["hh_size"], bool(row["employed"]))
            selectors.append(emp_margin_indices.get(key, _EMPTY_SELECTOR))
            targets.append(float(row["weight"]))

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
    iteration = 0
    converged = False
    weights = df_model["weight"].values

    max_iterations = context.config("braunschweig.ipf.max_iterations")
    tolerance = context.config("braunschweig.ipf.tolerance")

    while iteration < max_iterations:
        iteration_factors = []
    
        for f, target_weight in zip(selectors, targets):
            current_weight = np.sum(weights[f])
    
            if current_weight > 0:
                update_factor = target_weight / current_weight
                weights[f] *= update_factor
                iteration_factors.append(update_factor)
            elif target_weight > 0:
                # Cell has zero current weight but a positive target: this
                # would be infeasible. Re-seed with a tiny epsilon to allow
                # the IPF to recover. Happens with the hh-size hard zero
                # interaction at very small communes.
                weights[f] = 1e-9
                iteration_factors.append(target_weight / np.sum(weights[f]))

        if iteration % 50 == 0 or iteration == max_iterations - 1:
            print(
                "Iteration:", iteration,
                "factors:", len(iteration_factors),
                "mean:", np.mean(iteration_factors),
                "min:", np.min(iteration_factors),
                "max:", np.max(iteration_factors))
        
        if np.max(iteration_factors) - 1 < tolerance:
            if np.min(iteration_factors) > 1 - tolerance:
                converged = True
                break
    
        iteration += 1

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
