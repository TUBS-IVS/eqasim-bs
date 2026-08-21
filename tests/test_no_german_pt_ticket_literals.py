"""Guard: no German PT-ticket literal survives outside the raw-CSV boundary.

The PT ticket taxonomy was renamed to English (issue #329); the ONLY place the
codebook-German names may appear in code is the boundary mapping in
braunschweig/data/mid/reference_tables.py (raw committed CSVs keep German
headers for provenance). 'anderes'/'keine_angabe' appear legitimately in OTHER
survey contexts, so this guard checks only the unambiguous PT-ticket literals.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "fahre_nie", "einzelfahrschein", "mehrfachkarte",
    "wochen_monat_ohne_abo", "monat_abo_jahreskarte", "jobticket_semesterticket",
)
ALLOWED_FILES = {
    REPO / "braunschweig" / "data" / "mid" / "reference_tables.py",
    Path(__file__).resolve(),
}
# Tests that deliberately rebuild a RAW committed CSV (codebook-German headers)
# opt out with this marker comment near their fixture.
FIXTURE_MARKER = "PT_RAW_FIXTURE_OK"
SCAN_DIRS = ("braunschweig", "scripts", "matsim", "synthesis", "eqasim_common", "tests")


def test_no_german_pt_ticket_literals_outside_boundary():
    offenders = []
    for top in SCAN_DIRS:
        base = REPO / top
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if path in ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if FIXTURE_MARKER in text:
                continue
            hits = [w for w in FORBIDDEN if w in text]
            if hits:
                offenders.append((str(path.relative_to(REPO)), hits))
    assert not offenders, f"German PT-ticket literals outside the boundary: {offenders}"
