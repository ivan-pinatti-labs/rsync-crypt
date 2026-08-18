#!/usr/bin/env bash

: ' Script to browse the encrypted remote backup read-only via sshfs + gocryptfs.
    The decrypted view is served over SFTP on port 22 (mapped to host port 2222).
    Connect Thunar to: sftp://root@localhost:2222/view/dec
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
__BINARIES_LIST__+=" sshfs"
__BINARIES_LIST__+=" ssh-keygen"

# check binaries before proceeding
for binary in ${__BINARIES_LIST__}; do
    if ! which "${binary}" > /dev/null; then
        echo "Error! ${binary} is missing..."
        exit 2
    fi
done

# parameters
__remote_server=${1:-"user@x.x.x.x"}              # replace x.x.x.x with the remote server's IP or host name
__remote_backup_folder=${2:-"/remote/backup"}     # encrypted backup folder on the remote server
__passkey_file=${3:-"/view/passfile"}             # gocryptfs master key
__view_enc_folder=${4:-"/gocrypt-view/encrypted"}  # sshfs mount point (remote encrypted files)
__view_dec_folder=${5:-"/gocrypt-view/decrypted"} # gocryptfs mount point (decrypted read-only view)

#===============================================================
# Set a trap for CTRL+C to properly exit
#===============================================================

trap 'echo "View interrupted, cleaning up..."; pkill -f "sshd -f /tmp/sshd_config" 2>/dev/null || true; pkill -f "internal-sftp" 2>/dev/null || true; sleep 1; fusermount -u "${__view_dec_folder}" 2>/dev/null || true; fusermount -u "${__view_enc_folder}" 2>/dev/null || true; exit 3' SIGINT SIGTERM

#===============================================================
# Guard: enc/dec must be empty before mounting
#===============================================================

for _dir in "${__view_enc_folder}" "${__view_dec_folder}"; do
    if find "${_dir}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | read -r _; then
        echo "Error: ${_dir} must be empty before mounting."
        echo "If a previous session left it mounted, run: fusermount -u ${_dir}"
        exit 1
    fi
done

#===============================================================
# Mount remote encrypted backup via sshfs (no local copy needed)
#===============================================================

echo "Mounting remote encrypted backup from ${__remote_server}:${__remote_backup_folder}..."
if ! sshfs "${__remote_server}:${__remote_backup_folder}" "${__view_enc_folder}" \
        -o IdentityFile=/root/.ssh/id_rsa \
        -o UserKnownHostsFile=/root/.ssh/known_hosts \
        -o StrictHostKeyChecking=yes; then
    echo "sshfs failed"
    exit 1
fi

if ! test -f "${__view_enc_folder}/gocryptfs.conf"; then
    echo "Error: ${__view_enc_folder}/gocryptfs.conf not found — cannot decrypt."
    fusermount -u "${__view_enc_folder}" 2>/dev/null || true
    exit 1
fi

#===============================================================
# Decrypt (mount read-only virtual view)
#===============================================================

if [ "${__paranoid_mode}" = "true" ]; then
    echo "PARANOID MODE: passphrase will be entered interactively."
    __gocryptfs_passfile_args=""
else
    __gocryptfs_passfile_args="-passfile ${__passkey_file}"
fi

# shellcheck disable=SC2086
if ! gocryptfs -ro -nosyslog ${__gocryptfs_passfile_args} \
               "${__view_enc_folder}" "${__view_dec_folder}"; then
    echo "gocryptfs failed"
    fusermount -u "${__view_enc_folder}" 2>/dev/null || true
    exit 1
fi

#===============================================================
# Serve decrypted view over SFTP for host file-manager browsing
#===============================================================

# Derive public key from the mounted SSH private key → authorized_keys
ssh-keygen -y -f /root/.ssh/id_rsa > /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# Generate a temporary sshd host key (discarded on container exit)
ssh-keygen -t ed25519 -f /tmp/ssh_host_ed25519_key -N ""

# Write a minimal sshd config
cat > /tmp/sshd_config << 'EOF'
Port 22
HostKey /tmp/ssh_host_ed25519_key
AuthorizedKeysFile /root/.ssh/authorized_keys
PermitRootLogin prohibit-password
PasswordAuthentication no
Subsystem sftp internal-sftp
ForceCommand internal-sftp -d /gocrypt-view/decrypted
EOF

/usr/sbin/sshd -f /tmp/sshd_config

echo ""
echo "VIEWER MODE — decrypted backup accessible via SFTP:"
echo "  sftp://root@localhost:2222/gocrypt-view/decrypted"
echo ""
echo "File manager access (open your file manager and connect to server):"
echo "  GNOME Files / Nautilus : Other Locations → Connect to Server"
echo "  Thunar                 : Go → Open Location"
echo "  Dolphin                : Network → Add Network Folder"
echo ""
echo "When done, press Enter to unmount and exit."
read -r -p ""

pkill -f "sshd -f /tmp/sshd_config" 2>/dev/null || true
pkill -f "internal-sftp" 2>/dev/null || true
sleep 1
fusermount -u "${__view_dec_folder}" 2>/dev/null || true
fusermount -u "${__view_enc_folder}" 2>/dev/null || true
echo "Unmounted. Exiting."

exit 0
