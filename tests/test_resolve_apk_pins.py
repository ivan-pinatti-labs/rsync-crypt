"""Tests for scripts/resolve-apk-pins.py.

Exercises only the pure parsing and precision-matching functions
(`parse_apk_policy`, `match_precision`, `apply`), never `resolve_versions`
itself, which shells out to Docker: these run anywhere, with no Docker
daemon and no network, the same way the sibling `assert-pin-only-diff.py`
tests do.

    pytest -m scripts tests/test_resolve_apk_pins.py

The `apk policy` transcript fixtures below are a real capture, taken by
running scripts/resolve-apk-pins.py's own docker invocation against
`alpine:3.24` on 2026-09-03: `apk policy bash gocryptfs less openssh rsync
sshfs vim`. That is also, not coincidentally, the exact Alpine release and
package set the `Dockerfile` is pinned to today, which is what lets
`test_matches_every_current_pin_exactly` assert the resolver reproduces
every one of the seven live values with no fixture rigged to match.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "resolve-apk-pins.py"

pytestmark = pytest.mark.scripts


def _load_module():
    # The script has a hyphen in its filename, so it cannot be imported as
    # `resolve_apk_pins` the normal way; loaded explicitly from its path
    # instead, the same shape a hyphenated script under scripts/ always
    # needs here.
    spec = importlib.util.spec_from_file_location("resolve_apk_pins", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


resolve_apk_pins = _load_module()

# A real `apk policy bash gocryptfs less openssh rsync sshfs vim` transcript
# from inside `alpine:3.24`, captured 2026-09-03. Deliberately not sorted the
# way PACKAGE_TO_VAR is: apk prints packages in the order they were asked
# for, and parse_apk_policy must not depend on any particular order.
ALPINE_3_24_POLICY = """\
bash policy:
  5.3.9-r1:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
gocryptfs policy:
  2.6.1-r5:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/community
less policy:
  702-r0:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
openssh policy:
  10.3_p1-r1:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
rsync policy:
  3.5.0-r0:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
sshfs policy:
  3.7.6-r0:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
vim policy:
  9.2.1014-r0:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/community
