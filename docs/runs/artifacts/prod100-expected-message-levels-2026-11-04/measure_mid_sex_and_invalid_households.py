"""Read-only measurement on the raw MiD 2023 files (run manifest prod100-expected-message-levels-2026-11-04).

Prints: number of persons, HP_SEX value counts, persons and household rows whose H_ID is 0 or null.
Reads MiD2023_Personen.csv (H_ID, P_ID, HP_SEX) and MiD2023_Haushalte.csv (H_ID); writes nothing.
Usage: python measure_mid_sex_and_invalid_households.py <mid2023_raw directory>
"""
import sys

import pandas as pd

mid_dir = sys.argv[1]


def read(name, columns):
    path = mid_dir + "/" + name
    with open(path, encoding="utf-8-sig") as handle:
        header = handle.readline()
    sep = ";" if header.count(";") > header.count(",") else ","
    return pd.read_csv(path, sep=sep, usecols=columns), sep


persons, sep_p = read("MiD2023_Personen.csv", ["H_ID", "P_ID", "HP_SEX"])
households, sep_h = read("MiD2023_Haushalte.csv", ["H_ID"])
print("separator persons=%r households=%r" % (sep_p, sep_h))
print("persons rows:", len(persons))
print("HP_SEX value counts (dropna=False):")
print(persons["HP_SEX"].value_counts(dropna=False).sort_index().to_string())
n_fallback = int((~persons["HP_SEX"].isin([1, 2])).sum())
print("HP_SEX not in {1,2}: %d of %d = %.4f %%" % (n_fallback, len(persons), 100.0 * n_fallback / len(persons)))
bad_p = persons["H_ID"].isna() | (persons["H_ID"] == 0)
bad_h = households["H_ID"].isna() | (households["H_ID"] == 0)
print("household rows:", len(households))
print("persons with H_ID 0 or null:", int(bad_p.sum()))
print("household rows with H_ID 0 or null:", int(bad_h.sum()))
print("distinct H_ID among the bad persons:", persons.loc[bad_p, "H_ID"].nunique(dropna=False))
