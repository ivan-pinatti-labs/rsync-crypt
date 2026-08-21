#!/usr/bin/env bash

: ' Script to encrypt and backup files/folders
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
__backup_source=${1:-"/backup/src"}                               # local data to be backed up
__backup_encrypted_folder=${2:-"/backup/enc"}                     # encrypted virtual read-only directory
__backup_destination_folder=${3:-"/remote/backup"}                # folder in the remote server
__backup_passkey_file=${4:-"/backup/passfile"}                    # gocryptfs master key
__remote_server=${5:-"user@x.x.x.x"}                              # replace x.x.x.x with the remote server's IP or host name
__backup_filter_rules_file=${6:-"/backup/brave-filter-rules.txt"} # filter rules file (include + exclude patterns)
__rsync_rate_limit=${7:-0}                                        # maximum transfer rate (kbytes/s), 0 means no limit
__rsync_loop=${8:-true}                                           # rsync loop, helpful with not stable internet connections, it can be true or false
__gocryptfs_cipher=${9:-"aes-gcm"}                                # encryption cipher: aes-gcm (default), aes-siv, xchacha (only applied on first init)
__gocryptfs_scrypt_n=${10:-16}                                    # scrypt key derivation cost exponent: 2^N iterations (only applied on first init)
__gocryptfs_encrypt_names=${11:-true}                             # encrypt filenames: true (default, names scrambled), false (plaintext names)

# constants
readonly __backup_destination=${__remote_server}:${__backup_destination_folder} # rsync destination dir

# display rsync rate
echo "rsync --bwlimit set to ${__rsync_rate_limit} kbytes/s (0 means no limit)"

# wait for Docker to truly finish mounting volumes
sleep 5

#===============================================================
# Set a trap for CTRL+C to properly exit
#===============================================================

trap 'echo "Backup interrupted, cleaning up..."; fusermount -u ${__backup_encrypted_folder} 2>/dev/null || true; rm -rf ${__backup_encrypted_folder} 2>/dev/null || true; exit 3' SIGINT SIGTERM

#===============================================================
# Mount and rsync virtual encrypted directory
#===============================================================

if [ -n "$(find "${__backup_encrypted_folder}" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "The encrypted virtual directory ${__backup_encrypted_folder} must be empty!"
  exit 1
fi

if [ "${__paranoid_mode}" = "true" ]; then
  echo "PARANOID MODE: passphrase will be entered interactively."
  __gocryptfs_passfile_args=()
elif test -f "${__backup_passkey_file}"; then
  echo "Gocryptfs passfile found, proceeding..."
  __gocryptfs_passfile_args=(-passfile "${__backup_passkey_file}")
else
  echo "ERROR! Gocryptfs passfile NOT found, aborting..."
  exit 1
fi

if [ -n "$(find "${__backup_source}" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
  echo "The unencrypted directory ${__backup_source} contains local data to be backed up..."
else
  echo "The unencrypted directory ${__backup_source} cannot be empty, it must contain local data to be backed up..."
  exit 1
fi

if [ ! -s "${__backup_source}/.gocryptfs.reverse.conf.original" ]; then
  if [ -f "${__backup_source}/.gocryptfs.reverse.conf" ]; then
    echo "Recovering existing gocryptfs config: saving as .gocryptfs.reverse.conf.original"
    cp "${__backup_source}/.gocryptfs.reverse.conf" \
      "${__backup_source}/.gocryptfs.reverse.conf.original"
    chmod 600 "${__backup_source}/.gocryptfs.reverse.conf.original"
  else
    echo "================================================================"
    echo "  FIRST-TIME INITIALIZATION"
    echo "  gocryptfs is about to display the MASTER KEY."
    echo "  Write it down and store it securely BEFORE pressing O."
    echo "  The master key is never stored to disk; it is shown ONLY ONCE."
    echo "  Without it, a lost passphrase makes the backup unrecoverable."
    echo "================================================================"
    if [ "${__gocryptfs_encrypt_names}" = "true" ]; then
      echo "Initializing encrypted view of ${__backup_source} (filenames will be scrambled on remote)."
      __plaintextnames_flag=()
    else
      echo "Initializing encrypted view of ${__backup_source} (filenames visible on remote)."
      __plaintextnames_flag=(-plaintextnames)
    fi
    case "${__gocryptfs_cipher}" in
    "aes-siv") __cipher_flag=(-aessiv) ;;
    "xchacha") __cipher_flag=(-xchacha) ;;
    *) __cipher_flag=() ;;
    esac
    gocryptfs -reverse -init \
      "${__plaintextnames_flag[@]}" \
      "${__cipher_flag[@]}" \
      -scryptn "${__gocryptfs_scrypt_n}" \
      "${__backup_source}" "${__gocryptfs_passfile_args[@]}"
    cp "${__backup_source}/.gocryptfs.reverse.conf" "${__backup_source}/.gocryptfs.reverse.conf.original"
    chmod 600 "${__backup_source}/.gocryptfs.reverse.conf" \
      "${__backup_source}/.gocryptfs.reverse.conf.original"
    echo ""
    echo "================================================================"
    echo "  Config saved:    ${__backup_source}/.gocryptfs.reverse.conf"
    echo "  Reference copy:  ${__backup_source}/.gocryptfs.reverse.conf.original"
    echo "  Back up both files and your passphrase file off-machine."
    echo "================================================================"
    read -r -p "Press O once you have saved the master key shown above: " input
    while [[ "$input" != "O" && "$input" != "o" ]]; do
      read -r -p "Please press O to confirm the master key is saved: " input
    done
    printf '\033c' # clear the terminal so the master key is no longer visible
  fi
