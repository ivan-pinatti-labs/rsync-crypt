# Security and Key Management

Part of the [rsync-crypt](../README.md) documentation. What gets created on a
first run, what has to be stored off-machine, and what is unrecoverable if it
is not. Build and usage instructions are in [USAGE.md](USAGE.md).

---

## Files Created on First Run

On the first `make backup`, three key files are created:

| File                  | Default location                                         | Created by                         | Purpose                                                |
| --------------------- | -------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------ |
| Passphrase file       | `GOCRYPTFS_PASSKEY_FILE` (set in `.env`)                 | `make backup` (interactive prompt) | Encryption passphrase, required for every operation    |
| gocryptfs config      | `$BACKUP_SOURCE_FOLDER/.gocryptfs.reverse.conf`          | gocryptfs                          | Encryption parameters (cipher, scrypt cost, name mode) |
| Config reference copy | `$BACKUP_SOURCE_FOLDER/.gocryptfs.reverse.conf.original` | `backup.sh`                        | Canonical config; restored before every run            |

> **Root backup:** the config is stored at the path set in
> `BACKUP_ENCRYPTION_CONF` instead of inside `BACKUP_SOURCE_FOLDER`.

---

## The Passphrase File

`GOCRYPTFS_PASSKEY_FILE` is a plain text file containing your encryption
passphrase.

- If the file does not exist when you run `make backup`, you are prompted to
  type a passphrase and the file is created automatically
- Permissions are set to `600` automatically
- Required for every backup, view, and restore operation
- Do not delete it unless you have the master key safely recorded somewhere
  else

**Prefer never writing the passphrase to disk?** Set `PARANOID_MODE=true` in
your `.env`. The passkey file is completely bypassed: `check-passkey` is
skipped, no volume is mounted into the container, and gocryptfs will prompt
you to type the passphrase interactively at startup. Note that this mode
requires an interactive terminal and cannot be used with cron or other
non-interactive schedulers.

---

## The Master Key

During the first `gocryptfs -reverse -init`, gocryptfs generates a random
master key and prints it to the terminal. The script pauses with a "Press O"
prompt so you can write it down.

> **The plaintext master key is never written to disk. It is printed once
> and never again.** An encrypted copy of it (`EncryptedKey`, wrapped with a
> key scrypt derives from your passphrase) lives inside
> `.gocryptfs.reverse.conf`, which is how the passphrase file alone is
> normally enough to unlock the backup: without the passphrase, that copy is
> only as recoverable as the passphrase itself, which is why the printed
> plaintext is the one durable, offline fallback.

Store it off-machine, separate from the backup destination:

- A password manager entry
- An offline or encrypted USB drive
- Paper in a physically secure location

**If you lose the passphrase file and do not have the master key, the
encrypted backup is permanently unrecoverable.**

With the master key you can still access the backup even without the
passphrase file. Mount the *remote* encrypted directory (not
`BACKUP_SOURCE_FOLDER`, which is the plaintext reverse-mode source, not the
backup itself), read-only, supplying the key on stdin rather than as a
command-line argument that would otherwise sit in your shell history and
`ps` output:

```bash
echo "<your-master-key>" | gocryptfs -ro -masterkey=stdin \
  /path/to/remote/encrypted/dir /path/to/mount-point
```

That relies on `gocryptfs.conf` still being present in the remote directory
(the ordinary case: `backup.sh` ships the reverse-mode config there under
that name on every run) for the cipher and filename-encryption settings. If
that config is also gone, supply them explicitly instead: reverse mode
always uses AES-SIV regardless of `GOCRYPTFS_CIPHER`, so add `-aessiv`, and
add `-plaintextnames` too if the backup was initialised with the default
`GOCRYPTFS_ENCRYPT_NAMES=false`:

```bash
echo "<your-master-key>" | gocryptfs -ro -masterkey=stdin -aessiv -plaintextnames \
  /path/to/remote/encrypted/dir /path/to/mount-point
```

---

## The Config File

`.gocryptfs.reverse.conf` stores the encryption parameters set at init time:
cipher, scrypt cost, and whether filenames are encrypted. It does not contain
the encryption key itself.

The `.original` copy is the canonical reference. Before every run,
`backup.sh` copies it back to `.gocryptfs.reverse.conf` to ensure the config
stays consistent. Do not delete the `.original` file.

Back up the `.original` file alongside your passphrase (or passphrase file) to
a second location off-machine.

---

## Recovery Scenarios

| Situation                                  | Recovery                                                                   |
| ------------------------------------------ | -------------------------------------------------------------------------- |
| Passphrase file lost, master key available | Use `gocryptfs -masterkey <key>` to access the backup                      |
| Passphrase file lost, no master key        | Backup is permanently unrecoverable                                        |
| `.gocryptfs.reverse.conf` missing          | Restored automatically from `.gocryptfs.reverse.conf.original` on next run |
| `.gocryptfs.reverse.conf.original` missing | Restore from your off-machine backup of the config file                    |

---

## Verifying a Published Image

Every published image is signed with
[cosign](https://github.com/sigstore/cosign) using GitHub Actions' keyless
signing, so there is no private key to leak or rotate. The signature covers
both registries, since they publish the same digest. Verify a pulled image
actually came out of this repository's `publish-image.yml` workflow before
trusting it. A real release signs with the identity of the git tag that
triggered it (`refs/tags/vX.Y.Z`), not the `main` branch, so verification has
to match the tag pattern rather than one fixed branch ref:

```bash
cosign verify \
  --certificate-identity-regexp "^https://github\.com/ivan-pinatti-labs/rsync-crypt/\.github/workflows/publish-image\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/ivan-pinatti-labs/rsync-crypt:1.5.2
```

Prefer a pinned version tag over `latest` for anything unattended (a cron job,
a scheduled backup): it guarantees the same `gocryptfs`, `rsync`, and other
`apk`-pinned binaries on every run, instead of whatever the most recent
release happened to bake in. A released version tag is never rebuilt in place;
`publish-image.yml` refuses to republish one.

---

## What `make clean` Removes

`make clean` permanently deletes:

- The passphrase file (`GOCRYPTFS_PASSKEY_FILE`)
- Both `.gocryptfs.reverse.conf` files from `BACKUP_SOURCE_FOLDER`
- The root-backup config copy (`BACKUP_ENCRYPTION_CONF`), if set
- The Docker image

After `make clean`, the next `make backup` re-initialises gocryptfs with a new
master key. **The previous backup on the remote server remains intact and can
still be read using the original passphrase or master key**, but the fresh
local init produces a new config that is incompatible with the existing
remote backup: the new key does not decrypt files the old one wrote, and the
new `gocryptfs.conf` `rsync` ships to the same destination would overwrite
the old one there, stranding whatever old-encrypted files that sync doesn't
also happen to touch, unreadable under either config. Point
`REMOTE_SERVER_BACKUP_FOLDER` at a new, empty destination for the
re-initialised backup instead of reusing the old one. Only repoint it back to
the original destination, if that is what you want, after a full sync to the
new destination has completed and you have confirmed the old backup is no
longer needed.
