"""End-to-end validation of the Braunschweig 10 % synthetic population.

Compares synthesis outputs against MiD 2023, BA Pendleratlas 2025, Zensus 2022
and INKAR. Produces a professional HTML report (printable to PDF) plus a
JSON summary and a directory of publication-quality plots.

Run as a module: ``python -m scripts.validate_bs_10pct``.
"""
from .config import OUTPUT_DIR, DATA_DIR, PREFIX, SAMPLING_RATE, ZGB8

__all__ = ["OUTPUT_DIR", "DATA_DIR", "PREFIX", "SAMPLING_RATE", "ZGB8"]
