"""GOCRYPTFS_CIPHER must resolve to AES-SIV or refuse to run.

Backups use gocryptfs reverse mode, which needs deterministic nonces so an
unchanged file encrypts to the same bytes and rsync only moves what changed.
The gocryptfs manual states that -reverse implies -aessiv, because AES-SIV is
the mode that stays secure when nonces repeat. Two consequences the config used
to hide:

- aes-gcm and aes-siv are the same thing here. Selecting aes-gcm never produced
  AES-GCM.
- xchacha cannot work at all. XChaCha20-Poly1305 is unsafe with repeated
  nonces, so gocryptfs refuses to combine it with AES-SIV and init aborts. The
  old code passed -xchacha through and let gocryptfs fail with its own message
  about feature flags, which does not tell the reader why.

These tests execute the real `case` block lifted out of backup.sh rather than
asserting on its source text, so they check behaviour: the flag chosen, the
exit status, and that the message explains the cause. The happy path is also
covered end to end by test_roundtrip.py, whose env file sets aes-gcm.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from conftest import REPO_ROOT

BACKUP_SH = REPO_ROOT / "scripts" / "backup.sh"


def _cipher_case_block():
    """The `case "${__gocryptfs_cipher}" in ... esac` block from backup.sh.

    Lifted rather than duplicated so the test cannot drift from the script.
    """
    body = BACKUP_SH.read_text()
    match = re.search(
        r'^\s*case "\$\{__gocryptfs_cipher\}" in$.*?^\s*esac$',
        body,
        re.MULTILINE | re.DOTALL,
    )
    assert match, "the cipher case block was not found in backup.sh"
    return match.group(0)


def _cipher_assignment():
    """backup.sh's own `__gocryptfs_cipher=${9:-"..."}` line, comment stripped.

    Taken from the script so the default cannot drift, and so the test runs the
    same normalisation the script does. That matters: an empty ninth argument
    never reaches the case block as an empty string, because `${9:-...}`
    substitutes the default for an empty value as well as a missing one.
    """
    body = BACKUP_SH.read_text()
    # Deliberately permissive about what is inside the braces. The assertions
    # below decide whether the expansion is correct; if this pattern insisted on
    # `:-` with a non-empty default it would fail to match a changed line and
    # every test in the file would error on extraction instead of reporting
    # which behaviour broke.
    match = re.search(r"^__gocryptfs_cipher=\$\{9[^}]*\}", body, re.MULTILINE)
    assert match, "the __gocryptfs_cipher assignment was not found in backup.sh"
    return match.group(0)


def _run_case(value):
    """Run the real pipeline: ninth positional argument, then the case block.

    `value` is passed as `$9` exactly as the Makefile would pass it, so the
    default substitution is exercised rather than bypassed.
    """
    script = (
        "set -o errexit -o pipefail -o nounset\n"
        f"{_cipher_assignment()}\n"
        "__cipher_flag=()\n"
        f"{_cipher_case_block()}\n"
        'printf "FLAG:%s\\n" "${__cipher_flag[*]-}"\n'
    )
    # $0 then eight placeholders, so `value` lands in $9.
    argv = ["bash", "-c", script, "backup.sh", *(["_"] * 8), value]
    # check=False on purpose: the exit status is what these tests assert on.
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)


@pytest.mark.parametrize("value", ["aes-gcm", "aes-siv"])
def test_accepted_values_select_aessiv(value):
    """Both accepted spellings resolve to -aessiv, which is the only option."""
    result = _run_case(value)
    assert result.returncode == 0, (
        f"{value} was rejected, but reverse mode supports it: {result.stdout}"
        f"{result.stderr}"
    )
    assert "FLAG:-aessiv" in result.stdout, (
        f"{value} did not select -aessiv; got {result.stdout!r}. Reverse mode "
        f"implies AES-SIV, so anything else cannot be what was configured"
    )


def test_aes_gcm_and_aes_siv_are_indistinguishable():
    """The two spellings must not diverge: they name one cipher."""
    gcm = _run_case("aes-gcm")
    siv = _run_case("aes-siv")
    assert gcm.stdout == siv.stdout, (
        "aes-gcm and aes-siv produced different flags, which would mean the "
        "setting now changes the cipher. Reverse mode implies AES-SIV, so it "
        f"cannot: {gcm.stdout!r} vs {siv.stdout!r}"
    )


def test_xchacha_is_refused_with_the_reason():
    """xchacha must abort before gocryptfs emits its own opaque error."""
    result = _run_case("xchacha")
    assert result.returncode == 1, (
        f"xchacha exited {result.returncode}, expected 1. Passing -xchacha "
        f"through only defers the failure to gocryptfs"
    )
    combined = result.stdout + result.stderr
    assert "-xchacha" not in combined, (
        "the flag was still selected; xchacha must be rejected, not passed on"
    )
    # The message has to explain the cause, not merely refuse. Someone hitting
    # this chose xchacha for a CPU without AES acceleration and needs to know
    # no setting can give them that here.
    for phrase in ("AES-SIV", "nonce"):
        assert phrase.lower() in combined.lower(), (
            f"the xchacha message never mentions {phrase!r}, so it does not "
            f"explain why the value cannot work: {combined!r}"
        )


def test_empty_argument_falls_back_to_the_default():
    """An empty ninth argument must take the default, not abort.

    `${9:-"aes-gcm"}` substitutes the default for an empty value as well as a
    missing one, so an unset GOCRYPTFS_CIPHER stays working. An earlier version
    of this file asserted that empty aborts, which was a contract the script
    never had: it only looked true because the harness set the variable directly
    and skipped the substitution.
    """
    result = _run_case("")
    assert result.returncode == 0, (
        f"an empty cipher aborted, but the default should apply: "
        f"{result.stdout}{result.stderr}"
    )
    assert "FLAG:-aessiv" in result.stdout, (
        f"the default did not resolve to -aessiv; got {result.stdout!r}"
    )


@pytest.mark.parametrize("value", ["aes_siv", "AES-SIV", "16", "xchacha20"])
def test_unrecognised_values_abort(value):
    """Silent fallback is what let a shifted argument look like a cipher.

    `16` is the realistic case: the Makefile expands these variables unquoted,
    so a blank one disappears instead of passing as empty and every later
    positional shifts, landing GOCRYPTFS_SCRYPT_N in the cipher slot.
    """
    result = _run_case(value)
    assert result.returncode == 1, (
        f"{value!r} was accepted and exited {result.returncode}; an "
        f"unrecognised cipher must abort rather than fall back silently"
    )
