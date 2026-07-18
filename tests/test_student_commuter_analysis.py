import pandas as pd
from braunschweig.analysis.simwrapper import student_commuters as sc


def test_student_od_aggregates_and_sums_to_count():
    persons = pd.DataFrame({
        "orig_ars5": ["09999", "09999", "16077"],
        "dest_commune": ["03101", "03101", "03158"]})
    od = sc.student_od(persons)
    assert od.set_index(["from_ars5", "to_commune"]).loc[("09999", "03101"), "value"] == 2
    assert od["value"].sum() == len(persons)


def test_student_od_origins_outside_zgb():
    persons = pd.DataFrame({"orig_ars5": ["09999"], "dest_commune": ["03101"]})
    od = sc.student_od(persons)
    assert not od["from_ars5"].str.startswith("031").any()  # no ZGB-BS origin
