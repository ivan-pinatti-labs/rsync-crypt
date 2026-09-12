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
__gocryptfs_cipher=${9:-"aes-gcm"}                                # cipher: aes-gcm or aes-siv, equivalent here (reverse mode implies AES-SIV); only applied on first init
__gocryptfs_scrypt_n=${10:-16}                                    # scrypt key derivation cost exponent: 2^N iterations (only applied on first init)
__gocryptfs_encrypt_names=${11:-true}                             # encrypt filenames: true (default, names scrambled), false (plaintext names)

# read the network-mount exclusion flag (passed by Docker via --env
# BACKUP_EXCLUDE_NETWORK_MOUNTS). An environment variable rather than a
# twelfth positional argument, so an env file written before this existed
# simply leaves it unset and gets the default instead of shifting every
# later argument.
# --- BEGIN network-mount flag (lifted by tests/test_network_mounts.py) ---
__exclude_network_mounts=${BACKUP_EXCLUDE_NETWORK_MOUNTS:-true}

case "${__exclude_network_mounts}" in
"true" | "false") ;;
*)
  echo "ERROR! Unknown BACKUP_EXCLUDE_NETWORK_MOUNTS '${__exclude_network_mounts}', aborting..."
  echo "  Valid values are true (the default: mount points under the backup"
  echo "  source that live on another machine are skipped) and false (back"
  echo "  them up like any other directory)."
  echo "  Unset and empty both mean true, so the safer behaviour applies to an"
  echo "  env file written before this setting existed."
  exit 1
  ;;
esac
# --- END network-mount flag ---

#===============================================================
# Network-backed mounts
#
# A mount point nested under the backup source whose storage lives on another
# machine (a NAS share, an sshfs or rclone mount, an object-store bucket) is
# data this tool would otherwise read over the network, re-encrypt and push
# to the remote server. That is slow, it doubles the copy, and it is almost
# never what "back up this machine" is meant to include, so it is skipped by
# default.
#
# Linux exposes no "this filesystem is remote" property, so the only honest
# test is the filesystem type and the list below is therefore explicit: a
# type not named here is treated as local. Bind mounts, tmpfs, ext4, XFS,
# Btrfs and removable drives are all backed up exactly as before. 9p is
# deliberately absent even though it is a wire protocol: it is how a VM or
# WSL sees a host directory, which is frequently where the user's own data
# lives, so classifying it as network would silently drop the backup.
#
# The exclusions are handed to gocryptfs itself (-exclude, which takes a path
# relative to the reverse-mount root) rather than written out as rsync filter
# rules, so they hold whether or not filename encryption is on: rsync sees
# ciphertext names when GOCRYPTFS_ENCRYPT_NAMES=true and no filter rule can
# match them, but gocryptfs excludes on the plaintext path before encrypting
# it. rsync's --one-file-system is not used either: it would also drop every
# local filesystem mounted under the source.
#===============================================================

# --- BEGIN network-mount detection (lifted by tests/test_network_mounts.py) ---
__NETWORK_FS_TYPES__="cifs smb3 smbfs"
__NETWORK_FS_TYPES__+=" nfs nfs4"
__NETWORK_FS_TYPES__+=" fuse.sshfs fuse.rclone fuse.s3fs fuse.gcsfuse fuse.goofys"
__NETWORK_FS_TYPES__+=" davfs davfs2 fuse.davfs"
__NETWORK_FS_TYPES__+=" ceph cephfs glusterfs fuse.glusterfs lustre"
__NETWORK_FS_TYPES__+=" afs coda"
__NETWORK_FS_TYPES__+=" fuse.gvfsd-fuse fuse.curlftpfs"

# Whether a mountinfo filesystem type is one of the network-backed ones.
__is_network_fs_type() {
  case " ${__NETWORK_FS_TYPES__} " in
  *" ${1} "*) return 0 ;;
  *) return 1 ;;
  esac
}

