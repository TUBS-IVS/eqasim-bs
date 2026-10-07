"""The curated reading of the garages of the regional evidence package of 2026-10-07 (spec Amendment E1, issue #436).

Pure data for ``regional_garages.py``: for every garage of the package which of its tariff rules carries which part of the
garage tariff (the rule ROLES), what the columns do not express (other tiers, caps that are not applied, ignored rules) and why
a garage is not priced. The specifications hold rule ids and roles, never a tariff value that enters a column: the values are
read from the rules of the package by the roles, so a number can neither drift from its source nor be typed wrongly, and the
committed-data tests pin the numbers independently.

What the texts of this file may contain: free texts (the ``reason_text`` of an unpriced garage, the ``comment`` of a garage,
the notes of the candidates and of the monthly products) explain a decision to the reader and may quote published numbers;
a quoted number never enters a column, and no code reads these texts for a value. The one value that a specification types is
the window of ``window=("stated", start, end, quotation)``, a window that a source states in a page text which the package
keeps only as text, written with its quotation.

Rules of the reading (rulings R-4b-3, R-4b-4, R-4b-8, R-4b-9 and R-4b-10b, assumptions P3 to P7 of
``braunschweig.parking.garages.ASSUMPTIONS``):

* A tariff is encoded only where the published structure maps exactly to the garage columns: an optional first period (price
  for the first minutes), one rate per started billing unit with one fee window or, where the rate changes with the time of
  day, the time-of-day tiers, and an optional day cap. A first period whose price is the price of one billing unit is no
  first period (the rate alone is the same tariff). Anything else (a free period, a rate that changes with the duration, a
  continuation the source does not state, contradicting sources) stays listed and not priced with the reason code of
  ``garages.NOT_PRICED_REASONS``.
* Only a rule that the package marks ``preferred_for_current_use`` may name a role that sets a value (``rate``, ``tiers``,
  ``first``, ``first_equals_rate``, ``cap``, the used monthly product); a rule that is not preferred never sets a value and
  there is no waiver (ruling R-4b-8). A rule that sets no value may be named without the flag (``other_tiers``,
  ``other_caps``, ``ignored``, the ``evidence`` of an unpriced garage): the notes mark it as not preferred from the flag of the
  package, and every other rule of the facility that is not preferred is listed once as not used.
* Where the rate changes with the time of day (a morning, day, evening and night rate per started unit), the rules of the
  tiers are named in ``tiers`` (ASSUMPTION P6, ruling R-4b-10b). ``other_tiers`` names a night tariff that is no per-unit rate of
  the preferred rules (a flat night fee, an unresolved night tier): it is not charged (ASSUMPTION P3). ``other_caps`` names the
  caps that the single day-cap column cannot hold (a night cap, a maximum for day and night together): they are not applied and
  the day cap (or the 24-hour maximum where there is no day cap) applies to the whole stay (ASSUMPTION P7). A customer-specific
  tariff (a card, a retailer validation, a permit) is never encoded (``ignored``).
* The single-window form takes its fee window from the charging times of the rate rule (``window="rate"``), from a window
  that a source states in a page text (``window=("stated", ...)``) or, where no preferred rule states one, is 0 to 24 h
  (``window=None``, ASSUMPTION P5). A rate without a stated rounding is billed per started unit (ASSUMPTION P4, found from the
  rule).
* A garage whose operator page is its source names the operator with ``operator``; the step checks that the page is the
  operator's own (its name is in the host of the URL).
"""
from __future__ import annotations

#: Town key -> (8-digit AGS, name); the AGS of the municipality of the tariff rows of the same town.
TOWNS = {"bs": ("03101000", "Braunschweig"), "wob": ("03103000", "Wolfsburg"), "wf": ("03158037", "Wolfenbuettel"),
         "gf": ("03151009", "Gifhorn"), "he": ("03154028", "Helmstedt"), "pe": ("03157006", "Peine"),
         "sz": ("03102000", "Salzgitter"), "gs": ("03153017", "Goslar")}

#: The Braunschweig rule ids are the package source id plus a running number.
_BS = {"eiermarkt": "2605832cf112cb970b3af61a2266c3e553e7ae", "ring_center": "26057844400587384de83e9d214300551ccc28",
       "lange_strasse_nord": "2605823b1c49c8a9740ccce9895e8f8bac76e9", "magni": "260580365a948d771e7406382925d0a5920a81",
       "packhof": "26057992e51ffe6cbc1e55d2429314ad817eda", "schloss": "26057672fc365cf4d8873b664dff581246c9a0",
       "schuetzenstrasse": "2605772abc7ced1b535278bd3918c066383218", "wallstrasse": "260574fce0bcf3487a542f0f13e756d2dfead2",
       "wilhelmstrasse": "2605739b069e2cfe46fc2ffd9f4938088443c3", "forschungsflughafen": "4781292017fb49b091cc3066a17483fafbca28"}


def _bs(name: str, *numbers: int) -> tuple:
    return tuple(f"{_BS[name]}-{number}" for number in numbers)


