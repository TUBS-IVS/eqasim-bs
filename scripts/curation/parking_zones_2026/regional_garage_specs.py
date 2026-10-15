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


#: The owner's transcription of the meinestadt.de directory entry of the Goslar garage GALERIA (2026-10-15; secondary evidence of
#: the recorded 8 EUR day maximum, never a value of the row): its file name and SHA-256 (data record
#: parking_owner_transcriptions_2026_10_15; the file is no input of the step, only cited).
GALERIA_DIRECTORY_FILE = "goslar_galeria_meinestadt_tariff.txt"
GALERIA_DIRECTORY_SHA256 = "e84d9312589341d29e9b3da3a329483f2aabb62534635a4726289178b605b76e"

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
     "p10_decisions": ("R-4b2-2", {"BS_FORSCHUNGSFLUGHAFEN:free_period_deducted_after_threshold": (
         "open_with_general_operator_indication",)}),
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
                "source id are used, and the facility is matched by the supplement's unambiguous legacy key "
                "BS_SOURCE_..., never by "
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
     "p10_decisions": ("R-4b2-3", {"WOB_SUEDKOPF:free_period_deducted_after_threshold": ("open",)}),
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
     "comment": "Saba. Time-of-day tiers (ruling R-4b2-5, spec E10; no night tariff is left out of the price any more): "
                "the day tier keeps the "
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
    {"garage_id": "wob_hauptbahnhof", "town": "wob", "layer": "wob_parkhaeuser",
     "feature": ("facility_id", "WOB_HAUPTBAHNHOF"), "facility": "WOB_HAUPTBAHNHOF", "operator": "Contipark",
     "rate": "WOB_HBF_P1_R01", "cap": "WOB_HBF_P1_R02", "window": None,
     "ignored": {"WOB_HBF_P1_R03": "the day tariff at the reduced price for holders of a Pcard or the digital BahnCard, "
                                    "discounted at the on-site terminal only: a customer group the model cannot identify "
                                    "(spec Amendment G2)"},
     "comment": "Parkdeck Hauptbahnhof P1 (DB BahnPark, operated by Contipark), a garage OPTION since spec Amendment G2 (owner "
                "decision 2026-10-08: the station car parks are public; rulings R-4b-4 and R-4b-9 are revoked for it). Rate per "
                "started 60 min and the published day tariff as the cap; the cap period is 'day definition unspecified', so the "
                "day tariff is read as a maximum per stay. The 186 (Contipark) and 190 (DB BahnPark leaflet) spaces conflict and "
                "are not chosen: no capacity is reported. The product for public-transport customers (35 EUR) is restricted "
                "and not used."},
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
     "p11_basis": "the monthly product only (the tariff itself rests on the official brochure and not on this assumption): the "
                  "undated directory entry HE_PARKINGLIST (parkinglist.de, a secondary directory; operator Stadt Helmstedt as the "
                  "entry states it; no source date) quotes '33.00 Euro / Monat', and the brochure of December 2024 states no "
                  "monthly product, so the entry is the best available evidence of one for this garage; the same entry differs "
                  "from the brochure at 2 h (1.20 EUR against 1.00 EUR), so it may be older and the monthly amount may be "
                  "outdated (owner ruling R-4b2-9)",
     "ignored": {"HE_GROEPERN_TG_118_DIRECTORY_TWO_HOURS": "the CONFLICTING variant of the undated directory entry HE_PARKINGLIST "
                                                           "(operator Stadt Helmstedt): 1.20 EUR for 2 hours against the "
                                                           "1.00 EUR that the encoded brochure tariff gives at 2 h (0.70 EUR "
                                                           "for the first hour plus 0.30 EUR for the second); the official, "
                                                           "dated brochure outranks the undated directory (ruling R-4b2-9)"},
     "qa_comment": "the brochure tariff of December 2024 is encoded; the undated directory HE_PARKINGLIST states 1.20 EUR at 2 h "
                   "against the encoded 1.00 EUR (conflicting variant, not used); its monthly product 33.00 EUR is used under "
                   "ASSUMPTION P11",
     "comment": "Municipal underground garage of the Helmstedt brochure of December 2024 (118 spaces, ticket, no maximum stay); "
                "the package holds no tariff rule for it, so the tariff is read from the quotations of the brochure text that "
                "the supplement keeps (ruling R-4b2-1): '0,70 EUR / 1 Std.' and 'jede weitere 0,30 EUR'. The brochure "
                "(official, Stadt Helmstedt, December 2024) gives no unit for 'jede weitere'; the unit of one hour rests on the "
                "supplement's evidence copy of the undated directory entry HE_PARKINGLIST (operator Stadt Helmstedt), which "
                "states '0.30 Euro / jede weitere Stunde' (the step checks that its first and further prices equal the "
                "brochure's). The same directory states 1.20 EUR for 2 hours where the encoded tariff gives 1.00 EUR: that "
                "CONFLICTING variant is recorded and not used, because the official and dated brochure outranks an undated "
                "directory (ruling R-4b2-9). The directory's monthly product of 33.00 EUR is the only product of the garage "
                "and names no restricted customer group, so it is the cheapest publicly purchasable one and is used under "
                "ASSUMPTION P11 (best available evidence, spec E13); its amount may be outdated and its terms are not "
                "stated. The new key "
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
                   "locator states 8.00 EUR (the conflicting variant, not used; a business directory that is not shown to be "
                   "independent of the operator locator repeats it, owner's transcription of 2026-10-15, "
                   + GALERIA_DIRECTORY_FILE + ", SHA-256 " + GALERIA_DIRECTORY_SHA256
                   + "); the Dauerstellplatz 39.00 EUR per month is the used monthly product",
     "comment": "PRICED (rulings R-4b2-4 and R-4b2-2 of spec E12). The first and every further hour cost 1.50 EUR (two operator "
                "sources and a third provider page), the rounding of a started hour is not stated (ASSUMPTION P4). The daily "
                "maximum is 15.00 EUR per calendar day (WVV, the service provider; the tourism page also states 15 EUR for "
                "24 h) by the owner decision of spec E12; the direct locator's 8.00 EUR is the conflicting variant, named here "
                "and in the QA table. Further evidence of that conflict (spec v3 B7, owner decision of 2026-10-15: the 15 EUR "
                "stay): the owner's transcription of the meinestadt.de business directory (" + GALERIA_DIRECTORY_FILE + ", "
                "SHA-256 " + GALERIA_DIRECTORY_SHA256 + "; a secondary directory source, pasted on 2026-10-15) states 1.50 EUR "
                "for the first and for every further hour, a daily maximum of 8 EUR and a Dauerstellplatz of 39 EUR per month: "
                "it repeats the 8 EUR of the direct locator and the 39 EUR monthly price, so the 8 EUR now stands on two "
                "secondary sources against the service provider's 15 EUR, and the owner decision keeps 15 EUR (not used). The "
                "cap is stated per calendar day and the dataset can only hold a maximum per stay: for "
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
                  "2018 with this garage is not verified. Consistency check against the garages of the same town (ASSUMPTION "
                  "P11): 1.00 EUR per hour is BELOW the 1.50 EUR per hour of Parkhaus bei C&A, GALERIA and Achtermann in "
                  "Goslar, so the directory value does not agree with them; it is kept, because the garage is a municipal "
                  "office garage that is public only outside the core working hours, where a lower municipal tariff is "
                  "plausible, and no source for this garage states another price (result of the check: kept, with the "
                  "difference named; owner decision of spec E13)",
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
    {"record_id": "monthly_wob_hauptbahnhof_24h", "rule": "WOB_HAUPTBAHNHOF_MONTHLY_1", "garage_id": "wob_hauptbahnhof",
     "decision": "used", "why": "Dauerparken Mo-So 24 h, the cheapest product open to every customer (spec Amendment G2)"},
    {"record_id": "monthly_wob_hauptbahnhof_oepnv", "rule": "WOB_HAUPTBAHNHOF_MONTHLY_2", "garage_id": "wob_hauptbahnhof",
     "decision": "not_used", "reason": "restricted_customer_group"},
    {"record_id": "monthly_gs_galeria_dauerstellplatz", "rule": "GS_GALERIA_MONTHLY", "garage_id": "gs_galeria",
     "decision": "used"},
    {"record_id": "monthly_he_groepern_tiefgarage", "rule": "HE_GROEPERN_TG_118_DIRECTORY_MONTHLY",
     "garage_id": "he_groepern_tiefgarage", "decision": "used",
     "why": "decision (ruling R-4b2-9): used, because it is the only monthly product of the garage and names no restricted "
            "customer group, so it is the cheapest publicly purchasable one (spec D2); the evidence is a secondary, undated "
            "directory entry (ASSUMPTION P11, best available evidence) that differs from the brochure at 2 h, so the amount may "
            "be outdated"},
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
OTHER_TOWNS = {"cl": ("03153018", "Clausthal-Zellerfeld"), "se": ("03153012", "Seesen"), "bh": ("03153002", "Bad Harzburg")}