# Decode the four escapes the kernel writes into a mountinfo path: space
# (\040), tab (\011), newline (\012) and backslash (\134). The result is
# returned in __unescaped_path rather than printed, because a path can end in
# a newline and command substitution would eat it.
#
# The single left-to-right pass is the point. Four search-and-replace passes
# would be wrong in a way that is easy to miss: a literal backslash is written
# \134, so a directory actually named '\040' arrives as '\134040', and
# replacing \134 first turns it into a space.
__unescape_mountinfo_path() {
  local rest=${1}

  __unescaped_path=""
  while [ -n "${rest}" ]; do
    case "${rest}" in
    '\040'*)
      __unescaped_path+=" "
      rest=${rest#\\040}
      ;;
    '\011'*)
      __unescaped_path+=$'\t'
      rest=${rest#\\011}
      ;;
    '\012'*)
      __unescaped_path+=$'\n'
      rest=${rest#\\012}
      ;;
    '\134'*)
      __unescaped_path+="\\"
      rest=${rest#\\134}
      ;;
    *)
      __unescaped_path+=${rest:0:1}
      rest=${rest:1}
      ;;
    esac
  done
}

# Fill __network_mount_types and __network_mount_paths with every
# network-backed mount strictly below ${2}, read from mount table ${1}.
# Paths are relative to ${2} and never start with a slash, which is the form
# gocryptfs -exclude takes.
#
# Returns non-zero, with an explanation, when the table cannot be read or a
# line cannot be understood. That is deliberately fatal to the backup: a
# mount table this cannot parse is a mount table it cannot prove is free of
# network storage, and quietly copying network storage is the outcome the
# whole feature exists to prevent.
__find_network_mounts() {
  local mountinfo_file=${1}
  local source_dir=${2}
  local -a fields=()
  local -A seen=()
  local line fstype mount_point relative index separator
  local line_number=0

  __network_mount_types=()
  __network_mount_paths=()

  if [ ! -r "${mountinfo_file}" ]; then
    echo "ERROR! Cannot read the mount table ${mountinfo_file}, aborting..."
    echo "  Network-backed mounts under ${source_dir} cannot be ruled out, and"
    echo "  backing them up silently is worse than stopping here. Set"
    echo "  BACKUP_EXCLUDE_NETWORK_MOUNTS=false to back up regardless."
    return 1
  fi

  # A trailing slash would leave every relative path below starting with one.
  while [ "${source_dir}" != "/" ] && [ "${source_dir}" != "${source_dir%/}" ]; do
    source_dir=${source_dir%/}
  done

  while IFS= read -r line; do
    line_number=$((line_number + 1))
    [ -n "${line}" ] || continue

    # Fields are space separated and the kernel escapes any space inside a
    # path, so plain word splitting cannot split a path in half.
    read -r -a fields <<<"${line}"

    # The optional fields sit between the mount options and the "-"
    # separator and vary in number, so the separator has to be searched for
    # rather than indexed. It cannot appear before index 6: the six mandatory
    # fields are two IDs, a major:minor pair, two paths and an option list,
    # none of which is ever a bare "-".
    separator=-1
    for index in "${!fields[@]}"; do
      if [ "${index}" -ge 6 ] && [ "${fields[index]}" = "-" ]; then
        separator=${index}
        break
      fi
    done

    # Six mandatory fields, the separator, then filesystem type, mount source
    # and super options: ten is the shortest a valid line can be.
    if [ "${separator}" -lt 0 ] || [ "${#fields[@]}" -lt 10 ]; then
      echo "ERROR! ${mountinfo_file} line ${line_number} is not valid mountinfo, aborting..."
      echo "  ${line}"
      echo "  Network-backed mounts under ${source_dir} cannot be ruled out from"
      echo "  a mount table that cannot be parsed. Set"
      echo "  BACKUP_EXCLUDE_NETWORK_MOUNTS=false to back up regardless."
      return 1
    fi

    fstype=${fields[separator + 1]}
    __is_network_fs_type "${fstype}" || continue

    __unescape_mountinfo_path "${fields[4]}"
    mount_point=${__unescaped_path}

    # Strictly below the source. The source's own mount is not an exclusion
    # (excluding it would exclude the entire backup), and a network mount
    # anywhere else on the machine is not this backup's business.
    case "${mount_point}" in
    "${source_dir}"/?*) ;;
    *) continue ;;
    esac

    relative=${mount_point#"${source_dir}/"}

    # The same directory can appear more than once: a mount can be mounted
    # over, and a mount namespace can carry several entries for one path.
    if [ -n "${seen["${relative}"]:-}" ]; then
      continue
    fi
    seen["${relative}"]=1

    __network_mount_types+=("${fstype}")
    __network_mount_paths+=("${relative}")
  done <"${mountinfo_file}"

  return 0
}

