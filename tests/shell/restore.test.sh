#!/usr/bin/env bash
#
# Tests for scripts/restore.sh, held to 100% of its lines by `make coverage`
# the same way as backup.test.sh. gocryptfs, rsync, fusermount and sleep are
# stand-ins (see lib.sh).

# shellcheck source=tests/shell/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

stub gocryptfs
stub rsync
stub fusermount
stub sleep

enc="${__scratch}/enc"
dec="${__scratch}/dec"
dest="${__scratch}/dest"
paths="${__scratch}/restore-paths.txt"

# An encrypted copy as the pull leaves it (with its gocryptfs.conf), an empty
# mount point for the decrypted view, and a paths file with nothing active.
fresh() {
  rm -rf "${enc}" "${dec}" "${dest}"
  mkdir "${enc}" "${dec}" "${dest}"
  touch "${enc}/gocryptfs.conf"
  printf '# a comment\n\n' >"${paths}"
}

# restore [<rsync loop>]
restore() {
  run restore.sh user@host /remote/backup "${enc}" "${dec}" "${__scratch}/passfile" \
    "${__scratch}/exclude.txt" 0 "${1:-true}" "${dest}" "${paths}"
}

__run_path="$(path_without rsync fusermount gocryptfs)" run restore.sh
check "a missing binary exits 2" 2 out "Error! rsync is missing..."

# RESTORE_PATHS replaces the paths file, one path per word.
fresh
DEBUG=true RESTORE_PATHS="home/a home/b" restore
check "RESTORE_PATHS restores only the listed paths" 0 out "Selective restore: restoring listed paths to ${dest}"
check "the pull mirrors the remote copy" 0 calls "rsync --bwlimit=0 -P -a -z --stats -h --delete --exclude-from=${__scratch}/exclude.txt user@host:/remote/backup/ ${enc}"
check "the view is mounted with the passfile" 0 calls "gocryptfs -ro -nosyslog -passfile ${__scratch}/passfile ${enc} ${dec}"
check "the view is unmounted afterwards" 0 out "Restore complete. Files are in: ${dest}"

fresh
restore
check "a paths file with nothing active restores everything" 0 out "Full restore: restoring everything to ${dest}"

fresh
rm "${enc}/gocryptfs.conf"
exits rsync 24
restore
check "rsync exit 24 is a warning" 1 out "rsync completed with warnings (exit 24)"
check "no gocryptfs.conf, nothing to decrypt" 1 out "gocryptfs.conf not found, cannot decrypt."

fresh
exits rsync 12
restore false
check "a failed rsync without the loop stops" 1 out "rsync failed (exit 12)"

fresh
touch "${dec}/leftover"
exits rsync 12 0
restore
check "a failed rsync with the loop retries" 1 out "rsync failed (exit 12), retrying in 5s"
check "the retry waits first" 1 calls "sleep 5"
check "a non empty mount point aborts" 1 out "${dec} must be empty before mounting."

fresh
exits gocryptfs 1
PARANOID_MODE=true restore
check "paranoid mode asks for the passphrase" 1 out "PARANOID MODE"
check "paranoid mode passes no passfile" 1 calls "gocryptfs -ro -nosyslog ${enc} ${dec}"
check "a failed mount stops the restore" 1 out "gocryptfs failed"

finish