#: The decisions on the entries of the Braunschweig car-park directory (ASCII names as ``regional_garages`` transliterates
#: them): reason code of ``garage_qa.CANDIDATE_REASONS`` and a note. A directory entry that is not listed stops the step.
DIRECTORY_SURFACE_LOT = "surface_lot_site"
DIRECTORY_DECISIONS = {
    "Kurzzeitparkplatz Lilienthalplatz": ("customer_regime", "airport short-stay car park; the coverage register lists it as "
                                                             "excluded (customer and visitor regime)"),
    # spec Amendment I1 revises ruling R-E1 for surface lots: a car park of ParkGO or of the BgA is an off-street option whenever
    # it is public and large (spec I2), so these entries are sites of the research package (DIRECTORY_LOT_KEYS): the site is its
    # own dataset row or its own QA row of the package, never a second record here
    "Parkplatz Grosser Hof": (DIRECTORY_SURFACE_LOT, "a site of the large-lot package (spec Amendment I)"),
    "Parkplatz Hauptbahnhof Nord": (DIRECTORY_SURFACE_LOT, "a site of the large-lot package (spec Amendment I; owner decision A: "
                                                           "the station zone bs_hbf_p1_nord stays a zone and the lot is an "
                                                           "option)"),
    "Parkplatz Hauptbahnhof Sued": (DIRECTORY_SURFACE_LOT, "a site of the large-lot package (spec Amendment I; owner decision A: "
                                                           "the station zone bs_hbf_p2_sued stays a zone and the lot is an "
                                                           "option)"),
    "Parkplatz Markthalle": (DIRECTORY_SURFACE_LOT, "a site of the large-lot package (spec Amendment I; owner decision A: the BgA "
                                                    "zone bs_bga_markthalle stays a zone and the lot is an option)"),
    "Parkplatz Nimesstrasse": ("lot_capacity_unknown", "ParkGO zone 1 car park (maximum stay 3 h): the street product of its zone "
                                                       "would be its tariff, but neither the directory nor the research package "
                                                       "states its capacity, so the size policy (spec Amendment I2) cannot select "
                                                       "it"),
    "Parkplatz Suedstrasse": (DIRECTORY_SURFACE_LOT, "a site of the large-lot package (spec Amendment I; a BgA zone that passes "
                                                     "the size policy like every lot)"),
    "Parkplatz Werder": (DIRECTORY_SURFACE_LOT, "a site of the large-lot package (spec Amendment I; Wendenstrasse / Werder)"),
    "Stellplaetze am Loewenwall": ("lot_capacity_unknown", "ParkGO tariff zone 1 spaces: the street product of their zone would "
                                                           "be their tariff, but neither the directory nor the research package "
                                                           "states a capacity, so the size policy (spec Amendment I2) cannot "
                                                           "select them"),
}
#: The research-package site (``facility_key``) of each directory entry decided as ``DIRECTORY_SURFACE_LOT``.
DIRECTORY_LOT_KEYS = {"Parkplatz Grosser Hof": "braunschweig_grosser_hof",
                      "Parkplatz Hauptbahnhof Nord": "braunschweig_hbf_nord",
                      "Parkplatz Hauptbahnhof Sued": "braunschweig_hbf_sued", "Parkplatz Markthalle": "braunschweig_markthalle",
                      "Parkplatz Suedstrasse": "braunschweig_suedstrasse", "Parkplatz Werder": "braunschweig_wendenstrasse_werder"}

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
     "directory": {"member": "evidence/he_coordinates/raw/parkinglist_groepern.html", "source_id": "HE_PARKINGLIST",
                   "operator": "Betreiber: Stadt Helmstedt", "first": "0.70 Euro / 1 Stunde",
                   "second": "1.20 Euro / 2 Stunden", "further": "0.30 Euro / jede weitere Stunde",
                   "monthly": "33.00 Euro / Monat", "ruling": "R-4b2-9"},
     "unit_reading": "the brochure states no unit for 'jede weitere 0,30' (its column header is only 'Kosten'); the "
                     "supplement's evidence copy of the undated directory entry HE_PARKINGLIST (operator Stadt Helmstedt) states "
                     "'0.30 Euro / jede weitere Stunde' (the same amount), so the unit is one hour, which is also the unit of "
                     "the first price (ruling R-4b2-9; ruling R-4b2-1 read it the same way)"},
)

#: Owner decisions that release a rule of the follow-up package for use (spec E13, ruling R-4b2-8, ASSUMPTION P11): the
#: observations of the follow-up package that state a tariff. Each field decision is still OPEN in the package (the follow-up
#: finds no current operator tariff); P11 is the owner's decision to price from the best available secondary evidence.
FOLLOWUP_RELEASED = {
    "ACHTERMANN_DIRECTORY_TABLE": (
        "R-4b2-8", {"GS_ACHTERMANN:current_operator_tariff": ("open_with_new_operator_and_tariff_lead",)}),
    "ACHTERMANN_DIRECTORY_TABLE:cap": (
        "R-4b2-8", {"GS_ACHTERMANN:current_operator_tariff": ("open_with_new_operator_and_tariff_lead",)}),
    "CHARLEY_DIRECTORY": ("R-4b2-8", {"GOS_POINT_4:official_facility_documented_in_2026": ("resolved_official_budget_evidence",),
                                      "GOS_POINT_4:current_public_tariff": ("open_undated_secondary_tariff_only",)}),
    "SUEDKOPF_SECONDARY_CAP": ("R-4b2-8", {"WOB_SUEDKOPF:daily_cap_eur": ("open_secondary_five_euro_candidate",)}),
}


