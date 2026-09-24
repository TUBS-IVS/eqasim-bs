import pytest

from braunschweig.data.vrb import fare_config_xml as fcx

CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
          '<config>\n\t<module name="global">\n\t\t<param name="randomSeed" value="1234" />\n\t</module>\n</config>\n')
PARAMS = {"enabled": "true", "fareModelPath": "vrb_fare_model_2026.json", "lineScopesPath": "vrb_line_scopes.csv",
          "dayTicketCapEnabled": "true", "unsupportedFallbackPriceCents": "370", "maximumUnsupportedShare": "0.05"}


def test_module_is_appended_and_doctype_is_preserved(tmp_path):
    path = tmp_path / "bs_config.xml"
    path.write_text(CONFIG, encoding="utf-8")
    fcx.write_vrb_fare_module(path, PARAMS)
    text = path.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE config SYSTEM')
    assert '<module name="vrbFare">' in text
    assert '<param name="fareModelPath" value="vrb_fare_model_2026.json" />' in text
    assert fcx.read_vrb_fare_module(path) == PARAMS


def test_identical_rerun_is_a_no_op_and_conflict_is_refused(tmp_path):
    path = tmp_path / "bs_config.xml"
    path.write_text(CONFIG, encoding="utf-8")
    fcx.write_vrb_fare_module(path, PARAMS)
    before = path.read_bytes()
    fcx.write_vrb_fare_module(path, PARAMS)
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="conflicting vrbFare configuration"):
        fcx.write_vrb_fare_module(path, dict(PARAMS, maximumUnsupportedShare="0.10"))


def test_missing_config_root_is_rejected(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("<notconfig/>", encoding="utf-8")
    with pytest.raises(ValueError, match="MATSim config root"):
        fcx.write_vrb_fare_module(path, PARAMS)
