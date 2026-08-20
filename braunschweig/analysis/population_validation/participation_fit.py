"""Acceptance-gate analysis for the SrV per-Kreis participation controls (#224).

Task 7 of feature #224: a standalone, tested analysis module reused by the
Task-8 popsim smoke to evaluate the pre-registered acceptance gate (spec
Section 9) -- per-Kreis realised-vs-SrV-target participation shares, the
Mobilitaetsquote (mobility rate), and a donor-duplication effective sample
size (N_eff). This module is NOT wired into any live pipeline stage; it is a
reusable analysis imported by whatever evaluates the gate.

HONESTY CAVEAT (mandatory, reproduce in any report built on this module):
these participation controls are a FIT CHECK (the SrV target STEERS the
raking), so a good realised-vs-target fit measures raking convergence, NOT
independent agreement with reality; and ~5-8pp of the mobility level is a
documented SrV-vs-MiD survey method offset (spec Section 7). A tight fit here
must never be reported as "validated against reality".
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from braunschweig.popsim.mid import PARTICIPATION_W_ZWECK

LOGGER = logging.getLogger("braunschweig.analysis.participation_fit")

# The per-purpose participation controls evaluated by this module, derived from
# mid.PARTICIPATION_W_ZWECK (the single source of truth for the registered purpose
# set: work / leisure / education from feature #224, escort from issue #227), plus
# the derived mobility ("Mobilitaetsquote") pseudo-purpose. A purpose registered
# there is picked up here automatically -- including its committed target file
# requirement in load_participation_targets.
PARTICIPATION_PURPOSES = tuple(PARTICIPATION_W_ZWECK)


def realised_participation(trips: pd.DataFrame, persons_kreis: pd.DataFrame) -> pd.DataFrame:
    """Per-Kreis realised participation and mobility rates from a trip diary.

    ``trips`` must carry a ``person_id`` column and either:

    - the eqasim ``eqasim_trips.csv`` schema (``following_purpose`` and
      ``preceding_purpose`` string columns, e.g. "work"/"leisure"/"education"),
      where a person participates in purpose P if any of their trips has
      ``following_purpose == P`` or ``preceding_purpose == P``; or
    - a raw MiD ``W_ZWECK`` int column, where a person participates in
      purpose P if any of their trips has ``W_ZWECK`` in
      ``mid.PARTICIPATION_W_ZWECK[P]``.

    The schema is detected from the columns present; if neither is found this
    raises ``KeyError`` (no silent fallback to an empty/zero result).

    ``persons_kreis`` must carry ``person_id`` and ``ars5`` (5-digit Kreis).
    Every person in ``persons_kreis`` is counted (including persons absent
    from ``trips``, who are immobile / non-participating by construction).

    Returns one row per (``ars5``, ``purpose``) with ``purpose`` in
    ``PARTICIPATION_PURPOSES`` plus ``"mobility"`` (currently work / leisure /
    education / escort / mobility): columns ``ars5, purpose,
    realised_rate, n_persons``. ``mobility`` is the share of persons with at
    least one trip of ANY purpose that day (the Mobilitaetsquote), i.e. the
    complement of the trip-class "0 trips" share.
    """
    missing_persons_cols = [c for c in ("person_id", "ars5") if c not in persons_kreis.columns]
    if missing_persons_cols:
        raise KeyError(
            f"realised_participation: persons_kreis is missing required column(s) "
            f"{missing_persons_cols} (has {list(persons_kreis.columns)}).")
    if "person_id" not in trips.columns:
        raise KeyError(
            f"realised_participation: trips is missing required column 'person_id' "
            f"(has {list(trips.columns)}).")

    has_purpose_string_schema = {"following_purpose", "preceding_purpose"}.issubset(trips.columns)
    has_wzweck_schema = "W_ZWECK" in trips.columns
    if not has_purpose_string_schema and not has_wzweck_schema:
        raise KeyError(
            "realised_participation: trips must carry either the eqasim "
            "'following_purpose'/'preceding_purpose' string pair or a MiD 'W_ZWECK' "
            f"int column; has {list(trips.columns)}. No silent fallback to an empty result.")
    if has_purpose_string_schema and has_wzweck_schema:
        LOGGER.info(
            "realised_participation: trips carries both the eqasim purpose-string "
            "columns and a MiD 'W_ZWECK' column; using the eqasim schema deterministically.")

    # Mobility: any recorded trip that day, regardless of its purpose (including
    # purely "home"-bound legs), makes a person mobile -- mirrors the trip-class
    # target's "0 trips" definition of immobility.
    mobile_person_ids = set(trips["person_id"].unique())

    per_purpose_person_ids: dict[str, set] = {}
    if has_purpose_string_schema:
        for purpose in PARTICIPATION_PURPOSES:
            mask = (trips["following_purpose"] == purpose) | (trips["preceding_purpose"] == purpose)
            per_purpose_person_ids[purpose] = set(trips.loc[mask, "person_id"].unique())
    else:
        for purpose in PARTICIPATION_PURPOSES:
            codes = PARTICIPATION_W_ZWECK[purpose]
            mask = trips["W_ZWECK"].isin(codes)
            per_purpose_person_ids[purpose] = set(trips.loc[mask, "person_id"].unique())

    rows = []
    for ars5, group in persons_kreis.groupby("ars5"):
        n_persons = len(group)
        if n_persons == 0:
            continue
        person_ids = set(group["person_id"])
        for purpose in (*PARTICIPATION_PURPOSES, "mobility"):
            participating_ids = mobile_person_ids if purpose == "mobility" else per_purpose_person_ids[purpose]
            n_participating = len(person_ids & participating_ids)
            rows.append({
                "ars5": ars5,
                "purpose": purpose,
                "realised_rate": n_participating / n_persons,
                "n_persons": n_persons,
            })
    return pd.DataFrame(rows, columns=["ars5", "purpose", "realised_rate", "n_persons"])


def load_participation_targets(targets_dir: Path) -> pd.DataFrame:
    """Load the committed SrV per-Kreis participation targets as a tidy frame.

    Reads ``target2026_<purpose>_participation_by_kreis.csv`` for EVERY purpose in
    ``PARTICIPATION_PURPOSES`` (currently work / leisure / education / escort;
    ``comment="#"``) and takes each purpose's ``<purpose>_yes`` column as the
    target participation rate -- one committed target file per registered purpose
    is REQUIRED, independent of whether that purpose's control is toggled on. Adds
    a ``mobility`` pseudo-purpose row per Kreis from
    ``target2026_trip_class_by_kreis.csv``, with ``target_rate = 1 - trips_0``
    (the Mobilitaetsquote implied by the SrV trip-class distribution).

    Returns tidy columns ``ars5, purpose, target_rate``.

    Raises ``FileNotFoundError`` if a required target file is missing, and
    ``KeyError`` if a required column is missing from a target file that IS
    present (no silent fallback to a guessed column name).
    """
    targets_dir = Path(targets_dir)
    frames = []

    for purpose in PARTICIPATION_PURPOSES:
        path = targets_dir / f"target2026_{purpose}_participation_by_kreis.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"load_participation_targets: required target file {path} is missing.")
        target = pd.read_csv(path, comment="#", dtype={"ars5": str})
        yes_col = f"{purpose}_yes"
        if "ars5" not in target.columns or yes_col not in target.columns:
            raise KeyError(
                f"load_participation_targets: {path} is missing required column(s) among "
                f"['ars5', {yes_col!r}] (has {list(target.columns)}).")
        frames.append(pd.DataFrame({
            "ars5": target["ars5"],
            "purpose": purpose,
            "target_rate": target[yes_col],
        }))

    trip_class_path = targets_dir / "target2026_trip_class_by_kreis.csv"
    if not trip_class_path.exists():
        raise FileNotFoundError(
            f"load_participation_targets: required trip-class target file "
            f"{trip_class_path} is missing (needed to derive the mobility target).")
    trip_class = pd.read_csv(trip_class_path, comment="#", dtype={"ars5": str})
    if "ars5" not in trip_class.columns or "trips_0" not in trip_class.columns:
        raise KeyError(
            f"load_participation_targets: {trip_class_path} is missing required column(s) "
            f"among ['ars5', 'trips_0'] (has {list(trip_class.columns)}).")
    frames.append(pd.DataFrame({
        "ars5": trip_class["ars5"],
        "purpose": "mobility",
        "target_rate": 1.0 - trip_class["trips_0"],
    }))

    return pd.concat(frames, ignore_index=True)


def participation_fit(trips: pd.DataFrame, persons_kreis: pd.DataFrame, targets_dir: Path) -> pd.DataFrame:
    """Join realised participation (interface 1) to the SrV targets (interface 2).

    Returns ``ars5, purpose, realised_rate, target_rate, abs_error``.

    Realised (``ars5``, ``purpose``) cells with no matching target row are
    logged (warning, with examples) and dropped -- mirroring
    ``control_validation.evaluate_control``'s out-of-vocabulary handling --
    rather than silently coercing them to NaN or 0.

    See the module docstring for the mandatory honesty caveat: a good fit here
    reflects raking convergence toward the SrV target, not independent
    agreement with reality.
    """
    realised = realised_participation(trips, persons_kreis)
    targets = load_participation_targets(targets_dir)

    merged = realised.merge(targets, on=["ars5", "purpose"], how="left", indicator=True)
    missing = merged[merged["_merge"] == "left_only"]
    if not missing.empty:
        examples = list(zip(missing["ars5"], missing["purpose"]))[:5]
        LOGGER.warning(
            "participation_fit: %d realised (ars5, purpose) cell(s) have no matching SrV "
            "target and are excluded from the fit; examples: %s", len(missing), examples)
    merged = merged[merged["_merge"] == "both"].drop(columns="_merge")
    merged["abs_error"] = (merged["realised_rate"] - merged["target_rate"]).abs()
    return merged[["ars5", "purpose", "realised_rate", "target_rate", "abs_error"]].reset_index(drop=True)


def donor_neff(persons: pd.DataFrame, donor_id_col: str) -> dict:
    """Effective sample size implied by donor duplication in a synthetic population.

    Each synthetic person is a copy of some donor identified by
    ``donor_id_col`` (e.g. the MiD source person id the population carries).
    Kish's design-effect formula for equal-weight cluster duplication gives:

        N_eff = N**2 / sum(copies_per_donor**2)

    where ``copies_per_donor`` is the value_counts of ``donor_id_col`` and
    ``N`` is the total number of persons. N_eff equals N iff every donor is
    used exactly once (no duplication); it shrinks as donors are reused more.

    Returns a dict with:

    - ``n``: total number of persons (int)
    - ``n_eff``: the effective sample size (float)
    - ``n_eff_fraction``: ``n_eff / n`` (float in (0, 1])
    - ``max_copies_over_median``: the most-duplicated donor's copy count
      divided by the median copy count across donors, a simple concentration
      diagnostic (float)

    Raises ``KeyError`` if ``donor_id_col`` is absent from ``persons`` (no
    silent fallback to an assumed column name), and ``ValueError`` if
    ``persons`` is empty (N_eff is undefined for zero persons).
    """
    if donor_id_col not in persons.columns:
        raise KeyError(
            f"donor_neff: donor id column {donor_id_col!r} is absent from the persons frame "
            f"(has {list(persons.columns)}); cannot compute the donor-duplication N_eff.")
    n = int(len(persons))
    if n == 0:
        raise ValueError("donor_neff: persons frame is empty; N_eff is undefined for zero persons.")

    copies_per_donor = persons[donor_id_col].value_counts()
    sum_squared_copies = float((copies_per_donor ** 2).sum())
    n_eff = (n ** 2) / sum_squared_copies

    return {
        "n": n,
        "n_eff": n_eff,
        "n_eff_fraction": n_eff / n,
        "max_copies_over_median": float(copies_per_donor.max()) / float(copies_per_donor.median()),
    }
