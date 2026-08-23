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
    scanned = 0
    for top in SCAN_DIRS:
        base = REPO / top
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            scanned += 1
            if path in ALLOWED_FILES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if FIXTURE_MARKER in text:
                continue
            hits = [w for w in FORBIDDEN if w in text]
            if hits:
                offenders.append((str(path.relative_to(REPO)), hits))
    assert not offenders, f"German PT-ticket literals outside the boundary: {offenders}"
    # Tripwire, not a coverage target: if REPO/SCAN_DIRS ever stop resolving
    # (file moved, layout change) this guard must fail loudly instead of
    # passing vacuously on an empty scan. 100 is far below the repo's actual
    # Python file count (~1000+) and only guards against a silently empty scan.
    assert scanned > 100, (
        f"only scanned {scanned} files -- REPO/SCAN_DIRS may have stopped "
        "resolving, which would make the assertion above pass vacuously"
    )
