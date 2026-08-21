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


BRANCH_HEADER = re.compile(r"^(?P<indent>\s*)(?P<kw>if|elif|else)\b(?P<rest>.*)$")


def _branches(body):
    """Yield (condition, body_lines) for each branch of every if/elif chain.

    A branch's body stops at the next branch header sharing its indentation,
    so a `break` in one branch is never credited to another. That mattered:
    the earlier version of this test matched from `-eq 23` to the closing
    `else` with a DOTALL `.*?`, which spans any number of intervening
    branches, so a script that broke on 23 and fell through on 24 passed it.
    """
    lines = body.splitlines()
    headers = []
    for i, line in enumerate(lines):
        m = BRANCH_HEADER.match(line)
        if m:
            headers.append((i, len(m.group("indent")), m.group("rest")))
    for pos, (start, indent, cond) in enumerate(headers):
        end = len(lines)
        for nxt_start, nxt_indent, _ in headers[pos + 1 :]:
            if nxt_indent <= indent:
                end = nxt_start
                break
        yield cond, lines[start + 1 : end]


@pytest.mark.parametrize("name", LOOPING_SCRIPTS)
@pytest.mark.parametrize("status", [23, 24])
def test_partial_transfer_status_breaks_the_loop(name, status):
    """Each of 23 and 24 must be handled by a branch that actually breaks."""
    body = _script(name)
    token = f"-eq {status}"
    assert token in body, (
        f"{name} does not special-case rsync exit {status}, so a partial "
        f"transfer is treated as a retriable failure and loops forever"
    )

    handling = [(cond, lines) for cond, lines in _branches(body) if token in cond]
    assert handling, (
        f"{name}: `{token}` appears but not as a branch condition, so nothing "
        f"acts on it"
    )

    # Every branch that claims this status has to break. An executable
    # `break` on its own line, not the word inside a comment or a string:
    # `"break" in branch` once passed against a branch whose only break was
    # the comment `# we should break here`.
    for cond, lines in handling:
        assert any(line.strip() == "break" for line in lines), (
            f"{name}: the branch matching `{token}` ({cond.strip()}) has no "
            f"executable break, so it falls through to the retry path and loops"
        )


@pytest.mark.parametrize("name", LOOPING_SCRIPTS)
def test_retry_path_backs_off(name):
    """A persistent failure must not spin as fast as rsync can return."""
    body = _script(name)
    assert "__retry_delay" in body, f"{name} has no retry delay"
    assert re.search(r"sleep \"\$\{__retry_delay\}\"", body), (
        f"{name} defines a retry delay but never sleeps on it"
    )

    # The cap has to be used by the computation, not merely defined. Asserting
    # only that the name appears in the file passed against a version where
    # the assignment had been reduced to `__retry_delay * 2` with the ceiling
    # left dangling, which is unbounded growth with a reassuring variable name.
    assignment = re.search(r"__retry_delay=\$\(\(.*?\)\)", body, re.DOTALL)
    assert assignment, f"{name}: no arithmetic update of __retry_delay found"
    assert "__retry_delay_max" in assignment.group(0), (
        f"{name}: the backoff grows without reference to __retry_delay_max, "
        f"so the ceiling is never applied"
    )


@pytest.mark.parametrize("name", LOOPING_SCRIPTS)
def test_rsync_exit_status_is_captured(name):
    """`if rsync ...; then` discards the status, which is how this was missed."""
    body = _script(name)
    assert re.search(r"rsync .*\|\| __rsync_exit=\$\?", body), (
        f"{name} does not capture rsync's exit status, so it cannot tell "
        f"23 or 24 apart from a real failure"
    )
