"""Assemble eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson (one-off curation, issue #249).

Not part of the pipeline and never run by synpp. Inputs (all gitignored, from the curation session of 2026-09-29):
the Braunschweig zones Ia/Ib of extract_braunschweig_zone_map.py and the affine of georeference_zone_map.py, the
Stadthalle fee islands of digitise_stadthalle_fee_islands.py, the Overpass responses under raw_overpass/ (one per
municipality, written by scripts/build_parking_zones_from_osm.py), the OSM-derived car network of the local MATSim
scenario eqasim-data/output_bs (2026-04-29) for the three towns whose Overpass request failed, and the cached output
of data.spatial.municipalities (containment check). Every polygon gets geometry_source, source_url, source_date,
digitised_on and a digitising_note with its method and parameters; overlaps are resolved by an explicit precedence
(the more specific zone is cut out of the enclosing one); every polygon must lie at least 99 % inside its
municipality. The file carries the ODbL licence and attribution as top-level members, because every polygon's
coordinates depend on OpenStreetMap (outline, street or georeference).

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/assemble_parking_zones.py --ia-ib bs_zone_map_ia_ib.geojson \
        --affine bs_zone_map_affine.json --fee-islands stadthalle_fee_islands.geojson \
        --raw-overpass eqasim-data/data/braunschweig/parking/raw_overpass \
        --network <main checkout>/eqasim-data/output_bs/braunschweig_1pct_network.xml.gz \
        --municipalities <main checkout>/eqasim-data/cache_bs_bpsmoke/data.spatial.municipalities__<hash>.p \
        --out eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box
from shapely.ops import unary_union

import curation_common as cc

DIGITISED_ON = "2026-09-29"
LICENSE = ("ODbL-1.0: every polygon is derived from OpenStreetMap data (OSM outlines, OSM streets, or a georeference "
           "on OSM street centrelines); Open Database License 1.0, https://opendatacommons.org/licenses/odbl/1-0/")
ATTRIBUTION = ("(c) OpenStreetMap contributors (https://www.openstreetmap.org/copyright). Zone boundaries after the "
               "parking maps and ordinances of the City of Braunschweig (Abt. Geoinformation), the TU Braunschweig GB3 "
               "parking pages and the municipal parking pages of Wolfsburg, Salzgitter, Peine, Goslar, Wolfenbuettel, "
               "Gifhorn and Helmstedt (source_url per feature).")
BS_PARKRAUM_URL = "https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/parkraummanagement.php"
ZONE_MAP_URL = ("https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/ausweitung-parkgebuehrenpflicht/"
                "20251126_Karte-Parkzone-Innenstadt-a-b-2025-01.jpg.scaled/a72c34d0df9f9693b72eca1f31da2e9f.jpg")
BGA_URL = "https://www.braunschweig.de/politik_verwaltung/politik/stadtrecht/2_09_Entgeltordnung-BgA-Parkraumbewirtschaftung.pdf"
TU_URL = "https://www.tu-braunschweig.de/gb3/parkraumbewirtschaftung/parkbereiche"
STREET_BUFFER_M = 15.0
PARKING_BUFFER_M = 5.0
STADTHALLE_STREETS = ["Adolfstraße", "Bertramstraße", "Körnerstraße", "Marthastraße", "Gerstäckerstraße",
                      "Kleine Campestraße", "Lachmannstraße", "Villierstraße", "Kleine Leonhardstraße"]
STADTHALLE_WINDOW = box(604550, 5790650, 605250, 5791350)
PRECEDENCE = ["bs_bga_markthalle", "bs_bga_kannengiesserstrasse", "bs_bga_an_der_martinikirche",
              "bs_bga_jodutenstrasse_klint", "bs_bga_suedstrasse", "tu_international_house",
              "bs_parkscheininsel_marthastrasse_koernerstrasse", "bs_parkscheininsel_gerstaeckerstrasse_kleine_campestrasse",
              "bs_parkscheininsel_mentestrasse", "bs_zone_ia", "bs_zone_ib", "bs_resident_stadthalle_132",
              "tu_zentralcampus", "tu_campus_nord", "tu_campus_ost_beethovenstrasse", "tu_campus_ost_langer_kamp",
              "tu_forschungsflughafen"]
MINIMUM_PART_M2 = 20.0
SIMPLIFY_M = 0.5
MINIMUM_INSIDE_SHARE = 0.99


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ia-ib", required=True)
    parser.add_argument("--affine", required=True)
    parser.add_argument("--fee-islands", required=True, help="GeoJSON of digitise_stadthalle_fee_islands.py")
    parser.add_argument("--raw-overpass", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--municipalities", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    zones = []

    def add(zone_id, ags, geometry, geometry_source, source_url, note):
        zones.append({"zone_id": zone_id, "_ags": ags, "geometry": geometry, "geometry_source": geometry_source,
                      "source_url": source_url, "source_date": DIGITISED_ON, "digitised_on": DIGITISED_ON,
                      "digitising_note": note})

    responses = {ags: cc.overpass_response(args.raw_overpass, ags)
                 for ags in ("03101000", "03102000", "03103000", "03154028", "03157006")}
    base = {ags: response["osm3s"]["timestamp_osm_base"] for ags, response in responses.items()}

    # ---------------------------------------------------------------- Braunschweig zones Ia / Ib (city zone map)
    bs = gpd.read_file(args.ia_ib).to_crs(cc.METRIC_CRS).set_index("zone_id")
    fit = json.load(open(args.affine, encoding="utf-8"))
    rms_px, mpp = fit["history"][-1]["cp_rms_px"], fit["metres_per_px"]
    ax, ay = fit["affine_px"], fit["affine_py"]
    georef = (f"georeferenced by an affine fit of the map to OSM street centrelines (Overpass 2026-09-29; "
              f"{len(fit['cp_residual_px'])} windowed chamfer control points, RMS {rms_px:.1f} px = {rms_px * mpp:.1f} m at "
              f"{mpp:.2f} m/px; px = {ax[0]:.9f} E + {ax[1]:.9f} N + {ax[2]:.3f}, py = {ay[0]:.9f} E + {ay[1]:.9f} N + "
              f"{ay[2]:.3f}, EPSG:25832, 1920x1450 px image; scripts/curation/parking_zones_2026)")
    # Best fit of the annex (2026-09-29, scratch code, not committed because it produced no committed geometry): an
    # affine chamfer fit of this file's 1a/1b outline to the annex zone lines; RMS 11.1 m over the outline samples
    # within 10 px of an annex line, 26.8 m RMS at the five BgA car-park dots as independent check points.
    annex = ("The zone extents are defined by the ParkGO annex map (sec. 2(1); 'Anlage zur ParkGO', published on page 3 "
             "of 2_08_Parkgebuehrenordnung_2025.pdf), which also draws zone II; the annex was not digitised in v1 "
             "because it could not be georeferenced to an RMS of 10 m or better (best affine fit, about 3.7 m per "
             "pixel: 11.1 m RMS between the zone 1a/1b outline of this file and the annex zone lines, 26.8 m RMS at "
             "the five BgA car parks as check points), so this overview map is used instead.")
    add("bs_zone_ia", "03101000", bs.loc["bs_zone_ia", "geometry"], "ordinance_map", ZONE_MAP_URL,
        "Traced from the City of Braunschweig overview map of parking zones 1a/1b (image 20251126_Karte-Parkzone-"
        "Innenstadt-a-b-2025-01, published on parken-in-der-innenstadt.php): the region enclosed by the dotted zone-1a "
        "outline (dark pixels joined by a 4 px dilation, flood-filled from the core, grown back by 4 px), minus the "
        "green zone-1b fill; " + georef + "; simplified 1 m. The outline leaves the map at its lower edge (Buergerpark / "
        "Wolfenbuetteler Strasse); the polygon is closed along that edge, so the part of zone 1a south of the map extent "
        "(drawn on the ParkGO annex) is missing. The BgA car parks of the Entgeltordnung are cut out (own zones). " + annex
        + " The city describes 1a as the area inside the City-Ring plus Grosser Hof, Werder, Wilhelmstrasse and "
        "Nimesstrasse.")
    add("bs_zone_ib", "03101000", bs.loc["bs_zone_ib", "geometry"], "ordinance_map", ZONE_MAP_URL,
        "Traced from the same City of Braunschweig zone map as bs_zone_ia: the green zone-1b fill (pixels with G-R > 12 "
        "and G-B > 40, closed by 3 px, holes filled, specks < 400 px dropped), two parts (Wallring; Loewenwall / "
        "Windmuehlenberg); " + georef + "; simplified 1 m. The city describes 1b as the Wallring without the "
        "Theaterumfahrt and the Steintorwall (both belong to 1a). " + annex)

    # ---------------------------------------------------------------- Braunschweig BgA car parks (Entgeltordnung 2_09)
    ways_by_id = {e["id"]: e for e in responses["03101000"]["elements"] if e["type"] == "way"}

    def osm_union(ids, buffer_m=0.0):
        geometry = unary_union(cc.to_metric([cc.element_geometry(ways_by_id[i]) for i in ids]))
        return geometry.buffer(buffer_m, join_style=2) if buffer_m else geometry

    add("bs_bga_markthalle", "03101000", osm_union([7743381]), "ordinance_map", BGA_URL,
        "BgA car park 'Markthalle': the lot is named and located by the Entgeltordnung B 660 (Anlage 1 map); its outline "
        "is the OSM amenity=parking way 7743381 (name Markthalle, operator Stadt Braunschweig, capacity 70), so the "
        f"geometry is OSM-derived (ODbL). OSM tags the lot fee=no, the ordinance makes it paid. Overpass 2026-09-29 (OSM "
        f"base {base['03101000']}).")
    for zone_id, ids, label, annex_page in (
            ("bs_bga_kannengiesserstrasse", [1208730136, 1208730137], "Kannengiesserstrasse", "Anlage 2"),
            ("bs_bga_an_der_martinikirche", [1125907066, 1125907067], "An der Martinikirche", "Anlage 3"),
            ("bs_bga_jodutenstrasse_klint", [128210174], "Jodutenstrasse/Klint", "Anlage 4"),
            ("bs_bga_suedstrasse", [336899051], "Suedstrasse", "Anlage 5")):
        add(zone_id, "03101000", osm_union(ids, PARKING_BUFFER_M), "osm_fee_tags", BGA_URL,
            f"BgA car park '{label}' (Entgeltordnung B 660, {annex_page} map): the fee=yes OSM amenity=parking way(s) "
            f"{', '.join(str(i) for i in ids)} at the position drawn in {annex_page}, buffered {PARKING_BUFFER_M:.0f} m. "
            f"Overpass 2026-09-29 (OSM base {base['03101000']}).")

    # ---------------------------------------------------------------- Stadthalle concept: zone 132 and its fee islands
    ways = cc.named_ways(responses["03101000"])
    streets = ways[ways["highway"] != ""]
    islands = gpd.read_file(args.fee_islands).to_crs(cc.METRIC_CRS)
    for island in islands.itertuples():
        label = island.streets.replace(", ", "/")
        add(island.zone_id, "03101000", island.geometry, "street_list_buffer", BS_PARKRAUM_URL,
            f"Parkscheininsel {label} of the resident parking concept Stadthalle (named with its tariff on "
            f"parkraummanagement.php): the sections of the OSM way(s) {island.osm_way_ids} ({island.streets}) spanned "
            f"by the dark-blue 'Parkgebuehren' kerb lines of the concept plan 202308-Beschilderung-Bestand-PRMK-Stadthalle "
            f"({island.sections}), buffered {STREET_BUFFER_M:.0f} m and dissolved. Plan "
            f"georeferenced by a similarity fit on {island.cp_count} street junctions (OSM ways, Overpass 2026-09-29): "
            f"{island.metres_per_px:.3f} m/px, rotation {island.rotation_deg:.2f} deg, RMS {island.cp_rms_px:.1f} px = "
            f"{island.cp_rms_m:.1f} m; the blue pixels within 12 m of the named ways are projected onto them "
            f"(scripts/curation/parking_zones_2026/digitise_stadthalle_fee_islands.py). Cut out of "
            f"bs_resident_stadthalle_132 where they overlap. OSM base {base['03101000']}.")
    selected = streets[streets["name"].isin(STADTHALLE_STREETS) & streets.intersects(STADTHALLE_WINDOW)]
    missing = sorted(set(STADTHALLE_STREETS) - set(selected["name"]))
    if missing:
        raise SystemExit(f"Stadthalle streets missing in OSM: {missing}")
    stadthalle = cc.closed_union([g.intersection(STADTHALLE_WINDOW).buffer(STREET_BUFFER_M) for g in selected.geometry], 60.0)
    add("bs_resident_stadthalle_132", "03101000", stadthalle, "street_list_buffer", BS_PARKRAUM_URL,
        "Resident parking concept 'Stadthalle' (Bewohnerparkzone 132 on the city's Helmstedter Strasse plan): the streets "
        "marked 'Bewohnerparken Mischprinzip' on the concept plan 202308-Beschilderung-Bestand-PRMK-Stadthalle "
        "(Adolfstrasse, Bertramstrasse, Koernerstrasse, Marthastrasse, Gerstaeckerstrasse, Kleine Campestrasse, "
        "Lachmannstrasse, Villierstrasse, Kleine Leonhardstrasse) as OSM ways clipped to E 604550-605250 / N 5790650-"
        f"5791350 (EPSG:25832), buffered {STREET_BUFFER_M:.0f} m, dissolved, closed by 60 m, enclosed blocks filled; "
        "the fee islands Marthastrasse/Koernerstrasse and Gerstaeckerstrasse/Kleine Campestrasse are cut out (own "
        "zones). Street names read from the plan and matched to OSM by name (Overpass 2026-09-29, OSM base "
        f"{base['03101000']}); the main roads with unrestricted or short-term parking (Helmstedter Strasse, "
        "Leonhardstrasse, St. Leonhard) are not part of the zone.")

    # ---------------------------------------------------------------- TU Braunschweig campus areas (OSM outlines)
    for zone_id, ids, buffer_m, label, what in (
            ("tu_zentralcampus", [7972781, 27059288, 27092139], 0.0, "Zentralcampus",
             "OSM amenity=university ways 7972781 and 27059288 (both 'Technische Universitaet Braunschweig - "
             "Zentralbereich') and 27092139 ('Mensa 1')"),
            ("tu_campus_nord", [8011320], 0.0, "Campus Nord",
             "OSM amenity=university way 8011320 ('TU Braunschweig - Campus Nord')"),
            ("tu_campus_ost_beethovenstrasse", [8013640], 0.0, "Campus Ost Beethovenstrasse",
             "OSM amenity=university way 8013640 ('TU Braunschweig - Campus Ost')"),
            ("tu_campus_ost_langer_kamp", [8011319, 23967822], 0.0, "Campus Ost Langer Kamp",
             "OSM amenity=university ways 8011319 ('TU Braunschweig - Campus Ost - Langer Kamp') and 23967822 ('TU "
             "Braunschweig', the block south of Hans-Sommer-Strasse)"),
            ("tu_international_house", [134220301, 172658383], PARKING_BUFFER_M, "car parks at the International House",
             "the two TU-operated OSM amenity=parking ways 134220301 and 172658383 at Bueltenweg (International House, "
             "Bueltenweg 74, per its TU page), matching the two small detection zones west of Brucknerstrasse on the GB3 "
             f"map 'Campus Ost Langer Kamp', buffered {PARKING_BUFFER_M:.0f} m"),
            ("tu_forschungsflughafen", [264963238], 0.0, "Campus Forschungsflughafen",
             "OSM amenity=university way 264963238 ('TU Braunschweig - Campus Forschungsflughafen')")):
        add(zone_id, "03101000", osm_union(ids, buffer_m), "centre_approximation", TU_URL,
            f"TU Braunschweig {label}: {what}; Overpass 2026-09-29 (OSM base {base['03101000']}). The outline "
            "approximates the campus whose TU car parks are ticketed (the GB3 Parkbereiche maps show the camera "
            "detection zones inside it); it is not the detection-zone boundary itself.")

    # ---------------------------------------------------------------- Wolfsburg Innenstadt (OSM fee tags)
    wob_window = box(620900, 5808820, 622250, 5810290)
    wob = cc.paid_objects(responses["03103000"], wob_window)
    buffered = [g.buffer(STREET_BUFFER_M if kind == "street" else PARKING_BUFFER_M) for _, kind, g, _ in wob]
    add("wob_innenstadt", "03103000", cc.largest_parts(cc.closed_union(buffered, 120.0).intersection(wob_window), 5000.0),
        "osm_fee_tags", "https://www.wolfsburg.de/mobilitaetverkehr/parken",
        f"OSM fee evidence in the Wolfsburg city centre (the Parkleitsystem areas Norden, Mitte and Sueden of "
        f"wolfsburg.de): {len(wob)} objects (ticket/fee on-street ways buffered 15 m, fee=yes car parks buffered 5 m) "
        "inside the window E 620900-622250 / N 5808820-5810290 (EPSG:25832; Mittellandkanal/railway to Siemensstrasse), "
        "dissolved, closed by 120 m, enclosed blocks filled, parts < 5000 m2 dropped; Overpass 2026-09-29 (OSM base "
        f"{base['03103000']}). The Autostadt car parks north of the canal are outside the window (coverage register).")

    # ---------------------------------------------------------------- Salzgitter-Lebenstedt (street list of the city page)
    sz_window = box(590300, 5778780, 591450, 5779320)
    sz_names = ["Joachim-Campe-Straße", "Albert-Schweitzer-Straße", "Berliner Straße", "Marienbruchstraße",
                "Chemnitzer Straße", "Konrad-Adenauer-Straße"]
    sz_elements = responses["03102000"]["elements"]
    sz_geometries = cc.to_metric([cc.element_geometry(e) for e in sz_elements])
    sz_streets = [g.intersection(sz_window) for e, g in zip(sz_elements, sz_geometries)
                  if g is not None and e["type"] == "way" and e.get("tags", {}).get("name") in sz_names
                  and g.intersects(sz_window)]
    sz_lots = [g for _, _, g, _ in cc.paid_objects(responses["03102000"], sz_window)]
    add("sz_lebenstedt", "03102000", unary_union([g for g in sz_streets if not g.is_empty] + sz_lots).convex_hull.buffer(25.0),
        "street_list_buffer", "https://www.salzgitter.de/rathaus/fachdienste/tiefbau/parken.php",
        "Salzgitter-Lebenstedt centre: the streets with ticket machines named on the city page (Rathausvorplatz at "
        "Joachim-Campe-Strasse, Albert-Schweitzer-Strasse, Berliner Strasse, Marienbruchstrasse, Chemnitzer Strasse, car "
        "park at Konrad-Adenauer-Strasse) as OSM ways clipped to the centre window E 590300-591450 / N 5778780-5779320 "
        f"(EPSG:25832), plus the {len(sz_lots)} fee=yes car parks in that window; convex hull, buffered 25 m. ASSUMPTION: "
        "the page names the machine streets, not their extent; the window limits them to the Lebenstedt centre. Overpass "
        f"2026-09-29 (OSM base {base['03102000']}).")

    # ---------------------------------------------------------------- Peine and Helmstedt (hull of fee-tagged car parks)
    for zone_id, ags, anchor_name, anchor_ascii, radius_m, url, extra, extra_ascii in (
            ("pe_innenstadt", "03157006", "Breite Straße", "Breite Strasse", 400.0,
             "https://www.peine.de/de/rathaus/stadtportraet/verkehr-und-parken/parkplaetze/parkplatz-luisenstrasse.php",
             ["Luisenstraße"], "Luisenstrasse"),
            ("he_innenstadt", "03154028", "Markt", "Markt", 400.0,
             "https://www.stadt-helmstedt.de/fileadmin/user_upload/01_Rathaus/Virtuelle_Verwaltung/Ortsrecht_neu/"
             "Gebuehrenordnung_Parkscheinautomaten.pdf", [], "")):
        elements = responses[ags]["elements"]
        geoms = cc.to_metric([cc.element_geometry(e) for e in elements])
        anchor = unary_union([g for e, g in zip(elements, geoms) if g is not None and e["type"] == "way"
                              and e.get("tags", {}).get("name") == anchor_name
                              and e.get("tags", {}).get("highway") in ("pedestrian", "living_street", "residential")])
        centre = anchor.centroid
        near = [g for _, _, g, _ in cc.paid_objects(responses[ags], centre.buffer(radius_m))]
        extra_streets = [g for e, g in zip(elements, geoms) if g is not None and e["type"] == "way"
                         and e.get("tags", {}).get("name") in extra and g.distance(centre) < radius_m]
        add(zone_id, ags, unary_union(near + extra_streets).convex_hull.buffer(30.0), "osm_fee_tags", url,
            f"Town centre enclosed by its fee-tagged car parks: convex hull of the {len(near)} OSM fee=yes objects within "
            f"{radius_m:.0f} m of the centroid of the '{anchor_ascii}' way(s) (E {centre.x:.0f} / N {centre.y:.0f}, "
            "EPSG:25832)" + (f" and the street {extra_ascii}" if extra else "") + ", buffered 30 m; Overpass 2026-09-29 "
            f"(OSM base {base[ags]}). ASSUMPTION: destinations inside the ring of paid car parks park at them.")

    # ---------------------------------------------------------------- Goslar, Wolfenbuettel, Gifhorn (Overpass failed)
    links = cc.load_network_links(args.network)
    for zone_id, ags, window, names, url, what in (
            ("gs_altstadt_zone1", "03153017", box(597300, 5750600, 599400, 5752500),
             ["Rosentorstraße", "Petersilienstraße", "Bäckerstraße", "Marktstraße", "Hoher Weg", "Breite Straße",
              "Kornstraße"],
             "https://www.goslar.de/fileadmin/media-goslar/stadt/ortsrecht-satzungen/oeffentliche_sicherheit-ordnung/"
             "20250624_gebuehrenordnung_ueber_das_parken_an_parkscheinautomaten_in_der_stadt_goslar_vom_24.06.2025.pdf",
             "Goslar Altstadt, parking zone 1 ('the inner-city area with the central shopping and business streets' in the "
             "city's announcement of the 2026 fees, reported by regionalheute.de on 2025-12-30): convex hull of the "
             "car-network ways named Rosentorstrasse, Petersilienstrasse, Baeckerstrasse, Marktstrasse, Hoher Weg, Breite "
             "Strasse, Kornstrasse. ASSUMPTION: these old-town shopping streets stand for zone 1; the city publishes no "
             "street list"),
            ("wf_innenstadt", "03158037", box(604100, 5779600, 605700, 5781200),
             ["Rosenwall", "Schulwall", "Harztorwall", "Stadtmarkt", "Kornmarkt", "Holzmarkt", "Kommißstraße",
              "Breite Herzogstraße", "Lange Straße"],
             "https://www.stadtbetriebe-wf.de/parkhaeuser/rosenwall.html",
             "Wolfenbuettel Innenstadt between the old wall streets (Rosenwall with the garage Rosenwall, Schulwall with the "
             "garage Schulwall at the Loewentor, Harztorwall) and the central squares (Stadtmarkt, Kornmarkt, Holzmarkt, "
             "Kommissstrasse, Breite Herzogstrasse, Lange Strasse): convex hull of these car-network ways"),
            ("gf_innenstadt", "03151009", box(604300, 5815000, 605600, 5817000),
             ["Steinweg", "Schillerplatz", "Hindenburgstraße", "Torstraße", "Cardenap"],
             "https://psg-gifhorn.de/parken",
             "Gifhorn centre around the pedestrian zone Steinweg (Schillerplatz, Torstrasse) with the municipal car parks "
             "Hindenburgstrasse and Schottische Muehle (access Cardenap): convex hull of these car-network ways")):
        x0, y0, x1, y1 = window.bounds
        sub = links.cx[x0:x1, y0:y1]
        chosen = sub[sub["name"].isin(names)]
        missing = sorted(set(names) - set(chosen["name"]))
        if missing:
            raise SystemExit(f"{zone_id}: streets missing in the network: {missing}")
        add(zone_id, ags, chosen.geometry.union_all().convex_hull.buffer(40.0), "centre_approximation", url,
            what + ", buffered 40 m. Street geometry from the car network of the local MATSim scenario (eqasim-data/"
            "output_bs, 2026-04-29; OSM-derived links with osm:way:name) because the Overpass request of 2026-09-29 for "
            "this municipality failed with HTTP 504 and was not repeated (at most one request per municipality).")

    # ---------------------------------------------------------------- precedence, containment, output
    by_id = {z["zone_id"]: z for z in zones}
    order = PRECEDENCE + [z["zone_id"] for z in zones if z["zone_id"] not in PRECEDENCE]
    taken, trims = None, []
    for zone_id in order:
        zone = by_id[zone_id]
        geometry = zone["geometry"].buffer(0)
        if taken is not None and geometry.intersects(taken):
            overlap = geometry.intersection(taken).area
            if overlap > 0.5:
                geometry = geometry.difference(taken)
                trims.append((zone_id, round(overlap, 1)))
        geometry = cc.largest_parts(geometry.buffer(0), MINIMUM_PART_M2).simplify(SIMPLIFY_M, preserve_topology=True)
        zone["geometry"] = geometry
        taken = geometry if taken is None else taken.union(geometry)
    print("trimmed by precedence:", trims)
    with open(args.municipalities, "rb") as stream:
        municipalities = pickle.load(stream)
    ars = municipalities["commune_id"].astype(str)
    municipalities["ags"] = ars.str[:5] + ars.str[-3:]
    municipalities = municipalities.set_index("ags").to_crs(cc.METRIC_CRS)
    for zone in zones:
        inside = zone["geometry"].intersection(municipalities.loc[zone["_ags"], "geometry"]).area / zone["geometry"].area
        print(f"{zone['zone_id']:58s} {zone['geometry_source']:22s} {zone['geometry'].area:10.0f} m2  inside "
              f"{zone['_ags']}: {inside:.4f}")
        if inside < MINIMUM_INSIDE_SHARE:
            raise SystemExit(f"{zone['zone_id']} lies only {inside:.3f} inside its municipality {zone['_ags']}")
        zone["digitising_note"].encode("ascii")
    frame = gpd.GeoDataFrame([{k: v for k, v in z.items() if k != "_ags"} for z in zones], geometry="geometry",
                             crs=cc.METRIC_CRS)
    frame = frame[["zone_id", "geometry_source", "source_url", "source_date", "digitised_on", "digitising_note", "geometry"]]
    out = Path(args.out)
    # RFC 7946 allows foreign members; GDAL writes them at the top level and GeoJSON readers ignore them.
    members = json.dumps({"license": LICENSE, "attribution": ATTRIBUTION})
    frame.to_crs("EPSG:4326").to_file(out, driver="GeoJSON", COORDINATE_PRECISION=7, FOREIGN_MEMBERS_COLLECTION=members)
    print(f"written {out} with {len(frame)} zones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
