"""Byte-identity tests for the IPF selector-index construction in
``braunschweig.ipf.model``.

The IPF stage builds, per constraint, the array of ``df_model`` row indices the
constraint applies to. The construction was refactored from chained boolean masks
(O(constraints x |df_model|), the dominant cost at 100 %) to a single
``groupby(...).indices`` pass per constraint group with O(1) per-combination dict
lookups. The refactor is REQUIRED to be byte-identical: the post-IPF diagnostic
indexes the selector list positionally and the synthesis is seeded, so any change
in the number, order, or contents of the selectors would re-baseline the whole
population.

These tests:

1. Reconstruct the LEGACY selectors with the exact original boolean-mask logic
   (``np.nonzero(mask.values)``) on a synthetic ``df_model``.
2. Build the NEW selectors via the refactored ``model._build_group_indices`` /
   ``model._EMPTY_SELECTOR`` lookup path, in the SAME combination order.
3. Assert identical count, order, and index arrays (``np.array_equal``).

Both the joint age x hh_size and the employment-by-hhsize (4-way) constraint
groups are covered, since they were the two ``iterrows()`` loops.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from braunschweig.ipf import model


# ---------------------------------------------------------------------------
# Synthetic df_model fixture mirroring the structure built in model.execute().
# Small but exercises: multiple communes/departements, both sexes, several age
# classes, both employed/license states, all hh_size bins, an age_group column,
# and -- crucially -- combinations that DO NOT occur (-> empty selectors).
# ---------------------------------------------------------------------------

def _synthetic_df_model():
    communes = [10, 11, 12]
    # commune -> departement (Kreis) mapping.
    commune_to_dep = {10: 100, 11: 100, 12: 101}
    sexes = [1, 2]
    age_classes = [0, 16, 30, 65]  # combined_age_class lower bounds
    employed = [True, False]
    license_ = [True, False]
    hh_sizes = list(model.HH_SIZE_BINS)

    rows = []
    for c, s, a, e, lic, h in itertools.product(
        communes, sexes, age_classes, employed, license_, hh_sizes
    ):
        rows.append((c, commune_to_dep[c], s, a, e, lic, h))
    df = pd.DataFrame(
        rows,
        columns=["commune_index", "departement_index", "sex",
                 "combined_age_class", "employed", "license", "hh_size"],
    )

    # The model maps the combined age class to per-margin age classes. Use a
    # coarser banding so several combined classes collapse onto one margin class
    # (this is exactly the situation the real mappings create).
    population_age_mapping = {0: 0, 16: 16, 30: 30, 65: 65}
    employment_age_mapping = {0: 0, 16: 16, 30: 16, 65: 65}   # 30 -> 16 band
    license_age_mapping = {0: 0, 16: 16, 30: 16, 65: 65}      # 30 -> 16 band
    df["age_class_population"] = df["combined_age_class"].replace(population_age_mapping)
    df["age_class_employment"] = df["combined_age_class"].replace(employment_age_mapping)
    df["age_class_license"] = df["combined_age_class"].replace(license_age_mapping)

    # age_group (coarse) as built for the joint margin.
    age_group_mapping = {0: 0, 16: 15, 30: 30, 65: 60}
    df["age_group"] = df["combined_age_class"].replace(age_group_mapping)

    return df


def _unique_axes(df):
    unique_communes = np.sort(df["commune_index"].unique())
    unique_departements = np.sort(df["departement_index"].unique())
    unique_sexes = np.sort(df["sex"].unique())
    population_age_classes = np.sort(df["age_class_population"].unique())
    employment_age_classes = np.sort(df["age_class_employment"].unique())
    license_age_classes = np.sort(df["age_class_license"].unique())
    return (unique_communes, unique_departements, unique_sexes,
            population_age_classes, employment_age_classes, license_age_classes)


# ---------------------------------------------------------------------------
# Legacy reference selector construction (verbatim boolean-mask logic).
# ---------------------------------------------------------------------------

def _legacy_selectors(df_model, *, minimum_employment_age=16,
                      minimum_one_person_age=16, dj=None, emp_long=None):
    (unique_communes, unique_departements, unique_sexes,
     population_age_classes, employment_age_classes,
     license_age_classes) = _unique_axes(df_model)
    unique_hh_sizes = list(model.HH_SIZE_BINS)

    selectors = []

    # Population
    for comb in itertools.product(unique_communes, unique_sexes, population_age_classes):
        f = df_model["commune_index"] == comb[0]
        f &= df_model["sex"] == comb[1]
        f &= df_model["age_class_population"] == comb[2]
        selectors.append(f)

    # Employment
    for comb in itertools.product(unique_departements, unique_sexes, employment_age_classes):
        f = df_model["departement_index"] == comb[0]
        f &= df_model["sex"] == comb[1]
        f &= df_model["age_class_employment"] == comb[2]
        f &= df_model["employed"]
        selectors.append(f)

    # Minimum employment age
    f = df_model["combined_age_class"] < minimum_employment_age
    f &= df_model["employed"]
    selectors.append(f)

    # License country
    for comb in itertools.product(unique_sexes, license_age_classes):
        f = df_model["sex"] == comb[0]
        f &= df_model["age_class_license"] == comb[1]
        f &= df_model["license"]
        selectors.append(f)

    # License Kreis
    for departement_index in unique_departements:
        f = df_model["departement_index"] == departement_index
        f &= df_model["license"]
        selectors.append(f)

    # Household-size
    for comb in itertools.product(unique_communes, unique_hh_sizes):
        f = df_model["commune_index"] == comb[0]
        f &= df_model["hh_size"] == comb[1]
        selectors.append(f)

    # Household-size hard zero
    f = df_model["combined_age_class"] < minimum_one_person_age
    f &= df_model["hh_size"] == "1"
    selectors.append(f)

    # Joint age x hh_size
    if dj is not None:
        for _, row in dj.iterrows():
            f = df_model["departement_index"] == row["departement_index"]
            f &= df_model["age_group"] == int(row["age_group_lower"])
            f &= df_model["hh_size"] == row["hh_size"]
            selectors.append(f)

    # Employment-by-hhsize
    if emp_long is not None:
        for _, row in emp_long.iterrows():
            f = df_model["departement_index"] == row["departement_index"]
            f &= df_model["hh_size"] == row["hh_size"]
            f &= df_model["employed"] == bool(row["employed"])
            selectors.append(f)

    return [np.nonzero(s.values) for s in selectors]


# ---------------------------------------------------------------------------
# New selector construction (the refactored groupby path from model.py).
# ---------------------------------------------------------------------------

def _new_selectors(df_model, *, minimum_employment_age=16,
                   minimum_one_person_age=16, dj=None, emp_long=None):
    (unique_communes, unique_departements, unique_sexes,
     population_age_classes, employment_age_classes,
     license_age_classes) = _unique_axes(df_model)
    unique_hh_sizes = list(model.HH_SIZE_BINS)

    selectors = []

    population_indices = model._build_group_indices(
        df_model, ["commune_index", "sex", "age_class_population"])
    for comb in itertools.product(unique_communes, unique_sexes, population_age_classes):
        selectors.append(population_indices.get(tuple(comb), model._EMPTY_SELECTOR))

    employment_indices = model._build_group_indices(
        df_model, ["departement_index", "sex", "age_class_employment", "employed"])
    for comb in itertools.product(unique_departements, unique_sexes, employment_age_classes):
        selectors.append(
            employment_indices.get((comb[0], comb[1], comb[2], True),
                                   model._EMPTY_SELECTOR))

    f = df_model["combined_age_class"] < minimum_employment_age
    f &= df_model["employed"]
    selectors.append(f)

    license_country_indices = model._build_group_indices(
        df_model, ["sex", "age_class_license", "license"])
    for comb in itertools.product(unique_sexes, license_age_classes):
        selectors.append(
            license_country_indices.get((comb[0], comb[1], True), model._EMPTY_SELECTOR))

    license_kreis_indices = model._build_group_indices(
        df_model, ["departement_index", "license"])
    for departement_index in unique_departements:
        selectors.append(
            license_kreis_indices.get((departement_index, True), model._EMPTY_SELECTOR))

    hh_size_indices = model._build_group_indices(
        df_model, ["commune_index", "hh_size"])
    for comb in itertools.product(unique_communes, unique_hh_sizes):
        selectors.append(hh_size_indices.get(tuple(comb), model._EMPTY_SELECTOR))

    f = df_model["combined_age_class"] < minimum_one_person_age
    f &= df_model["hh_size"] == "1"
    selectors.append(f)

    if dj is not None:
        joint_indices = model._build_group_indices(
            df_model, ["departement_index", "age_group", "hh_size"])
        for _, row in dj.iterrows():
            key = (int(row["departement_index"]), int(row["age_group_lower"]),
                   row["hh_size"])
            selectors.append(joint_indices.get(key, model._EMPTY_SELECTOR))

    if emp_long is not None:
        emp_margin_indices = model._build_group_indices(
            df_model, ["departement_index", "hh_size", "employed"])
        for _, row in emp_long.iterrows():
            key = (int(row["departement_index"]), row["hh_size"], bool(row["employed"]))
            selectors.append(emp_margin_indices.get(key, model._EMPTY_SELECTOR))

    return [
        s if isinstance(s, tuple) else np.nonzero(s.values)
        for s in selectors
    ]


# ---------------------------------------------------------------------------
# Assertions.
# ---------------------------------------------------------------------------

def _assert_selectors_byte_identical(old, new):
    assert len(old) == len(new), (
        f"selector count changed: legacy={len(old)} new={len(new)}")
    for i, (o, n) in enumerate(zip(old, new)):
        # Both must be 1-tuples holding a single ascending int64 index array.
        assert isinstance(o, tuple) and isinstance(n, tuple), f"selector #{i} not a tuple"
        assert len(o) == 1 and len(n) == 1, f"selector #{i} arity != 1"
        oa, na = o[0], n[0]
        assert oa.dtype == na.dtype == np.int64, (
            f"selector #{i} dtype mismatch: {oa.dtype} vs {na.dtype}")
        assert np.array_equal(oa, na), (
            f"selector #{i} index arrays differ:\n legacy={oa}\n new   ={na}")
        # Index arrays are ascending (np.nonzero & groupby.indices both ascend).
        assert np.all(np.diff(na) > 0) if na.size > 1 else True, (
            f"selector #{i} new index array is not strictly ascending: {na}")


def test_selectors_byte_identical_base_constraints():
    """Population + employment + license + hh_size groups, no optional joints."""
    df = _synthetic_df_model()
    old = _legacy_selectors(df)
    new = _new_selectors(df)
    _assert_selectors_byte_identical(old, new)
    assert len(new) > 0


def test_selectors_byte_identical_with_joint_and_employment_margins():
    """Cover the two former iterrows() loops (joint age x size + emp-by-hhsize)."""
    df = _synthetic_df_model()

    # Joint age x hh_size targets (departement x age_group x hh_size). Include a
    # row whose (departement, age_group, hh_size) cell exists and one that maps to
    # an EMPTY selector (age_group 30 with hh_size "6+" exists; departement 999
    # does not) to exercise the missing-key path.
    dj = pd.DataFrame(
        [
            [100, 0, "5", 80.0],
            [100, 60, "1", 50.0],
            [101, 30, "2", 12.0],
            [999, 0, "1", 7.0],   # departement_index 999 absent -> empty selector
        ],
        columns=["departement_index", "age_group_lower", "hh_size", "weight"],
    )

    # Employment-by-hhsize targets (departement x hh_size x employed).
    emp_long = pd.DataFrame(
        [
            [100, "1", True, 30.0],
            [100, "1", False, 20.0],
            [101, "3", True, 9.0],
            [999, "2", True, 3.0],  # absent departement -> empty selector
        ],
        columns=["departement_index", "hh_size", "employed", "weight"],
    )

    old = _legacy_selectors(df, dj=dj, emp_long=emp_long)
    new = _new_selectors(df, dj=dj, emp_long=emp_long)
    _assert_selectors_byte_identical(old, new)


def test_empty_selector_matches_nonzero_of_all_false_mask():
    """The missing-key default must equal np.nonzero of an all-False mask."""
    df = _synthetic_df_model()
    all_false = df["commune_index"] == 99999  # no such commune
    legacy_empty = np.nonzero(all_false.values)
    assert np.array_equal(model._EMPTY_SELECTOR[0], legacy_empty[0])
    assert model._EMPTY_SELECTOR[0].dtype == legacy_empty[0].dtype == np.int64


def test_group_indices_are_ascending_and_partition_rows():
    """groupby().indices arrays are ascending and partition df_model exactly."""
    df = _synthetic_df_model()
    idx = model._build_group_indices(
        df, ["commune_index", "sex", "age_class_population"])
    seen = []
    for _, (arr,) in idx.items():
        assert arr.dtype == np.int64
        if arr.size > 1:
            assert np.all(np.diff(arr) > 0)
        seen.extend(arr.tolist())
    # Every row belongs to exactly one population group (no overlap, full cover).
    assert sorted(seen) == list(range(len(df)))
