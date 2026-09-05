#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ivan Pinatti
"""Refuse a unified diff that changes anything but a dependency pin.

Read a diff on stdin (`gh pr diff <n> | scripts/assert-pin-only-diff.py`) and
exit non-zero unless every changed file is one of the pin surfaces below and
every changed line differs from its counterpart in nothing but a version.

Ported from docker-torrent-box-with-vpn's script of the same name, which is
the check that stands between "renovate[bot] or dependabot[bot] opened a pull
request" and an unattended merge there. It exists here for the same reason,
ahead of ever needing it: approving a bot's pull request on the strength of
its author means the bot identity holds write access to main, and a diff that
is not actually pin-only is exactly the shape a compromised or misconfigured
bot would take. A path allowlist alone would not be much of a fence, since
`.github/workflows/` and `.pre-commit-config.yaml` are executable surfaces on
their own; the line comparison below is what makes it one.

The comparison normalizes both sides and requires them to match line for line
per file, duplicates counted. A line whose structure changed has no
counterpart and the diff is refused, which covers
`uses: actions/checkout@v7` becoming `uses: evil/checkout@v7` as much as it
covers an added `curl | sh`. Anything this refuses is not broken, it just
waits for a person: the approval is skipped and the pull request sits there,
which is the direction to fail in.

What it deliberately does not catch: a bump to a version that exists but is
malicious. `alpine:3.24` becoming `alpine:3.25` is the change this file
exists to permit, and no amount of diff reading can tell a good release from a
backdoored one.
"""

import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The pin surfaces a dependency bot actually touches in this repository.
# Dependabot manages `.github/workflows/` (Action SHAs) and
# `.pre-commit-config.yaml` (hook `rev:` pins), see .github/dependabot.yml.
# Renovate manages `.tool-versions` (the asdf manager) and, inside the
# `Dockerfile`, only the `ARG` lines its custom regex manager is anchored to
# (see below and .github/renovate.json5). The `Dockerfile` also carries seven
# apk `~=` pins that neither bot manages: .github/workflows/resolve-apk-pins.yml
# bumps those instead, on the lines its own `# apk-pin:` marker is anchored to
# (see below), and this script grades that automated commit the same way it
# grades a bot's. Neither bot touches `tests/requirements.txt`: dependabot.yml
# enables only the github-actions and pre-commit ecosystems, so a pip pin bump
# is not a real bot-authored shape here and is deliberately left off this list.
#
# `.env.example` used to be on this list and no longer is. Every annotated pin
# it carried moved into the `Dockerfile`; what is left in it is user
# configuration (paths, a remote host, an image tag) that no bot has ever
# bumped and none of these markers annotate. Leaving it listed would widen the
# allowlist to a file nothing here manages, which is the opposite of what an
# allowlist is for, so it came off along with the pins.
ALLOWED_PATHS = (
    "Dockerfile",
    ".pre-commit-config.yaml",
    ".github/workflows/",
    ".tool-versions",
)

# A released version, always starting with a digit (an optional single
# leading `v` aside): `2.2.2`, `v2.2.2`, `4.6.2`. Anchors both `rev:` in
# `.pre-commit-config.yaml` and every value in `.tool-versions`, and is
# deliberately narrower than "any tag-shaped token": a floating ref like
# `main` or `latest` is made entirely of characters this would otherwise
# accept, and normalizing it the same as a real release would let a
# compromised bot trade an immutable pin for something that can move under it
# after the diff is already merged, with nothing left in the diff to catch
# it.
RELEASE = r"v?[0-9][0-9A-Za-z.+_-]*"

# `.tool-versions` writes `<tool> <version>`, one per line, with nothing to
# anchor on but the space. That cannot go in the prefix set below, because a
# lookbehind of variable width is not allowed and "the word after a space"
# would match most of a workflow file. It is matched whole-line instead, and
# only for that file, which is why normalize() takes the path. The value
# after the space has to be a real release, not merely non-blank: `pre-commit
# main` would otherwise normalize identically to `pre-commit 4.6.2`.
TOOL_VERSION_LINE = re.compile(
    r"^(?P<prefix>[A-Za-z0-9_.-]+[ \t]+)" + RELEASE + r"[ \t]*$"
)

# A pre-commit hook `rev:`. The prefix is captured and put back, so that a
# pin changing shape rather than value still reads as a difference.
#
# GitHub Actions pins are handled separately below rather than through this
# same released-version grammar: this repository, like the one it was ported
# from, pins every action to a full commit SHA rather than a tag (see any
# `uses:` line in .github/workflows/), so the immutable shape to require
# there is a SHA, not a release number.
REV_PIN = re.compile(r"(?P<prefix>\brev:[ \t]+)" + RELEASE)

