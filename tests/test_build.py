"""Checks on the image that 'make build' produces."""

from __future__ import annotations

import pytest

from conftest import run

# Every binary the scripts check for at startup, plus the ones view mode needs.
REQUIRED_BINARIES = ["gocryptfs", "rsync", "sshfs", "fusermount", "ssh", "sshd"]


@pytest.mark.parametrize("binary", REQUIRED_BINARIES)
def test_required_binary_is_present(image, binary):
    result = run(
        ["docker", "run", "--rm", "--entrypoint", "/bin/bash", image, "-c", f"command -v {binary}"]
    )
    assert result.returncode == 0, f"{binary} is missing from the image"


def test_entrypoint_is_gocryptfs(image):
    result = run(["docker", "inspect", "-f", "{{json .Config.Entrypoint}}", image])
    assert result.returncode == 0, result.stderr
    assert "gocryptfs" in result.stdout


def test_image_runs_as_the_crypt_user(image):
    result = run(["docker", "inspect", "-f", "{{.Config.User}}", image])
    assert result.stdout.strip() == "crypt"


def test_gocryptfs_version_matches_the_pin(image, build_env_file):
    """The installed build should satisfy the GOCRYPTFS_VERSION constraint."""
    pinned = ""
    for line in build_env_file.read_text().splitlines():
        if line.startswith("GOCRYPTFS_VERSION="):
            pinned = line.split("=", 1)[1].strip().strip('"')
    assert pinned, "GOCRYPTFS_VERSION missing from the build env file"

    result = run(
        ["docker", "run", "--rm", "--entrypoint", "/usr/bin/gocryptfs", image, "-version"]
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
        ["docker", "run", "--rm", "--entrypoint", "/usr/bin/gocryptfs", image, "-bs", "4096", "-version"]
    )
    assert result.returncode != 0, "-bs is now accepted; revisit the note in CLAUDE.md"