# ---------------------------------------------------------------- Braunschweig monthly products (spec Amendment F1)
#: The monthly status of every Braunschweig garage of the dataset, read from the owner's package
#: ``Braunschweig_Monatstarife_2026-10-08.zip`` and the Contipark evidence directory (``bs_monthly_products``). Keys:
#: ``garage_id``; ``facility`` and ``facility_key`` (the package facility record, checked against the garage by id); ``status``
#: (published, sold_out, no_price, price_on_request or period_unconfirmed; the step checks it against the package's records); for
#: a published product ``rule`` (WHICH rule of the package to read, which must be the cheapest of its facility) or ``offer`` (the
#: package offer whose price comes from the configurator capture); for the others ``quotes`` ((token, phrase) pairs: each names
#: the one sentence of the package README that states the status); ``evidence`` (a capture of the evidence directory that adds
#: to it) and ``hidden_amount`` (the package's visibility evidence records an amount in hidden page sections). No amount is typed
#: here: the amounts are read from the package and the capture.
BS_MONTHLY_SPECS = (
    {"garage_id": "bs_eiermarkt", "facility": "BS_PH004", "facility_key": "braunschweig_eiermarkt", "status": "no_price",
     "quotes": (("Eiermarkt", "kein belastbarer aktueller Monatspreis ermittelt"),), "evidence": "eiermarkt"},
    {"garage_id": "bs_forschungsflughafen", "facility": "BS_FORSCHUNGSFLUGHAFEN", "facility_key": "forschungsflughafen",
     "status": "price_on_request", "quotes": (("Forschungsflughafen", "Preis auf Anfrage"),), "hidden_amount": True},
    {"garage_id": "bs_lange_strasse_nord", "facility": "BS_PH010", "facility_key": "lange_nord", "status": "no_price",
     "quotes": (("Lange Stra\u00dfe Nord/S\u00fcd", "kein belastbarer aktueller Monatspreis ermittelt"),)},
    {"garage_id": "bs_lange_strasse_sued", "facility": "BS_ADDITIONAL_1", "facility_key": "lange_sued", "status": "no_price",
     "quotes": (("Lange Stra\u00dfe Nord/S\u00fcd", "kein belastbarer aktueller Monatspreis ermittelt"),)},
    {"garage_id": "bs_magni", "facility": "BS_PH001_DYNAMISCH_AUSLAUSTUNGSDATEN_DEAKTIVIERT", "facility_key": "magni",
     "status": "sold_out", "quotes": (("Magni", "Dauerstellpl\u00e4tze laut Betreiber belegt"),)},
    {"garage_id": "bs_packhof", "facility": "BS_PH007_DYNAMISCH_AUSLAUSTUNGSDATEN_DEAKTIVIERT", "facility_key": "packhof",
     "status": "sold_out", "quotes": (("Packhof", "Dauerstellpl\u00e4tze laut Betreiber belegt"),)},
    {"garage_id": "bs_ring_center", "facility": "BS_RING_CENTER", "facility_key": "ring_center", "status": "no_price",
     "quotes": (("Ring-Center", "kein belastbarer aktueller Monatspreis ermittelt"),)},
    {"garage_id": "bs_schloss", "facility": "BS_PH011", "facility_key": "schloss", "status": "no_price",
     "quotes": (("Schloss", "kein belastbarer aktueller Monatspreis ermittelt"),)},
    {"garage_id": "bs_schuetzenstrasse", "facility": "BS_PH006", "facility_key": "schuetzenstrasse", "status": "no_price",
     "quotes": (("Sch\u00fctzenstra\u00dfe", "kein belastbarer aktueller Monatspreis ermittelt"),)},
    {"garage_id": "bs_steinstrasse", "facility": "BS_ADDITIONAL_2", "facility_key": "steinstrasse", "status": "published",
     "rule": "BS_ADDITIONAL_2_O01"},
    {"garage_id": "bs_wallstrasse", "facility": "BS_PH003", "facility_key": "braunschweig_wallstrasse", "status": "published",
     "offer": "BS_PH003_O01", "quotes": (("Wallstra\u00dfe", "Preis hinter Anmeldung"),)},
    {"garage_id": "bs_wilhelmstrasse", "facility": "BS_PH002", "facility_key": "wilhelmstrasse", "status": "period_unconfirmed",
     "quotes": (("Wilhelmstra\u00dfe", "Tarifblatt 75/79/88 EUR brutto"), ("Monatsbezug", "Monatsbezug fehlt ausdr\u00fccklich"))},
)
#: The facilities of the Braunschweig package that are no garage of the dataset, each recorded with its cheapest current product
#: (rows of the QA table, never a dataset row): ``record_id``, ``facility`` and ``facility_key``, ``facility_type`` (the
#: package's kind: garage or surface_parking), ``reason`` (a code of ``garage_qa.MONTHLY_NOT_USED_REASONS``) and ``why`` (a
#: sentence of the note). Eves and Fichtengrund are garages that the dataset does not list (the regional package holds no tariff
#: of them): their current published product is, with the used products of the dataset's Braunschweig garages, a value of
#: ASSUMPTION P13; the three station car parks (``decision`` used: the commuter product of their zone, spec Amendment G1) and
#: the two APCOA surface lots are no garage products and never count there.
_STATION_PRODUCT_WHY = (
    "the commuter product of the station zone {zone_id} (spec Amendment G1): commuter_day_eur is this amount over 21 working days "
    "(ASSUMPTION P2); a surface lot, no garage product and never a value of ASSUMPTION P13; the surface lot of the same site "
    "carries it as its monthly product (spec Amendment I1, owner decision A)")
BS_RECORDED_SPECS = (
    {"record_id": "monthly_bs_eves", "facility": "BS_EVES", "facility_key": "eves", "facility_type": "garage",
     "reason": "not_a_dataset_option",
     "why": "a garage open to long-term parkers only that the dataset does not list (no garage tariff in the regional package): "
            "no option, and its published product counts among the Braunschweig garage products of ASSUMPTION P13"},
    {"record_id": "monthly_bs_fichtengrund", "facility": "BS_FICHTENGRUND", "facility_key": "fichtengrund",
     "facility_type": "garage", "reason": "not_a_dataset_option",
     "why": "a garage that the dataset does not list (no garage tariff in the regional package): no option, and its published "
            "product counts among the Braunschweig garage products of ASSUMPTION P13; the package cautions that an earlier "
            "price and a promotion context exist and that the end date of this offer is unknown, so it is not a guaranteed "
            "regular price"},
    {"record_id": "monthly_bs_hbf_nord_p1", "facility": "BS_DB_P1", "facility_key": "braunschweig_hbf_nord_p1",
     "facility_type": "surface_parking", "decision": "used", "zone_ids": ("bs_hbf_p1_nord",),
     "why": _STATION_PRODUCT_WHY.format(zone_id="bs_hbf_p1_nord")},
    {"record_id": "monthly_bs_hbf_sued_p2", "facility": "BS_DB_P2", "facility_key": "braunschweig_hbf_sued_p2",
     "facility_type": "surface_parking", "decision": "used", "zone_ids": ("bs_hbf_p2_sued",),
     "why": _STATION_PRODUCT_WHY.format(zone_id="bs_hbf_p2_sued")},
    {"record_id": "monthly_bs_hbf_west_p3", "facility": "BS_DB_P3", "facility_key": "braunschweig_hbf_west_p3",
     "facility_type": "surface_parking", "decision": "used", "zone_ids": ("bs_hbf_p3_west",),
     "why": _STATION_PRODUCT_WHY.format(zone_id="bs_hbf_p3_west")},
    {"record_id": "monthly_bs_apcoa_s1", "facility": "BS_APCOA_S1", "facility_key": "s1_holwedestrasse",
     "facility_type": "surface_parking", "reason": "surface_lot",
     "why": "an open APCOA car park, no garage: it is no garage of the dataset and never a value of ASSUMPTION P13"},
    {"record_id": "monthly_bs_apcoa_s3", "facility": "BS_APCOA_S3", "facility_key": "s3_celler_strasse",
     "facility_type": "surface_parking", "reason": "surface_lot",
     "why": "an open APCOA car park, no garage: it is no garage of the dataset and never a value of ASSUMPTION P13"},
)


