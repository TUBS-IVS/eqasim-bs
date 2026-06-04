"""Joint age x household-size IPF margin from Zensus 2022 1000A-3082.

Adds the observed age x household-size CORRELATION to the IPF. The flat
``commune x hh_size`` margin (``braunschweig.ipf.prepare._build_household_size_margin``)
balances size independently of age, so the IPF reproduces the age marginal and
the size marginal but *invents* their joint: it does not know that large
households skew toward school-age children while 1-person households skew toward
the elderly and young adults. This module supplies the missing correlation.

Strategy (feasibility-safe).  The joint is enforced at KREIS (departement)
resolution over a small set of COARSE age groups.  For each Kreis the observed
``age_group x hh_size`` table from 1000A-3082 is raked (2D IPF) to be consistent
with BOTH

  (a) the population's per-Kreis age-group marginal (already an IPF margin), and
  (b) the per-Kreis size marginal aggregated from the commune x hh_size margin
      (already an IPF margin).

Because the raked joint's two 1D marginals then equal the existing IPF margins,
adding it as a margin CANNOT make the IPF infeasible -- it only ties the two
existing marginals together with the observed correlation.  The commune-level
size detail stays with the existing 5th margin.

Coarse age groups (default boundaries 15 / 30 / 60) align with native
1000A-3082 ALTKL2 band edges, so the 1000A side aggregates without band
splitting::

    [0, 15)   children
    [15, 30)  young adults
    [30, 60)  middle-aged
    [60, inf) older

The same lower-bound containment maps the IPF's ``combined_age_class`` values
onto these groups, so the selector built in ``braunschweig.ipf.model`` lines up
with the targets produced here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Household-size bins, identical to braunschweig.ipf.{prepare,model}.HH_SIZE_BINS.
HH_SIZE_BINS: tuple[str, ...] = ("1", "2", "3", "4", "5", "6+")

# Default coarse age-group lower boundaries. The implied groups are
# [0,15) [15,30) [30,60) [60,inf). These edges are native 1000A-3082 ALTKL2
# band edges, so aggregating the Zensus joint to groups never splits a band.
DEFAULT_AGE_GROUP_BOUNDS: tuple[int, ...] = (15, 30, 60)

# Tiny seed floor so a 2D rake stays feasible even when a Zensus joint cell is
# zero (privacy-suppressed) while its row/column margins are positive.
_SEED_FLOOR = 1e-9


def age_group_lower(age: float, bounds: tuple[int, ...] = DEFAULT_AGE_GROUP_BOUNDS) -> int:
    """Lower bound of the coarse age group containing ``age``.

    With ``bounds = (15, 30, 60)`` the groups are [0,15) [15,30) [30,60)
    [60,inf): age 8 -> 0, 20 -> 15, 45 -> 30, 70 -> 60. Any non-negative age
    maps deterministically to exactly one group lower bound.
    """
    a = int(age)
    lower = 0
    for b in bounds:
        if a < b:
            break
        lower = b
    return lower


def group_lowers(bounds: tuple[int, ...] = DEFAULT_AGE_GROUP_BOUNDS) -> list[int]:
    """The ordered list of group lower bounds, i.e. ``[0, *bounds]``."""
    return [0, *bounds]


def rake_2d(seed: np.ndarray, row_targets: np.ndarray, col_targets: np.ndarray,
            forbidden: np.ndarray | None = None,
            max_iterations: int = 200, tolerance: float = 1e-9) -> np.ndarray:
    """Rake a 2D ``seed`` matrix to the given row and column marginals.

    Classic 2D iterative proportional fitting: alternately rescale rows to
    ``row_targets`` and columns to ``col_targets``. ``row_targets`` and
    ``col_targets`` must have (approximately) equal totals for the fit to
    converge to a table matching both exactly. A small floor is added to the
    seed so all-zero rows/cols cannot block a positive target.

    ``forbidden`` is an optional boolean mask of structurally-zero cells (e.g.
    children in a 1-person household, which the downstream IPF hard-zeros). Those
    cells are held at exactly zero throughout, so the joint margin stays
    consistent with the IPF's hard zeros (otherwise the full IPF cannot satisfy a
    positive joint target on a hard-zeroed cell and diverges).

    Returns the fitted matrix (same shape as ``seed``).
    """
    m = np.array(seed, dtype=float)
    m = np.maximum(m, _SEED_FLOOR)
    if forbidden is not None:
        m = np.where(forbidden, 0.0, m)
    row_targets = np.asarray(row_targets, dtype=float)
    col_targets = np.asarray(col_targets, dtype=float)
    for _ in range(max_iterations):
        row_sums = m.sum(axis=1)
        row_factor = np.divide(row_targets, row_sums,
                               out=np.zeros_like(row_targets),
                               where=row_sums > 0)
        m *= row_factor[:, None]
        col_sums = m.sum(axis=0)
        col_factor = np.divide(col_targets, col_sums,
                               out=np.zeros_like(col_targets),
                               where=col_sums > 0)
        m *= col_factor[None, :]
        new_row_sums = m.sum(axis=1)
        if np.max(np.abs(new_row_sums - row_targets)) <= tolerance * (1.0 + row_targets.sum()):
            break
    return m


def build_joint_age_size_margin(
    df_joint: pd.DataFrame,
    df_population: pd.DataFrame,
    df_size_margin: pd.DataFrame,
    bounds: tuple[int, ...] = DEFAULT_AGE_GROUP_BOUNDS,
    min_one_person_age: int = 16,
) -> pd.DataFrame:
    """Per (Kreis, age_group, hh_size) target consistent with the IPF margins.

    Parameters
    ----------
    df_joint : the Zensus 2022 1000A-3082 loader output
        (``commune_id, sex, lower_age, upper_age, hh_size, weight``; persons).
    df_population : the IPF population margin
        (``commune_id, age_class, weight``; persons). ``departement_id`` is
        derived as ``commune_id[:5]``.
    df_size_margin : the commune x hh_size margin from
        ``braunschweig.ipf.prepare`` (``commune_id, hh_size, weight``; persons,
        already rescaled per commune to the population total).
    bounds : coarse age-group lower boundaries (default 15/30/60).

    Returns
    -------
    DataFrame[departement_id, age_group_lower, hh_size, weight] -- the raked
    joint, with per-Kreis row marginals equal to the population age-group totals
    and column marginals equal to the size totals (so it is consistent with both
    existing IPF margins and therefore feasible).
    """
    g_lowers = group_lowers(bounds)
    sizes = list(HH_SIZE_BINS)

    # Structural zeros consistent with the IPF hard zero (persons younger than
    # ``min_one_person_age`` cannot live in a 1-person household): forbid the
    # (age group entirely below the threshold) x (hh_size == "1") cells. Without
    # this the raked joint puts positive mass on a cell the full IPF hard-zeros,
    # making the IPF infeasible (diverges).
    g_uppers = [*g_lowers[1:], 10_000]
    forbidden = np.zeros((len(g_lowers), len(sizes)), dtype=bool)
    for gi, upper in enumerate(g_uppers):
        if upper <= min_one_person_age:
            forbidden[gi, sizes.index("1")] = True

    joint = df_joint.copy()
    joint["departement_id"] = joint["commune_id"].astype(str).str[:5]
    joint["group_lower"] = joint["lower_age"].map(lambda a: age_group_lower(a, bounds))

    pop = df_population.copy()
    pop["departement_id"] = pop["commune_id"].astype(str).str[:5]
    pop["group_lower"] = pop["age_class"].map(lambda a: age_group_lower(a, bounds))

    size = df_size_margin.copy()
    size["departement_id"] = size["commune_id"].astype(str).str[:5]

    rows: list[dict] = []
    for dep in sorted(pop["departement_id"].unique()):
        # Row margin: population per age group (authoritative ages).
        pop_dep = pop[pop["departement_id"] == dep]
        row_targets = np.array(
            [pop_dep.loc[pop_dep["group_lower"] == g, "weight"].sum() for g in g_lowers],
            dtype=float,
        )
        # Column margin: size margin per hh_size (consistent with population
        # total because prepare rescaled it per commune to the population total).
        size_dep = size[size["departement_id"] == dep]
        col_targets = np.array(
            [size_dep.loc[size_dep["hh_size"] == s, "weight"].sum() for s in sizes],
            dtype=float,
        )
        # Reconcile the two totals (they should already match up to rounding):
        # scale the column margin to the row total so the rake fits both exactly.
        row_total = row_targets.sum()
        col_total = col_targets.sum()
        if row_total <= 0 or col_total <= 0:
            continue
        col_targets = col_targets * (row_total / col_total)

        # Seed: observed age_group x hh_size joint from 1000A-3082 (over sex
        # and the Kreis's communes).
        jd = joint[joint["departement_id"] == dep]
        seed = np.zeros((len(g_lowers), len(sizes)), dtype=float)
        if not jd.empty:
            pivot = (
                jd.groupby(["group_lower", "hh_size"], observed=True)["weight"]
                .sum()
            )
            for gi, g in enumerate(g_lowers):
                for si, s in enumerate(sizes):
                    seed[gi, si] = float(pivot.get((g, s), 0.0))

        fitted = rake_2d(seed, row_targets, col_targets, forbidden=forbidden)
        for gi, g in enumerate(g_lowers):
            for si, s in enumerate(sizes):
                rows.append({
                    "departement_id": dep,
                    "age_group_lower": g,
                    "hh_size": s,
                    "weight": float(fitted[gi, si]),
                })

    return pd.DataFrame(
        rows, columns=["departement_id", "age_group_lower", "hh_size", "weight"]
    )