#: One entry per garage, in the order of the dataset. Keys: ``garage_id``; ``town``; ``layer`` and ``feature`` (the
#: layer feature that gives name and position: (column, value)); ``attributes`` (a second layer feature of the same
#: garage, whose attributes complete the first: operator, geometry method); ``facility`` (the package facility record
#: with the capacity and the rule list); ``operator`` (optional: the operator whose own page is the source, or None where
#: the package's operator text is no operator); then either the roles of a priced garage (``rate`` with ``window`` or
#: ``tiers``; ``first`` or ``first_equals_rate``; ``cap``; ``other_tiers``, ``other_caps`` and ``ignored``) or ``reason`` (a
#: code of ``garages.NOT_PRICED_REASONS``), ``reason_text`` and ``evidence`` (the rule ids that show the reason); ``comment``.
GARAGE_SPECS = (
    # ------------------------------------------------------------------ Braunschweig: the PULP feed of the city
    {"garage_id": "bs_eiermarkt", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH004"),
     "facility": "BS_PH004", "operator": "Contipark", "tiers": _bs("eiermarkt", 36, 39), "first": _bs("eiermarkt", 35)[0],
     "cap": _bs("eiermarkt", 37)[0], "other_caps": _bs("eiermarkt", 38, 40),
     "comment": "The operator page gives the day tariff (first hour, hourly rate, day cap) and the night tariff with its cap "
                "and the 24-hour maximum; the city feed states the same first hour, rate and day rate without the times."},
    {"garage_id": "bs_forschungsflughafen", "town": "bs", "layer": "bs_parkhaeuser",
     "feature": ("source_id", "4781292017fb49b091cc3066a17483fafbca28"),
     "facility": "BS_SOURCE_4781292017fb49b091cc3066a17483fafbca28", "reason": "free_period",
     "reason_text": "free for the first 15 min, then 1.50 EUR per hour with no stated rounding, day ticket 15.00 EUR; the "
                    "package marks the treatment of the free 15 min on longer stays as not stated, so neither a free "
                    "first period nor a first period of 15 min can be encoded without inventing a value",
     "evidence": _bs("forschungsflughafen", 5, 6, 7),
     "comment": "The layer attribute tariff_rule_ids of this feature names the rules of the Ring-Center (both features "
                "share the facility id BS_None in the package); the rules of its own source id are used."},
    {"garage_id": "bs_lange_strasse_nord", "town": "bs", "layer": "bs_parkhaeuser",
     "feature": ("facility_id", "BS_PH010"), "facility": "BS_PH010", "rate": _bs("lange_strasse_nord", 10)[0],
     "cap": _bs("lange_strasse_nord", 11)[0], "window": None,
     "ignored": {_bs("lange_strasse_nord", 12)[0]: "bicycles and motorcycles park free, not a car tariff",
                 _bs("lange_strasse_nord", 55)[0]: "cinema customers with a validation (first hour 0.50 EUR), a customer "
                                                   "group the model cannot identify",
                 _bs("lange_strasse_nord", 56)[0]: "cinema customers with a validation, as the rule before"},
     "comment": "The standard tariff of the city feed."},
    {"garage_id": "bs_magni", "town": "bs", "layer": "bs_parkhaeuser",
     "feature": ("facility_id", "BS_PH001_DYNAMISCH_AUSLAUSTUNGSDATEN_DEAKTIVIERT"),
     "facility": "BS_PH001_DYNAMISCH_AUSLAUSTUNGSDATEN_DEAKTIVIERT", "operator": "Park und Tank",
     "rate": _bs("magni", 50)[0], "first_equals_rate": _bs("magni", 49)[0], "cap": _bs("magni", 51)[0], "window": None,
     "ignored": {_bs("magni", 52)[0]: "a lost ticket, not a stay"},
     "comment": "Operator page of Park und Tank; the city feed states the same amounts."},
    {"garage_id": "bs_packhof", "town": "bs", "layer": "bs_parkhaeuser",
     "feature": ("facility_id", "BS_PH007_DYNAMISCH_AUSLAUSTUNGSDATEN_DEAKTIVIERT"),
     "facility": "BS_PH007_DYNAMISCH_AUSLAUSTUNGSDATEN_DEAKTIVIERT", "operator": "Park und Tank",
     "rate": _bs("packhof", 46)[0], "first_equals_rate": _bs("packhof", 45)[0], "cap": _bs("packhof", 47)[0], "window": None,
     "ignored": {_bs("packhof", 48)[0]: "a lost ticket, not a stay"},
     "comment": "Operator page of Park und Tank; the city feed states the same amounts."},
    {"garage_id": "bs_ring_center", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("source_id", _BS["ring_center"]),
     "facility": "BS_None", "reason": "banded_tariff",
     "reason_text": "1.50 EUR per started hour for the first and second hour, then 2.00 EUR per started hour, day ticket "
                    "15.00 EUR: the rate changes after 2 h, which one rate after a first period cannot express",
     "evidence": _bs("ring_center", 19, 20, 21),
     "comment": "Not connected to the city's parking guidance system (feed text)."},
    {"garage_id": "bs_schloss", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH011"),
     "facility": "BS_PH011", "rate": _bs("schloss", 22)[0], "cap": _bs("schloss", 23)[0], "window": None,
     "ignored": {_bs("schloss", 24)[0]: "a lost ticket, not a stay"},
     "comment": "Shopping-centre garage Schloss-Arkaden, open to the public at the published tariff."},
    {"garage_id": "bs_schuetzenstrasse", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH006"),
     "facility": "BS_PH006", "rate": _bs("schuetzenstrasse", 25)[0], "cap": _bs("schuetzenstrasse", 26)[0], "window": None,
     "comment": "The feed states a temporarily reduced capacity (barriers); the tariff is the standard tariff."},
    {"garage_id": "bs_wallstrasse", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH003"),
     "facility": "BS_PH003", "operator": "Contipark", "rate": _bs("wallstrasse", 41)[0], "cap": _bs("wallstrasse", 42)[0],
     "window": "rate",
     "ignored": {_bs("wallstrasse", 43)[0]: "the P Card tariff, a customer card of the operator",
                 _bs("wallstrasse", 44)[0]: "the day cap of the P Card tariff"},
     "comment": "Operator page of Contipark. The city feed and the city overview state other amounts than the operator page "
                "(the rules of the feed that the package does not prefer are listed with their ids)."},
    {"garage_id": "bs_wilhelmstrasse", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH002"),
     "facility": "BS_PH002", "rate": _bs("wilhelmstrasse", 31)[0], "cap": _bs("wilhelmstrasse", 53)[0], "window": None,
     "ignored": {_bs("wilhelmstrasse", 32)[0]: "the tariff for customers of the shops and the theatre (first hour "
                                               "0.60 EUR) with a validation, a customer group the model cannot identify",
                 _bs("wilhelmstrasse", 33)[0]: "the continuation of that customer tariff",
                 _bs("wilhelmstrasse", 34)[0]: "the feed copy of the day rate; the city page gives it again as rules -53 "
                                               "and -54"},
     "comment": "The standard tariff of the city feed; the day rate comes from the city's car-park page."},
    # ------------------------------------------------------------------ Wolfsburg: the Geoviewer theme Parken of the city
    {"garage_id": "wob_suedkopf", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_SUEDKOPF"),
     "facility": "WOB_SUEDKOPF", "reason": "free_period",
     "reason_text": "free for the first 30 min, then 1.00 EUR per hour; the source does not state whether the free 30 min "
                    "are deducted from the charged duration",
     "evidence": ("WOB_SUEDKOPF_R01", "WOB_SUEDKOPF_R02"), "comment": "Shopping-centre garage Suedkopf-Center."},
    {"garage_id": "wob_rathaus", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_RATHAUS"),
     "facility": "WOB_RATHAUS", "tiers": ("WOB_RATHAUS_R04", "WOB_RATHAUS_R02"), "first": "WOB_RATHAUS_R01",
     "cap": "WOB_RATHAUS_R03", "other_caps": ("WOB_RATHAUS_R05",),
     "comment": "Aufbau-Gesellschaft Wolfsburg (Kunstmuseum / Rathaus). The day cap belongs to the daytime tariff; the package "
                "notes that the interaction of the day and night caps and the rounding are not defined."},
    {"garage_id": "wob_poststrasse", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_POST"),
     "attributes": ("region_parkhaeuser", ("facility_id", "WOB_POST")), "facility": "WOB_POST",
     "rate": "WOB_POST_R01_148", "cap": "WOB_POST_R02_149", "window": None, "other_tiers": ("WOB_POST_R03_150",),
     "other_caps": ("WOB_POST_R04_151",),
     "comment": "Saba. The night tariff is an unresolved rule (the operator states no billing unit): it sets no window and is "
                "not charged, so the fee window is 0-24 h; the 'Tagestarif' is read as the day maximum (its day boundary is "
                "not defined); entry only Mo-Fr 06:30-20:00, exit at any time. The municipal page lists another hourly rate "
                "than the operator."},
    {"garage_id": "wob_congresspark", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_CONGRESS"),
     "attributes": ("region_parkhaeuser", ("facility_id", "WOB_CONGRESS")), "facility": "WOB_CONGRESS",
     "rate": "WOB_CONGRESS_R01_142", "cap": "WOB_CONGRESS_R02_143", "window": None,
     "comment": "Saba; the day maximum is not defined as a rolling 24 h (package note)."},
    {"garage_id": "wob_rothenfelder", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_ROTHENFELDER"), "attributes": ("region_parkhaeuser", ("facility_id", "WOB_ROTHENFELDER")),
     "facility": "WOB_ROTHENFELDER", "tiers": ("WOB_ROTHENFELDER_R01_144", "WOB_ROTHENFELDER_R02_145"),
     "cap": "WOB_ROTHENFELDER_R04_147", "other_caps": ("WOB_ROTHENFELDER_R03_146",),
     "comment": "Saba; the 24-hour maximum is the only cap for day and night together and is held as the cap; the night cap "
                "lies below it; entry only Mo-Fr 06:30-20:00 and Sa 07:00-20:00."},
    {"garage_id": "wob_schillerstrasse", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_SCHILLER"), "facility": "WOB_SCHILLER", "reason": "banded_tariff",
     "reason_text": "1.00 EUR per hour for the first 2 h, then 1.50 EUR per hour, day maximum 15.00 EUR: the rate changes "
                    "after 2 h, which one rate after a first period cannot express",
     "evidence": ("WOB_SCHILLER_R01", "WOB_SCHILLER_R02", "WOB_SCHILLER_R03"), "operator": None,
     "comment": "The package states the operator as 'Saba per city; not listed among current Saba three facilities': the "
                "city lists Saba, the operator's own page does not list the garage, so no operator is taken."},
    {"garage_id": "wob_city_galerie", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_CITY_GALERIE"), "facility": "WOB_CITY_GALERIE", "rate": "WOB_CITYGALERIE_R02",
     "first_equals_rate": "WOB_CITYGALERIE_R01", "cap": "WOB_CITYGALERIE_R03", "window": None,
     "comment": "Shopping-centre garage City-Galerie (ECE), open to the public at the published tariff; special shopping "
                "Sundays have their own fees (not modelled)."},
    {"garage_id": "wob_designer_outlets", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_OUTLETS"), "facility": "WOB_OUTLETS", "reason": "banded_tariff",
     "reason_text": "free for 20 min, 1.00 EUR up to 2 h, 0.50 EUR per hour up to 4 h, 1.50 EUR per hour up to 7 h, 5.00 EUR "
                    "per hour from the 7th hour, no day cap: a rate that changes in four steps",
     "evidence": ("WOB_DESIGNEROUTLETS_R01", "WOB_DESIGNEROUTLETS_R02", "WOB_DESIGNEROUTLETS_R03",
                  "WOB_DESIGNEROUTLETS_R04", "WOB_DESIGNEROUTLETS_R05"),
     "comment": "Outlet-centre garage; the capacity of 1,000 is an approximate figure for all parking areas of the centre."},
    {"garage_id": "wob_phaeno", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_PHAENO"),
     "facility": "WOB_PHAENO", "tiers": ("WOB_PHAENO_R04", "WOB_PHAENO_R02"), "first": "WOB_PHAENO_R01",
     "cap": "WOB_PHAENO_R03", "other_caps": ("WOB_PHAENO_R05",),
     "comment": "Aufbau-Gesellschaft Wolfsburg (Nordkopf / phaeno); the operator's capacity 408 supersedes the municipal 400."},
    # ------------------------------------------------------------------ the other towns: operator pages
    {"garage_id": "wf_schulwall", "town": "wf", "layer": "region_parkhaeuser", "feature": ("facility_id", "WF_SCHULWALL"),
     "facility": "WF_SCHULWALL",
     "tiers": ("WF_SCHULWALL_R02_153", "WF_SCHULWALL_R01_152", "WF_SCHULWALL_R03_154", "WF_SCHULWALL_R04_155"),
     "ignored": {"WF_SCHULWALL_R05_156": "the tariff of Sundays and public holidays from 08 to 22 h; the model's average "
                                         "weekday, spec D1, has none"},
     "comment": "Stadtbetriebe Wolfenbuettel (operator page, state of September 2026): morning and evening, day and night "
                "tiers per started half hour, the night tier also on Sundays. The operator publishes no day maximum, so none "
                "is encoded; the operator's map marker lies about 78 m north of the tourism marker."},
    {"garage_id": "wf_rosenwall", "town": "wf", "layer": "region_parkhaeuser", "feature": ("facility_id", "WF_ROSENWALL"),
     "facility": "WF_ROSENWALL",
     "tiers": ("WF_ROSENWALL_R02_158", "WF_ROSENWALL_R01_157", "WF_ROSENWALL_R03_159", "WF_ROSENWALL_R04_160"),
     "comment": "Stadtbetriebe Wolfenbuettel (operator page, state of September 2026); 127 short-stay and 45 permanent spaces. "
                "The operator publishes no day maximum."},
    {"garage_id": "gf_hindenburgstrasse", "town": "gf", "layer": "region_parkhaeuser",
     "feature": ("facility_id", "GF_HINDENBURG"), "facility": "GF_HINDENBURG", "rate": "GF_HINDENBURG_R01_161",
     "cap": "GF_HINDENBURG_R02_162", "window": None,
     "ignored": {"GF_HINDENBURG_R03_163": "one hour credited to customers of a fashion store, a customer group the model "
                                          "cannot identify",
                 "GF_HINDENBURG_R04_164": "free for holders of a disability permit, a group the model cannot identify"},
     "comment": "Parkraum- und Schwimmbadgesellschaft Stadt Gifhorn (PSG); the capacity is approximate."},
    {"garage_id": "he_marktpassage", "town": "he", "layer": "region_parkhaeuser",
     "feature": ("facility_id", "HE_MARKTPASSAGE"), "facility": "HE_MARKTPASSAGE", "rate": "HE_MARKTPASSAGE_R01_165",
     "cap": None, "window": "rate",
     "comment": "Garage of the Marktpassage. The operator publishes no billing step and no day maximum; the charging times "
                "equal the opening hours Mo-Sa 06:00-20:00 (the rule carries them as charging times)."},
    {"garage_id": "he_edelhoefe", "town": "he", "layer": "region_parkhaeuser", "feature": ("facility_id", "HE_EDELHOEFE"),
     "facility": "HE_EDELHOEFE", "rate": "HE_EDELHOEFE_R01_166", "cap": "HE_EDELHOEFE_R02_167",
     "window": ("stated", "00:00", "24:00",
                "the city brochure of December 2024 gives the garage the charging time 'Oeffnungszeit: 24 Stunden' in its "
                "column of fee-liable times (gebuehrenpflichtige Zeit), and the ordinance of 2018-12-18 sec. 2(1) opens the "
                "garage from 00.00 to 24.00"),
     "comment": "Garage of the city of Helmstedt, tariff of the city brochure of December 2024. The ordinance of 2018-12-18 "
                "(retrieved 2026-09-29) states another structure: 0.50 EUR for the first 30 min, 1.00 EUR up to 1 h, 2.00 "
                "EUR up to 2 h, 0.50 EUR per further started hour, at most 8.00 EUR, and the garage's notice board decides; "
                "the 2018 text is the older source and is not encoded."},
    {"garage_id": "pe_werderstrasse", "town": "pe", "layer": "region_parkhaeuser", "feature": ("facility_id", "PE_WERDER"),
     "facility": "PE_WERDER", "reason": "banded_tariff",
     "reason_text": "0.20 EUR for the first 30 min, 0.80 EUR in total up to 1 h, then 0.40 EUR per half hour up to 5 h, "
                    "4.00 EUR in total for 5 to 24 h: the first half hour is a band of its own, which a first period plus "
                    "one rate cannot express (a first period of 60 min at 0.80 EUR would overprice a short stay)",
     "evidence": ("PE_WERDER_R01_168", "PE_WERDER_R02_169", "PE_WERDER_R03_170", "PE_WERDER_R04_171"),
     "comment": "Stadtwerke Peine; the position is the OSM feature centre via mapcarta."},
    {"garage_id": "pe_wallstrasse", "town": "pe", "layer": "region_parkhaeuser", "feature": ("facility_id", "PE_WALL"),
     "facility": "PE_WALL", "reason": "banded_tariff",
     "reason_text": "0.20 EUR for the first 30 min, 0.80 EUR in total up to 1 h, then 0.40 EUR per half hour up to 5 h, "
                    "4.00 EUR in total for 5 to 24 h: the first half hour is a band of its own, which a first period plus "
                    "one rate cannot express (a first period of 60 min at 0.80 EUR would overprice a short stay)",
     "evidence": ("PE_WALL_R01_172", "PE_WALL_R02_173", "PE_WALL_R03_174", "PE_WALL_R04_175"),
     "comment": "Stadtwerke Peine; daily 07:00-23:15 (longer after events in the Forum)."},
    {"garage_id": "sz_brawo_carree", "town": "sz", "layer": "region_parkhaeuser", "feature": ("facility_id", "SZ_BRAWO"),
     "facility": "SZ_BRAWO", "reason": "banded_tariff",
     "reason_text": "1.50 EUR per started hour for the first 6 h, from the 7th started hour a published day total of 15.00 "
                    "EUR, which is a jump and no cap of the hourly rate (a cap at 15.00 EUR would underprice stays of 7 to "
                    "9 h)",
     "evidence": ("SZ_BRAWO_R01_176", "SZ_BRAWO_R02_177"),
     "comment": "Shopping-centre garage BRAWO Carree; customers of two retailers park 60 or 90 min free with a validation "
                "(rules -178 and -179); the capacity counts the regular spaces only; the position is an OSM node via "
                "mapcarta."},
    {"garage_id": "gs_achtermann", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_ACHTERMANN"),
     "facility": "GS_ACHTERMANN", "reason": "conflicting_sources",
     "reason_text": "1.50 EUR per hour for the first 3 h and 0.80 EUR per hour after that contradict the separately "
                    "published totals of 15.00 EUR for 12 h and 25.00 EUR for 24 h; the package marks the rule set "
                    "'conflicting_primary_publication_not_calculation_ready' and asks for the operator's confirmation, so "
                    "no tariff is encoded (spec Amendment D1)",
     "evidence": ("GS_ACHTERMANN_REV_01", "GS_ACHTERMANN_REV_02", "GS_ACHTERMANN_REV_03", "GS_ACHTERMANN_REV_04"),
     "comment": "The position is the city's point of 2018."},
    {"garage_id": "gs_ca", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_CA"),
     "facility": "GS_CA", "reason": "banded_tariff",
     "reason_text": "1.50 EUR per hour for the first 3 h, then 0.80 EUR per half hour, 25.00 EUR for 24 h: the rate changes "
                    "after 3 h, which one rate after a first period cannot express",
     "evidence": ("GS_CA_REV_01", "GS_CA_REV_02", "GS_CA_REV_03"),
     "comment": "Tariff of the tourism page, not confirmed by the operator (package status)."},
    {"garage_id": "gs_galeria", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_GALERIA"),
     "facility": "GS_GALERIA", "reason": "incomplete_tariff",
     "reason_text": "1.50 EUR for 1 h and 15.00 EUR for 24 h are published, but not how a stay is billed after the first "
                    "hour (repetition and rounding are not stated), so the continuation would be invented",
     "evidence": ("GS_GALERIA_REV_01", "GS_GALERIA_REV_02"),
     "comment": "Tariff of the tourism page; the position is the city's point of 2018."},
    {"garage_id": "gs_charley_jacob_strasse", "town": "gs", "layer": "gos_parking_locations",
     "feature": ("facility_id", "GOS_POINT_4"), "facility": "GOS_POINT_4", "reason": "no_published_tariff",
     "reason_text": "the city's service of 2018 lists a Parkhaus at this point (Nutzung Parkhaus); the package holds no "
                    "tariff rule for it",
     "evidence": (), "comment": "Listed because spec Amendment E1 lists a garage without a sourced tariff; current existence "
                                "not verified (the layer is a snapshot of 2018-11-22)."},
)