# ------------------------------------------------------------------------------------------------------------------------
# Large surface lots as off-street options (spec Amendment I, issue #436, Task 4j; owner decisions of 2026-10-13: "A ist
# ebenfalls entschieden", "P12 streichen, Belege einzeln", "die anderen beiden nehmen wir den avg oder median wo sie liegen",
# threshold 50 with 30 and 100 as sensitivities, all eight towns). The input is the research package
# ``Grossparkplaetze_ZGB8_2026-10-12.zip`` (``large_lots.py``). The selection (access and size, ASSUMPTION L50) is a rule of
# ``large_lots.decide`` and needs no entry here; an entry states the TARIFF decision (spec I3) of a site that is an option at a
# threshold of 30, 50 or 100, by the role of its package components. The values (rates, units, caps, windows) are read from the
# components and never typed here; the only numbers of this section are the elapsed minutes of a duration band, which are the
# boundaries between the published products (derived in the comment of the entry where they are not stated by the source).

#: The forms of an entry (exactly one):
#:
#: * ``zone_product``: the lot carries the street product of the zone (``parking_tariffs_2026.csv``) that contains its point
#:   (spec I1; owner decision A for the Hbf lots and the BgA car park Markthalle), ``check`` is the package component that
#:   documents the same tariff for the consistency check (a difference is named in the notes and the QA table, never resolved
#:   silently);
#: * ``free``: the component that states the lot free of charge (an explicit source statement, never a default);
#: * ``rate``: one rate in started units (+ ``cap`` = a ``<rule id>:cap`` component, ``window`` ``"rate"`` = the rate's explicit
#:   weekday window, ``None`` = none stated, ASSUMPTION P5);
#: * ``grace``: (free short stay, rate): the free short stay is a grace band (ASSUMPTION P10, spec I3), never a free day;
#: * ``bands``: the components of a duration schedule in order (their ``roles`` give the elapsed range of each);
#: * ``p14``: the tariff is not stated; the lot takes the median product of the priced lots of its zone (ASSUMPTION P14, spec I4);
#: * ``mixed``: paid and free subareas without separate verified geometry: no blanket tariff (spec I3), a QA row.
#:
#: ``roles`` types a component whose role is not the plain one (``large_lots.infer_rule_type``), ``ignored`` names the
#: components that set no value with the reason, ``comment`` explains the decision. The restrictions that the garage columns do not
#: carry (a maximum stay, a Saturday window, a customer discount) are NOT tariff components: they are listed per row in the notes
#: from the package components (``regional_garages._lot_limitations``) and counted in the run log.
_BROETCHENTASTE = ("the free short stay (the Broetchentaste, a free ticket of up to 30 min at the machine) is a grace band, never "
                   "a free day (spec I3): a stay not longer than it is free and a longer stay is billed from its arrival, the "
                   "free minutes are not deducted (ASSUMPTION P10); the package states that no additive free time is "
                   "proven for a longer paid stay")
_STARTED = "Mo-Sa windows are read on Monday to Friday of the model's average weekday (spec D1)"

#: Owner decision A (spec Amendment I1, ruling R-4j-6): a lot that is a zone too carries EXACTLY the row of its zone, the commuter
#: product per working day included: the lot key -> the ``record_id`` of the used recorded product of the Braunschweig monthly
#: package (``BS_RECORDED_SPECS``) that sets ``commuter_day_eur`` of the zone of the lot. The lot's ``monthly_eur`` is that amount
#: with its source page and product text, so the garage option prices a work or education stay like the zone (ASSUMPTION P2).
LARGE_LOT_ZONE_MONTHLY = {"braunschweig_hbf_nord": "monthly_bs_hbf_nord_p1", "braunschweig_hbf_sued": "monthly_bs_hbf_sued_p2",
                          "braunschweig_hbf_west": "monthly_bs_hbf_west_p3"}
_HBF_MONTH_NOT_CHEAPEST = ("the month product with a fixed place is not the cheapest month product ({cheapest} is the monthly "
                           "product of the lot); not used, as the zone row names it")
_HBF_WEEK = ("the week product is a product of another duration (spec D2 takes monthly and 30-day products); not used, as the "
             "zone row names it")
_HBF_MACHINE_MONTH = ("the month product at the machine is not the cheapest month product ({cheapest} is the monthly product of "
                      "the lot); not used, as the zone row names it")
