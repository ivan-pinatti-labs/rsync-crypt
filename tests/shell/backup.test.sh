#!/usr/bin/env bash
#
# Tests for scripts/backup.sh. Every line of it has to run in one of these
# cases: `make coverage` runs tests/shell/run.sh under kcov and fails below
# 100%. gocryptfs, rsync, fusermount and sleep are stand-ins (see lib.sh),
# so what is checked is the script's own logic: which arguments it builds,
# which branch it takes and what it says. The real tools are exercised by
# the Docker roundtrip in tests/test_roundtrip.py.

# shellcheck source=tests/shell/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The -init call writes the config gocryptfs would, into the source
# directory it was given (the first argument that is a directory).
stub gocryptfs 'if [[ "${2:-}" = "-init" ]]; then
  for arg in "$@"; do
    if [[ -d "${arg}" ]]; then touch "${arg}/.gocryptfs.reverse.conf"; break; fi
  done
fi'
stub rsync
stub fusermount
stub sleep

src="${__scratch}/src"
enc="${__scratch}/enc"
pass="${__scratch}/passfile"
rules="${__scratch}/rules.txt"
mounts="${__scratch}/mountinfo"

# A source with data in it, an empty encrypted mount point and a passfile.
# `fresh initialized` adds the reference copy of the gocryptfs config that a
# previous run leaves behind.
fresh() {
  rm -rf "${src}" "${enc}"
  mkdir "${src}" "${enc}"
  echo data >"${src}/file.txt"
  echo secret >"${pass}"
  if [[ "${1:-}" = "initialized" ]]; then
    echo conf >"${src}/.gocryptfs.reverse.conf.original"
  fi
}

# backup <cipher> <encrypt names> <rsync loop>, with the source given as
# ${source_arg} (default ${src}).
backup() {
  run backup.sh "${source_arg:-${src}}" "${enc}" /remote/backup "${pass}" \
    user@host "${rules}" 0 "${3:-true}" "${1:-aes-gcm}" 10 "${2:-true}"
}

# A mount table with every kind of line the detection has to get right, under
# a source of ${src}: the root, the source's own mount, a blank line, a local
# mount under the source, network mounts under it (one with each escape the
# kernel writes, one listed twice), and a network mount elsewhere.
cat >"${mounts}" <<EOF
24 1 259:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw
30 24 0:24 / ${src} rw,relatime shared:2 - btrfs /dev/sda3 rw

31 30 0:25 / ${src}/disk rw,relatime shared:3 - ext4 /dev/sdb1 rw
32 30 0:41 / ${src}/nas rw,relatime shared:4 master:1 - cifs //server/share rw
33 30 0:42 / ${src}/my\\040share\\011tab\\012line\\134back rw - nfs4 server:/x rw
34 30 0:41 / ${src}/nas rw,relatime - cifs //server/share rw
35 24 0:43 / /mnt/elsewhere rw,relatime - fuse.sshfs host:/ rw
EOF

__run_path="$(path_without gocryptfs fusermount)" run backup.sh
check "a missing binary exits 2" 2 out "Error! gocryptfs is missing..."

BACKUP_EXCLUDE_NETWORK_MOUNTS=maybe run backup.sh
check "an unknown network mount setting aborts" 1 out "Unknown BACKUP_EXCLUDE_NETWORK_MOUNTS 'maybe'"

fresh
touch "${enc}/leftover"
backup
check "a non empty encrypted directory aborts" 1 out "must be empty!"

fresh
rm "${pass}"
backup
check "a missing passfile aborts" 1 out "Gocryptfs passfile NOT found"

fresh
rm "${src}/file.txt"
backup
check "an empty source aborts" 1 out "cannot be empty"

