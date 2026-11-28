"""Residents' university flows: the fit, the rounding and the allotment inside a home Kreis (issue #140, Phase 4).

Design ``docs/superpowers/specs/2026-11-25-student-commuting-design.md``, section 5a, B5 and B6 "Count and
origin", and A26. Pure functions, no synpp, no stage, no file access. The flow matrix:

- :func:`fit_flow_matrix` fits the flow matrix home Kreis x destination to the persons each home Kreis has
  PRESENT on the simulated day (rows) and to each destination's share of the region's heads in the balance table
  T (columns), starting from the heads of T (iterative proportional fitting, Deming and Stephan 1940).
- :func:`controlled_round` turns a real matrix into whole persons without bias: every cell, every row sum, every
  column sum and the grand total become the floor or the ceiling of their real values, and every cell keeps its
  value in expectation (unbiased controlled rounding, Cox 1987, "A constructive procedure for unbiased controlled
  rounding", JASA 82(398); the cycle-and-path step is the dependent rounding of Gandhi, Khuller, Parthasarathy
  and Srinivasan 2006, J. ACM 53(3)).

The location stage uses both for the residents (B5); the rounding also serves the in-commuters per origin Kreis
and site (B6). A wrong margin or a biased rounding would move students between sites without any error, so the
fit raises when it does not converge and the rounding checks its margins and raises on a violation.

Inside a home Kreis (B5 "Inside a home Kreis the persons are matched to the destinations by distance"):

- :func:`observed_education_leg_km` reads the length of each person's education leg from the plan, never imputed.
- :func:`destination_centres` gives the point a destination's distance is measured to.
- :func:`allot_within_kreis` gives every person of a Kreis one destination so that every destination receives
  exactly its whole-numbered total: by the length of the plan's education leg (rule ``plan_length``, with the
  university decay as the counted fallback) or by the distance from the home alone (rule ``home_distance``).
- :func:`draw_buildings` draws a building of the chosen destination by capacity.
- :func:`allotment_diagnostics` and :func:`random_allotment_mean_distance` are the figures the analysis stage
  reports for the comparison of the two rules.

Import boundary (Phase 4 global constraints): numpy and pandas only. The module is a hashed helper of the
location stage; keeping it free of project imports keeps that hash closure small. The transport problem of the
allotment is solved here (successive shortest paths, :func:`_min_cost_allotment`), not by a general solver.
ASSUMPTION (a dated single measurement on the development laptop, 2026-11-28, synthetic random input; other
machines differ): ``scipy.optimize.linprog`` (HiGHS, scipy 1.10.1) took about 4 s for 1,000 persons and 40 s for
2,000 persons with 19 destinations. ASSUMPTION (the size the Phase 4 plan names, not measured on a population): the
largest home Kreis, Braunschweig at 100 %, holds about 4,500 present students.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_LOG_TAG = "[university assignment]"

#: A value (cell or margin) within this distance of an integer counts as that integer. It absorbs the
#: floating-point noise of the fit and of the rounding steps and lies far below one person at every sampling rate;
#: a numerical tolerance, not a modelling parameter.
INTEGRAL_TOLERANCE = 1e-9

#: The shares of the destinations must add up to one within this tolerance; a larger gap means a destination is
#: missing from the shares and its students would be spread over the others.
_SHARE_SUM_TOLERANCE = 1e-9

_SEED_COLUMNS = ["home_ars5", "destination_id", "seed"]


# ------------------------------------------------------------------------------------------------ the fit
def fit_flow_matrix(seed: pd.DataFrame, row_totals: pd.Series, dest_shares: pd.Series, tolerance: float,
                    max_iterations: int) -> tuple[pd.DataFrame, dict]:
    """Fit the flow matrix home Kreis x destination to the persons present and the destinations' shares.

    Iterative proportional fitting of the seed (design B5): each iteration scales the columns to their targets
    and then the rows to theirs, so the rows end every iteration exact (up to floating point) and the fit stops
    once the largest column deviation is at most ``tolerance``. Ending on the rows matters: a row total of whole
    persons then stays integral for :func:`controlled_round`, which keeps it exactly.

    Args:
        seed: ``[home_ars5, destination_id, seed]``, one row per pair (``student_flows_inputs.region_seed``): the
            heads of T, finite and >= 0. A pair missing from the frame has seed 0 (a structural zero the fit
            keeps).
        row_totals: ``home_ars5 -> persons`` present on the simulated day, at the sampling rate (whole persons
            in the location stage; any finite value >= 0 is accepted). A home of the seed missing here counts as
            0 persons.
        dest_shares: ``destination_id -> share`` of the region's heads (``student_flows_inputs.destination_shares``),
            every share > 0, adding up to one.
        tolerance: largest accepted absolute deviation of a column sum from its target, in persons
            (``braunschweig.students.fit_tolerance``).
        max_iterations: largest number of iterations (one column and one row scaling each;
            ``braunschweig.students.fit_max_iterations``).

    Returns:
        ``(fitted, diagnostics)``. ``fitted`` is float, index ``home_ars5`` (the homes with persons, sorted),
        columns ``destination_id`` (sorted); its row sums are the row totals, its column sums
        ``row_totals.sum() * dest_shares`` within ``tolerance``. ``diagnostics`` holds ``iterations``,
        ``max_deviation_persons`` (the largest column deviation at the end) and ``rows_left_out`` (the sorted
        homes without persons; they are not in ``fitted``).

    Raises:
        ValueError: on invalid input (home codes of different types in the seed and the row totals included);
            when a destination has a positive target but no home with persons has a
            seed for it (its share would be lost); when a home with persons has no seed on any destination with a
            share (the caller routes such homes to the fallback before the fit, design B5); when the seed holds
            heads for a destination without a share; when the fit does not converge within ``max_iterations`` (the message
            gives the last maximum deviation; the zeros of the seed can make the margins infeasible).
    """
    if not (math.isfinite(tolerance) and tolerance > 0):
        raise ValueError(f"{_LOG_TAG} tolerance must be a finite number > 0 persons, got {tolerance!r}")
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, (int, np.integer)) or max_iterations < 1:
        raise ValueError(f"{_LOG_TAG} max_iterations must be an integer >= 1, got {max_iterations!r}")
    seed_matrix = _seed_matrix(seed)
    _check_home_code_types(seed["home_ars5"], row_totals.index)
    shares = _checked_shares(dest_shares)
    totals = _checked_row_totals(row_totals)

    homes = sorted(set(seed_matrix.index) | set(totals.index))
    totals = totals.reindex(homes, fill_value=0.0)
    rows_left_out = [home for home in homes if totals[home] == 0.0]
    populated = [home for home in homes if totals[home] > 0.0]

    without_share = sorted(str(destination) for destination in seed_matrix.columns
                           if destination not in shares.index and (seed_matrix[destination] > 0).any())
    if without_share:
        raise ValueError(f"{_LOG_TAG} the seed names destinations without a share: {without_share}; "
                         f"the seed and the shares must come from the same table T")

    columns = pd.Index(shares.index, name="destination_id")
    index = pd.Index(populated, name="home_ars5")
    if not populated:
        logger.warning("%s no home Kreis has persons present; the fitted matrix is empty (%d rows left out)",
                       _LOG_TAG, len(rows_left_out))
        empty = pd.DataFrame(np.zeros((0, len(columns))), index=index, columns=columns)
        return empty, {"iterations": 0, "max_deviation_persons": 0.0, "rows_left_out": rows_left_out}

    fitted = seed_matrix.reindex(index=populated, columns=columns, fill_value=0.0).to_numpy(dtype=float, copy=True)
    row_targets = totals.loc[populated].to_numpy(dtype=float)
    column_targets = row_targets.sum() * shares.to_numpy(dtype=float)

    unreachable = sorted(str(destination) for destination, reach in zip(columns, fitted.sum(axis=0)) if reach <= 0)
    if unreachable:
        raise ValueError(f"{_LOG_TAG} destinations with a positive share that no home Kreis with persons reaches: "
                         f"{unreachable}; their share of the students would be lost (very small samples can leave a "
                         f"destination without any populated home Kreis: raise the sampling rate)")
    stranded = sorted(str(home) for home, reach in zip(populated, fitted.sum(axis=1)) if reach <= 0)
    if stranded:
        raise ValueError(f"{_LOG_TAG} home Kreise with persons but no seed on a destination with a share: "
                         f"{stranded}; route them to the fallback before the fit")

    deviation = math.inf
    iteration = 0
    with np.errstate(divide="ignore", invalid="ignore"):
        for iteration in range(1, max_iterations + 1):
            fitted *= column_targets / fitted.sum(axis=0)
            fitted *= (row_targets / fitted.sum(axis=1))[:, np.newaxis]
            deviation = float(np.abs(fitted.sum(axis=0) - column_targets).max())
            if deviation <= tolerance:
                break
    if not deviation <= tolerance:
        raise ValueError(f"{_LOG_TAG} the fit did not converge within {max_iterations} iterations: after "
                         f"iteration {iteration} the largest column deviation is still {deviation:.6g} persons "
                         f"(tolerance {tolerance:g}); the zeros of the seed make the row and column totals "
                         f"infeasible or only just feasible")

    logger.info("%s fit converged after %d iterations: %d home Kreise x %d destinations, %.6g persons, maximum "
                "column deviation %.3g persons; %d home Kreise without persons left out: %s", _LOG_TAG, iteration,
                len(populated), len(columns), row_targets.sum(), deviation, len(rows_left_out), rows_left_out)
    diagnostics = {"iterations": iteration, "max_deviation_persons": deviation, "rows_left_out": rows_left_out}
    return pd.DataFrame(fitted, index=index, columns=columns), diagnostics


def _seed_matrix(seed: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in _SEED_COLUMNS if column not in seed.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the seed lacks the columns {missing}; expected {_SEED_COLUMNS}")
    values = pd.to_numeric(seed["seed"], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"{_LOG_TAG} the seed must be finite and >= 0")
    duplicated = seed.duplicated(["home_ars5", "destination_id"])
    if duplicated.any():
        pairs = seed.loc[duplicated, ["home_ars5", "destination_id"]].head(5).values.tolist()
        raise ValueError(f"{_LOG_TAG} the seed holds a pair more than once (first: {pairs}); sum the rows of a "
                         f"pair first (student_flows_inputs.region_seed)")
    return seed.assign(seed=values).pivot(index="home_ars5", columns="destination_id", values="seed").fillna(0.0)


def _check_home_code_types(seed_homes: pd.Series, total_homes: pd.Index) -> None:
    """Refuse home codes of different or mixed types (integer 3101 against the string '03101' of T).

    Such codes would never match: every home of the seed would count as a row without persons and every home of
    the row totals as a row without seed.
    """
    if len(seed_homes) == 0 or len(total_homes) == 0:
        return
    seed_kind = pd.api.types.infer_dtype(seed_homes, skipna=False)
    totals_kind = pd.api.types.infer_dtype(total_homes, skipna=False)
    if seed_kind != totals_kind or seed_kind.startswith("mixed"):
        raise ValueError(f"{_LOG_TAG} the home Kreis codes of the seed (dtype {seed_homes.dtype}, values "
                         f"{seed_kind}) and of the row totals (dtype {total_homes.dtype}, values {totals_kind}) "
                         f"differ in type or are mixed; pass both as the 5-digit strings of T, such as '03101'")


def _checked_shares(dest_shares: pd.Series) -> pd.Series:
    if not dest_shares.index.is_unique:
        raise ValueError(f"{_LOG_TAG} the destination shares name a destination more than once")
    shares = pd.to_numeric(dest_shares, errors="raise").astype(float).sort_index()
    if shares.empty or not np.isfinite(shares).all() or (shares <= 0).any():
        raise ValueError(f"{_LOG_TAG} every destination share must be finite and > 0 (a destination without "
                         f"students of the region is dropped before the fit), got {shares.to_dict()}")
    total = float(shares.sum())
    if abs(total - 1.0) > _SHARE_SUM_TOLERANCE:
        raise ValueError(f"{_LOG_TAG} the destination shares add up to {total!r}, not 1; a destination is missing "
                         f"or the shares are not shares")
    # Removes the last floating-point gap, so the column targets add up to the row targets.
    return shares / total


def _checked_row_totals(row_totals: pd.Series) -> pd.Series:
    if not row_totals.index.is_unique:
        raise ValueError(f"{_LOG_TAG} the row totals name a home Kreis more than once")
    totals = pd.to_numeric(row_totals, errors="raise").astype(float)
    if not np.isfinite(totals).all() or (totals < 0).any():
        raise ValueError(f"{_LOG_TAG} the persons present per home Kreis must be finite and >= 0")
    return totals


# ------------------------------------------------------------------------------------------------ the rounding
def controlled_round(matrix: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Round a real matrix of persons to whole persons, keeping every margin and every expectation.

    Guarantees (values within :data:`INTEGRAL_TOLERANCE` of an integer count as integral):

    - every cell is the floor or the ceiling of its value (an integral cell is kept);
    - every row sum, every column sum and the grand total is the floor or the ceiling of its real value, so an
      integral margin is kept exactly;
    - the expectation of every cell is its value (unbiased);
    - the same matrix (same order of rows and columns) and the same generator state give the same result.

    The guarantees hold for the real values passed in, not for targets the caller had in mind. The location stage
    passes the fitted matrix of :func:`fit_flow_matrix`: its row sums are the whole persons present (the fit ends
    on the rows), so they are kept exactly; its column sums meet their targets only within the fit tolerance, so a
    column whose target is a whole number but whose fitted sum lies just above (below) it can end at the target
    plus (minus) one. Within one person of the target always holds; exactly the target does not.

    Method (Cox 1987): the integer parts are set aside and only the fractional parts in ``[0, 1)`` are rounded.
    The table of fractional parts is completed with one slack column (each row's distance to its ceiling), one
    slack row (each column's distance to its ceiling) and a corner cell (the fractional part of the grand total),
    so every row and every column of the completed table has an integral sum. Then, as long as a fractional cell
    is left, a cycle of fractional cells is taken on the bipartite graph rows x columns (or, where floating-point
    noise has left a row or column with a single fractional cell, a maximal path between two such ends); its
    cells are shifted alternately up and down, either by ``up`` along the signs or by ``down`` against them, the
    largest amounts that keep every cell in ``[0, 1]``. The first is chosen with probability
    ``down / (up + down)``, so every cell's expected change is zero. Each step turns at least one cell integral
    and no integral cell becomes fractional again, so the loop ends after at most ``(m + 1) x (n + 1)`` steps
    for an ``m x n`` matrix (the cells of the completed table); each step walks at most ``m + n + 2`` vertices.
    The cycle is chosen deterministically (lowest-numbered vertex first); the generator draws one uniform number
    per step.

    Why the margins hold: a cycle changes no row or column sum, so every row and column of the completed table
    keeps its integral sum. (In exact arithmetic a row or column with an integral sum never has exactly one
    fractional cell, so no path occurs; a path caused by floating-point noise changes only its two ends, and the
    final check below raises if that ever moved a margin.) A row of the matrix then ends at that integral sum minus its
    rounded slack cell (0 or 1): the ceiling or the floor of its real sum, exactly its value when that is
    integral (its slack cell is 0 and never moves). Columns likewise. The grand total ends at the completed
    table's integral total minus the integral sums of the slack column and the slack row plus the rounded corner,
    which differs from the real total only by the corner's rounding: its floor or its ceiling.

    Args:
        matrix: real values >= 0 (persons), any index and columns.
        rng: the seeded generator; the stage passes its own so the result is reproducible.

    Returns:
        A frame of ``int64`` with the index and columns of ``matrix``.

    Raises:
        ValueError: on a non-numeric, non-finite or negative value.
        RuntimeError: if the result violates a guarantee (an implementation error; never expected).
    """
    values = _checked_matrix(matrix)
    nearest = np.rint(values)
    values = np.where(np.abs(values - nearest) <= INTEGRAL_TOLERANCE, nearest, values)
    floors = np.floor(values)
    fractions = values - floors
    n_rows, n_cols = fractions.shape

    completed = np.zeros((n_rows + 1, n_cols + 1))
    completed[:n_rows, :n_cols] = fractions
    completed[:n_rows, n_cols] = _distance_to_ceiling(fractions.sum(axis=1))
    completed[n_rows, :n_cols] = _distance_to_ceiling(fractions.sum(axis=0))
    completed[n_rows, n_cols] = _fractional_part(float(fractions.sum()))
    rounded_fractions, n_steps = _dependent_rounding(completed, rng)

    rounded = floors.astype(np.int64) + rounded_fractions[:n_rows, :n_cols]
    _check_guarantees(rounded, values)
    logger.debug("%s controlled rounding of a %d x %d matrix in %d steps", _LOG_TAG, n_rows, n_cols, n_steps)
    return pd.DataFrame(rounded, index=matrix.index, columns=matrix.columns)


