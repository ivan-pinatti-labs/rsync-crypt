"""The rsync retry loops must not spin forever on a partial transfer.

rsync exit 23 means a partial transfer (files skipped as unreadable or
locked) and 24 means files vanished between the file list being built and
the transfer running. Neither is retriable: a second attempt hits the same
files and returns the same status, so a loop that treats them as failures
never terminates.

backup.sh has treated both as success-with-warning since they caused exactly
that infinite loop. restore.sh carried the same loop shape unfixed, which is
what these tests guard against coming back.

These are static checks on the scripts rather than live rsync runs: making a
real rsync return 23 needs an unreadable file inside a transfer, and making
it return 24 needs a file deleted mid-run, neither of which is reproducible
enough to gate a suite on. The failure being guarded is a missing branch, so
the branch is what gets asserted.
"""

from __future__ import annotations

import re

import pytest
from conftest import REPO_ROOT

LOOPING_SCRIPTS = ["backup.sh", "restore.sh"]


def _script(name):
    return (REPO_ROOT / "scripts" / name).read_text()


@pytest.mark.parametrize("name", LOOPING_SCRIPTS)
def test_partial_transfer_statuses_break_the_loop(name):
    """23 and 24 must be handled, and must break rather than retry."""
    body = _script(name)
    assert "-eq 23" in body and "-eq 24" in body, (
        f"{name} does not special-case rsync exit 23/24, so a partial "
        f"transfer is treated as a retriable failure and loops forever"
    )

    # The branch that matches 23/24 has to break out, not fall through to the
    # retry path. Grab the elif block and assert it contains a break.
    branch = re.search(
        r"elif \[ \"\$\{__rsync_exit\}\" -eq 23 \].*?(?=\n  else\b)",
        body,
        re.DOTALL,
    )
    assert branch, f"{name}: could not locate the 23/24 branch"
    assert "break" in branch.group(0), (
        f"{name}: the 23/24 branch does not break, so it still retries"
    )


@pytest.mark.parametrize("name", LOOPING_SCRIPTS)
def test_retry_path_backs_off(name):
    """A persistent failure must not spin as fast as rsync can return."""
    body = _script(name)
    assert "__retry_delay" in body, f"{name} has no retry delay"
    assert re.search(r"sleep \"\$\{__retry_delay\}\"", body), (
        f"{name} defines a retry delay but never sleeps on it"
    )
    assert "__retry_delay_max" in body, f"{name} has no ceiling on its backoff"


@pytest.mark.parametrize("name", LOOPING_SCRIPTS)
def test_rsync_exit_status_is_captured(name):
    """`if rsync ...; then` discards the status, which is how this was missed."""
    body = _script(name)
    assert re.search(r"rsync .*\|\| __rsync_exit=\$\?", body), (
        f"{name} does not capture rsync's exit status, so it cannot tell "
        f"23 or 24 apart from a real failure"
    )
