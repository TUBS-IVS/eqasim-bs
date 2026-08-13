"""Enriched population stage - Braunschweig overrides + Bavaria base.

This module is the merged successor of:
- ``bavaria/synthesis/population/enriched.py`` (Origin: eqasim-bavaria @ b20fbe6).
- ``braunschweig/synthesis/population/enriched.py`` (BS-specific MiD-2023
  vehicle counts, INKAR income scaling, BS-resident flag).

Phase 2.8 of the eqasim-bs refactor merged both into a single module so the
BS pipeline no longer delegates through ``bavaria.synthesis.population.enriched``.

Per Decision D-5 the merge is behaviour-preserving: the wrapper-around-wrapper
structure is intentionally preserved (BUG-001 stays untouched; cleanup is
deferred to a separate post-refactor pass).

Pipeline order: ``synthesis.population.enriched`` (eqasim core) -> base
augmentation (car/bike/PT IPF, household size + income sampling) -> BS overlay
(MiD-2023 vehicle counts, INKAR income, BS resident flag).

Package layout (issue #267 split; formerly one ~2900-line module, itself the
rename of the legacy ``enriched.py``): this ``__init__`` is the synpp stage
(``configure``/``execute``/``validate``) and re-exports every submodule name,
so external imports of the stage module path keep working unchanged. The
submodules:

    availability        PT-subscription conditioning (A6) and consistent
                        car_availability (A5)
    base                Inherited eqasim-bavaria configure()/execute() (car/bike/PT
                        IPF, household_size and household_income sampling)
    economic_status    5-class MiD economic status (income-class-derived and
                        MiD hhtype x region Bayes-derived variants)
    housing_tenure      housing_tenure completeness attribute sampled from MiD
                        P(tenure | income_bracket, raumtyp) (Bayes-inverted)
    income_distribution MiD income-bracket distribution draw (with the INKAR
                        Kreis fine tilt) and the legacy INKAR class-midpoint
                        income scaling
    vehicle_ownership  MiD H7/H12.3 vehicle-count sampling, income-aware
                        number_of_cars, and the shared Kreis-ARS / binarisation
                        helpers

``validate()`` hashes all submodule sources into the synpp validation token
because ``get_stage_hash`` only covers this file -- a helper-only change
devalidates the cached stage output exactly like an edit here.
"""

import hashlib
import inspect

from braunschweig.data.mid.reference_tables import load_class_midpoint_eur

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (calibration scripts, tests) keep importing from
# the stage module path unchanged. Each submodule MUST also be listed in
# _HELPER_MODULES below so its source participates in the synpp cache-
# validation token.
# ---------------------------------------------------------------------------

from . import availability, base, economic_status, housing_tenure, income_distribution, vehicle_ownership
from .availability import (  # noqa: F401  (re-exports)
    CAR_AVAILABILITY_CATEGORIES,
    CAR_AVAILABILITY_RAKE_FALLBACK_WARN_RATE,
    _PT_CAR_MARGIN_RAKE_ITERATIONS,
    _apply_car_availability_pt_margin,
    _condition_pt_subscription_probs,
    _derive_car_availability_consistent,
)
from .base import (  # noqa: F401  (re-exports)
    _build_income_size_map,
    _compute_zone_membership,
    _configure_base,
    _execute_base,
    _income_bin_for_size,
    delegate,
    gpd,
    np,
    pd,
)
from .economic_status import (  # noqa: F401  (re-exports)
    ECONOMIC_STATUS_BY_INCOME_CLASS,
    ECONOMIC_STATUS_CATEGORIES,
    INCOME_CLASS_BY_ECONOMIC_STATUS,
    STATUS_HHTYPE_FALLBACK_WARN_RATE,
    _build_economic_status_by_income_class,
    _build_income_class_by_status,
    _derive_economic_status,
    _derive_economic_status_from_hhtype,
)
from .housing_tenure import (  # noqa: F401  (re-exports)
    HOUSING_TENURE_FALLBACK_WARN_RATE,
    _apply_housing_tenure,
    _eur_to_bracket_index,
)
from .income_distribution import (  # noqa: F401  (re-exports)
    INCOME_DISTRIBUTION_FALLBACK_WARN_RATE,
    INCOME_MIDPOINT_FALLBACK_EUR,
    INCOME_MIDPOINT_FALLBACK_WARN_RATE,
    INCOME_MIN_EUR,
    INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION,
    INCOME_OPEN_TOP_MAX_EUR,
    _apply_distribution_income,
    _apply_inkar_income_scale,
    _income_class_from_eur,
)
from .vehicle_ownership import (  # noqa: F401  (re-exports)
    CARS_INCOME_AWARE_FALLBACK_WARN_RATE,
    INSIDE_FLAG_TO_ARS5,
    KREIS_SHARE_FALLBACK_WARN_RATE,
    _assign_to_column_targets,
    _binarise_availability,
    _derive_kreis_ars5,
    _largest_remainder,
    _sample_cars_income_aware,
    _sample_counts,
    _sample_vehicle_counts,
    load_kreis_share_table,
    rake_2d,
)