"""

# The precision each ARG is pinned at today, read straight off the Dockerfile
# rather than hardcoded a second time, so this test cannot drift from what is
# actually committed.
CURRENT_PINS = resolve_apk_pins.read_current_pins(REPO_ROOT / "Dockerfile")


# ---------------------------------------------------------------------------
# parse_apk_policy: reading apk's own transcript
# ---------------------------------------------------------------------------


def test_parses_every_package_block():
    versions = resolve_apk_pins.parse_apk_policy(ALPINE_3_24_POLICY)
    assert versions == {
        "bash": "5.3.9-r1",
        "gocryptfs": "2.6.1-r5",
        "less": "702-r0",
        "openssh": "10.3_p1-r1",
        "rsync": "3.5.0-r0",
        "sshfs": "3.7.6-r0",
        "vim": "9.2.1014-r0",
    }


def test_takes_the_first_version_when_several_are_listed():
    # apk policy lists every available version under a package, most
    # preferred first; only the first is what would actually be installed.
    transcript = (
        "gocryptfs policy:\n"
        "  2.6.1-r5:\n"
        "    https://dl-cdn.alpinelinux.org/alpine/v3.24/community\n"
        "  2.5.0-r0:\n"
        "    https://dl-cdn.alpinelinux.org/alpine/v3.24/community\n"
    )
    assert resolve_apk_pins.parse_apk_policy(transcript) == {"gocryptfs": "2.6.1-r5"}


def test_ignores_repository_url_lines():
    # A repo URL is indented four spaces, not two, and never ends in ":" the
    # way a version line's own colon-terminated shape does; parse_apk_policy
    # must not mistake one for a second version.
    transcript = (
        "bash policy:\n"
        "  5.3.9-r1:\n"
        "    https://dl-cdn.alpinelinux.org/alpine/v3.24/main\n"
    )
    assert resolve_apk_pins.parse_apk_policy(transcript) == {"bash": "5.3.9-r1"}


# ---------------------------------------------------------------------------
# match_precision: truncating to the shape .env.example already commits to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("full_version", "current_pin", "expected"),
    [
        ("2.6.1-r5", "2.6", "2.6"),
        ("702-r0", "702", "702"),
        ("10.3_p1-r1", "10.3", "10.3"),
        ("5.3.9-r1", "5.3", "5.3"),
        ("3.5.0-r0", "3.5", "3.5"),
        ("3.7.6-r0", "3.7", "3.7"),
        ("9.2.1014-r0", "9.2", "9.2"),
    ],
)
def test_matches_the_current_pins_precision(full_version, current_pin, expected):
    assert resolve_apk_pins.match_precision(full_version, current_pin) == expected


def test_matches_every_current_pin_exactly():
    # The point of the whole script: resolving alpine:3.24 today must
    # reproduce every one of the seven values .env.example already carries,
    # since that is what alpine:3.24 was verified against by hand before
    # this script existed (see CLAUDE.md, "Alpine gocryptfs version").
    versions = resolve_apk_pins.parse_apk_policy(ALPINE_3_24_POLICY)
    for pkg, var in resolve_apk_pins.PACKAGE_TO_VAR.items():
        resolved = resolve_apk_pins.match_precision(versions[pkg], CURRENT_PINS[var])
        assert resolved == CURRENT_PINS[var], f"{var}: expected no change"


def test_truncates_a_longer_full_version_to_one_component():
    assert resolve_apk_pins.match_precision("5.3.9-r1", "702") == "5"


def test_a_non_numeric_version_is_returned_unchanged():
    # apk policy always prints a real version, never a floating word, but a
    # transcript this script cannot parse as a version must not crash or
    # silently coerce into something that looks like a bump.
    assert resolve_apk_pins.match_precision("unknown", "2.6") == "unknown"


# ---------------------------------------------------------------------------
# apply: rewriting the Dockerfile, and leaving everything else alone
# ---------------------------------------------------------------------------


def _write_dockerfile(tmp_path, overrides):
    base = {
        "BASH_VERSION": "5.3",
        "GOCRYPTFS_VERSION": "2.6",
        "LESS_VERSION": "702",
        "OPENSSH_VERSION": "10.3",
        "RSYNC_VERSION": "3.5",
        "SSHFS_VERSION": "3.7",
        "VIM_VERSION": "9.2",
    }
    base.update(overrides)
    path = tmp_path / "Dockerfile"
    lines = [
        "# renovate: datasource=docker depName=alpine versioning=docker",
        "ARG ALPINE_VERSION=3.24",
        "FROM alpine:${ALPINE_VERSION}",
        *[
            line
            for name, value in base.items()
            for line in (
                "# apk-pin: resolved-from=ALPINE_VERSION",
                f"ARG {name}={value}",
            )
        ],
        "RUN apk add --no-cache bash~=${BASH_VERSION}",
        "",
    ]
    path.write_text("\n".join(lines))
    return path


def test_apply_reports_no_changes_when_everything_already_matches(tmp_path):
    path = _write_dockerfile(tmp_path, {})
    before = path.read_text()
    changes = resolve_apk_pins.apply(
        path, resolve_apk_pins.parse_apk_policy(ALPINE_3_24_POLICY)
    )
    assert changes == []
    assert path.read_text() == before


def test_apply_rewrites_only_the_pins_that_changed(tmp_path):
    path = _write_dockerfile(
        tmp_path, {"GOCRYPTFS_VERSION": "2.5", "LESS_VERSION": "685"}
    )
    changes = resolve_apk_pins.apply(
        path, resolve_apk_pins.parse_apk_policy(ALPINE_3_24_POLICY)
    )
    assert sorted(changes) == [
        "GOCRYPTFS_VERSION: 2.5 -> 2.6",
        "LESS_VERSION: 685 -> 702",
    ]
    rewritten = path.read_text()
    assert "ARG GOCRYPTFS_VERSION=2.6" in rewritten
    assert "ARG LESS_VERSION=702" in rewritten
    # Untouched lines, including the unrelated ALPINE_VERSION pin, both
    # annotation markers and the instructions around them, must survive byte
    # for byte. ALPINE_VERSION especially: it is the input this resolution is
    # derived from, and rewriting it would be the resolver overwriting the
    # very bump that triggered it.
    assert "ARG ALPINE_VERSION=3.24" in rewritten
    assert "FROM alpine:${ALPINE_VERSION}" in rewritten
    assert "RUN apk add --no-cache bash~=${BASH_VERSION}" in rewritten
    assert "# renovate: datasource=docker depName=alpine versioning=docker" in rewritten
    assert rewritten.count("# apk-pin: resolved-from=ALPINE_VERSION") == 7


def test_apply_raises_if_apk_policy_never_reported_a_package(tmp_path):
    path = _write_dockerfile(tmp_path, {})
    incomplete = resolve_apk_pins.parse_apk_policy(ALPINE_3_24_POLICY)
    del incomplete["vim"]
    with pytest.raises(SystemExit):
        resolve_apk_pins.apply(path, incomplete)


def test_read_current_pins_raises_on_a_missing_arg(tmp_path):
    path = tmp_path / "Dockerfile"
    path.write_text("ARG BASH_VERSION=5.3\n")
    with pytest.raises(SystemExit):
        resolve_apk_pins.read_current_pins(path)


def test_read_current_pins_ignores_an_arg_with_no_default(tmp_path):
    """`ARG NAME` with no `=` is a build argument, not a pin to resolve.

    Rewriting one into `ARG NAME=<version>` would silently give it a default
    it never had, so the grammar requires the `=` and such a line reads as
    absent instead.
    """
    path = tmp_path / "Dockerfile"
    path.write_text("ARG GOCRYPTFS_VERSION\n")
    with pytest.raises(SystemExit):
        resolve_apk_pins.read_current_pins(path)


# ---------------------------------------------------------------------------
# resolve_versions: the one function that shells out, guarded before it does
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["3.24; rm -rf /", "$(id)", "3.24 && curl evil", "", "edge", "3.24\ntrap"],
)
def test_refuses_an_unsafe_alpine_version_before_touching_docker(value):
    with pytest.raises(SystemExit):
        resolve_apk_pins.resolve_versions(value)
