"""Static guard: the Windows lock files must carry every pip pin of environment.yml.

Defect class (audit TOOL-F1): a pip pin was added to environment.yml but the lock files were not
re-generated, so a lock-based install silently lacked the package. These checks read only text files.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
ENVIRONMENT_FILE = REPO / "environment.yml"
CONDA_LOCK_FILE = REPO / "environments" / "conda-lock.yml"
EXPLICIT_LOCK_FILE = REPO / "environments" / "conda-win-64.lock"


def _normalise(name: str) -> str:
    return re.sub(r"[._]", "-", name.strip().lower())


def _environment_pip_pins(environment_file: Path = ENVIRONMENT_FILE) -> dict[str, str]:
    environment = yaml.safe_load(environment_file.read_text(encoding="utf-8"))
    pins: dict[str, str] = {}
    for dependency in environment["dependencies"]:
        if isinstance(dependency, dict) and "pip" in dependency:
            for requirement in dependency["pip"]:
                match = re.fullmatch(r"([A-Za-z0-9._-]+)==(\S+)", requirement.strip())
                if match:  # URL requirements (git+...) carry no version pin
                    pins[_normalise(match.group(1))] = match.group(2)
    return pins


def _load_conda_lock(conda_lock_file: Path = CONDA_LOCK_FILE) -> dict:
    return yaml.safe_load(conda_lock_file.read_text(encoding="utf-8"))


def _lock_pip_packages(lock: dict) -> dict[str, str]:
    return {_normalise(package["name"]): str(package["version"])
            for package in lock["package"] if package["manager"] == "pip"}


def _lock_all_packages(lock: dict) -> dict[str, str]:
    return {_normalise(package["name"]): str(package["version"]) for package in lock["package"]}


def _explicit_lock_pip_names(explicit_lock_file: Path = EXPLICIT_LOCK_FILE) -> set[str]:
    return {_normalise(match.group(1))
            for match in re.finditer(r"^# pip (\S+) @ ", explicit_lock_file.read_text(encoding="utf-8"),
                                     flags=re.MULTILINE)}


def _explicit_lock_input_hash(explicit_lock_file: Path = EXPLICIT_LOCK_FILE) -> str:
    match = re.search(r"^# input_hash: (\S+)", explicit_lock_file.read_text(encoding="utf-8"),
                      flags=re.MULTILINE)
    assert match, f"{explicit_lock_file.name} has no '# input_hash:' line"
    return match.group(1)


def test_environment_pip_pins_are_in_conda_lock():
    # conda-lock may satisfy a pip pin from the conda layer when the same package is also a conda
    # requirement (pytest), so any manager counts as long as the pinned version is locked.
    locked = _lock_all_packages(_load_conda_lock())
    pins = _environment_pip_pins()
    assert pins, "no pip pins parsed from environment.yml; the parser is broken"
    wrong = {name: (version, locked.get(name)) for name, version in pins.items()
             if locked.get(name) != version}
    assert not wrong, f"environment.yml pip pins missing or different in conda-lock.yml (pin, locked): {wrong}"


def test_explicit_lock_and_conda_lock_carry_the_same_pip_packages():
    assert _explicit_lock_pip_names() == set(_lock_pip_packages(_load_conda_lock()))


def test_explicit_lock_input_hash_matches_conda_lock_content_hash():
    content_hash = _load_conda_lock()["metadata"]["content_hash"]["win-64"]
    assert _explicit_lock_input_hash() == content_hash
