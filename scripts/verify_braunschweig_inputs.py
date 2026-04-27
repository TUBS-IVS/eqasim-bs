"""Verify presence of all input files required to run the Braunschweig pipeline.

Usage:
    python scripts/verify_braunschweig_inputs.py [--data-path eqasim-data/data] [--matsim]

Prints a checklist of expected files with status (OK/MISSING) and download
source URLs for each missing dataset. Mirrors scripts/verify_bavaria_inputs.py
but for the Lower Saxony / Braunschweig region (ARS prefix 031).
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import List


@dataclass
class Input:
    name: str
    rel_path: str
    source: str
    notes: str = ""
    matsim_only: bool = False
    glob: bool = False
    required_files: List[str] = field(default_factory=list)
    alt_paths: List[str] = field(default_factory=list)


INPUTS: List[Input] = [
    # --- Federal / shared with Bavaria ---
    Input(
        name="Admin-Grenzen Deutschland (VG250-EW)",
        rel_path="germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
        source="https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-250-000-mit-einwohnerzahlen-stand-31-12-vg250-ew-31-12.html",
        notes="Shared with Bavaria setup",
    ),
    Input(
        name="Fuehrerscheinbestand KBA (FE4)",
        rel_path="germany/fe4_2024.xlsx",
        source="https://www.kba.de/DE/Statistik/Kraftfahrer/Fahrerlaubnisse/Fahrerlaubnisbestand/fahrerlaubnisbestand_node.html",
        notes="Shared with Bavaria setup - Sheets FE4.2 / FE4.3 / FE4.4",
    ),

    # --- Lower Saxony specific ---
    Input(
        name="Bevoelkerung Niedersachsen x Gemeinde (GENESIS 12111-0001)",
        rel_path="braunschweig/12111-0001_population_ni.xlsx",
        source="https://www.regionalstatistik.de/genesis/online?operation=statistic&code=12111",
        notes="Regionaldatenbank, Gemeindeebene NI, Sex x Altersklassen, XLSX",
    ),
    Input(
        name="Erwerbstaetige Niedersachsen x Kreis (GENESIS 13111-0004)",
        rel_path="braunschweig/13111-0004_employment_ni.xlsx",
        source="https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111",
        notes="Kreisebene NI, Sex x Altersklassen, XLSX",
    ),
    Input(
        name="Pendlerstatistik Niedersachsen",
        rel_path="braunschweig/pendler_ni.xlsx",
        source="https://statistik.arbeitsagentur.de/ (BA Pendleratlas Kreis) oder https://www.statistik.niedersachsen.de",
        notes="Wohnort -> Arbeitsort, bevorzugt Gemeindeebene (ARS 031xx)",
    ),
    Input(
        name="Hausumringe Niedersachsen (LGLN)",
        rel_path="braunschweig/buildings",
        source="https://opengeodata.lgln.niedersachsen.de",
        notes="Shapefile-Pakete pro Kreis/Region - auf ARS 031 filtern - ZIPs oder entpackt",
        glob=True,
    ),

    # --- matsim.output only ---
    Input(
        name="OSM Niedersachsen (PBF)",
        rel_path="osm/niedersachsen-latest.osm.pbf",
        source="https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf",
        notes="~800 MB - nur fuer matsim.output",
        matsim_only=True,
    ),
    Input(
        name="GTFS Deutschland / Regionalverbund",
        rel_path="gtfs",
        source="https://www.opendata-oepnv.de/ht/de/organisation/delfi/startseite (Delfi-Deutschland) oder https://www.zgb.de (Verbund-Feeds)",
        notes="ZIP ablegen als eqasim-data/data/gtfs/<beliebig>.zip - auf 031-BBox filtern - nur fuer matsim.output",
        matsim_only=True,
        glob=True,
    ),

    # --- ENTD 2008 (shared, reused as HTS) ---
    *[
        Input(
            name=f"ENTD 2008 - {fname}",
            rel_path=f"entd_2008/{fname}",
            source="https://www.statistiques.developpement-durable.gouv.fr/enquete-nationale-transports-et-deplacements-entd-2008",
            notes="Shared with Bavaria setup",
        )
        for fname in (
            "Q_individu.csv",
            "Q_tcm_individu.csv",
            "Q_menage.csv",
            "Q_tcm_menage_0.csv",
            "K_deploc.csv",
            "Q_ind_lieu_teg.csv",
        )
    ],
]


def check(inp: Input, data_path: str) -> dict:
    full = os.path.join(data_path, inp.rel_path)
    status = "MISSING"
    size_mb = 0.0
    detail = ""

    if inp.glob:
        if not os.path.isdir(full):
            detail = "Ordner fehlt"
        elif inp.required_files:
            missing = [f for f in inp.required_files if not os.path.isfile(os.path.join(full, f))]
            if not missing:
                status = "OK"
                size_mb = sum(os.path.getsize(os.path.join(full, f)) for f in inp.required_files) / 1e6
            else:
                detail = "Fehlend: " + ", ".join(missing)
        else:
            entries = [f for f in os.listdir(full) if not f.startswith(".")]
            if entries:
                status = "OK"
                size_mb = sum(os.path.getsize(os.path.join(full, f)) for f in entries if os.path.isfile(os.path.join(full, f))) / 1e6
                detail = f"{len(entries)} Datei(en)"
            else:
                detail = "Ordner leer"
    else:
        candidate_paths = [inp.rel_path, *inp.alt_paths]
        found = None
        for rp in candidate_paths:
            p = os.path.join(data_path, rp)
            if os.path.isfile(p):
                found = p
                break
        if found:
            status = "OK"
            size_mb = os.path.getsize(found) / 1e6
            if found != full:
                detail = f"(als {os.path.basename(found)})"
        else:
            detail = "Datei fehlt"

    return {"input": inp, "status": status, "size_mb": size_mb, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", default="eqasim-data/data")
    parser.add_argument("--matsim", action="store_true")
    args = parser.parse_args()

    data_path = os.path.abspath(args.data_path)
    print(f"Checking Braunschweig inputs in: {data_path}\n")

    results_syn, results_matsim = [], []
    for inp in INPUTS:
        r = check(inp, data_path)
        (results_matsim if inp.matsim_only else results_syn).append(r)

    def render(title: str, rows):
        print(f"=== {title} ===")
        for r in rows:
            icon = "[OK] " if r["status"] == "OK" else "[--] "
            size = f"{r['size_mb']:.1f} MB" if r["status"] == "OK" else r["detail"]
            print(f"  {icon}{r['input'].name:<55} -> {r['input'].rel_path}  ({size})")
        print()

    render("synthesis.output (Pflicht)", results_syn)
    if args.matsim:
        render("matsim.output (optional)", results_matsim)

    missing = [r for r in results_syn if r["status"] != "OK"]
    if args.matsim:
        missing += [r for r in results_matsim if r["status"] != "OK"]

    if missing:
        print("=== DOWNLOAD-CHECKLISTE (fehlende Inputs) ===")
        for r in missing:
            inp = r["input"]
            print(f"\n[ ] {inp.name}")
            print(f"    Ziel:   {os.path.join(data_path, inp.rel_path)}")
            print(f"    Quelle: {inp.source}")
            if inp.notes:
                print(f"    Hinweis: {inp.notes}")
        return 1

    print("Alle benoetigten Inputs vorhanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
