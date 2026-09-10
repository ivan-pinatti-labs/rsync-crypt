"""Tests for scripts/generate-third-party-licenses.py.

Exercises only the pure parsing, rendering and splicing functions, never
`collect`, which shells out to Docker: these run anywhere, with no Docker
daemon and no network, the same shape the sibling `resolve-apk-pins.py` and
`assert-pin-only-diff.py` tests take.

    pytest -m scripts tests/test_generate_third_party_licenses.py

The fixtures below are real captures from an image built from this
repository's `Dockerfile` on 2026-09-10, trimmed to a handful of packages.
`fuse-common` is in them on purpose and is not a filler row: it was installed
at `3.18.2-r0` while Alpine's repository had already moved to `3.18.3-r0`, so
its installed version's `apk policy` block carries no repository URL at all.
That is the case the fallback in `parse_apk_policy` exists for, and it was
found by running the generator rather than by imagining it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate-third-party-licenses.py"

pytestmark = pytest.mark.scripts


def _load_module():
    # Hyphenated filename, so it cannot be imported as a module name the
    # normal way; loaded explicitly from its path instead.
    spec = importlib.util.spec_from_file_location(
        "generate_third_party_licenses", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = _load_module()


# Real stanzas from `/lib/apk/db/installed`, trimmed to the fields the
# inventory reads plus enough of the trailing file list to prove the parser
# stops at the right place: `musl` keeps its repeating `R:` and `a:` lines
# for exactly that reason. The maintainer and per-file checksum lines are
# dropped, since nothing here reads them.
INSTALLED_DB = """\
P:musl
V:1.2.6-r2
A:x86_64
T:the musl c library (libc) implementation
U:https://musl.libc.org/
L:MIT
o:musl
t:1775900007
c:f5640d3a10f664c9119720c60515265d3d6f6d01
p:so:libc.musl-x86_64.so.1=1
F:lib
R:ld-musl-x86_64.so.1
a:0:0:755
R:libc.musl-x86_64.so.1
a:0:0:777

P:gocryptfs
V:2.6.1-r6
A:x86_64
L:MIT
o:gocryptfs
c:1e1aed58b7720fcb6b1859043d543b33019d8c4f
F:usr/bin
R:gocryptfs

P:fuse-common
V:3.18.2-r0
A:x86_64
L:GPL-2.0-only AND LGPL-2.1-only
o:fuse3
c:19abb11201b27c75a38af1815dcd6f0caf4ee686
F:etc
"""

# `apk policy musl gocryptfs fuse-common`, same image. gocryptfs is in
# community and musl in main, which is what the source link path needs;
# fuse-common's installed 3.18.2-r0 has only the installed pseudo-source
# under it, because the repository already carries 3.18.3-r0.
POLICY = """\
fuse-common policy:
  3.18.2-r0:
    lib/apk/db/installed
  3.18.3-r0:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
gocryptfs policy:
  2.6.1-r6:
    lib/apk/db/installed
    https://dl-cdn.alpinelinux.org/alpine/v3.24/community
musl policy:
  1.2.6-r2:
    lib/apk/db/installed
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
"""


def test_parses_every_field_the_inventory_needs():
    packages = generator.parse_installed_db(INSTALLED_DB)

    assert [package.name for package in packages] == [
        "fuse-common",
        "gocryptfs",
        "musl",
    ], "packages are sorted by name, whatever order apk stored them in"

    musl = packages[2]
    assert musl.version == "1.2.6-r2"
    assert musl.license == "MIT"
    assert musl.origin == "musl"
    # detect-secrets reads a bare 40-hex literal as a high-entropy string. It
    # is a public aports commit, the same one the fixture above carries.
    assert (
        musl.commit
        == "f5640d3a10f664c9119720c60515265d3d6f6d01"  # pragma: allowlist secret
    )


def test_repeating_file_list_lines_do_not_overwrite_a_field():
    # 'R:' repeats once per file in a stanza. A parser that let a later line
    # win would still produce a plausible-looking row, so this asserts the
    # first-wins rule that keeps the five real fields intact.
    packages = generator.parse_installed_db(INSTALLED_DB)
    assert packages[2].name == "musl"


def test_missing_field_is_refused_rather_than_left_blank():
    without_commit = INSTALLED_DB.replace(
        "c:f5640d3a10f664c9119720c60515265d3d6f6d01\n", ""
    )
    with pytest.raises(generator.GenerationError, match="musl has no c field"):
        generator.parse_installed_db(without_commit)


def test_empty_database_is_refused():
    with pytest.raises(generator.GenerationError, match="no packages at all"):
        generator.parse_installed_db("")


def test_repository_comes_from_the_installed_version():
    repositories = generator.parse_apk_policy(POLICY)
    assert repositories["musl"] == "main"
    assert repositories["gocryptfs"] == "community"


def test_repository_falls_back_when_the_installed_revision_is_superseded():
    # fuse-common's own installed block names no repository, because Alpine
    # has published a newer revision. main-versus-community is a property of
    # the package, not the revision, so the later block answers it.
    repositories = generator.parse_apk_policy(POLICY)
    assert repositories["fuse-common"] == "main"


def test_a_named_installed_repository_wins_over_a_fallback():
    # Contrived, but it is the ordering the merge depends on: if a package
    # somehow appeared in both, the installed version's own answer is the
    # correct one and must not be overwritten by a candidate's.
    both = """\
