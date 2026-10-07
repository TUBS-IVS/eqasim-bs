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

Rules of the reading (rulings R-4b-3, R-4b-4, R-4b-8, R-4b-9, R-4b-10b, R-4b-11 and R-4b-12, assumptions P3 to P8 of
``braunschweig.parking.garages.ASSUMPTIONS``):

* A tariff is encoded only where the published structure maps exactly to the garage columns: an optional first period (price
  for the first minutes, with the clock window its rule states, ruling R-4b-12), one rate per started billing unit with one
  fee window or, where the rate changes with the time of day, the time-of-day tiers or, where the price follows the duration
  of the stay, the duration bands (ruling R-4b-11), and an optional day cap. A first period whose price is the price of one
  billing unit is no first period (the rate alone is the same tariff). Anything else (a free period whose deduction is not
  stated, a continuation the source does not state, contradicting sources) stays listed and not priced with the reason code
  of ``garages.NOT_PRICED_REASONS``.
* Only a rule that the package marks ``preferred_for_current_use`` may name a role that sets a value (``rate``, ``tiers``,
  ``bands``, ``first``, ``first_equals_rate``, ``cap``, the used monthly product); a rule that is not preferred never sets a
  value and there is no waiver (ruling R-4b-8). A rule that sets no value may be named without the flag (``other_tiers``,
  ``other_caps``, ``ignored``, the ``evidence`` of an unpriced garage): the notes mark it as not preferred from the flag of the
  package, and every other rule of the facility that is not preferred is listed once as not used.
* Where the rate changes with the time of day (a morning, day, evening and night rate per started unit), the rules of the
  tiers are named in ``tiers`` (ASSUMPTION P6, ruling R-4b-10b). ``other_tiers`` names a night tariff that is no per-unit rate of
  the preferred rules (a flat night fee, an unresolved night tier): it is not charged (ASSUMPTION P3). ``other_caps`` names the
  caps that the single day-cap column cannot hold (a night cap, a maximum for day and night together): they are not applied and
  the day cap (or the 24-hour maximum where there is no day cap) applies to the whole stay (ASSUMPTION P7). A customer-specific
  tariff (a card, a retailer validation, a permit) is never encoded (``ignored``).
