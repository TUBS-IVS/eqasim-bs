"""Verify the input files required to run the Braunschweig pipeline.

Two modes over the same ``INPUTS`` catalog:

*File presence* (default) --
    ``python scripts/verify_braunschweig_inputs.py [--data-path eqasim-data/data] [--matsim]``

    Prints a checklist of expected files with status (OK/MISSING) and download
    source URLs for each missing dataset. The grouping mirrors
    ``eqasim-data/DOWNLOAD_CHECKLIST_BS.md``: federal datasets (A), Lower-Saxony
    statistical inputs (B), preprocessed ALKIS/ATKIS/OSM (C), and MATSim-only
    inputs (D, optional).

*Source reachability* (``--check-urls``) --
    ``python scripts/verify_braunschweig_inputs.py --check-urls``

    Checks whether the documented DOWNLOAD SOURCE URLs are still reachable, and
    touches no local data at all. This is the CI-shaped question: file presence
    cannot be asked on a runner that has no data, but "did a portal move or rename
    our source" can. See ``docs/codebase/notes/ci-data-availability-checks.md``.

Always update this file *and* ``DOWNLOAD_CHECKLIST_BS.md`` together when
adding or replacing pipeline inputs.
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Input:
    name: str
    rel_path: str
    source: str
    notes: str = ""
    matsim_only: bool = False
    glob: bool = False
    optional: bool = False
    #: The dataset is a restricted/non-public delivery (never redistributed);
    #: a missing file is reported as [RESTRICTED] so the fix is "obtain via the
    #: usage agreement", not "download".
    restricted: bool = False
    #: The file is generated locally from raw inputs by the listed script; a
    #: missing file is reported as [GENERATED] so the fix is "run the script".
    generated: bool = False
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
        restricted=True,
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
    # B6 removed: braunschweig.work_flow_path is dead config on the BS DAG
    # (eqasim_common.data.census.employees is aliased to braunschweig.data.census.employees).
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
        restricted=True,
    ),
    Input(
        name="B10a MiD 2023 extracted CSVs (P9 / P12_1 / P13 / P17_1)",
        rel_path="braunschweig/mid",
        source="Generated locally by scripts/extract_mid_tables.py from B10.",
        notes="Required by braunschweig.data.mid.references and synthesis.spatial.commute_distance.",
        generated=True,
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
        generated=True,
        rel_path="braunschweig/preprocessed/alkis_buildings.parquet",
        source="Run python scripts/preprocess_alkis_landuse.py (raw input: braunschweig/buildings/gebaeude-ni.zip from https://opengeodata.lgln.niedersachsen.de)",
        notes="dl-de/zero-2-0 (LGLN). Read by braunschweig/data/alkis.py.",
    ),
    Input(
        name="C2  ATKIS landuse preprocessed parquet",
        generated=True,
        rel_path="braunschweig/preprocessed/landuse.parquet",
        source="Run python scripts/preprocess_alkis_landuse.py (raw input: braunschweig/landuse/FS_LN_03_NI_*.zip from https://opengeodata.lgln.niedersachsen.de)",
        notes="dl-de/zero-2-0 (LGLN). Read by braunschweig/data/landuse.py.",
    ),
    Input(
        name="C3  OSM POIs preprocessed parquet",
        generated=True,
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


# --------------------------------------------------------------------------- #
# --check-urls mode: is each documented DOWNLOAD SOURCE still reachable?
#
# Deliberately separate from the file-presence mode above: it touches no local
# data, so it is the only one of the two questions a CI runner can answer.
# --------------------------------------------------------------------------- #

#: Several statistical portals reject the default python-requests agent outright.
#: A browser agent is not evasion here -- these are public download pages whose
#: files we are documented to fetch by hand.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
_URL_TIMEOUT_SECONDS = 30
#: Attempts per HTTP method (so 2 = one retry). Kept small: the run is a weekly
#: cron over ~20 hosts, and a source that needs more than one retry to answer is
#: itself worth reporting.
_URL_ATTEMPTS = 2


def url_from_source(source: str) -> Optional[str]:
    """Extract the download URL from an :class:`Input`'s ``source`` field, if any.

    ``source`` is documentation prose in the general case. Three shapes occur:
    a bare URL; a URL followed by a parenthesised hint (e.g. B11's
    ``"https://... (run python scripts/download_regiostar.py)"``); and pure prose
    with no URL at all (e.g. B10's ``"infas mobility report - provided by ZGB"``).

    The first whitespace-delimited token is therefore tested rather than the whole
    string: taking the whole string would classify every URL-plus-hint entry as
    prose and silently shrink the check to a third of the catalog. Trailing
    punctuation is stripped so a URL ending a sentence or a list item
    (``"https://x.de, alternatively ..."``) still resolves. Returns ``None`` when
    the leading token is not an ``http(s)`` URL, which the caller reports as an
    explicit SKIPPED line (no silent omissions). A URL that merely appears LATER in
    the prose (``"see https://x.de"``) is deliberately not extracted: the leading
    token is the documented convention, and scanning prose for URLs would start
    fetching whatever an unrelated sentence happens to mention.
    """
    tokens = source.split()
    leading = tokens[0].rstrip(",;.") if tokens else ""
    if leading.startswith("http://") or leading.startswith("https://"):
        return leading
    return None


def probe_url(url: str, cache: dict) -> dict:
    """Probe ONE distinct URL for reachability, memoised in ``cache`` by URL.

    Deduplication is not an optimisation: several inputs legitimately share a
    source page (the six A3 ENTD files, both regionalstatistik tables, both
    Pendleratlas exports, both INKAR entries), so probing per INPUT would hit one
    host up to six times with up to four requests each and turn a single outage
    into six identical failure lines. The catalog stays one entry per FILE (the
    file-presence mode needs that); only the network work is shared.

    A non-2xx/3xx HEAD is never sufficient to declare a source dead: several
    statistical portals answer GET but not HEAD (or rate-limit HEAD), so the check
    retries and then falls back to a RANGED GET (one byte) before failing.

    Returns ``{"ok", "detail", "saw_http_status"}``. ``saw_http_status`` is False
    when NO attempt ever reached HTTP -- a TLS / DNS / timeout transport failure,
    whose remediation is not "fix the URL" (see the caller).
    """
    if url in cache:
        cached = dict(cache[url])
        cached["detail"] = f"{cached['detail']} [reused for this URL]"
        return cached

    import requests   # imported lazily so the file-presence mode needs no dependency

    session = requests.Session()
    session.headers.update({"User-Agent": _USER_AGENT})
    attempts: List[str] = []
    saw_http_status = False

    def _try(method: str, **kwargs) -> bool:
        """Run one method up to ``_URL_ATTEMPTS`` times; return True on success."""
        nonlocal saw_http_status
        for attempt in range(1, _URL_ATTEMPTS + 1):
            try:
                response = session.request(
                    method, url, timeout=_URL_TIMEOUT_SECONDS,
                    allow_redirects=True, **kwargs
                )
                saw_http_status = True
                note = f"{method} {response.status_code}"
                attempts.append(f"{note} (attempt {attempt})")
                if response.status_code < 400:
                    return True
            except Exception as exc:                      # noqa: BLE001 - any transport error
                # Keep the message, not just the class: SSLError(ASN1: NOT_ENOUGH_DATA)
                # and SSLError(CERTIFICATE_VERIFY_FAILED) are different diagnoses, and
                # the class name alone hides which one happened.
                message = " ".join(str(exc).split())[:160]
                attempts.append(f"{method} {type(exc).__name__}: {message} (attempt {attempt})")
        return False

    ok = _try("HEAD")
    if not ok:
        # Ranged GET: ask for a single byte so a large file is not downloaded just
        # to prove the URL resolves.
        ok = _try("GET", headers={"Range": "bytes=0-0"}, stream=True)
    session.close()

    result = {"ok": ok, "detail": "; ".join(attempts), "saw_http_status": saw_http_status}
    cache[url] = result
    return dict(result)


def check_url(inp: Input, cache: Optional[dict] = None) -> dict:
    """Resolve one input's download source to a reachability status.

    Statuses: ``OK`` (the source answered), ``UNREACHABLE`` (it did not), or
    ``SKIPPED`` (there is no public URL to check -- restricted delivery, locally
    generated file, or a prose-only source). Every skip and every retry carries
    its reason into ``detail`` so the run's output explains itself.
    """
    if inp.restricted:
        return {"input": inp, "status": "SKIPPED", "url": "", "saw_http_status": False,
                "detail": "restricted delivery (obtain via the usage agreement, no public URL)"}
    if inp.generated:
        return {"input": inp, "status": "SKIPPED", "url": "", "saw_http_status": False,
                "detail": "generated locally by the listed script (no public URL)"}

    url = url_from_source(inp.source)
    if url is None:
        return {"input": inp, "status": "SKIPPED", "url": "", "saw_http_status": False,
                "detail": "not-a-URL (prose source; acquire by hand per DOWNLOAD_CHECKLIST_BS.md)"}

    probe = probe_url(url, cache if cache is not None else {})
    return {
        "input": inp, "status": "OK" if probe["ok"] else "UNREACHABLE", "url": url,
        "detail": probe["detail"], "saw_http_status": probe["saw_http_status"],
    }


def run_url_check() -> int:
    """Check every input's download source and return the process exit code.

    Exit 1 when any NON-optional public source is unreachable; an ``optional=True``
    input's failure is a warning that does not fail the run (the pipeline runs
    without it). MATSim-only sources are included unconditionally -- unlike the
    file-presence mode, where group D is gated behind ``--matsim`` because the data
    may legitimately be absent, an unreachable source URL is a documentation defect
    at any scale.
    """
    print("Checking reachability of the documented Braunschweig download sources.")
    print(f"(HEAD then ranged GET, {_URL_ATTEMPTS} attempts each, "
          f"{_URL_TIMEOUT_SECONDS}s timeout, browser User-Agent)\n")

    # One shared cache so a source page used by several inputs is probed ONCE.
    cache: dict = {}
    results = [check_url(inp, cache) for inp in INPUTS]
    for r in results:
        inp = r["input"]
        tag = {"OK": "[OK]         ", "UNREACHABLE": "[UNREACHABLE]",
               "SKIPPED": "[SKIPPED]    "}[r["status"]]
        optional = " (optional)" if inp.optional else ""
        print(f"  {tag} {inp.name:<55}{optional}")
        if r["url"]:
            print(f"                {r['url']}")
        print(f"                {r['detail']}")

    ok = [r for r in results if r["status"] == "OK"]
    skipped = [r for r in results if r["status"] == "SKIPPED"]
    dead_required = [r for r in results
                     if r["status"] == "UNREACHABLE" and not r["input"].optional]
    dead_optional = [r for r in results
                     if r["status"] == "UNREACHABLE" and r["input"].optional]
    # Both counts are reported: the catalog is one entry per FILE, so "18 reachable"
    # over 13 distinct sources would otherwise overstate how much was actually probed.
    n_distinct = len({r["url"] for r in results if r["url"]})

    print(f"\nSummary: {len(results)} inputs over {n_distinct} distinct sources -- "
          f"{len(ok)} reachable, "
          f"{len(dead_required)} unreachable (required), "
          f"{len(dead_optional)} unreachable (optional, warning only), "
          f"{len(skipped)} skipped (no public URL).")

    for r in dead_optional:
        print(f"WARNING: optional source unreachable: {r['input'].name} -> {r['url']}")
    if dead_required:
        print("\n=== Unreachable REQUIRED sources ===")
        for r in dead_required:
            inp = r["input"]
            print(f"\n[!] {inp.name}")
            print(f"    URL:      {r['url']}")
            print(f"    Attempts: {r['detail']}")
            if inp.notes:
                print(f"    Note:     {inp.notes}")
        print(f"\nExit 1: {len(dead_required)} required download source(s) unreachable.")
        # A moved/renamed source and a blocked/broken transport need OPPOSITE actions,
        # and only the exception shape distinguishes them. Prescribing "fix the URL" for
        # a TLS-intercepting proxy sends the reader to edit a URL that is perfectly fine.
        if any(r["saw_http_status"] for r in dead_required):
            print("Some failures returned an HTTP status: for those, the source likely moved "
                  "or was renamed -- fix the URL here AND in "
                  "eqasim-data/DOWNLOAD_CHECKLIST_BS.md.")
        if any(not r["saw_http_status"] for r in dead_required):
            print("Some failures produced NO HTTP status at all: transport error "
                  "(TLS/DNS/timeout), not an HTTP 4xx/5xx -- see "
                  "docs/codebase/notes/ci-data-availability-checks.md.")
        return 1
    print("Exit 0: every required Braunschweig download source is reachable.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-path", default="eqasim-data/data")
    parser.add_argument("--matsim", action="store_true", help="Also check MATSim-only inputs (group D)")
    parser.add_argument(
        "--check-urls", action="store_true",
        help="Instead of local file presence, check that the documented download source "
             "URLs are still reachable (needs network, touches no data; used by CI).",
    )
    args = parser.parse_args()

    if args.check_urls:
        return run_url_check()

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
            if r["status"] == "OK":
                tag = "[OK]        "
            elif inp.restricted:
                tag = "[RESTRICTED]"
            elif inp.generated:
                tag = "[GENERATED] "
            elif inp.optional:
                tag = "[OPTIONAL]  "
            else:
                tag = "[MISSING]   "
            detail = f"{r['size_mb']:.1f} MB" if r["status"] == "OK" else r["detail"]
            print(f"  {tag} {inp.name:<55} -> {inp.rel_path}  ({detail})")
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