#: Garage ids of the dataset, in order.
GARAGE_IDS = tuple(spec["garage_id"] for spec in GARAGE_SPECS)

#: Every monthly or 30-day product the sources publish (spec Amendment D2, ruling R-D2-a). ``rule`` is the package rule (None
#: for a product that is not in the package); ``garage_id`` or ``zone_ids`` say where a used product enters (the dataset's
#: ``monthly_eur`` or the tariff rows' ``commuter_day_eur``); ``reason`` is a code of ``garage_qa.MONTHLY_NOT_USED_REASONS``
#: for a product that is not used; ``why`` adds a sentence to the generated note. A garage with a used product has exactly
#: one, the cheapest. A product that is publicly purchasable is used even where it is limited by a waiting list (the
#: Wolfsburg Dauerparker products of the Aufbau-Gesellschaft and of the City-Galerie); a permit offer for a closed group of a
#: fixed small number of places is not (Goslar).
MONTHLY_PRODUCTS = (
    {"record_id": "monthly_wob_rathaus_tag", "rule": "WOB_RATHAUS_MONTHLY_1", "garage_id": "wob_rathaus", "decision": "used"},
    {"record_id": "monthly_wob_rathaus_tag_nacht", "rule": "WOB_RATHAUS_MONTHLY_2", "garage_id": "wob_rathaus",
     "decision": "not_used", "reason": "not_the_cheapest"},
    {"record_id": "monthly_wob_phaeno_tag", "rule": "WOB_PHAENO_MONTHLY_1", "garage_id": "wob_phaeno", "decision": "used"},
    {"record_id": "monthly_wob_phaeno_tag_nacht", "rule": "WOB_PHAENO_MONTHLY_2", "garage_id": "wob_phaeno",
     "decision": "not_used", "reason": "not_the_cheapest"},
    {"record_id": "monthly_wob_city_galerie_mo_fr", "rule": "WOB_CITY_GALERIE_MONTHLY_1", "garage_id": "wob_city_galerie",
     "decision": "used"},
    {"record_id": "monthly_wob_city_galerie_mo_sa", "rule": "WOB_CITY_GALERIE_MONTHLY_2", "garage_id": "wob_city_galerie",
     "decision": "not_used", "reason": "not_the_cheapest"},
    {"record_id": "monthly_wob_designer_outlets", "rule": "WOB_OUTLETS_MONTHLY_1", "garage_id": "wob_designer_outlets",
     "decision": "used"},
    {"record_id": "monthly_wob_hauptbahnhof_24h", "rule": "WOB_HAUPTBAHNHOF_MONTHLY_1", "garage_id": None,
     "municipality_ags": "03103000", "subject": "Wolfsburg Parkdeck Hauptbahnhof (Contipark / DB BahnPark): Dauerparken Mo-So 24 h",
     "decision": "not_used", "reason": "garage_not_listed",
     "why": "the deck is a station BahnPark car park and no garage of the dataset (ruling R-4b-9, candidate "
            "candidate_wob_hauptbahnhof)"},
    {"record_id": "monthly_wob_hauptbahnhof_oepnv", "rule": "WOB_HAUPTBAHNHOF_MONTHLY_2", "garage_id": None,
     "municipality_ags": "03103000", "subject": "Wolfsburg Parkdeck Hauptbahnhof (Contipark / DB BahnPark): product for "
                                                "public-transport customers",
     "decision": "not_used", "reason": "restricted_customer_group",
     "why": "the deck is a station BahnPark car park and no garage of the dataset either (ruling R-4b-9)"},
    {"record_id": "monthly_wf_rosenwall", "rule": "WF_ROSENWALL_MONTHLY_1", "garage_id": "wf_rosenwall", "decision": "used"},
    {"record_id": "monthly_wf_schulwall_comfort_25", "rule": "WF_SCHULWALL_MONTHLY_1", "garage_id": "wf_schulwall",
     "decision": "not_used", "reason": "no_fixed_price"},
    {"record_id": "monthly_pe_werderstrasse", "rule": "PE_WERDER_MONTHLY_1", "garage_id": "pe_werderstrasse",
     "decision": "used"},
    {"record_id": "monthly_pe_wallstrasse", "rule": "PE_WALL_MONTHLY_1", "garage_id": "pe_wallstrasse", "decision": "used"},
    {"record_id": "monthly_bs_zone_ib_30_day", "rule": "bs_zone_ib_30D", "zone_ids": ("bs_zone_ib",), "decision": "used",
     "municipality_ags": "03101000", "subject": "Braunschweig zone Ib 30-day ticket (phone parking only)",
     "note": "ParkGO sec. 1(2) of 2022-12-20 as amended 2025-11-04, zone Ib only: 30-day ticket 79.00 EUR, valid for 30 "
             "consecutive calendar days from the time of purchase, sold by phone parking only, no entitlement to a space; "
             "commuter_day_eur of bs_zone_ib is this amount over 21 working days (ASSUMPTION P2)"},
    {"record_id": "monthly_bs_zone_ib_7_day", "rule": None, "zone_ids": (), "decision": "not_used",
     "reason": "not_monthly_or_30_day", "municipality_ags": "03101000", "amount_eur": 29.0,
     "subject": "Braunschweig zone Ib 7-day ticket", "evidence": ("bs_zone_ib_BASE",),
     "note": "ParkGO sec. 1(2) of 2022-12-20 as amended 2025-11-04: 7-day ticket 29.00 EUR, valid for 7 consecutive calendar "
             "days; spec Amendment D2 takes monthly and 30-day products"},
    {"record_id": "monthly_tu_member_month_ticket", "rule": None,
     "zone_ids": ("tu_zentralcampus", "tu_campus_nord", "tu_campus_ost_beethovenstrasse", "tu_campus_ost_langer_kamp",
                  "tu_forschungsflughafen", "tu_campus_volkmaroder_strasse"), "decision": "used",
     "municipality_ags": "03101000", "amount_eur": 10.0, "subject": "TU Braunschweig month ticket for university members",
     "evidence": ("TU Parkordnung (Stand 2026-06-03) sec. 6(2)",),
     "note": "10 EUR per month ticket for university members (the group the campus member rule identifies: work and "
             "education on campus, ruling R-D2-a), https://www.tu-braunschweig.de/fileadmin/Redaktionsgruppen/Verwaltung/"
             "GB3/Parkraumbewirtschaftung/16-06-2026_Parkordnung.pdf, retrieved 2026-09-29; it competes with the member day "
             "ticket of 3.50 EUR (spec Amendment A4)"},
    {"record_id": "monthly_cl_tiefgarage_rathaus", "rule": "cl_rental_cl_rathaus", "garage_id": None,
     "decision": "not_used", "reason": "no_coordinates", "municipality_ags": "03153018",
     "subject": "Clausthal-Zellerfeld garage Am Rathaus, rental of a space",
     "note": "rental of a space in the garage Am Rathaus, minimum contract 12 months; the package holds no coordinates of the "
             "garage (it asks to georeference the entrance) and Clausthal-Zellerfeld has no zone, so no row can carry it"},
    {"record_id": "monthly_gs_pendler_kaiserpfalz_sued", "rule": "goslar_pendler_REV_01", "garage_id": None,
     "decision": "not_used", "reason": "capacity_limited_permits", "municipality_ags": "03153017",
     "subject": "Goslar commuter permit Kaiserpfalz Sued, 40 EUR per month",
     "note": "the city re-introduced its commuter car parks on 2026-05-01; the permit offer is limited to a fixed small number "
             "of places (30 permits, ruling R-D2-a) and to people who work in the centre and live elsewhere"},
    {"record_id": "monthly_he_edelhoefe_2018_5_days", "rule": None, "garage_id": None, "decision": "not_used",
     "reason": "outdated_source", "municipality_ags": "03154028", "amount_eur": 45.0,
     "subject": "Helmstedt garage Edelhoefe, permanent parkers 5 days a week (Mo-Fr)",
     "evidence": ("Entgelt- und Benutzungsordnung fuer das Parkhaus Edelhoefe of 2018-12-18, sec. 3(2)(a)",
                  "gaps.json: he_edel_service_blocked"),
     "note": "the ordinance of 2018-12-18 (retrieved 2026-09-29) states 45.00 EUR per month; the garage's tariff has risen "
             "since (the city brochure of December 2024 states 0.60 EUR per 30 min where the ordinance states 0.50 EUR), the "
             "package found the current city service page blocked (HTTP 403) and did not promote the third-party amounts "
             "45 and 55, so no current monthly amount is known"},
    {"record_id": "monthly_he_edelhoefe_2018_7_days", "rule": None, "garage_id": None, "decision": "not_used",
     "reason": "outdated_source", "municipality_ags": "03154028", "amount_eur": 55.0,
     "subject": "Helmstedt garage Edelhoefe, permanent parkers 7 days a week",
     "evidence": ("Entgelt- und Benutzungsordnung fuer das Parkhaus Edelhoefe of 2018-12-18, sec. 3(2)(b)",
                  "gaps.json: he_edel_service_blocked"),
     "note": "the ordinance of 2018-12-18 (retrieved 2026-09-29) states 55.00 EUR per month; see the 5-day product"},
)

