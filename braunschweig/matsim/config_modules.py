"""Insert a named parameter module into a prepared MATSim config without touching anything else.

Generic form of ``braunschweig.data.vrb.fare_config_xml`` (whose vrbFare functions stay as they are),
with the module name as a parameter: the Java side turns a generic ``<module name="...">`` into its typed
config group when the config is loaded. The file is edited as text so the XML declaration and the MATSim
DOCTYPE survive (ElementTree would drop the DOCTYPE on write); the module is inserted before the closing
``</config>`` tag and every other byte is kept, including the file's line endings (a CRLF file gets CRLF
lines). An identical existing module is left as it is; a conflicting one is refused rather than overwritten.

Only flat modules (``<param>`` children) are supported: a module with parameter sets or repeated parameter
names cannot be compared parameter by parameter and is rejected.

``set_param`` changes the value of one existing parameter of a module with the same byte-preserving text edit
(used for ``qsim.insertingWaitingVehiclesBeforeDrivingVehicles``, ADR-0169).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping
from xml.sax.saxutils import escape, quoteattr, unescape

CLOSING_CONFIG_TAG = "</config>"


def _check_module_name(name) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"module name must be a non-empty text, got {name!r}")
    return name


def _check_params(params: Mapping) -> dict[str, str]:
    """The parameters as a text-to-text dict; MATSim config values are text, so the caller chooses the form."""
    checked = {}
    for name, value in params.items():
        if not isinstance(name, str) or not name:
            raise TypeError(f"parameter names must be non-empty text, got {name!r}")
        if not isinstance(value, str):
            raise TypeError(f"parameter {name!r} must be given as text (e.g. '0.05', 'true'), got {value!r}")
        checked[name] = value
    return checked


def read_module(config_path, name: str) -> dict[str, str] | None:
    """Parameters of the module ``name`` of a MATSim config, or None when the config has no such module.

    Raises ``xml.etree.ElementTree.ParseError`` when the file is not well-formed XML (for example a truncated
    config); note that ``ParseError`` derives from ``SyntaxError``, not from ``ValueError``, so a caller that
    catches ``ValueError`` does not catch it. Raises ``ValueError`` when the file is not a MATSim config (root
    element other than ``<config>``) or the module is not flat (parameter sets, repeated parameter names).
    Reads the file; no side effects.
    """
    _check_module_name(name)
    root = ET.parse(str(config_path)).getroot()
    if root.tag != "config":
        raise ValueError(f"{config_path}: expected a MATSim config root element, found <{root.tag}>")
    for module in root.findall("module"):
        if module.get("name") != name:
            continue
        params: dict[str, str] = {}
        for child in module:
            if child.tag != "param":
                raise ValueError(f"{config_path}: module {name!r} has parameter sets (<{child.tag}>); only flat "
                                 "modules can be compared")
            param_name = child.get("name")
            if param_name in params:
                raise ValueError(f"{config_path}: module {name!r} has a duplicate parameter {param_name!r}")
            params[param_name] = child.get("value")
        return params
    return None


def write_module(config_path, name: str, params: Mapping[str, str]) -> Path:
    """Insert the module ``name`` with ``params`` (text values) before ``</config>``; return the path.

    Side effect: rewrites ``config_path`` unless an identical module is already present (then nothing is
    written). Raises ``ValueError`` for a conflicting existing module, a missing ``</config>`` tag or a file
    that is not a MATSim config, ``xml.etree.ElementTree.ParseError`` for a file that is not well-formed XML
    (from ``read_module``; nothing is written), and ``TypeError`` for non-text parameter names or values.
    Names and values are XML-escaped.
    """
    path = Path(config_path)
    _check_module_name(name)
    checked = _check_params(params)
    existing = read_module(path, name)
    if existing is not None:
        if existing == checked:
            return path
        raise ValueError(f"{path}: conflicting {name} configuration; refusing to overwrite {existing} with {checked}")
    text = path.read_bytes().decode("utf-8")
    marker = text.rfind(CLOSING_CONFIG_TAG)
    if marker < 0:
        raise ValueError(f"{path}: closing {CLOSING_CONFIG_TAG} tag not found")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = [f"\t<module name={quoteattr(name)}>"]
    lines.extend(f"\t\t<param name={quoteattr(param)} value={quoteattr(value)} />" for param, value in checked.items())
    lines.append("\t</module>")
    path.write_bytes((text[:marker] + newline.join(lines) + newline + text[marker:]).encode("utf-8"))
    return path


def set_param(config_path, module: str, name: str, value: str) -> str:
    """Set the existing parameter ``name`` of module ``module`` to ``value`` (text); return the old value.

    Edits only the ``value`` attribute of that one ``<param>`` element as text, so the XML declaration, the
    DOCTYPE, comments, line endings and every other byte are kept. A parameter of the same name in another
    module is not touched. Side effect: rewrites ``config_path``. Raises ``ValueError`` when the module or the
    parameter is absent or appears more than once (nothing is written: a missing parameter means the config
    was not generated the way the caller expects, and adding one silently would hide that), ``TypeError``
    for a non-text value, and ``xml.etree.ElementTree.ParseError`` for a file that is not well-formed XML.
    """
    _check_module_name(module)
    _check_params({name: value})
    path = Path(config_path)
    ET.parse(str(path))  # fail early on a malformed file, before any text edit
    text = path.read_bytes().decode("utf-8")
    modules = list(re.finditer(r"<module\s+name=\"%s\"\s*>(.*?)</module>" % re.escape(module), text, re.DOTALL))
    if not modules:
        raise ValueError(f"{path}: no module {module!r}")
    if len(modules) > 1:
        raise ValueError(f"{path}: module {module!r} appears {len(modules)} times")
    body_start, body_end = modules[0].span(1)
    params = list(re.finditer(r"<param\s+name=\"%s\"\s+value=\"([^\"]*)\"" % re.escape(name),
                              text[body_start:body_end]))
    if not params:
        raise ValueError(f"{path}: no parameter {name!r} in module {module!r}")
    if len(params) > 1:
        raise ValueError(f"{path}: parameter {name!r} appears {len(params)} times in module {module!r}")
    value_start, value_end = (body_start + offset for offset in params[0].span(1))
    old_value = unescape(text[value_start:value_end], {"&quot;": '"'})
    path.write_bytes((text[:value_start] + escape(value, {'"': "&quot;"}) + text[value_end:]).encode("utf-8"))
    return old_value
