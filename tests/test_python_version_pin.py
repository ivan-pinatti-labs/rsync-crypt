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
`scripts/assert-pin-only-diff.py` can grade, so `Pin Only` refuses the diff and
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