def _checked_matrix(matrix: pd.DataFrame) -> np.ndarray:
    try:
        values = matrix.to_numpy(dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{_LOG_TAG} the matrix to round must be numeric: {error}") from error
    if not np.isfinite(values).all():
        raise ValueError(f"{_LOG_TAG} the matrix to round holds a missing or infinite value")
    if (values < 0).any():
        raise ValueError(f"{_LOG_TAG} the matrix to round holds a negative value (persons cannot be negative)")
    return values


def _is_integral(values: np.ndarray) -> np.ndarray:
    return np.abs(values - np.rint(values)) <= INTEGRAL_TOLERANCE


def _distance_to_ceiling(sums: np.ndarray) -> np.ndarray:
    """``ceil(s) - s`` per sum, 0 for a sum that counts as integral (its slack cell then never moves)."""
    return np.where(_is_integral(sums), 0.0, np.ceil(sums) - sums)


def _fractional_part(total: float) -> float:
    """``total - floor(total)``, 0 for a total that counts as integral."""
    if abs(total - round(total)) <= INTEGRAL_TOLERANCE:
        return 0.0
    return total - math.floor(total)


def _dependent_rounding(completed: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, int]:
    """Round every cell of ``completed`` (values in ``[0, 1)``) to 0 or 1 by cycles and maximal paths.

    The graph: vertex ``i`` is row ``i``, vertex ``n_rows + j`` is column ``j``; a fractional cell is an edge.
    Returns the 0/1 matrix (``int64``) and the number of steps.
    """
    n_rows, n_cols = completed.shape
    cells = completed.tolist()
    neighbours: list[set[int]] = [set() for _ in range(n_rows + n_cols)]
    for row in range(n_rows):
        for col in range(n_cols):
            if cells[row][col] > 0.0:
                neighbours[row].add(n_rows + col)
                neighbours[n_rows + col].add(row)
    n_fractional = sum(len(adjacent) for adjacent in neighbours[:n_rows])

    n_steps = 0
    while True:
        start = _walk_start(neighbours)
        if start is None:
            break
        n_steps += 1
        if n_steps > n_fractional:
            raise RuntimeError(f"{_LOG_TAG} controlled rounding did not end within {n_fractional} steps")
        walk = _cycle_or_maximal_path(neighbours, start)
        edges = [(min(a, b), max(a, b) - n_rows) for a, b in zip(walk, walk[1:])]
        signs = [1.0 if position % 2 == 0 else -1.0 for position in range(len(edges))]
        current = [cells[row][col] for row, col in edges]
        up = min(1.0 - x if sign > 0 else x for x, sign in zip(current, signs))
        down = min(x if sign > 0 else 1.0 - x for x, sign in zip(current, signs))
        shift = up if rng.random() < down / (up + down) else -down
        n_integral = 0
        for (row, col), x, sign in zip(edges, current, signs):
            moved = x + sign * shift
            if moved <= INTEGRAL_TOLERANCE or moved >= 1.0 - INTEGRAL_TOLERANCE:
                moved = 0.0 if moved < 0.5 else 1.0
                neighbours[row].discard(n_rows + col)
                neighbours[n_rows + col].discard(row)
                n_integral += 1
            cells[row][col] = moved
        if n_integral == 0:
            raise RuntimeError(f"{_LOG_TAG} a controlled-rounding step made no cell integral")

    rounded = np.asarray(cells, dtype=float)
    if not ((rounded == 0.0) | (rounded == 1.0)).all():
        raise RuntimeError(f"{_LOG_TAG} controlled rounding left a fractional cell")
    return rounded.astype(np.int64), n_steps


def _walk_start(neighbours: list[set[int]]) -> int | None:
    """The lowest vertex with exactly one fractional cell (a path end), else the lowest with any, else None."""
    first_with_edges = None
    for vertex, adjacent in enumerate(neighbours):
        if len(adjacent) == 1:
            return vertex
        if adjacent and first_with_edges is None:
            first_with_edges = vertex
    return first_with_edges


def _cycle_or_maximal_path(neighbours: list[set[int]], start: int) -> list[int]:
    """Walk from ``start`` along fractional cells, never straight back, always to the lowest next vertex.

    Returns the closed cycle (first vertex repeated at the end) once a vertex comes round again, or the path up
    to a vertex with no other fractional cell. A dead end can only be reached from a start with a single cell
    (every other vertex entered has another cell to leave by), so a returned path is maximal at both ends.
    """
    path = [start]
    position = {start: 0}
    previous = -1
    current = start
    while True:
        candidates = neighbours[current] - {previous}
        if not candidates:
            return path
        following = min(candidates)
        if following in position:
            return path[position[following]:] + [following]
        position[following] = len(path)
        path.append(following)
        previous, current = current, following


def _check_guarantees(rounded: np.ndarray, values: np.ndarray) -> None:
    """Raise if a cell, row sum, column sum or the grand total is not the floor or the ceiling of its value."""
    checks = [("cell", rounded, values),
              ("row sum", rounded.sum(axis=1), values.sum(axis=1)),
              ("column sum", rounded.sum(axis=0), values.sum(axis=0)),
              ("grand total", np.array([rounded.sum()]), np.array([values.sum()]))]
    for name, result, real in checks:
        outside = (result < np.floor(real + INTEGRAL_TOLERANCE)) | (result > np.ceil(real - INTEGRAL_TOLERANCE))
        if outside.any():
            raise RuntimeError(f"{_LOG_TAG} controlled rounding broke a guarantee: a {name} of "
                               f"{result[outside][0]} for the real value {real[outside][0]!r}")


# ------------------------------------------------------------------------------------------------ inside a Kreis
#: The two rules of :func:`allot_within_kreis` (the stage's ``braunschweig.students.kreis_matching_rule``).
RULE_PLAN_LENGTH = "plan_length"
RULE_HOME_DISTANCE = "home_distance"
ALLOTMENT_RULES = (RULE_PLAN_LENGTH, RULE_HOME_DISTANCE)

#: The ``path`` of an allotted person: matched by the length of the plan's education leg (primary of the plan rule),
#: placed by the university decay because the plan has no observed length (fallback of the plan rule), or matched
#: by the distance from the home (the home-distance rule, which has no fallback).
PATH_PLAN_LENGTH = "plan_length"
PATH_FALLBACK_DECAY = "fallback_decay"
PATH_HOME_DISTANCE = "home_distance"

#: The projected CRS of the facilities and the homes (Phase 4 global constraints): distances are straight lines in
#: metres in it.
PROJECTED_EPSG = 25832

_TRIP_COLUMNS = ["person_id", "preceding_purpose", "following_purpose", "euclidean_distance"]
_PERSON_COLUMNS = ["person_id", "x", "y", "leg_km"]
_CENTRE_COLUMNS = ["destination_id", "x", "y"]
_ASSIGNED_COLUMNS = ["person_id", "destination_id", "path"]
_SCOPE_LOCAL = "local"
_SCOPE_SURROUNDING = "surrounding"

#: Marks a move between two destinations that does not exist (no person to move away). Far above every cost sum the
#: solver forms (whole metres, a few million per person at most), far below the int64 limit, so adding a cost to it
#: can neither overflow nor make it look finite.
_NO_MOVE = np.iinfo(np.int64).max // 4


# ------------------------------------------------------------------------------------------------ the observed leg
def observed_education_leg_km(trips: pd.DataFrame) -> pd.Series:
    """The length of each person's education leg as the plan holds it, in km, never imputed (design B5).

    The leg is selected as in ``braunschweig.popsim.commute_distance._per_purpose`` for the purpose ``education``:
    the FIRST trip of the person, in the row order of ``trips``, from home to education or from education to home (a
    trip from or to another activity does not count). Unlike that stage, a selected leg whose length is missing keeps
    the missing value ("Only an observed length counts"): the stage fills it with a length drawn from all persons'
    education legs, pupils included, which would make the person look like a primary case of the plan rule.

    Args:
        trips: ``[person_id, preceding_purpose, following_purpose, euclidean_distance]``, the trips of the plans
            (``synthesis.population.trips``); ``euclidean_distance`` is the straight-line length in metres.

    Returns:
        ``person_id -> leg_km`` (float, name ``leg_km``): one entry per person with a home <-> education leg, NaN
        where that leg's length is missing. A person without such a leg is absent.

    Raises:
        ValueError: on a missing column or a negative or infinite length.
    """
    _require_columns(trips, _TRIP_COLUMNS, "trips")
    is_leg = (((trips["preceding_purpose"] == "home") & (trips["following_purpose"] == "education"))
              | ((trips["preceding_purpose"] == "education") & (trips["following_purpose"] == "home")))
    legs = trips.loc[is_leg, ["person_id", "euclidean_distance"]].drop_duplicates("person_id", keep="first")
    length_m = pd.to_numeric(legs["euclidean_distance"], errors="raise").to_numpy(dtype=float)
    if np.isinf(length_m).any() or (length_m < 0).any():
        raise ValueError(f"{_LOG_TAG} an education leg has a negative or infinite euclidean_distance")
    observed = pd.Series(length_m / 1000.0, index=pd.Index(legs["person_id"].to_numpy(), name="person_id"),
                         name="leg_km")
    n_missing = int(observed.isna().sum())
    logger.info("%s observed education legs: %d persons with a home<->education leg, %d of them without a length "
                "(they take the fallback of the plan rule); %d of %d persons with trips have no such leg",
                _LOG_TAG, len(observed), n_missing, int(trips["person_id"].nunique()) - len(observed),
                int(trips["person_id"].nunique()))
    return observed


# ------------------------------------------------------------------------------------------------ destination centres
def destination_centres(facilities: pd.DataFrame) -> pd.DataFrame:
    """The point each destination's distance is measured to (design B5).

    A local site: the capacity-weighted centre of its buildings, so the matched distance is that to the site as a
    whole; the building is drawn afterwards (:func:`draw_buildings`) and the realised distance differs by the extent
    of the site. A surrounding institution: its single point.

    Args:
        facilities: the university facilities of the ON path (``university_facilities``): a GeoDataFrame
            ``[location_id, capacity, geometry, destination_id, scope, site_ags8]`` in EPSG:25832 with point
            geometries, capacities >= 0, one row per building of a local site and one row per surrounding
            institution.

    Returns:
        ``[destination_id, x, y, scope]`` (metres in EPSG:25832), one row per destination, sorted by
        ``destination_id``. ``scope`` is kept for :func:`allotment_diagnostics`.

    Raises:
        ValueError: on another CRS, a missing column, a missing destination, a non-finite coordinate or capacity, a
            destination with two scopes or without capacity, or a surrounding institution with more than one row.
    """
    _require_columns(facilities, ["destination_id", "scope", "capacity", "geometry"], "facilities")
    _check_projected(facilities, "facilities")
    if facilities["destination_id"].isna().any():
        raise ValueError(f"{_LOG_TAG} a facility has no destination_id")
    capacity = pd.to_numeric(facilities["capacity"], errors="raise").to_numpy(dtype=float)
    x = facilities.geometry.x.to_numpy(dtype=float)
    y = facilities.geometry.y.to_numpy(dtype=float)
    if not (np.isfinite(capacity).all() and np.isfinite(x).all() and np.isfinite(y).all()) or (capacity < 0).any():
        raise ValueError(f"{_LOG_TAG} the facilities need finite point coordinates and finite capacities >= 0")
    unknown_scopes = sorted(set(facilities["scope"]) - {_SCOPE_LOCAL, _SCOPE_SURROUNDING})
    if unknown_scopes:
        raise ValueError(f"{_LOG_TAG} facilities with an unknown scope {unknown_scopes}; expected "
                         f"{[_SCOPE_LOCAL, _SCOPE_SURROUNDING]}")
    frame = pd.DataFrame({"destination_id": facilities["destination_id"].to_numpy(),
                          "scope": facilities["scope"].to_numpy(), "capacity": capacity, "x": x, "y": y})
    rows = []
    for destination, group in frame.groupby("destination_id", sort=True):
        scopes = group["scope"].unique()
        weight = float(group["capacity"].sum())
        if len(scopes) != 1:
            raise ValueError(f"{_LOG_TAG} destination {destination!r} has facilities of two scopes {list(scopes)}")
        if not weight > 0:
            raise ValueError(f"{_LOG_TAG} destination {destination!r} has no capacity; its centre is undefined")
        if scopes[0] == _SCOPE_SURROUNDING and len(group) != 1:
            raise ValueError(f"{_LOG_TAG} the surrounding institution {destination!r} has {len(group)} facility "
                             f"rows; it is one point")
        rows.append((destination, float((group["x"] * group["capacity"]).sum() / weight),
                     float((group["y"] * group["capacity"]).sum() / weight), scopes[0]))
    return pd.DataFrame(rows, columns=["destination_id", "x", "y", "scope"])


# ------------------------------------------------------------------------------------------------ the allotment
def allot_within_kreis(persons: pd.DataFrame, cell_totals: pd.Series, centres: pd.DataFrame, rule: str,
                       decay_slope: float, rng: np.random.Generator, *, label: str | None = None) -> pd.DataFrame:
    """Give every person of one home Kreis a destination so that each destination receives exactly its total.

    The flow matrix fixes how many persons of the Kreis go to each destination (the row of the rounded matrix); this
    function decides only who (design B5, A26). Distances are straight lines in metres from the home to the
    destination's centre (:func:`destination_centres`); a cell of 0 receives nobody.

    The default rule is ``home_distance`` (owner decision 2026-11-28, ADR-0213 Decision 3); ``plan_length`` is
    selectable (``braunschweig.students.kreis_matching_rule``).

    Rule ``plan_length`` (the plan decides):

    1. Places for the fallback persons (``leg_km`` missing) are held back from the cells in proportion to the cell
       totals by largest remainder (Hamilton), in exact integer arithmetic. Equal remainders are ordered by a seeded
       permutation of the cells, so no destination is favoured by its name.
    2. The fallback persons take the held-back places one after the other, in a seeded order. Each draws a cell with
       a held-back place left with probability proportional to ``places left x exp(decay_slope x d_km)``: today's
       university decay (``assign_by_decay``, capacity times ``exp(slope x d)``) with the held-back places of the
       cell as its capacity, i.e. each remaining place is drawn with the decay weight of its distance.
    3. The persons with an observed leg (a leg of 0.0 km is observed) are matched to the remaining places so that
       the sum of ``|d(home, centre) - leg|`` is the smallest possible: an integral transport problem with costs in
       whole metres (rounded), solved exactly by :func:`_min_cost_allotment`.

    Rule ``home_distance``: every person, with or without a leg, is matched by the smallest sum of
    ``d(home, centre)``; no decay and no fallback.

    Ties: among allotments of equal total cost, the one chosen depends only on the seeded order of the persons (and
    of the cells for equal remainders in step 1), never on the row order of the input: the persons are sorted by
    ``person_id`` before the generator permutes them, and the cells by ``destination_id``.

    Args:
        persons: ``[person_id, x, y, leg_km]``: the persons of one home Kreis (unique ids of one comparable type),
            home coordinates in metres (EPSG:25832), ``leg_km`` the observed leg (:func:`observed_education_leg_km`)
            or NaN when the plan has none.
        cell_totals: ``destination_id -> persons``, whole numbers >= 0 adding up to the number of persons (the row
            of the Kreis in the rounded flow matrix).
        centres: ``[destination_id, x, y]`` (:func:`destination_centres`); every cell with persons needs a centre.
        rule: ``"plan_length"`` or ``"home_distance"``.
        decay_slope: the university decay per km (``education_university_slope``), finite and <= 0; used by the
            fallback of the plan rule only.
        rng: the seeded generator of the stage.
        label: names the call (the home Kreis) in the log line.

    Returns:
        ``[person_id, destination_id, path]`` in the row order of ``persons`` (fresh index); ``path`` is
        ``plan_length`` or ``fallback_decay`` under the plan rule, ``home_distance`` under the other.

    Raises:
        ValueError: on an unknown rule, a slope that is not finite or > 0, invalid persons (missing column, repeated
            or missing id, non-finite coordinate, negative or infinite leg), cell totals that are not whole numbers
            >= 0 or do not add up to the number of persons, or a cell with persons but no centre.
        RuntimeError: if a cell does not end at exactly its total or the matching is not minimal (an implementation
            error; never expected).
    """
    if rule not in ALLOTMENT_RULES:
        raise ValueError(f"{_LOG_TAG} unknown allotment rule {rule!r}; expected one of {list(ALLOTMENT_RULES)} "
                         f"(braunschweig.students.kreis_matching_rule)")
    if not (math.isfinite(decay_slope) and decay_slope <= 0):
        raise ValueError(f"{_LOG_TAG} the decay slope must be finite and <= 0 per km (the chance falls with the "
                         f"distance), got {decay_slope!r}")
    frame = _checked_persons(persons)
    totals = _checked_cell_totals(cell_totals)
    n_persons = len(frame)
    if n_persons != int(totals.sum()):
        raise ValueError(f"{_LOG_TAG} {n_persons} persons but the cell totals add up to {int(totals.sum())} persons; "
                         f"every person needs a place and every place a person")
    cells = totals[totals > 0]
    capacities = cells.to_numpy(dtype=np.int64)
    distance_m = _distance_matrix_m(frame, _centre_xy(centres, cells.index))

    column = np.full(n_persons, -1, dtype=np.int64)
    path = np.empty(n_persons, dtype=object)
    if rule == RULE_HOME_DISTANCE:
        column[:] = _min_cost_allotment(np.rint(distance_m).astype(np.int64), capacities, rng)
        path[:] = PATH_HOME_DISTANCE
        n_fallback = 0
    else:
        leg_m = frame["leg_km"].to_numpy(dtype=float) * 1000.0
        fallback = np.isnan(leg_m)
        n_fallback = int(fallback.sum())
        held = _held_back_places(capacities, n_fallback, rng)
        column[fallback] = _place_by_decay(distance_m[fallback] / 1000.0, held, decay_slope, rng)
        primary = ~fallback
        cost = np.rint(np.abs(distance_m[primary] - leg_m[primary, np.newaxis])).astype(np.int64)
        column[primary] = _min_cost_allotment(cost, capacities - held, rng)
        path[fallback] = PATH_FALLBACK_DECAY
        path[primary] = PATH_PLAN_LENGTH

    reached = np.bincount(column[column >= 0], minlength=len(capacities))
    if (column < 0).any() or not np.array_equal(reached, capacities):
        raise RuntimeError(f"{_LOG_TAG} the allotment missed a cell total: {reached.tolist()} for "
                           f"{capacities.tolist()}")
    name = f" {label}" if label else ""
    if rule == RULE_PLAN_LENGTH:
        n_primary = n_persons - n_fallback
        logger.info("%s allotment%s by the plan's leg: primary %d/%d (%.1f%%), fallback %d (%.1f%%) placed by the "
                    "university decay; %d destinations with persons", _LOG_TAG, name, n_primary, n_persons,
                    _percent(n_primary, n_persons), n_fallback, _percent(n_fallback, n_persons), len(cells))
    else:
        logger.info("%s allotment%s by home distance: %d persons, %d destinations with persons", _LOG_TAG, name,
                    n_persons, len(cells))
    result = pd.DataFrame({"person_id": frame["person_id"].to_numpy(),
                           "destination_id": cells.index.to_numpy()[column], "path": path})
    return result.iloc[np.argsort(frame["row"].to_numpy(), kind="stable")].reset_index(drop=True)


def _percent(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else 0.0


def _require_columns(frame: pd.DataFrame, columns: list[str], what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} the {what} lack the columns {missing}; expected {columns}")


def _check_projected(frame: pd.DataFrame, what: str) -> None:
    crs = getattr(frame, "crs", None)
    if crs is None or crs.to_epsg() != PROJECTED_EPSG:
        raise ValueError(f"{_LOG_TAG} the {what} must be in EPSG:{PROJECTED_EPSG} (metres), got {crs}")


def _checked_persons(persons: pd.DataFrame) -> pd.DataFrame:
    """The persons as ``[person_id, x, y, leg_km, row]`` sorted by ``person_id``; ``row`` is the input position."""
    _require_columns(persons, _PERSON_COLUMNS, "persons")
    ids = persons["person_id"]
    if ids.isna().any():
        raise ValueError(f"{_LOG_TAG} a person has no person_id")
    if ids.duplicated().any():
        raise ValueError(f"{_LOG_TAG} the persons name a person more than once (first: "
                         f"{ids[ids.duplicated()].iloc[0]!r})")
    x = pd.to_numeric(persons["x"], errors="raise").to_numpy(dtype=float)
    y = pd.to_numeric(persons["y"], errors="raise").to_numpy(dtype=float)
    leg = pd.to_numeric(persons["leg_km"], errors="raise").to_numpy(dtype=float)
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError(f"{_LOG_TAG} a person's home has a missing or infinite coordinate")
    observed = ~np.isnan(leg)
    if np.isinf(leg).any() or (leg[observed] < 0).any():
        raise ValueError(f"{_LOG_TAG} a leg_km must be missing (no observed leg) or a finite length >= 0")
    frame = pd.DataFrame({"person_id": ids.to_numpy(), "x": x, "y": y, "leg_km": leg, "row": np.arange(len(ids))})
    try:
        return frame.sort_values("person_id", kind="mergesort").reset_index(drop=True)
    except TypeError as error:
        raise ValueError(f"{_LOG_TAG} the person ids must be of one comparable type: {error}") from error


def _checked_cell_totals(cell_totals: pd.Series) -> pd.Series:
    """The totals as ``int64`` sorted by destination."""
    if not cell_totals.index.is_unique or cell_totals.index.hasnans:
        raise ValueError(f"{_LOG_TAG} the cell totals name a destination more than once or not at all")
    values = pd.to_numeric(cell_totals, errors="raise").to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or not _is_integral(values).all():
        raise ValueError(f"{_LOG_TAG} the cell totals must be whole numbers of persons >= 0, got "
                         f"{cell_totals.to_dict()}")
    totals = pd.Series(np.rint(values).astype(np.int64), index=cell_totals.index)
    try:
        return totals.sort_index()
    except TypeError as error:
        raise ValueError(f"{_LOG_TAG} the destination ids must be of one comparable type: {error}") from error


def _centre_xy(centres: pd.DataFrame, destinations: pd.Index) -> np.ndarray:
    """The ``(m, 2)`` centre coordinates of ``destinations`` in their order."""
    _require_columns(centres, _CENTRE_COLUMNS, "centres")
    if centres["destination_id"].duplicated().any():
        raise ValueError(f"{_LOG_TAG} the centres name a destination more than once")
    lookup = centres.set_index("destination_id")
    absent = [str(destination) for destination in destinations if destination not in lookup.index]
    if absent:
        raise ValueError(f"{_LOG_TAG} cells with persons but no centre: {absent}")
    xy = lookup.loc[list(destinations), ["x", "y"]].to_numpy(dtype=float).reshape(len(destinations), 2)
    if not np.isfinite(xy).all():
        raise ValueError(f"{_LOG_TAG} a centre has a missing or infinite coordinate")
    return xy


def _distance_matrix_m(frame: pd.DataFrame, centre_xy: np.ndarray) -> np.ndarray:
    """Straight-line distances in metres, persons x destinations."""
    home = frame[["x", "y"]].to_numpy(dtype=float)
    return np.hypot(home[:, 0, np.newaxis] - centre_xy[np.newaxis, :, 0],
                    home[:, 1, np.newaxis] - centre_xy[np.newaxis, :, 1])


def _held_back_places(capacities: np.ndarray, n_fallback: int, rng: np.random.Generator) -> np.ndarray:
    """Largest-remainder share of ``n_fallback`` places over the cells, in proportion to their totals.

    Exact integer arithmetic: the quota of cell ``j`` is ``n_fallback x total_j / N`` with ``N`` the sum of the
    totals. Each cell gets the floor of its quota; the places left go one each to the cells with the largest
    remainders (equal remainders in a seeded order of the cells). A cell never gets more than its total: a quota
    is at most the total, and a cell whose quota is whole has remainder 0 and is passed over, because more cells
    have a positive remainder than places are left.
    """
    held = np.zeros(len(capacities), dtype=np.int64)
    if n_fallback == 0:
        return held
    n_places = int(capacities.sum())
    numerators = capacities.astype(np.int64) * n_fallback
    held = numerators // n_places
    remainders = numerators % n_places
    n_left = n_fallback - int(held.sum())
    tie_order = rng.permutation(len(capacities))
    by_remainder = np.lexsort((tie_order, -remainders))
    held[by_remainder[:n_left]] += 1
    if (held > capacities).any() or int(held.sum()) != n_fallback:
        raise RuntimeError(f"{_LOG_TAG} the held-back places {held.tolist()} do not fit {capacities.tolist()}")
    return held


def _place_by_decay(distance_km: np.ndarray, held: np.ndarray, decay_slope: float,
                    rng: np.random.Generator) -> np.ndarray:
    """Place each fallback person on a held-back place, one after the other in a seeded order (step 2).

    A person draws cell ``j`` with probability proportional to ``left_j x exp(decay_slope x d_j)`` over the cells
    with a held-back place left (computed in logarithms, so a far cell cannot underflow every weight to zero).
    """
    n_fallback = len(distance_km)
    column = np.empty(n_fallback, dtype=np.int64)
    if n_fallback == 0:
        return column
    left = held.astype(np.int64).copy()
    order = rng.permutation(n_fallback)
    uniforms = rng.random(n_fallback)
    for position, person in enumerate(order):
        open_cells = np.flatnonzero(left > 0)
        log_weight = np.log(left[open_cells]) + decay_slope * distance_km[person, open_cells]
        cumulative = np.cumsum(np.exp(log_weight - log_weight.max()))
        pick = int(np.searchsorted(cumulative, uniforms[position] * cumulative[-1], side="right"))
        cell = open_cells[min(pick, len(open_cells) - 1)]
        column[person] = cell
        left[cell] -= 1
    return column


# ------------------------------------------------------------------------------------------------ the transport problem
def _min_cost_allotment(cost: np.ndarray, capacities: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """An exact minimum-cost allotment of persons to cells with fixed whole capacities.

    The transport problem (unit supply per person, ``capacities.sum() == n`` places) is solved by successive
    shortest paths (Ahuja, Magnanti and Orlin 1993, "Network Flows", ch. 9), exploiting that the cells (the
    destinations of T) are few while the persons are many: the persons enter one at a time in a seeded order, and
    each enters along a cheapest chain "the new person takes a place in cell j1, a person of j1 moves to j2, ..., a
    person moves to a cell with a free place". A move ``j -> l`` costs the smallest ``cost[p, l] - cost[p, j]``
    over the persons ``p`` in ``j``, kept per pair of cells and refreshed for the cells a chain touched; the
    cheapest chain is found by Bellman-Ford on the cells. Why this is exact: if the persons entered so far are allotted at minimum cost (no
    cycle of moves has a negative cost), adding a person along a cheapest chain gives a minimum-cost allotment of
    one person more (the difference to any other allotment splits into the new person's chain, at least as
    expensive, and cycles, none negative). At the end every cell is full and the absence of a negative cycle,
    which is exactly the optimality condition, is checked again on a move table rebuilt from the final allotment
    (:func:`_certify_minimum`).

    Ties: the persons enter in the seeded order; among chains of equal cost the cell with the lower position wins,
    and among persons of a cell with an equal move cost the one that entered first moves. The seeded order thus
    decides every tie.

    Args:
        cost: ``(n, m)`` whole costs (int64), here whole metres.
        capacities: ``(m,)`` whole capacities adding up to ``n``.
        rng: draws the order in which the persons enter.

    Returns:
        ``(n,)`` the cell of each person (positions into ``capacities``).
    """
    n_persons, n_cells = cost.shape
    if n_persons != int(capacities.sum()):
        raise RuntimeError(f"{_LOG_TAG} {n_persons} persons for {int(capacities.sum())} places")
    if n_persons == 0:
        return np.empty(0, dtype=np.int64)
    order = rng.permutation(n_persons)
    ranked = np.ascontiguousarray(cost[order])
    cell_of = np.full(n_persons, -1, dtype=np.int64)
    count = np.zeros(n_cells, dtype=np.int64)
    move_cost = np.full((n_cells, n_cells), _NO_MOVE, dtype=np.int64)
    mover = np.full((n_cells, n_cells), -1, dtype=np.int64)
    for entrant in range(n_persons):
        reach, previous = _cheapest_chains(ranked[entrant], move_cost)
        target = int(np.argmin(np.where(count < capacities, reach, _NO_MOVE)))
        chain = [target]
        while previous[chain[-1]] >= 0:
            chain.append(int(previous[chain[-1]]))
            if len(chain) > n_cells:
                raise RuntimeError(f"{_LOG_TAG} the chain of moves runs in a circle")
        chain.reverse()
        moving = [int(mover[source, sink]) for source, sink in zip(chain, chain[1:])]
        cell_of[entrant] = chain[0]
        for sink, person in zip(chain[1:], moving):
            cell_of[person] = sink
        count[target] += 1
        for cell in chain:
            _refresh_moves(ranked, cell_of, cell, move_cost, mover)
    if not np.array_equal(count, capacities):
        raise RuntimeError(f"{_LOG_TAG} the transport problem ended at {count.tolist()} for {capacities.tolist()}")
    _certify_minimum(ranked, cell_of)
    result = np.empty(n_persons, dtype=np.int64)
    result[order] = cell_of
    return result


def _cheapest_chains(entry_cost: np.ndarray, move_cost: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bellman-Ford over the cells: the cheapest cost of placing the entrant so that cell ``l`` gains a person.

    ``reach[l]`` is that cost, ``previous[l]`` the cell the last move came from (-1: the entrant itself takes the
    place in ``l``). Each round relaxes every move at once; without a negative cycle a cheapest chain has at most
    ``m - 1`` moves, so the rounds end with a round without change by round ``m``.
    """
    n_cells = len(entry_cost)
    reach = entry_cost.astype(np.int64)
    previous = np.full(n_cells, -1, dtype=np.int64)
    columns = np.arange(n_cells)
    for _ in range(n_cells):
        via = reach[:, np.newaxis] + move_cost
        source = via.argmin(axis=0)
        best = via[source, columns]
        better = best < reach
        if not better.any():
            return reach, previous
        reach = np.where(better, best, reach)
        previous = np.where(better, source, previous)
    raise RuntimeError(f"{_LOG_TAG} a cycle of moves has a negative cost; the allotment so far is not minimal")


def _refresh_moves(ranked: np.ndarray, cell_of: np.ndarray, cell: int, move_cost: np.ndarray,
                   mover: np.ndarray) -> None:
    """Recompute the cheapest move out of ``cell`` to every other cell and the person who makes it."""
    members = np.flatnonzero(cell_of == cell)
    if len(members) == 0:
        move_cost[cell] = _NO_MOVE
        mover[cell] = -1
        return
    delta = ranked[members] - ranked[members, cell][:, np.newaxis]
    best = delta.argmin(axis=0)
    move_cost[cell] = delta[best, np.arange(ranked.shape[1])]
    mover[cell] = members[best]
    move_cost[cell, cell] = _NO_MOVE
    mover[cell, cell] = -1


def _certify_minimum(cost: np.ndarray, cell_of: np.ndarray) -> None:
    """Raise unless the full allotment ``cell_of`` has the minimum total ``cost``.

    With every cell full, an allotment is minimal exactly when no cycle of moves (a person of ``j1`` to ``j2``, a
    person of ``j2`` to ``j3``, ..., back to ``j1``) has a negative cost: the optimality condition of a minimum-cost
    flow. The table of cheapest moves is rebuilt here from ``cost`` and the final ``cell_of`` alone, in O(n x m), and
    not taken from the solver's incrementally kept table, so a fault in that bookkeeping cannot pass the check. The
    cycles are then searched by Bellman-Ford over the cells.

    Args:
        cost: ``(n, m)`` whole costs.
        cell_of: ``(n,)`` the cell of each person, every person placed.
    """
    n_persons, n_cells = cost.shape
    if len(cell_of) != n_persons or (cell_of < 0).any() or (cell_of >= n_cells).any():
        raise RuntimeError(f"{_LOG_TAG} the allotment to check leaves a person without a cell")
    delta = cost - cost[np.arange(n_persons), cell_of][:, np.newaxis]
    move_cost = np.full((n_cells, n_cells), _NO_MOVE, dtype=np.int64)
    for cell in range(n_cells):
        leaving = delta[cell_of == cell]
        if len(leaving):
            move_cost[cell] = leaving.min(axis=0)
    np.fill_diagonal(move_cost, _NO_MOVE)
    potential = np.zeros(n_cells, dtype=np.int64)
    for _ in range(n_cells + 1):
        relaxed = np.minimum(potential, (potential[:, np.newaxis] + move_cost).min(axis=0))
        if np.array_equal(relaxed, potential):
            return
        potential = relaxed
    raise RuntimeError(f"{_LOG_TAG} the allotment is not minimal: a cycle of moves has a negative cost")


# ------------------------------------------------------------------------------------------------ buildings
def draw_buildings(destination_ids: pd.Series, facilities: pd.DataFrame, rng: np.random.Generator) -> pd.Series:
    """Draw a building of each person's destination with probability proportional to its capacity.

    The draw does not depend on the row order of ``destination_ids``, as the allotment does not: the persons are
    drawn in the order of the sorted index (the stage passes the person ids as the index), destination by
    destination in sorted order, and each building of a destination in the row order of ``facilities``.

    Args:
        destination_ids: the destination of each person (the ``destination_id`` of :func:`allot_within_kreis`),
            with a unique index of one comparable type that identifies the person.
        facilities: ``[location_id, capacity, destination_id]`` (``university_facilities``), capacities >= 0.
        rng: the seeded generator.

    Returns:
        ``location_id`` per person, with the index and row order of ``destination_ids``.

    Raises:
        ValueError: on a repeated or unsortable index, a missing destination, a destination without a facility or
            without capacity, or an invalid capacity.
    """
    _require_columns(facilities, ["location_id", "capacity", "destination_id"], "facilities")
    if destination_ids.isna().any():
        raise ValueError(f"{_LOG_TAG} a person has no destination to draw a building for")
    if not destination_ids.index.is_unique:
        raise ValueError(f"{_LOG_TAG} the index of the destinations names a person more than once; pass one row per "
                         f"person, indexed by person id")
    try:
        ordered = destination_ids.sort_index(kind="mergesort")
    except TypeError as error:
        raise ValueError(f"{_LOG_TAG} the index of the destinations must be of one comparable type: {error}") \
            from error
    capacity = pd.to_numeric(facilities["capacity"], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(capacity).all() or (capacity < 0).any():
        raise ValueError(f"{_LOG_TAG} the facility capacities must be finite and >= 0")
    wanted = ordered.to_numpy()
    known = set(facilities["destination_id"])
    unknown = sorted(str(destination) for destination in set(wanted) - known)
    if unknown:
        raise ValueError(f"{_LOG_TAG} destinations with no facility: {unknown}")
    location_ids = facilities["location_id"].to_numpy()
    facility_destination = facilities["destination_id"].to_numpy()
    drawn = np.empty(len(wanted), dtype=object)
    for destination in sorted(set(wanted)):
        rows = np.flatnonzero(facility_destination == destination)
        weight = capacity[rows]
        if not weight.sum() > 0:
            raise ValueError(f"{_LOG_TAG} destination {destination!r} has no facility with capacity")
        persons = np.flatnonzero(wanted == destination)
        drawn[persons] = location_ids[rows[rng.choice(len(rows), size=len(persons), p=weight / weight.sum())]]
    return pd.Series(drawn, index=ordered.index, name="location_id").loc[destination_ids.index]


# ------------------------------------------------------------------------------------------------ diagnostics
def allotment_diagnostics(assigned: pd.DataFrame, persons: pd.DataFrame, centres: pd.DataFrame,
                          radius_km: float = 2.0, *, kreis_column: str) -> dict:
    """The figures of design B5 for the comparison of the two rules, from the frames of one allotment.

    Args:
        assigned: ``[person_id, destination_id, path]`` (:func:`allot_within_kreis`): whole allotments, i.e. every
            person of each allotted home Kreis (the places of a Kreis are read off them, see below).
        persons: ``[person_id, x, y, leg_km]`` of the same persons.
        centres: ``[destination_id, x, y, scope]`` (:func:`destination_centres`).
        radius_km: a person living within this straight-line distance of a local site's centre lives "next to" the
            site. The default 2 km is the design's reporting radius (B5); it enters no allotment.
        kreis_column: the column of ``assigned`` naming each person's home Kreis (required, also for a frame of
            one Kreis: without it the places of several Kreise would be pooled and the "with place" counts would
            come out too high).

    Returns:
        A dict: ``n_persons``; ``n_by_path`` (path -> persons, sorted); ``n_with_observed_leg``;
        ``mean_abs_leg_gap_km``, the mean of ``|d(home, assigned centre) - leg_km|`` over the persons with an
        observed leg (under the plan rule exactly its primary persons; under the home-distance rule the same persons,
        for comparison; NaN without any); ``mean_distance_km``, the mean ``d(home, assigned centre)`` (NaN without
        persons); ``radius_km``; and two pairs of counts of persons living next to a local site (two sites of one
        commune share a centre, so going to the other is not "elsewhere"):

        - ``n_near_local_site``: the persons within the radius of at least one local site's centre, whatever their
          Kreis's places; ``n_near_local_site_sent_elsewhere``: those of them whose destination is none of the local
          sites within the radius. This counts also persons next to a site their Kreis has no place at (cell total
          0), whom no rule could send there.
        - ``n_near_local_site_with_place``: the persons within the radius of at least one local site that has a
          place for their home Kreis (cell total > 0); ``n_near_local_site_with_place_sent_elsewhere``: those of
          them whose destination is none of the local sites within the radius. These are the persons the rule could
          have kept next to their site and did not. A site has a place for a Kreis exactly when a person of that
          Kreis was sent there, because every cell receives exactly its total.

    Raises:
        ValueError: on a missing column, a radius that is not finite and > 0, persons that differ between
            ``assigned`` and ``persons``, a missing home Kreis, or a destination without a centre.
    """
    if not (math.isfinite(radius_km) and radius_km > 0):
        raise ValueError(f"{_LOG_TAG} the radius must be finite and > 0 km, got {radius_km!r}")
    assigned_columns = _ASSIGNED_COLUMNS + [kreis_column]
    _require_columns(assigned, assigned_columns, "assigned persons")
    _require_columns(centres, _CENTRE_COLUMNS + ["scope"], "centres (destination_centres)")
    frame = _checked_persons(persons)
    if assigned["person_id"].duplicated().any() or set(assigned["person_id"]) != set(frame["person_id"]):
        raise ValueError(f"{_LOG_TAG} the assigned persons and the persons are not the same persons")
    merged = frame.merge(assigned[assigned_columns], on="person_id", how="left", validate="one_to_one")
    if merged[kreis_column].isna().any():
        raise ValueError(f"{_LOG_TAG} an assigned person has no home Kreis in {kreis_column!r}")
    kreis = pd.factorize(merged[kreis_column])[0]
    destinations = pd.Index(sorted(set(merged["destination_id"])))
    centre_xy = _centre_xy(centres, destinations)
    column = destinations.get_indexer(merged["destination_id"])
    distance_km = _distance_matrix_m(merged, centre_xy)[np.arange(len(merged)), column] / 1000.0

    local = centres.loc[centres["scope"] == _SCOPE_LOCAL, "destination_id"]
    near = _distance_matrix_m(merged, _centre_xy(centres, pd.Index(local))) / 1000.0 <= radius_km
    sent_to = merged["destination_id"].to_numpy()
    goes_to_near_site = np.zeros(len(merged), dtype=bool)
    has_place = np.zeros(near.shape, dtype=bool)
    for position, destination in enumerate(local):
        goes_to_near_site |= near[:, position] & (sent_to == destination)
        has_place[:, position] = np.isin(kreis, np.unique(kreis[sent_to == destination]))
    near_any = near.any(axis=1)
    near_with_place = (near & has_place).any(axis=1)

    leg = merged["leg_km"].to_numpy(dtype=float)
    observed = ~np.isnan(leg)
    return {
        "n_persons": int(len(merged)),
        "n_by_path": {str(path): int(n) for path, n in sorted(merged["path"].value_counts().items())},
        "n_with_observed_leg": int(observed.sum()),
        "mean_abs_leg_gap_km": float(np.abs(distance_km[observed] - leg[observed]).mean()) if observed.any()
        else math.nan,
        "mean_distance_km": float(distance_km.mean()) if len(merged) else math.nan,
        "radius_km": float(radius_km),
        "n_near_local_site": int(near_any.sum()),
        "n_near_local_site_sent_elsewhere": int((near_any & ~goes_to_near_site).sum()),
        "n_near_local_site_with_place": int(near_with_place.sum()),
        "n_near_local_site_with_place_sent_elsewhere": int((near_with_place & ~goes_to_near_site).sum()),
    }


def random_allotment_mean_distance(persons: pd.DataFrame, cell_totals: pd.Series, centres: pd.DataFrame,
                                   rng: np.random.Generator) -> float:
    """The mean home-to-centre distance in km of one random allotment with the same cell totals.

    The baseline the two rules are read against: the places of the cells are shuffled over the persons (persons
    sorted by ``person_id`` first, so the row order does not matter). NaN without persons.

    Raises:
        ValueError: as :func:`allot_within_kreis` for invalid persons, totals or centres.
    """
    frame = _checked_persons(persons)
    totals = _checked_cell_totals(cell_totals)
    if len(frame) != int(totals.sum()):
        raise ValueError(f"{_LOG_TAG} {len(frame)} persons but the cell totals add up to {int(totals.sum())} persons")
    if len(frame) == 0:
        return math.nan
    cells = totals[totals > 0]
    distance_km = _distance_matrix_m(frame, _centre_xy(centres, cells.index)) / 1000.0
    column = rng.permutation(np.repeat(np.arange(len(cells)), cells.to_numpy()))
    return float(distance_km[np.arange(len(frame)), column].mean())
