"""build_controls_df must append the 5 economic_status x Kreis controls when asked.

Pure catalog-rendering check (no pipeline run): confirms the stage-level switch
threads through to control_spec.full_catalog(include_status_kreis=...), and that the
status controls are KREIS-geography and absent when the switch is off (OFF byte-identical
to the controls.csv the stage renders today).
"""
from braunschweig.popsim.stage import build_controls_df
from braunschweig.popsim.status_kreis_control import STATUS_CONTROL_COLUMNS


def test_build_controls_df_appends_status_controls_only_when_on():
    off = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0",))
    on = build_controls_df(controls_source="catalog", seed="mid", tiers=("tier0",), status_kreis=True)
    off_fields = set(off["control_field"])
    on_fields = set(on["control_field"])
    # render_catalog_csv writes control_field = f"{name}_{geography}" -> economic_status_{c}_KREIS.
    expected = {f"{c}_KREIS" for c in STATUS_CONTROL_COLUMNS}
    assert not (expected & off_fields)   # OFF: unchanged
    assert expected <= on_fields          # ON: 5 status controls present
    status_rows = on[on["control_field"].isin(expected)]
    assert (status_rows["geography"] == "KREIS").all()
    assert (status_rows["seed_table"] == "households").all()


def test_build_controls_df_status_kreis_rejected_for_csv_source(tmp_path):
    # The hand-edited CSV source cannot express the status control -> fail-fast (no silent drop).
    csv = tmp_path / "controls.csv"
    csv.write_text("target;geography;seed_table;importance;control_field;expression\n")
    import pytest
    with pytest.raises(ValueError):
        build_controls_df(controls_source="csv", controls_path=str(csv), seed="mid", status_kreis=True)
