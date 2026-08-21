#!/usr/bin/env bash

: ' Script to decrypt and restore files/folders
    # exit(s) status code(s)
    0 - success
    1 - fail
    2 - binary is missing
    3 - user cancelled
    '

# check if debug flag is set
if [ "${DEBUG}" = true ]; then

  set -x # enable print commands and their arguments as they are executed.
  export # show all declared variables (includes system variables)
  whoami # print current user

else

  # unset if flag is not set
  unset DEBUG

fi

# bash default parameters
set -o errexit  # make your script exit when a command fails
set -o pipefail # exit status of the last command that threw a non-zero exit code is returned
set -o nounset  # exit when your script tries to use undeclared variables

# read paranoid mode flag (passed by Docker via --env PARANOID_MODE)
__paranoid_mode=${PARANOID_MODE:-false}

# binaries list
__BINARIES_LIST__="fusermount"
__BINARIES_LIST__+=" gocryptfs"
__BINARIES_LIST__+=" rsync"

# check binaries before proceeding
for binary in ${__BINARIES_LIST__}; do
  if ! which "${binary}" >/dev/null; then
    echo "Error! ${binary} is missing..."
    exit 2
  fi
done

# parameters
__remote_server=${1:-"user@x.x.x.x"}                                     # replace x.x.x.x with the remote server's IP or host name
__backup_remote_folder=${2:-"/restore/origin"}                           # folder in the remote server
__restore_encrypted_folder=${3:-"/restore/enc"}                          # encrypted local copy
__restore_decrypted_folder=${4:-"/restore/dec"}                          # decrypted read-only virtual directory
__restore_passkey_file=${5:-"/restore/passfile"}                         # gocryptfs master key
__restore_exclude_pattern_file=${6:-"/restore/restore-exclude-list.txt"} # paths to exclude when pulling from remote
__rsync_rate_limit=${7:-0}                                               # maximum transfer rate (kbytes/s), 0 means no limit
__rsync_loop=${8:-true}                                                  # rsync loop for unstable connections
__restore_destination=${9:-"/restore/origin"}                            # destination path for restored files
__restore_paths_file=${10:-"/restore/restore-paths.txt"}                 # paths to selectively restore (empty = full restore)

# constants
readonly __restore_origin="${__remote_server}:${__backup_remote_folder}" # rsync origin dir

# RESTORE_PATHS env var override: if set, write to temp file and use instead of paths file
if [ -n "${RESTORE_PATHS:-}" ]; then
  printf '%s' "${RESTORE_PATHS}" | tr ' ' '\n' >/tmp/restore-paths-override.txt
  __restore_paths_file="/tmp/restore-paths-override.txt"
fi

# display rsync rate
echo "rsync --bwlimit set to ${__rsync_rate_limit} kbytes/s (0 means no limit)"

#===============================================================
# Set a trap for CTRL+C to properly exit
#===============================================================

trap 'echo "Restore interrupted, cleaning up..."; fusermount -u ${__restore_decrypted_folder} 2>/dev/null || true; exit 3' SIGINT SIGTERM

#===============================================================
# Pull encrypted data from remote
#===============================================================

__retry_delay=5
__retry_delay_max=300

while true; do
  # rsync remote encrypted copy of data to local copy:
  __rsync_exit=0
  rsync --bwlimit="${__rsync_rate_limit}" -P -a -z --stats -h --delete --exclude-from="${__restore_exclude_pattern_file}" "${__restore_origin}"/ "${__restore_encrypted_folder}" || __rsync_exit=$?
  if [ "${__rsync_exit}" -eq 0 ]; then
    echo "rsync succeeded -> encrypted data from ${__restore_origin} is ready in ${__restore_encrypted_folder}"
    break
  elif [ "${__rsync_exit}" -eq 23 ] || [ "${__rsync_exit}" -eq 24 ]; then
    # 23 is a partial transfer, 24 is files that vanished mid transfer. Neither
    # is retriable: the next attempt hits the same unreadable or already gone
    # files and returns the same status, so looping on them never terminates.
    # backup.sh has treated these as success-with-warning since they caused
    # exactly that infinite loop there; restore.sh was missed at the time.
    echo "rsync completed with warnings (exit ${__rsync_exit}): some files were skipped (locked, unreadable, or vanished during transfer)."
    echo "The restore is otherwise complete."
    break
  else
    if ! ${__rsync_loop}; then
      echo "rsync failed (exit ${__rsync_exit})"
      exit 1
    fi
    # Back off between attempts. This loop previously had no delay at all, so
    # a persistent failure spun as fast as rsync could return, hammering the
    # remote. Mirrors backup.sh: 5s doubling to a 300s ceiling.
    echo "rsync failed (exit ${__rsync_exit}), retrying in ${__retry_delay}s..."
    sleep "${__retry_delay}"
    __retry_delay=$((__retry_delay * 2 > __retry_delay_max ? __retry_delay_max : __retry_delay * 2))
  fi
done

#===============================================================
# Decrypt (mount read-only virtual view)
#===============================================================

if ! test -f "${__restore_encrypted_folder}"/gocryptfs.conf; then
  echo "Error: ${__restore_encrypted_folder}/gocryptfs.conf not found, cannot decrypt."
  exit 1
fi

if find "${__restore_decrypted_folder}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | read -r _; then
  echo "Error: ${__restore_decrypted_folder} must be empty before mounting."
  echo "If a previous session left it mounted, run: fusermount -u ${__restore_decrypted_folder}"
  exit 1
fi

if [ "${__paranoid_mode}" = "true" ]; then
  echo "PARANOID MODE: passphrase will be entered interactively."
  __gocryptfs_passfile_args=()
else
  __gocryptfs_passfile_args=(-passfile "${__restore_passkey_file}")
fi

if ! gocryptfs -ro -nosyslog "${__gocryptfs_passfile_args[@]}" \
  "${__restore_encrypted_folder}" "${__restore_decrypted_folder}"; then
  echo "gocryptfs failed"
  exit 1
fi

echo "Decrypted read-only view mounted at ${__restore_decrypted_folder}"

#===============================================================
# Restore
#===============================================================

# read active (non-comment, non-blank) lines from paths file
_active_paths=$(grep -v '^\s*#' "${__restore_paths_file}" 2>/dev/null | grep -v '^\s*$' || true)

if [ -n "${_active_paths}" ]; then
  echo "Selective restore: restoring listed paths to ${__restore_destination}"
  rsync -a --bwlimit="${__rsync_rate_limit}" \
    --files-from=<(echo "${_active_paths}") \
    "${__restore_decrypted_folder}/" \
    "${__restore_destination}/"
else
  echo "Full restore: restoring everything to ${__restore_destination}"
  rsync -a --bwlimit="${__rsync_rate_limit}" \
    "${__restore_decrypted_folder}/" \
    "${__restore_destination}/"
fi

fusermount -u "${__restore_decrypted_folder}"
echo "Restore complete. Files are in: ${__restore_destination}"

exit 0