# A GitHub Actions pin, always a full 40 character commit SHA in this
# repository (dependabot updates it that way; a trailing `# v7` comment is
# left as ordinary text and not touched here). The negative lookahead stops a
# 40 character prefix of a longer hex run from matching and silently
# swallowing the character that would have made the shapes differ.
ACTION_SHA = re.compile(r"(?P<prefix>@)[0-9a-f]{40}(?![0-9a-fA-F])")

FILE_HEADER = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")

# The exact shape .github/renovate.json5's custom regex manager is anchored
# to: a `# renovate: datasource=... depName=...` comment immediately above the
# `ARG NAME=value` line it annotates.
RENOVATE_ANNOTATION = re.compile(r"^#\s*renovate:")

# The second, distinct marker: an ARG resolved automatically by
# .github/workflows/resolve-apk-pins.yml rather than by Renovate. These are
# apk `~=` version constraints (GOCRYPTFS_VERSION, BASH_VERSION,
# LESS_VERSION, OPENSSH_VERSION, RSYNC_VERSION, SSHFS_VERSION,
# VIM_VERSION), not Docker tags, so no Renovate datasource can track them
# independently; see the customManagers comment in .github/renovate.json5.
# Deliberately not `# renovate:` with a different datasource tacked on: that
# shape is exactly what Renovate's own regex would match, which would put
# these ARGs right back under Renovate's independent tracking, the
# failure mode they are excluded from Renovate to avoid in the first place.
# `resolved-from=ALPINE_VERSION` is fixed text, not a placeholder: every one
# of these seven ARGs is resolved from ALPINE_VERSION today, and a
# marker naming a different source variable would not match this pattern,
# so widening it to a genuinely different upstream later is a deliberate,
# reviewed change to this script rather than a silent grant.
APK_PIN_ANNOTATION = re.compile(r"^#\s*apk-pin:\s*resolved-from=ALPINE_VERSION\s*$")

DOCKERFILE = REPO_ROOT / "Dockerfile"

# A Dockerfile `ARG NAME=value` default line. The `ARG ` prefix is required
# rather than optional: it is what keeps this from matching a bare
# `NAME=value` line elsewhere in the file (inside a RUN, say) that no
# annotation above it was ever meant to cover.
ARG_LINE = re.compile(r"^ARG (?P<name>[A-Z0-9_]+)=(?P<value>\S*)$")
# The whole value has to be a real release on its own, anchored end to end by
# ARG_LINE's own `$`, not merely contain one: substituting on a partial match
# would let `ARG ALPINE_VERSION=3.24` becoming
# `ARG ALPINE_VERSION=$(payload)3.24` read as a clean version bump, since the
# trailing digits alone would satisfy an unanchored search.
ARG_VALUE = re.compile(r"(?P<prefix>^ARG [A-Z0-9_]+=)\S*$")


def _dockerfile_args_annotated_by(
    annotation: re.Pattern[str], path: Path = DOCKERFILE
) -> frozenset[str]:
    """Return the ARG names immediately preceded by `annotation`.

    Read live off this checkout's own `Dockerfile` (the base branch's copy,
    since the workflow that runs this script checks that out rather than the
    pull request's) so the allowed ARG set can never drift from what the
    annotation's owner is actually configured to manage, and so a bot's pull
    request cannot smuggle in its own annotation comment to widen what it is
    allowed to touch: the set is fixed by main's copy of the file, not by the
    diff being graded.
    """
    names = set()
    previous = ""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        # No Dockerfile to read is not this script's problem to solve; an
        # empty set simply means nothing in the file is treated as a pin,
        # which fails closed rather than open.
        return frozenset()
    for line in lines:
        arg = ARG_LINE.match(line)
        if arg and annotation.match(previous):
            names.add(arg.group("name"))
        previous = line
    return frozenset(names)


def renovate_annotated_args(path: Path = DOCKERFILE) -> frozenset[str]:
    """Return the ARG names Renovate's custom regex manager may bump."""
    return _dockerfile_args_annotated_by(RENOVATE_ANNOTATION, path)


def apk_pin_annotated_args(path: Path = DOCKERFILE) -> frozenset[str]:
    """Return the ARG names resolve-apk-pins.yml may bump."""
    return _dockerfile_args_annotated_by(APK_PIN_ANNOTATION, path)


