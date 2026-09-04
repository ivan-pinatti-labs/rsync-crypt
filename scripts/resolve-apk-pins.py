#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ivan Pinatti
"""Re-resolve the apk-constrained version pins in the `Dockerfile`.

`GOCRYPTFS_VERSION`, `BASH_VERSION`, `LESS_VERSION`, `OPENSSH_VERSION`,
`RSYNC_VERSION`, `SSHFS_VERSION` and `VIM_VERSION` are apk `~=` version
constraints, not Docker tags: no Renovate datasource models "the version of
this apk package available in Alpine release X", so these seven are pinned
by hand against whichever `alpine:${ALPINE_VERSION}` the Dockerfile builds
from, each carrying a `# apk-pin: resolved-from=ALPINE_VERSION` marker (see
scripts/assert-pin-only-diff.py) instead of Renovate's own `# renovate:`
marker.

All eight are `ARG NAME=value` defaults in the `Dockerfile` itself. They used
to be quoted `NAME="value"` lines in `.env.example`, which this script
rewrote instead; the grammar below is the only thing that changed with them,
since the Dockerfile writes an unquoted value.

This script is that hand-resolution, automated. Given an Alpine version, it
runs `apk update && apk policy <pkg>` inside `alpine:<version>` for each of
the seven packages, and rewrites the `Dockerfile` in place with whatever it
finds, at the same precision each ARG already uses today (an `X.Y.Z-rN` apk
version truncated to as many `X.Y...` components as the current pin has:
`ARG LESS_VERSION=702` keeps one component, `ARG RSYNC_VERSION=3.5` keeps
two, matching `apk policy`'s own precedence order, which lists the version
that would actually be installed first).

Used by .github/workflows/resolve-apk-pins.yml, which runs it against the
`alpine:<version>` a Renovate ALPINE_VERSION pull request proposes and pushes
a commit if anything changed. Also runnable by hand:

    python3 scripts/resolve-apk-pins.py --alpine-version 3.24

The Docker-shelling half (`resolve_versions`) is kept separate from the pure
parsing and precision-matching half (`parse_apk_policy`,
`match_precision`) precisely so the latter can be unit tested with a captured
`apk policy` transcript and no Docker daemon or network access at all; see
tests/test_resolve_apk_pins.py.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"

# apk package name -> the Dockerfile ARG it fills. Alphabetical by package
# name, which is also the order `apk policy pkg1 pkg2 ...` prints its blocks
# in, though parse_apk_policy does not depend on that ordering.
PACKAGE_TO_VAR = {
    "bash": "BASH_VERSION",
    "gocryptfs": "GOCRYPTFS_VERSION",
    "less": "LESS_VERSION",
    "openssh": "OPENSSH_VERSION",
    "rsync": "RSYNC_VERSION",
    "sshfs": "SSHFS_VERSION",
    "vim": "VIM_VERSION",
}

# A safe Docker tag: digits and dots only. Alpine release tags never carry
# anything else (`3.24`, `3.24.1`, `edge`), and `edge` is deliberately not
# matched here: this script only ever runs against a value Renovate's own
# `versioning=docker` already validated as a real semver-shaped release, and
# failing closed on anything unexpected is cheaper than explaining a shell
# injection into `docker run alpine:<value>` after the fact.
SAFE_ALPINE_VERSION = re.compile(r"^[0-9]+(\.[0-9]+)*$")

# `apk policy` prints one block per package:
#   <pkg> policy:
#     <version>:
#       <repo url>
#       ...
# possibly repeated for older versions still available; the first version
# line is the one apk would actually install (its own precedence order), so
# only that line is read per block.
POLICY_HEADER = re.compile(r"^(?P<pkg>\S+) policy:$")
POLICY_VERSION = re.compile(r"^  (?P<version>\S+):$")

# The leading numeric-dotted run of an apk version, e.g. "2.6" out of
# "2.6.1-r5" or "10.3" out of "10.3_p1-r1": apk's own version grammar hangs
# a `-r<revision>` (package build number) and, on some packages, a
# `_p<patch>` off the end of the upstream version, and neither belongs in
# this repository's `~=` pins, which have always named the upstream version
# only. Stopping at the first character that is not a digit or a dot drops
# both without having to know each package's own suffix vocabulary.
VERSION_CORE = re.compile(r"^\d+(?:\.\d+)*")

# A Dockerfile `ARG NAME=value` default. Anchored end to end, and requiring a
# value, so a bare `ARG NAME` (a build argument with no default, which this
# script has nothing to resolve for) is left alone rather than rewritten into
# one that suddenly has a default.
ARG_LINE = re.compile(r"^ARG (?P<name>[A-Z0-9_]+)=(?P<value>\S+)$")


def parse_apk_policy(output: str) -> dict[str, str]:
    """Map package name to its first (installable) full apk version."""
    versions: dict[str, str] = {}
    pkg = None
    for line in output.splitlines():
        header = POLICY_HEADER.match(line)
        if header:
            pkg = header.group("pkg")
            continue
        if pkg is None or pkg in versions:
            continue
        version = POLICY_VERSION.match(line)
        if version:
            versions[pkg] = version.group("version")
    return versions


def match_precision(full_version: str, current_pin: str) -> str:
    """Truncate `full_version` to the component count `current_pin` has.

    `"2.6.1-r5"` matched against a two-component current pin (`"2.6"`)
    becomes `"2.6"`; `"702-r0"` matched against a one-component pin
    (`"702"`) stays `"702"`. `apk policy` output that somehow carries fewer
    components than the current pin (an Alpine release that dropped a
    version segment) is returned as-is, short rather than padded, so a
    genuine shape change is visible in the diff instead of being silently
    stretched to fit.
    """
    core = VERSION_CORE.match(full_version)
    if not core:
        return full_version
    components = core.group(0).split(".")
    want = max(current_pin.count(".") + 1, 1)
    return ".".join(components[:want])


def read_current_pins(dockerfile: Path) -> dict[str, str]:
    """Read the seven apk-pinned ARGs' current defaults from the Dockerfile."""
    current: dict[str, str] = {}
    wanted = set(PACKAGE_TO_VAR.values())
    for line in dockerfile.read_text().splitlines():
        match = ARG_LINE.match(line)
        if match and match.group("name") in wanted:
            current[match.group("name")] = match.group("value")
    missing = wanted - current.keys()
    if missing:
        raise SystemExit(
            f"{dockerfile}: missing expected ARG(s): {', '.join(sorted(missing))}"
        )
    return current


