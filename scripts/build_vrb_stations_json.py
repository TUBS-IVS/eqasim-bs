"""Build ``vrb/stations.json`` for the VRB tariff-zone stage.

Mirrors the MVG REST export schema so that ``braunschweig.data.vrb.zones``
can reuse the MVG algorithm bit-for-bit. Each station emitted has the
fields ``name``, ``place``, ``id``, ``tariffZones``, ``products``,
``latitude`` and ``longitude``. The single field actually consumed
downstream is ``tariffZones`` (split on ``|`` and ``/``); the rest is
kept for parity with the MVG dump and for human inspection.

Two input modes are supported:

1. ``--vrb-html PATH``  (preferred; default since the VRB Waben polygon
   shapefile is not publicly available).  Parses a saved copy of
   https://www.vrb-online.de/de/tickets/tarifzonen-preisstufen and uses
   the place-name dropdown options to build a ``place -> tariff zone``
   mapping.  Each GTFS stop is then matched against this mapping by
   normalised place name, optionally disambiguated by an in-name city
   hint (e.g. ``Alvesse (Edemissen)`` vs ``Alvesse (Vechelde)``).

2. ``--waben FILE``  (legacy).  Spatial join of GTFS stops against a
   VRB Waben polygon layer.  Kept for the day VRB releases an authorita-
   tive shapefile.

In both modes the GTFS feed (zip) supplies stop coordinates and names.
The default path matches ``configs/fixtures/config_local_braunschweig.yml``:
``data_path/gtfs/latest.zip``. Stops are filtered to the ZGB-8 bounding
box (lon/lat WGS84).

Output
------
``vrb/stations.json`` under ``data_path``: a JSON array with one record
per stop, where ``tariffZones`` is the resolved zone ID (or ``""`` if
the stop could not be matched -- such stops are still included so the
JSON stays a one-to-one image of GTFS).

Usage
-----
HTML scrape mode (recommended)::

    Invoke-WebRequest "https://www.vrb-online.de/de/tickets/tarifzonen-preisstufen" `
        -OutFile eqasim-data/data/vrb/tarifzonen.html
    python scripts/build_vrb_stations_json.py `
        --vrb-html eqasim-data/data/vrb/tarifzonen.html `
        --gtfs eqasim-data/data/gtfs/latest.zip `
        --out eqasim-data/data/vrb/stations.json

Waben polygon mode::

    python scripts/build_vrb_stations_json.py `
        --waben path/to/vrb_waben.gpkg --waben-zone-column WABE `
        --gtfs eqasim-data/data/gtfs/latest.zip `
        --out eqasim-data/data/vrb/stations.json
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import pandas as pd


# ZGB-8 rough bounding box in WGS84 (mirrors DOWNLOAD_CHECKLIST_BS.md).
ZGB_BBOX_WGS84 = (9.6, 51.4, 11.4, 52.7)  # minlon, minlat, maxlon, maxlat


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def load_gtfs_stops(gtfs_zip: Path) -> pd.DataFrame:
    with zipfile.ZipFile(gtfs_zip) as zf:
        with zf.open("stops.txt") as fp:
            df = pd.read_csv(io.TextIOWrapper(fp, encoding="utf-8"))

    required = {"stop_id", "stop_name", "stop_lat", "stop_lon"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError("GTFS stops.txt is missing columns: {}".format(missing))

    minlon, minlat, maxlon, maxlat = ZGB_BBOX_WGS84
    mask = (
        (df["stop_lon"] >= minlon) & (df["stop_lon"] <= maxlon)
        & (df["stop_lat"] >= minlat) & (df["stop_lat"] <= maxlat)
    )
    return df.loc[mask].reset_index(drop=True)


def _record(row: pd.Series, zone: str) -> dict:
    return {
        "name": row["stop_name"],
        "place": row["stop_name"],
        "id": str(row["stop_id"]),
        "divaId": None,
        "abbreviation": None,
        "tariffZones": zone,
        "products": [],
        "latitude": float(row["stop_lat"]),
        "longitude": float(row["stop_lon"]),
    }


# --------------------------------------------------------------------------
# Mode 1: VRB HTML scrape
# --------------------------------------------------------------------------

# VRB option pattern: ``<kreis>-<gemeinde>-<place>-zone-<NN>``.  ``kreis``
# may be missing for special pseudo-places (e.g. ``elm-zone-34``); we
# still want to keep those because their place name may match a GTFS stop.
_OPTION_RE = re.compile(
    r'<option value="([^"]+-zone-\d+)"[^>]*>([^<]+)</option>'
)
_ZONE_SUFFIX_RE = re.compile(r"^(?P<slug>.+)-zone-(?P<zone>\d+)$")


def _normalise(text: str) -> str:
    """Normalise to the same alphabet VRB uses in slugs.

    VRB drops umlaut diacritics (``ü -> u``) instead of expanding them
    (``ü -> ue``). We follow that convention so GTFS names compare
    against slug pieces directly.  Returns a string with single-space
    word separation; callers may further strip spaces.
    """
    if text is None:
        return ""
    s = text.lower()
    repl = {
        "\u00e4": "a", "\u00f6": "o", "\u00fc": "u",  # ä ö ü
        "\u00df": "ss",                                # ß
        "\u00e9": "e", "\u00e8": "e",                  # é è
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def _key(text: str) -> str:
    """Canonical lookup key: normalised, with whitespace removed."""
    return _normalise(text).replace(" ", "")


def _slug_key(slug_part: str) -> str:
    """Canonical lookup key for slug fragments (already ascii)."""
    return re.sub(r"[^a-z0-9]+", "", slug_part.lower())


# Tokens that are routinely appended to a GTFS stop name but are not
# part of the place. Stripped before matching.
_PLACE_TAIL_RE = re.compile(
    r"\b(b\.?\s*braunschw?\.?|b\.?\s*braunsch?\.?|bahnhof|bf\.?|hbf\.?)\b",
    re.IGNORECASE,
)

# Local abbreviations used in GTFS stop names. ``SZ-Drütte`` means
# "Salzgitter-Drütte"; ``GF`` / ``WF`` flag a Gifhorn / Wolfenbüttel
# district when ambiguous.
_PREFIX_EXPANSIONS = {
    "sz": "salzgitter",
    "bs": "braunschweig",
    "wob": "wolfsburg",
    "gf": "gifhorn",
    "wf": "wolfenbuttel",
    "hi": "hildesheim",
    "gs": "goslar",
    "pe": "peine",
    "he": "helmstedt",
}


def _stop_place_candidate(stop_name: str) -> tuple[str, str | None]:
    """Extract (place, city_hint) from a GTFS stop name.

    * ``Braunschweig, Im Seumel``           -> ("Braunschweig", None)
    * ``Alvesse (Edemissen) Schule``        -> ("Alvesse", "Edemissen")
    * ``Westerhof(Kalefeld) Steinweg``      -> ("Westerhof", "Kalefeld")
    * ``Abbenrode b Braunsch, Pferdeteich`` -> ("Abbenrode", "Braunsch")
    * ``SZ-Drütte, Hoheweg``                -> ("Drütte", "Salzgitter")
    * ``Nettlingen/Helmerser Straße``       -> ("Nettlingen", None)
    * ``Leiferde GF, Kirche``               -> ("Leiferde", "Gifhorn")
    """
    name = stop_name
    # Strip trailing INSA / version annotations.
    name = re.sub(r"\s*\[[^\]]*\]\s*$", "", name).strip()

    # First segment: cut at comma OR slash, whichever comes first.
    cut = len(name)
    for sep in (",", "/"):
        i = name.find(sep)
        if i >= 0 and i < cut:
            cut = i
    head = name[:cut].strip()

    city_hint: str | None = None

    # Parenthetical disambiguator: ``Alvesse (Edemissen)``.
    m = re.search(r"\(([^)]+)\)", head)
    if m:
        city_hint = m.group(1).strip()
        head = (head[: m.start()] + head[m.end():]).strip()

    # SZ- / WOB- / BS- style prefix, e.g. ``SZ-Drütte``.
    m = re.match(r"^([A-Za-z]{2,4})-(?=\S)", head)
    if m:
        token = m.group(1).lower()
        if token in _PREFIX_EXPANSIONS:
            if city_hint is None:
                city_hint = _PREFIX_EXPANSIONS[token]
            head = head[m.end():].strip()

    # Trailing district code, e.g. ``Leiferde GF``.
    m = re.search(r"\s+([A-Za-z]{2,3})\s*$", head)
    if m:
        token = m.group(1).lower()
        if token in _PREFIX_EXPANSIONS:
            if city_hint is None:
                city_hint = _PREFIX_EXPANSIONS[token]
            head = head[: m.start()].strip()

    # "b Braunsch" disambiguator without parens.
    m = _PLACE_TAIL_RE.search(head)
    if m:
        if city_hint is None:
            city_hint = m.group(0).strip()
        head = (head[: m.start()] + head[m.end():]).strip()

    head = re.sub(r"[\s,;:./-]+$", "", head).strip()
    return head, city_hint


# The known VRB kreis (district) prefixes used in option slugs. Order
# matters: longer prefixes are tried first.
_KREIS_PREFIXES = (
    "region-hannover",
    "braunschweig",
    "gifhorn",
    "goslar",
    "helmstedt",
    "peine",
    "salzgitter",
    "wolfenbuttel",
    "wolfsburg",
    "elm",  # single-token pseudo entry (``elm-zone-34``)
)


def _split_kreis(slug: str) -> tuple[str, str]:
    """Return (kreis, rest) for a VRB slug. ``rest`` may be empty."""
    for kreis in _KREIS_PREFIXES:
        if slug == kreis:
            return kreis, ""
        if slug.startswith(kreis + "-"):
            return kreis, slug[len(kreis) + 1:]
    # Fallback: take the first dash-separated token.
    head, _, rest = slug.partition("-")
    return head, rest


_HTML_ENTITY_RE = re.compile(r"&(#x?[0-9a-fA-F]+|[a-zA-Z]+);")


def _html_unescape(text: str) -> str:
    import html
    return html.unescape(text)


def parse_vrb_html(path: Path) -> list[dict]:
    """Return a list of records ``{slug, name, place_name, gemeinde, kreis, zone}``.

    ``place_name`` is the human-readable place from the option text with
    any parenthetical ``(City)`` stripped; ``gemeinde`` carries that
    parenthetical (or the slug-derived gemeinde, when no parens).
    """
    text = path.read_text(encoding="utf-8")
    seen: dict[str, str] = {}
    for slug_zone, name in _OPTION_RE.findall(text):
        # Same slug appears twice (start + destination dropdown); keep first.
        seen.setdefault(slug_zone, _html_unescape(name).strip())

    records: list[dict] = []
    for slug_zone, name in seen.items():
        m = _ZONE_SUFFIX_RE.match(slug_zone)
        if not m:
            continue
        slug = m.group("slug")
        zone = m.group("zone")

        kreis, rest = _split_kreis(slug)
        # ``rest`` is "<gemeinde>-<place>" but both parts may contain
        # dashes themselves. Without a list of all gemeinden the only
        # reliable place key is the display name; we still record the
        # raw slug tail for substring fallbacks.
        slug_tail = rest

        # Display name disambiguator, e.g. ``Beienrode (Königslutter am Elm)``.
        gemeinde_hint = ""
        place_name = name
        pm = re.search(r"\(([^)]+)\)\s*$", place_name)
        if pm:
            gemeinde_hint = pm.group(1).strip()
            place_name = place_name[: pm.start()].strip()

        records.append({
            "slug": slug,
            "name": name,
            "place_name": place_name,
            "slug_tail": slug_tail,
            "gemeinde": gemeinde_hint,
            "kreis": kreis,
            "zone": zone,
        })
    return records


def _build_lookup(vrb_records: list[dict]) -> tuple[dict, dict]:
    """Build (place -> [records], kreis -> {zone}) indices.

    Each VRB record contributes the full normalised display name and,
    when the display is multi-word (e.g. ``"Königslutter am Elm"``,
    ``"Hämelerwald Bahnhof"``), an additional key on its first word so
    that GTFS stops carrying only the leading place name still match.
    """
    by_place: dict[str, list[dict]] = defaultdict(list)
    by_kreis: dict[str, set[str]] = defaultdict(set)
    for r in vrb_records:
        full_key = _key(r["place_name"])
        if not full_key:
            continue
        by_place[full_key].append(r)
        # First-word alias.
        first_word = r["place_name"].split()[0] if r["place_name"] else ""
        first_key = _key(first_word)
        if first_key and first_key != full_key:
            by_place[first_key].append(r)
        if r["kreis"]:
            by_kreis[_slug_key(r["kreis"])].add(r["zone"])
    return by_place, by_kreis


def resolve_zone(
    stop_name: str,
    by_place: dict[str, list[dict]],
    by_kreis: dict[str, set[str]],
) -> str:
    place, city_hint = _stop_place_candidate(stop_name)
    place_key = _key(place)
    if not place_key:
        return ""

    # Try the place key, then a "<hint><place>" combo (handles
    # "SZ-Bad" -> "salzgitterbad"), then "<place><hint>", then the
    # first word of the place (handles "Hämelerwald Wohnpark" with no
    # comma to delimit the place from the stop).
    first_word = place.split()[0] if place else ""
    first_word_key = _key(first_word)

    candidates: list[dict] = []
    tried: set[str] = set()
    for k in (
        place_key,
        (_key(city_hint) + place_key) if city_hint else "",
        (place_key + _key(city_hint)) if city_hint else "",
        first_word_key if first_word_key != place_key else "",
    ):
        if k and k not in tried:
            tried.add(k)
            if by_place.get(k):
                candidates = by_place[k]
                break

    if not candidates:
        # Kreis fallback: stop "Salzgitter, Bahnhof" -> kreis salzgitter
        # has only one zone, accept it.
        zones = by_kreis.get(place_key)
        if zones and len(zones) == 1:
            return next(iter(zones))
        return ""

    if len(candidates) == 1:
        return candidates[0]["zone"]

    # Multiple zones share the place name. Disambiguate via city hint.
    if city_hint:
        hint_key = _key(city_hint)
        for c in candidates:
            if _key(c["gemeinde"]) == hint_key:
                return c["zone"]
            if _slug_key(c["kreis"]) == hint_key:
                return c["zone"]

    # Accept if all candidates collapse to the same zone.
    zones = {c["zone"] for c in candidates}
    if len(zones) == 1:
        return next(iter(zones))

    return ""


def build_stations_from_html(stops: pd.DataFrame, vrb_html: Path) -> list[dict]:
    vrb_records = parse_vrb_html(vrb_html)
    print("[INFO] Parsed {} VRB place entries".format(len(vrb_records)))
    by_place, by_kreis = _build_lookup(vrb_records)

    records: list[dict] = []
    matched = 0
    for _, row in stops.iterrows():
        zone = resolve_zone(row["stop_name"], by_place, by_kreis)
        if zone:
            matched += 1
        records.append(_record(row, zone))
    print("[INFO] Resolved {}/{} GTFS stops to a tariff zone".format(matched, len(records)))
    return records


# --------------------------------------------------------------------------
# Mode 2: Waben polygon spatial join (kept for the day VRB publishes one)
# --------------------------------------------------------------------------

def build_stations_from_waben(
    stops: pd.DataFrame, waben_path: Path, zone_column: str
) -> list[dict]:
    import geopandas as gpd  # local import keeps mode 1 free of geo deps
    import shapely.geometry as sgeo

    gdf = gpd.read_file(waben_path)
    if zone_column not in gdf.columns:
        raise RuntimeError(
            "Waben file {} has no column '{}'. Columns: {}".format(
                waben_path, zone_column, list(gdf.columns)
            )
        )
    if gdf.crs is None:
        raise RuntimeError("Waben file {} has no CRS set".format(waben_path))
    waben = gdf[[zone_column, "geometry"]].rename(columns={zone_column: "_zone"})

    gdf_stops = gpd.GeoDataFrame(
        stops.copy(),
        geometry=[sgeo.Point(lon, lat) for lon, lat in zip(stops["stop_lon"], stops["stop_lat"])],
        crs="EPSG:4326",
    ).to_crs(waben.crs)

    joined = gpd.sjoin(gdf_stops, waben, how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]

    records: list[dict] = []
    for _, row in joined.iterrows():
        zone = row.get("_zone")
        zone = "" if pd.isna(zone) else str(zone)
        records.append(_record(row, zone))
    return records


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gtfs", type=Path, required=True,
        help="Path to GTFS zip (e.g. eqasim-data/data/gtfs/latest.zip).",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--vrb-html", type=Path,
        help="Path to a saved copy of the VRB tariff zones page (HTML).",
    )
    src.add_argument(
        "--waben", type=Path,
        help="Path to VRB Waben polygon file (GeoPandas-readable).",
    )
    parser.add_argument(
        "--waben-zone-column", default="WABE",
        help="Attribute on the Waben file carrying the zone ID (default: WABE).",
    )
    parser.add_argument(
        "--out", type=Path, required=True,
        help="Output JSON path (e.g. eqasim-data/data/vrb/stations.json).",
    )
    args = parser.parse_args(argv)

    stops = load_gtfs_stops(args.gtfs)
    print("[INFO] Loaded {} GTFS stops in ZGB bbox".format(len(stops)))

    if args.vrb_html is not None:
        records = build_stations_from_html(stops, args.vrb_html)
    else:
        records = build_stations_from_waben(stops, args.waben, args.waben_zone_column)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fp:
        json.dump(records, fp, ensure_ascii=False, indent=2)

    print("[OK] Wrote {}".format(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
