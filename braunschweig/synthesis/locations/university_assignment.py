"""Residents' university flows: the balanced fit and an unbiased controlled rounding (issue #140, Phase 4).

Design ``docs/superpowers/specs/2026-11-25-student-commuting-design.md``, section 5a, B5 and B6 "Count and
origin". Two pure functions, no synpp, no stage, no file access:

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

Import boundary (Phase 4 global constraints): numpy and pandas only. The module is a hashed helper of the
location stage; keeping it free of project imports keeps that hash closure small.
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
        ValueError: on invalid input; when a destination has a positive target but no home with persons has a
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
                         f"{unreachable}; their share of the students would be lost")
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
        raise ValueError(f"{_LOG_TAG} the fit did not converge within {max_iterations} iterations: last maximum "
                         f"column deviation {deviation:.6g} persons (tolerance {tolerance:g}); the zeros of the "
                         f"seed may make the row and column totals infeasible")

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