* Where the price follows the elapsed duration of the stay (a free stretch, a total for a stretch, a rate per started unit
  from a minute on), the rules of the bands are named in ``bands`` (ASSUMPTION P8, ruling R-4b-11); each band rule is a
  ``free``, ``duration_total``, ``published_day_total_from_duration`` or ``increment`` rule with its own elapsed range, and the
  published cap or 24-hour price is the ``cap``. A banded specification has no ``first`` period.
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
#: the package's operator text is no operator); then either the roles of a priced garage (``rate`` with ``window``, or
#: ``tiers``, or ``bands`` with ``window``; ``first`` or ``first_equals_rate``, not with ``bands``; ``cap``; ``other_tiers``,
#: ``other_caps`` and ``ignored``) or ``reason`` (a code of ``garages.NOT_PRICED_REASONS``), ``reason_text`` and ``evidence``
#: (the rule ids that show the reason); ``comment``.
GARAGE_SPECS = (
    # ------------------------------------------------------------------ Braunschweig: the PULP feed of the city
    {"garage_id": "bs_eiermarkt", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH004"),
     "facility": "BS_PH004", "operator": "Contipark", "tiers": _bs("eiermarkt", 36, 39), "first": _bs("eiermarkt", 35)[0],
     "cap": _bs("eiermarkt", 37)[0], "other_caps": _bs("eiermarkt", 38, 40),
     "comment": "The operator page gives the day tariff (first hour, hourly rate, day cap) and the night tariff with its cap "
                "and the 24-hour maximum; the city feed states the same first hour, rate and day rate without the times."},
    {"garage_id": "bs_forschungsflughafen", "town": "bs", "layer": "bs_parkhaeuser",
     "feature": ("source_id", "4781292017fb49b091cc3066a17483fafbca28"),
     "facility": "BS_SOURCE_4781292017fb49b091cc3066a17483fafbca28", "supplement": "BS_FORSCHUNGSFLUGHAFEN",
     "followup": "BS_FORSCHUNGSFLUGHAFEN", "grace": (_bs("forschungsflughafen", 5)[0], "BS_FORSCHUNGSFLUGHAFEN_REGULAR"),
     "cap": "BS_FORSCHUNGSFLUGHAFEN_REGULAR:cap", "window": None,
     "p10_basis": "the general FAQ of the parking provider that the operator's site page links "
                  "(https://www.mh-parkservice.com/faq-wissen) describes a grace period without a deduction when it is exceeded; "
                  "the FAQ is no statement of this site, and the supplement's field decision on the deduction stays open "
                  "(open_with_general_operator_indication); the owner decided on 2026-10-07 to read the 15 free minutes as a grace "
                  "period (spec E12)",
     "ignored": {_bs("forschungsflughafen", 6)[0]: "the same rate as the supplement rule used (the feed states no rounding there; "
                                                    "the operator page states a started hour)",
                 _bs("forschungsflughafen", 7)[0]: "the day ticket 15.00 EUR of the feed text; the visible operator block states "
                                                    "18.00 EUR and the 15 EUR blocks of the operator page are CSS-hidden "
                                                    "templates, so the 18.00 EUR of the supplement rule is used",
                 _bs("forschungsflughafen", 8)[0]: "the week ticket 80.00 EUR of the feed text (a CSS-hidden template on the "
                                                    "operator page), not a short-stay tariff",
                 _bs("forschungsflughafen", 9)[0]: "a lost ticket, not a stay",
                 "BS_FORSCHUNGSFLUGHAFEN_WEEK_PREBOOKED": "a separate product (99.00 EUR for seven days, prebooked and "
                                                           "registered), no monthly or 30-day product and no cap of the short-stay "
                                                           "tariff; recorded in the QA table",
                 "BS_FORSCHUNGSFLUGHAFEN_FOLLOWUP": "the follow-up record of the same operator page (1.50 EUR per hour, ceil, "
                                                     "15 free minutes, 18.00 EUR; the cap period stays unspecified)",
                 "BS_FORSCHUNGSFLUGHAFEN_FOLLOWUP:cap": "the cap of that follow-up record, the same 18.00 EUR"},
     "qa_comment": "the 18.00 EUR day maximum of the visible operator block is used (the 15.00 EUR blocks are hidden templates); "
                   "the 99.00 EUR seven-day prebooked product is a separate product and not used",
     "comment": "PRICED by the supplement package (ruling R-4b2-2). Rate 1.50 EUR per started 60 min (rounding 'ceil' of the "
                "supplement, resolved) with the 15 free minutes read as a grace period (ASSUMPTION P10, owner decision of spec "
                "E12): bands '0-15 free; 15-60 total 1.50; 60- 1.50/60'. Day maximum 18.00 EUR from the visible operator block "
                "(the 15.00 EUR blocks of the same page are CSS-hidden templates); its period is not stated (cap period "
                "unspecified in the supplement and again in the follow-up, which finds that the 24 hours of the page concern "
                "late payment), so it is read as a maximum per stay. The 99.00 EUR seven-day product is a separate prebooked "
                "product, not a cap and no monthly product. The layer attribute tariff_rule_ids of this feature names the "
                "rules of the Ring-Center (both features share the facility id BS_None in the package); the rules of its own "
                "source id are used, and the facility is matched by the supplement's unambiguous legacy key BS_SOURCE_..., never by "
                "BS_None."},
    {"garage_id": "bs_lange_strasse_nord", "town": "bs", "layer": "bs_parkhaeuser",
     "feature": ("facility_id", "BS_PH010"), "facility": "BS_PH010", "rate": _bs("lange_strasse_nord", 10)[0],
     "cap": _bs("lange_strasse_nord", 11)[0], "window": None,
     "ignored": {_bs("lange_strasse_nord", 12)[0]: "bicycles and motorcycles park free, not a car tariff",
                 _bs("lange_strasse_nord", 55)[0]: "cinema customers with a validation (first hour 0.50 EUR), a customer "
                                                   "group the model cannot identify",
                 _bs("lange_strasse_nord", 56)[0]: "cinema customers with a validation, as the rule before"},
     "comment": "The standard tariff of the city feed."},
    {"garage_id": "bs_lange_strasse_sued", "town": "bs", "supplement_point": "BS_ADDITIONAL_1_ENTRANCE",
     "supplement": "BS_ADDITIONAL_1", "facility": "BS_ADDITIONAL_1", "first": "BS_ADDITIONAL_1_FIRST",
     "rate": "BS_ADDITIONAL_1_NEXT", "cap": "BS_ADDITIONAL_1_DAY", "window": None,
     "comment": "City car-park page of Braunschweig, listed without coordinates in the regional package (ruling R-4b2-1; the "
                "position is the OSM-mapped entrance of the supplement package, the city's address marker is an alternative "
                "that is not used). First 60 min 1.00 EUR in total, then 0.50 EUR per 30 min, published day tariff 10.00 EUR "
                "read as the day cap (its day boundary is not stated: a maximum per stay). The first period plus rate form "
                "expresses exactly the same prices as the bands '0-60 total 1.00; 60- 0.50/30' (pinned by a test). The package "
                "names no operator."},
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
     "facility": "BS_None", "bands": _bs("ring_center", 19, 20), "cap": _bs("ring_center", 21)[0], "window": None,
     "comment": "IDENTITY CHECKED against the package (ruling R-4b-11): the facility record BS_None is NAMED 'Parkhaus "
                "Forschungsflughafen' in facilities.json (the name of the first of its two features, bs_parkhaeuser:2, which "
                "share the facility id BS_None), but the attributes of that record (source_id, address Berliner Platz 1, "
                "description_text) and its rules -19 to -21 are those of the Parkhaus Ring-Center (feature bs_parkhaeuser:6), "
                "not of the Forschungsflughafen: the raw records of the three rules name "
                "facility_name 'Parkhaus Ring-Center', the rule ids carry the source id of the Ring-Center feature, and their "
                "source excerpts ('1. und 2. angef. Std.: 1,50 EUR', 'jede weitere angef. Std.: 2 EUR / 60 Min.', 'Tagessatz (24 "
                "Std.): 15 EUR') are the tariff lines of the Ring-Center feed text, whereas the feed text of the "
                "Forschungsflughafen (free for 15 min, each hour 1.50 EUR, day ticket 15.00 EUR) has its own rules -5 to -9 under "
                "the facility BS_SOURCE_4781292017fb49b091cc3066a17483fafbca28. The package's facility name is a mislabel of the "
                "shared facility id (both features list the Ring-Center rules in their tariff_rule_ids); the Ring-Center "
                "tariff is encoded and the Forschungsflughafen keeps its own rules. The feed text gives the opening hours "
                "Mo-Fr 06:00-21:30, Sa 06:30-21:30 and closed on Sundays and public holidays, which are no charging hours "
                "(ASSUMPTION P5); it also states that the garage is not connected to the city's parking guidance system. The "
                "amount of the first two hours is understood as per started hour (the package notes that the wording does not "
                "say 'je')."},
    {"garage_id": "bs_schloss", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH011"),
     "facility": "BS_PH011", "rate": _bs("schloss", 22)[0], "cap": _bs("schloss", 23)[0], "window": None,
     "ignored": {_bs("schloss", 24)[0]: "a lost ticket, not a stay"},
     "comment": "Shopping-centre garage Schloss-Arkaden, open to the public at the published tariff."},
    {"garage_id": "bs_schuetzenstrasse", "town": "bs", "layer": "bs_parkhaeuser", "feature": ("facility_id", "BS_PH006"),
     "facility": "BS_PH006", "rate": _bs("schuetzenstrasse", 25)[0], "cap": _bs("schuetzenstrasse", 26)[0], "window": None,
     "comment": "The feed states a temporarily reduced capacity (barriers); the tariff is the standard tariff."},
    {"garage_id": "bs_steinstrasse", "town": "bs", "supplement_point": "BS_ADDITIONAL_2_ENTRANCE",
     "supplement": "BS_ADDITIONAL_2", "facility": "BS_ADDITIONAL_2", "rate": "BS_ADDITIONAL_2_HOUR",
     "cap": "BS_ADDITIONAL_2_DAY", "window": None,
     "operator": ("supplement_page", "AGR Parking UG", "evidence/bs_coordinates/raw/operator_steinstrasse.html",
                  "Betreiber: AGR Parking UG", "operator_steinstrasse"),
     "comment": "City car-park page of Braunschweig (1.80 EUR per 60 min, day tariff 18.00 EUR read as the day cap, a maximum per "
                "stay), listed without coordinates in the regional package (ruling R-4b2-1). The position is the western "
                "entrance that OSM maps (the eastern exit of the garage is excluded by the supplement)."},
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
     "facility": "WOB_SUEDKOPF", "supplement": "WOB_SUEDKOPF", "followup": "WOB_SUEDKOPF",
     "grace": ("WOB_SUEDKOPF_R01", "WOB_SUEDKOPF_R02"), "cap": "SUEDKOPF_SECONDARY_CAP", "window": None,
     "p10_basis": "no source for the Suedkopf-Center; the reading of the Forschungsflughafen (the FAQ of a parking provider, not "
                  "site-specific) is applied by analogy, as the owner decided on 2026-10-07 (spec E12); the city page does not "
                  "state whether the 30 free minutes are deducted",
     "p11_basis": "the day maximum of 5.00 EUR comes from a secondary directory page (parkingworld.ch, source F13 of the "
                  "follow-up package), undated and not confirmed by the operator, and is consistent with the tariff of 1.00 EUR "
                  "per hour (owner decision of spec E13); the follow-up package itself does not take it as confirmed",
     "ignored": {"WOB_SUEDKOPF_REGULAR_REPORTED": "the supplement's record of the same city tariff (30 min free, 1.00 EUR per 60 "
                                                    "min; currentness of the city entry unconfirmed), no value beyond the "
                                                    "preferred rules used",
                 "WOB_SUEDKOPF_FOLLOWUP": "the follow-up record of the same city tariff (rounding, day maximum and currentness "
                                          "open)"},
     "comment": "Shopping-centre garage Suedkopf-Center, PRICED (rulings R-4b2-3 and R-4b2-8). City page: 30 min free, 1.00 EUR "
                "per 60 min (the preferred rules WOB_SUEDKOPF_R01 and R02), no rounding stated (ASSUMPTION P4), the free minutes "
                "read as a grace period (ASSUMPTION P10): bands '0-30 free; 30-60 total 1.00; 60- 1.00/60'. The day cap 5.00 EUR "
                "is the secondary directory's figure (ASSUMPTION P11; a maximum per stay, no day boundary stated); the 10.50 "
                "EUR lost-ticket price is no day maximum. Currentness of the city tariff is unconfirmed: the city page and the "
                "centre's own page publish different opening hours (city Mo-Fr 06:00-22:00, Sa 06:00-21:00, closed Sundays and "
                "public holidays; centre Mo-Fr 06:00-01:00, Sa-Su 06:00-22:00), which shows a need for maintenance but proves "
                "no price change; the operator is not confirmed (the website operator Save Holding GmbH is not taken as the "
                "operator of the garage). The Wolfsbox offer of one free hour is for its own customers only and not used."},
    {"garage_id": "wob_rathaus", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_RATHAUS"),
     "facility": "WOB_RATHAUS", "tiers": ("WOB_RATHAUS_R04", "WOB_RATHAUS_R02"), "first": "WOB_RATHAUS_R01",
     "cap": "WOB_RATHAUS_R03", "other_caps": ("WOB_RATHAUS_R05",),
     "comment": "Aufbau-Gesellschaft Wolfsburg (Kunstmuseum / Rathaus). The day cap belongs to the daytime tariff; the package "
                "notes that the interaction of the day and night caps and the rounding are not defined."},
    {"garage_id": "wob_poststrasse", "town": "wob", "layer": "wob_parkhaeuser", "feature": ("facility_id", "WOB_POST"),
     "attributes": ("region_parkhaeuser", ("facility_id", "WOB_POST")), "facility": "WOB_POST", "supplement": "WOB_POST",
     "tiers": ("WOB_POST_NIGHT",), "rest_tier": "WOB_POST_R01_148", "cap": "WOB_POST_R02_149",
     "other_caps": ("WOB_POST_R04_151",),
     "ignored": {"WOB_POST_R03_150": "the operator's night tariff 1.00 EUR from 21:00 to 06:30 without a billing unit (the "
                                     "package does not prefer it); replaced by the supplement rule WOB_POST_NIGHT, which adds "
                                     "the unit of 60 min from the city page"},
     "comment": "Saba. Time-of-day tiers (ruling R-4b2-5, spec E10; no night tariff is left out of the price any more): the day tier keeps the "
                "preferred day rate WOB_POST_R01_148 (2.00 EUR per started 60 min), which states no charging times and so applies "
                "at every time of day outside the night tier (06:30-21:00); the night tier 21:00-06:30 costs 1.00 EUR per 60 "
                "min: price and times from the operator page (sabaparking.com, which states '1 EUR' without a unit), the unit of "
                "60 min from the city page wolfsburg.de only (currentness unclear: the city page's day price is out of date "
                "against the operator's), the rounding of the night unit is not stated (ASSUMPTION P4, started unit). Caps "
                "re-checked against the tiers: the 'Tagestarif' 9.00 EUR stays the day cap and, as the single cap applies to the "
                "whole stay (ASSUMPTION P7), a stay that includes the night is capped at 9.00 EUR where the operator's maximum "
                "for day and night together is 10.00 EUR (WOB_POST_R04_151, not applied); the 'Tagestarif' is read as the day "
                "maximum (its day boundary is not defined). Entry only Mo-Fr 06:30-20:00, exit at any time. The municipal page "
                "lists another hourly rate than the operator."},
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
     "feature": ("facility_id", "WOB_SCHILLER"), "facility": "WOB_SCHILLER",
     "bands": ("WOB_SCHILLER_R01", "WOB_SCHILLER_R02"), "cap": "WOB_SCHILLER_R03",
     "window": None, "operator": None,
     "comment": "The package states the operator as 'Saba per city; not listed among current Saba three facilities': the "
                "city lists Saba, the operator's own page does not list the garage, so no operator is taken."},
    {"garage_id": "wob_city_galerie", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_CITY_GALERIE"), "facility": "WOB_CITY_GALERIE", "rate": "WOB_CITYGALERIE_R02",
     "first_equals_rate": "WOB_CITYGALERIE_R01", "cap": "WOB_CITYGALERIE_R03", "window": None,
     "comment": "Shopping-centre garage City-Galerie (ECE), open to the public at the published tariff; special shopping "
                "Sundays have their own fees (not modelled)."},
    {"garage_id": "wob_designer_outlets", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_OUTLETS"), "facility": "WOB_OUTLETS",
     "bands": ("WOB_DESIGNEROUTLETS_R01", "WOB_DESIGNEROUTLETS_R02", "WOB_DESIGNEROUTLETS_R03", "WOB_DESIGNEROUTLETS_R04",
               "WOB_DESIGNEROUTLETS_R05"), "window": None,
     "comment": "Outlet-centre garage: free for 20 min, 1.00 EUR up to 2 h, 0.50 EUR per hour up to 4 h, 1.50 EUR per hour up to "
                "7 h, 5.00 EUR per hour from the 7th hour, no day cap published; the capacity of 1,000 is an approximate "
                "figure for all parking areas of the centre."},
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
                "the city brochure of December 2024 gives the garage 'Oeffnungszeit: 24 Stunden' in its column of fee-liable "
                "times (gebuehrenpflichtige Zeit), which is the basis of the window; the ordinance of 2018-12-18 sec. 2(1) "
                "states only the opening hours of the garage (geoeffnet 00.00 bis 24.00), which are no charging hours and not "
                "a source of the window"),
     "comment": "Garage of the city of Helmstedt, tariff of the city brochure of December 2024. The ordinance of 2018-12-18 "
                "(retrieved 2026-09-29) states another structure: 0.50 EUR for the first 30 min, 1.00 EUR up to 1 h, 2.00 "
                "EUR up to 2 h, 0.50 EUR per further started hour, at most 8.00 EUR, and the garage's notice board decides; "
                "the 2018 text is the older source and is not encoded."},
    {"garage_id": "he_stobenstrasse", "town": "he", "supplement_point": "HE_STOBEN_ACCESS", "supplement": "HE_STOBEN",
     "facility": "HE_STOBEN", "rate": "HE_STOBEN_FEE", "window": "rate",
     "comment": "Municipal parking deck of the Helmstedt brochure of December 2024 (0.80 EUR per 60 min, Mo-Sa 07:00-19:00, 64 "
                "spaces), listed without coordinates in the regional package (ruling R-4b2-1). The position is a DERIVED access "
                "point (the connection of the driveway to the Stobenstrasse at an existing OSM node), no confirmed barrier or "
                "portal point. The 23 disc-parking spaces in the Stobenstrasse are a separate street regulation and not this "
                "garage."},
    {"garage_id": "he_groepern_tiefgarage", "town": "he", "supplement_point": "HE_GROEPERN_TG_118_ACCESS",
     "supplement": "HE_GROEPERN_TG_118", "facility": "HE_GROEPERN_TG_118",
     "first": "HE_GROEPERN_TG_118_BROCHURE_FIRST", "rate": "HE_GROEPERN_TG_118_BROCHURE_NEXT",
     "window": ("stated", "00:00", "24:00",
                "the city brochure of December 2024 gives the garage 'Oeffnungszeit: 24 Stunden' in its column of fee-liable "
                "times (gebuehrenpflichtige Zeit), which is the basis of the window"),
     "comment": "Municipal underground garage of the Helmstedt brochure of December 2024 (118 spaces, ticket, no maximum stay); "
                "the package holds no tariff rule for it, so the tariff is read from the quotations of the brochure text that "
                "the supplement keeps (ruling R-4b2-1): '0,70 EUR / 1 Std.' and 'jede weitere 0,30 EUR'. The new key "
                "HE_GROEPERN_TG_118 is never joined to HE_GROEPERN_STRASSE (18 fee-liable street spaces). The position is the "
                "OSM-mapped entrance of the underground garage, checked against city documents and the driveway by the "
                "supplement."},
    {"garage_id": "pe_werderstrasse", "town": "pe", "layer": "region_parkhaeuser", "feature": ("facility_id", "PE_WERDER"),
     "facility": "PE_WERDER",
     "bands": ("PE_WERDER_R01_168", "PE_WERDER_R02_169", "PE_WERDER_R03_170", "PE_WERDER_R04_171"), "window": None,
     "comment": "Stadtwerke Peine: 0.20 EUR in total for the first 30 min, 0.80 EUR in total up to 1 h, then 0.40 EUR per half "
                "hour up to 5 h, 4.00 EUR in total for 5 to 24 h (the published anchors 0.20 at 30 min, 0.80 at 60 min and "
                "4.00 at 300 min are reproduced by the bands); the position is the OSM feature centre via mapcarta."},
    {"garage_id": "pe_wallstrasse", "town": "pe", "layer": "region_parkhaeuser", "feature": ("facility_id", "PE_WALL"),
     "facility": "PE_WALL", "bands": ("PE_WALL_R01_172", "PE_WALL_R02_173", "PE_WALL_R03_174", "PE_WALL_R04_175"),
     "window": None,
     "comment": "Stadtwerke Peine, the same schedule as the garage Werderstrasse (0.20 EUR in total for the first 30 min, 0.80 EUR "
                "in total up to 1 h, then 0.40 EUR per half hour up to 5 h, 4.00 EUR in total for 5 to 24 h); the opening hours "
                "are daily 07:00-23:15 (longer after events in the Forum), which are no charging hours."},
    {"garage_id": "sz_brawo_carree", "town": "sz", "layer": "region_parkhaeuser", "feature": ("facility_id", "SZ_BRAWO"),
     "facility": "SZ_BRAWO", "bands": ("SZ_BRAWO_R01_176", "SZ_BRAWO_R02_177"), "window": None,
     "ignored": {"SZ_BRAWO_R03_178": "customer condition: free for 90 min for customers of Kaufland or dm with a retailer "
                                     "validation, a customer group the model cannot identify",
                 "SZ_BRAWO_R04_179": "customer condition: free for 60 min for customers of MediaMarkt or ABC-Schuhcenter with "
                                     "a retailer validation, a customer group the model cannot identify"},
     "comment": "Shopping-centre garage BRAWO Carree: 1.50 EUR per started hour for the first 6 h, from the 7th started hour a "
                "published day total of 15.00 EUR, a jump and no cap of the hourly rate (the anchors 9.00 EUR at 360 min and "
                "15.00 EUR beyond are reproduced by the bands); the capacity counts the regular spaces only; the position is an "
                "OSM node via mapcarta."},
    {"garage_id": "gs_achtermann", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_ACHTERMANN"),
     "facility": "GS_ACHTERMANN", "supplement": "GS_ACHTERMANN", "followup": "GS_ACHTERMANN",
     "table": ("ACHTERMANN_DIRECTORY_TABLE", "0-180 1.50/60; 180-570 0.80/30; 570-720 total 15.00; 720- 1.00/60"),
     "cap": "ACHTERMANN_DIRECTORY_TABLE:cap", "window": None,
     "p11_basis": "the directory table of the same facility (parkito.ch, source F05 of the follow-up package): undated, no "
                  "operator confirmation, an operator candidate (Tessner Verwaltungs GmbH) that is not confirmed; the table "
                  "explains the contradiction of the tourism page (1.50 EUR per hour for 3 h, then 0.80 EUR, against 15.00 EUR "
                  "for 12 h and 25.00 EUR for 24 h) as 0.80 EUR per 30 min and agrees with the Goslar garage C&A (1.50 EUR per "
                  "hour for 3 h, then 0.80 EUR per 30 min, 25.00 EUR for 24 h); owner decision of spec E13",
     "ignored": {"GS_ACHTERMANN_REV_01": "the tourism page's hourly rate for the first 3 h (the table agrees: 1.50 EUR per hour)",
                 "GS_ACHTERMANN_REV_02": "the tourism page's 0.80 EUR per HOUR after 3 h, which contradicts the totals; the "
                                         "directory table shows 0.80 EUR per 30 min instead",
                 "GS_ACHTERMANN_REV_03": "the tourism page's 12 h price 15.00 EUR (the table's 720 min point)",
                 "GS_ACHTERMANN_REV_04": "the tourism page's 24 h price 25.00 EUR (the table's 1440 min point, the day cap)",
                 "GS_ACHTERMANN_UNRESOLVED": "the supplement's record: no current operator tariff found, the hotel price of "
                                             "15.00 EUR per night is for hotel guests with validation only and no public tariff",
                 "GS_ACHTERMANN_FOLLOWUP": "the follow-up record (current operator tariff unverified)"},
     "comment": "PRICED from the directory table under ASSUMPTION P11 (ruling R-4b2-8, spec E13; the package recommends leaving "
                "the garage unpriced, which the owner overrides). Bands as the owner decided them from the table: 1.50 EUR per "
                "started 60 min up to 3 h, 0.80 EUR per 30 min up to 9.5 h, 15.00 EUR in total up to 12 h, then 1.00 EUR per "
                "60 min (the further hour of the table); the step evaluates the bands at EVERY price point of the table (60 to "
                "570 min, 720 min, 1440 min capped at 25.00 EUR). The 24-hour price 25.00 EUR is the day cap, with no stated "
                "period (a maximum per stay). The rounding is not stated (ASSUMPTION P4), no charging times are stated "
                "(ASSUMPTION P5). The operator is left empty: the directory names Tessner Verwaltungs GmbH and the "
                "Tessner group lists the garage on its property page, which is no operator confirmation. The hotel price is no "
                "public tariff. The position is the city's point of 2018."},
    {"garage_id": "gs_ca", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_CA"),
     "facility": "GS_CA", "bands": ("GS_CA_REV_01", "GS_CA_REV_02"), "cap": "GS_CA_REV_03", "window": None,
     "comment": "Tariff of the tourism page, not confirmed by the operator (package status): 1.50 EUR per hour for the first 3 "
                "h, then 0.80 EUR per half hour, 25.00 EUR for 24 h (the 24-hour price is the day cap; the package notes that "
                "its cap mechanics and re-entry are not stated); the opening hours 06:00-20:00 are no charging hours."},
    {"garage_id": "gs_galeria", "town": "gs", "layer": "region_parkhaeuser", "feature": ("facility_id", "GS_GALERIA"),
     "facility": "GS_GALERIA", "supplement": "GS_GALERIA", "followup": "GS_GALERIA",
     "rate": "GS_GALERIA_REGULAR:stage2", "first_equals_rate": "GS_GALERIA_REGULAR:stage1", "cap": "GS_GALERIA_WVV",
     "window": None,
     "ignored": {"GS_GALERIA_REV_01": "the tourism page states the same 1.50 EUR for the first hour",
                 "GS_GALERIA_REV_02": "the tourism page states the same 15.00 EUR for 24 h (as written: rolling 24 hours)",
                 "GS_GALERIA_DIRECT_OPERATOR": "the CONFLICTING variant: the direct operator locator states a daily maximum of "
                                               "8.00 EUR (numeric price 8.0; its menu identifier TH15 is no price), not used by "
                                               "the owner decision of spec E12",
                 "GS_GALERIA_FOLLOWUP": "the follow-up record: a third provider page (SVG ParkingHQ) states the same 1.50 EUR per "
                                        "hour and no rounding; search hits that state 'je angefangene Stunde' belong to other "
                                        "garages"},
     "qa_comment": "the daily maximum 15.00 EUR per calendar day (WVV, owner decision of spec E12) is used; the direct operator "
                   "locator states 8.00 EUR (the conflicting variant, not used); the Dauerstellplatz 39.00 EUR per month is the "
                   "used monthly product",
     "comment": "PRICED (rulings R-4b2-4 and R-4b2-2 of spec E12). The first and every further hour cost 1.50 EUR (two operator "
                "sources and a third provider page), the rounding of a started hour is not stated (ASSUMPTION P4). The daily "
                "maximum is 15.00 EUR per calendar day (WVV, the service provider; the tourism page also states 15 EUR for "
                "24 h) by the owner decision of spec E12; the direct locator's 8.00 EUR is the conflicting variant, named here "
                "and in the QA table. The cap is stated per calendar day and the dataset can only hold a maximum per stay: for "
                "the garage's opening hours (Mo-Sa 08:30-19:00, no charging hours) a stay inside one day pays what is "
                "published and a stay across midnight is capped once (the reading is counted). Monthly product (spec D2): the "
                "direct operator publishes a fixed Dauerstellplatz of 39.00 EUR per month with an online contract booking and "
                "names no restricted customer group, so it is publicly purchasable and the only product: used; the contract "
                "term, the availability and the access rights are not verified (named in the product text). The position is "
                "the city's point of 2018."},
    {"garage_id": "gs_charley_jacob_strasse", "town": "gs", "layer": "gos_parking_locations",
     "feature": ("facility_id", "GOS_POINT_4"), "facility": "GOS_POINT_4", "supplement": "GOS_POINT_4",
     "followup": "GOS_POINT_4", "rate": "CHARLEY_DIRECTORY", "window": None, "capacity_from": "CHARLEY_DIRECTORY",
     "archived_point": True,
     "p11_basis": "the undated directory entry for the Tiefgarage Stadtverwaltung at Charley-Jacob-Strasse 3 (parkinglist.de, "
                  "source F08 of the follow-up package: 1.00 EUR per hour, 58 spaces, access outside core working hours) "
                  "without any operator confirmation; the garage is documented in the budget of Goslar for 2026 (product "
                  "546-01 parking management lists the barrier system of the garage, the asset plan of the GGM lists the "
                  "underground garage), which proves its existence and no tariff; the identity of the archived map point of "
                  "2018 with this garage is not verified (owner decision of spec E13)",
     "ignored": {"GOS_POINT_4_UNRESOLVED": "the supplement's record: identity and current operation unverified, no tariff of "
                                           "the open-air car park nearby (2.00 EUR per hour) and none of an undated directory "
                                           "transferred there",
                 "GOS_POINT_4_FOLLOWUP": "the follow-up record (current public tariff unverified)"},
     "comment": "PRICED from the directory under ASSUMPTION P11 (ruling R-4b2-8, spec E13; the package recommends excluding it "
                "from the costed list, which the owner overrides). 1.00 EUR per 60 min, rounding not stated (ASSUMPTION P4), "
                "no charging times stated (ASSUMPTION P5), no day cap stated, 58 spaces as the directory reports them (a "
                "secondary directory total). The garage is not closed: the budget 2026 documents a barrier-controlled garage; "
                "police report 2021 and event flyer 2023 only show earlier use. The directory's restriction 'public access "
                "outside core working hours' is not modelled (the capacity and availability of garages are not modelled, spec "
                "E3). The position is the archived municipal map point of 2018 (the same coordinate as the regional layer, "
                "checked), the geometry method names it; the budget does not update its age or accuracy."},
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
    {"record_id": "monthly_gs_galeria_dauerstellplatz", "rule": "GS_GALERIA_MONTHLY", "garage_id": "gs_galeria",
     "decision": "used"},
    {"record_id": "monthly_bs_forschungsflughafen_7_day", "rule": "BS_FORSCHUNGSFLUGHAFEN_WEEK_PREBOOKED",
     "garage_id": "bs_forschungsflughafen", "decision": "not_used", "reason": "not_monthly_or_30_day", "amount_eur": 99.0,
     "subject": "Braunschweig Parkhaus Forschungsflughafen, 7-day product (prebooked)",
     "note": "the operator page offers a seven-day product for 99.00 EUR that has to be booked and registered in advance; it is a "
             "separate product, no monthly or 30-day product (spec Amendment D2 takes monthly and 30-day products) and no cap "
             "of the short-stay tariff"},
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