# First run: names encrypted, a typo at the master key prompt, and every
# network mount in the table excluded once. The trailing slash on the source
# must not reach the relative paths.
fresh
printf 'x\nO\n' >"${__scratch}/stdin"
DEBUG=true RSYNC_CRYPT_TEST_MOUNTINFO="${mounts}" source_arg="${src}/" backup
check "first run initializes with scrambled names" 0 out "filenames will be scrambled on remote"
check "first run passes -aessiv and the scrypt cost" 0 calls "gocryptfs -reverse -init -aessiv -scryptn 10 ${src}/ -passfile ${pass}"
check "first run keeps a reference copy of the config" 0 out "Reference copy:"
check "a network mount is excluded" 0 calls "-reverse -exclude nas -exclude my share"
check "a mount listed twice is excluded once" 0 out "2 network-backed mount(s) excluded"
check "mountinfo escapes are decoded" 0 calls "-exclude my share"$'\t'"tab"
check "first run completes" 0 out "rsync succeeded"
[[ ! -e "${enc}" ]] || fail "the encrypted directory was left behind"

# First run with plaintext names, the other spelling of the cipher, a
# passphrase typed rather than read from a file, and no network mounts.
fresh
echo O >"${__scratch}/stdin"
exits rsync 23
head -n 2 "${mounts}" >"${__scratch}/local-mountinfo"
PARANOID_MODE=true RSYNC_CRYPT_TEST_MOUNTINFO="${__scratch}/local-mountinfo" \
  backup aes-siv false
check "plaintext names pass -plaintextnames" 0 calls "gocryptfs -reverse -init -plaintextnames -aessiv"
check "paranoid mode asks for the passphrase" 0 out "PARANOID MODE"
check "no network mounts under the source" 0 out "No network-backed mounts found"
check "rsync exit 23 is a warning" 0 out "rsync completed with warnings (exit 23)"

fresh
backup xchacha
check "xchacha is refused" 1 out "GOCRYPTFS_CIPHER=xchacha cannot work"

fresh
backup bogus
check "an unknown cipher is refused" 1 out "Unknown GOCRYPTFS_CIPHER 'bogus'"

# A config from before the reference copy existed is recovered, not
# re-initialized.
fresh
echo conf >"${src}/.gocryptfs.reverse.conf"
echo >"${__scratch}/stdin"
exits gocryptfs 1
BACKUP_EXCLUDE_NETWORK_MOUNTS=false backup
check "an existing config is recovered" 1 out "Recovering existing gocryptfs config"
check "false skips network mount detection" 1 out "BACKUP_EXCLUDE_NETWORK_MOUNTS=false"
check "a failed mount stops the backup" 1 out "gocryptfs failed"
[[ -s "${src}/.gocryptfs.reverse.conf.original" ]] || fail "the recovered config was not kept"

fresh initialized
RSYNC_CRYPT_TEST_MOUNTINFO="${__scratch}/absent" backup
check "an initialized source restores its config" 1 out "already initialized for gocryptfs usage"
check "an unreadable mount table stops the backup" 1 out "Cannot read the mount table"

fresh initialized
echo "not a mountinfo line" >"${__scratch}/bad-mountinfo"
RSYNC_CRYPT_TEST_MOUNTINFO="${__scratch}/bad-mountinfo" backup
check "an unparseable mount table stops the backup" 1 out "line 1 is not valid mountinfo"

fresh initialized
echo >"${__scratch}/stdin"
exits rsync 12
RSYNC_CRYPT_TEST_MOUNTINFO="${mounts}" backup aes-gcm true false
check "a failed rsync without the loop stops" 1 out "rsync failed (exit 12)"
check "a failed rsync unmounts the view" 1 calls "fusermount -u ${enc}"

fresh initialized
exits rsync 12 0
RSYNC_CRYPT_TEST_MOUNTINFO="${mounts}" backup
check "a failed rsync with the loop retries" 0 out "rsync failed (exit 12), retrying in 5s"
check "the retry waits first" 0 calls "sleep 5"
check "the retry succeeds" 0 out "rsync succeeded"

finish