RENOVATE_ANNOTATED_ARGS = renovate_annotated_args()
APK_PIN_ANNOTATED_ARGS = apk_pin_annotated_args()
# The two marker types are semantically distinct (one drives Renovate, the
# other drives resolve-apk-pins.yml) but grade identically here: either one
# is enough to let an `ARG NAME=value` line's value change count as a pin bump.
PIN_ELIGIBLE_ARGS = RENOVATE_ANNOTATED_ARGS | APK_PIN_ANNOTATED_ARGS


def normalize(line: str, path: str = "") -> str:
    """Reduce a line to everything about it that a version bump may not change."""
    if path.endswith(".tool-versions"):
        return TOOL_VERSION_LINE.sub(r"\g<prefix><version>", line)
    # Exact equality, not `endswith`: the eligible ARG names above are read
    # from the repository root's own Dockerfile, so they describe that file
    # and no other. A `sub/Dockerfile` added later would carry its own,
    # unrelated ARGs, and grading it against this file's annotations would be
    # looser than reading its lines raw. It cannot reach here today anyway,
    # since ALLOWED_PATHS refuses it, but the two checks should not have to
    # agree for this one to be safe.
    if path == "Dockerfile":
        arg = ARG_LINE.match(line)
        if (
            arg
            and arg.group("name") in PIN_ELIGIBLE_ARGS
            and re.fullmatch(RELEASE, arg.group("value"))
        ):
            return ARG_VALUE.sub(r"\g<prefix><version>", line)
        # Not an annotated ARG, or the value is not a release on its own:
        # returned unchanged either way, so any such edit shows up as a
        # structural mismatch instead of being waved through.
        return line
    line = ACTION_SHA.sub(r"\g<prefix><version>", line)
    line = REV_PIN.sub(r"\g<prefix><version>", line)
    return line


def parse(diff: str) -> tuple[dict[str, tuple[Counter, Counter]], list[str]]:
    """Group removed and added lines by file, and collect structural changes."""
    changes: dict[str, tuple[Counter, Counter]] = {}
    structural: list[str] = []
    path = None
    in_hunk = False

    for line in diff.splitlines():
        header = FILE_HEADER.match(line)
        if header:
            old, new = header.group("old"), header.group("new")
            path = new
            in_hunk = False
            changes.setdefault(path, (Counter(), Counter()))
            if old != new:
                structural.append(f"{old} renamed to {new}")
            continue

        if line.startswith("@@"):
            in_hunk = True
            continue

        # Everything between a file header and its first hunk is preamble: the
        # index line, the ---/+++ pair, and any mode line. Recognizing those
        # only here is what stops a content line impersonating one. Inside a
        # hunk, `+++foo` is an added line reading `++foo`, and skipping it as
        # a file header would drop it from the comparison, which fails open.
        if not in_hunk:
            if line.startswith(
                ("new file ", "deleted file ", "old mode ", "new mode ")
            ):
                structural.append(f"{path}: {line.strip()}")
            continue

        if path is None:
            continue

        if line.startswith("-"):
            changes[path][0][normalize(line[1:], path)] += 1
        elif line.startswith("+"):
            changes[path][1][normalize(line[1:], path)] += 1

    return changes, structural


def main() -> int:
    diff = sys.stdin.read()
    if not diff.strip():
        print("REFUSED: the diff is empty, so there is nothing to approve.")
        return 1

    changes, problems = parse(diff)

    # Output that parsed into nothing is not a clean bill of health. Truncated
    # output, a binary diff, or anything that arrives without a `diff --git`
    # header would otherwise leave the change set empty and read as "no
    # problems found", approving a diff nobody managed to read.
    if not changes:
        print("REFUSED: no file headers in the diff, so nothing could be checked.")
        return 1

    for path in changes:
        if not path.startswith(ALLOWED_PATHS):
            problems.append(f"{path}: not a dependency pin file")

    for path, (removed, added) in changes.items():
        if not removed and not added:
            problems.append(
                f"{path}: no readable changed lines, so nothing was checked"
            )

    for path, (removed, added) in changes.items():
        # Counter subtraction drops non-positive counts, so each direction has
        # to be asked separately to see both halves of a mismatch.
        for line in removed - added:
            problems.append(f"{path}: removed a line that was not re-added: -{line}")
        for line in added - removed:
            problems.append(
                f"{path}: added a line that was not a version bump: +{line}"
            )

    if problems:
        print("REFUSED: this diff changes more than dependency pins.")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nNothing is broken. The automated approval is skipped and the pull "
            "request waits for a person, which is what should happen when a "
            "dependency bot reaches outside its lane."
        )
        return 1

    files = ", ".join(sorted(changes)) or "nothing"
    print(f"Pin-only diff confirmed: {files}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