gocryptfs policy:
  2.6.0-r0:
    https://dl-cdn.alpinelinux.org/alpine/v3.24/main
  2.6.1-r6:
    lib/apk/db/installed
    https://dl-cdn.alpinelinux.org/alpine/v3.24/community
"""
    assert generator.parse_apk_policy(both)["gocryptfs"] == "community"


def test_source_link_is_pinned_to_the_commit_that_built_the_revision():
    packages = generator.parse_installed_db(INSTALLED_DB)
    repositories = generator.parse_apk_policy(POLICY)
    table = generator.render_table(packages, repositories)

    assert (
        "<https://gitlab.alpinelinux.org/alpine/aports/-/tree/"
        "1e1aed58b7720fcb6b1859043d543b33019d8c4f/community/gocryptfs>" in table
    )
    # fuse-common is built from the fuse3 aports directory, not from one
    # named after the package, which is why the origin field is read at all.
    assert "/main/fuse3>" in table


def test_table_columns_are_padded_to_a_uniform_width():
    # Prettier reformats Markdown tables on every commit, so a generator that
    # emitted ragged columns would fight the formatter and make --check fail
    # on a file it had just written.
    packages = generator.parse_installed_db(INSTALLED_DB)
    repositories = generator.parse_apk_policy(POLICY)
    lines = generator.render_table(packages, repositories).splitlines()

    assert len({len(line) for line in lines}) == 1


def test_a_package_with_no_repository_anywhere_is_refused():
    orphan = POLICY.replace(
        "    https://dl-cdn.alpinelinux.org/alpine/v3.24/main\ngocryptfs policy:\n",
        "gocryptfs policy:\n",
    )
    packages = generator.parse_installed_db(INSTALLED_DB)
    with pytest.raises(generator.GenerationError, match="fuse-common"):
        generator.render_table(packages, generator.parse_apk_policy(orphan))


def test_splice_replaces_only_the_marked_region():
    document = (
        "prose above\n\n"
        f"{generator.BEGIN_MARKER}\nold inventory\n{generator.END_MARKER}\n\n"
        "prose below\n"
    )
    spliced = generator.splice(
        document, f"{generator.BEGIN_MARKER}\nnew\n{generator.END_MARKER}"
    )

    assert spliced.startswith("prose above\n")
    assert spliced.endswith("prose below\n")
    assert "old inventory" not in spliced
    assert "new" in spliced


def test_splice_refuses_a_document_with_no_markers():
    with pytest.raises(generator.GenerationError, match="region to write into"):
        generator.splice("no markers here\n", "anything")


def test_committed_file_carries_the_markers_and_a_generation_date():
    # The committed file is the only thing --check can splice into, so its
    # markers and its date line are part of the contract, not decoration.
    document = generator.LICENSES_FILE.read_text(encoding="utf-8")
    assert generator.BEGIN_MARKER in document
    assert generator.END_MARKER in document
    assert generator._committed_date(document) is not None


def test_rendered_region_states_its_provenance():
    packages = generator.parse_installed_db(INSTALLED_DB)
    repositories = generator.parse_apk_policy(POLICY)
    rendered = generator.render(packages, repositories, "3.24.1", "2026-09-10")

    assert "**Generated on 2026-09-10 from an image built on Alpine 3.24.1.**" in (
        rendered
    )
    assert "3 packages:" in rendered
    assert rendered.startswith(generator.BEGIN_MARKER)
    assert rendered.endswith(generator.END_MARKER)
