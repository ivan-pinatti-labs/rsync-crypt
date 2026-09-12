"""Every container run stays `--user root`, and the reason is not the obvious one.

Issue #69 found `USER 1000` in the Dockerfile inert, since all ten `docker
run` invocations in the Makefile override it. The resolution was to keep it
that way and record why, so these tests exist to make the next person who
spots the same thing read the reasoning before changing it.

The reasoning, in short, because a test that only says "do not change this" is
worse than none: under this project's default runtime, rootless Podman, the
container's root IS the invoking user mapped through a user namespace.
`--user root` is therefore the unprivileged choice, and passing `--user $(id
-u)` instead maps to a subuid that cannot read the invoking user's own files.
That was measured rather than assumed, and it fails in the direction that
looks like it works: the mount succeeds and the read fails later.

Root is not what the FUSE mount needs either. fusermount is setuid in the
image, so `--cap-add SYS_ADMIN --device /dev/fuse` is what the Makefile passes
for that, and a non-root uid can mount perfectly well. Both of those facts
live in the Makefile comment this test guards.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

MAKEFILE = (REPO_ROOT / "Makefile").read_text()
DOCKERFILE = (REPO_ROOT / "Dockerfile").read_text()

# A `docker run` recipe line, and the flags that follow it until the image
# reference. Each run is one backslash-continued shell command.
RUN_BLOCK = re.compile(r"^\tdocker run \\\n((?:\t\t.*\\\n)+)", re.MULTILINE)


def _run_blocks():
    blocks = RUN_BLOCK.findall(MAKEFILE)
    assert blocks, "no `docker run` blocks found in the Makefile"
    return blocks


def test_every_container_run_is_user_root():
    for block in _run_blocks():
        assert "--user root" in block, (
            "a `docker run` here does not pass `--user root`. Under rootless "
            "Podman, this project's default runtime, container root is the "
            "invoking user and `--user root` is the unprivileged choice; "
            "`--user $(id -u)` maps to a subuid that cannot read that user's "
            "own files. See the comment above the targets in the Makefile and "
            f"issue #69.\nBlock:\n{block}"
        )


def test_the_mount_is_granted_by_capability_not_by_uid():
    """Every run that mounts a FUSE filesystem asks for the two things it needs."""
    for block in _run_blocks():
        if "--device /dev/fuse" in block:
            assert "--cap-add SYS_ADMIN" in block, (
                f"a run with /dev/fuse does not add SYS_ADMIN:\n{block}"
            )


def test_ssh_material_is_mounted_where_the_container_user_reads_it():
    """The key goes to root's home, since that is who the container runs as.

    `run_container` used to mount it at /home/crypt/.ssh/id_rsa while running
    as root, a leftover from an unprivileged design that was never finished,
    so in that one target the key landed where nothing would read it.
    """
    key_mounts = re.findall(r"--volume \$\{SSH_KEY_FILE\}:(\S+)", MAKEFILE)
    assert key_mounts, "no SSH key mounts found"
    for target in key_mounts:
        assert target == "/root/.ssh/id_rsa", (
            f"SSH key mounted at {target}, which the container's root user "
            "does not read. It belongs at /root/.ssh/id_rsa."
        )


def test_every_ssh_key_mount_is_paired_with_known_hosts():
    """Otherwise host verification prompts, or silently accepts, per target."""
    keys = MAKEFILE.count("--volume ${SSH_KEY_FILE}:")
    hosts = MAKEFILE.count("--volume ${SSH_KNOWN_HOSTS_FILE}:")
    assert keys == hosts, (
        f"{keys} SSH key mounts but {hosts} known_hosts mounts; each target "
        "that mounts one should mount the other."
    )


def test_the_image_still_defaults_to_an_unprivileged_user():
    """What someone running the published image directly gets, and its only job."""
    assert re.search(r"^USER 1000$", DOCKERFILE, re.MULTILINE), (
        "the image must still default to a non-root user for anyone running "
        "it without the Makefile"
    )