#: Owner decisions that release a rule of the supplement package for use (spec E12, rulings R-4b2-2, R-4b2-4 and R-4b2-5):
#: {rule id: (the ruling, {field decision id of the package: the statuses it may have})}. The supplement marks every rule
#: full_cost_calculation_ready=false, so no rule sets a value unless it is listed here, and it is released only while the
#: package's own field decision has the status the ruling relied on (``garage_supplement.release_rules``). The deduction of the
#: free 15 min at the Forschungsflughafen is an OPEN decision of the package: ASSUMPTION P10 is the owner's reading of it.
SUPPLEMENT_RELEASED = {
    "BS_FORSCHUNGSFLUGHAFEN_REGULAR": (
        "R-4b2-2", {"BS_FORSCHUNGSFLUGHAFEN:rounding": ("resolved",),
                    "BS_FORSCHUNGSFLUGHAFEN:free_period_deducted_after_threshold": ("open_with_general_operator_indication",)}),
    "BS_FORSCHUNGSFLUGHAFEN_REGULAR:cap": (
        "R-4b2-2", {"BS_FORSCHUNGSFLUGHAFEN:daily_cap_eur": ("resolved_visible_operator_block",)}),
    "WOB_POST_NIGHT": ("R-4b2-5", {"WOB_POST:night_billing_unit_minutes": ("supported_by_municipal_source_currentness_unclear",)}),
    "GS_GALERIA_REGULAR:stage1": ("R-4b2-4", {"GS_GALERIA:subsequent_price_eur_per_60_minutes": ("resolved_operator_sources",)}),
    "GS_GALERIA_REGULAR:stage2": ("R-4b2-4", {"GS_GALERIA:subsequent_price_eur_per_60_minutes": ("resolved_operator_sources",)}),
    "GS_GALERIA_WVV": ("R-4b2-4 (owner decision R-4b2-2 of spec E12)", {"GS_GALERIA:daily_cap_eur": ("conflict",)}),
    "GS_GALERIA_MONTHLY": ("R-4b2-4", {"GS_GALERIA:monthly_eur": ("published_terms_unverified",)}),
}

