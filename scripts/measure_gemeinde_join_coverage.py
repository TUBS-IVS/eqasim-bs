"""Measure the Gemeinde-tilt join coverage of the KBA reference tables.

Reproduces the evidence table of ADR-0083: how many of the population's BBSR
RegioStaR ``name_20`` Gemeinde labels find a key in the KBA FZ 27.17 Gemeinde
table, for the 8 ZGB Kreise, under the CURRENT canonical normalisation
(:func:`braunschweig.synthesis.vehicles.fleet_sampling_de.normalize_gemeinde_name`
plus :func:`~braunschweig.synthesis.vehicles.fleet_sampling_de.apply_gebietsstand_crosswalk`)
and under the two historical predecessor rules the merge of issue #277 had to
choose between.

Reported per variant:

* matched labels of ALL RegioStaR entries;
* matched labels of POPULATED Gemeinden -- gemeindefreie Gebiete (unpopulated
  forest / military training areas) appear in no KBA Gemeinde table, so counting
  them as misses understates coverage and counting them as hits would mean a
  false match;
* key collisions on both sides (two distinct source names collapsing onto one
  normalised key), because an over-aggressive rule buys coverage with wrong
  assignments.

Usage (from the repository root):

    python scripts/measure_gemeinde_join_coverage.py
    python scripts/measure_gemeinde_join_coverage.py --fz-table PATH --regiostar PATH

Inputs are the committed derived table
``eqasim-data/data/braunschweig/kba/derived/kba_gemeinde_private_bev.csv`` and the
BBSR RegioStaR reference workbook
``eqasim-data/data/regiostar/regiostar_referenzdatei.xlsx`` (local-only, not
committed -- the script exits with a clear message when it is absent).

This is a diagnostic, not a pipeline stage: it reads reference data only and
writes nothing.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from braunschweig.synthesis.vehicles.fleet_sampling_de import (  # noqa: E402
    apply_gebietsstand_crosswalk,
    normalize_gemeinde_name,
)

logger = logging.getLogger("measure_gemeinde_join_coverage")

DEFAULT_FZ_TABLE = (REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "kba"
                    / "derived" / "kba_gemeinde_private_bev.csv")
DEFAULT_REGIOSTAR = (REPO_ROOT / "eqasim-data" / "data" / "regiostar"
                     / "regiostar_referenzdatei.xlsx")
REGIOSTAR_SHEET = "ReferenzGebietsstand2020"

#: The 8 Kreise of the Zweckverband Grossraum Braunschweig (AGS-5).
ZGB_KREISE = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")

#: Marker of a gemeindefreies Gebiet in the RegioStaR ``name_20`` vocabulary.
GEMEINDEFREI_MARKER = "GEMFR"


def _predecessor_main(name) -> str:
    """The rule that was on ``main`` before issue #277 merged (commit fbb86da8).

    Fixed municipal-status suffix token list; kept here ONLY to reproduce the
    ADR-0083 comparison table.
    """
    if name is None or pd.isna(name):
        return ""
    text = str(name).strip().upper()
    text = text.replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")
    text = text.replace(".", "")
    text = re.sub(r",\s*(?:STADT|ST|FLECKEN)\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _predecessor_branch(name) -> str:
    """The rule that was on ``feature/fleet-quality-and-data`` before the merge.

    Generic comma-drop plus parenthetical-drop, WITHOUT the gemeindefrei
    exclusion -- which is where its three false matches come from. Kept here
    ONLY to reproduce the ADR-0083 comparison table.
    """
    text = str(name).upper().strip()
    for a, b in (("Ü", "UE"), ("Ö", "OE"), ("Ä", "AE"), ("ß", "SS")):
        text = text.replace(a, b)
    text = re.sub(r",.*$", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    return " ".join(text.split())


def _canonical(kreis_ags5: str, name) -> str:
    """The rule in force: normalisation + Gebietsstand crosswalk (ADR-0083)."""
    return apply_gebietsstand_crosswalk(kreis_ags5, normalize_gemeinde_name(name))


def load_reference_keys(fz_table: Path, normalise) -> set[tuple[str, str]]:
    """Build the ``(kreis_ags5, normalised name)`` key set of the KBA table."""
    df = pd.read_csv(fz_table, dtype={"kreis_ags5": str})
    missing = {"kreis_ags5", "gemeinde"} - set(df.columns)
    if missing:
        raise SystemExit(
            f"{fz_table} lacks the required column(s) {sorted(missing)}; "
            "expected the derived FZ 27.17 Gemeinde table")
    return {(str(k), normalise(g)) for k, g in zip(df["kreis_ags5"], df["gemeinde"])}


def load_population_labels(regiostar: Path) -> pd.DataFrame:
    """Return the ZGB ``(kreis_ags5, gemeinde)`` labels of the population side.

    Mirrors the pipeline: ``braunschweig.synthesis.vehicles.cars.household``
    upper-cases the RegioStaR ``name_20`` label before the tilt lookup.
    """
    raw = pd.read_excel(regiostar, sheet_name=REGIOSTAR_SHEET)
    for column in ("gem_20", "name_20"):
        if column not in raw.columns:
            raise SystemExit(
                f"{regiostar} sheet {REGIOSTAR_SHEET!r} lacks column {column!r}")
    out = pd.DataFrame({
        "ags8": raw["gem_20"].astype(str).str.zfill(8),
        "gemeinde": raw["name_20"].astype(str).str.strip().str.upper(),
    })
    out["kreis_ags5"] = out["ags8"].str[:5]
    return out[out["kreis_ags5"].isin(ZGB_KREISE)].reset_index(drop=True)


def report(label: str, keys: set[tuple[str, str]], population: pd.DataFrame,
           key_of, collision_key_of=None) -> None:
    """Log matched/populated/collision counts for one normalisation variant.

    ``collision_key_of`` isolates ACCIDENTAL collisions from intentional ones:
    the Gebietsstand crosswalk deliberately maps several predecessor labels onto
    one successor Gemeinde, which must not be reported as a false match. Pass the
    pre-crosswalk key function there; it defaults to ``key_of``.
    """
    collision_key_of = collision_key_of or key_of
    pairs = list(zip(population["kreis_ags5"], population["gemeinde"]))
    populated = [(k, g) for k, g in pairs
                 if GEMEINDEFREI_MARKER not in str(g).upper()]
    hits = [(k, g) for k, g in pairs if (k, key_of(k, g)) in keys]
    pop_hits = [(k, g) for k, g in populated if (k, key_of(k, g)) in keys]
    crosswalked = [(k, g) for k, g in pairs
                   if key_of(k, g) != collision_key_of(k, g)]

    buckets: dict[tuple[str, str], set[str]] = {}
    for kreis, name in pairs:
        buckets.setdefault((kreis, collision_key_of(kreis, name)), set()).add(str(name))
    collisions = {k: v for k, v in buckets.items() if len(v) > 1 and k[1] != ""}

    logger.info(
        "%-34s all %3d/%3d (%5.1f%%) | populated %3d/%3d (%5.1f%%) | "
        "false-match keys %d | crosswalked %d",
        label, len(hits), len(pairs), 100.0 * len(hits) / max(len(pairs), 1),
        len(pop_hits), len(populated),
        100.0 * len(pop_hits) / max(len(populated), 1), len(collisions),
        len(crosswalked),
    )
    for kreis, name in crosswalked:
        logger.info("  CROSSWALK %s %r -> %r (municipal merger, ADR-0083)",
                    kreis, name, key_of(kreis, name))
    for key, sources in sorted(collisions.items()):
        logger.warning("  FALSE MATCH %s <- %s", key, sorted(sources))
    for kreis, name in populated:
        if (kreis, key_of(kreis, name)) not in keys:
            logger.info("  MISS (populated) %s %r -> %r",
                        kreis, name, key_of(kreis, name))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fz-table", type=Path, default=DEFAULT_FZ_TABLE,
                        help="derived FZ 27.17 Gemeinde table (CSV)")
    parser.add_argument("--regiostar", type=Path, default=DEFAULT_REGIOSTAR,
                        help="BBSR RegioStaR reference workbook (XLSX)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    for path, what in ((args.fz_table, "FZ 27.17 Gemeinde table"),
                       (args.regiostar, "RegioStaR reference workbook")):
        if not path.exists():
            raise SystemExit(f"missing {what}: {path}")

    population = load_population_labels(args.regiostar)
    logger.info("ZGB RegioStaR labels: %d (Gebietsstand 2020)", len(population))

    variants = (
        ("main before #277 (suffix tokens)", _predecessor_main,
         lambda k, g: _predecessor_main(g), None),
        ("branch before #277 (comma-drop)", _predecessor_branch,
         lambda k, g: _predecessor_branch(g), None),
        ("in force (ADR-0083, + crosswalk)", normalize_gemeinde_name, _canonical,
         lambda k, g: normalize_gemeinde_name(g)),
    )
    for label, reference_normalise, key_of, collision_key_of in variants:
        keys = load_reference_keys(args.fz_table, reference_normalise)
        report(label, keys, population, key_of, collision_key_of)
    return 0


if __name__ == "__main__":
    sys.exit(main())
