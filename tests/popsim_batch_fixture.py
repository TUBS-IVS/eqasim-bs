"""Synthetic PopulationSim work directory used by the popsim_batch_diagnostics tests.

One batch ``batch_000`` with two 100 m cells ``A`` and ``B`` inside one 1 km zone
``K1``.  The checkpoint store mirrors the real PopulationSim store: the incidence
table carries non-control columns next to the ``*_target`` columns, so consumers
must select control columns by name.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

TOTAL = "HH_TOTAL_ZENSUS100m_target"
SEED_HOUSEHOLDS = pd.DataFrame({
    "H_ID": [1, 2, 3, 4], "H_GEW": [0.5, 1.5, 2.0, 4.0], "H_GR": [1, 2, 1, 2],
    "H_MIETE": [1, 2, 9, 1], "haustyp": [2, 1, 1, 95], "STAAT": 1})
# H_ID as float text, as PopulationSim batch folders carry it for persons.
SEED_PERSONS = pd.DataFrame({
    "H_ID": ["1.0", "2.0", "2.0", "3.0", "4.0", "4.0"], "P_ID": [1, 1, 2, 1, 1, 2],
    "HP_ALTER": [30, 40, 8, 70, 50, 45], "HP_SEX": [1, 2, 1, 3, 1, 2], "STAAT": 1})
CONTROLS = [  # (target, seed_table, importance, expression); geography ZENSUS100m for all
    (TOTAL, "households", 1000000000, "(households.H_GEW > 0) & (households.H_GEW < np.inf)"),
    ("M_AGE_0_17_ZENSUS100m_target", "persons", 200, "(persons.HP_ALTER < 18)&(persons.HP_SEX==1)"),
    ("M_AGE_18_plus_ZENSUS100m_target", "persons", 200, "(persons.HP_ALTER > 17)&(persons.HP_SEX==1)"),
    ("F_AGE_0_17_ZENSUS100m_target", "persons", 200, "(persons.HP_ALTER < 18)&(persons.HP_SEX==2)"),
    ("F_AGE_18_plus_ZENSUS100m_target", "persons", 200, "(persons.HP_ALTER > 17)&(persons.HP_SEX==2)"),
    ("MieterHH_Tenure_ZENSUS100m_target", "households", 1000, "(households.H_MIETE == 1)"),
    ("EigentuemerHH_Tenure_ZENSUS100m_target", "households", 1000, "(households.H_MIETE == 2)"),
    ("building_type_efh_ZENSUS100m_target", "households", 1000, "(households.haustyp == 1)"),
    ("building_type_mfh_ZENSUS100m_target", "households", 1000, "(households.haustyp.isin([2, 3]))"),
]
# control_field = target without the "_target" suffix.
TOTALS_100M = {  # cell -> values in CONTROLS order
    "A": [3, 1, 1, 0, 2, 2, 1, 2, 2],
    "B": [2, 0, 2, 0, 1, 1, 1, 1, 2],
}
EXPANDED = [("A", 2), ("A", 4), ("A", 4), ("B", 3), ("B", 4)]   # (ZENSUS100m, H_ID)
# Checkpoint store, 100 m level: (cell, H_ID, balanced_weight, integer_weight)
WEIGHTS_100M = [("A", 1, 0.4, 0), ("A", 2, 0.9, 1), ("A", 3, 0.3, 0), ("A", 4, 1.4, 2),
                ("B", 1, 0.2, 0), ("B", 2, 0.1, 0), ("B", 3, 0.7, 1), ("B", 4, 1.0, 1)]
LOG_LINES = [
    "INFO - populationsim.integerizing.wrappers - do_simul_integerizing succeeded for STAAT_1 status OPTIMAL. ",
    "WARNING - populationsim.integerizing.wrappers - do_simul_integerizing failed for KREIS_3153 status INFEASIBLE. ",
    "ERROR - populationsim.integerizing.wrappers - do_simul_integerizing retry failed for KREIS_3153 status NOT_SOLVED.",
    "INFO - populationsim.integerizing.wrappers - do_simul_integerizing falling back to sequential integerizing for KREIS_3153.",
    "INFO - populationsim.integerizing.wrappers - do_simul_integerizing succeeded for KREIS_3153_ZENSUS1km_K1 status OPTIMAL. ",
    "WARNING - populationsim.integerizing.wrappers - do_simul_integerizing failed for KREIS_3153_ZENSUS1km_K2 status INFEASIBLE. ",
    "INFO - populationsim.integerizing.wrappers - do_simul_integerizing retry succeeded for KREIS_3153_ZENSUS1km_K2 status OPTIMAL. ",
]

_SETTINGS = """total_hh_control: HH_TOTAL_ZENSUS100m_target
household_id_col: H_ID
household_weight_col: H_GEW
geographies: [STAAT, ZENSUS1km, ZENSUS100m]
seed_geography: STAAT
"""


def _controls_frame(extra_controls: Sequence[dict]) -> pd.DataFrame:
    rows = [{"target": target, "geography": "ZENSUS100m", "seed_table": table,
             "importance": importance, "control_field": target[: -len("_target")],
             "expression": expression}
            for target, table, importance, expression in CONTROLS]
    rows.extend(dict(row) for row in extra_controls)
    return pd.DataFrame(rows, columns=["target", "geography", "seed_table", "importance",
                                       "control_field", "expression"])


def _incidence(controls: pd.DataFrame) -> pd.DataFrame:
    """Household incidence of the controls plus the non-control columns of the real store."""
    hh, persons = SEED_HOUSEHOLDS, SEED_PERSONS.copy()
    persons["H_ID"] = persons["H_ID"].astype(float).astype(int)
    out = pd.DataFrame(index=pd.Index(hh["H_ID"], name="H_ID"))
    for row in controls.itertuples():
        env = {"np": np, "households": hh, "persons": persons, "__builtins__": {}}
        if row.seed_table == "households":
            out[row.target] = np.asarray(eval(row.expression, env)).astype(int)  # noqa: S307
        elif row.seed_table == "persons":
            mask = np.asarray(eval(row.expression, env)).astype(int)  # noqa: S307
            per_hh = pd.Series(mask, index=persons["H_ID"]).groupby(level=0).sum()
            out[row.target] = per_hh.reindex(out.index, fill_value=0).to_numpy()
        else:  # extra controls without a known table are all-zero
            out[row.target] = 0
    out["STAAT"] = 1
    out["WELT"] = 1
    out["sample_weight"] = hh["H_GEW"].to_numpy()
    out["group_size"] = 1
    out["group_id"] = np.arange(len(hh))
    return out


def build_work_dir(root: Path, *, with_store: bool = True,
                   extra_controls: Sequence[dict] = ()) -> Path:
    """Write ``<root>/batch_000`` and return ``root``."""
    batch = Path(root) / "batch_000"
    for sub in ("data", "configs", "output"):
        (batch / sub).mkdir(parents=True, exist_ok=True)
    controls = _controls_frame(extra_controls)

    SEED_HOUSEHOLDS.to_csv(batch / "data" / "seed_households.csv", index=False)
    SEED_PERSONS.to_csv(batch / "data" / "seed_persons.csv", index=False)
    pd.DataFrame({"STAAT": 1, "ZENSUS1km": "K1", "ZENSUS100m": ["A", "B"]}).to_csv(
        batch / "data" / "geo_cross_walk.csv", index=False)

    totals = pd.DataFrame(
        [[cell] + values for cell, values in TOTALS_100M.items()],
        columns=["ZENSUS100m"] + [t[: -len("_target")] for t, *_ in CONTROLS])
    for row in pd.DataFrame(list(extra_controls)).itertuples() if extra_controls else ():
        totals[row.control_field] = 0
    totals.to_csv(batch / "data" / "control_totals_ZENSUS100m.csv", index=False)

    controls.to_csv(batch / "configs" / "controls.csv", index=False)
    (batch / "configs" / "settings.yaml").write_text(_SETTINGS, encoding="utf-8")

    pd.DataFrame([("1", "K1", cell, hid) for cell, hid in EXPANDED],
                 columns=["STAAT", "ZENSUS1km", "ZENSUS100m", "H_ID"]).to_csv(
        batch / "output" / "final_expanded_household_ids.csv", index=False)
    (batch / "output" / "populationsim.log").write_text(
        "\n".join(LOG_LINES) + "\n", encoding="utf-8")

    if with_store:
        weights = pd.DataFrame(WEIGHTS_100M, columns=["ZENSUS100m", "H_ID",
                                                       "balanced_weight", "integer_weight"])
        weights = weights[["H_ID", "ZENSUS100m", "balanced_weight", "integer_weight"]]
        cell_controls = totals.set_index("ZENSUS100m")
        cell_controls.columns = [c + "_target" for c in cell_controls.columns]
        with pd.HDFStore(str(batch / "output" / "pipeline.h5"), mode="w") as store:
            store.put("/incidence_table/setup_data_structures", _incidence(controls),
                      format="fixed")
            store.put("/control_spec/setup_data_structures", controls, format="fixed")
            store.put("/ZENSUS100m_controls/setup_data_structures", cell_controls,
                      format="fixed")
            store.put("/ZENSUS100m_weights/sub_balancing.geography=ZENSUS100m", weights,
                      format="fixed")
    return Path(root)
