"""How concentrated the synthetic copies of the seed persons with HP_SEX_raw 3/9 are, per batch (#647 arm 2).

For the households that hold at least one such person: synthetic person copies of those persons, distinct
donor households used, and the copies of the most-copied donor household. Descriptive; reads the arm's
batch folders (seed_persons.csv with HP_SEX_raw, final_expanded_household_ids.csv).

Usage: python resolved_sex_concentration.py <arm_dir> batch_000 batch_032
"""
import sys
from pathlib import Path

import pandas as pd

arm = Path(sys.argv[1])
for batch in sys.argv[2:]:
    persons = pd.read_csv(arm / batch / "data" / "seed_persons.csv")
    expanded = pd.read_csv(arm / batch / "output" / "final_expanded_household_ids.csv")
    id_col = "H_ID" if "H_ID" in expanded.columns else expanded.columns[-1]
    copies = expanded[id_col].astype("int64").value_counts()
    persons["H_ID"] = pd.to_numeric(persons["H_ID"]).astype("int64")
    flagged = persons[persons["HP_SEX_raw"].isin([3, 9])]
    per_household = flagged.groupby("H_ID").size()
    used = per_household[per_household.index.isin(copies.index)]
    person_copies = (used * copies.reindex(used.index)).sum()
    top = copies.reindex(used.index).sort_values(ascending=False)
    print(f"{batch}: seed persons 3/9 {len(flagged)} in {len(per_household)} households; "
          f"used households {len(used)}; synthetic person copies {int(person_copies)}; "
          f"top household copies {top.head(5).astype(int).tolist()}")