# ---------------------------------------------------------------------------
# synpp cache validation
# ---------------------------------------------------------------------------

# Extracted helper submodules of this stage package. synpp's get_stage_hash
# only hashes THIS file's source (inspect.getsource of the stage module), so
# without the validate() hook below a change confined to a helper submodule
# would silently reuse the stale cached stage output on a partial rerun.
# Every submodule extracted from this package MUST be listed here.
_HELPER_MODULES = (
    availability,
    base,
    economic_status,
    housing_tenure,
    income_distribution,
    vehicle_ownership,
)


def validate(context):
    """synpp validation token: md5 over the helper submodules' sources.

    synpp compares this token against the one stored with the cached stage
    output and devalidates the cache on mismatch, so helper-only source
    changes recompute the stage just like changes to this file itself.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    _configure_base(context)
    context.stage("braunschweig.data.inkar.household_income")
    context.config("random_seed")
    context.config("data_path")


def execute(context):
    df_persons = _execute_base(context)

    data_path = context.config("data_path")

    # Derive the per-person Kreis AGS-5 once for the INKAR income scaling below.
    # NOTE: the MiD H7 / H12.3 vehicle counts (number_of_cars /
    # number_of_bicycles) are NO LONGER sampled here. They were moved into
    # _execute_base (the VEHICLE COUNTS block, before car_availability) so the A5
    # consistent-car_availability feature can condition on the household car count
    # and the licence. The +91731 RNG stream and the cars-then-bikes consumption
    # order are preserved there, so the OFF-path draws are byte-identical to the
    # legacy outer-execute sampling.
    kreis = _derive_kreis_ars5(df_persons)

    # household_income_eur.
    #
    # ON (income_eur_from_distribution, default): draw the EUR value from the real
    # MiD monthly net-income distribution P(bracket | hh_size, raumtyp) (NDS base +
    # raumtyp tilt), rank-aligned to economic_status so income is monotone in the
    # SES anchor; INKAR is applied only as a FINE within-region Kreis tilt. The
    # categorical household_income / high_income are RE-DERIVED from the drawn EUR.
    #
    # OFF: the legacy class-midpoint x INKAR-scale path (byte-identical
    # household_income_eur).
    df_inkar = context.stage("braunschweig.data.inkar.household_income")
    class_midpoint_eur = load_class_midpoint_eur(data_path)
    if context.config("income_eur_from_distribution"):
        from braunschweig.data.mid.income_by_size import (
            load_income_by_size_bundesland,
            load_income_by_size_raumtyp,
        )
        from braunschweig.data.mid.income_by_status import (
            load_income_by_status_bundesland,
            load_income_by_status_raumtyp,
        )
        df_regiostar_income = context.stage("braunschweig.data.bbsr.regiostar")
        df_income_bund = load_income_by_size_bundesland(data_path)
        df_income_raum = load_income_by_size_raumtyp(data_path)
        # Empirical income x economic-status conditional (replaces the legacy
        # rank-alignment heuristic; see _apply_distribution_income).
        df_income_status_bund = load_income_by_status_bundesland(data_path)
        df_income_status_raum = load_income_by_status_raumtyp(data_path)
        df_persons = _apply_distribution_income(
            df_persons, df_inkar, df_income_bund, df_income_raum,
            df_regiostar_income, class_midpoint_eur,
            context.config("random_seed"),
            df_status_bundesland=df_income_status_bund,
            df_status_raumtyp=df_income_status_raum,
            kreis=kreis,
        )
    else:
        df_persons = _apply_inkar_income_scale(df_persons, df_inkar,
                                               class_midpoint_eur, kreis=kreis)

    # housing_tenure COMPLETENESS attribute (synthesise_housing_tenure, default
    # ON). Sampled per household from P(tenure | income_bracket, raumtyp) using the
    # FINAL household_income_eur (resolved to a bracket) -- so it runs AFTER the
    # income block above regardless of which income path produced the EUR value.
    # OFF -> the attribute is never added, so the output schema is byte-identical.
    if context.config("synthesise_housing_tenure"):
        from braunschweig.data.mid.tenure_by_income import (
            load_tenure_by_income_bundesland,
            load_tenure_by_income_raumtyp,
        )
        df_regiostar_tenure = context.stage("braunschweig.data.bbsr.regiostar")
        df_tenure_bund = load_tenure_by_income_bundesland(data_path)
        df_tenure_raum = load_tenure_by_income_raumtyp(data_path)
        df_persons = _apply_housing_tenure(
            df_persons, df_tenure_bund, df_tenure_raum,
            df_regiostar_tenure, context.config("random_seed"),
        )

    # ``commune_id`` (kept by _execute_base only for the distribution income +
    # housing-tenure raumtyp lookups) is dropped now so the returned schema matches
    # the legacy path (the tenure draw above is the last consumer).
    if "commune_id" in df_persons.columns:
        df_persons = df_persons.drop(columns=["commune_id"])

    # BS-specific residency flag (aligns with is_munich_resident semantics).
    if "inside_braunschweig" in df_persons.columns:
        df_persons["is_bs_resident"] = df_persons["inside_braunschweig"]
        # Region-neutral alias consumed by synthesis.output and the MATSim
        # writer; written to MATSim XML under the legacy attribute key
        # "isParis" (BavariaPredictorUtils.isParisResident).
        df_persons["is_urban_resident"] = df_persons["inside_braunschweig"]

    # ------------------------------------------------------------------
    # Post-enriched control variables - hard asserts that ensure no silent
    # NaN propagation, no impossible categorical, and (when the IPF
    # hh_size margin is enabled) that the per-cell shares survive the
    # household-formation/sampling round-trip with low deviation against
    # the IPF input target.
    # ------------------------------------------------------------------
    n = len(df_persons)
    for col in [
        "household_size", "household_income", "high_income", "economic_status",
        "car_availability", "bicycle_availability", "has_pt_subscription",
        "pt_subscription_type",
        "number_of_cars", "number_of_bicycles",
    ]:
        if col not in df_persons.columns:
            raise RuntimeError(
                f"[braunschweig.enriched] expected column '{col}' missing after enrichment"
            )
        n_na = int(df_persons[col].isna().sum())
        if n_na > 0:
            raise RuntimeError(
                f"[braunschweig.enriched] column '{col}' has {n_na}/{n} NaN values"
            )

    if context.config("braunschweig.ipf.use_household_size_margin"):
        achieved = (
            df_persons["household_size"].astype(str).value_counts(normalize=True)
            .sort_index()
        )
        df_size_ref = context.stage("braunschweig.data.census.household_size").copy()
        if "household_size" in df_size_ref.columns and "weight" in df_size_ref.columns:
            target = (
                df_size_ref.groupby("household_size", observed=True)["weight"].sum()
            )
            target = (target / target.sum()).sort_index()
            common = sorted(set(achieved.index) & set(target.index))
            if common:
                deltas = {
                    k: float(achieved.get(k, 0.0)) - float(target.get(k, 0.0))
                    for k in common
                }
                max_dev = max(abs(v) for v in deltas.values())
                print(
                    "[braunschweig.enriched] hh_size deviation vs reference (pp): "
                    + ", ".join(f"{k}={v*100:+.2f}" for k, v in deltas.items())
                    + f" - max |delta|={max_dev*100:.2f}pp"
                )
                if max_dev > 0.05:
                    raise RuntimeError(
                        "[braunschweig.enriched] hh_size shares drift more than "
                        f"5pp from reference: {deltas}"
                    )

    # Sanity ranges (catch arithmetic blow-ups, e.g. INKAR scale gone wild).
    if "household_income_eur" in df_persons.columns:
        eur = df_persons["household_income_eur"]
        n_eur_na = int(eur.isna().sum())
        if n_eur_na > 0:
            raise RuntimeError(
                f"[braunschweig.enriched] household_income_eur has {n_eur_na} NaN values"
            )
        if (eur < 100).any() or (eur > 20000).any():
            raise RuntimeError(
                f"[braunschweig.enriched] household_income_eur outside plausible "
                f"range [100, 20000]: min={eur.min():.0f}, max={eur.max():.0f}"
            )

    print(
        "[braunschweig.enriched] post-enrichment OK: "
        f"n={n:,}, high_income={df_persons['high_income'].mean():.2%}, "
        f"car_avail={(df_persons['car_availability']=='all').mean():.2%}, "
        f"bike_avail={(df_persons['bicycle_availability']=='all').mean():.2%}, "
        f"pt_sub={df_persons['has_pt_subscription'].mean():.2%}."
    )

    return df_persons
