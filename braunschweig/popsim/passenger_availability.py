"""Derive MiD passenger availability independently of driver access.

``P_VAUTO`` describes general car access as a driver, passenger or car-sharing
user. It is therefore kept separate from the existing driver-only
``car_availability``. Adults retain valid own answers; missing adult responses
are imputed once per MiD donor person, and children use explicit household and
diary evidence before a logged empirical fallback.
"""

from __future__ import annotations

import logging

import pandas as pd

from braunschweig.popsim import missing

logger = logging.getLogger(__name__)

# Independent from the established +74511 person-attribute stream and +74513
# completed-donor stream. Keeping this in one named constant makes the produced
# imputation stable and prevents passenger draws from shifting legacy attributes.
PASSENGER_AVAILABILITY_RNG_OFFSET = 74517

PASSENGER_AVAILABILITY_BY_P_VAUTO = {1: "all", 2: "some", 3: "none"}
PASSENGER_AVAILABILITY_VALUES = frozenset({"none", "some", "all"})
PASSENGER_MIN_OWN_RESPONSE_AGE_YEARS = 14
HOUSEHOLD_ADULT_MIN_AGE_YEARS = 18
P_VAUTO_NONRESPONSE_CODES = (9, 206)
P_VAUTO_CHILD_CODE = 402
_MISSING_RESPONSE_SENTINEL = -1

SOURCE_OWN_RESPONSE = "own_response"
SOURCE_ADULT_IMPUTATION = "adult_empirical_imputation"
SOURCE_CHILD_HOUSEHOLD = "child_household_evidence"
SOURCE_CHILD_DIARY = "child_diary_evidence"
SOURCE_CHILD_FALLBACK = "child_empirical_fallback"

DIARY_EVIDENCE_COLUMN = "src_has_car_passenger_trip"


def _require_columns(frame: pd.DataFrame, columns, *, frame_name: str) -> None:
    absent = [column for column in columns if column not in frame.columns]
    if absent:
        raise KeyError(
            f"[passenger_availability] {frame_name} lacks required column(s) {absent}"
        )


def attach_car_passenger_diary_evidence(
    persons: pd.DataFrame,
    wege: pd.DataFrame,
    *,
    household_id_col: str = "H_ID",
    person_id_col: str = "P_ID",
    source_household_id_col: str = "source_H_ID",
    source_person_id_col: str = "source_P_ID",
    mode_col: str = "hvm_imp",
    rbw_col: str = "W_RBW",
) -> pd.DataFrame:
    """Attach whether the selected MiD plan source reported a passenger trip.

    Member-completion and diary matching may redirect a person's plan source via
    ``source_H_ID`` / ``source_P_ID``. When those columns are present the lookup
    follows them; otherwise it follows the person's own MiD keys. Regular
    work-related summary rows (``W_RBW == 1``) are not observed diary trips and
    therefore cannot provide passenger evidence.
    """
    _require_columns(
        persons, (household_id_col, person_id_col), frame_name="persons frame"
    )
    _require_columns(
        wege,
        (household_id_col, person_id_col, mode_col, rbw_col),
        frame_name="MiD Wege frame",
    )
    has_source_household = source_household_id_col in persons.columns
    has_source_person = source_person_id_col in persons.columns
    if has_source_household != has_source_person:
        raise KeyError(
            "[passenger_availability] persons frame must carry both plan-source "
            f"columns {source_household_id_col!r} and {source_person_id_col!r}, or neither"
        )
    lookup_household = source_household_id_col if has_source_household else household_id_col
    lookup_person = source_person_id_col if has_source_person else person_id_col

    mode = pd.to_numeric(wege[mode_col], errors="coerce")
    rbw = pd.to_numeric(wege[rbw_col], errors="coerce")
    reported = wege.loc[
        mode.eq(3) & ~rbw.eq(1), [household_id_col, person_id_col]
    ].drop_duplicates()
    reported_keys = pd.MultiIndex.from_frame(reported)
    lookup_keys = pd.MultiIndex.from_arrays(
        [persons[lookup_household], persons[lookup_person]]
    )
    out = persons.copy()
    out[DIARY_EVIDENCE_COLUMN] = lookup_keys.isin(reported_keys)
    n_reported = int(out[DIARY_EVIDENCE_COLUMN].sum())
    logger.info(
        "[passenger_availability] passenger diary evidence: %d/%d persons (%.2f%%) "
        "have at least one non-rbW hvm_imp=3 trip in their selected plan source",
        n_reported,
        len(out),
        100.0 * n_reported / max(len(out), 1),
    )
    return out


