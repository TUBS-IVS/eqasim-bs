"""Verify presence of all input files required to run the Bavaria pipeline.

Usage:
    python scripts/verify_bavaria_inputs.py [--data-path eqasim-data/data] [--matsim]

Prints a checklist of expected files with status (OK/MISSING) and
download source URLs for each missing dataset.
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import List


@dataclass
class Input:
    name: str
    rel_path: str           # relative to data_path, or glob pattern
    source: str             # URL or portal description
    notes: str = ""
    matsim_only: bool = False
    glob: bool = False       # rel_path is a directory that must contain at least one file
    required_files: List[str] = field(default_factory=list)
    alt_paths: List[str] = field(default_factory=list)  # accepted alternative filenames


INPUTS: List[Input] = [
    # --- synthesis.output ---
    Input(
        name="Admin-Grenzen Deutschland (VG250-EW)",
        rel_path="germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
        source="https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-250-000-mit-einwohnerzahlen-stand-31-12-vg250-ew-31-12.html",
        notes="UTM32s / Geopackage / ebenen, Version 12-31",
    ),
    Input(
        name="Bevoelkerung Bayern (A1310C)",
        rel_path="bavaria/a1310c_202200.xla",
        source="https://www.statistik.bayern.de/statistik/gebiet_bevoelkerung/bevoelkerungsstand/",
        notes="Statistik Bayern, Jahrgang 202200 (.xla oder .xlsx)",
        alt_paths=["bavaria/a1310c_202200.xlsx"],
    ),
    Input(
        name="Erwerbstaetige Bezirk (13111-004r)",
        rel_path="bavaria/13111-004r.xlsx",
        source="https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=13111",
        notes="ERW032 Wohnort -> Werteabruf -> XLSX Export",
    ),
    Input(
        name="Erwerbstaetige Gemeinden (A6502C)",
        rel_path="bavaria/a6502c_202200.xla",
        source="https://www.statistik.bayern.de/statistik/gebiet_bevoelkerung/erwerbstaetigkeit/index.html",
        notes="Statistik Bayern, Jahrgang 202200 (.xla oder .xlsx)",
        alt_paths=["bavaria/a6502c_202200.xlsx"],
    ),
    Input(
        name="Haushaltsgroesse (12211-105)",
        rel_path="bavaria/12211-105.xlsx",
        source="https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=12211",
        notes="Sex x Age x Haushaltsgroesse",
    ),
    Input(
        name="Haushaltseinkommen (12211-101)",
        rel_path="bavaria/12211-101.xlsx",
        source="https://www.statistikdaten.bayern.de/genesis/online?operation=statistic&code=12211",
        notes="Haushaltsgroesse x Einkommensklasse",
    ),
    Input(
        name="Fuehrerscheinbestand KBA (FE4)",
        rel_path="germany/fe4_2024.xlsx",
        source="https://www.kba.de/DE/Statistik/Kraftfahrer/Fahrerlaubnisse/Fahrerlaubnisbestand/fahrerlaubnisbestand_node.html",
        notes="Jahrgang 2024 - Sheets FE4.2/FE4.3/FE4.4",
    ),
    Input(
        name="Hausumringe Bayern (Regierungsbezirke)",
        rel_path="bavaria/buildings",
        source="https://geodaten.bayern.de/opengeodata/OpenDataDetail.html?pn=hausumringe",
        notes="Pflicht: 091_Oberbayern, 092_Niederbayern, 097_Schwaben (ZIP pro Bezirk)",
        glob=True,
        required_files=[
            "091_Oberbayern_Hausumringe.zip",
            "092_Niederbayern_Hausumringe.zip",
            "097_Schwaben_Hausumringe.zip",
        ],
    ),
    # ENTD CSVs (French HTS, used as trip pattern source)
    *[
        Input(
            name=f"ENTD 2008 - {fname}",
            rel_path=f"entd_2008/{fname}",
            source="https://www.statistiques.developpement-durable.gouv.fr/enquete-nationale-transports-et-deplacements-entd-2008",
            notes="Latin-1 Encoding; Einzeldownloads, nicht das ZIP",
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

    # --- matsim.output only ---
    Input(
        name="OSM Bayern (PBF)",
        rel_path="osm/bayern-latest.osm.pbf",
        source="https://download.geofabrik.de/europe/germany/bayern.html",
        notes="~2 GB - nur fuer matsim.output (alternativ datierte Geofabrik-Snapshots)",
        matsim_only=True,
        alt_paths=["osm/bayern-260421.osm.pbf"],
    ),
    Input(
        name="GTFS Deutschland",
        rel_path="gtfs",
        source="https://gtfs.de/de/feeds/de_full/",
        notes="ZIP ablegen als eqasim-data/data/gtfs/<beliebig>.zip - nur fuer matsim.output",
        matsim_only=True,
        glob=True,
    ),
    Input(
        name="MVG Stations (JSON)",
        rel_path="mvg/stations.json",
        source="https://www.mvg.de/.rest/zdm/stations",
        notes="JSON direkt speichern - nur fuer matsim.output",
        matsim_only=True,
    ),
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
    parser.add_argument("--data-path", default="eqasim-data/data", help="Root des Input-Datenordners")
    parser.add_argument("--matsim", action="store_true", help="Auch matsim-only Inputs pruefen")
    args = parser.parse_args()

    data_path = os.path.abspath(args.data_path)
    print(f"Checking Bavaria inputs in: {data_path}\n")

    results_syn, results_matsim = [], []
    for inp in INPUTS:
        r = check(inp, data_path)
        (results_matsim if inp.matsim_only else results_syn).append(r)

    def render(title: str, rows):
        print(f"=== {title} ===")
        for r in rows:
            icon = "[OK] " if r["status"] == "OK" else "[--] "
            size = f"{r['size_mb']:.1f} MB" if r["status"] == "OK" else r["detail"]
            print(f"  {icon}{r['input'].name:<45} -> {r['input'].rel_path}  ({size})")
        print()

    render("synthesis.output (Pflicht)", results_syn)
    if args.matsim:
        render("matsim.output (optional)", results_matsim)

    # Print download checklist for missing items
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
            if inp.required_files:
                print(f"    Benoetigte Dateien: {', '.join(inp.required_files)}")
        return 1

    print("Alle benoetigten Inputs vorhanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
