"""Full backup and restore round trip against a throwaway sshd container.

This is the test that actually exercises the tool: gocryptfs reverse-mounts the
source, rsync pushes the encrypted view over SSH, and a restore pulls it back
and decrypts it. Everything else in the suite is a cheaper sanity check.
"""

from __future__ import annotations

import pytest

from conftest import (
    MAKE_CONTAINER,
    docker_rm,
    remote_bytes,
    remote_listing,
    remote_manifest,
    run_make,
)

EXPECTED_ON_REMOTE = [
    "Documents/notes.txt",
    "Documents/nested/deep.txt",
    ".config/Code/User/settings.json",
]

EXCLUDED_BY_FILTER_RULES = [
    "Documents/app.lock",
    "Documents/data.db-wal",
    "Documents/.cache/junk.txt",
    "backup/stuff.txt",
]


@pytest.fixture(scope="session")
def backup(env_file):
    docker_rm(MAKE_CONTAINER)
    result = run_make("backup", env_file)
    assert result.returncode == 0, result.stdout
    return result


@pytest.fixture(scope="session")
def restored(backup, env_file, workspace):
    docker_rm(MAKE_CONTAINER)
    result = run_make("restore", env_file)
    assert result.returncode == 0, result.stdout
    return workspace["restore"]


def test_backup_reports_success(backup):
    assert "rsync succeeded" in backup.stdout


def test_backup_reuses_the_existing_gocryptfs_config(backup):
    """A second run must not re-initialise and re-encrypt everything."""
    assert "already initialized for gocryptfs usage" in backup.stdout
    assert "FIRST-TIME INITIALIZATION" not in backup.stdout


def test_gocryptfs_config_reaches_the_remote(backup):
    """restore.sh refuses to decrypt without it."""
    assert "gocryptfs.conf" in remote_listing()


@pytest.mark.parametrize("path", EXPECTED_ON_REMOTE)
def test_included_file_reaches_the_remote(backup, path):
    assert path in remote_listing()


@pytest.mark.parametrize("path", EXCLUDED_BY_FILTER_RULES)
def test_filter_rules_keep_file_off_the_remote(backup, path):
    assert path not in remote_listing(), f"{path} should have been excluded"


def test_file_contents_are_encrypted_on_the_remote(backup, workspace):
    """Plaintext filenames are expected here; contents must still be ciphertext."""
    plaintext = (workspace["src"] / "Documents" / "notes.txt").read_bytes()
    stored = remote_bytes("Documents/notes.txt")

    assert stored != plaintext
    assert b"hello world" not in stored
    # gocryptfs prepends a file header and appends an auth tag per block.
    assert len(stored) > len(plaintext)


def test_filenames_are_plaintext_when_encrypt_names_is_false(backup):
    """Guards the documented coupling between name encryption and filter rules.

    Filter rules match on the names rsync sees. If names were scrambled, no
    pattern could match and the exclusion tests above would silently pass for
    the wrong reason.
    """
    listing = remote_listing()
    assert "Documents" in listing
    assert not any(name.startswith("gocryptfs.longname.") for name in listing)


def test_restore_reports_success(restored, backup):
    assert restored.exists()


@pytest.mark.parametrize("path", EXPECTED_ON_REMOTE)
def test_restored_file_matches_the_source(restored, workspace, path):
    original = (workspace["src"] / path).read_bytes()
    assert (restored / path).read_bytes() == original


@pytest.mark.parametrize("path", EXCLUDED_BY_FILTER_RULES)
def test_excluded_file_is_absent_after_restore(restored, path):
    assert not (restored / path).exists()


def test_backup_is_idempotent(env_file, backup):
    """Running it twice must succeed and change nothing on the remote.

    Digests are compared as well as paths: gocryptfs reverse mode derives file
    IDs deterministically so that unchanged files produce identical ciphertext,
    which is what keeps incremental rsync cheap. A listing-only check would
    miss a regression there.
    """
    before = remote_manifest()
    assert before, "the remote should not be empty before the second backup"

    docker_rm(MAKE_CONTAINER)
    second = run_make("backup", env_file)
    assert second.returncode == 0, second.stdout

    assert remote_manifest() == before