def _numeric_response(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    malformed_text = values.notna() & numeric.isna()
    if malformed_text.any():
        bad = values.loc[malformed_text].astype(str).value_counts().to_dict()
        raise ValueError(
            f"[passenger_availability] P_VAUTO contains non-numeric values: {bad}"
        )
    return numeric


def _validate_response_codes(responses: pd.Series, ages: pd.Series) -> None:
    adult = ages >= PASSENGER_MIN_OWN_RESPONSE_AGE_YEARS
    allowed_adult = set(PASSENGER_AVAILABILITY_BY_P_VAUTO) | set(P_VAUTO_NONRESPONSE_CODES)
    allowed_child = {P_VAUTO_CHILD_CODE, *P_VAUTO_NONRESPONSE_CODES}
    invalid_adult = adult & responses.notna() & ~responses.isin(allowed_adult)
    invalid_child = ~adult & responses.notna() & ~responses.isin(allowed_child)
    invalid = invalid_adult | invalid_child
    if invalid.any():
        details = (
            pd.DataFrame({"age": ages[invalid], "P_VAUTO": responses[invalid]})
            .value_counts(dropna=False)
            .to_dict()
        )
        raise ValueError(
            "[passenger_availability] P_VAUTO contains malformed or age-incompatible "
            f"codes: {details}. Valid age-14+ answers are 1/2/3, 9 and 206 are "
            "imputed, and under-14 persons must carry 402, 9, 206 or a missing value."
        )


def _adult_donor_rows(
    persons: pd.DataFrame,
    donor_households: pd.DataFrame,
    responses: pd.Series,
    ages: pd.Series,
) -> pd.DataFrame:
    adult = ages >= PASSENGER_MIN_OWN_RESPONSE_AGE_YEARS
    work = persons.loc[adult].copy()
    work["_passenger_response"] = responses.loc[adult]
    work["_passenger_has_household_car"] = (
        pd.to_numeric(work["number_of_cars"], errors="coerce") > 0
    ).astype(int)
    if "RegioStaR7" in donor_households.columns:
        regions = donor_households[["H_ID", "RegioStaR7"]].drop_duplicates()
        duplicate_households = regions["H_ID"].duplicated(keep=False)
        if duplicate_households.any():
            raise ValueError(
                "[passenger_availability] donor household frame carries conflicting "
                "RegioStaR7 rows for the same H_ID"
            )
        work["_passenger_donor_region"] = work["H_ID"].map(
            regions.set_index("H_ID")["RegioStaR7"]
        )

    donor_keys = ["H_ID", "P_ID"]
    consistency_columns = [
        "_passenger_response",
        "age",
        "alter_gr1",
        "_passenger_has_household_car",
        "_passenger_donor_region",
    ]
    consistency_columns = [c for c in consistency_columns if c in work.columns]
    for column in consistency_columns:
        inconsistent = work.groupby(donor_keys, dropna=False)[column].nunique(dropna=False) > 1
        if inconsistent.any():
            sample = list(inconsistent[inconsistent].index[:5])
            raise ValueError(
                f"[passenger_availability] repeated donor persons disagree in {column!r}; "
                f"example (H_ID, P_ID) keys: {sample}"
            )
    return work.drop_duplicates(donor_keys, keep="first")


def derive_car_passenger_availability(
    persons: pd.DataFrame,
    donor_households: pd.DataFrame,
    *,
    rng,
) -> pd.DataFrame:
    """Resolve ``car_passenger_availability`` and its public derivation source.

    Valid answers are never changed. Adult missing responses are drawn once per
    own MiD person key from comparable valid respondents using age group,
    household car access and donor RegioStaR7 when available. Children receive
    ``some`` for positive household or selected-diary evidence; resolved negative
    adult household evidence yields ``none``. Unknown households use a logged
    adult empirical fallback. An empty valid adult pool fails rather than inventing
    a default.
    """
    _require_columns(
        persons,
        (
            "H_ID",
            "P_ID",
            "household_id",
            "age",
            "P_VAUTO",
            "number_of_cars",
            "has_license",
        ),
        frame_name="persons frame",
    )
    _require_columns(donor_households, ("H_ID",), frame_name="donor household frame")
    out = persons.copy()
    ages = pd.to_numeric(out["age"], errors="coerce")
    if ages.isna().any():
        raise ValueError(
            f"[passenger_availability] age is missing or non-numeric for {int(ages.isna().sum())} persons"
        )
    responses = _numeric_response(out["P_VAUTO"])
    _validate_response_codes(responses, ages)

    adult_mask = ages >= PASSENGER_MIN_OWN_RESPONSE_AGE_YEARS
    unique_adults = _adult_donor_rows(out, donor_households, responses, ages)
    unique_adults["_passenger_response"] = unique_adults["_passenger_response"].fillna(
        _MISSING_RESPONSE_SENTINEL
    )
    valid_pool_size = int(
        unique_adults["_passenger_response"].isin(PASSENGER_AVAILABILITY_BY_P_VAUTO).sum()
    )
    needs_adult_imputation = unique_adults["_passenger_response"].isin(
        (_MISSING_RESPONSE_SENTINEL, *P_VAUTO_NONRESPONSE_CODES)
    ).any()
    child_mask = ~adult_mask
    if valid_pool_size == 0 and (needs_adult_imputation or child_mask.any()):
        raise ValueError(
            "[passenger_availability] valid adult P_VAUTO pool is empty; enabled MiD "
            "passenger availability cannot impute adult nonresponse or child fallback"
        )

    group_cols = [
        column
        for column in (
            "alter_gr1",
            "_passenger_has_household_car",
            "_passenger_donor_region",
        )
        if column in unique_adults.columns
    ]
    spec = missing.AttributeSpec(
        name="car_passenger_availability",
        source_col="_passenger_response",
        value_map=PASSENGER_AVAILABILITY_BY_P_VAUTO,
        structural={},
        impute_codes=(_MISSING_RESPONSE_SENTINEL, *P_VAUTO_NONRESPONSE_CODES),
        group_cols=tuple(group_cols),
        default=None,
    )
    resolved_adults, report = missing.resolve(unique_adults, spec, rng=rng)
    if resolved_adults.isna().any():
        raise ValueError(
            "[passenger_availability] adult P_VAUTO imputation produced missing values; "
            "the valid empirical pool is unusable"
        )
    donor_keys = pd.MultiIndex.from_frame(unique_adults[["H_ID", "P_ID"]])
    resolved_by_key = pd.Series(resolved_adults.to_numpy(), index=donor_keys)
    person_adult_keys = pd.MultiIndex.from_frame(out.loc[adult_mask, ["H_ID", "P_ID"]])
    out.loc[adult_mask, "car_passenger_availability"] = resolved_by_key.reindex(
        person_adult_keys
    ).to_numpy()
    own_valid = adult_mask & responses.isin(PASSENGER_AVAILABILITY_BY_P_VAUTO)
    out.loc[own_valid, "passenger_availability_source"] = SOURCE_OWN_RESPONSE
    out.loc[adult_mask & ~own_valid, "passenger_availability_source"] = SOURCE_ADULT_IMPUTATION

    diary = (
        out[DIARY_EVIDENCE_COLUMN].fillna(False).astype(bool)
        if DIARY_EVIDENCE_COLUMN in out.columns
        else pd.Series(False, index=out.index)
    )
    conflicts = own_valid & responses.eq(3) & diary
    logger.info(
        "[passenger_availability] own_none_with_reported_passenger=%d; valid own "
        "P_VAUTO=3 remains authoritative",
        int(conflicts.sum()),
    )

    household_adult = ages >= HOUSEHOLD_ADULT_MIN_AGE_YEARS
    adult_rows = out.loc[household_adult]
    adult_exists = adult_rows.groupby("household_id").size().gt(0)
    adult_positive = (
        adult_rows["car_passenger_availability"]
        .isin(("all", "some"))
        .groupby(adult_rows["household_id"])
        .any()
    )
    licensed_adult = (
        adult_rows["has_license"]
        .fillna(False)
        .astype(bool)
        .groupby(adult_rows["household_id"])
        .any()
    )
    child_households = out.loc[child_mask, "household_id"]
    has_adult = child_households.map(adult_exists).fillna(False).astype(bool)
    has_positive_adult = child_households.map(adult_positive).fillna(False).astype(bool)
    has_licensed_adult = child_households.map(licensed_adult).fillna(False).astype(bool)
    child_cars = pd.to_numeric(out.loc[child_mask, "number_of_cars"], errors="coerce")
    positive_household = has_positive_adult | (child_cars.gt(0) & has_licensed_adult)
    child_diary = diary.loc[child_mask]

    child_indices = out.index[child_mask]
    positive_household_indices = child_indices[positive_household.to_numpy()]
    out.loc[positive_household_indices, "car_passenger_availability"] = "some"
    out.loc[positive_household_indices, "passenger_availability_source"] = SOURCE_CHILD_HOUSEHOLD

    diary_only = ~positive_household & child_diary
    diary_indices = child_indices[diary_only.to_numpy()]
    out.loc[diary_indices, "car_passenger_availability"] = "some"
    out.loc[diary_indices, "passenger_availability_source"] = SOURCE_CHILD_DIARY

    resolved_negative = ~positive_household & ~child_diary & has_adult
    negative_indices = child_indices[resolved_negative.to_numpy()]
    out.loc[negative_indices, "car_passenger_availability"] = "none"
    out.loc[negative_indices, "passenger_availability_source"] = SOURCE_CHILD_HOUSEHOLD

    fallback = ~positive_household & ~child_diary & ~has_adult
    fallback_indices = child_indices[fallback.to_numpy()]
    if len(fallback_indices):
        empirical_pool = unique_adults.loc[
            unique_adults["_passenger_response"].isin(PASSENGER_AVAILABILITY_BY_P_VAUTO),
            "_passenger_response",
        ].map({1: "some", 2: "some", 3: "none"}).to_numpy()
        draws = rng.randint(len(empirical_pool), size=len(fallback_indices))
        out.loc[fallback_indices, "car_passenger_availability"] = empirical_pool[draws]
        out.loc[fallback_indices, "passenger_availability_source"] = SOURCE_CHILD_FALLBACK
        logger.warning(
            "[passenger_availability] child empirical fallback: %d/%d children "
            "(%.2f%%) lacked usable adult household and diary evidence",
            len(fallback_indices),
            int(child_mask.sum()),
            100.0 * len(fallback_indices) / max(int(child_mask.sum()), 1),
        )

    invalid_output = ~out["car_passenger_availability"].isin(PASSENGER_AVAILABILITY_VALUES)
    if invalid_output.any():
        raise ValueError(
            "[passenger_availability] derivation left invalid passenger availability "
            f"for {int(invalid_output.sum())} persons"
        )
    logger.info(
        "[passenger_availability] sources: own=%d, adult_imputed=%d, "
        "child_household=%d, child_diary=%d, child_fallback=%d; adult group "
        "fallback=%d/%d",
        int((out["passenger_availability_source"] == SOURCE_OWN_RESPONSE).sum()),
        int((out["passenger_availability_source"] == SOURCE_ADULT_IMPUTATION).sum()),
        int((out["passenger_availability_source"] == SOURCE_CHILD_HOUSEHOLD).sum()),
        int((out["passenger_availability_source"] == SOURCE_CHILD_DIARY).sum()),
        int((out["passenger_availability_source"] == SOURCE_CHILD_FALLBACK).sum()),
        report.n_group_fallback,
        report.n_nonresponse,
    )
    out["car_passenger_availability"] = out["car_passenger_availability"].astype("string")
    out["passenger_availability_source"] = out["passenger_availability_source"].astype("string")
    return out
