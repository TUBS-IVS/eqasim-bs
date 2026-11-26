"""Synthetic inputs of the student level gate, shared by the evidence and level-table tests.

One definition instead of two copies (precedent: ``tests/fleet_frames.py``); the expected values
of both test modules follow from the hand-built numbers in :func:`gate_inputs`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ZGB = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")


def share_row(code, level, n_adults, n_students, share_adults, n_20_29, n_students_20_29,
              share_20_29, se_20_29, students_weighted):
    return {"code": code, "level": level, "n_adults": n_adults, "n_students": n_students,
            "student_share_adults": share_adults, "n_effective_adults": float(n_adults),
            "se_student_share_adults": 0.01, "n_20_29": n_20_29,
            "n_students_20_29": n_students_20_29, "student_share_20_29": share_20_29,
            "n_effective_20_29": float(n_20_29), "se_student_share_20_29": se_20_29,
            "students_weighted_persons": students_weighted, "presence_share": np.nan,
            "n_effective_presence": np.nan, "se_presence_share": np.nan}


def gate_inputs():
    """Hand-built inputs; the expected values of the gate tests follow from these numbers.

    Census: 03101 has 500 students of 4000 persons aged 20-29; every other covered Kreis 100 of
    2000; Wolfsburg (03103, not surveyed by SrV) 300 of 3000; Land 03 8000 of 100000 (8 %);
    Germany DG 100000 of 1000000 (10 %). MiD student share 20-29: DE 0.12, Land 03 0.10. So the
    Germany ratio is 0.12 / 0.10 = 1.2 and the Land ratio 0.10 / 0.08 = 1.25.
    """
    shares = []
    for code in ZGB:
        if code == "03101":
            shares.append(share_row(code, "kreis", 300, 60, 0.2, 80, 20, 0.25, 0.05, 12000.0))
        elif code == "03103":
            shares.append(share_row(code, "kreis", 0, 0, np.nan, 0, 0, np.nan, np.nan, 0.0))
        else:
            shares.append(share_row(code, "kreis", 100, 5, 0.05, 30, 3, 0.1, 0.06, 900.0))
    shares.append(share_row("03ZGB", "total", 900, 90, 0.1, 260, 38, 0.15, 0.02, 20000.0))
    srv = pd.DataFrame(shares)

    census_rows = []
    for code in ZGB:
        students, persons = {"03101": (500, 4000), "03103": (300, 3000)}.get(code, (100, 2000))
        census_rows.append((code, "Kreis " + code, "kreis", "20-29", students, persons))
    census_rows.append(("03", "Niedersachsen", "land", "20-29", 8000, 100000))
    census_rows.append(("DG", "Deutschland", "germany", "20-29", 100000, 1000000))
    census = pd.DataFrame(census_rows, columns=["region", "region_name", "region_level",
                                                "age_group", "students_nonemployed_persons",
                                                "all_persons"])

    mid = pd.DataFrame({"region": ["DE", "03", "06"], "n_unweighted": [5000, 400, 300],
                        "share_pupils": [0.05, 0.05, 0.05],
                        "share_students": [0.12, 0.10, 0.30],
                        "share_pupils_and_students": [0.17, 0.15, 0.35]})

    sites = pd.DataFrame(
        [("Uni A", "A", "03101000", "03101", "local", 1000, "WS 2099/00"),
         ("Art school", "A", "03101000", "03101", "local", 200, "WS 2099/00"),
         ("HS B", "W", "03103000", "03103", "local", 400, "WS 2099/00"),
         ("HS B", "F", "03158037", "03158", "local", 300, "WS 2099/00"),
         ("Uni H", "H", "03241001", "03241", "surrounding", 5000, "WS 2099/00")],
        columns=["institution", "site_commune", "site_ags8", "site_kreis_ars5", "scope",
                 "students_persons", "semester"])
    return srv, census, mid, sites
