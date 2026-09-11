#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ivan Pinatti
"""Refuse a unified diff that changes anything but a dependency pin.

Read a diff on stdin (`gh pr diff <n> | scripts/assert-pin-only-diff.py`) and
exit non-zero unless every changed file is one of the pin surfaces below and
every changed line differs from its counterpart in nothing but a version.

Ported from docker-torrent-box-with-vpn's script of the same name, which is
the check that stands between "renovate[bot] opened a pull request" and an
unattended merge there. It exists here for the same reason,
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

# The pin surfaces Renovate, the only dependency bot here, actually touches in
# this repository. Its native `github-actions` and `pre-commit` managers cover
# `.github/workflows/` (Action SHAs) and `.pre-commit-config.yaml` (hook
# `rev:` pins); Dependabot managed those same two files from a separate set of
# pin positions until its `updates:` configuration was retired (see
# .github/renovate.json5). Renovate's `asdf` manager covers `.tool-versions`,
# and, inside the `Dockerfile`, only the `ARG` lines its custom regex manager
# is anchored to (see below and .github/renovate.json5) come from Renovate
# too. The `Dockerfile` also carries seven apk `~=` pins that no bot manages:
# .github/workflows/resolve-apk-pins.yml bumps those instead, on the lines its
# own `# apk-pin:` marker is anchored to (see below), and this script grades
# that automated commit the same way it grades a bot's. Nothing touches
# `tests/requirements.txt`: no enabled manager here reads pip requirements
# files, so a pip pin bump is not a real bot-authored shape here and is
# deliberately left off this list.
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
# repository (the dependency bot updates it that way; a trailing `# v7`
# comment is left as ordinary text and not touched here). The negative
# lookahead stops a
# 40 character prefix of a longer hex run from matching and silently
# swallowing the character that would have made the shapes differ.
#
# Case-insensitive (`[0-9a-fA-F]`, not `[0-9a-f]`): GitHub resolves a
# `uses:` SHA the same way regardless of case, so an uppercase or
# mixed-case SHA is just as real a pin as a lowercase one, and matching
# only lowercase left a gap a CodeRabbit review of BARE_ACTION_VERSION
# below found: an uppercase SHA on a first-time pin's new side fell
# through ACTION_SHA entirely and was accepted by BARE_ACTION_VERSION's
# generic RELEASE grammar instead, which does not check that a
# first-time pin's target is SHA-shaped at all.
#
# Anchored to a genuine `uses:` field at the start of the line, the same
# anchor BARE_ACTION_VERSION uses, rather than a bare `@<sha>` matched
# anywhere: a follow-up CodeRabbit finding on this exact pattern pointed
# out the original, unanchored ACTION_SHA matched a 40 character hex run
# on ANY changed workflow line, `run:` step content included, so a `run:`
# command could change while its normalized form stayed equal, as long as
# the line still ended in something SHA-shaped. `prefix` now captures the
# full `uses: owner/repo@` text, not only `@`, so the dependency name
# stays literal to the left exactly as it already did.
ACTION_SHA = re.compile(
    r"(?P<prefix>^(?:[ \t]*-[ \t]+)?[ \t]*uses:[ \t]+[\w.-]+/[\w./-]+@)"
    r"[0-9a-fA-F]{40}(?![0-9a-fA-F])"
)

# A first-time `pinDigests` bump on a GitHub Action changes
# `uses: actions/checkout@v7` to `uses: actions/checkout@<sha>` in one step:
# there is no prior SHA to compare against. ACTION_SHA above normalizes the
# pinned side to `@<version>`; this pattern gives the unpinned side the
# identical placeholder, so the two sides of a first-time pin compare equal
# the same way an ordinary SHA-to-SHA bump does.
#
# Requires a `uses:` field and an owner/repo-shaped coordinate immediately
# before the `@`, not bare `@RELEASE` anywhere on the line: an unscoped
# version would let a `run:` step's own `tool@v7` normalize the same way,
# so that line could grow an unrelated-looking SHA and still read as a
# first-time pin. Ported from docker-torrent-box-with-vpn's script of the
# same name, where a CodeRabbit review found exactly that gap in an earlier,
# unscoped version of this pattern.
#
# `uses:` alone is not narrow enough either, as a follow-up CodeRabbit
# finding on this exact pattern went on to show: `\buses:` is a
# word-boundary check, not a position check, so it matches the substring
# "uses:" anywhere a line contains it, including inside a `run:` step's own
# text (`run: uses: actions/checkout@v7` normalized the same way a real
# `uses:` line did). Anchored to the start of the line instead, with only
# an optional YAML list marker (`- `) and indentation in front of `uses:`,
# which is the only place a real `uses:` field can sit.
#
# Applying ACTION_SHA first, ahead of this one, is what keeps the two from
# double matching: RELEASE's character class is wide enough to also accept a
# 40 character hex run as a "version", so an already-pinned line would match
# this pattern too if it still carried its SHA. ACTION_SHA already replaces
# that SHA with `<version>` by the time this pattern runs, and `<version>`
# itself does not start with a digit or a bare `v`, so RELEASE cannot match
# it a second time.
#
# The version is its own capture group, `bare_version`, rather than folded
# unnamed into the match, because a third CodeRabbit-class finding (found by
# extending their own test, not reported directly) showed RELEASE alone is
# still too permissive here: `_normalize_bare_action_version` below refuses
# a 40 character match outright, real hex or not, because 40 characters is
# the shape ACTION_SHA exists to own exclusively. Without that check, a
# non-hex 40 character token, `0` followed by 39 `z`s for instance, never
# matches ACTION_SHA (not hex) and was accepted here instead, since nothing
# about this pattern's own grammar checked that the "version" replacing a
# first-time pin's bare tag was ever a real SHA at all, only that it was
# RELEASE-shaped. A real first-time pin's target is always exactly a 40
# character SHA, ACTION_SHA's exclusive domain, so anything that length
# reaching this pattern instead is already suspect, and refusing it outright
# costs nothing: a length that long never occurs in a genuine bare release
# tag either.
BARE_ACTION_VERSION = re.compile(
    r"(?P<action_prefix>^(?:[ \t]*-[ \t]+)?[ \t]*uses:[ \t]+[\w.-]+/[\w./-]+)@"
    r"(?P<bare_version>" + RELEASE + r")$"
)


def _normalize_bare_action_version(match: re.Match[str]) -> str:
    if len(match.group("bare_version")) == 40:
        return match.group(0)
    return f"{match.group('action_prefix')}@<version>"


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


# A YAML block scalar opener: `key: |`, `key: >`, or a bare sequence item
# whose own value is the scalar (`- |`, `- >-`), with the optional
# chomping (`-`/`+`) and explicit indentation (a digit) modifiers the spec
# allows, in either order (`|2-` and `|-2` are both valid YAML), and an
# optional trailing comment after them. Everything indented more than a
# line matching this, until a line at or below its own indentation
# appears, is that block scalar's literal content, not further YAML
# structure: a `run: |` step body is the shape that matters here, since
# its content can coincidentally read exactly like a `uses:` field. A
# CodeRabbit review found and confirmed this: an indented `uses:
# owner/action@<sha> # v7` inside a run: | block matched ACTION_SHA and
# BARE_ACTION_VERSION alike, treating shell text as if it were a real
# GitHub Actions step, which a required check reading `Pin Only` then
# approves. A later review round found the first regex here only matched
# one modifier order and no trailing comment, so `run: |2-  # step body`
# or `run: |-2` opened a block scalar this check could not recognize as
# one. A further review found it still missed a standalone sequence-item
# scalar header, `- |` with no `key:` in front at all, since the pattern
# required a colon before the scalar indicator; confirmed exploitable the
# same way, a `uses:` line nested under one read as ordinary YAML
# structure instead of a block scalar's literal content. The Dockerfile
# has no YAML block scalars, so this only ever matters for the
# ACTION_SHA/BARE_ACTION_VERSION/REV_PIN branch below, never the ARG
# branch.
BLOCK_SCALAR_OPENER = re.compile(
    r"(?::|^[ \t]*-)\s*[|>](?:[+-][1-9]?|[1-9][+-]?)?(?:[ \t]+#.*)?\s*$"
)


def _line_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _in_block_scalar(context: list[str], indent: int) -> bool:
    """Judge, from the lines already seen in this file's diff, whether
    `indent` sits inside an open YAML block scalar.

    Scans backward for the nearest line indented less than `indent`,
    skipping blank lines (a block scalar can itself contain one, and its
    zero indentation must not be mistaken for the boundary that closes the
    scalar). Inside a block scalar if that nearer line opens one.
    Conservatively also inside one if no such line is visible at all: the
    diff is all this script ever sees of the file around a change, so a
    block scalar whose own opening line sits outside the diff's context
    cannot be told apart from one that was never open, and refusing the
    line as a candidate pin either way is the fail closed direction, the
    same one every other shape in this file takes when it cannot be sure.

    A CodeRabbit review named the residual gap in this precisely: the
    first shallower line found is trusted as the boundary even when it is
    itself ordinary scalar content one level further out, rather than the
    real opener sitting deeper in the scan, so a `uses:` line nested under
    something like an `if` inside a `run: |` block, both indented past the
    block's own floor, is not caught. Scanning past a shallower non-opener
    line to keep looking, rather than trusting it as decisive, would close
    that gap, but was tried and reverted: it also requires reaching the
    file's own top level (indentation zero) before a real diff's limited
    context ever earns a confident "not inside one", and no ordinary `gh
    pr diff` output carries that much. Verified against
    docker-torrent-box-with-vpn's own #178, whose real diff never contains
    the change's enclosing indentation chain down to indentation zero: the
    deeper version refused it outright, the same result a compromised
    bot's diff should get, not a clean one. This narrower version is the
    one actually deployed; the nested case above is an accepted,
    documented gap rather than a silently unfixed one.
    """
    for seen in reversed(context):
        if not seen.strip():
            continue
        if _line_indent(seen) < indent:
            return bool(BLOCK_SCALAR_OPENER.search(seen))
    return True


def normalize(line: str, path: str = "", in_block_scalar: bool = False) -> str:
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
    # Scoped to .github/workflows/: a block scalar (run: |) is a GitHub
    # Actions workflow construct, not something .pre-commit-config.yaml's
    # schema has, so gating that file by it too would only cost real
    # first-time pins their context there for no matching risk.
    if in_block_scalar and path.startswith(".github/workflows/"):
        return line
    line = ACTION_SHA.sub(r"\g<prefix><version>", line)
    line = BARE_ACTION_VERSION.sub(_normalize_bare_action_version, line)
    line = REV_PIN.sub(r"\g<prefix><version>", line)
    return line


def parse(diff: str) -> tuple[dict[str, tuple[Counter, Counter]], list[str]]:
    """Group removed and added lines by file, and collect structural changes."""
    changes: dict[str, tuple[Counter, Counter]] = {}
    structural: list[str] = []
    path = None
    in_hunk = False
    # The lines of each side of this file seen so far in the current hunk,
    # in file order: what a block scalar check has to work with, since the
    # diff never carries the whole file. Kept separate because a hunk can
    # add or remove a block scalar's own opening line, which changes
    # whether a later line on just one side is inside one. Reset on every
    # hunk header, not only every file header: a hunk boundary means the
    # diff skips lines in between, and a line just past the gap could
    # otherwise be judged against context from before it, a shallower line
    # left over from the previous hunk that is not actually the nearest
    # one to the real file. Kept context from the file's earlier hunks
    # cannot be trusted to still be the true boundary once the diff has
    # jumped past lines neither side of this comparison ever saw; starting
    # each hunk with nothing visible falls back to the same fail closed
    # default `_in_block_scalar` already takes when a file's first hunk
    # opens with no context at all.
    old_context: list[str] = []
    new_context: list[str] = []

    for line in diff.splitlines():
        header = FILE_HEADER.match(line)
        if header:
            old, new = header.group("old"), header.group("new")
            path = new
            in_hunk = False
            old_context = []
            new_context = []
            changes.setdefault(path, (Counter(), Counter()))
            if old != new:
                structural.append(f"{old} renamed to {new}")
            continue

        if line.startswith("@@"):
            in_hunk = True
            old_context = []
            new_context = []
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
            content = line[1:]
            in_scalar = _in_block_scalar(old_context, _line_indent(content))
            changes[path][0][normalize(content, path, in_scalar)] += 1
            old_context.append(content)
        elif line.startswith("+"):
            content = line[1:]
            in_scalar = _in_block_scalar(new_context, _line_indent(content))
            changes[path][1][normalize(content, path, in_scalar)] += 1
            new_context.append(content)
        elif line.startswith(" ") or line == "":
            # An unchanged context line: not compared itself, but part of
            # the surrounding structure a block scalar check on a later
            # line in this file needs to see.
            content = line[1:] if line else line
            old_context.append(content)
            new_context.append(content)

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
