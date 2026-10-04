"""Keep ruff's target interpreter and CI's interpreter from drifting apart.

`ruff.toml`'s `target-version` decides which idioms `UP` rewrites to and which
version-dependent rules fire; `python-version` in
.github/workflows/pull-request-validation.yml decides which interpreter
actually runs the tests and the hooks. Neither value is derived from the
other, so the pair can silently disagree, and the failure is quiet in the
worse direction: ruff grading against an older interpreter than CI runs keeps
passing while suggesting fixes the real interpreter is free to break on.

Nothing watches either value automatically. Renovate's github-actions manager
does propose `uses-with` bumps of exactly this shape, and .github/renovate.json5
disables that depType on purpose, because a `with:` input is not a pin position
the shared pin-only check can grade, so `Pin Only` refuses the diff and
the pull request can never merge (PR #82 proved it live). That makes every
future Python bump here a hand edit of two files, which is precisely the kind
of pairing a person forgets. This test is the reminder.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/pull-request-validation.yml"
RUFF_CONFIG = REPO_ROOT / "ruff.toml"

# `python-version: "3.14"`, quoted, as actions/setup-python is given it. The
# quotes are not optional in the workflow and not optional here either: YAML
# reads a bare 3.10 as the float 3.1, so an unquoted value is a bug worth
# failing on rather than a spelling to accept.
WORKFLOW_PYTHON = re.compile(
    r'^\s*python-version:\s*"(?P<major>\d+)\.(?P<minor>\d+)"\s*$', re.MULTILINE
)

# `target-version = "py314"`, at the top level of ruff.toml.
RUFF_TARGET = re.compile(
    r'^target-version\s*=\s*"py(?P<major>\d)(?P<minor>\d+)"\s*$', re.MULTILINE
)


def test_workflow_declares_exactly_one_python_version():
    """More than one would make "the interpreter CI uses" ambiguous."""
    matches = WORKFLOW_PYTHON.findall(WORKFLOW.read_text())
    assert len(matches) == 1, (
        f"expected exactly one quoted python-version in {WORKFLOW.name}, "
        f"found {len(matches)}: {matches}"
    )


def test_ruff_target_matches_the_interpreter_ci_runs():
    workflow = WORKFLOW_PYTHON.search(WORKFLOW.read_text())
    assert workflow, f"no quoted python-version found in {WORKFLOW.name}"

    ruff = RUFF_TARGET.search(RUFF_CONFIG.read_text())
    assert ruff, f"no target-version found in {RUFF_CONFIG.name}"

    ci = (workflow.group("major"), workflow.group("minor"))
    target = (ruff.group("major"), ruff.group("minor"))
    assert ci == target, (
        f"{RUFF_CONFIG.name} targets py{target[0]}{target[1]} but "
        f"{WORKFLOW.name} runs Python {ci[0]}.{ci[1]}. Both have to move "
        "together; nothing derives one from the other."
    )


# `sonar.python.version=3.14`, which SonarQube Cloud's Python rules judge the
# code against. A third copy of the same fact, so it drifts the same way.
SONAR_PROPERTIES = REPO_ROOT / "sonar-project.properties"
SONAR_PYTHON = re.compile(
    r"^sonar\.python\.version=(?P<major>\d+)\.(?P<minor>\d+)\s*$", re.MULTILINE
)


def test_sonar_python_version_matches_the_interpreter_ci_runs():
    workflow = WORKFLOW_PYTHON.search(WORKFLOW.read_text())
    assert workflow, f"no quoted python-version found in {WORKFLOW.name}"

    sonar = SONAR_PYTHON.search(SONAR_PROPERTIES.read_text())
    assert sonar, f"no sonar.python.version found in {SONAR_PROPERTIES.name}"

    ci = (workflow.group("major"), workflow.group("minor"))
    analyzed = (sonar.group("major"), sonar.group("minor"))
    assert ci == analyzed, (
        f"{SONAR_PROPERTIES.name} analyzes as Python {analyzed[0]}.{analyzed[1]} "
        f"but {WORKFLOW.name} runs Python {ci[0]}.{ci[1]}. Both have to move "
        "together; nothing derives one from the other."
    )


# `make coverage` (which sonarqube.yml runs) measures the Python in a
# container, from PYTHON_IMAGE in the Makefile. A fourth copy, its tag moved
# by hand for the same reason; Renovate only moves its digest.
MAKEFILE = REPO_ROOT / "Makefile"
MAKEFILE_PYTHON = re.compile(
    r"^PYTHON_IMAGE \?= \S+/python:(?P<major>\d+)\.(?P<minor>\d+)-slim"
    r"@sha256:[0-9a-f]{64}$",
    re.MULTILINE,
)


def test_coverage_image_runs_the_same_interpreter_as_ci():
    workflow = WORKFLOW_PYTHON.search(WORKFLOW.read_text())
    assert workflow, f"no quoted python-version found in {WORKFLOW.name}"

    image = MAKEFILE_PYTHON.findall(MAKEFILE.read_text())
    assert len(image) == 1, (
        f"expected exactly one digest pinned PYTHON_IMAGE in {MAKEFILE.name}, "
        f"found {len(image)}: {image}"
    )

    ci = (workflow.group("major"), workflow.group("minor"))
    assert image[0] == ci, (
        f"{MAKEFILE.name}'s PYTHON_IMAGE is Python {image[0][0]}.{image[0][1]} but "
        f"{WORKFLOW.name} runs Python {ci[0]}.{ci[1]}. Both have to move "
        "together; nothing derives one from the other."
    )


# Every other workflow that sets up Python. Only pull-request-validation.yml
# does today; one added later has to run the same interpreter, quoted.
WORKFLOWS = sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml"))
ANY_PYTHON_VERSION = re.compile(
    r"^\s*python-version:\s*(?P<value>.*?)\s*$", re.MULTILINE
)


def test_every_workflow_python_version_is_quoted_and_the_same():
    workflow = WORKFLOW_PYTHON.search(WORKFLOW.read_text())
    assert workflow, f"no quoted python-version found in {WORKFLOW.name}"
    ci = f'"{workflow.group("major")}.{workflow.group("minor")}"'

    found = {
        f"{path.name}: {match.group('value')}"
        for path in WORKFLOWS
        for match in ANY_PYTHON_VERSION.finditer(path.read_text())
    }
    wrong = sorted(entry for entry in found if not entry.endswith(f": {ci}"))
    assert not wrong, (
        f"every python-version has to be {ci}, quoted, as in {WORKFLOW.name}; "
        f"found {wrong}"
    )


# The hash lock tests/requirements.txt is resolved for one interpreter
# (`uv pip compile --python-version`). Its header points at the documented
# command instead of restating it, so both are read: a `--python-version` in
# any lock header, and in the command docs/USAGE.md gives, along with the
# python image that command runs in.
LOCK_FILES = sorted((REPO_ROOT / "tests").glob("requirements*.txt"))
LOCK_DOC = REPO_ROOT / "docs/USAGE.md"
LOCK_PYTHON = re.compile(r"--python-version[ =](?P<major>\d+)\.(?P<minor>\d+)\b")
# Any reference to the python image: `python:3.14-slim`, `docker.io/python:...`
# or `docker.io/library/python:...`, with or without a variant suffix.
DOC_IMAGE = re.compile(
    r"(?<![\w.-])(?:[\w.-]+/)*python:(?P<major>\d+)\.(?P<minor>\d+)\b"
)


def _lock_header(path: Path) -> str:
    lines = []
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines)


def test_hash_lock_is_resolved_for_the_interpreter_ci_runs():
    workflow = WORKFLOW_PYTHON.search(WORKFLOW.read_text())
    assert workflow, f"no quoted python-version found in {WORKFLOW.name}"
    ci = (workflow.group("major"), workflow.group("minor"))

    assert LOCK_FILES, "no tests/requirements*.txt lock found"
    sources = {path.name: _lock_header(path) for path in LOCK_FILES}
    sources[LOCK_DOC.name] = LOCK_DOC.read_text()
    found = [
        (name, match.group(0), (match.group("major"), match.group("minor")))
        for name, text in sources.items()
        for pattern in (LOCK_PYTHON, DOC_IMAGE)
        for match in pattern.finditer(text)
    ]
    assert any(
        name == LOCK_DOC.name and "--python-version" in text for name, text, _ in found
    ), f"no --python-version found in {LOCK_DOC.name}'s lock command"
    assert any(
        name == LOCK_DOC.name and "python:" in text for name, text, _ in found
    ), f"no python image found in {LOCK_DOC.name}'s lock command"
    drifted = sorted(f"{name}: {text}" for name, text, got in found if got != ci)
    assert not drifted, (
        f"the lock is resolved for a different Python than {WORKFLOW.name}'s "
        f"{ci[0]}.{ci[1]}: {drifted}. Regenerate it per {LOCK_DOC.name}, "
        "Updating the test dependencies."
    )