# Turn the detected mounts into gocryptfs arguments in
# __gocryptfs_exclude_args, or explain why there are none. ${1} is the flag,
# ${2} the mount table to read and ${3} the backup source.
__build_network_mount_excludes() {
  local flag=${1}
  local mountinfo_file=${2}
  local source_dir=${3}
  local index

  __gocryptfs_exclude_args=()

  case "${flag}" in
  "false")
    echo "BACKUP_EXCLUDE_NETWORK_MOUNTS=false: network-backed mounts under ${source_dir} will be backed up like any other directory."
    return 0
    ;;
  "true") ;;
  *)
    echo "ERROR! Unknown BACKUP_EXCLUDE_NETWORK_MOUNTS '${flag}', aborting..."
    echo "  Valid values are true and false."
    return 1
    ;;
  esac

  __find_network_mounts "${mountinfo_file}" "${source_dir}" || return 1

  if [ "${#__network_mount_paths[@]}" -eq 0 ]; then
    echo "No network-backed mounts found under ${source_dir}, nothing to exclude."
    return 0
  fi

  for index in "${!__network_mount_paths[@]}"; do
    echo "Excluding network mount (${__network_mount_types[index]}): ${source_dir}/${__network_mount_paths[index]}"
    __gocryptfs_exclude_args+=(-exclude "${__network_mount_paths[index]}")
  done
  echo "${#__network_mount_paths[@]} network-backed mount(s) excluded from this backup (BACKUP_EXCLUDE_NETWORK_MOUNTS=true)."

  return 0
}
# --- END network-mount detection ---

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
    # Reverse mode needs deterministic nonces, so that a file whose contents
    # have not changed encrypts to the same bytes and rsync transfers only what
    # actually moved. The gocryptfs manual states that -reverse "Implies
    # -aessiv", because AES-SIV is the mode that stays secure when nonces
    # repeat. AES-SIV is therefore the only cipher available here whatever
    # GOCRYPTFS_CIPHER asks for, so aes-gcm and aes-siv are the same thing.
    # -aessiv is passed explicitly rather than left implied, so the flag that
    # produced the config is visible in the command. Verified identical: both
    # spellings yield the same FeatureFlags in .gocryptfs.reverse.conf.
    case "${__gocryptfs_cipher}" in
    "aes-gcm" | "aes-siv")
      __cipher_flag=(-aessiv)
      ;;
    "xchacha")
      echo "ERROR! GOCRYPTFS_CIPHER=xchacha cannot work, aborting..."
      echo "  Reverse mode implies AES-SIV, and XChaCha20-Poly1305 is unsafe"
      echo "  with the repeated nonces reverse mode relies on, so gocryptfs"
      echo "  refuses the pair: \"can't have both XChaCha20Poly1305 and AESSIV"
      echo "  feature flags\". This is not a limitation of this tool and no"
      echo "  version of it can offer xchacha for a backup."
      echo "  Set GOCRYPTFS_CIPHER=aes-siv. Nothing is lost: aes-gcm and"
      echo "  aes-siv both give AES-SIV here."
      exit 1
      ;;
    *)
      echo "ERROR! Unknown GOCRYPTFS_CIPHER '${__gocryptfs_cipher}', aborting..."
      echo "  Valid values are aes-siv and aes-gcm, which are equivalent in"
      echo "  reverse mode: both give AES-SIV."
      echo "  If that value looks like a different setting entirely, an empty"
      echo "  variable in the env file has shifted the arguments: the Makefile"
      echo "  expands them unquoted, so a blank one disappears instead of"
      echo "  passing as empty."
      exit 1
      ;;
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

# Look for network-backed mounts under the source now rather than at startup:
# the sleep above is what lets Docker finish setting up its own volume mounts,
# and a mount that appears after this point is not covered either way.
if ! __build_network_mount_excludes "${__exclude_network_mounts}" \
  /proc/self/mountinfo "${__backup_source}"; then
  exit 1
fi

# mount read-only encrypted virtual copy of unencrypted local data:
if gocryptfs -ro -nosyslog "${__gocryptfs_passfile_args[@]}" -reverse \
  "${__gocryptfs_exclude_args[@]}" \
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