LARGE_LOT_SPECS = {
    # ---- Braunschweig: lots with the ParkGO or BgA product of their zone, the Hbf lots of DB BahnPark (owner decision A), P+R
    "braunschweig_wendenstrasse_werder": {"zone_product": "bs_zone_ia", "check": "braunschweig_wendenstrasse_werder__1"},
    "braunschweig_grosser_hof": {"zone_product": "bs_zone_ia", "check": "braunschweig_grosser_hof__1"},
    "braunschweig_markthalle": {"zone_product": "bs_bga_markthalle", "check": "braunschweig_markthalle__1",
                                "decision_a": True},
    "braunschweig_hbf_nord": {
        "zone_product": "bs_hbf_p1_nord", "check": "braunschweig_hbf_nord__1", "decision_a": True,
        "ignored": {"braunschweig_hbf_nord__2": "the Kiss&Ride tariff of 15 min is a short-stay special tariff, not the general "
                                                "tariff; not modelled, as the zone row names it",
                    "braunschweig_hbf_nord__3": "the evening tariff (18:00-02:00, at most 5.00 EUR on the first parking day) is a "
                                                "time-limited special tariff; not modelled, as the zone row names it",
                    "braunschweig_hbf_nord__5": _HBF_MONTH_NOT_CHEAPEST.format(cheapest="braunschweig_hbf_nord__4")}},
    "braunschweig_hbf_sued": {
        "zone_product": "bs_hbf_p2_sued", "check": "braunschweig_hbf_sued__1", "decision_a": True,
        "ignored": {"braunschweig_hbf_sued__2": _HBF_WEEK,
                    "braunschweig_hbf_sued__3": _HBF_MACHINE_MONTH.format(cheapest="braunschweig_hbf_sued__4"),
                    "braunschweig_hbf_sued__5": _HBF_MONTH_NOT_CHEAPEST.format(cheapest="braunschweig_hbf_sued__4")}},
    "braunschweig_hbf_west": {
        "zone_product": "bs_hbf_p3_west", "check": "braunschweig_hbf_west__1", "decision_a": True,
        "ignored": {"braunschweig_hbf_west__2": _HBF_WEEK,
                    "braunschweig_hbf_west__3": _HBF_MACHINE_MONTH.format(cheapest="braunschweig_hbf_west__4"),
                    "braunschweig_hbf_west__5": _HBF_MONTH_NOT_CHEAPEST.format(cheapest="braunschweig_hbf_west__4")}},
    "braunschweig_willy_brandt_kommunal": {"zone_product": "bs_bga_willy_brandt_platz",
                                           "check": "braunschweig_willy_brandt_kommunal__1"},
    "braunschweig_pr_petzvalstrasse": {"free": "braunschweig_pr_petzvalstrasse__1"},
    "braunschweig_pr_gaensekamp": {"free": "braunschweig_pr_gaensekamp__1"},
    "braunschweig_pr_rote_wiese": {"free": "braunschweig_pr_rote_wiese__1"},
    "braunschweig_pr_thueringenplatz": {"free": "braunschweig_pr_thueringenplatz__1"},
    "braunschweig_pr_lincolnsiedlung": {"free": "braunschweig_pr_lincolnsiedlung__1"},
    "braunschweig_buergerpark": {"p14": True, "unknown_component": "braunschweig_buergerpark__1",
                                 "comment": "the general tariff is not stated by the package (a paid general offer; bathers "
                                            "get 50 % off the fee, a customer condition that is not modelled)"},
    # ---- Wolfsburg
    "WOB_PARK_1638410": {"zone_product": "wob_tarifzone_2", "check": "WOB_PARK_1638410__1"},
    # The two lots in a Wolfsburg area zone (spec v3 B2): the comment states the measured overlap of the lot outline with the street
    # section of the area (the Wolfsburg layer of Wolfsburg_Parkdaten.zip against researched_outlines.geojson and
    # researched_locations.geojson of the lot package, EPSG:25832) and the maximum stay of the lot's own component, which is
    # the maximum of the area zone.
    "WOB_PARK_1769502": {"zone_product": "wob_area_680", "check": "WOB_PARK_1769502__1",
                         "comment": "area zone evidence (spec v3 B2): the street section of area 680 of the Wolfsburg layer "
                                    "(979 m2) lies 100 % inside the OSM outline of the lot (way 115027361, 2,416 m2; the section "
                                    "is 40.5 % of the outline), the research point lies inside the section, and the component "
                                    "WOB_PARK_1769502__1 states a maximum stay of 60 min (its condition text names the "
                                    "Hoechstparkdauer 60 Minuten), the maximum of the area zone wob_area_680"},
    "WOB_PARK_1835014": {"zone_product": "wob_tarifzone_2", "check": "WOB_PARK_1835014__1"},
    "WOB_PARK_720901": {"zone_product": "wob_tarifzone_1", "check": "WOB_PARK_720901__1"},
    "WOB_PARK_1245204": {"zone_product": "wob_area_736", "check": "WOB_PARK_1245204__1",
                         "comment": "area zone evidence (spec v3 B2): the street section of area 736 of the Wolfsburg layer "
                                    "(1,317 m2) contains 89.1 % of the OSM outline of the lot (way 25672155, 680 m2; 606 m2 of "
                                    "it), the research point lies inside the section and the outline, and the component "
                                    "WOB_PARK_1245204__1 states a maximum stay of 120 min, the maximum of the area zone "
                                    "wob_area_736",
                         "ignored": {"WOB_PARK_1245204__2": "a short-stay product of at most 30 min that the package says is "
                                                            "not to be added as a discount to every longer stay"}},
    "WOB_PARK_1114127": {"rate": "WOB_PARK_1114127__1", "window": None,
                         "ignored": {"WOB_PARK_1114127__2": "free of charge only for guests of the BadeLand with a validated "
                                                            "parking ticket: a customer condition, never an unconditional "
                                                            "option"},
                         "comment": "public car park with a customer discount (the package class public_with_customer_discount): "
                                    "the general tariff of non-guests is the Zone I fee of the bath tariff; spec E14 had left "
                                    "this lot out as a customer regime, which the size policy of spec I2 reverses for the "
                                    "package class public_with_customer_discount"},
    "WOB_PARK_1835028": {"free": "WOB_PARK_1835028__1"},
    "WOB_THEATERPARKPLATZ": {"free": "WOB_THEATERPARKPLATZ__1"},
    "WOB_HOFFMANN_VON_FALLERSLEBEN": {"free": "WOB_HOFFMANN_VON_FALLERSLEBEN__1"},
    # ---- Wolfenbuettel
    "wf_bahnhof_checkin": {"free": "wf_bahnhof_checkin__1",
                           "comment": "mixed public and customer access (the package class mixed_public_and_customers): the public "
                                      "part of the site is an option (ruling R-4j-8, which the rule of ruling R-4j-3 covers: a lot "
                                      "with a customer condition is an option at its general tariff); derived capacity 204 of 334 "
                                      "(130 hotel guests), the package's derivation and no separately published value, which the "
                                      "size threshold uses and which no column of the dataset carries"},
    "wf_alte_spinnerei": {"free": "wf_alte_spinnerei__1"},
    "wf_am_forum": {"free": "wf_am_forum__1"},
    "wf_aral": {"free": "wf_aral__1"},
    "wf_lechlumer": {"free": "wf_lechlumer__1"},
    "wf_oderwald": {"free": "wf_oderwald__1"},
    "wf_lessingtheater": {
        "bands": ("wf_lessingtheater__1", "wf_lessingtheater__2", "wf_lessingtheater__3"), "window": None,
        "roles": {"wf_lessingtheater__1": {"type": "duration_total", "from": 0, "to": 15},
                  "wf_lessingtheater__2": {"type": "increment", "from": 15, "to": 120, "rounding": "started_unit"},
                  "wf_lessingtheater__3": {"type": "duration_total", "from": 120, "to": None}},
        "comment": "the package states: a minimum fee for the first 15 min, then a rate per started 3 min within the first 2 "
                   "hours, then the day ticket; the bands 0-15 total, 15-120 increment and 120- total are these three statements "
                   "(the increment band reaches the day ticket exactly at 120 min: 0.25 EUR + 35 started units of 3 min); the "
                   "fee times are not stated (ASSUMPTION P5)"},
    "wf_okeraue": {"rate": "wf_okeraue__1", "window": None,
                   "ignored": {"wf_okeraue__2": "free of charge only for guests of the Stadtbad and its offers: a customer "
                                                "condition, not an unconditional option"},
                   "comment": "outside users are admitted against a fee per started hour (the general tariff); guests of the "
                              "Stadtbad park free"},
    # ---- Gifhorn
    "gf_schottische_muehle": {"rate": "gf_schottische_muehle__1", "window": "rate", "cap": "gf_schottische_muehle__1:cap",
                              "ignored": {"gf_schottische_muehle__2": "states that parking outside the paid times is free, which "
                                                                       "the fee window of the rate already is"},
                              "comment": "a special card tariff is mentioned by the source without a price and is not modelled"},
    "gf_schuetzenplatz": {"free": "gf_schuetzenplatz__1"},
    "gf_iseparkplatz": {"rate": "gf_iseparkplatz__1", "window": "rate"},
    # ---- Helmstedt (the Broetchentaste of the three paid lots)
    "helmstedt_harsleber_tor": {"grace": ("helmstedt_harsleber_tor__2", "helmstedt_harsleber_tor__1"), "window": "rate",
                                "p10_basis": _BROETCHENTASTE},
    "helmstedt_holzberg": {"grace": ("helmstedt_holzberg__2", "helmstedt_holzberg__1"), "window": "rate",
                           "p10_basis": _BROETCHENTASTE},
    "helmstedt_suedertor": {"grace": ("helmstedt_suedertor__2", "helmstedt_suedertor__1"), "window": "rate",
                            "cap": "helmstedt_suedertor__3:cap", "p10_basis": _BROETCHENTASTE,
                            "comment": "the day ticket is valid until 09:00 of the next day; it is read as a maximum per stay "
                                       "(the day boundary of the cap is not stated)"},
    "helmstedt_wallplatz": {"grace": ("helmstedt_wallplatz__2", "helmstedt_wallplatz__1"), "window": "rate",
                            "p10_basis": _BROETCHENTASTE,
                            "comment": "only the paid subarea of the Wallplatz (43 spaces) is the lot; the free disc and long-term "
                                       "spaces under the same name are other regimes and not added"},
    "helmstedt_bahnhof": {"free": "helmstedt_bahnhof__1"},
    "helmstedt_maschweg": {"free": "helmstedt_maschweg__1"},
    "helmstedt_lappwaldsee": {"free": "helmstedt_lappwaldsee__1"},
    # ---- Peine (the paid zone II products: the cheapest product for a stay, spec E2)
    "peine_schuetzenplatz_sued": {"mixed": True},
    "peine_friedrich_ebert_platz": {"mixed": True},
    "peine_schuetzenplatz_nord": {"free": "peine_schuetzenplatz_nord__1"},
    "peine_kirchhofstrasse": {"free": "peine_kirchhofstrasse__1"},
    "peine_hagenmarkt": {"free": "peine_hagenmarkt__1"},
    "peine_schleifmuehlenweg": {"free": "peine_schleifmuehlenweg__1"},
    "peine_schlosswall": {
        "bands": ("peine_schlosswall__1", "peine_schlosswall__2", "peine_schlosswall__3"), "window": "rate",
        "roles": {"peine_schlosswall__1": {"type": "increment", "from": 0, "to": 150},
                  "peine_schlosswall__2": {"type": "duration_total", "from": 150, "to": 240},
                  "peine_schlosswall__3": {"type": "duration_total", "from": 240, "to": None}},
        "ignored": {"peine_schlosswall__4": "free of charge for vehicles with an electric plate for 3 h with a parking disc until "
                                            "2028-04-30: a vehicle-group condition, not a general tariff"},
        "comment": "the machine sells three products: a rate per started half hour, a 4 h half-day ticket and a day ticket; the "
                   "driver buys the cheapest product for the stay (ASSUMPTION P1 and spec E2), so the schedule is the rate up to "
                   "the duration at which it reaches the half-day ticket (5 started half hours = 2.50 EUR; the 6th would be 3.00 "
                   "EUR against 2.60 EUR) = 150 min, the half-day ticket up to its 4 h = 240 min, then the day ticket (the "
                   "metered price would exceed it from the first started half hour after 240 min); the fee window 09:00-17:00 "
                   "limits a stay to 8 h, the validity the municipal page names for the day ticket"},
    "peine_werderpark": {
        "bands": ("peine_werderpark__1", "peine_werderpark__2"), "window": "rate",
        "roles": {"peine_werderpark__1": {"type": "increment", "from": 0, "to": 150},
                  "peine_werderpark__2": {"type": "duration_total", "from": 150, "to": None}},
        "ignored": {"peine_werderpark__3": "an electric-plate exemption, a vehicle-group condition"},
        "comment": "a rate per started half hour and a 3 h ticket: the rate up to 150 min (2.50 EUR), then the ticket"},
    # ---- Salzgitter
    "sz_konrad_adenauer_16": {
        "bands": ("sz_konrad_adenauer_16__1", "sz_konrad_adenauer_16__2", "sz_konrad_adenauer_16__3"), "window": "rate",
        "roles": {"sz_konrad_adenauer_16__1": {"type": "free", "from": 0, "to": 30},
                  "sz_konrad_adenauer_16__2": {"type": "duration_total", "from": 30, "to": 60},
                  "sz_konrad_adenauer_16__3": {"type": "increment", "from": 60, "to": None}},
        "p10_basis": _BROETCHENTASTE,
        "comment": "up to 30 min free (the Broetchentaste), the first hour of a longer stay as a total, then a rate per further "
                   "6 min (the rounding is not stated, ASSUMPTION P4); the municipal tariff of the zone sz_lebenstedt"},
    "sz_rathausvorplatz": {
        "bands": ("sz_rathausvorplatz__1", "sz_rathausvorplatz__2", "sz_rathausvorplatz__3"), "window": "rate",
        "roles": {"sz_rathausvorplatz__1": {"type": "free", "from": 0, "to": 30},
                  "sz_rathausvorplatz__2": {"type": "duration_total", "from": 30, "to": 60},
                  "sz_rathausvorplatz__3": {"type": "increment", "from": 60, "to": None}},
        "p10_basis": _BROETCHENTASTE,
        "comment": "the same municipal tariff as the Konrad-Adenauer-Strasse lot; the capacity of 50 is an OSM tag (the historical "
                   "municipal survey states 47), so the lot is at the edge of the threshold"},
    "sz_berliner_6": {
        "bands": ("sz_berliner_6__1", "sz_berliner_6__2", "sz_berliner_6__3"), "window": "rate",
        "roles": {"sz_berliner_6__1": {"type": "free", "from": 0, "to": 30},
                  "sz_berliner_6__2": {"type": "duration_total", "from": 30, "to": 60},
                  "sz_berliner_6__3": {"type": "increment", "from": 60, "to": None}},
        "p10_basis": _BROETCHENTASTE, "comment": "the same municipal tariff as the Konrad-Adenauer-Strasse lot"},
    "sz_schillerstrasse": {"free": "sz_schillerstrasse__1"},
    "sz_see_ost_stadtbad": {"free": "sz_see_ost_stadtbad__1"},
    "sz_see_sued": {"free": "sz_see_sued__1"},
    "sz_see_west": {"free": "sz_see_west__1"},
    "sz_see_nord": {"free": "sz_see_nord__1"},
    "sz_bad_pr_petershagen_south": {"free": "sz_bad_pr_petershagen_south__1"},
    "sz_petershagen_public": {"free": "sz_petershagen_public__1",
                              "comment": "free of charge with a parking disc (a 2 h maximum stay in the paid-hours window); the "
                                         "disc and the maximum stay are restrictions and no price"},
    "sz_buechereivorplatz": {"p14": True, "unknown_component": "sz_buechereivorplatz__1",
                             "comment": "historically paid in the masterplan; the current tariff page does not name the lot and "
                                        "the package transfers no price from the Rathausvorplatz, which this decision does"},
    # ---- Goslar
    "gs_fuellekuhle": {"free": "gs_fuellekuhle__1"},
    "gs_osterfeld": {"free": "gs_osterfeld__1"},
    "gs_hildesheimer": {"free": "gs_hildesheimer__1"},
    "gs_hahnen_stab": {"free": "gs_hahnen_stab__1"},
    "gs_hahnen_teichwiesen": {"free": "gs_hahnen_teichwiesen__1"},
    # the same car parks as the single-site zones of the tariff table (ruling R-4j-7 a): zone AND lot (decision A), carrying
    # exactly the zone row; the unit that the package reads (an hour) and the zone's started half hour are both named
    "gs_baeringer": {"zone_product": "gs_parkplatz_baeringerstrasse", "check": "gs_baeringer__1", "decision_a": True,
                     "comment": _STARTED},
    "gs_marktstrasse": {"rate": "gs_marktstrasse__1", "window": "rate", "comment": _STARTED},
    "gs_kornstrasse": {"rate": "gs_kornstrasse__1", "window": "rate", "comment": _STARTED},
    "gs_klubgarten": {"zone_product": "gs_parkplatz_klubgartenstrasse_zob", "check": "gs_klubgarten__1",
                      "decision_a": True},
    "gs_christian_dohm": {"rate": "gs_christian_dohm__1", "window": "rate", "comment": _STARTED},
    "gs_glockengiesser": {"rate": "gs_glockengiesser__1", "window": "rate"},
}

