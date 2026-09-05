# Build and Usage

Part of the [rsync-crypt](../README.md) documentation. The README covers the
Quick Start with the published image; everything else lives here: building
from source, the full configuration reference, every `make` target, the test
suite, and the known limitations.

Key management is separate, in [SECURITY.md](SECURITY.md). Running under
Podman is covered in [PODMAN.md](PODMAN.md).

---

## Contents

- [Build from Source](#build-from-source)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Multiple Configurations](#multiple-configurations)
  - [Filter Rules](#filter-rules)
- [Usage](#usage)
  - [Build](#build)
  - [Backup](#backup)
  - [View](#view)
  - [Restore](#restore)
- [Make Targets Reference](#make-targets-reference)
- [Tests](#tests)
- [Known Issues and Limitations](#known-issues-and-limitations)

---

## Build from Source

```bash
# 1. Clone the repository
git clone https://github.com/ivan-pinatti-labs/rsync-crypt.git
cd rsync-crypt

# 2. Create your environment file
cp .env.example .env

# 3. Edit .env with your settings (see Configuration below)
$EDITOR .env

# 4. Build the Docker image
make build
```

`make build` passes no build arguments. Every version baked into the image
(the Alpine base, plus the `apk` pins for gocryptfs, bash, less, openssh,
rsync, sshfs and vim) is an `ARG` default in the `Dockerfile` itself, so a
plain `docker build .` produces the same image the `Makefile` does.

To try a different version without editing the `Dockerfile`, set the variable
on the command line and the `build` target forwards it as a `--build-arg`:

```bash
ALPINE_VERSION=3.20 make build
make build GOCRYPTFS_VERSION=2.7   # same thing, other spelling
```

An override that names a version the pinned Alpine release does not actually
carry fails the build at `apk add`, loudly, which is what the `~=`
constraints are for. The seven `apk` pins are re-resolved automatically
whenever the Alpine version moves, by
[`.github/workflows/resolve-apk-pins.yml`](../.github/workflows/resolve-apk-pins.yml).

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```dotenv
# Docker image: which image every target runs
DOCKER_IMAGE_TAG_NAME="local/gocryptfs"
DOCKER_IMAGE_TAG_VERSION="1.0.0"

# SSH credentials
SSH_KEY_FILE="/home/youruser/.ssh/id_ed25519"
SSH_KNOWN_HOSTS_FILE="/home/youruser/.ssh/known_hosts"

# Passkey: file containing the gocryptfs passphrase (keep this safe!)
GOCRYPTFS_PASSKEY_FILE="/home/youruser/.gocrypt-passfile"

# Backup source
BACKUP_SOURCE_FOLDER="/home/youruser"
BACKUP_FILTER_RULES="./conf/backup-filter-rules.txt"

# Root backup: gocryptfs config preserved across runs
BACKUP_ENCRYPTION_CONF="/home/youruser/.gocryptfs.reverse.conf"

# Remote server
REMOTE_SERVER="user@192.168.1.100"
REMOTE_SERVER_BACKUP_FOLDER="/mnt/backups/youruser"

# Restore
RESTORE_DESTINATION="/tmp/restore"
RESTORE_EXCLUDE_LIST="./conf/restore-exclude-list.txt"
RESTORE_PATHS_FILE="./conf/restore-paths.txt"

# rsync options
RSYNC_RATE_LIMIT=0          # kbytes/s, 0 = unlimited
RSYNC_LOOP=true             # retry on failure

# gocryptfs encryption (applied only on first init, stored in config afterwards)
GOCRYPTFS_ENCRYPT_NAMES=false # false = plaintext names (default), true = scramble filenames
GOCRYPTFS_CIPHER="aes-siv"    # aes-siv, or aes-gcm as an equivalent spelling
GOCRYPTFS_SCRYPT_N=16         # key derivation cost: 2^N iterations

# Passphrase mode
PARANOID_MODE=false # true = never store passphrase on disk, gocryptfs prompts interactively
```

**Variable reference:**

| Variable                      | Description                                                                                                                                                                                 |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DOCKER_IMAGE_TAG_NAME`       | Image every target runs. A local build tag, or a published image such as `ghcr.io/ivan-pinatti-labs/rsync-crypt`                                                                            |
| `DOCKER_IMAGE_TAG_VERSION`    | Tag of that image. With a published image, a release version such as `1.5.0`                                                                                                                |
| `SSH_KEY_FILE`                | SSH private key used to authenticate to the remote server                                                                                                                                   |
| `SSH_KNOWN_HOSTS_FILE`        | Known hosts file to verify the remote server fingerprint                                                                                                                                    |
| `GOCRYPTFS_PASSKEY_FILE`      | File containing the gocryptfs passphrase. Created interactively if it does not exist                                                                                                        |
| `BACKUP_SOURCE_FOLDER`        | Directory to back up (user backup)                                                                                                                                                          |
| `BACKUP_FILTER_RULES`         | rsync filter rules file, controls what is included/excluded                                                                                                                                 |
| `BACKUP_ENCRYPTION_CONF`      | Path where the gocryptfs reverse config is preserved (root backup)                                                                                                                          |
| `REMOTE_SERVER`               | `user@host` for the SSH backup destination                                                                                                                                                  |
| `REMOTE_SERVER_BACKUP_FOLDER` | Path on the remote server where the encrypted backup is stored                                                                                                                              |
| `RESTORE_DESTINATION`         | Local staging directory for restored files                                                                                                                                                  |
| `RESTORE_EXCLUDE_LIST`        | rsync exclude list applied during a restore                                                                                                                                                 |
| `RESTORE_PATHS_FILE`          | File listing specific paths to restore, one per line. Empty restores everything                                                                                                             |
| `RSYNC_RATE_LIMIT`            | Bandwidth cap in kbytes/s (`0` = no limit)                                                                                                                                                  |
| `RSYNC_LOOP`                  | `true` to retry rsync automatically on transient errors                                                                                                                                     |
| `GOCRYPTFS_ENCRYPT_NAMES`     | `false` to keep filenames as plaintext on the remote server (default, required for filter rules to work). `true` scrambles filenames (see [Known Issues](#known-issues-and-limitations))    |
| `GOCRYPTFS_CIPHER`            | Cipher at first init. Reverse mode implies AES-SIV, so `aes-siv` and `aes-gcm` are equivalent and `xchacha` is rejected with an explanation                                                 |
| `GOCRYPTFS_SCRYPT_N`          | scrypt key derivation cost exponent (default `16`, meaning 2^16 iterations)                                                                                                                 |
| `PARANOID_MODE`               | `false` (default). When `true`, the passphrase is never written to disk; gocryptfs prompts interactively on each run. `GOCRYPTFS_PASSKEY_FILE` is ignored. Requires an interactive terminal |

> **Note:** the versions baked into the image are deliberately not in this
> file. They are `ARG` defaults in the `Dockerfile`; see
> [Build from Source](#build-from-source) for how to override one.

### Multiple Configurations

To use a different environment file without modifying `.env`, pass `ENV_FILE`
on the command line or via the shell environment:

```bash
# Command-line variable
make backup ENV_FILE=.env.myconfig

# Shell environment variable
ENV_FILE=.env.myconfig make backup
```

Copy `.env.example` to `.env.myconfig` (or `.env.personal`, etc.) and fill in
the values for each profile. The default is `.env` when `ENV_FILE` is not set,
so existing setups are unaffected.

> **Note:** `.env` and `.env.*` are both listed in `.gitignore`, so all profile
> files are excluded from version control by default.

### Filter Rules

Edit `conf/backup-filter-rules.txt` to control what gets backed up. The file
uses rsync filter rule syntax (`+` to include, `-` to exclude).

The default rules back up:

- **Chromium-based browsers** (Brave, Chrome, Chromium): bookmarks,
  preferences, extensions
- **Firefox**: bookmarks, preferences, extensions, passwords, certificates
- **Tor Browser**: profile data (bookmarks, prefs, extensions, certificates),
  excluding the browser binary and cache
- **VSCode**: user settings, keybindings, snippets, profiles
- **Lens Desktop**: cluster configs and settings
- **Spotify**: user preferences only (cached tracks are excluded)

Common exclusions by default: `.cache`, Trash, Docker local data, Flatpak
data, `.asdf`, Minikube, Steam, Terraform providers.

> **Tip:** The filter file is well commented. Uncomment optional lines to also
> back up browser history, cookies, session data, or VSCode extensions.

<!-- Separates two adjacent blockquotes. Without it MD028 reads the blank
    line as being inside one quote. These are two distinct callouts and
    merging them would bury the second. -->

> **Important:** Filter rules only work when `GOCRYPTFS_ENCRYPT_NAMES=false`
> (the default). When filename scrambling is enabled, rsync operates on the
> encrypted virtual directory and sees only ciphertext names, so no pattern in
> the filter file can match them. See
> [Known Issues](#known-issues-and-limitations) for details.

---

## Usage

### Build

Build the Docker image (required once, or after any `Dockerfile` change):

```bash
make build
```

Not needed at all when `DOCKER_IMAGE_TAG_NAME` points at a published image.

---

### Backup

#### User Backup

Backs up `BACKUP_SOURCE_FOLDER` (your home directory or any folder) to the
remote server, encrypted.

```bash
make backup
```

On the **first run**, gocryptfs initialises the encrypted view, saves its
config to `BACKUP_SOURCE_FOLDER`, and prints the **master key** to the
terminal. The script pauses so you can write it down before continuing. See
[Security and Key Management](SECURITY.md) for a full description of what is
created and what to back up off-machine.

If `GOCRYPTFS_PASSKEY_FILE` does not exist, you are prompted for a passphrase
and the file is created automatically at that path.

rsync will keep running (retrying on failure) until a full sync completes.
Subsequent runs are **incremental**, only changed files are transferred.

#### Root Backup (System Files)

Backs up `/etc`, `/home`, `/opt`, `/root`, and `/srv` as root, encrypted.

```bash
make backup_as_root
```

This is useful for backing up system-wide configuration alongside your user
data. It needs privileged access to the host by definition; see
[PODMAN.md](PODMAN.md#rootless-specifically-for-this-project) for what that
does and does not change under a rootless runtime.

#### Combined Build + Backup

```bash
make bb     # build + user backup
make bbr    # build + root backup
```

#### Bandwidth Limiting

Set `RSYNC_RATE_LIMIT` in `.env` (kbytes/s) or override at runtime:

```bash
RSYNC_RATE_LIMIT=5000 make backup   # limit to ~5 MB/s
```

#### Paranoid Mode

By default the passphrase is read from `GOCRYPTFS_PASSKEY_FILE` on disk. If
you prefer to never store the passphrase on disk at all, enable paranoid mode.
gocryptfs will prompt you to type it interactively on every run and it is
never written anywhere.

Enable permanently in `.env`:

```dotenv
PARANOID_MODE=true
```

Or override for a single run:

```bash
PARANOID_MODE=true make backup
PARANOID_MODE=true make backup_as_root
```

When paranoid mode is active:

- `GOCRYPTFS_PASSKEY_FILE` is ignored entirely
- No passkey volume is mounted into the container
- gocryptfs prompts `Password:` on stdin at startup

> **Note:** Paranoid mode requires an interactive terminal (`--interactive
> --tty` is already set by all `make` targets). It cannot be used with cron
> jobs or any non-interactive scheduler.

---

### View

The view mode lets you **browse the decrypted remote backup from any GUI file
manager** without downloading the full backup locally. It is read-only and
safe.

```bash
make view           # browse user backup
make view_as_root   # browse root/system backup
```

What happens:

1. `sshfs` mounts the remote encrypted folder directly into the container (no
   local copy)
2. `gocryptfs` decrypts it into a read-only virtual mount inside the container
3. An SFTP server starts inside the container, available at `127.0.0.1:2222`
4. Your terminal shows the SFTP address and waits, press `Enter` to unmount
   and exit

#### Connecting Your File Manager

Once `make view` is running, open your file manager and connect to:

```text
sftp://root@localhost:2222/gocrypt-view/decrypted
```

| File Manager           | How to connect                     |
| ---------------------- | ---------------------------------- |
| GNOME Files / Nautilus | Other Locations, Connect to Server |
| Thunar                 | Go, Open Location                  |
| Dolphin                | Network, Add Network Folder        |
| Any SFTP client        | `sftp root@localhost -p 2222`      |

> **Security note:** The SFTP port is bound to `127.0.0.1` only, it is not
> reachable from the network. Authentication uses your existing SSH key, no
> password is required.

When you are done browsing, press **Enter** in the terminal. The view will
unmount cleanly and the container exits.

#### Paranoid Mode

The same `PARANOID_MODE` flag applies to view:

```bash
PARANOID_MODE=true make view
PARANOID_MODE=true make view_as_root
```

gocryptfs will prompt for the passphrase before mounting the decrypted view.
The passphrase is never stored on disk.

---

### Restore

Restores pull the encrypted backup from the remote server, decrypt it, and
write the result either to a staging directory (safe, reviewable) or straight
back over the original location (destructive).

```bash
make restore                     # user backup, to RESTORE_DESTINATION
make restore_to_origin           # user backup, to BACKUP_SOURCE_FOLDER
make restore_as_root             # system backup, to RESTORE_DESTINATION
make restore_as_root_to_origin   # system backup, to /etc, /home, /opt, /root, /srv
```

Prefer the staging targets and move the files yourself. The `_to_origin`
targets overwrite the live location with whatever the backup holds.

Restrict a restore to specific paths in either of two ways:

```bash
# One-off, on the command line
make restore RESTORE_PATHS="Documents/ .config/Code/User/"

# Persistently, one relative path per line
$EDITOR conf/restore-paths.txt
```

An empty `RESTORE_PATHS_FILE` restores everything. `RESTORE_EXCLUDE_LIST`
(`conf/restore-exclude-list.txt`) is applied on top either way.

---

## Make Targets Reference

Running `make` with no target displays this reference. It does not require an
environment file; every target that performs work does.

| Target                           | Shorthand  | Description                                                                                          |
| -------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------- |
| `make` / `make help`             | n/a        | Show this reference                                                                                  |
| `make all`                       | n/a        | Build the image and start a user-backup container                                                    |
| `make build`                     | n/a        | Build the Docker image                                                                               |
| `make backup`                    | n/a        | Encrypt and sync user data to remote server                                                          |
| `make backup_as_root`            | n/a        | Encrypt and sync system dirs to remote server                                                        |
| `make bb`                        | n/a        | Build + user backup                                                                                  |
| `make bbr`                       | n/a        | Build + root backup                                                                                  |
| `make view`                      | `make v`   | Browse decrypted user backup via SFTP                                                                |
| `make view_as_root`              | `make vr`  | Browse decrypted root backup via SFTP                                                                |
| `make restore`                   | `make r`   | Restore user backup to staging dir                                                                   |
| `make restore_to_origin`         | `make ro`  | Restore user backup to original location                                                             |
| `make restore_as_root`           | `make rr`  | Restore root backup to staging dir                                                                   |
| `make restore_as_root_to_origin` | `make rro` | Restore root backup to original paths                                                                |
| `make brr`                       | n/a        | Build + root restore to staging                                                                      |
| `make run_container`             | n/a        | Start an interactive user-backup container                                                           |
| `make run_container_as_root`     | n/a        | Start an interactive system-backup container                                                         |
| `make check-passkey`             | n/a        | Create or verify the passkey file                                                                    |
| `make clean`                     | n/a        | Remove container, image, passkey, and gocryptfs config files (destructive, prompts for confirmation) |

---

## Tests

The suite drives the real Makefile targets. The integration tests start a
throwaway sshd container that stands in for the remote backup server, run a
full backup into it, then restore and compare the result against the source.

```bash
python3 -m venv tests/.venv
tests/.venv/bin/pip install -r tests/requirements.txt
tests/.venv/bin/pytest tests
```

Requirements: Docker (or Podman, see [PODMAN.md](PODMAN.md)) and a working
`/dev/fuse`.

Test files and everything mounted into a container live under the pytest
temporary directory, so no real backup, passkey, or config is touched. Two
Docker resources are created outside it: the `rsync-crypt-test-remote`
container, removed automatically when the session ends, and the
`local/gocryptfs-test` image, which is left behind so repeat runs skip the
build. It is tagged separately from `local/gocryptfs`, so the image a normal
`make build` produces is never overwritten. Remove it with:

```bash
docker rmi local/gocryptfs-test
```

The tests that exercise a script directly, with no container and no stack
state, can be run on their own:

```bash
pytest tests -m scripts
```

The tests run on every pull request via the `Tests` job.

| File                           | Covers                                                                             |
| ------------------------------ | ---------------------------------------------------------------------------------- |
| `test_makefile.py`             | Help output, `ENV_FILE` handling, build-arg overrides, `.env.example` completeness |
| `test_build.py`                | Image builds, required binaries, the Dockerfile's version pins                     |
| `test_roundtrip.py`            | Backup, filter rule exclusions, encryption at rest, restore                        |
| `test_assert_pin_only_diff.py` | The `Pin Only` gate: what a dependency bot's diff may and may not change           |
| `test_resolve_apk_pins.py`     | Re-resolving the seven apk pins against a new Alpine release                       |

The suite runs serially: the Makefile names its container `gocryptfs`, so two
targets cannot run at the same time.

---

## Known Issues and Limitations

### Filter Rules Are Incompatible with Filename Encryption

Setting `GOCRYPTFS_ENCRYPT_NAMES=true` causes rsync filter rules to stop
working entirely.

**Why:** In gocryptfs reverse mode, the encrypted virtual directory (the one
rsync reads from) contains scrambled filenames and directory names. A path
like `.config/BraveSoftware/Brave-Browser/Default/Bookmarks` becomes something
like `gCqj/UKVCWfRmkXfp/nLpFwA==`. The rsync filter rules in
`conf/backup-filter-rules.txt` match on human-readable paths, so no rule can
ever match a scrambled name. The result is that rsync sees the entire
encrypted directory as-is, ignores all filter rules, and transfers everything,
including directories you intended to exclude.

**Current default:** `GOCRYPTFS_ENCRYPT_NAMES=false`. File and directory
**contents** are still fully encrypted by gocryptfs; only the names and paths
are stored in plaintext on the remote server. For most home backup scenarios
this is an acceptable trade-off: the remote server can see your directory
structure (revealing which applications you use) but cannot read any file
content without your passphrase.

**Why not use gocryptfs's own exclude flags?** gocryptfs reverse mode does
support `-exclude-wildcard` with gitignore-style negation patterns (e.g.,
`-exclude-wildcard '*' -exclude-wildcard '!/important'`), which operate on
plaintext paths before encryption. However, the rsync filter syntax used in
this project (specifically the include-first, catch-all-exclude pattern used
in the browser and Firefox sections) cannot be expressed with exclusion-only
patterns alone. Supporting this properly would require replacing the rsync
filter file with a gocryptfs-native exclude file and rearchitecting how
filtering is wired through the tool. This is a planned improvement for a
future version. Upstream tracking:
[gocryptfs#1000](https://github.com/rfjakob/gocryptfs/issues/1000) proposes a
`-filter-from` flag with rsync-style first-match-wins semantics that would
solve this cleanly.

**If you want scrambled filenames today** and are willing to trade
fine-grained filtering for privacy: set `GOCRYPTFS_ENCRYPT_NAMES=true` and
simplify `conf/backup-filter-rules.txt` to keep only the top-level exclusion
rules (the `- **/.cache`, `- .local/share/Trash/**`, etc. lines under "General
exclusions"). Then pass a plain exclude list to gocryptfs's `-exclude-from`
flag instead of rsync. This requires manual changes to `scripts/backup.sh` and
is not currently supported out of the box.
