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

import hashlib
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
# `.devcontainer/Dockerfile` is listed separately from `Dockerfile` on
# purpose: the check below is `path.startswith(ALLOWED_PATHS)`, and
# `.devcontainer/Dockerfile` does not start with `Dockerfile`, so the root
# entry never covered it. Renovate has been watching its base image digest
# since the development container landed, and every bump was refused as
# "not a dependency pin file" until this entry existed.
ALLOWED_PATHS = (
    "Dockerfile",
    ".pre-commit-config.yaml",
    ".github/workflows/",
    ".tool-versions",
    ".devcontainer/Dockerfile",
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
# repository (the dependency bot updates it that way), optionally followed
# by a trailing release comment (`# v7`, `# v4.38.0`), which the bot
# rewrites on the same bump whenever the tag it resolves the SHA from
# changes.
#
# Both have to normalize together, and this script did not do that until
# now: it normalized only the SHA and left the comment as ordinary text, so
# an ordinary bump that also moved `# v4.37.9` to `# v4.38.0` compared as
# `@<version> # v4.37.9` against `@<version> # v4.38.0`, read as a
# structural change, and `Pin Only` refused it. Since every Renovate action
# bump rewrites that comment, no action SHA bump could ever be approved
# here: the only four pull requests ever merged unattended in this
# repository were three `.tool-versions` bumps and one
# `.pre-commit-config.yaml` rev bump, and #105 is the one that finally
# surfaced it. github-template,
# pre-commit-checklists and pre-commit-checklists-demo have carried the
# fix below for some time; this copy had simply never received it, and its
# own tests pinned `# v7` on both sides of the bump, so nothing caught the
# shape that actually occurs.
#
# The comment is folded into the same placeholder only when it is a release
# token, so a change to unrelated trailing text after the SHA is still
# caught as structural. The negative lookahead stops a
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
    r"(?P<comment>[ \t]+#[ \t]*" + RELEASE + r")?"
)


def _normalize_action_pin(match: re.Match[str]) -> str:
    """Collapse a `@<sha>` pin and its optional trailing release comment."""
    normalized = f"{match.group('prefix')}<version>"
    if match.group("comment"):
        normalized += " # <version>"
    return normalized


# A first-time `pinDigests` bump on a GitHub Action changes
# `uses: actions/checkout@v7` to `uses: actions/checkout@<sha> # v7` in one
# step: there is no prior SHA to compare against, and the trailing release
# comment appears for the first time alongside it. ACTION_SHA above
# normalizes the pinned side to `@<version> # <version>` whenever a comment
# trails the SHA, which every first-time pin does in practice; this pattern
# gives the unpinned side the identical placeholder, so the two sides of a
# first-time pin compare equal the same way an ordinary SHA-to-SHA bump
# does. A first-time pin arriving with no comment at all still reads as
# structural and gets refused, which is the fail closed outcome for a shape
# that does not occur on a clean bump.
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
    # `# <version>` is appended here too, matching what _normalize_action_pin
    # produces for the pinned side: a first-time pin gains its release
    # comment in the same edit that gains the SHA, so the placeholder has to
    # carry one for the two sides to compare equal.
    if len(match.group("bare_version")) == 40:
        return match.group(0)
    return f"{match.group('action_prefix')}@<version> # <version>"


# The development container base image, pinned by digest in
# `.devcontainer/Dockerfile` as `ARG BASE_IMAGE=<image>@sha256:<64 hex>`.
#
# Distinct from the ARG_LINE and ARG_VALUE pair below, which grade the root
# Dockerfile's annotated pins by ARG name against the annotations read off
# main's own copy of that file. This one grades a digest instead, and only
# the digest becomes a placeholder: the image reference to the left of the
# `@` stays literal, so a bump that also pointed the ARG at a different
# image or registry reads as a structural change and is refused, the same
# way a swapped owner is for a `uses:` pin.
IMAGE_DIGEST = re.compile(
    r"(?P<prefix>^ARG [A-Z0-9_]+=[\w./-]+(?::[\w.-]+)?@)sha256:[0-9a-f]{64}$"
)

