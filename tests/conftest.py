"""Shared fixtures for the rsync-crypt test suite.

The integration tests drive the real Makefile targets against a throwaway sshd
container that stands in for the remote backup server. Everything the tests
touch lives under a pytest temporary directory, and the image is tagged
separately from the one a normal 'make build' produces, so running the suite
never disturbs a real backup or a locally built image.
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Tagged separately from the default local/gocryptfs so the suite cannot
# overwrite an image the user relies on.
IMAGE_NAME = "local/gocryptfs-test"
IMAGE_VERSION = "test"
IMAGE_REF = f"{IMAGE_NAME}:{IMAGE_VERSION}"

REMOTE_CONTAINER = "rsync-crypt-test-remote"
REMOTE_USER = "root"
REMOTE_BACKUP_DIR = "/srv/backup"

# The Makefile hardcodes this container name, so the targets cannot run
# concurrently. The suite is therefore serial by design.
MAKE_CONTAINER = "gocryptfs"

PASSPHRASE = "integration-test-passphrase"

# scrypt cost is deliberately low: the default of 16 adds seconds to every
# mount and the tests mount repeatedly. It does not change what is exercised.
SCRYPT_N = 10

# Resolved once at import time and reused at every call site, so every Docker
# invocation in the suite runs the same binary instead of each one resolving
# the bare name "docker" against PATH separately. None (rather than falling
# back to the string "docker") is deliberate: it is what lets require_docker
# below actually detect a missing binary instead of masking it.
DOCKER = shutil.which("docker")


def run(cmd, **kwargs):
    """Run a command and return the CompletedProcess, capturing output."""
    kwargs.setdefault("cwd", REPO_ROOT)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 600)
    return subprocess.run(cmd, check=False, **kwargs)


def run_make(target, env_file, timeout=900, extra_args=()):
    """Run a Makefile target with a pty on stdin.

    The backup, restore and view targets all invoke 'docker run --interactive
    --tty'. Docker refuses to start with "the input device is not a TTY" when
    stdin is a pipe, which is exactly what pytest and CI provide, so stdin has
    to be a real terminal. Only stdin needs to be a pty; stdout stays a pipe so
    the output can still be captured and asserted on.
    """
    cmd = ["make", target, f"ENV_FILE={env_file}", *extra_args]
    master, slave = pty.openpty()
    try:
        return subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        os.close(master)
        os.close(slave)


def docker_rm(name):
    """Remove a container if it exists, without relying on error suppression."""
    exists = run([DOCKER, "inspect", "--type", "container", name])
    if exists.returncode == 0:
        run([DOCKER, "rm", "--force", name])


@pytest.fixture(scope="session", autouse=True)
def require_docker():
    if DOCKER is None:
        pytest.skip("docker is not installed")
    info = run([DOCKER, "info"])
    if info.returncode != 0:
        pytest.skip("docker daemon is not reachable")


@pytest.fixture(scope="session")
def workspace(tmp_path_factory):
    """Source tree, ssh keys and passphrase file for the whole session."""
    root = tmp_path_factory.mktemp("rsync-crypt")

    src = root / "src"
    # Files that the filter rules should transfer.
    (src / "Documents" / "nested").mkdir(parents=True)
    (src / "Documents" / "notes.txt").write_text("hello world\n")
    (src / "Documents" / "nested" / "deep.txt").write_text("nested content\n")
    (src / ".config" / "Code" / "User").mkdir(parents=True)
    (src / ".config" / "Code" / "User" / "settings.json").write_text('{"a": 1}\n')

    # Files the filter rules should drop. The lock and cache patterns are
    # written as '**/...' in conf/backup-filter-rules.txt, so they are placed
    # in a subdirectory where that pattern applies. 'backup' is excluded by a
    # bare basename rule and so is checked at the top level.
    (src / "Documents" / "app.lock").write_text("lock\n")
    (src / "Documents" / "data.db-wal").write_text("wal\n")
    (src / "Documents" / ".cache").mkdir()
    (src / "Documents" / ".cache" / "junk.txt").write_text("junk\n")
    (src / "backup").mkdir()
    (src / "backup" / "stuff.txt").write_text("recursive\n")

    (root / "restore").mkdir()

    keys = root / "keys"
    keys.mkdir()
    key_file = keys / "id_ed25519"
    keygen = run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "rsync-crypt-test",
            "-f",
            str(key_file),
        ]
    )
    assert keygen.returncode == 0, keygen.stdout + keygen.stderr
    (keys / "known_hosts").touch()

    passkey = root / "passfile"
    passkey.write_text(PASSPHRASE)
    passkey.chmod(0o600)

    return {
        "root": root,
        "src": src,
        "restore": root / "restore",
        "key_file": key_file,
        "known_hosts": keys / "known_hosts",
        "passkey": passkey,
    }


@pytest.fixture(scope="session")
def build_env_file(workspace):
    """Minimal env file holding only what 'make build' reads."""
    path = workspace["root"] / "env.build"
    example = (REPO_ROOT / ".env.example").read_text()
    alpine = _read_var(example, "ALPINE_VERSION")
    gocryptfs = _read_var(example, "GOCRYPTFS_VERSION")
    path.write_text(
        "\n".join(
            [
                f'ALPINE_VERSION="{alpine}"',
                f'GOCRYPTFS_VERSION="{gocryptfs}"',
                f'DOCKER_IMAGE_TAG_NAME="{IMAGE_NAME}"',
                f'DOCKER_IMAGE_TAG_VERSION="{IMAGE_VERSION}"',
                "",
            ]
        )
    )
    return path


def _read_var(text, name):
    for line in text.splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError(f"{name} not found in .env.example")


@pytest.fixture(scope="session")
def image(build_env_file):
    """Build the image through 'make build' so the target itself is covered."""
    result = run_make("build", build_env_file, timeout=900)
    assert result.returncode == 0, result.stdout
    return IMAGE_REF


@pytest.fixture(scope="session")
def remote(image, workspace):
    """An sshd container standing in for the remote backup server.

    Reuses the project image, which already ships openssh, so the suite pulls
    nothing extra.
    """
    docker_rm(REMOTE_CONTAINER)

    setup = f"""
