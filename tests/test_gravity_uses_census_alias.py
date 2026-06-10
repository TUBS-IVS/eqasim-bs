import pathlib


def test_gravity_stages_census_filtered_not_ipf_directly():
    src = pathlib.Path("braunschweig/gravity/model.py").read_text(encoding="utf-8")
    assert 'context.stage("braunschweig.ipf.attributed")' not in src
    assert 'context.stage("data.census.filtered")' in src
