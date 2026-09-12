"""Network-backed mounts under the backup source are excluded by default.

A NAS share, an sshfs mount or an rclone remote mounted somewhere inside
BACKUP_SOURCE_FOLDER is storage that lives on another machine. Backing it up
means reading every byte over the network, encrypting it and pushing it to the
remote server, which is slow and almost never what "back up this machine"
meant. backup.sh therefore looks for those mounts before it creates the
gocryptfs reverse view and hands each one to gocryptfs as an -exclude.

These tests execute the real shell lifted out of scripts/backup.sh, between
its BEGIN/END markers, against synthetic /proc/self/mountinfo fixtures. That
is what makes the awkward parts testable without a privileged mount: the
optional fields before the "-" separator, the kernel's \\040 path escapes, and
the difference between a network filesystem and a local one.

Why gocryptfs -exclude rather than generated rsync filter rules: with
GOCRYPTFS_ENCRYPT_NAMES=true rsync only ever sees ciphertext names and no
filter pattern can match them (CLAUDE.md, "GOCRYPTFS_ENCRYPT_NAMES must be
false for filter rules to work"), while gocryptfs excludes on the plaintext
path before encrypting it and so works either way. Measured 2026-09-11
against the image's gocryptfs (2.6.1 then), in both name modes.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from conftest import REPO_ROOT, _read_var, run

# Nothing here starts a container or touches stack state: the shell blocks run
# directly and the two Makefile checks are 'make --dry-run'.
pytestmark = pytest.mark.scripts

BACKUP_SH = REPO_ROOT / "scripts" / "backup.sh"

SOURCE = "/backup/src"

# One ordinary local root plus the source's own mount, prepended to every
# fixture so each one is a plausible mount table rather than a bare line.
_PREAMBLE = (
    "21 24 0:20 / /sys rw,nosuid,nodev,noexec,relatime shared:7 - sysfs sysfs rw",
    "24 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw",
    f"30 24 0:24 / {SOURCE} rw,relatime shared:2"
    " - btrfs /dev/nvme0n1p3 rw,subvol=/home",
)


def _lift(name):
    """The shell between backup.sh's `--- BEGIN <name> ---`/`--- END <name> ---`.

    Lifted rather than duplicated so these tests cannot drift from the script
    they are meant to be checking, the same approach test_gocryptfs_cipher.py
    takes to the cipher `case` block.
    """
    body = BACKUP_SH.read_text()
    match = re.search(
        rf"^# --- BEGIN {re.escape(name)}\b.*?^# --- END {re.escape(name)} ---$",
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"the '{name}' block was not found in backup.sh"
    return match.group(0)


def _run_flag(value=None):
    """Run the real flag block with BACKUP_EXCLUDE_NETWORK_MOUNTS as given.

    `None` leaves the variable unset, which is a different path through
    `${BACKUP_EXCLUDE_NETWORK_MOUNTS:-true}` than setting it to the empty
    string, and is what an env file written before this setting existed
    produces.
    """
    script = (
        "set -o errexit -o pipefail -o nounset\n"
        f"{_lift('network-mount flag')}\n"
        'printf "FLAG:%s\\n" "${__exclude_network_mounts}"\n'
    )
    env = {"PATH": "/usr/bin:/bin"}
    if value is not None:
        env["BACKUP_EXCLUDE_NETWORK_MOUNTS"] = value
    # check=False on purpose: the exit status is part of what is asserted.
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        check=False,
    )


def _detect(tmp_path, lines, flag="true", source=SOURCE, preamble=True):
    """Run the real detection block over a synthetic mount table.

    Returns (CompletedProcess, argv), where argv is the gocryptfs argument
    list the block built. The arguments are written NUL-separated to a file
    rather than printed, so a mount point containing a space, a tab or even a
    newline survives the round trip intact and the test sees exactly what
    gocryptfs would be handed.
    """
    mountinfo = tmp_path / "mountinfo"
    body = list(_PREAMBLE) + list(lines) if preamble else list(lines)
    mountinfo.write_text("".join(f"{line}\n" for line in body))
    argv_file = tmp_path / "argv"

    script = (
        "set -o errexit -o pipefail -o nounset\n"
        f"{_lift('network-mount detection')}\n"
        "__gocryptfs_exclude_args=()\n"
        '__build_network_mount_excludes "$1" "$2" "$3"\n'
        # An empty array must produce an empty file, not one NUL: printf with
        # no arguments still runs its format once, which would read back as a
        # single empty exclusion rather than as no exclusions at all.
        ': > "$4"\n'
        'if [ "${#__gocryptfs_exclude_args[@]}" -gt 0 ]; then\n'
        '  printf "%s\\0" "${__gocryptfs_exclude_args[@]}" > "$4"\n'
        "fi\n"
    )
    result = subprocess.run(
        ["bash", "-c", script, "detect", flag, str(mountinfo), source, str(argv_file)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if not argv_file.exists():
        return result, None
    raw = argv_file.read_bytes().decode()
    return result, raw.split("\0")[:-1] if raw else []


def _excluded_paths(argv):
    """The paths from an [-exclude, path, -exclude, path, ...] argument list."""
    assert argv is not None, "the block never wrote an argument list"
    assert len(argv) % 2 == 0, f"odd number of arguments: {argv!r}"
    flags = argv[0::2]
    assert set(flags) <= {"-exclude"}, (
        f"every exclusion must be spelled '-exclude', got {flags!r}. "
        f"gocryptfs's own alias is '-e'; the long form is what the script "
        f"passes so the command stays readable in a log"
    )
    return argv[1::2]


# ---------------------------------------------------------------------------
# The flag itself
# ---------------------------------------------------------------------------


def test_unset_defaults_to_true():
    """An env file written before this setting existed gets the safe behaviour.

    This is the whole reason the setting travels as an environment variable
    rather than a twelfth positional argument: an old env file leaves it unset
    instead of shifting every later argument along one slot.
    """
    result = _run_flag()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FLAG:true" in result.stdout


def test_empty_defaults_to_true():
    """Empty means unset here, because that is what the Makefile can produce.

    'make backup' always passes '--env BACKUP_EXCLUDE_NETWORK_MOUNTS=<value>',
    so an env file with no such line hands the container an empty string, not
    an absent variable. Both have to mean true or the default would depend on
    which of the two paths the user came in through.
    """
    result = _run_flag("")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FLAG:true" in result.stdout


@pytest.mark.parametrize("value", ["true", "false"])
def test_explicit_values_are_accepted(value):
    result = _run_flag(value)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"FLAG:{value}" in result.stdout


@pytest.mark.parametrize("value", ["yes", "no", "1", "0", "True", "FALSE", "maybe"])
def test_invalid_values_abort_with_an_explanation(value):
    """A typo must stop the backup, not fall back to one of the two behaviours.

    Falling back to true would silently ignore a user who asked for false;
    falling back to false would silently copy network storage, which is the
    exact outcome this feature exists to prevent. Neither is safe to guess.
    """
    result = _run_flag(value)
    assert result.returncode == 1, (
        f"{value!r} was accepted and exited {result.returncode}"
    )
    combined = result.stdout + result.stderr
    assert "BACKUP_EXCLUDE_NETWORK_MOUNTS" in combined
    assert "true" in combined and "false" in combined, (
        f"the message must name the values that do work: {combined!r}"
    )


def test_false_skips_detection_entirely(tmp_path):
    """false is the escape hatch, so it must not even read the mount table."""
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/nas rw,relatime shared:3 - cifs //server/share rw"],
        flag="false",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv == [], f"false must produce no exclusions, got {argv!r}"
    assert "false" in result.stdout.lower()


def test_an_invalid_flag_also_aborts_the_detection_block(tmp_path):
    """The builder re-checks rather than trusting its caller.

    The flag is validated at startup so a typo fails before the first-run
    master key prompt, but the builder is reachable on its own and an
    unrecognised value there must abort too, never quietly take a branch.
    """
    result, _ = _detect(tmp_path, [], flag="maybe")
    assert result.returncode != 0
    assert "BACKUP_EXCLUDE_NETWORK_MOUNTS" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# What counts as a network mount
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fstype", "device"),
    [
        ("cifs", "//nas/share"),
        ("smb3", "//nas/share"),
        ("nfs", "server:/export"),
        ("nfs4", "server:/export"),
        ("fuse.sshfs", "user@host:/"),
        ("fuse.rclone", "rclone:remote"),
        ("fuse.s3fs", "s3fs"),
        ("fuse.gcsfuse", "bucket"),
        ("fuse.goofys", "goofys"),
        ("fuse.davfs", "https://dav.example"),
        ("davfs", "https://dav.example"),
        ("davfs2", "https://dav.example"),
        ("ceph", "mon:/"),
        ("glusterfs", "gluster:/vol"),
        ("fuse.glusterfs", "gluster:/vol"),
        ("lustre", "mgs:/fs"),
        ("afs", "afs"),
        ("coda", "coda"),
        ("fuse.gvfsd-fuse", "gvfsd-fuse"),
    ],
)
def test_network_filesystem_types_are_detected(tmp_path, fstype, device):
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/share rw,relatime - {fstype} {device} rw"],
        preamble=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _excluded_paths(argv) == ["share"]
    assert fstype in result.stdout, (
        f"the log must name the filesystem type so the exclusion can be "
        f"understood without re-running anything: {result.stdout!r}"
    )


@pytest.mark.parametrize(
    ("fstype", "device"),
    [
        ("ext4", "/dev/sda1"),
        ("btrfs", "/dev/sda2"),
        ("xfs", "/dev/sda3"),
        ("tmpfs", "tmpfs"),
        ("vfat", "/dev/sdb1"),
        ("exfat", "/dev/sdb2"),
        ("overlay", "overlay"),
        ("fuse.encfs", "encfs"),
        ("9p", "host_share"),
    ],
)
def test_local_filesystem_types_are_left_alone(tmp_path, fstype, device):
    """Only known-remote types are excluded; everything else is backed up.

    9p is in this list deliberately. It is a wire protocol, but it is how a VM
    or WSL sees a host directory, which is frequently exactly where the user's
    data lives, so treating it as remote would silently empty the backup.
    """
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/thing rw,relatime - {fstype} {device} rw"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv == [], f"{fstype} was treated as a network mount: {argv!r}"


def test_a_local_bind_mount_is_not_a_network_mount(tmp_path):
    """A bind mount reports the underlying filesystem's type, so it passes."""
    result, argv = _detect(
        tmp_path,
        [
            f"37 30 0:24 /sub {SOURCE}/bind rw,relatime shared:2"
            " - btrfs /dev/nvme0n1p3 rw,subvol=/home"
        ],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv == []


def test_a_bind_mount_of_a_network_share_is_still_excluded(tmp_path):
    """The filesystem type travels with the bind, which is what makes this work."""
    result, argv = _detect(
        tmp_path,
        [f"37 30 0:41 /sub {SOURCE}/bind rw,relatime - cifs //nas/share rw"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _excluded_paths(argv) == ["bind"]


# ---------------------------------------------------------------------------
# Which mounts are in scope
# ---------------------------------------------------------------------------


def test_mounts_outside_the_source_are_ignored(tmp_path):
    result, argv = _detect(
        tmp_path,
        [
            "38 24 0:47 / /mnt/other-nas rw,relatime - cifs //server/other rw",
            "39 24 0:48 / /srv/nfs rw,relatime - nfs4 server:/srv rw",
        ],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv == []
    assert "No network-backed mounts found" in result.stdout


def test_a_sibling_with_the_same_prefix_is_ignored(tmp_path):
    """'/backup/src-sibling' starts with '/backup/src' but is not under it.

    A prefix test written as a plain string comparison gets this wrong, which
    is why the match requires the separating slash.
    """
    result, argv = _detect(
        tmp_path,
        [f"40 24 0:48 / {SOURCE}-sibling rw,relatime - nfs server:/sibling rw"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv == []


def test_the_backup_source_mount_itself_is_never_excluded(tmp_path):
    """Excluding the source would exclude the entire backup.

    Someone whose home directory is itself an NFS mount gets a backup of it,
    not an empty one. Only mounts strictly below the source are in scope.
    """
    result, argv = _detect(
        tmp_path,
        [f"30 24 0:41 / {SOURCE} rw,relatime - nfs4 server:/home rw"],
        preamble=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert argv == []


def test_a_trailing_slash_on_the_source_does_not_leak_into_the_paths(tmp_path):
    """-exclude takes a path relative to the mount root, never an absolute one."""
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/nas rw,relatime - cifs //nas/share rw"],
        source=f"{SOURCE}/",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    paths = _excluded_paths(argv)
    assert paths == ["nas"]
    assert not paths[0].startswith("/")


def test_nested_paths_keep_their_full_relative_path(tmp_path):
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/media/movies rw,relatime - nfs4 s:/m rw"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _excluded_paths(argv) == ["media/movies"]


def test_duplicate_mounts_are_excluded_once(tmp_path):
    """One directory can appear several times: mounted over, or in two namespaces."""
    line = f"31 30 0:41 / {SOURCE}/nas rw,relatime shared:3 - cifs //nas/share rw"
    result, argv = _detect(tmp_path, [line, line.replace("31 30", "39 30")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _excluded_paths(argv) == ["nas"]


# ---------------------------------------------------------------------------
# mountinfo parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "optional",
    ["", "shared:3", "shared:3 master:2", "shared:3 master:2 propagate_from:1"],
)
def test_optional_fields_before_the_separator_are_handled(tmp_path, optional):
    """The optional fields vary in number, so the '-' has to be searched for.

    Indexing a fixed position instead reads an optional field as the
    filesystem type, which silently stops detecting anything.
    """
    fields = f" {optional}" if optional else ""
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/nas rw,relatime{fields} - cifs //nas/share rw"],
    )
    assert _excluded_paths(argv) == ["nas"], (
        f"optional fields {optional!r} broke the parse: {result.stdout}"
    )


@pytest.mark.parametrize(
    ("escaped", "decoded"),
    [
        (r"my\040share", "my share"),
        (r"two\040\040spaces", "two  spaces"),
        (r"tab\011here", "tab\there"),
        (r"back\134slash", "back\\slash"),
        (r"newline\012here", "newline\nhere"),
        # A directory literally named '\040'. The kernel writes the backslash
        # as \134, so decoding \134 before \040 would turn this into a space
        # and exclude a path that does not exist while backing up one that
        # does. Only a single left-to-right pass gets it right.
        (r"literal\134040", "literal\\040"),
    ],
)
def test_mountinfo_path_escapes_are_decoded(tmp_path, escaped, decoded):
    result, argv = _detect(
        tmp_path,
        [f"31 30 0:41 / {SOURCE}/{escaped} rw,relatime - cifs //nas/share rw"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _excluded_paths(argv) == [decoded]


def test_an_escaped_space_is_one_argument_not_two(tmp_path):
    """The exclusion has to reach gocryptfs as a single argv entry."""
    _, argv = _detect(
        tmp_path,
        [rf"31 30 0:41 / {SOURCE}/my\040share rw,relatime - cifs //nas/share rw"],
    )
    assert argv == ["-exclude", "my share"]


@pytest.mark.parametrize(
    "line",
    [
        "31 30 0:41 / /backup/src/nas rw,relatime shared:3",  # truncated
        "31 30 0:41 / /backup/src/nas rw,relatime - cifs",  # no super options
        "garbage",
    ],
)
def test_an_unparseable_line_fails_the_backup(tmp_path, line):
    """Refusing to guess is the point.

    A mount table this cannot parse is a mount table it cannot prove is free
    of network storage. Carrying on would back that storage up silently, which
    is precisely the failure this feature exists to prevent, so the backup
    stops and says so instead.
    """
    result, _ = _detect(tmp_path, [line])
    assert result.returncode != 0, f"{line!r} was parsed rather than refused"
    assert "mountinfo" in (result.stdout + result.stderr).lower()


def test_an_unreadable_mount_table_fails_the_backup(tmp_path):
    script = (
        "set -o errexit -o pipefail -o nounset\n"
        f"{_lift('network-mount detection')}\n"
        "__gocryptfs_exclude_args=()\n"
        '__build_network_mount_excludes true "$1" "$2"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script, "detect", str(tmp_path / "absent"), SOURCE],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Cannot read" in combined
    assert "BACKUP_EXCLUDE_NETWORK_MOUNTS=false" in combined, (
        "the failure must name the way out, or it is a dead end for anyone "
        "whose mount table this cannot read"
    )


def test_a_real_mount_table_parses(tmp_path):
    """The parser has to survive this machine's own /proc/self/mountinfo.

    The synthetic fixtures above are all hand-written, so they only ever test
    the shapes that were thought of. A real table brings whatever this kernel
    and container runtime actually emit, including any optional-field
    combination the fixtures missed.
    """
    real = open("/proc/self/mountinfo").read()
    mountinfo = tmp_path / "real"
    mountinfo.write_text(real)
    script = (
        "set -o errexit -o pipefail -o nounset\n"
        f"{_lift('network-mount detection')}\n"
        "__gocryptfs_exclude_args=()\n"
        '__build_network_mount_excludes true "$1" "$2"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script, "detect", str(mountinfo), "/nonexistent-source"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"this machine's own mount table failed to parse: {result.stdout}"
        f"{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Wiring: backup.sh, the Makefile and .env.example
# ---------------------------------------------------------------------------


def test_backup_sh_passes_the_exclusions_to_the_reverse_mount():
    """The arguments must reach the gocryptfs command, not just be built."""
    body = BACKUP_SH.read_text()
    match = re.search(r"^if gocryptfs -ro .*?; then$", body, re.MULTILINE | re.DOTALL)
    assert match, "the reverse-mount command was not found in backup.sh"
    assert '"${__gocryptfs_exclude_args[@]}"' in match.group(0)


def test_detection_runs_before_the_reverse_mount():
    """Excluding a path after the view exists would exclude nothing."""
    body = BACKUP_SH.read_text()
    build = body.index('__build_network_mount_excludes "${__exclude_network_mounts}"')
    mount = body.index("if gocryptfs -ro ")
    assert build < mount


def test_rsync_does_not_use_one_file_system():
    """--one-file-system would also drop local mounts under the source.

    It is the obvious-looking shortcut for this feature and the wrong one: a
    second local disk mounted under the source is ordinary data that belongs
    in the backup.
    """
    invocations = [
        line
        for line in BACKUP_SH.read_text().splitlines()
        if re.search(r"(^|\s)rsync\s", line) and not line.lstrip().startswith("#")
    ]
    assert invocations, "no rsync invocation was found in backup.sh"
    for line in invocations:
        for flag in ("--one-file-system", " -x "):
            assert flag not in line, f"rsync is using {flag!r}: {line}"


@pytest.mark.parametrize("target", ["backup", "backup_as_root"])
def test_the_setting_reaches_both_backup_targets(target):
    """Both targets run backup.sh, so both have to pass the setting through."""
    result = run(["make", "--dry-run", target, "ENV_FILE=.env.example"])
    assert result.returncode == 0, result.stdout + result.stderr
    expected = _read_var(
        (REPO_ROOT / ".env.example").read_text(), "BACKUP_EXCLUDE_NETWORK_MOUNTS"
    )
    assert f"--env BACKUP_EXCLUDE_NETWORK_MOUNTS='{expected}'" in result.stdout


def test_the_example_env_defaults_to_true():
    """The documented default has to match the script's own fallback.

    .env.example is what every new install is copied from, so a 'false' here
    would opt everyone out of the behaviour while the code still called true
    its default.
    """
    example = (REPO_ROOT / ".env.example").read_text()
    assert _read_var(example, "BACKUP_EXCLUDE_NETWORK_MOUNTS") == "true"