set -euo pipefail
ssh-keygen -A
mkdir -p /root/.ssh {REMOTE_BACKUP_DIR}
cp /keys/authorized_keys /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
echo 'root:rsync-crypt-test' | chpasswd
exec /usr/sbin/sshd -D -e -o PermitRootLogin=yes
"""
    started = run(
        [
            DOCKER,
            "run",
            "--detach",
            "--name",
            REMOTE_CONTAINER,
            "--user",
            "root",
            "--entrypoint",
            "/bin/bash",
            "--volume",
            f"{workspace['key_file']}.pub:/keys/authorized_keys:ro",
            IMAGE_REF,
            "-c",
            setup,
        ]
    )
    assert started.returncode == 0, started.stderr

    try:
        address = _wait_for_ip()
        _wait_for_sshd(address, workspace["known_hosts"])
        yield {"address": address, "target": f"{REMOTE_USER}@{address}"}
    finally:
        docker_rm(REMOTE_CONTAINER)


def _wait_for_ip(attempts=30):
    fmt = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
    for _ in range(attempts):
        result = run([DOCKER, "inspect", "-f", fmt, REMOTE_CONTAINER])
        address = result.stdout.strip()
        if address:
            return address
        time.sleep(1)
    raise AssertionError(f"{REMOTE_CONTAINER} never reported an IP address")


def _wait_for_sshd(address, known_hosts, attempts=30):
    """Poll sshd and record its host key, so the real known_hosts path is used."""
    for _ in range(attempts):
        scan = run(
            [
                DOCKER,
                "run",
                "--rm",
                "--entrypoint",
                "ssh-keyscan",
                IMAGE_REF,
                "-H",
                address,
            ]
        )
        keys = [
            line
            for line in scan.stdout.splitlines()
            if line and not line.startswith("#")
        ]
        if keys:
            Path(known_hosts).write_text("\n".join(keys) + "\n")
            return
        time.sleep(1)
    raise AssertionError(f"sshd on {address} never became reachable")


@pytest.fixture(scope="session")
def initialised_source(image, workspace):
    """Pre-create the gocryptfs reverse config in the source tree.

    On a first run backup.sh prints the master key and blocks on 'Press O once
    you have saved the master key'. Initialising up front means the tests
    exercise the steady-state path (mount plus rsync) rather than hanging on a
    one-time prompt. Writing .gocryptfs.reverse.conf.original is what makes
    backup.sh take its already-initialised branch.

    Both the init and the copy happen inside the container. gocryptfs writes
    the config 0600 as the container's root, and whether the host user can
    then read it depends on whether Docker is rootful or rootless: rootless
    maps container root to the invoking user, rootful does not. Keeping the
    copy on the container side works either way, and backup.sh reads both
    files as root regardless.
    """
    src = workspace["src"]
    marker = src / ".gocryptfs.reverse.conf.original"
    if not marker.exists():
        script = (
            "set -euo pipefail\n"
            "gocryptfs -reverse -init -plaintextnames"
            f" -scryptn {SCRYPT_N} -passfile /passfile /src\n"
            "cp /src/.gocryptfs.reverse.conf /src/.gocryptfs.reverse.conf.original\n"
            "chmod 600 /src/.gocryptfs.reverse.conf"
            " /src/.gocryptfs.reverse.conf.original\n"
        )
        result = run(
            [
                DOCKER,
                "run",
                "--rm",
                "--user",
                "root",
                "--entrypoint",
                "/bin/bash",
                "--volume",
                f"{src}:/src",
                "--volume",
                f"{workspace['passkey']}:/passfile:ro",
                IMAGE_REF,
                "-c",
                script,
            ]
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return src


def _chown_to_container_root(paths):
    """Hand files to whichever uid the container sees as root.

    ssh refuses an identity file owned by neither root nor the calling user,
    and every Makefile target runs as root inside the container. Under
    rootless Docker container root already maps to the invoking user, so this
    is a no-op; under rootful Docker, which is what GitHub runners provide, it
    hands the file to real root so the container sees uid 0.
    """
    volumes = []
    for index, path in enumerate(paths):
        volumes += ["--volume", f"{path}:/chown/{index}"]
    result = run(
        [
            DOCKER,
            "run",
            "--rm",
            "--user",
            "root",
            "--entrypoint",
            "/bin/bash",
            *volumes,
            IMAGE_REF,
            "-c",
            "chown 0:0 /chown/*",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="session")
def env_file(workspace, remote, initialised_source):
    """Full env file wired to the throwaway remote."""
    _chown_to_container_root([workspace["key_file"], workspace["known_hosts"]])

    path = workspace["root"] / "env.test"
    path.write_text(
        "\n".join(
            [
                f'DOCKER_IMAGE_TAG_NAME="{IMAGE_NAME}"',
                f'DOCKER_IMAGE_TAG_VERSION="{IMAGE_VERSION}"',
                f'SSH_KEY_FILE="{workspace["key_file"]}"',
                f'SSH_KNOWN_HOSTS_FILE="{workspace["known_hosts"]}"',
                f'GOCRYPTFS_PASSKEY_FILE="{workspace["passkey"]}"',
                "PARANOID_MODE=false",
                f'BACKUP_SOURCE_FOLDER="{workspace["src"]}"',
                f'BACKUP_FILTER_RULES="{REPO_ROOT}/conf/backup-filter-rules.txt"',
                f'REMOTE_SERVER="{remote["target"]}"',
                f'REMOTE_SERVER_BACKUP_FOLDER="{REMOTE_BACKUP_DIR}"',
                f'RESTORE_DESTINATION="{workspace["restore"]}"',
                f'RESTORE_EXCLUDE_LIST="{REPO_ROOT}/conf/restore-exclude-list.txt"',
                f'RESTORE_PATHS_FILE="{REPO_ROOT}/conf/restore-paths.txt"',
                "RSYNC_RATE_LIMIT=0",
                # false so a genuine failure surfaces as a failed test instead
                # of retrying with backoff until the timeout expires.
                "RSYNC_LOOP=false",
                "GOCRYPTFS_ENCRYPT_NAMES=false",
                'GOCRYPTFS_CIPHER="aes-gcm"',
                f"GOCRYPTFS_SCRYPT_N={SCRYPT_N}",
                "",
            ]
        )
    )
    return path


@pytest.fixture(autouse=True)
def clean_run_container():
    """Drop a leftover 'gocryptfs' container so the next target can start."""
    docker_rm(MAKE_CONTAINER)
    yield
    docker_rm(MAKE_CONTAINER)


def remote_listing():
    """Paths present on the remote, relative to the backup directory.

    Uses a plain find and strips the prefix in Python: the image ships busybox
    find, which has no -printf.
    """
    result = run(
        [
            DOCKER,
            "exec",
            REMOTE_CONTAINER,
            "find",
            REMOTE_BACKUP_DIR,
            "-mindepth",
            "1",
        ]
    )
    assert result.returncode == 0, result.stderr
    prefix = f"{REMOTE_BACKUP_DIR}/"
    return sorted(
        line[len(prefix) :]
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    )


def remote_manifest():
    """Map of remote file path to content digest.

    A listing alone cannot tell whether a repeated backup rewrote ciphertext,
    so the idempotency check compares digests too.
    """
    result = run(
        [
            DOCKER,
            "exec",
            REMOTE_CONTAINER,
            "sh",
            "-c",
            f"cd {REMOTE_BACKUP_DIR} && find . -type f -exec sha256sum {{}} +",
        ]
    )
    assert result.returncode == 0, result.stderr
    manifest = {}
    for line in result.stdout.splitlines():
        digest, _, path = line.partition("  ")
        if path:
            manifest[path.removeprefix("./")] = digest
    return manifest


def remote_bytes(relative_path):
    """Raw bytes of a file on the remote, as stored (encrypted)."""
    result = subprocess.run(
        [
            DOCKER,
            "exec",
            REMOTE_CONTAINER,
            "cat",
            f"{REMOTE_BACKUP_DIR}/{relative_path}",
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout
