"""Binds the road-type port to the contrib class it was proven equal to (post-run emissions, #688).

The HBEFA road type of every link comes from ``braunschweig.emissions.road_types``, a Python
port of MATSim's ``org.matsim.contrib.emissions.OsmHbefaMapping``. The port was compared with
the original link by link on the network of a 100 % run (run manifest
``emissions-road-type-parity-2026-11-26``). That proof holds for one version of the original
only, so it is tied to the jar here (ADR-0209, amendment A7):

* ``osm_hbefa_parity_proof.json`` lists, per proof, the sha256 of the class files the original's
  result depends on (``PROVEN_CLASS_FILES``);
* :func:`require_proven_jar` reads the same class files from the pipeline jar and raises
  :class:`UnprovenRoadTypeMapping` when no proof names exactly these digests. A new MATSim
  version that changes one of the classes therefore stops the emissions run until the proof
  is repeated;
* ``osm_hbefa_parity_cases.csv`` holds every combination of OSM class and freespeed that occurs
  on the links usable by car or truck of the proven network, with the road type the ORIGINAL
  returned. ``tests/test_emissions_road_types.py`` runs the port against it, so an edit of the
  port that leaves the proven behaviour fails a test.

There is deliberately no switch that turns the check off: a wiring run that needs another jar
switches it off in its driver script, like the refusal of the sample tables.
"""
from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from pathlib import Path

logger = logging.getLogger("braunschweig.emissions.road_type_proof")

_LOG_TAG = "[emissions.road_type_proof]"

PROOF_FILE = Path(__file__).with_name("osm_hbefa_parity_proof.json")
CASES_FILE = Path(__file__).with_name("osm_hbefa_parity_cases.csv")
#: The committed file a stage must add to its validate() token (it is not Python source).
COMMITTED_PROOF_FILES = (PROOF_FILE,)

#: Class files that decide what the original returns for a link: the mapping itself, its table
#: entry class, and ``NetworkUtils`` (``getHighwayType``, ``getAllowedSpeed``).
PROVEN_CLASS_FILES = (
    "org/matsim/contrib/emissions/OsmHbefaMapping.class",
    "org/matsim/contrib/emissions/OsmHbefaMapping$Hbefa.class",
    "org/matsim/core/network/NetworkUtils.class",
)

_PROOF_FORMAT = 1


class UnprovenRoadTypeMapping(RuntimeError):
    """The jar holds another ``OsmHbefaMapping`` than any the port was proven equal to."""


def load_proofs(path: Path | None = None) -> list[dict]:
    """Read and check the committed proof records (default: :data:`PROOF_FILE`)."""
    path = PROOF_FILE if path is None else Path(path)
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("format") != _PROOF_FORMAT:
        raise ValueError(f"{path}: expected an object with format {_PROOF_FORMAT}")
    proofs = document.get("proofs")
    if not isinstance(proofs, list) or not proofs:
        raise ValueError(f"{path}: 'proofs' must be a non-empty list")
    for index, proof in enumerate(proofs):
        for key in ("run_manifest", "contrib_version"):
            if not isinstance(proof.get(key), str) or not proof[key]:
                raise ValueError(f"{path}: proofs[{index}].{key} must be a non-empty text")
        digests = proof.get("class_sha256")
        if not isinstance(digests, dict) or set(digests) != set(PROVEN_CLASS_FILES):
            raise ValueError(
                f"{path}: proofs[{index}].class_sha256 must name exactly {list(PROVEN_CLASS_FILES)}"
            )
        for name, digest in digests.items():
            if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
                raise ValueError(f"{path}: proofs[{index}].class_sha256[{name!r}] is not a sha256 hex digest")
    return proofs


def jar_class_digests(jar_path) -> dict[str, str | None]:
    """sha256 of every class file of :data:`PROVEN_CLASS_FILES` in the jar (``None``: not in the jar)."""
    jar_path = Path(jar_path)
    if not jar_path.is_file():
        raise FileNotFoundError(f"{_LOG_TAG} pipeline jar not found: {jar_path}")
    digests: dict[str, str | None] = {}
    with zipfile.ZipFile(jar_path) as jar:
        names = set(jar.namelist())
        for name in PROVEN_CLASS_FILES:
            digests[name] = hashlib.sha256(jar.read(name)).hexdigest() if name in names else None
    return digests


def require_proven_jar(jar_path) -> dict:
    """Return the proof record that matches the jar's class files; raise when none does."""
    proofs = load_proofs()
    found = jar_class_digests(jar_path)
    for proof in proofs:
        if proof["class_sha256"] == found:
            logger.info(
                "%s %s holds the OsmHbefaMapping the road-type port was proven equal to "
                "(proof run on contrib %s, run manifest %s)",
                _LOG_TAG, jar_path, proof["contrib_version"], proof["run_manifest"],
            )
            # The version is the one the proof ran on; another build with byte-identical class
            # files matches the same record.
            return {
                "run_manifest": proof["run_manifest"],
                "proven_on_contrib_version": proof["contrib_version"],
                "class_sha256": dict(found),
            }
    closest = min(proofs, key=lambda proof: sum(proof["class_sha256"][name] != found[name] for name in found))
    differing = sorted(name for name in found if closest["class_sha256"][name] != found[name])
    missing = sorted(name for name, digest in found.items() if digest is None)
    raise UnprovenRoadTypeMapping(
        f"{_LOG_TAG} the road types of the emissions view come from the Python port of OsmHbefaMapping, "
        f"which is proven equal to the original only for the class files named in {PROOF_FILE.name} "
        f"(run manifests {sorted({proof['run_manifest'] for proof in proofs})}). The pipeline jar "
        f"{jar_path} differs in {differing}"
        + (f" (not in the jar: {missing})" if missing else "")
        + ". Repeat the proof against this jar (scripts and method in the run manifest), check the port "
        f"against the new result, and add a proof record to {PROOF_FILE.name}."
    )