FILE_HEADER = re.compile(r"^diff --git a/(?P<old>.+) b/(?P<new>.+)$")
# The blob ids a diff's preamble names for each side, and a hunk's starting
# line and length on each side (a length of one is written by omitting it).
INDEX_LINE = re.compile(
    r"^index (?P<old>[0-9a-f]{7,64})\.\.[0-9a-f]{7,64}(?: [0-7]{6})?$"
)
HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_len>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))? @@"
)

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
#
# YAML also allows node properties, an anchor (`&body`) and a tag (`!!str`,
# `!local`), between the colon or dash and the indicator, in either order:
# `run: &body |2-` and `run: !!str |-` open a block scalar just as `run: |`
# does. A CodeRabbit review found the pattern missed them, which let a
# `uses:` shaped line inside an anchored or tagged `run:` body read as a
# real step. Confirmed with PyYAML before the fix.
BLOCK_SCALAR_OPENER = re.compile(
    r"(?::|^[ \t]*-)(?:[ \t]+[&!]\S*)*\s*[|>](?:[+-][1-9]?|[1-9][+-]?)?"
    r"(?:[ \t]+#.*)?\s*$"
)
# The sequence markers leading a line, each a dash followed by whitespace,
# and the node properties (anchors, tags) that may lead a node after one.
SEQUENCE_MARKER = re.compile(r"-[ \t]+")
NODE_PROPERTIES = re.compile(r"(?:[&!]\S*[ \t]+)*")


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
    one actually deployed for a diff it cannot see past.

    It is now the fallback rather than the rule. When the file's base side
    can be read whole and proven to be the diff's own base, which is the
    ordinary case, `_whole_file_block_scalars` below decides instead, from
    every line of the file rather than from three lines of context, and
    that also closes the nested gap described above.
    """
    for seen in reversed(context):
        if not seen.strip():
            continue
        if _line_indent(seen) < indent:
            return bool(BLOCK_SCALAR_OPENER.search(seen))
    return True


def _block_scalar_floor(line: str) -> int:
    """The indentation a block scalar opened on `line` has to exceed.

    For `key: |` that is the key's own column. After a sequence marker,
    `- name: |`, it is still the key's column rather than the dash's: YAML
    reads a line starting at that column as the key's sibling, not as
    scalar content (confirmed with PyYAML), so a step's `uses:` beside a
    `- name: |` is ordinary structure. Only when the sequence item itself
    is the scalar, `- |` or `- &body |`, is the dash the floor. Properties
    leading a compact mapping, `- &step name: |`, belong to the mapping,
    which starts where they do, so they are skipped before deciding.
    """
    column = _line_indent(line)
    rest = line[column:]
    while True:
        marker = SEQUENCE_MARKER.match(rest)
        if not marker:
            return column
        after = rest[marker.end() :]
        value = after[NODE_PROPERTIES.match(after).end() :]
        if not value or value[0] in "|>":
            return column
        column += marker.end()
        rest = after


def _block_scalar_lines(lines: list[str]) -> list[bool]:
    """Mark every line of a whole YAML file as inside a block scalar or not.

    Walks the file top to bottom. After a line opening a block scalar,
    every following line that is blank or indented deeper than the opener
    is that scalar's literal content, until a non-blank line at or below
    the opener's own indentation closes it. Content is never read as an
    opener itself, which is what the diff context version above cannot
    guarantee: a `uses:` nested under an `if` inside a `run: |` block is
    content here, however deep. Anything that merely looks like an opener
    (a comment ending in `: |`, say) marks what follows as content, which
    only ever refuses more, never less.
    """
    marks: list[bool] = []
    floor: int | None = None
    for line in lines:
        if floor is not None:
            if not line.strip() or _line_indent(line) > floor:
                marks.append(True)
                continue
            floor = None
        marks.append(False)
        if BLOCK_SCALAR_OPENER.search(line):
            floor = _block_scalar_floor(line)
    return marks


def _git_blob_id(data: bytes) -> str:
    # git's own object id, recomputed to compare against the diff's `index`
    # line. It identifies a file version rather than guarding one: what
    # actually holds `_apply_hunks` honest is that every context and removed
    # line must match the base, which no hash collision can fake.
    header = b"blob %d\0" % len(data)
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def _base_lines(path: str, blob: str) -> list[str] | None:
    """The file's base side read from the checkout, or None if it is not
    provably the same file the diff was taken against.

    coderabbit-gate.yml checks out the default branch, never the pull
    request's head, so this reads the file as it stands on main. That is
    the diff's own base only while main has not moved the file since the
    pull request branched, which is what comparing the git blob id against
    the diff's `index` line proves. If main has moved it, or the path
    leaves the checkout, the answer is None and the caller falls back to
    judging from the diff's context.
    """
    root = REPO_ROOT.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    data = target.read_bytes()
    if not _git_blob_id(data).startswith(blob):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _apply_hunks(
    base: list[str], hunks: list[tuple[re.Match[str], list[str]]]
) -> list[str] | None:
    """Rebuild the file's head side from its base and the diff's hunks.

    Every context and removed line has to match the base where the hunk
    header says it sits, every hunk has to land where its header says it
    does on the head side too, and each hunk body has to carry exactly the
    line counts its header declares on both sides. Any disagreement means
    the diff and the base are not describing the same file, or the diff
    was cut short, and the answer is None.
    """
    head: list[str] = []
    cursor = 0
    for header, body in hunks:
        old_len = int(header.group("old_len") or 1)
        new_len = int(header.group("new_len") or 1)
        start = int(header.group("old_start")) - (1 if old_len else 0)
        if start < cursor:
            return None
        head.extend(base[cursor:start])
        if len(head) != int(header.group("new_start")) - (1 if new_len else 0):
            return None
        position = start
        added = 0
        for line in body:
            tag, content = (line[:1], line[1:]) if line else (" ", "")
            if tag in (" ", "-"):
                if position >= len(base) or base[position] != content:
                    return None
                position += 1
                if tag == " ":
                    head.append(content)
                    added += 1
            elif tag == "+":
                head.append(content)
                added += 1
            elif tag != "\\":
                return None
        if position - start != old_len or added != new_len:
            return None
        cursor = position
    head.extend(base[cursor:])
    return head


def _whole_file_block_scalars(
    diff_lines: list[str],
) -> dict[str, tuple[list[bool], list[bool]]]:
    """Block scalar marks for each side of every workflow file whose base
    can be read whole and proven to be the diff's own.

    Keyed by path; each value holds the base side's marks and the head
    side's, indexed by line number minus one. A file missing from the
    result is judged by `_in_block_scalar` from the diff alone, exactly as
    before this existed. Only `.github/workflows/` is read, the one place
    `normalize` consults the answer.
    """
    files: dict[str, tuple[str | None, list[tuple[re.Match[str], list[str]]]]] = {}
    path = None
    for line in diff_lines:
        header = FILE_HEADER.match(line)
        if header:
            same = header.group("old") == header.group("new")
            path = header.group("new") if same else None
            if path is not None:
                files[path] = (None, [])
            continue
        if path is None:
            continue
        blob, hunks = files[path]
        hunk = HUNK_HEADER.match(line)
        if hunk:
            hunks.append((hunk, []))
        elif hunks:
            hunks[-1][1].append(line)
        else:
            index = INDEX_LINE.match(line)
            if index:
                files[path] = (index.group("old"), hunks)

    marks: dict[str, tuple[list[bool], list[bool]]] = {}
    for path, (blob, hunks) in files.items():
        if not path.startswith(".github/workflows/") or not blob or not hunks:
            continue
        if not blob.strip("0"):
            continue
        base = _base_lines(path, blob)
        if base is None:
            continue
        head = _apply_hunks(base, hunks)
        if head is None:
            continue
        marks[path] = (_block_scalar_lines(base), _block_scalar_lines(head))
    return marks


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
    # Exact equality, like the root Dockerfile branch above: this grades the
    # development container's base image digest and nothing else, so another
    # Dockerfile added under .devcontainer/ later would be read raw rather
    # than against this grammar.
    if path == ".devcontainer/Dockerfile":
        return IMAGE_DIGEST.sub(r"\g<prefix><digest>", line)
    line = ACTION_SHA.sub(_normalize_action_pin, line)
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
    # Whole-file marks where the base could be proven, and each side's
    # current line number (zero based) to look a line up in them by.
    diff_lines = diff.splitlines()
    whole_file = _whole_file_block_scalars(diff_lines)
    marks = None
    old_number = new_number = 0

    for line in diff_lines:
        header = FILE_HEADER.match(line)
        if header:
            old, new = header.group("old"), header.group("new")
            path = new
            in_hunk = False
            old_context = []
            new_context = []
            marks = whole_file.get(path) if old == new else None
            changes.setdefault(path, (Counter(), Counter()))
            if old != new:
                structural.append(f"{old} renamed to {new}")
            continue

        if line.startswith("@@"):
            in_hunk = True
            old_context = []
            new_context = []
            hunk = HUNK_HEADER.match(line)
            if hunk:
                old_number = int(hunk.group("old_start")) - 1
                new_number = int(hunk.group("new_start")) - 1
            else:
                marks = None
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
            in_scalar = (
                marks[0][old_number]
                if marks
                else _in_block_scalar(old_context, _line_indent(content))
            )
            changes[path][0][normalize(content, path, in_scalar)] += 1
            old_context.append(content)
            old_number += 1
        elif line.startswith("+"):
            content = line[1:]
            in_scalar = (
                marks[1][new_number]
                if marks
                else _in_block_scalar(new_context, _line_indent(content))
            )
            changes[path][1][normalize(content, path, in_scalar)] += 1
            new_context.append(content)
            new_number += 1
        elif line.startswith(" ") or line == "":
            # An unchanged context line: not compared itself, but part of
            # the surrounding structure a block scalar check on a later
            # line in this file needs to see.
            content = line[1:] if line else line
            old_context.append(content)
            new_context.append(content)
            old_number += 1
            new_number += 1

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
