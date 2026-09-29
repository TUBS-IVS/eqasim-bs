"""The generic MATSim config-module writer: a text edit that keeps the DOCTYPE and every other byte."""
from __future__ import annotations

import pytest

from braunschweig.matsim import config_modules as cm

CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\n'
          '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
          '<config>\n\t<module name="global">\n\t\t<param name="randomSeed" value="1234" />\n\t</module>\n</config>\n')
MODULE_NAME = "exampleModule"
PARAMS = {"enabled": "true", "tariffModelPath": "bs_parking_tariffs_2026-09-28.json", "maximumShare": "0.05"}


def _config(tmp_path, text: str = CONFIG):
    path = tmp_path / "bs_config.xml"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_module_is_written_before_the_closing_config_tag_and_nothing_else_changes(tmp_path):
    path = _config(tmp_path)
    assert cm.write_module(path, MODULE_NAME, PARAMS) == path
    text = path.read_bytes().decode("utf-8")
    # The XML declaration, the DOCTYPE and the existing modules are kept byte for byte.
    assert text.startswith(CONFIG[:CONFIG.rindex("</config>")])
    assert '\t<module name="exampleModule">\n\t\t<param name="enabled" value="true" />\n' in text
    assert text.endswith("\t</module>\n</config>\n")
    assert cm.read_module(path, MODULE_NAME) == PARAMS
    assert cm.read_module(path, "global") == {"randomSeed": "1234"}


def test_an_identical_module_is_left_as_it_is(tmp_path):
    path = _config(tmp_path)
    cm.write_module(path, MODULE_NAME, PARAMS)
    before = path.read_bytes()
    cm.write_module(path, MODULE_NAME, dict(PARAMS))
    assert path.read_bytes() == before


def test_a_conflicting_module_is_refused(tmp_path):
    path = _config(tmp_path)
    cm.write_module(path, MODULE_NAME, PARAMS)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="conflicting exampleModule configuration"):
        cm.write_module(path, MODULE_NAME, dict(PARAMS, maximumShare="0.10"))
    assert path.read_bytes() == before


def test_a_config_without_a_closing_config_tag_is_rejected(tmp_path):
    path = _config(tmp_path, '<?xml version="1.0" encoding="utf-8"?>\n<config/>\n')
    with pytest.raises(ValueError, match="closing </config> tag not found"):
        cm.write_module(path, MODULE_NAME, PARAMS)


def test_a_file_that_is_not_a_matsim_config_is_rejected(tmp_path):
    path = _config(tmp_path, "<notconfig/>")
    with pytest.raises(ValueError, match="MATSim config root"):
        cm.write_module(path, MODULE_NAME, PARAMS)
    with pytest.raises(ValueError, match="MATSim config root"):
        cm.read_module(path, MODULE_NAME)


def test_read_module_returns_none_for_an_absent_module(tmp_path):
    assert cm.read_module(_config(tmp_path), MODULE_NAME) is None


def test_values_are_escaped_and_crlf_line_endings_are_kept(tmp_path):
    path = _config(tmp_path, CONFIG.replace("\n", "\r\n"))
    params = {"label": 'a "quoted" <value> & more'}
    cm.write_module(path, MODULE_NAME, params)
    raw = path.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b""), "a line ending other than CRLF was written"
    assert cm.read_module(path, MODULE_NAME) == params


def test_a_module_that_is_not_flat_cannot_be_compared(tmp_path):
    nested = CONFIG.replace('\t</module>\n</config>',
                            '\t</module>\n\t<module name="exampleModule">\n\t\t<parameterset type="mode" />\n'
                            '\t</module>\n</config>')
    with pytest.raises(ValueError, match="parameter sets"):
        cm.read_module(_config(tmp_path, nested), MODULE_NAME)
    duplicated = CONFIG.replace('value="1234" />', 'value="1234" />\n\t\t<param name="randomSeed" value="42" />')
    with pytest.raises(ValueError, match="duplicate parameter 'randomSeed'"):
        cm.read_module(_config(tmp_path, duplicated), "global")


def test_module_name_and_parameters_must_be_text(tmp_path):
    path = _config(tmp_path)
    with pytest.raises(TypeError, match="maximumShare"):
        cm.write_module(path, MODULE_NAME, {"maximumShare": 0.05})
    with pytest.raises(ValueError, match="module name"):
        cm.write_module(path, "", PARAMS)
    assert path.read_bytes() == CONFIG.encode("utf-8")
