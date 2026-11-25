"""Seed the committed CHE table of student origins (Kreis of the school leaving certificate).

Hand-read from the CHE DatenCHECK 2/2024 pages (data: Statistisches Bundesamt),
winter semester 2022/23, in two views that are pinned in the constants below:

* ``INSTITUTION_ORIGINS``: per institution the (up to) ten largest origin Kreise, plus
  the abroad, other and total rows.
* ``KREIS_DESTINATIONS``: per origin Kreis the (up to) ten institutions with most
  students from there, plus the total.

Cells below the tenth value are censored by the source. The origin is the Kreis of the
school leaving certificate, not the place of residence.

``--list-required-kreise`` prints the origin Kreise that must be hand-read (every Kreis
within ``--max-distance-km`` of a university site) and exits without writing.

Usage: python scripts/seed_che_student_origins.py
       python scripts/seed_che_student_origins.py --list-required-kreise \
           --vg250 <vg250 gpkg zip> --sites <lsn_university_sites.csv>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEMESTER = "WS 2022/23"
SOURCE = "CHE DatenCHECK 2/2024 (data: Statistisches Bundesamt)"
OUTPUT_PATH = Path("eqasim-data/data/braunschweig/schools/che_student_origins_ws2022_23.csv")
METRIC_CRS = "EPSG:25832"
MAX_LISTED_RANKS = 10

# Filled together with the two lists below; main() refuses to run while any is empty.
ACCESS_DATE = "2026-11-25"

# (institution, rank_or_kind, origin_ars5, origin_name, students_persons)
# rank_or_kind is 1..10 (listed), "abroad", "other" or "total"; abroad/other/total rows
# carry an empty origin_ars5.
# OMITTED, not forgotten: the art academy in Braunschweig (Hochschule fuer Bildende Kuenste
# Braunschweig), a local institution of the site table, has NO origin list here. The CHE
# institution view lists only institutions with at least 1,000 students, and its selector did
# not offer this one (checked 2026-11-25); it appears only as a destination in the Kreis view
# (KREIS_DESTINATIONS). Phase 1 needs an explicit rule for its origins; no list is invented.
INSTITUTION_ORIGINS: list[tuple[str, int | str, str, str, int]] = [
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 1, '03101', 'Braunschweig, Stadt', 2417),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 2, '03241', 'Region Hannover', 1027),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 3, '03103', 'Wolfsburg, Stadt', 624),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 4, '03151', 'Gifhorn', 579),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 5, '03157', 'Peine', 533),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 6, '03158', 'Wolfenbüttel', 466),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 7, '03254', 'Hildesheim', 457),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 8, '03102', 'Salzgitter, Stadt', 426),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 9, '03153', 'Goslar', 363),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 10, '02000', 'Hamburg', 316),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 'abroad', '', '', 2648),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 'other', '', '', 6953),
    ('Technische Universität Carolo-Wilhelmina zu Braunschweig', 'total', '', '', 16809),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 1, '03101', 'Braunschweig, Stadt', 2067),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 2, '03103', 'Wolfsburg, Stadt', 927),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 3, '03241', 'Region Hannover', 682),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 4, '03102', 'Salzgitter, Stadt', 589),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 5, '03158', 'Wolfenbüttel', 540),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 6, '03151', 'Gifhorn', 531),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 7, '03157', 'Peine', 336),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 8, '03153', 'Goslar', 306),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 9, '03360', 'Uelzen', 277),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 10, '03254', 'Hildesheim', 271),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 'abroad', '', '', 703),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 'other', '', '', 3538),
    ('Ostfalia Hochschule für angewandte Wissenschaften', 'total', '', '', 10767),
    ('Technische Universität Clausthal', 1, '03153', 'Goslar', 167),
    ('Technische Universität Clausthal', 2, '03159', 'Göttingen', 117),
    ('Technische Universität Clausthal', 3, '03241', 'Region Hannover', 71),
    ('Technische Universität Clausthal', 4, '03155', 'Northeim', 63),
    ('Technische Universität Clausthal', 5, '03254', 'Hildesheim', 49),
    ('Technische Universität Clausthal', 6, '03101', 'Braunschweig, Stadt', 39),
    ('Technische Universität Clausthal', 7, '02000', 'Hamburg', 31),
    ('Technische Universität Clausthal', 8, '11000', 'Berlin', 29),
    ('Technische Universität Clausthal', 9, '05315', 'Köln, Stadt', 24),
    ('Technische Universität Clausthal', 10, '05711', 'Bielefeld, Stadt', 20),
    ('Technische Universität Clausthal', 'abroad', '', '', 1522),
    ('Technische Universität Clausthal', 'other', '', '', 892),
    ('Technische Universität Clausthal', 'total', '', '', 3024),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 1, '03241', 'Region Hannover', 10506),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 2, '03254', 'Hildesheim', 1249),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 3, '03257', 'Schaumburg', 818),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 4, '03351', 'Celle', 682),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 5, '03252', 'Hameln-Pyrmont', 534),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 6, '03256', 'Nienburg (Weser)', 421),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 7, '05770', 'Minden-Lübbecke', 421),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 8, '03358', 'Heidekreis', 379),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 9, '03157', 'Peine', 368),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 10, '03101', 'Braunschweig, Stadt', 364),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 'abroad', '', '', 3513),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 'other', '', '', 8767),
    ('Gottfried Wilhelm Leibniz Universität Hannover', 'total', '', '', 28022),
    ('Hochschule Hannover', 1, '03241', 'Region Hannover', 3644),
    ('Hochschule Hannover', 2, '03254', 'Hildesheim', 346),
    ('Hochschule Hannover', 3, '03257', 'Schaumburg', 259),
    ('Hochschule Hannover', 4, '03351', 'Celle', 212),
    ('Hochschule Hannover', 5, '03252', 'Hameln-Pyrmont', 189),
    ('Hochschule Hannover', 6, '03256', 'Nienburg (Weser)', 161),
    ('Hochschule Hannover', 7, '05770', 'Minden-Lübbecke', 124),
    ('Hochschule Hannover', 8, '03101', 'Braunschweig, Stadt', 108),
    ('Hochschule Hannover', 9, '03157', 'Peine', 106),
    ('Hochschule Hannover', 10, '03159', 'Göttingen', 92),
    ('Hochschule Hannover', 'abroad', '', '', 790),
    ('Hochschule Hannover', 'other', '', '', 2880),
    ('Hochschule Hannover', 'total', '', '', 8911),
    ('Medizinische Hochschule Hannover (MHH)', 1, '03241', 'Region Hannover', 798),
    ('Medizinische Hochschule Hannover (MHH)', 2, '03254', 'Hildesheim', 108),
    ('Medizinische Hochschule Hannover (MHH)', 3, '04011', 'Bremen, Stadt', 106),
    ('Medizinische Hochschule Hannover (MHH)', 4, '11000', 'Berlin', 95),
    ('Medizinische Hochschule Hannover (MHH)', 5, '03404', 'Osnabrück, Stadt', 70),
    ('Medizinische Hochschule Hannover (MHH)', 6, '03101', 'Braunschweig, Stadt', 69),
    ('Medizinische Hochschule Hannover (MHH)', 7, '03257', 'Schaumburg', 62),
    ('Medizinische Hochschule Hannover (MHH)', 8, '05770', 'Minden-Lübbecke', 58),
    ('Medizinische Hochschule Hannover (MHH)', 9, '03157', 'Peine', 56),
    ('Medizinische Hochschule Hannover (MHH)', 10, '05758', 'Herford', 53),
    ('Medizinische Hochschule Hannover (MHH)', 'abroad', '', '', 479),
    ('Medizinische Hochschule Hannover (MHH)', 'other', '', '', 1962),
    ('Medizinische Hochschule Hannover (MHH)', 'total', '', '', 3916),
    ('Stiftung Tierärztliche Hochschule Hannover', 1, '03241', 'Region Hannover', 136),
    ('Stiftung Tierärztliche Hochschule Hannover', 2, '02000', 'Hamburg', 69),
    ('Stiftung Tierärztliche Hochschule Hannover', 3, '05566', 'Steinfurt', 45),
    ('Stiftung Tierärztliche Hochschule Hannover', 4, '04011', 'Bremen, Stadt', 41),
    ('Stiftung Tierärztliche Hochschule Hannover', 5, '05770', 'Minden-Lübbecke', 34),
    ('Stiftung Tierärztliche Hochschule Hannover', 6, '03453', 'Cloppenburg', 32),
    ('Stiftung Tierärztliche Hochschule Hannover', 7, '05515', 'Münster, Stadt', 27),
    ('Stiftung Tierärztliche Hochschule Hannover', 8, '05562', 'Recklinghausen', 27),
    ('Stiftung Tierärztliche Hochschule Hannover', 9, '03159', 'Göttingen', 26),
    ('Stiftung Tierärztliche Hochschule Hannover', 10, '05315', 'Köln, Stadt', 26),
    ('Stiftung Tierärztliche Hochschule Hannover', 'abroad', '', '', 195),
    ('Stiftung Tierärztliche Hochschule Hannover', 'other', '', '', 1687),
    ('Stiftung Tierärztliche Hochschule Hannover', 'total', '', '', 2345),
    ('Hochschule für Musik, Theater und Medien Hannover', 1, '03241', 'Region Hannover', 274),
    ('Hochschule für Musik, Theater und Medien Hannover', 2, '03254', 'Hildesheim', 38),
    ('Hochschule für Musik, Theater und Medien Hannover', 3, '11000', 'Berlin', 36),
    ('Hochschule für Musik, Theater und Medien Hannover', 4, '02000', 'Hamburg', 35),
    ('Hochschule für Musik, Theater und Medien Hannover', 5, '03101', 'Braunschweig, Stadt', 25),
    ('Hochschule für Musik, Theater und Medien Hannover', 6, '03351', 'Celle', 16),
    ('Hochschule für Musik, Theater und Medien Hannover', 7, '03257', 'Schaumburg', 15),
    ('Hochschule für Musik, Theater und Medien Hannover', 8, '04011', 'Bremen, Stadt', 13),
    ('Hochschule für Musik, Theater und Medien Hannover', 9, '03157', 'Peine', 12),
    ('Hochschule für Musik, Theater und Medien Hannover', 10, '03159', 'Göttingen', 12),
    ('Hochschule für Musik, Theater und Medien Hannover', 'abroad', '', '', 476),
    ('Hochschule für Musik, Theater und Medien Hannover', 'other', '', '', 483),
    ('Hochschule für Musik, Theater und Medien Hannover', 'total', '', '', 1435),
    ('Georg-August-Universität Göttingen', 1, '03159', 'Göttingen', 2429),
    ('Georg-August-Universität Göttingen', 2, '03241', 'Region Hannover', 1539),
    ('Georg-August-Universität Göttingen', 3, '03155', 'Northeim', 830),
    ('Georg-August-Universität Göttingen', 4, '03254', 'Hildesheim', 805),
    ('Georg-August-Universität Göttingen', 5, '02000', 'Hamburg', 708),
    ('Georg-August-Universität Göttingen', 6, '06611', 'Kassel, documenta-Stadt', 683),
    ('Georg-August-Universität Göttingen', 7, '03101', 'Braunschweig, Stadt', 519),
    ('Georg-August-Universität Göttingen', 8, '11000', 'Berlin', 470),
    ('Georg-August-Universität Göttingen', 9, '04011', 'Bremen, Stadt', 346),
    ('Georg-August-Universität Göttingen', 10, '06636', 'Werra-Meißner-Kreis', 343),
    ('Georg-August-Universität Göttingen', 'abroad', '', '', 4252),
    ('Georg-August-Universität Göttingen', 'other', '', '', 14432),
    ('Georg-August-Universität Göttingen', 'total', '', '', 27356),
    ('Stiftung Universität Hildesheim', 1, '03241', 'Region Hannover', 2184),
    ('Stiftung Universität Hildesheim', 2, '03254', 'Hildesheim', 1162),
    ('Stiftung Universität Hildesheim', 3, '03101', 'Braunschweig, Stadt', 222),
    ('Stiftung Universität Hildesheim', 4, '03159', 'Göttingen', 183),
    ('Stiftung Universität Hildesheim', 5, '03252', 'Hameln-Pyrmont', 177),
    ('Stiftung Universität Hildesheim', 6, '03157', 'Peine', 167),
    ('Stiftung Universität Hildesheim', 7, '03351', 'Celle', 126),
    ('Stiftung Universität Hildesheim', 8, '03153', 'Goslar', 122),
    ('Stiftung Universität Hildesheim', 9, '03102', 'Salzgitter, Stadt', 111),
    ('Stiftung Universität Hildesheim', 10, '11000', 'Berlin', 110),
    ('Stiftung Universität Hildesheim', 'abroad', '', '', 634),
    ('Stiftung Universität Hildesheim', 'other', '', '', 2862),
    ('Stiftung Universität Hildesheim', 'total', '', '', 8060),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 1, '03241', 'Region Hannover', 904),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 2, '03254', 'Hildesheim', 745),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 3, '03159', 'Göttingen', 545),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 4, '03155', 'Northeim', 230),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 5, '05762', 'Höxter', 191),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 6, '03252', 'Hameln-Pyrmont', 170),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 7, '03101', 'Braunschweig, Stadt', 144),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 8, '03255', 'Holzminden', 142),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 9, '03153', 'Goslar', 98),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 10, '02000', 'Hamburg', 93),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 'abroad', '', '', 384),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 'other', '', '', 2602),
    ('Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 'total', '', '', 6248),
    ('Otto-von-Guericke-Universität Magdeburg', 1, '15003', 'Magdeburg, Landeshauptstadt', 1883),
    ('Otto-von-Guericke-Universität Magdeburg', 2, '15083', 'Börde', 589),
    ('Otto-von-Guericke-Universität Magdeburg', 3, '15089', 'Salzlandkreis', 362),
    ('Otto-von-Guericke-Universität Magdeburg', 4, '11000', 'Berlin', 339),
    ('Otto-von-Guericke-Universität Magdeburg', 5, '15085', 'Harz', 327),
    ('Otto-von-Guericke-Universität Magdeburg', 6, '15090', 'Stendal', 286),
    ('Otto-von-Guericke-Universität Magdeburg', 7, '15086', 'Jerichower Land', 279),
    ('Otto-von-Guericke-Universität Magdeburg', 8, '03103', 'Wolfsburg, Stadt', 199),
    ('Otto-von-Guericke-Universität Magdeburg', 9, '03101', 'Braunschweig, Stadt', 169),
    ('Otto-von-Guericke-Universität Magdeburg', 10, '03241', 'Region Hannover', 158),
    ('Otto-von-Guericke-Universität Magdeburg', 'abroad', '', '', 3429),
    ('Otto-von-Guericke-Universität Magdeburg', 'other', '', '', 4814),
    ('Otto-von-Guericke-Universität Magdeburg', 'total', '', '', 12834),
    ('Hochschule Magdeburg-Stendal', 1, '15003', 'Magdeburg, Landeshauptstadt', 1031),
    ('Hochschule Magdeburg-Stendal', 2, '15090', 'Stendal', 343),
    ('Hochschule Magdeburg-Stendal', 3, '15083', 'Börde', 314),
    ('Hochschule Magdeburg-Stendal', 4, '15085', 'Harz', 207),
    ('Hochschule Magdeburg-Stendal', 5, '15089', 'Salzlandkreis', 203),
    ('Hochschule Magdeburg-Stendal', 6, '15086', 'Jerichower Land', 182),
    ('Hochschule Magdeburg-Stendal', 7, '11000', 'Berlin', 139),
    ('Hochschule Magdeburg-Stendal', 8, '15081', 'Altmarkkreis Salzwedel', 114),
    ('Hochschule Magdeburg-Stendal', 9, '15082', 'Anhalt-Bitterfeld', 110),
    ('Hochschule Magdeburg-Stendal', 10, '14713', 'Leipzig, Stadt', 76),
    ('Hochschule Magdeburg-Stendal', 'abroad', '', '', 341),
    ('Hochschule Magdeburg-Stendal', 'other', '', '', 2192),
    ('Hochschule Magdeburg-Stendal', 'total', '', '', 5252),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 1, '15085', 'Harz', 453),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 2, '15003', 'Magdeburg, Landeshauptstadt', 151),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 3, '03153', 'Goslar', 150),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 4, '15083', 'Börde', 99),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 5, '03101', 'Braunschweig, Stadt', 87),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 6, '15089', 'Salzlandkreis', 85),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 7, '15082', 'Anhalt-Bitterfeld', 83),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 8, '03241', 'Region Hannover', 69),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 9, '15002', 'Halle (Saale), Stadt', 68),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 10, '15087', 'Mansfeld-Südharz', 68),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 'abroad', '', '', 205),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 'other', '', '', 1501),
    ('Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 'total', '', '', 3019),
    ('Leuphana Universität Lüneburg', 1, '02000', 'Hamburg', 1948),
    ('Leuphana Universität Lüneburg', 2, '03355', 'Lüneburg', 553),
    ('Leuphana Universität Lüneburg', 3, '03353', 'Harburg', 478),
    ('Leuphana Universität Lüneburg', 4, '01062', 'Stormarn', 251),
    ('Leuphana Universität Lüneburg', 5, '03241', 'Region Hannover', 225),
    ('Leuphana Universität Lüneburg', 6, '01053', 'Herzogtum Lauenburg', 224),
    ('Leuphana Universität Lüneburg', 7, '11000', 'Berlin', 216),
    ('Leuphana Universität Lüneburg', 8, '03359', 'Stade', 192),
    ('Leuphana Universität Lüneburg', 9, '01056', 'Pinneberg', 161),
    ('Leuphana Universität Lüneburg', 10, '03360', 'Uelzen', 145),
    ('Leuphana Universität Lüneburg', 'abroad', '', '', 993),
    ('Leuphana Universität Lüneburg', 'other', '', '', 4104),
    ('Leuphana Universität Lüneburg', 'total', '', '', 9490),
]

# (origin_ars5, origin_name, rank_or_kind, institution, students_persons)
# rank_or_kind is 1..10 (listed) or "total"; the total row carries an empty institution.
KREIS_DESTINATIONS: list[tuple[str, str, int | str, str, int]] = [
    ('03101', 'Braunschweig, Stadt', 1, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 2417),
    ('03101', 'Braunschweig, Stadt', 2, 'Ostfalia Hochschule für angewandte Wissenschaften', 2067),
    ('03101', 'Braunschweig, Stadt', 3, 'Georg-August-Universität Göttingen', 519),
    ('03101', 'Braunschweig, Stadt', 4, 'IU Internationale Hochschule in Erfurt', 405),
    ('03101', 'Braunschweig, Stadt', 5, 'Gottfried Wilhelm Leibniz Universität Hannover', 364),
    ('03101', 'Braunschweig, Stadt', 6, 'FernUniversität in Hagen', 300),
    ('03101', 'Braunschweig, Stadt', 7, 'Stiftung Universität Hildesheim', 222),
    ('03101', 'Braunschweig, Stadt', 8, 'Hochschule für Bildende Künste Braunschweig', 216),
    ('03101', 'Braunschweig, Stadt', 9, 'Otto-von-Guericke-Universität Magdeburg', 169),
    ('03101', 'Braunschweig, Stadt', 10, 'Universität Leipzig', 150),
    ('03101', 'Braunschweig, Stadt', 'total', '', 11629),
    ('03102', 'Salzgitter, Stadt', 1, 'Ostfalia Hochschule für angewandte Wissenschaften', 589),
    ('03102', 'Salzgitter, Stadt', 2, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 426),
    ('03102', 'Salzgitter, Stadt', 3, 'Stiftung Universität Hildesheim', 111),
    ('03102', 'Salzgitter, Stadt', 4, 'Georg-August-Universität Göttingen', 103),
    ('03102', 'Salzgitter, Stadt', 5, 'IU Internationale Hochschule in Erfurt', 102),
    ('03102', 'Salzgitter, Stadt', 6, 'Gottfried Wilhelm Leibniz Universität Hannover', 88),
    ('03102', 'Salzgitter, Stadt', 7, 'FernUniversität in Hagen', 55),
    ('03102', 'Salzgitter, Stadt', 8, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 40),
    ('03102', 'Salzgitter, Stadt', 9, 'Universität Kassel', 29),
    ('03102', 'Salzgitter, Stadt', 10, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 25),
    ('03102', 'Salzgitter, Stadt', 'total', '', 2277),
    ('03103', 'Wolfsburg, Stadt', 1, 'Ostfalia Hochschule für angewandte Wissenschaften', 927),
    ('03103', 'Wolfsburg, Stadt', 2, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 624),
    ('03103', 'Wolfsburg, Stadt', 3, 'IU Internationale Hochschule in Erfurt', 201),
    ('03103', 'Wolfsburg, Stadt', 4, 'Otto-von-Guericke-Universität Magdeburg', 199),
    ('03103', 'Wolfsburg, Stadt', 5, 'Gottfried Wilhelm Leibniz Universität Hannover', 178),
    ('03103', 'Wolfsburg, Stadt', 6, 'Georg-August-Universität Göttingen', 150),
    ('03103', 'Wolfsburg, Stadt', 7, 'FernUniversität in Hagen', 79),
    ('03103', 'Wolfsburg, Stadt', 8, 'Hochschule Magdeburg-Stendal', 69),
    ('03103', 'Wolfsburg, Stadt', 9, 'Stiftung Universität Hildesheim', 67),
    ('03103', 'Wolfsburg, Stadt', 10, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 59),
    ('03103', 'Wolfsburg, Stadt', 'total', '', 4511),
    ('03151', 'Gifhorn', 1, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 579),
    ('03151', 'Gifhorn', 2, 'Ostfalia Hochschule für angewandte Wissenschaften', 531),
    ('03151', 'Gifhorn', 3, 'Gottfried Wilhelm Leibniz Universität Hannover', 264),
    ('03151', 'Gifhorn', 4, 'Georg-August-Universität Göttingen', 154),
    ('03151', 'Gifhorn', 5, 'IU Internationale Hochschule in Erfurt', 135),
    ('03151', 'Gifhorn', 6, 'Otto-von-Guericke-Universität Magdeburg', 97),
    ('03151', 'Gifhorn', 7, 'FernUniversität in Hagen', 70),
    ('03151', 'Gifhorn', 8, 'Hochschule Hannover', 58),
    ('03151', 'Gifhorn', 9, 'Stiftung Universität Hildesheim', 51),
    ('03151', 'Gifhorn', 10, 'Universität Hamburg', 37),
    ('03151', 'Gifhorn', 'total', '', 3439),
    ('03153', 'Goslar', 1, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 363),
    ('03153', 'Goslar', 2, 'Georg-August-Universität Göttingen', 313),
    ('03153', 'Goslar', 3, 'Ostfalia Hochschule für angewandte Wissenschaften', 306),
    ('03153', 'Goslar', 4, 'Technische Universität Clausthal', 167),
    ('03153', 'Goslar', 5, 'Gottfried Wilhelm Leibniz Universität Hannover', 152),
    ('03153', 'Goslar', 6, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 150),
    ('03153', 'Goslar', 7, 'Stiftung Universität Hildesheim', 122),
    ('03153', 'Goslar', 8, 'IU Internationale Hochschule in Erfurt', 102),
    ('03153', 'Goslar', 9, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 98),
    ('03153', 'Goslar', 10, 'FernUniversität in Hagen', 74),
    ('03153', 'Goslar', 'total', '', 3345),
    ('03154', 'Helmstedt', 1, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 277),
    ('03154', 'Helmstedt', 2, 'Ostfalia Hochschule für angewandte Wissenschaften', 221),
    ('03154', 'Helmstedt', 3, 'Otto-von-Guericke-Universität Magdeburg', 98),
    ('03154', 'Helmstedt', 4, 'Georg-August-Universität Göttingen', 82),
    ('03154', 'Helmstedt', 5, 'Gottfried Wilhelm Leibniz Universität Hannover', 52),
    ('03154', 'Helmstedt', 6, 'IU Internationale Hochschule in Erfurt', 51),
    ('03154', 'Helmstedt', 7, 'FernUniversität in Hagen', 41),
    ('03154', 'Helmstedt', 8, 'Martin-Luther-Universität Halle-Wittenberg', 33),
    ('03154', 'Helmstedt', 9, 'Hochschule Magdeburg-Stendal', 28),
    ('03154', 'Helmstedt', 10, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 23),
    ('03154', 'Helmstedt', 'total', '', 1454),
    ('03155', 'Northeim', 1, 'Georg-August-Universität Göttingen', 830),
    ('03155', 'Northeim', 2, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 230),
    ('03155', 'Northeim', 3, 'Universität Kassel', 175),
    ('03155', 'Northeim', 4, 'Gottfried Wilhelm Leibniz Universität Hannover', 148),
    ('03155', 'Northeim', 5, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 117),
    ('03155', 'Northeim', 6, 'IU Internationale Hochschule in Erfurt', 112),
    ('03155', 'Northeim', 7, 'Stiftung Universität Hildesheim', 94),
    ('03155', 'Northeim', 8, 'PFH - Private Hochschule Göttingen', 87),
    ('03155', 'Northeim', 9, 'FernUniversität in Hagen', 70),
    ('03155', 'Northeim', 10, 'Technische Universität Clausthal', 63),
    ('03155', 'Northeim', 'total', '', 3456),
    ('03157', 'Peine', 1, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 533),
    ('03157', 'Peine', 2, 'Gottfried Wilhelm Leibniz Universität Hannover', 368),
    ('03157', 'Peine', 3, 'Ostfalia Hochschule für angewandte Wissenschaften', 336),
    ('03157', 'Peine', 4, 'Georg-August-Universität Göttingen', 167),
    ('03157', 'Peine', 5, 'Stiftung Universität Hildesheim', 167),
    ('03157', 'Peine', 6, 'Hochschule Hannover', 106),
    ('03157', 'Peine', 7, 'IU Internationale Hochschule in Erfurt', 93),
    ('03157', 'Peine', 8, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 61),
    ('03157', 'Peine', 9, 'Medizinische Hochschule Hannover (MHH)', 56),
    ('03157', 'Peine', 10, 'FernUniversität in Hagen', 47),
    ('03157', 'Peine', 'total', '', 3088),
    ('03158', 'Wolfenbüttel', 1, 'Ostfalia Hochschule für angewandte Wissenschaften', 540),
    ('03158', 'Wolfenbüttel', 2, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 466),
    ('03158', 'Wolfenbüttel', 3, 'Georg-August-Universität Göttingen', 176),
    ('03158', 'Wolfenbüttel', 4, 'Gottfried Wilhelm Leibniz Universität Hannover', 93),
    ('03158', 'Wolfenbüttel', 5, 'IU Internationale Hochschule in Erfurt', 73),
    ('03158', 'Wolfenbüttel', 6, 'Stiftung Universität Hildesheim', 58),
    ('03158', 'Wolfenbüttel', 7, 'Martin-Luther-Universität Halle-Wittenberg', 53),
    ('03158', 'Wolfenbüttel', 8, 'Otto-von-Guericke-Universität Magdeburg', 52),
    ('03158', 'Wolfenbüttel', 9, 'FernUniversität in Hagen', 39),
    ('03158', 'Wolfenbüttel', 10, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 36),
    ('03158', 'Wolfenbüttel', 'total', '', 2613),
    ('03159', 'Göttingen', 1, 'Georg-August-Universität Göttingen', 2429),
    ('03159', 'Göttingen', 2, 'Universität Kassel', 860),
    ('03159', 'Göttingen', 3, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 545),
    ('03159', 'Göttingen', 4, 'PFH - Private Hochschule Göttingen', 322),
    ('03159', 'Göttingen', 5, 'IU Internationale Hochschule in Erfurt', 298),
    ('03159', 'Göttingen', 6, 'Gottfried Wilhelm Leibniz Universität Hannover', 261),
    ('03159', 'Göttingen', 7, 'FernUniversität in Hagen', 236),
    ('03159', 'Göttingen', 8, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 233),
    ('03159', 'Göttingen', 9, 'Universität Leipzig', 204),
    ('03159', 'Göttingen', 10, 'Stiftung Universität Hildesheim', 183),
    ('03159', 'Göttingen', 'total', '', 10792),
    ('03241', 'Region Hannover', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 10506),
    ('03241', 'Region Hannover', 2, 'Hochschule Hannover', 3644),
    ('03241', 'Region Hannover', 3, 'Stiftung Universität Hildesheim', 2184),
    ('03241', 'Region Hannover', 4, 'Georg-August-Universität Göttingen', 1539),
    ('03241', 'Region Hannover', 5, 'IU Internationale Hochschule in Erfurt', 1239),
    ('03241', 'Region Hannover', 6, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 1027),
    ('03241', 'Region Hannover', 7, 'FernUniversität in Hagen', 976),
    ('03241', 'Region Hannover', 8, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 904),
    ('03241', 'Region Hannover', 9, 'Medizinische Hochschule Hannover (MHH)', 798),
    ('03241', 'Region Hannover', 10, 'Ostfalia Hochschule für angewandte Wissenschaften', 682),
    ('03241', 'Region Hannover', 'total', '', 40086),
    ('03252', 'Hameln-Pyrmont', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 534),
    ('03252', 'Hameln-Pyrmont', 2, 'Georg-August-Universität Göttingen', 253),
    ('03252', 'Hameln-Pyrmont', 3, 'Universität Paderborn', 208),
    ('03252', 'Hameln-Pyrmont', 4, 'Hochschule Hannover', 189),
    ('03252', 'Hameln-Pyrmont', 5, 'Stiftung Universität Hildesheim', 177),
    ('03252', 'Hameln-Pyrmont', 6, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 170),
    ('03252', 'Hameln-Pyrmont', 7, 'IU Internationale Hochschule in Erfurt', 138),
    ('03252', 'Hameln-Pyrmont', 8, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 108),
    ('03252', 'Hameln-Pyrmont', 9, 'Technische Hochschule Ostwestfalen-Lippe', 104),
    ('03252', 'Hameln-Pyrmont', 10, 'Universität Osnabrück', 92),
    ('03252', 'Hameln-Pyrmont', 'total', '', 4286),
    ('03254', 'Hildesheim', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 1249),
    ('03254', 'Hildesheim', 2, 'Stiftung Universität Hildesheim', 1162),
    ('03254', 'Hildesheim', 3, 'Georg-August-Universität Göttingen', 805),
    ('03254', 'Hildesheim', 4, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 745),
    ('03254', 'Hildesheim', 5, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 457),
    ('03254', 'Hildesheim', 6, 'Hochschule Hannover', 346),
    ('03254', 'Hildesheim', 7, 'Ostfalia Hochschule für angewandte Wissenschaften', 271),
    ('03254', 'Hildesheim', 8, 'IU Internationale Hochschule in Erfurt', 206),
    ('03254', 'Hildesheim', 9, 'FernUniversität in Hagen', 194),
    ('03254', 'Hildesheim', 10, 'Medizinische Hochschule Hannover (MHH)', 108),
    ('03254', 'Hildesheim', 'total', '', 9338),
    ('03255', 'Holzminden', 1, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 142),
    ('03255', 'Holzminden', 2, 'Georg-August-Universität Göttingen', 109),
    ('03255', 'Holzminden', 3, 'Universität Paderborn', 87),
    ('03255', 'Holzminden', 4, 'Gottfried Wilhelm Leibniz Universität Hannover', 52),
    ('03255', 'Holzminden', 5, 'IU Internationale Hochschule in Erfurt', 40),
    ('03255', 'Holzminden', 6, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 39),
    ('03255', 'Holzminden', 7, 'Technische Hochschule Ostwestfalen-Lippe', 39),
    ('03255', 'Holzminden', 8, 'Hochschule Hannover', 24),
    ('03255', 'Holzminden', 9, 'FernUniversität in Hagen', 21),
    ('03255', 'Holzminden', 10, 'Stiftung Universität Hildesheim', 20),
    ('03255', 'Holzminden', 'total', '', 1215),
    ('03256', 'Nienburg (Weser)', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 421),
    ('03256', 'Nienburg (Weser)', 2, 'Hochschule Hannover', 161),
    ('03256', 'Nienburg (Weser)', 3, 'Georg-August-Universität Göttingen', 156),
    ('03256', 'Nienburg (Weser)', 4, 'IU Internationale Hochschule in Erfurt', 128),
    ('03256', 'Nienburg (Weser)', 5, 'Carl von Ossietzky Universität Oldenburg', 112),
    ('03256', 'Nienburg (Weser)', 6, 'Universität Bremen', 105),
    ('03256', 'Nienburg (Weser)', 7, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 83),
    ('03256', 'Nienburg (Weser)', 8, 'Universität Vechta', 73),
    ('03256', 'Nienburg (Weser)', 9, 'Stiftung Universität Hildesheim', 71),
    ('03256', 'Nienburg (Weser)', 10, 'FernUniversität in Hagen', 62),
    ('03256', 'Nienburg (Weser)', 'total', '', 2918),
    ('03257', 'Schaumburg', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 818),
    ('03257', 'Schaumburg', 2, 'Hochschule Hannover', 259),
    ('03257', 'Schaumburg', 3, 'Universität Bielefeld', 220),
    ('03257', 'Schaumburg', 4, 'IU Internationale Hochschule in Erfurt', 163),
    ('03257', 'Schaumburg', 5, 'Georg-August-Universität Göttingen', 159),
    ('03257', 'Schaumburg', 6, 'Hochschule Bielefeld', 157),
    ('03257', 'Schaumburg', 7, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 126),
    ('03257', 'Schaumburg', 8, 'FernUniversität in Hagen', 111),
    ('03257', 'Schaumburg', 9, 'Stiftung Universität Hildesheim', 103),
    ('03257', 'Schaumburg', 10, 'Universität Osnabrück', 100),
    ('03257', 'Schaumburg', 'total', '', 4564),
    ('03351', 'Celle', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 682),
    ('03351', 'Celle', 2, 'Georg-August-Universität Göttingen', 281),
    ('03351', 'Celle', 3, 'Ostfalia Hochschule für angewandte Wissenschaften', 233),
    ('03351', 'Celle', 4, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 224),
    ('03351', 'Celle', 5, 'Hochschule Hannover', 212),
    ('03351', 'Celle', 6, 'Stiftung Universität Hildesheim', 126),
    ('03351', 'Celle', 7, 'IU Internationale Hochschule in Erfurt', 126),
    ('03351', 'Celle', 8, 'FernUniversität in Hagen', 123),
    ('03351', 'Celle', 9, 'Universität Hamburg', 94),
    ('03351', 'Celle', 10, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 81),
    ('03351', 'Celle', 'total', '', 4466),
    ('03353', 'Harburg', 1, 'Universität Hamburg', 956),
    ('03353', 'Harburg', 2, 'Hochschule für Angewandte Wissenschaften Hamburg', 485),
    ('03353', 'Harburg', 3, 'Leuphana Universität Lüneburg', 478),
    ('03353', 'Harburg', 4, 'Technische Universität Hamburg', 428),
    ('03353', 'Harburg', 5, 'Christian-Albrechts-Universität zu Kiel', 281),
    ('03353', 'Harburg', 6, 'IU Internationale Hochschule in Erfurt', 189),
    ('03353', 'Harburg', 7, 'Universität Bremen', 185),
    ('03353', 'Harburg', 8, 'Georg-August-Universität Göttingen', 180),
    ('03353', 'Harburg', 9, 'FOM Hochschule für Oekonomie & Management in Hamburg', 166),
    ('03353', 'Harburg', 10, 'FernUniversität in Hagen', 111),
    ('03353', 'Harburg', 'total', '', 6872),
    ('03354', 'Lüchow-Dannenberg', 1, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 65),
    ('03354', 'Lüchow-Dannenberg', 2, 'Universität Hamburg', 54),
    ('03354', 'Lüchow-Dannenberg', 3, 'Georg-August-Universität Göttingen', 48),
    ('03354', 'Lüchow-Dannenberg', 4, 'Leuphana Universität Lüneburg', 47),
    ('03354', 'Lüchow-Dannenberg', 5, 'Ostfalia Hochschule für angewandte Wissenschaften', 44),
    ('03354', 'Lüchow-Dannenberg', 6, 'Gottfried Wilhelm Leibniz Universität Hannover', 41),
    ('03354', 'Lüchow-Dannenberg', 7, 'IU Internationale Hochschule in Erfurt', 30),
    ('03354', 'Lüchow-Dannenberg', 8, 'FernUniversität in Hagen', 29),
    ('03354', 'Lüchow-Dannenberg', 9, 'Christian-Albrechts-Universität zu Kiel', 29),
    ('03354', 'Lüchow-Dannenberg', 10, 'Otto-von-Guericke-Universität Magdeburg', 26),
    ('03354', 'Lüchow-Dannenberg', 'total', '', 1050),
    ('03355', 'Lüneburg', 1, 'Leuphana Universität Lüneburg', 553),
    ('03355', 'Lüneburg', 2, 'Universität Hamburg', 413),
    ('03355', 'Lüneburg', 3, 'Georg-August-Universität Göttingen', 245),
    ('03355', 'Lüneburg', 4, 'IU Internationale Hochschule in Erfurt', 207),
    ('03355', 'Lüneburg', 5, 'Christian-Albrechts-Universität zu Kiel', 201),
    ('03355', 'Lüneburg', 6, 'Hochschule für Angewandte Wissenschaften Hamburg', 198),
    ('03355', 'Lüneburg', 7, 'Gottfried Wilhelm Leibniz Universität Hannover', 169),
    ('03355', 'Lüneburg', 8, 'FernUniversität in Hagen', 164),
    ('03355', 'Lüneburg', 9, 'Ostfalia Hochschule für angewandte Wissenschaften', 164),
    ('03355', 'Lüneburg', 10, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 163),
    ('03355', 'Lüneburg', 'total', '', 6052),
    ('03357', 'Rotenburg (Wümme)', 1, 'Universität Bremen', 493),
    ('03357', 'Rotenburg (Wümme)', 2, 'Hochschule Bremen', 205),
    ('03357', 'Rotenburg (Wümme)', 3, 'Carl von Ossietzky Universität Oldenburg', 204),
    ('03357', 'Rotenburg (Wümme)', 4, 'IU Internationale Hochschule in Erfurt', 164),
    ('03357', 'Rotenburg (Wümme)', 5, 'Universität Hamburg', 162),
    ('03357', 'Rotenburg (Wümme)', 6, 'Georg-August-Universität Göttingen', 148),
    ('03357', 'Rotenburg (Wümme)', 7, 'Gottfried Wilhelm Leibniz Universität Hannover', 131),
    ('03357', 'Rotenburg (Wümme)', 8, 'Christian-Albrechts-Universität zu Kiel', 119),
    ('03357', 'Rotenburg (Wümme)', 9, 'Hochschule für Angewandte Wissenschaften Hamburg', 104),
    ('03357', 'Rotenburg (Wümme)', 10, 'Leuphana Universität Lüneburg', 98),
    ('03357', 'Rotenburg (Wümme)', 'total', '', 4345),
    ('03358', 'Heidekreis', 1, 'Gottfried Wilhelm Leibniz Universität Hannover', 379),
    ('03358', 'Heidekreis', 2, 'Georg-August-Universität Göttingen', 158),
    ('03358', 'Heidekreis', 3, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 122),
    ('03358', 'Heidekreis', 4, 'Universität Hamburg', 115),
    ('03358', 'Heidekreis', 5, 'IU Internationale Hochschule in Erfurt', 107),
    ('03358', 'Heidekreis', 6, 'Universität Bremen', 101),
    ('03358', 'Heidekreis', 7, 'Christian-Albrechts-Universität zu Kiel', 95),
    ('03358', 'Heidekreis', 8, 'Hochschule Hannover', 84),
    ('03358', 'Heidekreis', 9, 'Leuphana Universität Lüneburg', 76),
    ('03358', 'Heidekreis', 10, 'Carl von Ossietzky Universität Oldenburg', 75),
    ('03358', 'Heidekreis', 'total', '', 3164),
    ('03360', 'Uelzen', 1, 'Ostfalia Hochschule für angewandte Wissenschaften', 277),
    ('03360', 'Uelzen', 2, 'Gottfried Wilhelm Leibniz Universität Hannover', 180),
    ('03360', 'Uelzen', 3, 'Georg-August-Universität Göttingen', 167),
    ('03360', 'Uelzen', 4, 'Leuphana Universität Lüneburg', 145),
    ('03360', 'Uelzen', 5, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 119),
    ('03360', 'Uelzen', 6, 'Universität Hamburg', 88),
    ('03360', 'Uelzen', 7, 'IU Internationale Hochschule in Erfurt', 67),
    ('03360', 'Uelzen', 8, 'Christian-Albrechts-Universität zu Kiel', 48),
    ('03360', 'Uelzen', 9, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 43),
    ('03360', 'Uelzen', 10, 'Hochschule Hannover', 40),
    ('03360', 'Uelzen', 'total', '', 2402),
    ('03361', 'Verden', 1, 'Universität Bremen', 588),
    ('03361', 'Verden', 2, 'Hochschule Bremen', 287),
    ('03361', 'Verden', 3, 'Carl von Ossietzky Universität Oldenburg', 171),
    ('03361', 'Verden', 4, 'Gottfried Wilhelm Leibniz Universität Hannover', 165),
    ('03361', 'Verden', 5, 'Georg-August-Universität Göttingen', 106),
    ('03361', 'Verden', 6, 'IU Internationale Hochschule in Erfurt', 101),
    ('03361', 'Verden', 7, 'Universität Hamburg', 74),
    ('03361', 'Verden', 8, 'FernUniversität in Hagen', 73),
    ('03361', 'Verden', 9, 'Universität Osnabrück', 68),
    ('03361', 'Verden', 10, 'IU Internationale Hochschule in Bremen', 52),
    ('03361', 'Verden', 'total', '', 3430),
    ('05758', 'Herford', 1, 'Universität Bielefeld', 2557),
    ('05758', 'Herford', 2, 'Hochschule Bielefeld', 1182),
    ('05758', 'Herford', 3, 'Universität Paderborn', 385),
    ('05758', 'Herford', 4, 'Technische Hochschule Ostwestfalen-Lippe', 376),
    ('05758', 'Herford', 5, 'Universität Osnabrück', 357),
    ('05758', 'Herford', 6, 'Universität Münster', 342),
    ('05758', 'Herford', 7, 'Hochschule Osnabrück', 299),
    ('05758', 'Herford', 8, 'IU Internationale Hochschule in Erfurt', 295),
    ('05758', 'Herford', 9, 'FernUniversität in Hagen', 183),
    ('05758', 'Herford', 10, 'Fachhochschule des Mittelstands (FHM) in NRW', 166),
    ('05758', 'Herford', 'total', '', 9695),
    ('05762', 'Höxter', 1, 'Universität Paderborn', 1145),
    ('05762', 'Höxter', 2, 'Universität Kassel', 345),
    ('05762', 'Höxter', 3, 'Technische Hochschule Ostwestfalen-Lippe', 266),
    ('05762', 'Höxter', 4, 'Georg-August-Universität Göttingen', 235),
    ('05762', 'Höxter', 5, 'Universität Bielefeld', 231),
    ('05762', 'Höxter', 6, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 191),
    ('05762', 'Höxter', 7, 'Universität Münster', 173),
    ('05762', 'Höxter', 8, 'IU Internationale Hochschule in Erfurt', 156),
    ('05762', 'Höxter', 9, 'FernUniversität in Hagen', 140),
    ('05762', 'Höxter', 10, 'Hochschule Bielefeld', 121),
    ('05762', 'Höxter', 'total', '', 5900),
    ('05766', 'Lippe', 1, 'Universität Bielefeld', 1860),
    ('05766', 'Lippe', 2, 'Technische Hochschule Ostwestfalen-Lippe', 1415),
    ('05766', 'Lippe', 3, 'Universität Paderborn', 1234),
    ('05766', 'Lippe', 4, 'Hochschule Bielefeld', 693),
    ('05766', 'Lippe', 5, 'IU Internationale Hochschule in Erfurt', 338),
    ('05766', 'Lippe', 6, 'Universität Münster', 308),
    ('05766', 'Lippe', 7, 'FernUniversität in Hagen', 219),
    ('05766', 'Lippe', 8, 'Hochschule für Polizei und öffentliche Verwaltung NRW', 197),
    ('05766', 'Lippe', 9, 'Fachhochschule des Mittelstands (FHM) in NRW', 142),
    ('05766', 'Lippe', 10, 'Gottfried Wilhelm Leibniz Universität Hannover', 124),
    ('05766', 'Lippe', 'total', '', 10865),
    ('05770', 'Minden-Lübbecke', 1, 'Universität Bielefeld', 1973),
    ('05770', 'Minden-Lübbecke', 2, 'Hochschule Bielefeld', 1097),
    ('05770', 'Minden-Lübbecke', 3, 'Universität Münster', 476),
    ('05770', 'Minden-Lübbecke', 4, 'Gottfried Wilhelm Leibniz Universität Hannover', 421),
    ('05770', 'Minden-Lübbecke', 5, 'Universität Osnabrück', 347),
    ('05770', 'Minden-Lübbecke', 6, 'Universität Paderborn', 335),
    ('05770', 'Minden-Lübbecke', 7, 'IU Internationale Hochschule in Erfurt', 310),
    ('05770', 'Minden-Lübbecke', 8, 'Hochschule Osnabrück', 303),
    ('05770', 'Minden-Lübbecke', 9, 'FernUniversität in Hagen', 251),
    ('05770', 'Minden-Lübbecke', 10, 'Georg-August-Universität Göttingen', 159),
    ('05770', 'Minden-Lübbecke', 'total', '', 10727),
    ('05774', 'Paderborn', 1, 'Universität Paderborn', 3457),
    ('05774', 'Paderborn', 2, 'Universität Bielefeld', 651),
    ('05774', 'Paderborn', 3, 'Universität Münster', 417),
    ('05774', 'Paderborn', 4, 'Katholische Hochschule Nordrhein-Westfalen', 397),
    ('05774', 'Paderborn', 5, 'Hochschule Bielefeld', 382),
    ('05774', 'Paderborn', 6, 'Technische Hochschule Ostwestfalen-Lippe', 378),
    ('05774', 'Paderborn', 7, 'Hochschule Hamm-Lippstadt', 297),
    ('05774', 'Paderborn', 8, 'Rheinisch-Westfälische Technische Hochschule Aachen', 269),
    ('05774', 'Paderborn', 9, 'IU Internationale Hochschule in Erfurt', 263),
    ('05774', 'Paderborn', 10, 'FernUniversität in Hagen', 254),
    ('05774', 'Paderborn', 'total', '', 12172),
    ('06611', 'Kassel, documenta-Stadt', 1, 'Universität Kassel', 5399),
    ('06611', 'Kassel, documenta-Stadt', 2, 'Georg-August-Universität Göttingen', 683),
    ('06611', 'Kassel, documenta-Stadt', 3, 'Philipps-Universität Marburg', 354),
    ('06611', 'Kassel, documenta-Stadt', 4, 'FernUniversität in Hagen', 234),
    ('06611', 'Kassel, documenta-Stadt', 5, 'IU Internationale Hochschule in Erfurt', 199),
    ('06611', 'Kassel, documenta-Stadt', 6, 'Justus-Liebig-Universität Gießen', 198),
    ('06611', 'Kassel, documenta-Stadt', 7, 'Hochschule Fulda', 197),
    ('06611', 'Kassel, documenta-Stadt', 8, 'Universität Leipzig', 129),
    ('06611', 'Kassel, documenta-Stadt', 9, 'Johann Wolfgang Goethe-Universität Frankfurt am Main', 125),
    ('06611', 'Kassel, documenta-Stadt', 10, 'Hessische Hochschule für Polizei und Verwaltung Wiesbaden', 110),
    ('06611', 'Kassel, documenta-Stadt', 'total', '', 11322),
    ('06632', 'Hersfeld-Rotenburg', 1, 'Universität Kassel', 734),
    ('06632', 'Hersfeld-Rotenburg', 2, 'Hochschule Fulda', 384),
    ('06632', 'Hersfeld-Rotenburg', 3, 'Technische Hochschule Mittelhessen (THM)', 127),
    ('06632', 'Hersfeld-Rotenburg', 4, 'Philipps-Universität Marburg', 124),
    ('06632', 'Hersfeld-Rotenburg', 5, 'Georg-August-Universität Göttingen', 96),
    ('06632', 'Hersfeld-Rotenburg', 6, 'Justus-Liebig-Universität Gießen', 95),
    ('06632', 'Hersfeld-Rotenburg', 7, 'IU Internationale Hochschule in Erfurt', 81),
    ('06632', 'Hersfeld-Rotenburg', 8, 'Johann Wolfgang Goethe-Universität Frankfurt am Main', 72),
    ('06632', 'Hersfeld-Rotenburg', 9, 'Hessische Hochschule für Finanzen und Rechtspflege Rotenburg', 55),
    ('06632', 'Hersfeld-Rotenburg', 10, 'FernUniversität in Hagen', 51),
    ('06632', 'Hersfeld-Rotenburg', 'total', '', 3079),
    ('06633', 'Kassel', 1, 'Universität Kassel', 1386),
    ('06633', 'Kassel', 2, 'Georg-August-Universität Göttingen', 243),
    ('06633', 'Kassel', 3, 'FOM Hochschule für Oekonomie & Management in Hessen', 237),
    ('06633', 'Kassel', 4, 'IU Internationale Hochschule in Erfurt', 207),
    ('06633', 'Kassel', 5, 'Philipps-Universität Marburg', 118),
    ('06633', 'Kassel', 6, 'FernUniversität in Hagen', 62),
    ('06633', 'Kassel', 7, 'Hessische Hochschule für Finanzen und Rechtspflege Rotenburg', 58),
    ('06633', 'Kassel', 8, 'Hessische Hochschule für Polizei und Verwaltung Wiesbaden', 55),
    ('06633', 'Kassel', 9, 'Hochschule Fulda', 54),
    ('06633', 'Kassel', 10, 'Justus-Liebig-Universität Gießen', 50),
    ('06633', 'Kassel', 'total', '', 3942),
    ('06634', 'Schwalm-Eder-Kreis', 1, 'Universität Kassel', 1519),
    ('06634', 'Schwalm-Eder-Kreis', 2, 'Philipps-Universität Marburg', 510),
    ('06634', 'Schwalm-Eder-Kreis', 3, 'Justus-Liebig-Universität Gießen', 203),
    ('06634', 'Schwalm-Eder-Kreis', 4, 'Georg-August-Universität Göttingen', 189),
    ('06634', 'Schwalm-Eder-Kreis', 5, 'Technische Hochschule Mittelhessen (THM)', 160),
    ('06634', 'Schwalm-Eder-Kreis', 6, 'Hochschule Fulda', 131),
    ('06634', 'Schwalm-Eder-Kreis', 7, 'IU Internationale Hochschule in Erfurt', 127),
    ('06634', 'Schwalm-Eder-Kreis', 8, 'FernUniversität in Hagen', 81),
    ('06634', 'Schwalm-Eder-Kreis', 9, 'Hessische Hochschule für Finanzen und Rechtspflege Rotenburg', 76),
    ('06634', 'Schwalm-Eder-Kreis', 10, 'Johann Wolfgang Goethe-Universität Frankfurt am Main', 72),
    ('06634', 'Schwalm-Eder-Kreis', 'total', '', 4873),
    ('06635', 'Waldeck-Frankenberg', 1, 'Universität Kassel', 702),
    ('06635', 'Waldeck-Frankenberg', 2, 'Philipps-Universität Marburg', 411),
    ('06635', 'Waldeck-Frankenberg', 3, 'Technische Hochschule Mittelhessen (THM)', 347),
    ('06635', 'Waldeck-Frankenberg', 4, 'Justus-Liebig-Universität Gießen', 241),
    ('06635', 'Waldeck-Frankenberg', 5, 'IU Internationale Hochschule in Erfurt', 112),
    ('06635', 'Waldeck-Frankenberg', 6, 'Universität Paderborn', 109),
    ('06635', 'Waldeck-Frankenberg', 7, 'Georg-August-Universität Göttingen', 100),
    ('06635', 'Waldeck-Frankenberg', 8, 'FernUniversität in Hagen', 82),
    ('06635', 'Waldeck-Frankenberg', 9, 'Johann Wolfgang Goethe-Universität Frankfurt am Main', 63),
    ('06635', 'Waldeck-Frankenberg', 10, 'Technische Universität Darmstadt', 56),
    ('06635', 'Waldeck-Frankenberg', 'total', '', 3952),
    ('06636', 'Werra-Meißner-Kreis', 1, 'Universität Kassel', 704),
    ('06636', 'Werra-Meißner-Kreis', 2, 'Georg-August-Universität Göttingen', 343),
    ('06636', 'Werra-Meißner-Kreis', 3, 'IU Internationale Hochschule in Erfurt', 72),
    ('06636', 'Werra-Meißner-Kreis', 4, 'Hochschule Fulda', 70),
    ('06636', 'Werra-Meißner-Kreis', 5, 'Philipps-Universität Marburg', 63),
    ('06636', 'Werra-Meißner-Kreis', 6, 'DIPLOMA Hochschule, Fachhochschule Nordhessen', 61),
    ('06636', 'Werra-Meißner-Kreis', 7, 'Justus-Liebig-Universität Gießen', 49),
    ('06636', 'Werra-Meißner-Kreis', 8, 'Hochschule für angewandte Wissenschaft und Kunst Hildesheim/Holzminden/Göttingen', 46),
    ('06636', 'Werra-Meißner-Kreis', 9, 'FernUniversität in Hagen', 41),
    ('06636', 'Werra-Meißner-Kreis', 10, 'Hessische Hochschule für Finanzen und Rechtspflege Rotenburg', 38),
    ('06636', 'Werra-Meißner-Kreis', 'total', '', 2557),
    ('12063', 'Havelland', 1, 'Universität Potsdam', 482),
    ('12063', 'Havelland', 2, 'Technische Universität Berlin', 384),
    ('12063', 'Havelland', 3, 'Freie Universität Berlin', 305),
    ('12063', 'Havelland', 4, 'Humboldt-Universität zu Berlin', 300),
    ('12063', 'Havelland', 5, 'Hochschule für Wirtschaft und Recht Berlin', 190),
    ('12063', 'Havelland', 6, 'Berliner Hochschule für Technik', 184),
    ('12063', 'Havelland', 7, 'IU Internationale Hochschule in Erfurt', 108),
    ('12063', 'Havelland', 8, 'Technische Hochschule Brandenburg', 103),
    ('12063', 'Havelland', 9, 'Hochschule für Technik und Wirtschaft Berlin', 80),
    ('12063', 'Havelland', 10, 'Charité Universitätsmedizin Berlin', 69),
    ('12063', 'Havelland', 'total', '', 3892),
    ('12070', 'Prignitz', 1, 'Universität Rostock', 112),
    ('12070', 'Prignitz', 2, 'Universität Potsdam', 95),
    ('12070', 'Prignitz', 3, 'Universität Greifswald', 57),
    ('12070', 'Prignitz', 4, 'Humboldt-Universität zu Berlin', 49),
    ('12070', 'Prignitz', 5, 'Hochschule Wismar - University of Applied Sciences: Technology, Business and Design', 49),
    ('12070', 'Prignitz', 6, 'IU Internationale Hochschule in Erfurt', 47),
    ('12070', 'Prignitz', 7, 'Hochschule Neubrandenburg', 36),
    ('12070', 'Prignitz', 8, 'Universität Leipzig', 31),
    ('12070', 'Prignitz', 9, 'Technische Hochschule Wildau', 31),
    ('12070', 'Prignitz', 10, 'Freie Universität Berlin', 28),
    ('12070', 'Prignitz', 'total', '', 1267),
    ('13076', 'Ludwigslust-Parchim', 1, 'Universität Rostock', 558),
    ('13076', 'Ludwigslust-Parchim', 2, 'Hochschule Wismar - University of Applied Sciences: Technology, Business and Design', 199),
    ('13076', 'Ludwigslust-Parchim', 3, 'Universität Greifswald', 164),
    ('13076', 'Ludwigslust-Parchim', 4, 'Universität Hamburg', 134),
    ('13076', 'Ludwigslust-Parchim', 5, 'IU Internationale Hochschule in Erfurt', 92),
    ('13076', 'Ludwigslust-Parchim', 6, 'Christian-Albrechts-Universität zu Kiel', 85),
    ('13076', 'Ludwigslust-Parchim', 7, 'Fachhochschule für öffentliche Verwaltung, Polizei und Rechtspflege in Güstrow', 79),
    ('13076', 'Ludwigslust-Parchim', 8, 'FernUniversität in Hagen', 74),
    ('13076', 'Ludwigslust-Parchim', 9, 'Universität Leipzig', 61),
    ('13076', 'Ludwigslust-Parchim', 10, 'Hochschule Neubrandenburg', 56),
    ('13076', 'Ludwigslust-Parchim', 'total', '', 3018),
    ('15003', 'Magdeburg, Landeshauptstadt', 1, 'Otto-von-Guericke-Universität Magdeburg', 1883),
    ('15003', 'Magdeburg, Landeshauptstadt', 2, 'Hochschule Magdeburg-Stendal', 1031),
    ('15003', 'Magdeburg, Landeshauptstadt', 3, 'Martin-Luther-Universität Halle-Wittenberg', 524),
    ('15003', 'Magdeburg, Landeshauptstadt', 4, 'Universität Leipzig', 329),
    ('15003', 'Magdeburg, Landeshauptstadt', 5, 'Hochschule Anhalt', 219),
    ('15003', 'Magdeburg, Landeshauptstadt', 6, 'IU Internationale Hochschule in Erfurt', 192),
    ('15003', 'Magdeburg, Landeshauptstadt', 7, 'FernUniversität in Hagen', 174),
    ('15003', 'Magdeburg, Landeshauptstadt', 8, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 151),
    ('15003', 'Magdeburg, Landeshauptstadt', 9, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 135),
    ('15003', 'Magdeburg, Landeshauptstadt', 10, 'Friedrich-Schiller-Universität Jena', 116),
    ('15003', 'Magdeburg, Landeshauptstadt', 'total', '', 7497),
    ('15081', 'Altmarkkreis Salzwedel', 1, 'Otto-von-Guericke-Universität Magdeburg', 155),
    ('15081', 'Altmarkkreis Salzwedel', 2, 'Martin-Luther-Universität Halle-Wittenberg', 115),
    ('15081', 'Altmarkkreis Salzwedel', 3, 'Hochschule Magdeburg-Stendal', 114),
    ('15081', 'Altmarkkreis Salzwedel', 4, 'Ostfalia Hochschule für angewandte Wissenschaften', 58),
    ('15081', 'Altmarkkreis Salzwedel', 5, 'Universität Leipzig', 52),
    ('15081', 'Altmarkkreis Salzwedel', 6, 'Hochschule Anhalt', 52),
    ('15081', 'Altmarkkreis Salzwedel', 7, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 47),
    ('15081', 'Altmarkkreis Salzwedel', 8, 'Universität Rostock', 37),
    ('15081', 'Altmarkkreis Salzwedel', 9, 'IU Internationale Hochschule in Erfurt', 37),
    ('15081', 'Altmarkkreis Salzwedel', 10, 'Friedrich-Schiller-Universität Jena', 25),
    ('15081', 'Altmarkkreis Salzwedel', 'total', '', 1290),
    ('15082', 'Anhalt-Bitterfeld', 1, 'Hochschule Anhalt', 518),
    ('15082', 'Anhalt-Bitterfeld', 2, 'Martin-Luther-Universität Halle-Wittenberg', 409),
    ('15082', 'Anhalt-Bitterfeld', 3, 'Universität Leipzig', 209),
    ('15082', 'Anhalt-Bitterfeld', 4, 'Otto-von-Guericke-Universität Magdeburg', 151),
    ('15082', 'Anhalt-Bitterfeld', 5, 'Hochschule Magdeburg-Stendal', 110),
    ('15082', 'Anhalt-Bitterfeld', 6, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 83),
    ('15082', 'Anhalt-Bitterfeld', 7, 'FernUniversität in Hagen', 75),
    ('15082', 'Anhalt-Bitterfeld', 8, 'Technische Universität Berlin', 73),
    ('15082', 'Anhalt-Bitterfeld', 9, 'Technische Universität Dresden', 69),
    ('15082', 'Anhalt-Bitterfeld', 10, 'IU Internationale Hochschule in Erfurt', 68),
    ('15082', 'Anhalt-Bitterfeld', 'total', '', 3632),
    ('15083', 'Börde', 1, 'Otto-von-Guericke-Universität Magdeburg', 589),
    ('15083', 'Börde', 2, 'Hochschule Magdeburg-Stendal', 314),
    ('15083', 'Börde', 3, 'Martin-Luther-Universität Halle-Wittenberg', 237),
    ('15083', 'Börde', 4, 'Hochschule Anhalt', 121),
    ('15083', 'Börde', 5, 'Universität Leipzig', 116),
    ('15083', 'Börde', 6, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 99),
    ('15083', 'Börde', 7, 'Friedrich-Schiller-Universität Jena', 76),
    ('15083', 'Börde', 8, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 75),
    ('15083', 'Börde', 9, 'IU Internationale Hochschule in Erfurt', 73),
    ('15083', 'Börde', 10, 'Technische Universität Dresden', 40),
    ('15083', 'Börde', 'total', '', 2661),
    ('15084', 'Burgenlandkreis', 1, 'Martin-Luther-Universität Halle-Wittenberg', 366),
    ('15084', 'Burgenlandkreis', 2, 'Universität Leipzig', 361),
    ('15084', 'Burgenlandkreis', 3, 'Friedrich-Schiller-Universität Jena', 229),
    ('15084', 'Burgenlandkreis', 4, 'Technische Universität Dresden', 127),
    ('15084', 'Burgenlandkreis', 5, 'Hochschule Merseburg', 127),
    ('15084', 'Burgenlandkreis', 6, 'Hochschule für Technik, Wirtschaft und Kultur Leipzig', 94),
    ('15084', 'Burgenlandkreis', 7, 'IU Internationale Hochschule in Erfurt', 76),
    ('15084', 'Burgenlandkreis', 8, 'FernUniversität in Hagen', 75),
    ('15084', 'Burgenlandkreis', 9, 'Universität Erfurt', 63),
    ('15084', 'Burgenlandkreis', 10, 'Hochschule Anhalt', 55),
    ('15084', 'Burgenlandkreis', 'total', '', 2972),
    ('15085', 'Harz', 1, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 453),
    ('15085', 'Harz', 2, 'Martin-Luther-Universität Halle-Wittenberg', 363),
    ('15085', 'Harz', 3, 'Otto-von-Guericke-Universität Magdeburg', 327),
    ('15085', 'Harz', 4, 'Universität Leipzig', 244),
    ('15085', 'Harz', 5, 'Hochschule Magdeburg-Stendal', 207),
    ('15085', 'Harz', 6, 'Technische Universität Carolo-Wilhelmina zu Braunschweig', 172),
    ('15085', 'Harz', 7, 'Ostfalia Hochschule für angewandte Wissenschaften', 140),
    ('15085', 'Harz', 8, 'IU Internationale Hochschule in Erfurt', 122),
    ('15085', 'Harz', 9, 'Technische Universität Dresden', 119),
    ('15085', 'Harz', 10, 'Friedrich-Schiller-Universität Jena', 97),
    ('15085', 'Harz', 'total', '', 4260),
    ('15086', 'Jerichower Land', 1, 'Otto-von-Guericke-Universität Magdeburg', 279),
    ('15086', 'Jerichower Land', 2, 'Hochschule Magdeburg-Stendal', 182),
    ('15086', 'Jerichower Land', 3, 'Martin-Luther-Universität Halle-Wittenberg', 134),
    ('15086', 'Jerichower Land', 4, 'Universität Leipzig', 58),
    ('15086', 'Jerichower Land', 5, 'Hochschule Anhalt', 46),
    ('15086', 'Jerichower Land', 6, 'IU Internationale Hochschule in Erfurt', 40),
    ('15086', 'Jerichower Land', 7, 'Universität Potsdam', 35),
    ('15086', 'Jerichower Land', 8, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 33),
    ('15086', 'Jerichower Land', 9, 'Fachhochschule Polizei Sachsen-Anhalt', 25),
    ('15086', 'Jerichower Land', 10, 'FernUniversität in Hagen', 24),
    ('15086', 'Jerichower Land', 'total', '', 1336),
    ('15087', 'Mansfeld-Südharz', 1, 'Martin-Luther-Universität Halle-Wittenberg', 366),
    ('15087', 'Mansfeld-Südharz', 2, 'Hochschule Nordhausen', 99),
    ('15087', 'Mansfeld-Südharz', 3, 'Universität Leipzig', 96),
    ('15087', 'Mansfeld-Südharz', 4, 'Hochschule Anhalt', 69),
    ('15087', 'Mansfeld-Südharz', 5, 'IU Internationale Hochschule in Erfurt', 69),
    ('15087', 'Mansfeld-Südharz', 6, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 68),
    ('15087', 'Mansfeld-Südharz', 7, 'Technische Universität Dresden', 61),
    ('15087', 'Mansfeld-Südharz', 8, 'Hochschule Merseburg', 51),
    ('15087', 'Mansfeld-Südharz', 9, 'FernUniversität in Hagen', 48),
    ('15087', 'Mansfeld-Südharz', 10, 'Otto-von-Guericke-Universität Magdeburg', 47),
    ('15087', 'Mansfeld-Südharz', 'total', '', 1721),
    ('15088', 'Saalekreis', 1, 'Martin-Luther-Universität Halle-Wittenberg', 653),
    ('15088', 'Saalekreis', 2, 'Hochschule Merseburg', 259),
    ('15088', 'Saalekreis', 3, 'Universität Leipzig', 132),
    ('15088', 'Saalekreis', 4, 'Hochschule Anhalt', 96),
    ('15088', 'Saalekreis', 5, 'Hochschule für Technik, Wirtschaft und Kultur Leipzig', 66),
    ('15088', 'Saalekreis', 6, 'Technische Universität Dresden', 60),
    ('15088', 'Saalekreis', 7, 'Friedrich-Schiller-Universität Jena', 59),
    ('15088', 'Saalekreis', 8, 'IU Internationale Hochschule in Erfurt', 57),
    ('15088', 'Saalekreis', 9, 'FernUniversität in Hagen', 53),
    ('15088', 'Saalekreis', 10, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 47),
    ('15088', 'Saalekreis', 'total', '', 2366),
    ('15089', 'Salzlandkreis', 1, 'Martin-Luther-Universität Halle-Wittenberg', 397),
    ('15089', 'Salzlandkreis', 2, 'Otto-von-Guericke-Universität Magdeburg', 362),
    ('15089', 'Salzlandkreis', 3, 'Hochschule Anhalt', 262),
    ('15089', 'Salzlandkreis', 4, 'Hochschule Magdeburg-Stendal', 203),
    ('15089', 'Salzlandkreis', 5, 'Universität Leipzig', 159),
    ('15089', 'Salzlandkreis', 6, 'Hochschule Harz - Hochschule für angewandte Wissenschaften (FH)', 85),
    ('15089', 'Salzlandkreis', 7, 'Friedrich-Schiller-Universität Jena', 67),
    ('15089', 'Salzlandkreis', 8, 'Fachhochschule Polizei Sachsen-Anhalt', 59),
    ('15089', 'Salzlandkreis', 9, 'IU Internationale Hochschule in Erfurt', 52),
    ('15089', 'Salzlandkreis', 10, 'Technische Universität Dresden', 51),
    ('15089', 'Salzlandkreis', 'total', '', 2607),
    ('15090', 'Stendal', 1, 'Hochschule Magdeburg-Stendal', 343),
    ('15090', 'Stendal', 2, 'Otto-von-Guericke-Universität Magdeburg', 286),
    ('15090', 'Stendal', 3, 'Martin-Luther-Universität Halle-Wittenberg', 172),
    ('15090', 'Stendal', 4, 'Universität Rostock', 134),
    ('15090', 'Stendal', 5, 'Universität Leipzig', 87),
    ('15090', 'Stendal', 6, 'Universität Potsdam', 59),
    ('15090', 'Stendal', 7, 'FernUniversität in Hagen', 50),
    ('15090', 'Stendal', 8, 'IU Internationale Hochschule in Erfurt', 49),
    ('15090', 'Stendal', 9, 'Hochschule Anhalt', 46),
    ('15090', 'Stendal', 10, 'Technische Universität Dresden', 45),
    ('15090', 'Stendal', 'total', '', 2290),
    ('16051', 'Erfurt, Stadt', 1, 'Friedrich-Schiller-Universität Jena', 628),
    ('16051', 'Erfurt, Stadt', 2, 'Fachhochschule Erfurt', 556),
    ('16051', 'Erfurt, Stadt', 3, 'Universität Erfurt', 429),
    ('16051', 'Erfurt, Stadt', 4, 'IU Internationale Hochschule in Erfurt', 416),
    ('16051', 'Erfurt, Stadt', 5, 'Universität Leipzig', 385),
    ('16051', 'Erfurt, Stadt', 6, 'Technische Universität Dresden', 327),
    ('16051', 'Erfurt, Stadt', 7, 'Technische Universität Ilmenau', 198),
    ('16051', 'Erfurt, Stadt', 8, 'Martin-Luther-Universität Halle-Wittenberg', 196),
    ('16051', 'Erfurt, Stadt', 9, 'FernUniversität in Hagen', 157),
    ('16051', 'Erfurt, Stadt', 10, 'Ernst-Abbe-Hochschule Jena', 152),
    ('16051', 'Erfurt, Stadt', 'total', '', 13634),
    ('16061', 'Eichsfeld', 1, 'Friedrich-Schiller-Universität Jena', 184),
    ('16061', 'Eichsfeld', 2, 'Georg-August-Universität Göttingen', 177),
    ('16061', 'Eichsfeld', 3, 'Universität Kassel', 169),
    ('16061', 'Eichsfeld', 4, 'Hochschule Nordhausen', 159),
    ('16061', 'Eichsfeld', 5, 'Universität Erfurt', 131),
    ('16061', 'Eichsfeld', 6, 'Universität Leipzig', 127),
    ('16061', 'Eichsfeld', 7, 'Fachhochschule Erfurt', 110),
    ('16061', 'Eichsfeld', 8, 'Martin-Luther-Universität Halle-Wittenberg', 84),
    ('16061', 'Eichsfeld', 9, 'IU Internationale Hochschule in Erfurt', 60),
    ('16061', 'Eichsfeld', 10, 'Technische Universität Ilmenau', 51),
    ('16061', 'Eichsfeld', 'total', '', 2281),
    ('16062', 'Nordhausen', 1, 'Hochschule Nordhausen', 256),
    ('16062', 'Nordhausen', 2, 'Martin-Luther-Universität Halle-Wittenberg', 191),
    ('16062', 'Nordhausen', 3, 'Universität Leipzig', 168),
    ('16062', 'Nordhausen', 4, 'Friedrich-Schiller-Universität Jena', 132),
    ('16062', 'Nordhausen', 5, 'Universität Erfurt', 114),
    ('16062', 'Nordhausen', 6, 'Technische Universität Dresden', 58),
    ('16062', 'Nordhausen', 7, 'Johann Wolfgang Goethe-Universität Frankfurt am Main', 54),
    ('16062', 'Nordhausen', 8, 'IU Internationale Hochschule in Erfurt', 51),
    ('16062', 'Nordhausen', 9, 'Georg-August-Universität Göttingen', 49),
    ('16062', 'Nordhausen', 10, 'Fachhochschule Erfurt', 47),
    ('16062', 'Nordhausen', 'total', '', 2763),
    ('16063', 'Wartburgkreis', 1, 'Friedrich-Schiller-Universität Jena', 253),
    ('16063', 'Wartburgkreis', 2, 'Universität Erfurt', 162),
    ('16063', 'Wartburgkreis', 3, 'Universität Kassel', 141),
    ('16063', 'Wartburgkreis', 4, 'Universität Leipzig', 131),
    ('16063', 'Wartburgkreis', 5, 'Hochschule Schmalkalden', 129),
    ('16063', 'Wartburgkreis', 6, 'IU Internationale Hochschule in Erfurt', 105),
    ('16063', 'Wartburgkreis', 7, 'Duale Hochschule Gera-Eisenach', 104),
    ('16063', 'Wartburgkreis', 8, 'Technische Universität Dresden', 92),
    ('16063', 'Wartburgkreis', 9, 'Martin-Luther-Universität Halle-Wittenberg', 85),
    ('16063', 'Wartburgkreis', 10, 'Fachhochschule Erfurt', 83),
    ('16063', 'Wartburgkreis', 'total', '', 2781),
    ('16064', 'Unstrut-Hainich-Kreis', 1, 'Friedrich-Schiller-Universität Jena', 239),
    ('16064', 'Unstrut-Hainich-Kreis', 2, 'Universität Erfurt', 186),
    ('16064', 'Unstrut-Hainich-Kreis', 3, 'Universität Leipzig', 112),
    ('16064', 'Unstrut-Hainich-Kreis', 4, 'Fachhochschule Erfurt', 85),
    ('16064', 'Unstrut-Hainich-Kreis', 5, 'IU Internationale Hochschule in Erfurt', 77),
    ('16064', 'Unstrut-Hainich-Kreis', 6, 'Technische Universität Dresden', 74),
    ('16064', 'Unstrut-Hainich-Kreis', 7, 'Martin-Luther-Universität Halle-Wittenberg', 72),
    ('16064', 'Unstrut-Hainich-Kreis', 8, 'Georg-August-Universität Göttingen', 69),
    ('16064', 'Unstrut-Hainich-Kreis', 9, 'Hochschule Nordhausen', 63),
    ('16064', 'Unstrut-Hainich-Kreis', 10, 'FernUniversität in Hagen', 54),
    ('16064', 'Unstrut-Hainich-Kreis', 'total', '', 1967),
    ('16065', 'Kyffhäuserkreis', 1, 'Friedrich-Schiller-Universität Jena', 115),
    ('16065', 'Kyffhäuserkreis', 2, 'Universität Erfurt', 102),
    ('16065', 'Kyffhäuserkreis', 3, 'Universität Leipzig', 74),
    ('16065', 'Kyffhäuserkreis', 4, 'Martin-Luther-Universität Halle-Wittenberg', 74),
    ('16065', 'Kyffhäuserkreis', 5, 'Hochschule Nordhausen', 73),
    ('16065', 'Kyffhäuserkreis', 6, 'IU Internationale Hochschule in Erfurt', 67),
    ('16065', 'Kyffhäuserkreis', 7, 'Fachhochschule Erfurt', 58),
    ('16065', 'Kyffhäuserkreis', 8, 'Technische Universität Dresden', 45),
    ('16065', 'Kyffhäuserkreis', 9, 'Georg-August-Universität Göttingen', 33),
    ('16065', 'Kyffhäuserkreis', 10, 'Technische Universität Ilmenau', 32),
    ('16065', 'Kyffhäuserkreis', 'total', '', 1242),
    ('16067', 'Gotha', 1, 'Friedrich-Schiller-Universität Jena', 346),
    ('16067', 'Gotha', 2, 'Universität Erfurt', 216),
    ('16067', 'Gotha', 3, 'Fachhochschule Erfurt', 149),
    ('16067', 'Gotha', 4, 'Universität Leipzig', 132),
    ('16067', 'Gotha', 5, 'IU Internationale Hochschule in Erfurt', 118),
    ('16067', 'Gotha', 6, 'Technische Universität Ilmenau', 108),
    ('16067', 'Gotha', 7, 'Technische Universität Dresden', 90),
    ('16067', 'Gotha', 8, 'Hochschule Schmalkalden', 73),
    ('16067', 'Gotha', 9, 'Martin-Luther-Universität Halle-Wittenberg', 63),
    ('16067', 'Gotha', 10, 'Duale Hochschule Gera-Eisenach', 63),
    ('16067', 'Gotha', 'total', '', 2591),
    ('16068', 'Sömmerda', 1, 'Friedrich-Schiller-Universität Jena', 155),
    ('16068', 'Sömmerda', 2, 'Universität Erfurt', 120),
    ('16068', 'Sömmerda', 3, 'Fachhochschule Erfurt', 80),
    ('16068', 'Sömmerda', 4, 'Universität Leipzig', 50),
    ('16068', 'Sömmerda', 5, 'IU Internationale Hochschule in Erfurt', 41),
    ('16068', 'Sömmerda', 6, 'Technische Universität Ilmenau', 32),
    ('16068', 'Sömmerda', 7, 'Ernst-Abbe-Hochschule Jena', 30),
    ('16068', 'Sömmerda', 8, 'Martin-Luther-Universität Halle-Wittenberg', 29),
    ('16068', 'Sömmerda', 9, 'Technische Universität Dresden', 26),
    ('16068', 'Sömmerda', 10, 'Duale Hochschule Gera-Eisenach', 26),
    ('16068', 'Sömmerda', 'total', '', 972),
    ('16071', 'Weimarer Land', 1, 'Friedrich-Schiller-Universität Jena', 187),
    ('16071', 'Weimarer Land', 2, 'Hochschule Fresenius in München', 103),
    ('16071', 'Weimarer Land', 3, 'Universität Erfurt', 81),
    ('16071', 'Weimarer Land', 4, 'Universität Leipzig', 54),
    ('16071', 'Weimarer Land', 5, 'Technische Universität Ilmenau', 49),
    ('16071', 'Weimarer Land', 6, 'Ernst-Abbe-Hochschule Jena', 48),
    ('16071', 'Weimarer Land', 7, 'Fachhochschule Erfurt', 40),
    ('16071', 'Weimarer Land', 8, 'Technische Universität Dresden', 36),
    ('16071', 'Weimarer Land', 9, 'Martin-Luther-Universität Halle-Wittenberg', 34),
    ('16071', 'Weimarer Land', 10, 'Bauhaus-Universität Weimar', 27),
    ('16071', 'Weimarer Land', 'total', '', 1159),
]

_COLUMNS = ["view", "institution", "origin_ars5", "origin_name", "rank", "row_kind",
            "students_persons"]
_SPECIAL_KINDS = ("abroad", "other", "total")


def _check_count(value, where: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"students_persons must be an integer, got {value!r} for {where}")
    if value < 0:
        raise ValueError(f"negative students_persons {value} for {where}")


def _check_ars5(ars5, where: str) -> None:
    if not isinstance(ars5, str) or len(ars5) != 5 or not ars5.isdigit():
        raise ValueError(f"origin_ars5 must be a 5-digit string, got {ars5!r} for {where}")


def _row_kind(rank_or_kind, where: str) -> str:
    if rank_or_kind in _SPECIAL_KINDS:
        return rank_or_kind
    if isinstance(rank_or_kind, int) and not isinstance(rank_or_kind, bool) \
            and 1 <= rank_or_kind <= MAX_LISTED_RANKS:
        return "listed"
    raise ValueError(f"rank_or_kind must be 1..{MAX_LISTED_RANKS}, 'abroad', 'other' or "
                     f"'total', got {rank_or_kind!r} for {where}")


def _check_ranked_listed(listed: list[tuple[int, int]], where: str) -> None:
    """listed = (rank, count) pairs: at most 10, ranks 1..n contiguous, counts non-increasing."""
    ranks = sorted(rank for rank, _ in listed)
    if ranks != list(range(1, len(ranks) + 1)):
        raise ValueError(f"listed ranks must be unique and contiguous from 1 for {where}, "
                         f"got {ranks}")
    counts = [count for _, count in sorted(listed)]
    if any(later > earlier for earlier, later in zip(counts, counts[1:])):
        raise ValueError(f"listed counts must be non-increasing by rank for {where}, got {counts}")


def build_origins_frame(institution_origins, kreis_destinations) -> pd.DataFrame:
    """Validate both views and return them stacked in one frame.

    Raises ValueError naming the institution or Kreis for every violated invariant
    (see the module docstring and the checks below).
    """
    records = []
    listed_origin_cells: dict[tuple[str, str], int] = {}

    by_institution: dict[str, list] = {}
    for institution, rank_or_kind, ars5, name, students in institution_origins:
        where = f"institution {institution!r}"
        _check_count(students, where)
        kind = _row_kind(rank_or_kind, where)
        if not institution:
            raise ValueError(f"empty institution in an institution_origins row ({rank_or_kind!r})")
        if kind == "listed":
            _check_ars5(ars5, where)
        by_institution.setdefault(institution, []).append((rank_or_kind, kind, ars5, students))
        records.append({"view": "institution_origins", "institution": institution,
                        "origin_ars5": ars5, "origin_name": name,
                        "rank": rank_or_kind if kind == "listed" else pd.NA,
                        "row_kind": kind, "students_persons": students})
        if kind == "listed":
            listed_origin_cells[(institution, ars5)] = students
    for institution, items in by_institution.items():
        where = f"institution {institution!r}"
        listed = [(rank, n) for rank, kind, _, n in items if kind == "listed"]
        if len(listed) > MAX_LISTED_RANKS:
            raise ValueError(f"more than {MAX_LISTED_RANKS} listed rows for {where}")
        _check_ranked_listed(listed, where)
        origin_kreise = [ars5 for _, kind, ars5, _ in items if kind == "listed"]
        duplicated = sorted({a for a in origin_kreise if origin_kreise.count(a) > 1})
        if duplicated:
            raise ValueError(f"origin Kreis listed twice for {where}: duplicate {duplicated}")
        singles = {}
        for kind in _SPECIAL_KINDS:
            values = [n for _, k, _, n in items if k == kind]
            if len(values) != 1:
                raise ValueError(f"expected exactly one {kind!r} row for {where}, "
                                 f"got {len(values)}")
            singles[kind] = values[0]
        closed = sum(n for _, n in listed) + singles["abroad"] + singles["other"]
        if closed != singles["total"]:
            raise ValueError(f"rows of {where} -- sum does not close: listed + abroad + other = "
                             f"{closed}, total = {singles['total']}")

    by_kreis: dict[str, list] = {}
    for ars5, name, rank_or_kind, institution, students in kreis_destinations:
        where = f"Kreis {ars5!r} ({name})"
        _check_count(students, where)
        kind = _row_kind(rank_or_kind, where)
        if kind in ("abroad", "other"):
            raise ValueError(f"kreis_destinations has no {kind!r} row, got one for {where}")
        _check_ars5(ars5, where)
        if kind == "listed" and not institution:
            raise ValueError(f"empty institution in a listed row of {where}")
        by_kreis.setdefault(ars5, []).append((rank_or_kind, kind, institution, students))
        records.append({"view": "kreis_destinations", "institution": institution,
                        "origin_ars5": ars5, "origin_name": name,
                        "rank": rank_or_kind if kind == "listed" else pd.NA,
                        "row_kind": kind, "students_persons": students})
    for ars5, items in by_kreis.items():
        where = f"Kreis {ars5!r}"
        listed = [(rank, n) for rank, kind, _, n in items if kind == "listed"]
        if len(listed) > MAX_LISTED_RANKS:
            raise ValueError(f"more than {MAX_LISTED_RANKS} listed rows for {where}")
        _check_ranked_listed(listed, where)
        institutions = [inst for _, kind, inst, _ in items if kind == "listed"]
        duplicated = sorted({i for i in institutions if institutions.count(i) > 1})
        if duplicated:
            raise ValueError(f"institution listed twice for {where}: duplicate {duplicated}")
        totals = [n for _, kind, _, n in items if kind == "total"]
        if len(totals) != 1:
            raise ValueError(f"expected exactly one 'total' row for {where}, got {len(totals)}")
        if sum(n for _, n in listed) > totals[0]:
            raise ValueError(f"listed rows of {where} exceed the total {totals[0]}")
        for rank, kind, institution, n in items:
            if kind != "listed":
                continue
            other = listed_origin_cells.get((institution, ars5))
            if other is not None and other != n:
                raise ValueError(f"views disagree for institution {institution!r} x Kreis "
                                 f"{ars5!r}: kreis_destinations {n}, institution_origins {other}")

    frame = pd.DataFrame(records, columns=_COLUMNS)
    frame["rank"] = frame["rank"].astype("Int64")
    return frame


def _require_metric(frame, label: str) -> None:
    """Distances are only computed in EPSG:25832 (metric); any other CRS is refused."""
    if frame.crs is None or frame.crs.to_epsg() != int(METRIC_CRS.split(":")[1]):
        raise ValueError(f"{label} must be in the metric CRS {METRIC_CRS}, got {frame.crs}; "
                         "reproject it explicitly first")


def required_origin_kreise(kreis_polygons, site_points, max_distance_km: float = 100.0) -> list[str]:
    """Sorted ARS-5 codes of Kreise whose polygon lies within max_distance_km of any site.

    kreis_polygons needs an ``ars5`` column; both frames must be in EPSG:25832.
    Polygon (not centroid) distance makes this a superset of the population-weighted
    centroid rule of the design of issue #140, so the pinned list over-covers rather than misses.
    """
    from shapely.ops import unary_union

    _require_metric(kreis_polygons, "kreis_polygons")
    _require_metric(site_points, "site_points")
    if "ars5" not in kreis_polygons.columns:
        raise ValueError("kreis_polygons needs an 'ars5' column")
    if len(site_points) == 0:
        raise ValueError("site_points is empty")
    sites = unary_union(list(site_points.geometry))
    distance_m = kreis_polygons.geometry.distance(sites)
    return sorted(set(kreis_polygons.loc[distance_m <= max_distance_km * 1000.0, "ars5"]))


def prepare_vg250_frames(kreise_raw, communes_raw, site_ags8):
    """Kreis polygons (``ars5``) and site-commune points from raw VG250 layers.

    Same conventions as the repository's own loaders (``braunschweig.analysis.spatial``
    ``load_kreise`` / ``load_gemeinden``, ``braunschweig.data.cordon.network.read_kreise``):
    land area only (``GF == 4``, applied to both layers before any geometry is used),
    ``ars5 = ARS[:5]`` (the Kreis layer carries 5-character ARS, the commune layer 12),
    8-digit commune key ``ARS[:5] + ARS[9:12]``, geometries dissolved per
    key and reprojected to EPSG:25832. Site points are representative points of the commune.
    """
    def land_only(frame):
        return frame[frame["GF"] == 4] if "GF" in frame.columns else frame

    kreise = land_only(kreise_raw).copy()
    kreis_ars = kreise["ARS"].astype(str)
    bad = kreis_ars[kreis_ars.str.len() < 5]
    if len(bad):
        raise ValueError(f"layer vg250_krs: ARS must have at least 5 characters (the real layer "
                         f"has exactly 5), got {sorted(set(bad))[:5]}")
    kreise["ars5"] = kreis_ars.str[:5]
    kreise = kreise.to_crs(METRIC_CRS).dissolve(by="ars5", as_index=False)[["ars5", "geometry"]]

    communes = land_only(communes_raw).copy()
    ars = communes["ARS"].astype(str)
    bad = ars[ars.str.len() != 12]
    if len(bad):
        raise ValueError(f"layer vg250_gem: ARS must have exactly 12 characters, got "
                         f"{sorted(set(bad))[:5]}")
    communes["commune_id"] = ars.str[:5] + ars.str[9:12]
    wanted = set(site_ags8)
    communes = communes[communes["commune_id"].isin(wanted)]
    missing = wanted - set(communes["commune_id"])
    if missing:
        raise ValueError(f"site communes not found in the vg250_gem land-area rows: "
                         f"{sorted(missing)}")
    communes = communes.to_crs(METRIC_CRS).dissolve(by="commune_id", as_index=False)
    points = communes[["commune_id", "geometry"]].copy()
    points["geometry"] = points.geometry.representative_point()
    return kreise, points


def local_site_ags8(sites: pd.DataFrame) -> list[str]:
    """site_ags8 of the LOCAL sites of the R1 table (scope == 'local'); raises if none.

    Raises ValueError naming the column when ``scope`` or ``site_ags8`` is missing.
    """
    missing = [column for column in ("scope", "site_ags8") if column not in sites.columns]
    if missing:
        raise ValueError(f"the R1 table is missing required column(s) {missing}")
    local = sites.loc[sites["scope"] == "local", "site_ags8"]
    if local.empty:
        raise ValueError("the R1 table has no site with scope == 'local'")
    return sorted(set(local))


def _list_required(vg250: Path, sites_csv: Path, max_distance_km: float) -> list[str]:
    import geopandas as gpd

    from braunschweig.analysis.spatial import VG250_INNER  # the repository's one owner of this path

    for path in (vg250, sites_csv):
        if not Path(path).exists():
            raise FileNotFoundError(f"input not found: {path}")
    source = f"/vsizip/{Path(vg250).resolve()}/{VG250_INNER}"
    sites = pd.read_csv(sites_csv, comment="#", dtype={"site_ags8": str})
    kreise, points = prepare_vg250_frames(
        gpd.read_file(source, layer="vg250_krs"), gpd.read_file(source, layer="vg250_gem"),
        local_site_ags8(sites))
    return required_origin_kreise(kreise, points, max_distance_km)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-required-kreise", action="store_true")
    parser.add_argument("--vg250", type=Path, help="VG250 archive, normally eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip (dataset vg250_ew)")
    parser.add_argument("--sites", type=Path, help="lsn_university_sites.csv")
    parser.add_argument("--max-distance-km", type=float, default=100.0)
    args = parser.parse_args(argv)

    if args.list_required_kreise:
        if args.vg250 is None or args.sites is None:
            parser.error("--list-required-kreise needs --vg250 and --sites")
        for ars5 in _list_required(args.vg250, args.sites, args.max_distance_km):
            print(ars5)
        return 0

    if not INSTITUTION_ORIGINS or not KREIS_DESTINATIONS or not ACCESS_DATE:
        raise ValueError("INSTITUTION_ORIGINS, KREIS_DESTINATIONS and ACCESS_DATE are empty: "
                         "fill them from the CHE DatenCHECK pages before running this script")
    frame = build_origins_frame(INSTITUTION_ORIGINS, KREIS_DESTINATIONS)
    counts = frame["view"].value_counts()
    header = [
        f"# Source: {SOURCE}, {SEMESTER}, accessed {ACCESS_DATE}",
        "# Script: scripts/seed_che_student_origins.py (values pinned in INSTITUTION_ORIGINS "
        "and KREIS_DESTINATIONS)",
        "# Licence: the CHE page names no licence; committed by owner decision 2026-11-25",
        "# Origin = Kreis of the school leaving certificate, not residence; cells below the "
        "tenth value are censored",
        f"# Rows: institution_origins {int(counts.get('institution_origins', 0))}, "
        f"kreis_destinations {int(counts.get('kreis_destinations', 0))}",
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(header) + "\n")
        frame.to_csv(handle, index=False, lineterminator="\n")
    print(f"[seed_che_student_origins] wrote {OUTPUT_PATH} ({len(frame)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
