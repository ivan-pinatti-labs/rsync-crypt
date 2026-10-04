"""Keep `make coverage` measuring every shell script, found rather than listed.

The Makefile's SHELL_SOURCES used to be a hand written list, which is how a
new script slips past the 100% shell coverage bar: nothing fails when nobody
adds it. It is now discovered (see the comment above SHELL_SOURCES), and this
file holds that in place three ways:

- the same rule, reimplemented here, finds the scripts this repository is
  known to have and nothing under tests/, which runs anywhere, including a
  copy of the tree with no .git and no git;
- the Makefile still carries the discovery rule, so a hand list cannot creep
  back in its place;
- where git and make are both available, `make print-shell-scripts` names
  exactly what the rule here finds, and the `coverage` pre-push hook's
  `files:` pattern matches every one of them, so editing any discovered
  script triggers the hook;
- SonarQube's shell analyzer is told about every measured file that has no
  .sh or .bash extension, since it would skip it otherwise.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
SONAR_PROPERTIES = REPO_ROOT / "sonar-project.properties"

# No container and no stack state, so this runs without Docker as well,
# inside `make coverage`'s Python container too, where there is no .git.
pytestmark = pytest.mark.scripts

# The scripts the image runs. A floor, not the whole set: a new script is
# found without touching this file.
KNOWN_SCRIPTS = {"scripts/backup.sh", "scripts/restore.sh", "scripts/view.sh"}
# Shell that neither its name nor a shebang identifies. Mirrors SHELL_EXTRA.
KNOWN_EXTRA = {"files/bash/.bashrc", "files/bash/.bash_aliases"}

SHELL_NAME = re.compile(r"\.(sh|bash)$")
# The Makefile's awk pattern, in Python: any interpreter path, `env` with or
# without options, then sh, bash or dash as a whole word.
SHELL_SHEBANG = re.compile(rb"^#!\s*(\S*/)?(env\s+(-\S+\s+)*)?(ba|da)?sh(\s|$)")


def _candidates(root: Path) -> list[str]:
    """Files git would commit, or every file when there is no git to ask.

    The coverage container gets the tree as a tar of exactly those files and
    has neither .git nor git, so walking it gives the same set there.
    """
    git = shutil.which("git")
    if git and (root / ".git").exists():
        out = subprocess.run(
            [git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            check=True,
            capture_output=True,
        ).stdout
        return [p for p in out.decode().split("\0") if p]
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            found.append((Path(dirpath) / name).relative_to(root).as_posix())
    return found


def _first_line(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.readline()


def discover(root: Path) -> set[str]:
    """The shell scripts SHELL_SOURCES discovers, before SHELL_EXTRA."""
    scripts = set()
    for rel in _candidates(root):
        path = root / rel
        # A path deleted in the working tree is still listed by git; the
        # Makefile drops it the same way, before awk sees it.
        if rel.startswith("tests/") or not path.is_file():
            continue
        if SHELL_NAME.search(rel) or SHELL_SHEBANG.match(_first_line(path)):
            scripts.add(rel)
    return scripts


def test_discovery_finds_the_known_scripts_and_no_tests():
    found = discover(REPO_ROOT)
    assert found >= KNOWN_SCRIPTS, f"not discovered: {KNOWN_SCRIPTS - found}"
    assert not [p for p in found if p.startswith("tests/")]


@pytest.mark.parametrize(
    ("first_line", "is_shell"),
    [
        (b"#!/bin/sh\n", True),
        (b"#!/bin/bash -e\n", True),
        (b"#!/usr/bin/dash\n", True),
        (b"#!/usr/bin/env bash\n", True),
        (b"#! /usr/bin/env -S bash -eu\n", True),
        (b"#!/bin/bashful\n", False),
        (b"#!/usr/bin/env python3\n", False),
        (b"#!/bin/zsh\n", False),
        (b"echo '#!/bin/sh'\n", False),
    ],
)
def test_shebang_rule(first_line, is_shell):
    assert bool(SHELL_SHEBANG.match(first_line)) is is_shell


def test_discovery_skips_tests_and_deleted_paths(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/helper").write_text("#!/bin/sh\n")
    (tmp_path / "tool").write_text("#!/usr/bin/env bash\n")
    (tmp_path / "lib.bash").write_text("f() { :; }\n")
    (tmp_path / "notes.txt").write_text("see #!/bin/sh\n")
    (tmp_path / "gone.sh").symlink_to(tmp_path / "missing")
    assert discover(tmp_path) == {"tool", "lib.bash"}


def test_makefile_discovers_rather_than_lists():
    text = MAKEFILE.read_text()
    match = re.search(r"^SHELL_SOURCES := (.*)$", text, re.MULTILINE)
    assert match, "SHELL_SOURCES is gone from the Makefile"
    rule = match.group(1)
    for piece in (
        "git ls-files -z --cached --others --exclude-standard",
        # mawk stops at the first file it cannot open, dropping the rest.
        '[ -f "$$f" ]',
        "$(filter-out $(SHELL_EXCLUDE)",
        "grep -v '^tests/'",
        "$(SHELL_EXTRA)",
        r"\.(sh|bash)$$",
    ):
        assert piece in rule, f"SHELL_SOURCES no longer carries {piece!r}"
    extra = re.search(r"^SHELL_EXTRA :=(.*)$", text, re.MULTILINE)
    assert extra, "SHELL_EXTRA is gone from the Makefile"
    assert set(extra.group(1).split()) == KNOWN_EXTRA
    for rel in KNOWN_EXTRA:
        assert (REPO_ROOT / rel).is_file(), f"SHELL_EXTRA names a missing {rel}"


def _coverage_hook_files() -> re.Pattern[str]:
    text = PRE_COMMIT_CONFIG.read_text()
    block = text.split("- id: coverage", 1)[1]
    match = re.search(r"^\s*files:\s*'([^']+)'", block, re.MULTILINE)
    assert match, "the coverage hook has no files: pattern"
    return re.compile(match.group(1))


def test_coverage_hook_runs_for_every_discovered_script():
    pattern = _coverage_hook_files()
    measured = discover(REPO_ROOT) | KNOWN_EXTRA
    missed = sorted(p for p in measured if not pattern.search(p))
    assert not missed, f"the coverage hook's files: pattern misses {missed}"


def test_sonar_analyzes_every_measured_script_without_an_extension():
    """SonarQube's shell analyzer only reads what its patterns name."""
    text = SONAR_PROPERTIES.read_text()
    match = re.search(r"^sonar\.lang\.patterns\.shell=(.*)$", text, re.MULTILINE)
    assert match, "sonar.lang.patterns.shell is gone"
    patterns = set(match.group(1).split(","))
    assert {"**/*.sh", "**/*.bash"} <= patterns
    bare = {p for p in discover(REPO_ROOT) | KNOWN_EXTRA if not SHELL_NAME.search(p)}
    assert bare <= patterns, f"not analyzed by SonarQube: {sorted(bare - patterns)}"


@pytest.mark.skipif(
    not (
        shutil.which("git") and shutil.which("make") and (REPO_ROOT / ".git").exists()
    ),
    reason="needs git, make and a checkout",
)
def test_makefile_measures_what_the_rule_finds():
    out = subprocess.run(
        ["make", "-s", "--no-print-directory", "print-shell-scripts"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert set(out.split()) == discover(REPO_ROOT) | KNOWN_EXTRA


def test_discovery_refuses_unsafe_script_names():
    """A script name reaches make's recipes as shell text, so discovery has to
    refuse any name outside [A-Za-z0-9._/+-] (a committed `x;id;#.sh` would
    otherwise run `id`)."""
    here = Path(__file__).resolve().parent
    while not (here / "Makefile").is_file():
        here = here.parent
    text = (here / "Makefile").read_text()
    assert "_shell_safe = $(if $(filter UNSAFE:," in text
    assert "$(call _shell_safe," in text
    assert '? FILENAME : "UNSAFE:")' in text