else
  cp "${__backup_source}/.gocryptfs.reverse.conf.original" "${__backup_source}/.gocryptfs.reverse.conf"
  echo "The unencrypted directory ${__backup_source} is already initialized for gocryptfs usage."
fi

# mount read-only encrypted virtual copy of unencrypted local data:
if gocryptfs -ro -nosyslog "${__gocryptfs_passfile_args[@]}" -reverse \
  "${__backup_source}" "${__backup_encrypted_folder}"; then
  echo "gocryptfs succeeded -> the decrypted dir ${__backup_source} is virtually encrypted in ${__backup_encrypted_folder}"
else
  echo "gocryptfs failed"
  # shellcheck disable=SC2162
  read -p "Press Enter to exit..."
  exit 1
fi

__retry_delay=5
__retry_delay_max=300

while true; do
  # rsync local encrypted virtual copy of data to destination dir:
  __rsync_exit=0
  rsync --bwlimit="${__rsync_rate_limit}" -a -z -h --delete --delete-excluded --filter=". ${__backup_filter_rules_file}" --info=progress2,stats2,name0 "${__backup_encrypted_folder}"/ "${__backup_destination}" || __rsync_exit=$?
  if [ "${__rsync_exit}" -eq 0 ]; then
    echo "rsync succeeded -> a full encrypted copy of ${__backup_source} is ready in ${__backup_destination}"
    break
  elif [ "${__rsync_exit}" -eq 23 ] || [ "${__rsync_exit}" -eq 24 ]; then
    echo "rsync completed with warnings (exit ${__rsync_exit}): some files were skipped (locked, unreadable, or vanished during transfer)."
    echo "The backup is otherwise complete. Skipped files will be retried on the next backup run."
    break
  else
    if ! ${__rsync_loop}; then
      echo "rsync failed (exit ${__rsync_exit})"
      fusermount -u "${__backup_encrypted_folder}"
      # shellcheck disable=SC2162
      read -p "Press Enter to exit..."
      exit 1
    fi
    echo "rsync failed (exit ${__rsync_exit}), retrying in ${__retry_delay}s..."
    sleep "${__retry_delay}"
    __retry_delay=$((__retry_delay * 2 > __retry_delay_max ? __retry_delay_max : __retry_delay * 2))
  fi
done

# unmount encrypted virtual copy of local data :
fusermount -u "${__backup_encrypted_folder}"

# remove encrypted virtual directory
rm -rf "${__backup_encrypted_folder}"

# clean exit
exit 0
