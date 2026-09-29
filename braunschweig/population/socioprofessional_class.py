"""The eqasim/INSEE socioprofessional class of a synthetic person, shared by both producers.

The in-house IPF chain (``braunschweig.ipf.attributed``) and the PopulationSim attribute
mapper (``braunschweig.popsim.attributes``) derive ``socioprofessional_class`` with the same
rule. It lives here, outside both, so a stage that applies the rule neither imports nor has to
hash the IPF attribute stage (ADR-0136). Moved verbatim from ``braunschweig.ipf.attributed``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# eqasim/INSEE CS1 socioprofessional-class codes (see
# eqasim_common.analysis.marginals.SOCIOPROFESIONAL_CLASS_LABELS):
#   0 ??? 1 Agriculture 2 Independent 3 Science 4 Intermediate
#   5 Employee 6 Worker 7 Retired 8 Other (incl. students/children)
SPC_INDEPENDENT = 2
SPC_SCIENCE = 3
SPC_INTERMEDIATE = 4
SPC_EMPLOYEE = 5
SPC_WORKER = 6
SPC_RETIRED = 7
SPC_STUDENT = 8          # students fall in the inactive "Other" class (ENTD CS24 80 -> 8)
SPC_OTHER_INACTIVE = 8   # working-age inactive non-students also map to "Other"

# Statutory retirement age used to separate retired (-> SPC 7) from working-age
# inactive (-> SPC 8). Germany's Regelaltersgrenze is rising toward 67; 65 is a
# conservative threshold for classifying the inactive elderly.
SPC_RETIREMENT_AGE = 65


def derive_socioprofessional_class(employed, age, studies):
    """Map the eqasim/INSEE ``socioprofessional_class`` from broad activity
    status (Task A3c).

    No occupation data exists upstream of the HTS in this fork, so SPC is derived
    from the activity status that the IPF + age inflation DO carry:

    - ``studies`` -> 8 (Other; students are inactive in the CS1 sense, matching
      ENTD CS24 code 80 -> 8).
    - inactive (not employed, not studying) and ``age >= SPC_RETIREMENT_AGE``
      -> 7 (Retired).
    - inactive working-age -> 8 (Other inactive).
    - employed -> an age-proxied active occupational class. Because real
      occupation is unavailable, age is used as a COARSE seniority proxy
      (documented assumption, NOT measured occupation): young employed lean
      toward Worker/Employee, mid-career toward Intermediate, older toward
      Science (cadres). This keeps the active SPC non-degenerate so the HTS
      matching does not collapse all employed persons onto one donor class.

    The mapping is a pure deterministic function of (employed, age, studies);
    studies takes precedence over employment (working students count as
    students for the activity-status attribute).

    Returns:
        An integer Series aligned to the inputs.
    """
    employed = pd.Series(employed).reset_index(drop=True)
    age = pd.Series(age).reset_index(drop=True)
    studies = pd.Series(studies).reset_index(drop=True)

    spc = pd.Series(np.full(len(age), SPC_OTHER_INACTIVE, dtype=int))

    # Retired: inactive elderly (overwritten by studies/employed below).
    spc[age >= SPC_RETIREMENT_AGE] = SPC_RETIRED

    # Employed -> age-proxied active class. Boundaries are a documented coarse
    # seniority proxy, not measured occupation.
    emp = employed.astype(bool).to_numpy()
    a = age.to_numpy()
    active = np.full(len(age), SPC_INTERMEDIATE, dtype=int)
    active[a < 25] = SPC_EMPLOYEE        # entry-level / apprenticeship-aged
    active[(a >= 25) & (a < 45)] = SPC_INTERMEDIATE
    active[a >= 45] = SPC_SCIENCE        # senior / cadre-aged
    spc[emp] = active[emp]

    # Students take precedence over employment for the activity-status attribute.
    spc[studies.astype(bool)] = SPC_STUDENT
    return spc.reset_index(drop=True)