#: The owner decision on the free Wolfsburg lots whose free status the package keeps open although the owner takes it on a per-site
#: source (spec Amendment I5a, spec v3 B4; ASSUMPTION P12 is withdrawn, so there is no default). A site in this table is the owner
#: decision that the lot is general public although the package class is unknown or event-conditional (``large_lots.decide`` takes
#: the keys of this table as the sites whose public access an owner decision establishes). Per site: the ``name``, the archived
#: ``pages`` ((file, quotation) pairs: files of the archived evidence, each with the quotation that must be in the page; the first
#: page is the source of the row), an ``assumption`` id of ``garages.ASSUMPTIONS`` that the row rests on (listed in its column
#: ``assumptions`` and named in its notes) and the ``condition`` of the owner decision (named in the notes) and,
#: where no page is archived or the run is given none, ``not_found`` (what was searched). A site whose pages are not given is a QA
#: row ``lot_free_evidence_missing``; it is never free without a source. A site that the package gives no position is a QA row
#: ``lot_no_position`` whatever its pages state (``LARGE_LOT_QA_DETAILS`` names the decision).
_RESTLUECKEN = "Parkdaten_Restluecken_2026-10-14.zip (the pages of evidence/wob_belege/; pass it as --wob-evidence-zip)"
_WMG_FREE_QUOTATION = "24/7 \u00f6ffentlich zug\u00e4nglich und kostenlos"
WOB_FREE_LOT_EVIDENCE = {
    "WOB_PARK_720902": {"name": "Allersee Suedufer",
                        "pages": (("wmg_schotter_suedufer.html", _WMG_FREE_QUOTATION),
                                  ("wmg_befestigt_suedufer.html", _WMG_FREE_QUOTATION)),
                        "condition": "Identification of the lot (owner decision 2026-10-14, spec v3 B4): the package records "
                                     "for both WMG pages the claim scope 'This named POI only. No extension to neighboring "
                                     "facility lots.' and names no polygon for their points (polygon match not established); "
                                     "the release extends the claim to this lot by the owner's decision and on the nearness of "
                                     "the point given in the page sentences above; both WMG pages state the same free, public, "
                                     "24/7 use",
                        "not_found": "the free status rests on the pages wmg_schotter_suedufer.html and "
                                     "wmg_befestigt_suedufer.html (WMG Wolfsburg, free, public, 24 h) of " + _RESTLUECKEN
                                     + "; this run was given no such evidence"},
    "WOB_PARK_1703940": {"name": "Freibad Fallersleben",
                         "pages": (("vfb_geschaeftsstelle.html", "neben dem Freibad oder gegen\u00fcber von der Schule auf den "
                                                                 "kostenlosen Parkpl\u00e4tzen parken"),),
                         "condition": "Identification of the lot (owner decision 2026-10-14, spec v3 B4): the page speaks of "
                                      "arrival at the club office, names the free car parks relatively (next to the Freibad or "
                                      "opposite the school) and states no hours or visitor rule; the package establishes neither "
                                      "the exact surface, nor all school car parks, nor an unrestricted 24/7 use, so the lot is "
                                      "taken as the car park next to the Freibad on the owner's decision; the free schedule "
                                      "'0- free' (free for every stay) is the owner's decision too: the page gives no hours or "
                                      "stay rule and the package makes no unrestricted 24/7 claim",
                         "not_found": "the free status rests on the page vfb_geschaeftsstelle.html (VfB Fallersleben, "
                                      "'Unser Standort in Fallersleben') of " + _RESTLUECKEN + "; this run was given no such "
                                      "evidence"},
    "WOB_ARENA_P1": {"name": "Volkswagen Arena P1",
                     "pages": (("vfl_anreise.html", "kann in der Regel auf den Parkpl\u00e4tzen P1 und P2 kostenfrei geparkt "
                                                    "werden"),),
                     "assumption": "W-match",
                     "condition": "Owner decision 2026-10-14, spec v3 B4: the VfL page states that P1 and P2 can generally be used "
                                  "free of charge outside match days; on match days P1 is open to P1/TG permit holders and P2 to "
                                  "P2 permit holders only, and larger events can restrict the access partly or completely. Match "
                                  "days and event closures are NOT modelled: the lot is a free option on every simulated weekday",
                     "not_found": "the free status outside match days rests on the page vfl_anreise.html (VfL Wolfsburg, "
                                  "Anreise zur Volkswagen Arena) of " + _RESTLUECKEN + "; this run was given no such evidence"},
    "WOB_ARENA_P2": {"name": "Volkswagen Arena P2",
                     "pages": (("vfl_anreise.html", "kann in der Regel auf den Parkpl\u00e4tzen P1 und P2 kostenfrei geparkt "
                                                    "werden"),),
                     "assumption": "W-match",
                     "condition": "Owner decision 2026-10-14, spec v3 B4: as for P1 (free outside match days, match days and "
                                  "event closures not modelled)",
                     "not_found": "the free status outside match days rests on the page vfl_anreise.html (VfL Wolfsburg, "
                                  "Anreise zur Volkswagen Arena) of " + _RESTLUECKEN + "; this run was given no such evidence"},
    "WOB_PARK_327697": {"name": "VW-Bad", "pages": (),
                        "not_found": "the WMG flyer of 2024 that names the car park at the VW-Bad free (the package's source "
                                     "EV_WMG_GREEN_2024) is HTTP 404 and no other page was found: the package keeps only a search "
                                     "index entry, which it does not take as a tariff"},
}

