"""Verify presence of all input files required to run the Braunschweig pipeline.

Usage:
    python scripts/verify_braunschweig_inputs.py [--data-path eqasim-data/data] [--matsim]

Prints a checklist of expected files with status (OK/MISSING) and download
source URLs for each missing dataset. The grouping mirrors
``eqasim-data/DOWNLOAD_CHECKLIST_BS.md``: federal datasets (A), Lower-Saxony
statistical inputs (B), preprocessed ALKIS/ATKIS/OSM (C), and MATSim-only
inputs (D, optional).

Always update this file *and* ``DOWNLOAD_CHECKLIST_BS.md`` together when
adding or replacing pipeline inputs.
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
    optional: bool = False
    required_files: List[str] = field(default_factory=list)
    alt_paths: List[str] = field(default_factory=list)


INPUTS: List[Input] = [
    # --- A: Federal / shared datasets -------------------------------------
    Input(
        name="A1  VG250-EW administrative boundaries",
        rel_path="germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
        source="https://gdz.bkg.bund.de/index.php/default/digitale-geodaten/verwaltungsgebiete/verwaltungsgebiete-1-250-000-mit-einwohnerzahlen-stand-31-12-vg250-ew-31-12.html",
        notes="dl-de/by-2-0 (BKG). Required for VG250 zones and landuse clipping.",
    ),
    Input(
        name="A2  KBA Fahrerlaubnisbestand FE4 2024",
        rel_path="germany/fe4_2024.xlsx",
        source="https://www.kba.de/DE/Statistik/Kraftfahrer/Fahrerlaubnisse/Fahrerlaubnisbestand/fahrerlaubnisbestand_node.html",
        notes="Sheets FE4.2 / FE4.3 / FE4.4 - Niedersachsen filter applied in code.",
    ),
    *[
        Input(
            name=f"A3  ENTD 2008 - {fname}",
            rel_path=f"entd_2008/{fname}",
            source="https://www.statistiques.developpement-durable.gouv.fr/enquete-nationale-transports-et-deplacements-entd-2008",
            notes="French HTS reused as travel-pattern donor (shared with Bavaria).",
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

    # --- B: Lower-Saxony statistical inputs (synthesis.output) ------------
    Input(
        name="B1  DESTATIS 12411-0018 population (Kreis x sex x age class)",
        rel_path="braunschweig/12411-0018_de.csv",
        source="https://www-genesis.destatis.de/genesis/online?operation=statistic&code=12411",
        notes="dl-de/by-2-0. Read by braunschweig.data.census.population (braunschweig.destatis_population_path).",
    ),
    Input(
        name="B1b urbistat Gemeinde-level population shares (CSV)",
        rel_path="braunschweig/urbistat_age_gemeinden.csv",
        source="https://urbistat.com (Gemeinde-level age scrape, 11 classes; project archive).",
        notes="Read via braunschweig.urbistat_gemeinden_path. Provides Gemeinde-level shares disaggregating B1.",
    ),
    Input(
        name="B2  GENESIS 13111-06-02-4 employees by residence",
        rel_path="braunschweig/13111-06-02-4.xlsx",
        source="https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111",
        notes="Wohnort x age x sex. Read via braunschweig.employment_path.",
    ),
    Input(
        name="B3  GENESIS 13111-01-03-5 employees at workplace (Gemeinde)",
        rel_path="braunschweig/13111-01-03-5.xlsx",
        source="https://www.regionalstatistik.de/genesis/online?operation=statistic&code=13111",
        notes="SvB Arbeitsort, Gemeindeebene. Read via braunschweig.employees_path.",
    ),
    Input(
        name="B4  BA Beschaeftigungsstatistik gemband-dlk",
        rel_path="braunschweig/gemband-dlk-0-202506-xlsx.xlsx",
        source="https://statistik.arbeitsagentur.de (Beschaeftigung -> sozialversicherungspflichtig -> Gemeindeband)",
        notes="Employees by Wirtschaftsabteilung x Gemeinde. Read via braunschweig.employment_gemband_path.",
    ),
    Input(
        name="B5a BA Pendleratlas - Einpendler ZGB (CSV)",
        rel_path="braunschweig/statistik_pendler_2026042493412.csv",
        source="https://statistik.arbeitsagentur.de/SiteGlobals/Forms/Suche/Einzelheftsuche_Formular.html?topic_f=beschaeftigung-sozbe-krpend",
        notes="Read via braunschweig.pendler_ein_path. Filename contains export timestamp - rename in config if re-exported.",
    ),
    Input(
        name="B5b BA Pendleratlas - Auspendler ZGB (CSV)",
        rel_path="braunschweig/statistik_pendler_2026042493430.csv",
        source="https://statistik.arbeitsagentur.de/SiteGlobals/Forms/Suche/Einzelheftsuche_Formular.html?topic_f=beschaeftigung-sozbe-krpend",
        notes="Read via braunschweig.pendler_aus_path.",
    ),
    # B6 removed: bavaria.work_flow_path is dead config on the BS DAG
    # (bavaria.data.census.employees is aliased to braunschweig.data.census.employees).
    Input(
        name="B7  Zensus 2022 households 5000H-2001 flat-CSV",
        rel_path="braunschweig/5000H-2001_de_flat.csv",
        source="https://ergebnisse.zensus2022.de (Tabelle 5000H-2001, Flat-File)",
        notes="dl-de/by-2-0. Required by household_size / households_size_age / households_type stages.",
    ),
    Input(
        name="B8  BBSR INKAR household income (E_Haushaltseinkommen.xls)",
        rel_path="braunschweig/E_Haushaltseinkommen.xls",
        source="https://www.inkar.de (Indikatorenexport)",
        notes="dl-de/by-2-0. Read via braunschweig.inkar_household_income_path.",
    ),
    Input(
        name="B9  BBSR INKAR full panel (optional)",
        rel_path="braunschweig",
        source="https://www.inkar.de",
        notes="Optional: E_Bevoelkerungsdichte.xls, E_Arbeitslosenquote.xls, E_HochschulabsolventenQuote.xls, E_AerzteJeEinwohner.xls (used by braunschweig.data.inkar.full_panel).",
        glob=True,
        optional=True,
        required_files=[
            "E_Bevoelkerungsdichte.xls",
            "E_Arbeitslosenquote.xls",
            "E_HochschulabsolventenQuote.xls",
            "E_AerzteJeEinwohner.xls",
        ],
    ),
    Input(
        name="B10 MiD 2023 Grossraum Braunschweig (infas 7555 PDF)",
        rel_path="braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Gro\u00dfraum_Braunschweig.pdf",
        source="infas mobility report - provided by ZGB / BMDV (non-commercial).",
        notes="Source for BS commute-distance CDFs (P13). Process with scripts/extract_mid_tables.py.",
        optional=True,
    ),
    Input(
        name="B10a MiD 2023 extracted CSVs (P9 / P12_1 / P13 / P17_1)",
        rel_path="braunschweig/mid",
        source="Generated locally by scripts/extract_mid_tables.py from B10.",
        notes="Required by braunschweig.data.mid.references and synthesis.spatial.commute_distance.",
        glob=True,
        required_files=[
            "mid2023_P9.csv",
            "mid2023_P12_1.csv",
            "mid2023_P13.csv",
            "mid2023_P17_1.csv",
        ],
    ),
    Input(
        name="B11 BMV RegioStaR-7 reference (auto-download)",
        rel_path="regiostar/regiostar_referenzdatei.xlsx",
        source="https://www.bmv.de/SharedDocs/DE/Anlage/G/regiostar-referenzdateien.xlsx (run python scripts/download_regiostar.py)",
        notes="dl-de/by-2-0 (BMV).",
    ),
    Input(
        name="B12 Zensus 2022 100 m grid parquet (auto-download)",
        rel_path="zensus_grid",
        source="https://github.com/JsLth/z22data (run python scripts/download_zensus_grid.py)",
        notes="dl-de/by-2-0. Provides population_100m.parquet and grid_100m.parquet.",
        glob=True,
        required_files=["population_100m.parquet", "grid_100m.parquet"],
        optional=True,
    ),

    # --- C: Preprocessed ALKIS / ATKIS / OSM parquets ---------------------
    Input(
        name="C1  ALKIS buildings preprocessed parquet",
        rel_path="braunschweig/preprocessed/alkis_buildings.parquet",
        source="Run python scripts/preprocess_alkis_landuse.py (raw input: braunschweig/buildings/gebaeude-ni.zip from https://opengeodata.lgln.niedersachsen.de)",
        notes="dl-de/zero-2-0 (LGLN). Read by braunschweig/data/alkis.py.",
    ),
    Input(
        name="C2  ATKIS landuse preprocessed parquet",
        rel_path="braunschweig/preprocessed/landuse.parquet",
        source="Run python scripts/preprocess_alkis_landuse.py (raw input: braunschweig/landuse/FS_LN_03_NI_*.zip from https://opengeodata.lgln.niedersachsen.de)",
        notes="dl-de/zero-2-0 (LGLN). Read by braunschweig/data/landuse.py.",
    ),
    Input(
        name="C3  OSM POIs preprocessed parquet",
        rel_path="braunschweig/preprocessed/osm_pois.parquet",
        source="Run python scripts/preprocess_osm_pois.py (raw input: osm/niedersachsen-latest.osm.pbf from https://download.geofabrik.de)",
        notes="ODbL 1.0 (OSM contributors). Read by braunschweig/data/osm.py.",
    ),

    # --- D: MATSim-only inputs --------------------------------------------
    Input(
        name="D1  OSM Niedersachsen PBF",
        rel_path="osm/niedersachsen-latest.osm.pbf",
        source="https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf",
        notes="ODbL 1.0. Required for matsim.output and the C3 OSM POI preprocessor.",
        matsim_only=True,
    ),
    Input(
        name="D2  GTFS feed (Delfi or ZGB)",
        rel_path="gtfs",
        source="https://www.opendata-oepnv.de/ht/de/organisation/delfi/startseite or https://www.zgb.de",
        notes="ZIP placed under eqasim-data/data/gtfs/. Pre-filter to ZGB bbox before use.",
        matsim_only=True,
        glob=True,
    ),
]


def _check_glob(inp: Input, full: str) -> dict:
    if not os.path.isdir(full):
        return {"input": inp, "status": "MISSING", "size_mb": 0.0, "detail": "directory missing"}
    if inp.required_files:
        missing = [f for f in inp.required_files if not os.path.isfile(os.path.join(full, f))]
        if missing:
            return {"input": inp, "status": "MISSING", "size_mb": 0.0, "detail": "missing: " + ", ".join(missing)}
        size_mb = sum(os.path.getsize(os.path.join(full, f)) for f in inp.required_files) / 1e6
        return {"input": inp, "status": "OK", "size_mb": size_mb, "detail": f"{len(inp.required_files)} file(s)"}
    entries = [f for f in os.listdir(full) if not f.startswith(".")]
    if not entries:
        return {"input": inp, "status": "MISSING", "size_mb": 0.0, "detail": "directory empty"}
    size_mb = sum(
        os.path.getsize(os.path.join(full, f))
        for f in entries
        if os.path.isfile(os.path.join(full, f))
    ) / 1e6
    return {"input": inp, "status": "OK", "size_mb": size_mb, "detail": f"{len(entries)} file(s)"}


def check(inp: Input, data_path: str) -> dict:
    full = os.path.join(data_path, inp.rel_path)
    if inp.glob:
        return _check_glob(inp, full)

    for rp in [inp.rel_path, *inp.alt_paths]:
        p = os.path.join(data_path, rp)
        if os.path.isfile(p):
            detail = "" if rp == inp.rel_path else f"(as {os.path.basename(rp)})"
            return {"input": inp, "status": "OK", "size_mb": os.path.getsize(p) / 1e6, "detail": detail}
    return {"input": inp, "status": "MISSING", "size_mb": 0.0, "detail": "file missing"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", default="eqasim-data/data")
    parser.add_argument("--matsim", action="store_true", help="Also check MATSim-only inputs (group D)")
    args = parser.parse_args()

    data_path = os.path.abspath(args.data_path)
    print(f"Checking Braunschweig inputs in: {data_path}\n")

    syn_results, matsim_results = [], []
    for inp in INPUTS:
        r = check(inp, data_path)
        (matsim_results if inp.matsim_only else syn_results).append(r)

    def render(title: str, rows):
        print(f"=== {title} ===")
        for r in rows:
            inp = r["input"]
            tag = "[OK] " if r["status"] == "OK" else ("[??] " if inp.optional else "[--] ")
            detail = f"{r['size_mb']:.1f} MB" if r["status"] == "OK" else r["detail"]
            print(f"  {tag}{inp.name:<55} -> {inp.rel_path}  ({detail})")
        print()

    render("synthesis.output (required + optional)", syn_results)
    if args.matsim:
        render("matsim.output (optional)", matsim_results)

    missing_required = [
        r for r in syn_results if r["status"] != "OK" and not r["input"].optional
    ]
    if args.matsim:
        missing_required += [
            r for r in matsim_results if r["status"] != "OK" and not r["input"].optional
        ]

    missing_optional = [
        r
        for r in syn_results + (matsim_results if args.matsim else [])
        if r["status"] != "OK" and r["input"].optional
    ]

    if missing_required or missing_optional:
        print("=== Download checklist (missing inputs) ===")
        for r in missing_required:
            inp = r["input"]
            print(f"\n[ ] {inp.name}")
            print(f"    Target: {os.path.join(data_path, inp.rel_path)}")
            print(f"    Source: {inp.source}")
            if inp.notes:
                print(f"    Note:   {inp.notes}")
        if missing_optional:
            print("\n--- optional ---")
            for r in missing_optional:
                inp = r["input"]
                print(f"\n[?] {inp.name}")
                print(f"    Target: {os.path.join(data_path, inp.rel_path)}")
                print(f"    Source: {inp.source}")
                if inp.notes:
                    print(f"    Note:   {inp.notes}")

    if missing_required:
        return 1
    print("All required Braunschweig inputs are present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
