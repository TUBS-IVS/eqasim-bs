"""Gravity-model balancing loop and per-origin friction-slope resolution.

The work-flow gravity OD is computed by an iterative doubly-constrained
Furness/IPF balancing (``evaluate_gravity``). The friction matrix consumed by
that balancing can optionally vary its distance-decay slope per origin via a
per-RegioStaR-7 override, resolved by ``_build_origin_slope_vector`` before
the friction matrix is built (``braunschweig.gravity.friction``).

Extracted verbatim from ``braunschweig.gravity.model`` (issue #267 split): the
functions, their signatures, their arithmetic and their log lines -- including
the ``[braunschweig.gravity.model]`` message prefixes -- are unchanged, so the
model output and the console log are byte-identical to the pre-split stage.
The prefixes deliberately still read ``model`` because they identify the STAGE
that emits them, not the file that hosts the code.

``braunschweig.gravity.model`` re-exports every public name defined here, so
existing imports of the stage module path keep working. This module must NEVER
depend on ``braunschweig.gravity.model`` in any direction other than downward
(that would close an import cycle): the dependency runs strictly
model -> balancing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# Iteration cap for the doubly-constrained balancing in ``evaluate_gravity``.
# Deliberately high so convergence (the 1e-3 per-step delta test below) is
# always reached before the cap on realistic inputs -- the cap only guards
# against a non-converging pathological input. Kept as a module constant (and
# overridable via the ``gravity_max_iterations`` config key declared in
# ``configure``) instead of the previous magic ``int(1e6)`` literal. The default
# preserves the exact prior behaviour: the loop ran up to 1e6 times before.
DEFAULT_GRAVITY_MAX_ITERATIONS = int(1e6)


def evaluate_gravity(
    population,
    employees,
    friction,
    max_iterations=DEFAULT_GRAVITY_MAX_ITERATIONS,
    debug=False,
):
    """Iterative balancing of a doubly-constrained gravity model.

    Parameters
    ----------
    population, employees, friction
        Production targets, attraction targets and the friction matrix.
    max_iterations
        Maximum number of balancing iterations before giving up. The default
        (``DEFAULT_GRAVITY_MAX_ITERATIONS`` = 1e6) reproduces the prior
        behaviour exactly; convergence is normally reached far earlier.
    debug
        When ``True`` the per-iteration convergence deltas are printed (off by
        default; the previous unconditional per-iteration print is the only
        behaviour change and is non-numerical).

    Notes
    -----
    The convergence test is unchanged: it compares ``production`` and
    ``attraction`` against their values at the start of the iteration and
    ``flow`` against its value from the previous iteration. ``production`` and
    ``attraction`` are mutated element-by-element in place, so their pre-update
    snapshots must be copies. ``flow`` is fully reassigned each iteration
    (``flow = np.copy(friction)``) and never mutated in place after the
    reassignment, so a plain reference to the previous ``flow`` object is
    sufficient for the delta -- the explicit ``np.copy(flow)`` was redundant and
    is removed. The returned matrix and the number of iterations performed are
    therefore identical to before.
    """
    # The production/attraction targets may arrive as pandas Series (compute_work_od
    # passes df columns). Their integer __getitem__ (``population[k]`` / ``employees[k]``
    # below) positionally indexes a string-indexed Series, which pandas deprecates with
    # a FutureWarning. Coerce to plain arrays once so positional access is explicit; the
    # values -- and hence the balancing result -- are unchanged.
    population = np.asarray(population)
    employees = np.asarray(employees)

    production = np.ones((len(population),))
    attraction = np.ones((len(population),))
    flow = np.ones((len(population), len(population)))
    converged = False

    for iteration in range(int(max_iterations)):
        previous_production = np.copy(production)
        previous_attraction = np.copy(attraction)
        # ``flow`` is reassigned (not mutated in place) below, so the old object
        # this reference points to stays valid for the delta -- no copy needed.
        previous_flow = flow

        for k in range(len(population)):
            production[k] = population[k] / np.sum(attraction * friction[k, :])

        for k in range(len(population)):
            attraction[k] = employees[k] / np.sum(production * friction[:, k])

        flow = np.copy(friction)
        for i in range(len(population)):
            flow[i, :] *= production[i]
        for j in range(len(population)):
            flow[:, j] *= attraction[j]

        production_delta = np.abs(production - previous_production)
        attraction_delta = np.abs(attraction - previous_attraction)
        flow_delta = np.abs(flow - previous_flow)

        if debug:
            print(
                "Gravity iteration", iteration,
                "prod. max. delta:", np.max(production_delta),
                "attr. max. delta:", np.max(attraction_delta),
                "flow max. delta:", np.max(flow_delta),
            )

        if (np.max(production_delta) < 1e-3
                and np.max(attraction_delta) < 1e-3
                and np.max(flow_delta) < 1e-3):
            converged = True
            break

    assert converged
    return flow


# Fallback-rate threshold for the per-RegioStaR slope override (CLAUDE.md
# "Fallback transparency"). Above this share of origins falling back to the
# scalar default slope, a WARNING is emitted: a high rate means the override
# map / RegioStaR join is not actually shaping the friction matrix for most
# origins (a wrong RS7 lookup, a stale override map, or an ARS-format mismatch),
# so the per-RS7 differentiation is silently inert. Below the threshold the
# per-RS7 primary path is doing its job. 10 % matches the "~5-10%" guidance.
ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD = 0.10


def _build_origin_slope_vector(
    municipalities: list[str],
    default_slope: float,
    overrides: dict | None,
    df_regiostar: pd.DataFrame | None,
) -> np.ndarray:
    """Return one slope per origin, optionally overridden by RegioStaR-7.

    ``overrides`` maps RegioStaR-7 codes (int 71..77) to slope values
    (negative floats, e.g. ``-0.05``). Origins whose commune_id is not
    in ``df_regiostar`` or whose RegioStaR-7 code has no override fall
    back to ``default_slope``. The returned array has shape ``(N,)``
    aligned with ``municipalities`` and is broadcast against the
    distance matrix as ``slope[:, None] * distances``.

    Fallback transparency (CLAUDE.md): the PRIMARY path is a per-RegioStaR-7
    override slope; the FALLBACK is the scalar ``default_slope`` (used when an
    origin has no RS7 in ``df_regiostar`` or its RS7 code is absent from
    ``overrides``). The primary-vs-fallback counts are logged as an explicit
    rate and a ``WARNING`` is printed when the fallback share exceeds
    ``ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD``, so a silently inert override map
    is surfaced rather than passing unnoticed.
    """
    slope_vec = np.full(len(municipalities), float(default_slope))
    if not overrides or df_regiostar is None or df_regiostar.empty:
        # No override map / no RegioStaR table -> every origin is the scalar
        # fallback by construction. This is the legitimate "feature off" case
        # (the scalar slope is the intended model), so it is reported at info
        # level without a WARNING: 100 % "fallback" here is the configured
        # behaviour, not a broken primary path.
        n = len(municipalities)
        print(
            "[braunschweig.gravity.model] per-RegioStaR slope inactive "
            f"(no override map): scalar slope used for all {n}/{n} origins "
            f"(default={default_slope})."
        )
        return slope_vec

    typed_overrides = {int(k): float(v) for k, v in overrides.items()}
    rs7_lookup = (
        df_regiostar.set_index("commune_id")["regiostar7"]
        .astype("Int64")
        .to_dict()
    )

    def _normalize(cid: str) -> str:
        """Convert 12-digit ARS to 8-digit AGS if needed.

        ``braunschweig.ipf.attributed`` produces commune_id in the full
        12-character ARS format (Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3)),
        while ``braunschweig.data.bbsr.regiostar`` keys on the 8-digit
        AGS = ARS[0:5] + ARS[9:12]. Other consumers may already pass
        the 8-digit form; in that case the slice is a no-op.
        """
        s = str(cid)
        if len(s) == 12:
            return s[0:5] + s[9:12]
        return s

    matched = 0
    used_codes: dict[int, int] = {}
    # Fallback breakdown for traceability: an origin can fall back either
    # because it has no RS7 in the RegioStaR table (no_rs7) or because its RS7
    # code is not a key in the override map (rs7_not_in_overrides). Both reuse
    # the scalar default slope.
    fallback_no_rs7 = 0
    fallback_rs7_not_in_overrides = 0
    for i, commune_id in enumerate(municipalities):
        key = _normalize(commune_id)
        rs7 = rs7_lookup.get(key)
        if rs7 is None or pd.isna(rs7):
            fallback_no_rs7 += 1
            continue
        rs7 = int(rs7)
        if rs7 in typed_overrides:
            slope_vec[i] = typed_overrides[rs7]
            matched += 1
            used_codes[rs7] = used_codes.get(rs7, 0) + 1
        else:
            fallback_rs7_not_in_overrides += 1

    n = len(municipalities)
    n_fallback = fallback_no_rs7 + fallback_rs7_not_in_overrides
    primary_pct = 100.0 * matched / n if n else 0.0
    fallback_pct = 100.0 * n_fallback / n if n else 0.0
    warn_prefix = (
        "WARNING: "
        if n and (n_fallback / n) > ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD
        else ""
    )
    print(
        f"[braunschweig.gravity.model] {warn_prefix}per-RegioStaR slope: "
        f"primary (per-RS7 override) {matched}/{n} ({primary_pct:.1f}%), "
        f"fallback (scalar default={default_slope}) {n_fallback}/{n} "
        f"({fallback_pct:.1f}%) "
        f"[no RS7 in table: {fallback_no_rs7}, "
        f"RS7 not in override map: {fallback_rs7_not_in_overrides}]; "
        f"overrides per RS7 = {used_codes}"
    )
    return slope_vec
