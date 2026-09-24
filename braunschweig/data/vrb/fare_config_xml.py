"""Insert the MATSim ``vrbFare`` module into a prepared config without touching anything else.

The Java side (``BraunschweigConfigurator.updateConfig``) promotes the generic module to the typed
``VrbFareConfigGroup`` at load time (ADR-0133). The text is edited in place so the XML declaration
and the MATSim DOCTYPE survive (ElementTree would drop the DOCTYPE on write). An identical existing
module is left as it is; a conflicting one is refused rather than overwritten.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

MODULE_NAME = "vrbFare"


def read_vrb_fare_module(config_path) -> dict[str, str] | None:
    """Parameters of the vrbFare module, or None when the config has no such module."""
    root = ET.parse(str(config_path)).getroot()
    if root.tag != "config":
        raise ValueError(f"{config_path}: expected a MATSim config root element, found <{root.tag}>")
    for module in root.findall("module"):
        if module.get("name") == MODULE_NAME:
            return {param.get("name"): param.get("value") for param in module.findall("param")}
    return None


def write_vrb_fare_module(config_path, params: Mapping[str, str]) -> Path:
    path = Path(config_path)
    existing = read_vrb_fare_module(path)
    if existing is not None:
        if existing == dict(params):
            return path
        raise ValueError(f"{path}: conflicting {MODULE_NAME} configuration; refusing to overwrite "
                         f"{existing} with {dict(params)}")
    lines = [f'\t<module name="{MODULE_NAME}">']
    for name, value in params.items():
        lines.append(f"\t\t<param name={quoteattr(name)} value={quoteattr(str(value))} />")
    lines.append("\t</module>")
    text = path.read_text(encoding="utf-8")
    marker = text.rfind("</config>")
    if marker < 0:
        raise ValueError(f"{path}: closing </config> tag not found")
    path.write_text(text[:marker] + "\n".join(lines) + "\n" + text[marker:], encoding="utf-8")
    return path
