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
# Renovate manages `.tool-versions` (the asdf manager) and, inside
# `.env.example`, only the lines its custom regex manager is anchored to (see
# below and .github/renovate.json5). Neither bot touches `tests/requirements.txt`:
# dependabot.yml enables only the github-actions and pre-commit ecosystems, so
# a pip pin bump is not a real bot-authored shape here and is deliberately
# left off this list.
ALLOWED_PATHS = (
    ".env.example",
    ".pre-commit-config.yaml",
    ".github/workflows/",
    ".tool-versions",
)

# `.tool-versions` writes `<tool> <version>`, one per line, with nothing to
# anchor on but the space. That cannot go in the prefix set below, because a
# lookbehind of variable width is not allowed and "the word after a space"
# would match most of a workflow file. It is matched whole-line instead, and
# only for that file, which is why normalize() takes the path.
TOOL_VERSION_LINE = re.compile(r"^(?P<prefix>[A-Za-z0-9_.-]+[ \t]+)\S+[ \t]*$")

# A version-shaped token that sits where a pin sits, and nowhere else. The
# prefix is what makes this narrow: matching any number on the line would
# accept `RSYNC_RATE_LIMIT=0` becoming `RSYNC_RATE_LIMIT=999999999`, or a
# `timeout-minutes:` changing, since both sides would normalize alike.
#
#   @v1.2.3              a GitHub Actions ref, or what is left after a SHA
#   rev: v1.2.3           a pre-commit hook revision
#
# The prefix is captured and put back, so that a pin changing shape rather
# than value, `@v7` becoming `@main`, still reads as a difference (`main` does
# not match the token shape required after it, since the token has to start
# with an alphanumeric and the comparison is line for line either way).
#
# `.env.example` is handled separately below rather than through this prefix
# set, because a bare `VAR=value` prefix would also match GOCRYPTFS_VERSION
# and DOCKER_IMAGE_TAG_VERSION, and CLAUDE.md and .env.example's own comments
# both say those two are bumped by hand and are deliberately not tracked by
# Renovate. Widening this regex to reach them would let a compromised
# Renovate touch a value it has no business touching and still read as
# pin-only.
VERSION = re.compile(r"(?P<prefix>@|\brev:[ \t]+)[0-9A-Za-z][0-9A-Za-z.+_-]*")

FILE_HEADER = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")

# The exact shape .github/renovate.json5's custom regex manager is anchored
# to: a `# renovate: datasource=... depName=...` comment immediately above the
# `VAR="value"` line it annotates. Read live off this checkout's own
# `.env.example` (the base branch's copy, since the workflow that runs this
# script checks that out rather than the pull request's) so the allowed
# variable set can never drift from what Renovate is actually configured to
# manage, and so a bot's pull request cannot smuggle in its own annotation
# comment to widen what it is allowed to touch: the set is fixed by main's
# copy of the file, not by the diff being graded.
ANNOTATION = re.compile(r"^#\s*renovate:")
ENV_VAR_LINE = re.compile(r"^(?P<name>[A-Z0-9_]+)=")


def annotated_env_vars(path: Path = REPO_ROOT / ".env.example") -> frozenset[str]:
    """Return the variable names Renovate's custom regex manager may bump."""
    names = set()
    previous = ""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        # No .env.example to read is not this script's problem to solve; an
        # empty set simply means nothing in the file is treated as a pin,
        # which fails closed rather than open.
        return frozenset()
    for line in lines:
        var = ENV_VAR_LINE.match(line)
        if var and ANNOTATION.match(previous):
            names.add(var.group("name"))
        previous = line
    return frozenset(names)


ANNOTATED_ENV_VARS = annotated_env_vars()


def normalize(line: str, path: str = "") -> str:
    """Reduce a line to everything about it that a version bump may not change."""
    if path.endswith(".tool-versions"):
        return TOOL_VERSION_LINE.sub(r"\g<prefix><version>", line)
    if path.endswith(".env.example"):
        var = ENV_VAR_LINE.match(line)
        if var and var.group("name") in ANNOTATED_ENV_VARS:
            return re.sub(r'="[^"]*"', '="<version>"', line)
        # Not an annotated variable: returned unchanged, so any edit to it,
        # version-shaped or not, shows up as a structural mismatch instead of
        # being waved through.
        return line
    return VERSION.sub(r"\g<prefix><version>", line)


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
