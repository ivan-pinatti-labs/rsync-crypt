# rsync-crypt — Claude Project Memory

## What This Project Is

Docker-based encrypted backup tool using gocryptfs reverse mode + rsync over SSH.
Makefile-driven. Alpine image. Key binaries: gocryptfs, rsync, sshfs, openssh (sshd).

## Key Files

| File                            | Purpose                                                                    |
| ------------------------------- | -------------------------------------------------------------------------- |
| `Makefile`                      | All targets; reads env file via `ENV_FILE ?= .env` + `include $(ENV_FILE)` |
| `.env`                          | User config (not committed); `.env.example` is the template                |
| `scripts/backup.sh`             | Main backup script, called inside Docker                                   |
| `scripts/restore.sh`            | Restore script                                                             |
| `scripts/view.sh`               | SFTP view mode via sshd inside container                                   |
| `conf/backup-filter-rules.txt`  | rsync filter rules (+ include, - exclude)                                  |
| `conf/restore-exclude-list.txt` | Restore exclusions                                                         |
| `conf/restore-paths.txt`        | Selective restore paths (empty = restore all)                              |

## Architecture

1. `gocryptfs -reverse` mounts a read-only encrypted virtual view of `BACKUP_SOURCE_FOLDER`
2. `rsync` pushes the encrypted view to the remote server over SSH
3. View mode: `sshfs` mounts the remote encrypted dir, `gocryptfs` decrypts it, `sshd` serves it via
   SFTP on `127.0.0.1:2222`

## Known Gotchas

### GOCRYPTFS_ENCRYPT_NAMES must be false for filter rules to work

When `true`, rsync sees scrambled filenames and no filter pattern can match them.
Default is `false`. File contents are still fully encrypted either way.
gocryptfs has `-exclude-wildcard` with gitignore negation, but the include-first
catch-all-exclude pattern in the filter file cannot be expressed with excludes alone.
Wiring gocryptfs `-exclude-from` instead of rsync filters is a planned future improvement.
Upstream: <https://github.com/rfjakob/gocryptfs/issues/1000> proposes a `-filter-from` flag with
rsync-style first-match-wins semantics.

### Alpine gocryptfs version

`GOCRYPTFS_VERSION="2.5"` resolves to `2.5.4-r8` in Alpine 3.23 community repo.
The `-bs` (block size) flag is NOT supported by this build. Do not add it back.

### rsync exit codes 23 and 24

Exit 23 = partial transfer (some files skipped/unreadable), exit 24 = vanished files.
Both are treated as success-with-warning (break loop), not as retriable failures.
With `RSYNC_LOOP=true` these used to cause infinite retry loops.

### ENODATA (errno 61) from gocryptfs

Happens when gocryptfs reverse mode cannot read locked files (SQLite WAL, LevelDB LOCK).
Fixed by excluding `*.lock`, `*.db-wal`, `*.db-shm`, `*.sqlite-wal`, `*.sqlite-shm`, `**/LOCK` in
filter rules.

### check-passkey requires a real TTY

The `read -r -p` prompt for passphrase creation needs an interactive terminal.
Running `make bb` from a non-TTY context will fail at `check-passkey`.
The `chmod 600` is guarded: exits with an error if the passkey file does not exist after the prompt.

### gocryptfs params are init-time only

Cipher, scryptn, and encrypt_names are stored in `.gocryptfs.reverse.conf` on first init.
Changing them after init requires deleting the conf and re-encrypting the full backup.

## User Preferences

- No em-dashes (`—` or `--`) in prose; use commas or parentheses instead
- No `|| true` in Makefile; use `docker inspect` conditionals instead
- `make clean` uses `docker inspect` pre-checks before `rm` to avoid false failures
- Do not add `|| true` as a general error suppressor; fail explicitly with a clear message
