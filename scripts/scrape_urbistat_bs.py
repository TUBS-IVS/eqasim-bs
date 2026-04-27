"""Urbistat scraper for the ZGB Braunschweig region (Kreise + Gemeinden).

Fetches age-class × sex population tables from urbistat.com/AdminStat for the
8 ZGB Kreise and all their Gemeinden. Writes:

    eqasim-data/data/braunschweig/urbistat_age_raw.csv
    eqasim-data/data/braunschweig/urbistat_age_kreise.csv
    eqasim-data/data/braunschweig/urbistat_age_gemeinden.csv

Source: Urbistat S.r.l. (elaboration of DESTATIS data), year 2023.

Usage:
    python scripts/scrape_urbistat_bs.py [--delay 1.0]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import requests
from bs4 import BeautifulSoup


BASE = "https://ugeo.urbistat.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (research-scraper; eqasim-bs)"}

# 8 ZGB Kreise: (ARS-5, urbistat_id, url_slug, friendly_name)
KREISE: list[tuple[str, str, str, str]] = [
    ("03101", "3101", "braunschweig%2c-kreisfreie-stadt", "Braunschweig"),
    ("03102", "3102", "salzgitter%2c-kreisfreie-stadt",   "Salzgitter"),
    ("03103", "3103", "wolfsburg%2c-kreisfreie-stadt",    "Wolfsburg"),
    ("03151", "3151", "gifhorn%2c-landkreis",             "Gifhorn"),
    ("03153", "3153", "goslar%2c-landkreis",              "Goslar"),
    ("03154", "3154", "helmstedt%2c-landkreis",           "Helmstedt"),
    ("03157", "3157", "peine%2c-landkreis",               "Peine"),
    ("03158", "3158", "wolfenbuttel%2c-landkreis",        "Wolfenbüttel"),
]

OUT_DIR = "eqasim-data/data/braunschweig"
RAW_CSV       = os.path.join(OUT_DIR, "urbistat_age_raw.csv")
KREIS_CSV     = os.path.join(OUT_DIR, "urbistat_age_kreise.csv")
GEMEINDE_CSV  = os.path.join(OUT_DIR, "urbistat_age_gemeinden.csv")


@dataclass
class AgeRow:
    kreis_ars: str
    kreis_name: str
    level: str            # "kreis" or "gemeinde"
    urbistat_id: str
    slug: str
    name: str
    age_class: str        # e.g. "0 - 2 anni"
    sex: str              # "male" | "female" | "total"
    count: int
    percent: float


def fetch(url: str, retries: int = 3, delay: float = 1.0) -> str:
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
            if r.status_code == 200:
                return r.text
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last = e
        time.sleep(delay * (2 ** attempt))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def parse_int(s: str) -> int | None:
    s = s.strip().replace(".", "").replace(",", "").replace("\xa0", "")
    if s in {"", "-"}:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_pct(s: str) -> float | None:
    s = s.strip().replace(",", ".")
    if s in {"", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_age_page(html: str) -> list[tuple[str, int | None, float | None, int | None, float | None, int | None, float | None]]:
    """Return rows of (age_class, male_n, male_pct, female_n, female_pct, total_n, total_pct)."""
    soup = BeautifulSoup(html, "lxml")
    out: list = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        header_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        # The main age-class table has headers "", "Maschi", "Femmine", "Totale"
        # and a sub-header row "Classi", "(n.)", "%", "(n.)", "%", "(n.)", "%".
        if header_cells != ["", "Maschi", "Femmine", "Totale"]:
            continue
        subheader = [c.get_text(strip=True) for c in rows[1].find_all(["th", "td"])]
        if subheader[:1] != ["Classi"]:
            continue
        for tr in rows[2:]:
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 7:
                continue
            label = cells[0]
            if label in {"", "Totale"}:  # skip total row — we reconstruct from parts
                if label == "Totale":
                    out.append((
                        "Totale",
                        parse_int(cells[1]), parse_pct(cells[2]),
                        parse_int(cells[3]), parse_pct(cells[4]),
                        parse_int(cells[5]), parse_pct(cells[6]),
                    ))
                continue
            out.append((
                label,
                parse_int(cells[1]), parse_pct(cells[2]),
                parse_int(cells[3]), parse_pct(cells[4]),
                parse_int(cells[5]), parse_pct(cells[6]),
            ))
        return out  # stop after first matching table
    return out


GEM_LINK = re.compile(r"/AdminStat/it/de/demografia/eta/([^/]+)/(\d+)/4$")


def extract_gemeinden(html: str) -> list[tuple[str, str, str]]:
    """Return list of (urbistat_id, slug, name) for every Gemeinde linked on a Kreis page."""
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for a in soup.find_all("a", href=True):
        m = GEM_LINK.search(a["href"])
        if not m:
            continue
        gid = m.group(2)
        if gid in seen:
            continue
        seen.add(gid)
        out.append((gid, m.group(1), a.get_text(strip=True)))
    return out


def age_url(slug: str, uid: str, level: int) -> str:
    return f"{BASE}/AdminStat/it/de/demografia/eta/{slug}/{uid}/{level}"


def rows_from_parsed(kreis_ars: str, kreis_name: str, level: str, uid: str, slug: str, name: str,
                     parsed: list) -> Iterable[AgeRow]:
    for (cls, m_n, m_p, f_n, f_p, t_n, t_p) in parsed:
        if m_n is not None:
            yield AgeRow(kreis_ars, kreis_name, level, uid, slug, name, cls, "male",   m_n, m_p or 0.0)
        if f_n is not None:
            yield AgeRow(kreis_ars, kreis_name, level, uid, slug, name, cls, "female", f_n, f_p or 0.0)
        if t_n is not None:
            yield AgeRow(kreis_ars, kreis_name, level, uid, slug, name, cls, "total",  t_n, t_p or 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between requests (be polite). Default: 1.0")
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    all_rows: list[AgeRow] = []

    for ars, uid, slug, name in KREISE:
        print(f"[Kreis] {ars} {name} ({uid})", flush=True)
        url = age_url(slug, uid, 3)
        html = fetch(url, delay=args.delay)
        time.sleep(args.delay)
        kreis_parsed = parse_age_page(html)
        if not kreis_parsed:
            print(f"  WARNING: no age table parsed for Kreis {ars}", file=sys.stderr)
        else:
            all_rows.extend(rows_from_parsed(ars, name, "kreis", uid, slug, name, kreis_parsed))

        gemeinden = extract_gemeinden(html)
        print(f"  Gemeinden: {len(gemeinden)}", flush=True)
        for gid, gslug, gname in gemeinden:
            try:
                g_html = fetch(age_url(gslug, gid, 4), delay=args.delay)
            except Exception as e:
                print(f"  SKIP Gemeinde {gname} ({gid}): {e}", file=sys.stderr)
                time.sleep(args.delay)
                continue
            time.sleep(args.delay)
            gem_parsed = parse_age_page(g_html)
            if not gem_parsed:
                print(f"  WARNING: no age table for Gemeinde {gname} ({gid})", file=sys.stderr)
                continue
            all_rows.extend(rows_from_parsed(ars, name, "gemeinde", gid, gslug, gname, gem_parsed))

    print(f"\nTotal rows scraped: {len(all_rows)}")

    # Write raw CSV
    fields = ["kreis_ars", "kreis_name", "level", "urbistat_id", "slug", "name",
              "age_class", "sex", "count", "percent"]
    with open(RAW_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in all_rows:
            w.writerow(r.__dict__)
    print(f"Wrote {RAW_CSV}")

    # Split Kreis vs Gemeinde for convenience
    for level, path in [("kreis", KREIS_CSV), ("gemeinde", GEMEINDE_CSV)]:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in all_rows:
                if r.level == level:
                    w.writerow(r.__dict__)
        print(f"Wrote {path}")

    # Quick sanity summary
    totals = {}
    for r in all_rows:
        if r.level == "kreis" and r.age_class == "Totale" and r.sex == "total":
            totals[r.kreis_ars] = r.count
    print("\nKreis totals (Totale, Gesamt):")
    for ars, _, _, name in KREISE:
        print(f"  {ars} {name:<15} {totals.get(ars, 'n/a')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
