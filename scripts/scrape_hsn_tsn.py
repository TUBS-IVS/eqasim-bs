"""Scrape the HSN/TSN -> technical-data lookup from hsn-tsn.de.

For each manufacturer (Marke) listed on http://www.hsn-tsn.de/ this fetches the
brand page (e.g. http://www.hsn-tsn.de/alfa-romeo.html), which contains ONE HTML
table with all HSN/TSN combinations of that brand and their technical data:

    HSN/TSN | Marke und Fahrzeugtyp | Leistung | Hubraum | Kraftstoff
    1742/AAA | Alfa Romeo Mito 1.4 LPG | 120 PS (88 kW) | 1368 ccm | Benzin/Autogas

All data is on the brand page directly (no per-TSN drill-down), so the whole scrape
is ONE request per brand (~37 requests total). The script is polite (descriptive
User-Agent, a delay between requests) and CACHES each raw HTML page, so re-runs do
not re-hit the site and the scrape is resumable.

Output: a single tidy lookup CSV
    eqasim-data/data/braunschweig/kba/hsn_tsn_lookup.csv
with columns
    brand, hsn, tsn, model, power_ps, power_kw, displacement_ccm, fuel

Provenance: hsn-tsn.de (public HSN/TSN directory). The site is HTTP-only with an
invalid TLS certificate for HTTPS, so requests use plain HTTP. This lookup is a
parked data source: combined with KBA FZ 6 (Bestand nach Hersteller-/Typschluessel)
it allows mapping the registered fleet to engine power / displacement / fuel. It is
NOT yet wired into the synthesis (deferred to the vehicle-power/emissions phase).

Usage:
    python scripts/scrape_hsn_tsn.py [--delay 1.5] [--refresh]

--refresh re-fetches even cached pages. Default uses the cache.
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import re
import time
import urllib.request
from pathlib import Path

# The manufacturer list from the hsn-tsn.de landing page (as provided).
BRANDS = [
    "Alfa Romeo", "Audi", "BMW", "Chrysler", "Citroen", "Dacia", "Daewoo",
    "Daihatsu", "Fiat", "Ford", "Honda", "Hyundai", "Isuzu", "Jaguar", "Kia",
    "Lada", "Lancia", "Mazda", "Mercedes-Benz", "Mitsubishi", "Nissan", "Opel",
    "Peugeot", "Porsche", "Renault", "Rover", "Saab", "Seat", "Skoda", "Smart",
    "Subaru", "Suzuki", "Talbot", "Toyota", "Volvo", "VW",
]

BASE_URL = "http://www.hsn-tsn.de/{slug}.html"
USER_AGENT = "eqasim-bs research data collection (HSN/TSN technical lookup)"

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_CSV = REPO_ROOT / "eqasim-data/data/braunschweig/kba/hsn_tsn_lookup.csv"
CACHE_DIR = REPO_ROOT / "eqasim-data/data/braunschweig/kba/.hsn_tsn_cache"

# Expected header of the data table, used to locate it among other tables.
_HEADER_TOKENS = ("HSN/TSN", "Leistung", "Hubraum", "Kraftstoff")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_POWER_RE = re.compile(r"(\d+)\s*PS\s*\(\s*(\d+)\s*kW\s*\)", re.I)
_CCM_RE = re.compile(r"(\d+)\s*ccm", re.I)
_HSN_TSN_RE = re.compile(r"^([0-9A-Z]{3,4})\s*/\s*([0-9A-Z]{2,4})$", re.I)


def slug_for(brand: str) -> str:
    """Brand display name -> URL slug (e.g. 'Mercedes-Benz' -> 'mercedes-benz')."""
    return brand.strip().lower().replace(" ", "-")


def _clean(cell_html: str) -> str:
    text = _TAG_RE.sub(" ", cell_html)
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def fetch_brand_html(brand: str, delay: float, refresh: bool) -> str | None:
    """Fetch (and cache) the raw HTML for one brand page. Returns None on failure."""
    slug = slug_for(brand)
    cache_path = CACHE_DIR / f"{slug}.html"
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8", errors="replace")

    url = BASE_URL.format(slug=slug)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception as error:  # noqa: BLE001 - report and continue, do not abort whole scrape
        print(f"  WARNING: failed to fetch {url}: {error}")
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(raw, encoding="utf-8")
    # Be polite: pause only when we actually hit the network.
    time.sleep(delay)
    return raw


def parse_brand(brand: str, raw_html: str) -> list[dict]:
    """Extract the HSN/TSN technical rows from one brand page."""
    rows: list[dict] = []
    seen_header = False
    for tr in _TR_RE.findall(raw_html):
        cells = [_clean(c) for c in _CELL_RE.findall(tr)]
        if len(cells) < 5:
            continue
        if not seen_header:
            # The data table starts at the header row carrying all expected tokens.
            if all(any(tok in c for c in cells) for tok in _HEADER_TOKENS):
                seen_header = True
            continue

        hsn_tsn, model, leistung, hubraum, kraftstoff = cells[:5]
        match = _HSN_TSN_RE.match(hsn_tsn)
        if not match:
            continue  # navigation / spacer row inside the table
        hsn, tsn = match.group(1).upper(), match.group(2).upper()

        power_ps = power_kw = ""
        pmatch = _POWER_RE.search(leistung)
        if pmatch:
            power_ps, power_kw = pmatch.group(1), pmatch.group(2)
        ccm = ""
        cmatch = _CCM_RE.search(hubraum)
        if cmatch:
            ccm = cmatch.group(1)

        rows.append({
            "brand": brand,
            "hsn": hsn,
            "tsn": tsn,
            "model": model,
            "power_ps": power_ps,
            "power_kw": power_kw,
            "displacement_ccm": ccm,
            "fuel": kraftstoff,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=1.5,
                        help="seconds to wait between network requests (default 1.5)")
    parser.add_argument("--refresh", action="store_true",
                        help="re-fetch even pages already in the cache")
    args = parser.parse_args()

    all_rows: list[dict] = []
    n_ok = n_fail = 0
    for brand in BRANDS:
        raw = fetch_brand_html(brand, args.delay, args.refresh)
        if raw is None:
            n_fail += 1
            continue
        rows = parse_brand(brand, raw)
        if not rows:
            print(f"  WARNING: 0 rows parsed for {brand} "
                  f"(page structure may have changed)")
            n_fail += 1
            continue
        n_ok += 1
        all_rows.extend(rows)
        print(f"  {brand:14s} {len(rows):6d} HSN/TSN rows")

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["brand", "hsn", "tsn", "model", "power_ps", "power_kw",
                  "displacement_ccm", "fuel"]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    # Coverage / fallback-rate logging (project no-silent-fallback rule).
    n_total = len(all_rows)
    n_power = sum(1 for r in all_rows if r["power_kw"])
    n_ccm = sum(1 for r in all_rows if r["displacement_ccm"])
    print(f"\nbrands ok {n_ok}/{len(BRANDS)} (failed {n_fail})")
    print(f"rows {n_total}; power_kw present {n_power} ({n_power / max(n_total, 1):.1%}), "
          f"displacement present {n_ccm} ({n_ccm / max(n_total, 1):.1%})")
    print(f"written -> {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
