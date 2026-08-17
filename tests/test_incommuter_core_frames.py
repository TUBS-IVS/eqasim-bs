from braunschweig.synthesis.incommuters import assemble_incommuter_core_frames


def test_core_frames_education_purpose():
    out = assemble_incommuter_core_frames(
        person_ids=[5], home_x=[0.0], home_y=[0.0], mid_x=[1000.0], mid_y=[0.0],
        mid_location_ids=["ic_edu_5"], depart_home_s=[30000.0], arrive_mid_s=[32000.0],
        depart_mid_s=[55000.0], arrive_home_s=[57000.0], modes=["pt"],
        crs="EPSG:25832", middle_purpose="education")
    assert list(out["trips"]["following_purpose"]) == ["education", "home"]
    assert list(out["trips"]["mode"]) == ["pt", "pt"]
    assert list(out["activities"]["purpose"]) == ["home", "education", "home"]
    assert (out["locations"]["location_id"] == "ic_edu_5").sum() == 1  # the middle row
