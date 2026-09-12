"""Per-session files must live in a private directory, not a fixed /tmp path.

`view.sh` wrote its sshd config and the host key it generates to
`/tmp/sshd_config` and `/tmp/ssh_host_ed25519_key`, and `restore.sh` wrote its
`RESTORE_PATHS` override to `/tmp/restore-paths-override.txt`. Fixed names
under a shared /tmp have two consequences: a second run overwrites the first
run's files while the first is still using them, and the material sits
somewhere predictable rather than somewhere only that run can reach.

These are static checks on the scripts rather than live runs. Proving the
real behaviour would mean standing up sshd against a gocryptfs mount inside
the container and racing two sessions, which is neither reproducible nor
cheap; the failure being guarded is a path written back as a literal, so the
literal is what gets asserted.

The pkill assertion is the one that is not merely about the literal. `sshd` is
stopped by matching its own command line, so the pattern and the `-f` argument
have to name the same file. They agreed as two copies of one hardcoded string
before; now they agree only because both read the same variable, and nothing
but this test notices if a later edit reintroduces a second spelling. A pkill
that matches nothing leaves sshd serving the decrypted view after the script
believes it has torn everything down.
"""

from __future__ import annotations

import re

import pytest
from conftest import REPO_ROOT

# A path literal directly under /tmp, which is what none of these scripts may
# carry any more. `/tmp` on its own is not matched: the scripts are free to
# mention it in prose, and mktemp's own default lands there.
FIXED_TMP_PATH = re.compile(r"(?<![\w$])/tmp/[\w.-]+")

SCRIPTS = ["view.sh", "restore.sh", "backup.sh"]


def _script(name):
    return (REPO_ROOT / "scripts" / name).read_text()


def _code_lines(body):
    """Lines with comments stripped, so prose cannot fail a test about code."""
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        yield line.split(" #")[0]


@pytest.mark.parametrize("name", SCRIPTS)
def test_no_fixed_tmp_paths(name):
    offenders = [
        line.strip()
        for line in _code_lines(_script(name))
        if FIXED_TMP_PATH.search(line)
    ]
    assert not offenders, (
        f"scripts/{name} names a fixed path under /tmp: {offenders}. "
        "Per-session files belong in a mktemp -d directory."
    )


def test_view_creates_a_private_scratch_directory():
    body = _script("view.sh")
    assert re.search(r'__view_tmp_dir="\$\(mktemp -d\)"', body), (
        "view.sh must create its scratch directory with mktemp -d"
    )
    for var in ("__view_sshd_config", "__view_host_key"):
        assert re.search(rf'{var}="\$\{{__view_tmp_dir\}}/', body), (
            f"{var} must live inside the mktemp -d directory"
        )


def test_view_host_key_and_config_are_written_to_the_scratch_paths():
    body = _script("view.sh")
    assert 'ssh-keygen -t ed25519 -f "${__view_host_key}"' in body
    assert 'cat >"${__view_sshd_config}"' in body
    assert "HostKey ${__view_host_key}" in body


def test_view_stops_the_sshd_it_started():
    """The pkill pattern and the sshd -f argument must name the same file."""
    body = _script("view.sh")
    started = re.search(r"/usr/sbin/sshd -f \"(?P<path>[^\"]+)\"", body)
    assert started, "view.sh must start sshd with a quoted config path"

    killed = re.findall(r'pkill -f "sshd -f (?P<path>[^"]+)"', body)
    assert killed, "view.sh must stop the sshd it started"
    for pattern in killed:
        assert pattern == started.group("path"), (
            f"pkill matches {pattern!r} but sshd was started with "
            f"{started.group('path')!r}; the two must be the same file, or "
            "the running sshd survives teardown."
        )


@pytest.mark.parametrize(
    ("name", "variable"),
    [("view.sh", "__view_tmp_dir"), ("restore.sh", "__restore_tmp_dir")],
)
def test_scratch_directory_is_removed_on_exit(name, variable):
    body = _script(name)
    assert re.search(rf'rm -rf "\$\{{{variable}\}}"', body), (
        f"scripts/{name} must remove {variable}"
    )
    # Leading whitespace allowed: restore.sh sets its trap inside the
    # `if` that creates the directory, since that is its only user.
    assert re.search(r"^\s*trap .*EXIT$", body, re.MULTILINE), (
        f"scripts/{name} must remove it on every exit path, not only the "
        "happy one, which takes an EXIT trap"
    )
