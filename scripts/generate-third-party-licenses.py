#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ivan Pinatti
"""Generate `THIRD_PARTY_LICENSES.md`'s package inventory from a built image.

The image bundles Alpine's own compiled packages, each under its own upstream
licence, and `rsync` is GPLv3, which obliges whoever redistributes the binary
to point at the corresponding source. That obligation is met by linking
Alpine's `aports` tree at the exact commit that built each package, so the
inventory has to name a version and a commit per package to be worth
anything at all.

Which is exactly what made the hand-maintained version rot. Alpine bumps a
package's `-rN` revision without changing the upstream version, and the
`Dockerfile` runs `apk update && apk upgrade` before installing anything, so
every row can move on a rebuild that changes no file in this repository. A
file full of exact revisions with nothing regenerating it drifts silently
while still reading as a statement about the current image (see issue #78).

Everything the table needs is already inside the built image, in apk's own
installed database (`/lib/apk/db/installed`), one stanza per package:

    P:musl                                          package name
    V:1.2.6-r2                                      exact version, -rN included
    L:MIT                                           licence, from the APKBUILD
    o:musl                                          origin, the aports subdir
    c:f5640d3a10f664c9119720c60515265d3d6f6d01      aports commit that built it

That `c:` field is the aports commit, which is what the source links pin, so
no scraping of pkgs.alpinelinux.org is needed to build a permalink. The one
thing the installed database does not record is whether a package came from
`main` or `community`, which the URL path needs; `apk policy` reports that,
so the collection step runs both and joins them on the package name.

Two modes, because generating and enforcing are different jobs:

    python3 scripts/generate-third-party-licenses.py --image local/gocryptfs:1.0.0
    python3 scripts/generate-third-party-licenses.py --image ... --check

`--check` regenerates in memory and exits non-zero if the committed file
differs, which is what gives regeneration a trigger rather than leaving it to
whoever remembers.

The Docker-shelling half (`collect`) is kept separate from the pure parsing
and rendering half (`parse_installed_db`, `parse_apk_policy`, `render`) so the
latter is unit testable against captured transcripts with no Docker daemon and
no network at all; see tests/test_generate_third_party_licenses.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSES_FILE = REPO_ROOT / "THIRD_PARTY_LICENSES.md"

# The generated region of THIRD_PARTY_LICENSES.md. Everything outside these
# markers is prose a person wrote and this script never touches; everything
# between them is rewritten wholesale on every run.
BEGIN_MARKER = "<!-- BEGIN GENERATED INVENTORY -->"
END_MARKER = "<!-- END GENERATED INVENTORY -->"

# Alpine's aports lives on GitLab, and a link is pinned to the commit that
# built the listed revision rather than to `<version>-stable`, which keeps
# moving as fixes are backported into it.
APORTS_TREE = "https://gitlab.alpinelinux.org/alpine/aports/-/tree"

# `apk policy` prints one source line per repository a version is available
# from, plus this pseudo-source for the version currently installed. It is
# how the installed version is told apart from any newer candidate.
INSTALLED_SOURCE = "lib/apk/db/installed"

# The repository component of an Alpine mirror URL, as `apk policy` prints it:
# https://dl-cdn.alpinelinux.org/alpine/v3.24/community -> community
REPO_URL_RE = re.compile(r"^https?://\S+/(?P<repo>main|community|testing)/?$")


@dataclass(frozen=True)
class Package:
    """One installed apk package, as the inventory table lists it."""

    name: str
    version: str
    license: str
    origin: str
    commit: str

    def source_url(self, repo: str) -> str:
        return f"{APORTS_TREE}/{self.commit}/{repo}/{self.origin}"


class GenerationError(RuntimeError):
    """Something the caller has to fix, reported instead of guessed around."""


# `--check` exit statuses. Drift gets its own, distinct from the 1 a
# GenerationError exits with, because the two need opposite handling and a
# caller cannot tell them apart from a bare non-zero: drift is the expected
# finding this tool exists to report, while a failed `apk update` or an
# unreadable image is a broken run whose "the inventory has moved" conclusion
# is worthless. third-party-licenses-audit.yml branches on exactly this.
DRIFT_EXIT = 2
ERROR_EXIT = 1


# The note that opens the generated region. A template at module scope rather
# than a block built inside `render`, so the Markdown below reads exactly as it
# is written to the file, with no source indentation to strip and no
# implicitly concatenated fragments to miscount a comma in.
PROVENANCE_NOTE = """\
> **Generated on {generated_on} from an image built on Alpine {alpine_release}.**
> Every row below is a dated observation, not a standing fact: Alpine bumps a
> package's `-rN` revision without changing its upstream version, and `Dockerfile`
> runs `apk update && apk upgrade` before installing anything, so a rebuild that
> changes no file in this repository can still move these versions. Regenerate
> with `make third-party-licenses`. The licences themselves do not move with a
> revision, and each source link stays valid for the revision it names."""


def parse_installed_db(text: str) -> list[Package]:
    """Parse apk's installed database into packages, sorted by name.

    Stanzas are separated by a blank line and each field is a single
    `<letter>:<value>` line. Only the five fields the table needs are read;
    the rest (file lists, checksums, dependencies) are ignored.

    A stanza missing any of those five is an error rather than a row with a
    hole in it: an inventory that silently omits a licence or a source link
    is worse than no inventory, because it still reads as complete.
    """
    packages: list[Package] = []
    for stanza in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in stanza.splitlines():
            key, separator, value = line.partition(":")
            if separator and len(key) == 1:
                # First occurrence wins: 'R:' and 'a:' repeat per file, and
                # none of the five fields read here ever appears twice.
                fields.setdefault(key, value)
        if "P" not in fields:
            continue
        # Emptiness counts as missing. `L:` with nothing after it parses fine
        # and would render a blank licence cell; `c:` empty would render a
        # source link pointing at the aports tree root. Both read as complete
        # rows, which is the failure this refuses.
        missing = [key for key in ("V", "L", "o", "c") if not fields.get(key)]
        if missing:
            raise GenerationError(
                f"package {fields['P']} has no {', '.join(missing)} field in apk's "
                "installed database, so its version, licence, origin or aports "
                "commit cannot be determined"
            )
        packages.append(
            Package(
                name=fields["P"],
                version=fields["V"],
                license=fields["L"],
                origin=fields["o"],
                commit=fields["c"],
            )
        )
    if not packages:
        raise GenerationError("apk's installed database listed no packages at all")
    return sorted(packages, key=lambda package: package.name)


def parse_apk_policy(text: str) -> dict[str, str]:
    """Map each package name to the Alpine repository its installed version came from.

    `apk policy` prints a block per package, a line per available version, and
    an indented source per version:

        gocryptfs policy:
          2.6.1-r6:
            lib/apk/db/installed
            https://dl-cdn.alpinelinux.org/alpine/v3.24/community

    Only the repository name is taken from this, never a version: the version
    and the aports commit both come from the installed database, which is the
    only source that describes what is actually in the image.

    The installed version's own block is preferred, but it routinely has no
    repository URL under it at all. Alpine publishes a new `-rN` and the
    installed revision stops being an available candidate, leaving the block
    with nothing but the installed pseudo-source; a `fuse-common` one revision
    behind does exactly this. Any other block for the same package answers the
    question just as well, because `main` versus `community` is a property of
    the package rather than of a revision, so a later block is used as a
    fallback. A package with no repository URL anywhere is a real failure and
    `render_table` refuses it rather than emitting a row with a broken link.
    """
    installed: dict[str, str] = {}
    fallback: dict[str, str] = {}
    package: str | None = None
    installed_version = False
    for line in text.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped.endswith(" policy:"):
            package = stripped.removesuffix(" policy:")
            installed_version = False
        elif indent == 2 and stripped.endswith(":"):
            installed_version = False
        elif indent >= 4 and package is not None:
            if stripped == INSTALLED_SOURCE:
                installed_version = True
                continue
            match = REPO_URL_RE.match(stripped)
            if not match:
                continue
            if installed_version:
                installed.setdefault(package, match["repo"])
            else:
                fallback.setdefault(package, match["repo"])
    # `installed` wins on the right of the merge, so a block that did name a
    # repository against the installed version overrides any fallback.
    return fallback | installed


def render_table(packages: list[Package], repositories: dict[str, str]) -> str:
    """Render the package table, columns padded the way Prettier formats them.

    Prettier reformats Markdown tables on every commit through
    `.pre-commit-config.yaml`, so emitting anything else would mean the
    generator and the formatter fought each other and `--check` failed on a
    freshly generated file.
    """
    headers = ("Package", "Version", "License", "Source")
    rows = []
    for package in packages:
        repo = repositories.get(package.name)
        if repo is None:
            raise GenerationError(
                f"`apk policy` reported no Alpine repository for the installed "
                f"{package.name}-{package.version}, so its aports source link "
                "cannot be built. Run `apk update` in the image before "
                "collecting, so the repository indexes are present."
            )
        rows.append(
            (
                package.name,
                package.version,
                package.license,
                f"<{package.source_url(repo)}>",
            )
        )

    widths = [
        max(len(header), *(len(row[column]) for row in rows))
        for column, header in enumerate(headers)
    ]
    lines = [
        "| "
        + " | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
        + " |",
        "| " + " | ".join("-" * w for w in widths) + " |",
    ]
    lines += [
        "| "
        + " | ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True))
        + " |"
        for row in rows
    ]
    return "\n".join(lines)


def render(
    packages: list[Package],
    repositories: dict[str, str],
    alpine_release: str,
    generated_on: str,
) -> str:
    """Render the whole generated region, provenance note included.

    The provenance is the point of the note, not decoration. Without it the
    table reads as a statement about the image someone is running today, when
    it is a snapshot of the one that was built when it was generated.
    """
    return "\n".join(
        [
            BEGIN_MARKER,
            "",
            PROVENANCE_NOTE.format(
                generated_on=generated_on, alpine_release=alpine_release
            ),
            "",
            f"{len(packages)} packages:",
            "",
            render_table(packages, repositories),
            "",
            END_MARKER,
        ]
    )


def splice(document: str, generated: str) -> str:
    """Replace the marked region of the document, leaving the prose alone."""
    start = document.find(BEGIN_MARKER)
    end = document.find(END_MARKER)
    if start == -1 or end == -1 or end < start:
        raise GenerationError(
            f"{LICENSES_FILE.name} has no `{BEGIN_MARKER}` ... `{END_MARKER}` "
            "region to write into"
        )
    return document[:start] + generated + document[end + len(END_MARKER) :]


def _run_in_image(image: str, script: str) -> str:
    """Run a shell snippet inside the image as root, returning its stdout."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "root",
            "--entrypoint",
            "sh",
            image,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GenerationError(
            f"reading package metadata out of {image} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


# Separator between the three payloads the collection step reads in one
# container run, chosen so it cannot occur in apk's own output.
SECTION = "===rsync-crypt-section==="


def collect(image: str) -> tuple[str, str, str]:
    """Read the installed database, `apk policy` output and release from an image.

    One container run rather than three: `apk update` has to happen first (a
    freshly built image has no repository indexes, since `Dockerfile` removes
    the apk cache, and `apk policy` without an index reports no repository at
    all while still exiting 0), and re-running it per query would triple the
    network round trips for no gain.
    """
    script = (
        "set -e; "
        "apk update >/dev/null; "
        "cat /etc/alpine-release; "
        f"echo '{SECTION}'; "
        "cat /lib/apk/db/installed; "
        f"echo '{SECTION}'; "
        "apk policy $(apk info)"
    )
    output = _run_in_image(image, script)
    parts = output.split(f"{SECTION}\n")
    if len(parts) != 3:
        raise GenerationError(
            f"expected three sections from {image}, got {len(parts)}; the "
            "collection script did not run to completion"
        )
    alpine_release, installed_db, policy = parts
    return installed_db, policy, alpine_release.strip()


GENERATED_ON_RE = re.compile(r"\*\*Generated on (?P<date>\d{4}-\d{2}-\d{2}) from")


def _committed_date(document: str) -> str | None:
    """Read the generation date already in the file, for `--check` to reuse."""
    match = GENERATED_ON_RE.search(document)
    return match["date"] if match else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate THIRD_PARTY_LICENSES.md's package inventory from a "
            "locally built image."
        )
    )
    parser.add_argument(
        "--image",
        required=True,
        help=(
            "image reference to read package metadata from, e.g. local/gocryptfs:1.0.0"
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "do not write; exit 1 if the committed file differs from what this "
            "run would generate"
        ),
    )
    args = parser.parse_args(argv)

    try:
        installed_db, policy, alpine_release = collect(args.image)
        packages = parse_installed_db(installed_db)
        repositories = parse_apk_policy(policy)
        document = LICENSES_FILE.read_text(encoding="utf-8")
        # --check compares against the date already committed, so that a file
        # whose packages have not moved does not fail merely for having been
        # generated on an earlier day.
        generated_on = (
            _committed_date(document) or ""
            if args.check
            else dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
        )
        if args.check and not generated_on:
            raise GenerationError(
                f"{LICENSES_FILE.name} carries no generation date to check against"
            )
        updated = splice(
            document, render(packages, repositories, alpine_release, generated_on)
        )
    except GenerationError as error:
        print(f"error: {error}", file=sys.stderr)
        return ERROR_EXIT

    if args.check:
        if updated == document:
            print(f"{LICENSES_FILE.name} is up to date ({len(packages)} packages).")
            return 0
        # The diff goes to stdout, not stderr, because it is the useful half
        # of the answer: third-party-licenses-audit.yml puts it straight into
        # the tracking issue so the drift is readable without rebuilding
        # anything, and a person running this by hand wants to see what moved
        # before regenerating.
        sys.stdout.writelines(
            difflib.unified_diff(
                document.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{LICENSES_FILE.name}",
                tofile=f"b/{LICENSES_FILE.name}",
            )
        )
        print(
            f"error: {LICENSES_FILE.name} does not match the packages in "
            f"{args.image}. Run `make third-party-licenses` and commit the result.",
            file=sys.stderr,
        )
        return DRIFT_EXIT

    if updated == document:
        print(f"{LICENSES_FILE.name} already up to date ({len(packages)} packages).")
        return 0
    LICENSES_FILE.write_text(updated, encoding="utf-8")
    print(f"{LICENSES_FILE.name} regenerated ({len(packages)} packages).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
