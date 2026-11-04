import numpy as np
import pandas as pd
import itertools

"""
This stage has the census data as input and samples households according to the
household weights given by INSEE. The resulting sample size can be controlled
through the 'sampling_rate' configuration option.
"""

def configure(context):
    if context.config("projection_year", None) is None:
        context.stage("data.census.filtered", alias = "source")
    else:
        context.stage("synthesis.population.projection.reweighted", alias = "source")

    context.config("random_seed")
    context.config("sampling_rate")

def execute(context):
    # Stable sort: the members of a household keep their input order. numpy's default sort is not stable, so the
    # member order would be whatever the sort algorithm of the installed numpy version makes of equal keys.
    df_census = context.stage("source").sort_values(by = "household_id", kind = "stable").copy()

    sampling_rate = context.config("sampling_rate")
    random = np.random.RandomState(context.config("random_seed"))

    # Perform stochastic rounding for the population (and scale weights)
    df_rounding = df_census[["household_id", "weight", "household_size"]].drop_duplicates("household_id")
    df_rounding["multiplicator"] = np.floor(df_rounding["weight"])
    df_rounding["multiplicator"] += random.random_sample(len(df_rounding)) <= (df_rounding["weight"] - df_rounding["multiplicator"])
    df_rounding["multiplicator"] = df_rounding["multiplicator"].astype(int)

    # Multiply households (use same multiplicator for all household members)
    household_multiplicators = df_rounding["multiplicator"].values
    household_sizes = df_rounding["household_size"].values

    # The declared household_size cuts the row blocks below, so it must equal the number of person rows of every
    # household; otherwise a member would silently move to the neighbouring household. Both series follow the sorted
    # household_id order of df_census.
    person_rows = df_census.groupby("household_id", sort = True).size().to_numpy()
    if not np.array_equal(person_rows, household_sizes):
        mismatched = df_rounding["household_id"].to_numpy()[person_rows != household_sizes]
        raise ValueError(
            "synthesis.population.sampled: household_size differs from the number of person rows for %d of %d "
            "households (first household_ids: %s); fix household_size in the source stage"
            % (len(mismatched), len(household_sizes), ", ".join(str(value) for value in mismatched[:5])))

    # create index to replicate all households members by their household weight
    # the order ([0, 1, 0, 1, 2, 2, ...]) is important here as they will be reassigned to new housholds later with that assumption
    expandor = np.split(np.arange(len(df_census)), np.cumsum(household_sizes))
    expandor = [x for x in expandor if x.size > 0]
    # Repeat the block of row positions of every household once per replica. The blocks differ in length, so they
    # are repeated as a Python list: np.repeat(expandor, ...) would first turn the list into a ragged array (a
    # VisibleDeprecationWarning in numpy 1.23, a ValueError from numpy 1.24 on). After the check above the numbers of
    # blocks and multiplicators agree; strict=True keeps that assumption explicit.
    expandor = [block for block, count in zip(expandor, household_multiplicators, strict=True) for _ in range(count)]
    expandor = list(itertools.chain(*expandor))

    df_census = df_census.iloc[expandor]

    # Create new household and person IDs
    df_census["census_person_id"] = df_census["person_id"]
    df_census["census_household_id"] = df_census["household_id"]

    df_census["person_id"] = np.arange(len(df_census))

    household_sizes = np.repeat(household_sizes, household_multiplicators)
    household_count = np.sum(household_multiplicators)
    # Column assignment, not df.loc[:, column] = ...: pandas 1.5 warns that the .loc form will write in place in a
    # later version; this form keeps replacing the column, which is what the stage did all along.
    df_census["household_id"] = np.repeat(np.arange(household_count), household_sizes)

    # Select sample from 100% population
    selector = random.random_sample(household_count) < sampling_rate
    selector = np.repeat(selector, household_sizes)
    df_census = df_census[selector]

    del df_census["weight"]
    return df_census