#: Why a record of the package is no garage of the dataset although it is a garage or car park. Keys: ``record_id``,
#: ``town``, ``subject``, ``facility`` (the facility id in facilities.json, or None where the package holds no facility
#: record), ``reason`` (a code of ``garage_qa.CANDIDATE_REASONS``), ``note`` and, for a candidate without a facility record, the
#: ``evidence`` (the page that states it). A facility that has a feature in a garage layer is decided here and not covered by
#: ``GARAGE_SPECS``; a candidate with the reason no_coordinates has no such feature.
PACKAGE_CANDIDATES = (
    {"record_id": "candidate_bs_lange_strasse_sued", "town": "bs", "subject": "Parkhaus Lange Strasse Sued",
     "facility": "BS_ADDITIONAL_1", "reason": "no_coordinates",
     "note": "listed on the city's car-park page with a tariff but without coordinates in the package (no geometry is "
             "invented)"},
    {"record_id": "candidate_bs_steinstrasse", "town": "bs", "subject": "Parkhaus Steinstrasse (Am Bankplatz)",
     "facility": "BS_ADDITIONAL_2", "reason": "no_coordinates",
     "note": "listed on the city's car-park page with a tariff but without coordinates in the package (no geometry is "
             "invented)"},
    {"record_id": "candidate_he_stobenstrasse", "town": "he", "subject": "Stobenstrasse (Parkdeck)", "facility": "HE_STOBEN",
     "reason": "no_coordinates",
     "note": "a municipal parking deck of the city brochure of December 2024 with a tariff but without coordinates in the "
             "package (no geometry is invented)"},
    {"record_id": "candidate_he_groepern_tiefgarage", "town": "he", "subject": "Groepern (Tiefgarage), 118 spaces",
     "facility": None, "reason": "no_coordinates",
     "evidence": "city brochure Parken in Helmstedt of December 2024, table 'Innerhalb des Stadtringes' (he_parken.pdf in "
                 "the package; https://www.stadt-helmstedt.de/fileadmin/user_upload/02_Kultur/pdf_Broschueren/"
                 "Parken_in_Helmstedt.pdf)",
     "note": "a municipal garage of the city brochure: 118 spaces, ticket, 0.70 EUR for the first hour, each further 0.30 EUR "
             "(the unit of 'jede weitere' is not stated), no maximum stay, charging time 'Oeffnungszeit: 24 Stunden'; the "
             "package holds neither a facility record nor coordinates for it (no geometry is invented), so the garage "
             "cannot be listed"},
    {"record_id": "candidate_cl_tiefgarage_rathaus", "town": "cl", "subject": "Clausthal-Zellerfeld, Tiefgarage Am Rathaus",
     "facility": "cl_rathaus", "reason": "no_coordinates",
     "note": "the package asks to georeference the entrance; Clausthal-Zellerfeld has no zone and no coordinates here"},
    {"record_id": "candidate_se_parkhaus", "town": "se", "subject": "Seesen, Parkhaus Bahnhofstrasse", "facility": "se_parkhaus",
     "reason": "outside_source_list",
     "note": "a private garage, free on the upper levels and for 2 h on the lower level (fee status free_conditional): not in "
             "the towns that spec Amendment E1 names and no tariff the columns could express"},
    {"record_id": "candidate_wob_hauptbahnhof", "town": "wob",
     "subject": "Wolfsburg Parkdeck Hauptbahnhof P1 (Contipark / DB BahnPark)", "facility": "WOB_HAUPTBAHNHOF",
     "reason": "station_bahnpark",
     "note": "a station deck of DB BahnPark, operated by Contipark: ruling R-4b-9 excludes the station BahnPark car parks in "
             "both cities, like the Braunschweig station car parks of the directory, and the coverage register lists the DB "
             "BahnPark station car parks of Wolfsburg as excluded; the package's garage layer holds the deck, so it is decided "
             "here; its monthly products are recorded and not used"},
    {"record_id": "candidate_wf_parkpalette_karlstrasse", "town": "wf", "subject": "Wolfenbuettel, Parkpalette Karlstrasse "
                                                                                 "(157 spaces)",
     "facility": None, "reason": "dauerparker_only",
     "evidence": "https://www.stadtbetriebe-wf.de/parkhaeuser.html (overview page of the operator, retrieved 2026-09-29)",
     "note": "the operator states that the Parkpalette is currently available to long-term parkers only (momentan nur "
             "Dauerparkern): a closed group of long-term renters, so no garage option; owner decision 2026-10-07: their free "
             "or contract parking is covered by the model's averaged free-parking share"},
    {"record_id": "candidate_wf_neue_strasse", "town": "wf", "subject": "Wolfenbuettel, Parkplatzanlage Neue Strasse",
     "facility": None, "reason": "dauerparker_only",
     "evidence": "https://www.stadtbetriebe-wf.de/parkhaeuser.html (overview page of the operator, retrieved 2026-09-29)",
     "note": "the operator states that the car park is currently available to long-term parkers only (momentan nur "
             "Dauerparkern): a closed group of long-term renters, so no garage option; owner decision 2026-10-07: their free "
             "or contract parking is covered by the model's averaged free-parking share"},
)
#: AGS of the towns of ``PACKAGE_CANDIDATES`` that are not in ``TOWNS``.
OTHER_TOWNS = {"cl": ("03153018", "Clausthal-Zellerfeld"), "se": ("03153012", "Seesen")}