#: The municipal points of the Wolfsburg city layer that are subpoints of a lot of the research package and never a second row of
#: the site (spec Amendment I2): the point and the reason text of its QA row; the package's alias file names the lot.
LARGE_LOT_SUBPOINTS = {
    "WOB_PARK_1048584": "the disabled-parking point of the Theater car park (a subset of the Theater lot for holders of a blue "
                        "permit, no option of the general public and no second row of the site)"}

#: What spec Amendment I5a states about the Wolfsburg points that carried the withdrawn free default of ASSUMPTION P12 and are no
#: option (the owner's reasons, appended to the QA note of the record; the reason code is the one the policy yields). Reislinger
#: Markt: spec I5a says "reported 48 < 50"; the package states an area estimate of 60 and no reported capacity, so the figure 48 is
#: not traceable in the package (named in the note, not resolved).
LARGE_LOT_QA_DETAILS = {
    "WOB_PARK_327689": "spec Amendment I5a: Schloss, only wedding guests are evidenced, no general public use",
    "WOB_PARK_524301": "spec Amendment I5a: Dunantplatz, OSM access=customers and an 8-year-old press note only",
    "WOB_PARK_589853": "spec Amendment I5a: Reislinger Markt, a reported capacity of 48 below the threshold; the research package "
                       "states no reported capacity but an area estimate of 60, so the figure 48 is not traceable in the package "
                       "and the record is no option on its unknown access alone",
    "WOB_PARK_262165": "spec Amendment I5a: no evidence at all, about 17 spaces by the area of the matched OSM parking polygon "
                       "(443.77 m2 / 25 m2)",
    "WOB_PARK_1703946": "spec Amendment I5a: neither a fee nor an access evidence",
    "WOB_ARENA_P2": "owner decision 2026-10-14 (spec v3 B4): free outside match days on the page of the VfL (pages archived in "
                    "Parkdaten_Restluecken_2026-10-14.zip), but the large-lot package gives P2 no position at all "
                    "(coordinate_role unresolved; the event operator's page of the package names only 'northwest behind the "
                    "training grounds'), so P2 cannot be an option until a position is supplied: none is invented",
}

