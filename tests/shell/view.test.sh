#!/usr/bin/env bash
#
# Tests for scripts/view.sh, held to 100% of its lines by `make coverage`
# the same way as backup.test.sh. sshfs, gocryptfs, fusermount, ssh-keygen,
# sshd, pkill and sleep are stand-ins (see lib.sh). The script's two fixed
# container paths, the SSH key directory and the sshd binary, point into the
# scratch directory through their RSYNC_CRYPT_TEST_* overrides.

# shellcheck source=tests/shell/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The sshfs stand-in mounts a copy holding gocryptfs.conf when
# __scratch/remote-has-conf exists, the way a real backup would look.
stub sshfs "if [[ -f '${__scratch}/remote-has-conf' ]]; then touch \"\${2}/gocryptfs.conf\"; fi"
stub gocryptfs
stub fusermount
stub ssh-keygen 'if [[ "${1}" = "-y" ]]; then echo "ssh-ed25519 AAAA test"; fi'
stub sshd
stub pkill
stub sleep

enc="${__scratch}/enc"
dec="${__scratch}/dec"
export RSYNC_CRYPT_TEST_SSH_DIR="${__scratch}/ssh"
export RSYNC_CRYPT_TEST_SSHD="${__bin}/sshd"

fresh() {
  rm -rf "${enc}" "${dec}" "${RSYNC_CRYPT_TEST_SSH_DIR}" "${__scratch}/remote-has-conf"
  mkdir "${enc}" "${dec}" "${RSYNC_CRYPT_TEST_SSH_DIR}"
  touch "${__scratch}/remote-has-conf"
}

view() {
  run view.sh user@host /remote/backup "${__scratch}/passfile" "${enc}" "${dec}"
}

__run_path="$(path_without ssh-keygen fusermount gocryptfs sshfs)" run view.sh
check "a missing binary exits 2" 2 out "Error! ssh-keygen is missing..."

fresh
touch "${dec}/leftover"
view
check "a non empty mount point aborts" 1 out "Error: ${dec} must be empty before mounting."
check "an early exit still cleans up" 1 calls "fusermount -u ${enc}"

fresh
exits sshfs 1
view
check "a failed sshfs stops the view" 1 out "sshfs failed"

fresh
rm "${__scratch}/remote-has-conf"
view
check "no gocryptfs.conf, nothing to decrypt" 1 out "gocryptfs.conf not found, cannot decrypt."

fresh
exits gocryptfs 1
PARANOID_MODE=true view
check "paranoid mode asks for the passphrase" 1 out "PARANOID MODE"
check "paranoid mode passes no passfile" 1 calls "gocryptfs -ro -nosyslog ${enc} ${dec}"
check "a failed mount stops the view" 1 out "gocryptfs failed"

fresh
echo >"${__scratch}/stdin"
DEBUG=true view
check "the remote is mounted with the mounted key" 0 calls "sshfs user@host:/remote/backup ${enc} -o IdentityFile=${RSYNC_CRYPT_TEST_SSH_DIR}/id_rsa"
check "the view is mounted with the passfile" 0 calls "gocryptfs -ro -nosyslog -passfile ${__scratch}/passfile ${enc} ${dec}"
check "sshd serves the view" 0 calls "sshd -f /"
check "the view is announced" 0 out "sftp://root@localhost:2222${dec}"
check "the view is torn down on Enter" 0 out "Unmounted. Exiting."
grep --quiet "ssh-ed25519 AAAA test" "${RSYNC_CRYPT_TEST_SSH_DIR}/authorized_keys" ||
  fail "authorized_keys was not derived from the key"
[[ "$(grep -c '^pkill -f internal-sftp' "${__calls}")" -eq 1 ]] ||
  fail "teardown ran more than once"

finish