def resolve_versions(alpine_version: str) -> dict[str, str]:
    """Run `apk update && apk policy <pkgs>` inside `alpine:<version>`."""
    if not SAFE_ALPINE_VERSION.match(alpine_version):
        raise SystemExit(
            f"refusing to use {alpine_version!r} as a Docker tag: "
            "expected digits and dots only"
        )
    packages = sorted(PACKAGE_TO_VAR)
    script = "apk update >&2 && apk policy " + " ".join(packages)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            f"alpine:{alpine_version}",
            "sh",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"apk policy inside alpine:{alpine_version} failed "
            f"(exit {result.returncode})"
        )
    return parse_apk_policy(result.stdout)


def apply(dockerfile: Path, resolved: dict[str, str]) -> list[str]:
    """Rewrite `dockerfile` with `resolved` values; return a change summary."""
    current = read_current_pins(dockerfile)
    changes = []
    new_values: dict[str, str] = {}
    for pkg, var in sorted(PACKAGE_TO_VAR.items()):
        full = resolved.get(pkg)
        if full is None:
            raise SystemExit(f"apk policy reported nothing for package {pkg!r}")
        new_value = match_precision(full, current[var])
        new_values[var] = new_value
        if new_value != current[var]:
            changes.append(f"{var}: {current[var]} -> {new_value}")

    if changes:
        lines = dockerfile.read_text().splitlines(keepends=True)
        rewritten = []
        for line in lines:
            match = ARG_LINE.match(line.rstrip("\n"))
            if match and match.group("name") in new_values:
                name = match.group("name")
                newline = "\n" if line.endswith("\n") else ""
                rewritten.append(f"ARG {name}={new_values[name]}{newline}")
            else:
                rewritten.append(line)
        dockerfile.write_text("".join(rewritten))

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--alpine-version",
        required=True,
        help="Alpine release to resolve the apk pins against, e.g. 3.24",
    )
    parser.add_argument(
        "--dockerfile",
        type=Path,
        default=DOCKERFILE,
        help="Path to the Dockerfile to rewrite (default: repo root's copy)",
    )
    args = parser.parse_args()

    resolved = resolve_versions(args.alpine_version)
    changes = apply(args.dockerfile, resolved)

    if changes:
        print("changed=true")
        print(f"Resolved apk pins for alpine:{args.alpine_version}:")
        for change in changes:
            print(f"  {change}")
    else:
        print("changed=false")
        print(f"apk pins already match alpine:{args.alpine_version}; nothing to do.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
