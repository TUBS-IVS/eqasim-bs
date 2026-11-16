"""Insert the MATSim ``vrbFare`` module into a prepared config without touching anything else.

The Java side (``EqasimConfigurator.updateConfig``, called by ``BraunschweigConfigurator``) turns the
generic module into the typed ``VrbFareConfigGroup`` at load time (ADR-0133). The text is edited in place so the XML declaration
and the MATSim DOCTYPE survive (ElementTree would drop the DOCTYPE on write). An identical existing
module is left as it is; a conflicting one is refused rather than overwritten.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

MODULE_NAME = "vrbFare"


def fare_inputs_report_name(output_prefix: str) -> str:
    """Name of the preparation report; it lists the fare input files (see ``fare_input_file_names``)."""
    return f"{output_prefix}vrb_fare_inputs_report.json"


def fare_input_file_names(output_prefix: str, snapshot_date: str) -> list[str]:
    """Fare model, line scopes and report written next to ``<prefix>config.xml``, in that order.

    The vrbFare module refers to the first two relative to the config file, and ``matsim.output`` exports
    all three under these names. The output prefix keeps two scenarios in one output path apart, and the
    fare model name carries the tariff snapshot it was built for.
    """
    return [f"{output_prefix}vrb_fare_model_{snapshot_date}.json", f"{output_prefix}vrb_line_scopes.csv",
            fare_inputs_report_name(output_prefix)]


def read_vrb_fare_module(config_path) -> dict[str, str] | None:
    """Parameters of the vrbFare module, or None when the config has no such module."""
    root = ET.parse(str(config_path)).getroot()
    if root.tag != "config":
        raise ValueError(f"{config_path}: expected a MATSim config root element, found <{root.tag}>")
    for module in root.findall("module"):
        if module.get("name") == MODULE_NAME:
            return {param.get("name"): param.get("value") for param in module.findall("param")}
    return None


def write_vrb_fare_module(config_path, params: Mapping[str, object]) -> Path:
    """Insert the vrbFare module with ``params`` before ``</config>``; return the path.

    Each value is written as ``str(value)``, so an int or a float is accepted. An existing vrbFare module is
    identical when its parameter texts equal the texts this call writes (nothing is written) and conflicting
    otherwise (``ValueError``). The caller's values are not compared themselves: the module read back holds
    text, so a re-run with an int or a float would refuse the module it had written. Side effect: rewrites
    ``config_path`` when it has no vrbFare module.
    """
    path = Path(config_path)
    param_texts = {name: str(value) for name, value in params.items()}
    existing = read_vrb_fare_module(path)
    if existing is not None:
        if existing == param_texts:
            return path
        raise ValueError(f"{path}: conflicting {MODULE_NAME} configuration; refusing to overwrite "
                         f"{existing} with {param_texts}")
    lines = [f'\t<module name="{MODULE_NAME}">']
    for name, value in param_texts.items():
        lines.append(f"\t\t<param name={quoteattr(name)} value={quoteattr(value)} />")
    lines.append("\t</module>")
    text = path.read_text(encoding="utf-8")
    marker = text.rfind("</config>")
    if marker < 0:
        raise ValueError(f"{path}: closing </config> tag not found")
    path.write_text(text[:marker] + "\n".join(lines) + "\n" + text[marker:], encoding="utf-8")
    return path