#: Spec v3 B7 (issue #436, ruling R-B-18, owner decisions of 2026-10-15): the facilities that a source DOCUMENTS and no package
#: locates, located in the pinned OpenStreetMap extract by ``documented_lots.match_locator``. Per entry: ``key`` (the garage_id of
#: the option and the key of its QA rows), ``town`` (a key of ``OTHER_TOWNS``), ``kind`` (garage or surface_lot), ``name``,
#: ``facility`` (the facility id of the regional package, or a stable id of the source where the package holds none),
#: ``evidence`` (which reader and tariff the row rests on: ``seesen_city_page`` is the free garage on the owner's transcription of
#: the city page, ``harzventure`` a lot of the HarzVenture page with its own tariff, ``brochure_free`` a car park area that the
#: town's brochure of 2021/22 names free), ``locator`` (the keys of ``documented_lots.match_locator``; ``"page_address"`` takes the
#: address from the page's heading; None where the source gives no way to locate it, with ``no_locator_reason``), ``harzventure``
#: (the lot of ``documented_lots.HARZVENTURE_LOT_KEYS``) and ``rate_rule`` (the rule of the regional package that states the rate).
#: Rules of the release (unchanged by B7): a surface lot is an option when its OSM capacity (``documented_lots.osm_capacity``: the
#: tag, else the area estimate of the large-lot package) reaches the size threshold (ASSUMPTION L50), else a QA row with the
#: existing reasons; a garage has no size rule; no position (unmatched, ambiguous) is the QA reason lot_no_position; a match that
#: is one of the zone car parks (``ZONE_CAR_PARK_FACILITIES``) is no new option (its zone is the tariff there) and a QA row of the
#: reason zone_street_product that names the zone and compares the page with its row.
DOCUMENTED_OPTIONS = (
    {"key": "se_parkhaus_bahnhofstrasse", "town": "se", "kind": "garage", "name": "Parkhaus Bahnhofstrasse",
     "facility": "se_parkhaus", "evidence": "seesen_city_page",
     "locator": {"osm_id": "way/91181792", "names": ("Parkhaus Bahnhofstrasse",)},
     "comment": "the private garage Bahnhofstrasse of Seesen: the city page 'Parken in Seesen' states the upper levels free all "
                "day for long-stay parkers and the lower levels free up to 2 h, and the owner accepted that page as the free "
                "evidence (decision 2026-10-15); the row is free for every stay (the free schedule '0- free'), the 2 h limit of "
                "the lower levels is not modelled, the garage is private (the page names no operator) and no capacity is "
                "documented (a garage has no size rule)"},
    {"key": "bh_harzventure_tourist_information", "town": "bh", "kind": "surface_lot",
     "name": "Parkplatz Tourist-Information (HarzVenture)", "facility": "badharzburg_tourist_information_harzventure",
     "evidence": "harzventure", "harzventure": "parkplatz_tourist_information", "locator": "page_address",
     "rate_rule": "badharzburg_tourist_information_harzventure_REV_01"},
    {"key": "bh_harzventure_hotel_seela", "town": "bh", "kind": "surface_lot",
     "name": "Parkplatz gegenueber Hotel Seela (HarzVenture)", "facility": "badharzburg_opposite_hotel_seela",
     "evidence": "harzventure", "harzventure": "parkplatz_gegenueber_hotel_seela", "locator": "page_address",
     "rate_rule": "badharzburg_opposite_hotel_seela_REV_01"},
    {"key": "bh_harzventure_seela_tankstelle", "town": "bh", "kind": "surface_lot",
     "name": "Parkplatz zwischen Hotel Seela und Tankstelle (HarzVenture)",
     "facility": "badharzburg_between_seela_petrol_station", "evidence": "harzventure",
     "harzventure": "parkplatz_zwischen_hotel_seela_und_tankstelle", "locator": None,
     "no_locator_reason": "the page gives no address and describes the lot only relative to two places ('between Hotel Seela and "
                          "the petrol station'); the matching function has keys for an object, a name, an address and ONE named "
                          "place, and no rule for a position between two, which no nearest-to-one-place search can replace "
                          "without taking the lot of the hotel for the lot of the petrol station",
     "rate_rule": "badharzburg_between_seela_petrol_station_REV_01"},
    {"key": "bh_galopprennbahn", "town": "bh", "kind": "surface_lot", "name": "Parkplatz Galopprennbahn (Bad Harzburg)",
     "facility": "bad_harzburg_brochure_2022:galopprennbahn", "evidence": "brochure_free",
     "locator": {"anchor_names": ("Galopprennbahn Bad Harzburg",)}},
    {"key": "bh_silberbornbad", "town": "bh", "kind": "surface_lot", "name": "Parkplatz Silberbornbad (Bad Harzburg)",
     "facility": "bad_harzburg_brochure_2022:silberbornbad", "evidence": "brochure_free",
     "locator": {"anchor_names": ("Silberbornbad",)}},
)
#: The candidate record of ``PACKAGE_CANDIDATES`` that an option of ``DOCUMENTED_OPTIONS`` replaces when the run builds the
#: documented options (key of the option): the QA row of the candidate is then the garage row of the option.
DOCUMENTED_CANDIDATE_RELEASES = {"candidate_se_parkhaus": "se_parkhaus_bahnhofstrasse"}
#: The zone car parks of Bad Harzburg: the facility ids of the regional package whose OpenStreetMap ways (the package's
#: ``osm_way_ids``) are the outlines of the five zones of the tariff table; the zone id of the table is the facility id.
ZONE_CAR_PARK_FACILITIES = ("bh_sole_therme", "bh_kurpark", "bh_grossparkplatz", "bh_burgberg", "bh_berliner_platz")