#: The decisions on the entries of the Braunschweig car-park directory (ASCII names as ``regional_garages`` transliterates
#: them): reason code of ``garage_qa.CANDIDATE_REASONS`` and a note. A directory entry that is not listed stops the step.
DIRECTORY_DECISIONS = {
    "Kurzzeitparkplatz Lilienthalplatz": ("customer_regime", "airport short-stay car park; the coverage register lists it as "
                                                             "excluded (customer and visitor regime)"),
    "Parkplatz Grosser Hof": ("zone_street_product", "ParkGO zone 1 car park (maximum stay 3 h): the street product of "
                                                     "its zone is its tariff"),
    "Parkplatz Hauptbahnhof Nord": ("station_bahnpark", "station car park of a private operator (BahnCard discount, "
                                                        "75 EUR per month for BahnCard holders): rulings R-4b-4 and R-4b-9"),
    "Parkplatz Hauptbahnhof Sued": ("station_bahnpark", "station car park of a private operator: rulings R-4b-4 and R-4b-9"),
    "Parkplatz Markthalle": ("bga_zone", "BgA car park, the zone bs_bga_markthalle"),
    "Parkplatz Nimesstrasse": ("zone_street_product", "ParkGO zone 1 car park (maximum stay 3 h): the street product of "
                                                      "its zone is its tariff"),
    "Parkplatz Suedstrasse": ("bga_zone", "BgA car park, the zone bs_bga_suedstrasse"),
    "Parkplatz Werder": ("zone_street_product", "ParkGO zone 1 car park: the street product of its zone is its tariff"),
    "Stellplaetze am Loewenwall": ("zone_street_product", "ParkGO tariff zone 1 spaces: the street product of their zone is "
                                                         "their tariff"),
}