#: The one tariff that only a brochure text states (``garage_supplement.brochure_rules``): the Helmstedt underground garage
#: Groepern (ruling R-4b2-1). The amounts and the unit are parsed from the quotations; the step checks that every quotation is in
#: the brochure row of the garage. ASCII source: \u00f6 is o-umlaut, \u20ac the euro sign, \u00d6 O-umlaut.
BROCHURE_TARIFFS = (
    {"facility": "HE_GROEPERN_TG_118", "member": "evidence/he_coordinates/raw/stadt_helmstedt_parken.txt",
     "source_id": "HE_CITY_PARKING", "anchor": "Gr\u00f6pern (Tiefgarage)", "first": "0,70\u20ac / 1 Std.",
     "further": "jede weitere 0,30\u20ac", "window": ("\u00d6ffnungszeit:", "24 Stunden"),
     "unit_reading": "the brochure states no unit for 'jede weitere 0,30'; it is read as the unit of the first price (one hour), "
                     "as ruling R-4b2-1 states (0.30 EUR for each further hour)"},
)

#: Owner decisions that release a rule of the follow-up package for use (spec E13, ruling R-4b2-8, ASSUMPTION P11): the
#: observations of the follow-up package that state a tariff. Each field decision is still OPEN in the package (the follow-up
#: finds no current operator tariff); P11 is the owner's decision to price from the best available secondary evidence.
FOLLOWUP_RELEASED = {
    "ACHTERMANN_DIRECTORY_TABLE": ("R-4b2-8", {"GS_ACHTERMANN:current_operator_tariff": ("open_with_new_operator_and_tariff_lead",)}),
    "ACHTERMANN_DIRECTORY_TABLE:cap": (
        "R-4b2-8", {"GS_ACHTERMANN:current_operator_tariff": ("open_with_new_operator_and_tariff_lead",)}),
    "CHARLEY_DIRECTORY": ("R-4b2-8", {"GOS_POINT_4:official_facility_documented_in_2026": ("resolved_official_budget_evidence",),
                                      "GOS_POINT_4:current_public_tariff": ("open_undated_secondary_tariff_only",)}),
    "SUEDKOPF_SECONDARY_CAP": ("R-4b2-8", {"WOB_SUEDKOPF:daily_cap_eur": ("open_secondary_five_euro_candidate",)}),
}
