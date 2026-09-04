"""Checks on the image that 'make build' produces."""

from __future__ import annotations

import fnmatch

import pytest
from conftest import REPO_ROOT, read_dockerfile_arg, run

# Every binary the scripts check for at startup, plus the ones view mode needs.
REQUIRED_BINARIES = ["gocryptfs", "rsync", "sshfs", "fusermount", "ssh", "sshd"]


def test_dockerfile_only_copies_tracked_files():
    """Every COPY source must be in git, or the build breaks on a fresh clone.

    This caught a real failure: files/ssh/ is gitignored, so 'COPY files/ssh/*'
    only ever worked on a machine that happened to have the directory left
    over locally. Anyone cloning the repository hit
    'lstat /files/ssh: no such file or directory'.
    """
    tracked = run(["git", "ls-files"]).stdout.split()

    unresolved = []
    for line in (REPO_ROOT / "Dockerfile").read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY "):
            continue
        # COPY [--flag ...] <src>... <dest>
        parts = [p for p in stripped.split()[1:] if not p.startswith("--")]
        for source in parts[:-1]:
            if not any(fnmatch.fnmatch(path, source) for path in tracked):
                unresolved.append(source)

    assert not unresolved, f"Dockerfile COPY sources not tracked by git: {unresolved}"


@pytest.mark.parametrize(
    ("arg", "annotation"),
    [
        ("ALPINE_VERSION", "# renovate: datasource=docker depName=alpine"),
        ("GOCRYPTFS_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
        ("BASH_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
        ("LESS_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
        ("OPENSSH_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
        ("RSYNC_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
        ("SSHFS_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
        ("VIM_VERSION", "# apk-pin: resolved-from=ALPINE_VERSION"),
    ],
)
def test_every_version_arg_has_a_default_and_its_annotation(arg, annotation):
    """Each pin carries a default value and the marker its owner reads.

    Three separate mechanisms are anchored to these exact two lines: Renovate's
    custom regex manager (`# renovate:` above `ARG ALPINE_VERSION=`),
    scripts/resolve-apk-pins.py (which rewrites the seven `# apk-pin:` ones)
    and scripts/assert-pin-only-diff.py (which will only grade a bump to an
    annotated ARG as a pin bump). All three fail silently and open if an
    annotation is dropped or an ARG loses its default: Renovate simply stops
    proposing Alpine bumps, and the `Pin Only` gate simply stops recognizing a
    legitimate one. Nothing else in the suite would notice.
    """
    lines = (REPO_ROOT / "Dockerfile").read_text().splitlines()
    declarations = [i for i, line in enumerate(lines) if line.startswith(f"ARG {arg}=")]
    assert len(declarations) == 1, f"expected exactly one 'ARG {arg}=' line"

    index = declarations[0]
    value = lines[index].split("=", 1)[1]
    assert value, f"ARG {arg} has no default value"
    assert lines[index - 1].startswith(annotation), (
        f"ARG {arg} is not preceded by '{annotation}'"
    )


@pytest.mark.parametrize("binary", REQUIRED_BINARIES)
def test_required_binary_is_present(image, binary):
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/bash",
            image,
            "-c",
            f"command -v {binary}",
        ]
    )
    assert result.returncode == 0, f"{binary} is missing from the image"


def test_entrypoint_is_gocryptfs(image):
    result = run(["docker", "inspect", "-f", "{{json .Config.Entrypoint}}", image])
    assert result.returncode == 0, result.stderr
    assert "gocryptfs" in result.stdout


def test_image_runs_as_the_crypt_user(image):
    """USER is the numeric uid (hadolint DL3066), which must still be crypt.

    Asserting on the number alone would pass even if the account behind it
    changed, so this also resolves the id inside the image.
    """
    result = run(["docker", "inspect", "-f", "{{.Config.User}}", image])
    assert result.stdout.strip() == "1000"

    resolved = run(["docker", "run", "--rm", "--entrypoint", "id", image, "-un"])
    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.strip() == "crypt"


def test_gocryptfs_version_matches_the_pin(image):
    """The installed build should satisfy the GOCRYPTFS_VERSION constraint.

    Read from the Dockerfile's own `ARG GOCRYPTFS_VERSION=` default, which is
    where the pin lives and what an unadorned 'make build' actually applies.
    It used to be read back out of the env file the build was driven with,
    which stopped being a check on anything once the env file no longer names
    it: the fixture would have had to supply the value first, and asserting an
    image matches a pin the test itself passed in proves nothing.
    """
    pinned = read_dockerfile_arg("GOCRYPTFS_VERSION")
    assert pinned, "GOCRYPTFS_VERSION has no default in the Dockerfile"

    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/usr/bin/gocryptfs",
            image,
            "-version",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"gocryptfs v{pinned}" in result.stdout


def test_block_size_flag_is_not_supported(image):
    """Guards the documented gotcha that this build rejects -bs.

    CLAUDE.md records that the Alpine package does not accept the block size
    flag and that it must not be reintroduced. If a future bump starts
    accepting it, this test fails and the note can be revisited.
    """
    result = run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/usr/bin/gocryptfs",
            image,
            "-bs",
            "4096",
            "-version",
        ]
    )
    assert result.returncode != 0, "-bs is now accepted; revisit the note in CLAUDE.md"
