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
to match the tag pattern rather than one fixed branch ref. Substitute the
release you are verifying for `X.Y.Z`; current tags are on the
[Releases](https://github.com/ivan-pinatti-labs/rsync-crypt/releases) page:

```bash
cosign verify \
  --certificate-identity-regexp "^https://github\.com/ivan-pinatti-labs/rsync-crypt/\.github/workflows/publish-image\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/ivan-pinatti-labs/rsync-crypt:X.Y.Z
```

Prefer a pinned version tag over `latest` for anything unattended (a cron job,
a scheduled backup): it guarantees the same `gocryptfs`, `rsync`, and other
`apk`-pinned binaries on every run, instead of whatever the most recent
release happened to bake in. A released version tag is never rebuilt in place;
`publish-image.yml` refuses to republish one.

**The `nightly` tag is the one exception to "pin a version tag," and it cuts
the other way.** `.github/workflows/nightly-build.yml` rebuilds the image
every night from the same Dockerfile ARG defaults as the latest release, no
version bump, purely to force a fresh `apk update && apk upgrade` and pick up
whatever Alpine has newly published within the existing pins. It is
overwritten in place every run and never promoted to `latest` or a version
tag. Use it to see whether a fix has already landed upstream, or to get the
freshest packages for a throwaway environment; do not point anything
unattended or production at it; the guidance above (pin a version tag) is
still what backs an actual backup schedule.

---

## What Scans What

Image scanning (below) covers the packages the image installs. It says nothing
about the code this repository writes, which is scanned separately:

| Code | Scanned by | Where |
| --- | --- | --- |
| `scripts/*.sh`, `files/bash/*` | shellcheck, shfmt, shebang checks | `checklist-dev-shell`, every commit |
| `scripts/*.py`, `tests/*.py` | ruff, flake8-bandit (`S`) rules on | `checklist-dev-python`, every commit |
| `scripts/*.py`, `tests/*.py` | CodeQL, `security-extended` suite | `codeql.yml`, on merge and weekly |
| `scripts/*.py`, `tests/*.py` | CodeQL quality queries | GitHub-managed Code Quality, on push, PR and weekly |
| `Dockerfile` | hadolint | `checklist-dev-docker`, every commit |
| `.github/workflows/*` | actionlint, zizmor | `checklist-github-actions`, every commit |
| Everything | detect-secrets, detect-private-key | `checklist-security-credentials`, every commit |

The asymmetry there is deliberate and worth knowing before someone tries to
"fix" it. **The shell scripts are the product**: `backup.sh`, `restore.sh` and
`view.sh` are what the container runs, and they are covered by shellcheck
rather than CodeQL because
[CodeQL does not support shell at all](https://docs.github.com/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql).
Its languages are JavaScript/TypeScript, Ruby, Python, Go, Java/Kotlin, C/C++
and C#.

The Security tab's "Code quality findings" entry is a separate product, not a
relabelling of `codeql.yml`, and it has to be enabled on its own; it was, on
2026-09-09. It runs a GitHub-managed CodeQL analysis for quality queries, and
it supports fewer languages still (`csharp`, `go`, `java-kotlin`,
`javascript-typescript`, `python`, `ruby`), so it cannot see `scripts/*.sh`
either. Enabling it is why `codeql.yml` narrowed from `security-and-quality`
to `security-extended`: quality moved there rather than being given up, and
running both suites over the same small directory would have analyzed it
twice. Its findings have no REST API and appear only in the UI at
`/security/quality`, which makes a clean result and a broken setup look the
same from outside; confirm it ran under Actions rather than from that page.
AI detections (`ai_findings_option`) are deliberately left disabled, as a
separate decision from enabling the baseline.

**The Python, conversely, does not ship.** It is repository tooling: grading
pull requests, re-resolving apk pins on an Alpine bump, auditing this file's
accepted-risk list. The image installs no Python interpreter, so a finding
there can never be a vulnerability in a published artifact; it can still gate
a merge wrongly, which is why it is analyzed twice over (ruff's `S` rules per
commit, CodeQL weekly and on merge).

That split is also why `COPY` in the `Dockerfile` names the three shell
scripts individually instead of globbing `scripts/*`. The glob shipped every
tooling script into `/app/` in the published image, confirmed present in
`ghcr.io/ivan-pinatti-labs/rsync-crypt:1.6.1`. They were inert, since nothing
in the image can execute Python, but they had no business being there.

## Image Vulnerability Scanning

> **Version numbers in this section are dated observations, not current facts.**
> Renovate, Dependabot and Alpine's own rebuilds move packages continuously and
> none of them edit prose, so anything specific written here starts going stale
> the moment it is committed. The versions are kept because the reasoning below
> is a set of measurements and a measurement without its subject is not
> checkable: "an older `x/crypto` has CVEs" tells you nothing you could verify.
> Treat each as "this is what was observed, on the date given, with the command
> given", and re-measure before relying on one. Where a version has no date
> next to it, assume it has drifted.

Every published image, `nightly` included, is scanned by both
[Docker Scout](https://docs.docker.com/scout/) and
[Trivy](https://trivy.dev/), in three places: locally before a push
(`.pre-commit-config.yaml`'s `trivy-image-scan`, a `pre-push` hook, since a
full image build is too slow to run on every commit) and again in CI
(`.github/workflows/publish-image.yml` for a release, `nightly-build.yml` for
the nightly rebuild). Docker Scout is report-only everywhere it runs, its
findings surfaced through this repository's code scanning tab rather than as
a release gate (see that step's own comment for why, including the retired
Dashboard health-score badge). Trivy is the blocking check on a release: an
unignored `CRITICAL` or `HIGH` finding fails both the local pre-push hook and
the CI job. On the nightly rebuild Trivy runs report-only instead, same as
Scout, since a scheduled job hard-failing every night on an upstream CVE
nobody can fix from this side, with no pull request to block, would only be
noise; a genuinely new finding still shows up in that run's SARIF upload.

### Accepted-risk CVEs

`.trivyignore.yaml` at the repository root lists CVE IDs that Trivy's
blocking gate would otherwise fail on, with no fix available on our side.
All of them today trace to gocryptfs, the only Go binary in this image: its
upstream release still pins `golang.org/x/crypto` v0.33.0, flagged for
multiple CVEs. gocryptfs's own `master` branch has already bumped past it,
but no release has shipped with that bump yet, so there is nothing to
upgrade to. `.trivyignore.yaml`'s own header comment carries the full
reasoning, including a wrinkle worth knowing before touching that file: a
plain `trivy image` scan of this image does not actually surface these
CVEs, because Trivy tracks gocryptfs purely as the Alpine `apk` package once
it is installed and does not separately analyze a binary a distro package
manager already owns. Docker Scout's SBOM-based scan does surface them,
which is how they were first found; the entries in `.trivyignore.yaml` are
kept as the canonical accepted-risk record regardless, documented here so
the reasoning has one home instead of being re-derived every time someone
asks why a CVE is on that list.

Do not add a new entry to `.trivyignore.yaml` without the same rigor: confirm
it against a live `trivy rootfs` run over the binary extracted from the image,
or a current Docker Scout scan of that image, not a copy from an advisory feed
or an older list. A `trivy image` run cannot confirm membership here, since it
does not produce these findings at all; it remains the right check for a
base-layer CVE, which is precisely the kind that should not be in this file.

A CVE in a transitive base-layer package (util-linux's `libblkid`/`libmount`
were the case that motivated all of this: Alpine's own security update fixes
them, and a rebuild of this image picks that fix up automatically) generally
should **not** go in `.trivyignore.yaml`. Ignoring it would hide the day the
fix actually lands; letting the scan keep failing until the next rebuild is
the point.

### Every entry expires

The file is YAML, not a plain CVE list, specifically for the `expired_at`
field Trivy's YAML ignore format supports (confirmed against the exact
pinned Trivy version, v0.74.0, and the exact pinned `aquasecurity/trivy-action`
release both CI workflows and the local pre-push hook use). Every entry
carries an `expired_at` date and a `statement` explaining what would resolve
it. Once that date passes, Trivy stops honoring the entry: the CVE reappears
in the scan, the CRITICAL/HIGH gate fails, and the next person to touch this
repository is forced to make a real decision instead of the entry quietly
suppressing the same finding forever. Renewing an entry means picking a new
`expired_at` and confirming the finding still reproduces per the rigor
above, exactly as if it were a new entry; it does not mean bumping the date
and moving on.

**That only works because a scan exists that can see these CVEs at all**, and
it is worth being precise about which one, because the obvious answer is
wrong. `trivy image` does not detect them: Trivy tracks gocryptfs purely as
the Alpine `apk` package that installed it and never runs its `gobinary`
analyzer against a binary a distro package manager already owns, so an image
scan produces no `gobinary` target and reports none of the vendored
`golang.org/x/crypto` findings. Measured against the published v1.6.1 image
with the pinned Trivy version, `trivy image` reports zero CRITICAL/HIGH while
`trivy rootfs` against the binary extracted from that same image reports all
twelve.

So both gates (`publish-image.yml` and the local pre-push hook) run a second,
blocking `trivy rootfs` scan against `/usr/bin/gocryptfs` extracted from the
image they just built, per platform in CI. Without it every entry in
`.trivyignore.yaml` would be suppressing a finding no gate ever produces,
`expired_at` would lapse with nothing failing, and this whole section would
describe an enforcement mechanism that does not enforce. An ignore list whose
entries are inert is worse than no ignore list, because it reads as though
something is being held back when nothing is.

Expiry dates are not decorative and should not be copied from one entry to
the next without thought. Tie each one to something real: a release window
upstream is expected to clear the finding in, or, when that window cannot be
predicted, a fixed re-review interval that forces a look regardless. Either
way, the `statement` field has to say which, so the person who hits the expiry
knows whether they are checking for a shipped fix or just re-affirming the
risk.

Windows follow severity, which is ordinary risk-acceptance practice: keep a
`HIGH` exception inside 90 days, tighten it for a `CRITICAL`, and never let an
expired one auto-renew. Today that means **30 days for the one `CRITICAL`
entry and 60 days for the eleven `HIGH` ones**. A shared date across all
twelve was the first arrangement and was replaced: they do share one root
cause, but severity is what the practice keys on, and a single date meant the
`CRITICAL` inherited the most permissive window on the list.

Do not tie the window to gocryptfs's own release cadence, which cannot carry
it. Upstream has shipped nothing since v2.6.1 (2025-08-10), and its historical
gaps run from one month to nineteen.

### Which fixes Alpine can deliver, and which it cannot

Two classes of CVE reach this binary, and only one of them a rebuild can clear.
The distinction is the whole answer to "so what would actually fix this":

| CVE class | Comes from | Cleared by |
| --- | --- | --- |
| Go stdlib | the toolchain gocryptfs was compiled with | an Alpine package rebuild |
| Vendored dependency (`x/crypto`, all current entries) | gocryptfs's own `go.mod` | only a gocryptfs release |

Alpine builds the release as upstream published it and does not patch
dependency versions, so **no number of Alpine rebuilds will move `x/crypto` off
v0.33.0.** That is why the Go-stdlib entries this list used to carry cleared by
themselves while these did not: 3.24's `2.6.1-r6` is compiled with go1.26.8,
new enough to shed the stdlib findings, and still vendors `x/crypto` v0.33.0.
gocryptfs `master` already carries v0.52.0; until a release is cut off it,
there is nothing for Alpine to package.

**A newer Alpine is not the answer either, and is currently worse.** Measured
2026-09-08 against the extracted binary:

| Alpine branch | gocryptfs | Built with | `x/crypto` | CRITICAL/HIGH |
| --- | --- | --- | --- | --- |
| 3.23 | 2.5.4-r11 | | | older release entirely |
| 3.24 (pinned) | 2.6.1-r6 | go1.26.8 | v0.33.0 | 12 |
| edge | 2.6.1-r7 | go1.26.5 | v0.33.0 | 20 |

edge reports **more** findings than the pinned branch, because its `r7` happens
to be built with an *older* Go toolchain and so reintroduces the stdlib CVEs
3.24 has already shed. So an `ALPINE_VERSION` bump is a decision about the base
image; it is never a remediation for these entries, and moving to edge chasing
one would regress the count.

The remaining option, building gocryptfs from source off `master`, would clear
them today and is deliberately rejected: see "Why not build gocryptfs from
source" below. Which leaves the honest position, and it is a comfortable one
rather than a resignation: nothing needs fixing, because the vulnerable
packages are not linked into the binary at all.

### What the current entries are really about

Every CVE in `.trivyignore.yaml` today is in `golang.org/x/crypto/ssh`,
`ssh/agent`, or `ssh/knownhosts`, and **none of those packages are linked into
the shipped binary.** gocryptfs is a filesystem tool with no SSH client and no
SSH server.

Two checks establish it, and the second is authoritative:

- `go list -deps ./...` at the `v2.6.1` tag lists the `x/crypto` packages
  genuinely in the build graph: `chacha20`, `chacha20poly1305`, `hkdf`,
  `internal/alias`, `internal/poly1305`, `pbkdf2`, `scrypt`. No `ssh` in any
  form, and the source imports `x/crypto/ssh` in zero files.
- `govulncheck ./...` at that tag, which is Go's own reachability analysis:
  *"Your code is affected by 0 vulnerabilities. This scan also found 0
  vulnerabilities in packages you import and 22 vulnerabilities in modules you
  require, but your code doesn't appear to call these vulnerabilities."*

gocryptfs's maintainer said the same in November 2025, on
[rfjakob/gocryptfs#973](https://github.com/rfjakob/gocryptfs/issues/973#issuecomment-3543578223):
"The mentioned vulnerable functions are not used by gocryptfs and, as far as I
can see, not even included in the gocryptfs binary."

**Do not try to re-verify this with `strings` or `go tool nm`.** Alpine ships
the binary stripped, so both return nothing for every package, including the
ones gocryptfs demonstrably uses; a zero there measures the strip, not
absence. `govulncheck -mode=binary` is actively misleading on a stripped
binary: it reports 21 vulnerabilities under a `=== Symbol Results ===` heading,
listing `ssh.Dial` five times as though verified present, which source mode at
the same tag contradicts outright. Use source mode, or an unstripped build.

They appear at all because Trivy resolves a Go binary's vulnerabilities at
**module** granularity, reading the module list out of the embedded build
info. One `golang.org/x/crypto v0.33.0` dependency therefore drags in every
CVE published against that module, whichever of its packages the linker
actually kept.

This is worth stating plainly because it changes what the expiry is for. These
are not reachable risks being tolerated until a fix arrives; the vulnerable
code is not in the artifact. What the expiry forces is re-verification of the
unreachability claim, since that is the part that could stop being true: a
future gocryptfs release could start linking `x/crypto/ssh` for some remote
feature, and nothing about a CVE list would announce it. When renewing an
entry, re-check that the binary still does not link the package, and say so.

`.github/workflows/security-ignore-audit.yml` runs on a schedule and checks
every entry against this repository's own code scanning history: whether it
still reproduces, and how close it is to its `expired_at`. It opens or
updates a single tracking issue when something needs attention. It is a
warning system, not the enforcement; `expired_at` is what actually forces
the re-decision if that workflow is ever broken, disabled, or its issue
ignored. It also never edits `.trivyignore.yaml` or the alerts it audits:
see "CI never autofixes" in `CLAUDE.md`.

### Why not build gocryptfs from source

The Dockerfile installs gocryptfs from Alpine's own package repository
(`apk add gocryptfs~=${GOCRYPTFS_VERSION}`), not by compiling gocryptfs's
`master` branch. `master` already carries the fix for every CVE in
`.trivyignore.yaml`: it has moved past the vendored `golang.org/x/crypto`
version the Alpine package still ships. Building from source would clear
most of the accepted-risk list immediately.

That trade is deliberate, not an oversight. The Alpine package is built,
tested, and signed as part of a distribution release process this project
did not do itself; a source build of an arbitrary upstream commit would ship
a binary nobody but this pipeline has ever tested, with no distribution
maintainer standing behind it. The CVEs in `.trivyignore.yaml` are a known,
bounded, documented cost with an expiry forcing periodic re-review. An
unsigned, untested binary that happens to have fewer known CVEs today is an
unbounded, undocumented one: it trades a finding the security tooling can
see and track for a class of risk the tooling has no way to see at all.
Given that choice, the tracked and expiring cost is the one worth taking.

**Be precise about what that distribution process does and does not cover**,
because "built, tested and signed by Alpine" is easy to over-read. Alpine
compiles the binary on its own builders and signs the resulting `apk`, so the
binary in this image is Alpine's work, not an upstream download. But Alpine's
*input* is not the git tag. `community/gocryptfs`'s APKBUILD fetches
`gocryptfs_v${pkgver}_src-deps.tar.gz`, a source tarball the gocryptfs
maintainer builds and signs on his own machine, with no CI provenance tying it
to a commit. Most comparable Alpine Go packages (`syncthing`, `rclone`, `age`,
`croc`) use GitHub's deterministic tag archive instead, so this is the
exception rather than the norm.

Verified for the version this image ships, rather than assumed. Against
`v2.6.1`: Alpine's pinned sha512 matches the published asset, all 206 `.go`
files are byte-identical to the git tag, `go.mod` and `go.sum` match,
`go mod verify` passes, and the shipped `vendor/` tree reproduces exactly from
a fresh `go mod vendor` against upstream modules. So every line Alpine
compiled is either the public tag or an upstream module matching its `go.sum`
hash.

That check was manual and covers one release, which is the actual gap: nothing
in the chain establishes it automatically, and a future tarball could diverge
from its tag with nothing noticing. It does not change the decision above, since
a source build off `master` would still put this pipeline in the position of
being the only party that ever built or tested the binary. It does mean the
argument rests on "Alpine compiles it, and the source is verifiable if someone
checks" rather than on an unbroken chain of attestations. Raised upstream as
[rfjakob/gocryptfs#1035](https://github.com/rfjakob/gocryptfs/issues/1035) and
with Alpine as
[aports#18435](https://gitlab.alpinelinux.org/alpine/aports/-/issues/18435).

This is why the CVEs in `.trivyignore.yaml` are unfixable from this side
for as long as it holds, and why they are accepted risk rather than a bug to
route around by quietly switching to a source build the next time this list
gets long. If that trade-off is ever revisited, it should be revisited
explicitly, here, not by a Dockerfile change nobody connects back to this
reasoning.

### Code scanning alerts do not carry architecture or version

Every published image is multi-platform (`linux/amd64` and `linux/arm64`),
and both Docker Scout and Trivy scan each platform separately, uploading
each as its own SARIF category (`scout-amd64`/`scout-arm64`,
`trivy-amd64`/`trivy-arm64`) so both platforms' findings reach the Security
tab. GitHub's code scanning alert, however, is keyed on the finding's rule
and location, not on the category the SARIF came in under: a Docker
Scout finding for a given CVE reports the same location
(the package as it exists in the image, not a per-architecture path) for
`linux/amd64` and `linux/arm64` alike, so both platforms' instances land on
one alert. The same is true across the release and nightly workflows'
separate analyses, and across every image version any of them has ever
scanned: one alert accumulates instances from every ref, category, and
workflow run that ever reproduced it, with no field on the alert itself
saying which platform or version any single instance came from. That
information exists (each instance's `ref` and `category`, readable through
the `code-scanning/alerts/<n>/instances` API), but nothing in the Security
tab's own UI surfaces it.

This was investigated as part of the ignore-list migration above and found
not worth working around. Making it visible would require Docker Scout's
SARIF to emit a distinct `location` per architecture, which it does not,
and the only way to force one from this side is to rewrite the SARIF after
the scan (for example, `jq`-ing a fake path segment onto the finding's
`artifactLocation.uri` before upload) so GitHub treats the two platforms as
different locations. That is a bigger hack than the problem is worth: it
would show a file path that doesn't exist to make an alert list a
platform, and no fingerprint games change what the alert already tells you
if you read the instance list instead of the summary. The honest answer is
that the Security tab's alert list is the wrong place to ask "does this
affect arm64," and the `instances` API is the right one; this repository is
not going to bend the SARIF to fix a UI limitation.

### Dismissal guidelines

A finding qualifies for dismissal only when it has a written accepted-risk
record behind it, in this file's "Accepted-risk CVEs" section above or
somewhere equivalent, before the dismissal happens, never as a bare API call
with no documentation to point at. The dismissal comment on the GitHub alert
should cite that record directly (which file, which section) so a reader
lands on the reasoning, not just the word "won't fix". A dismissal and a
`.trivyignore.yaml` entry are two halves of one decision: dismissing an
alert without a matching ignore-list entry, or adding an ignore-list entry
without dismissing the alert it corresponds to, leaves the two disagreeing
about whether a given CVE is accepted risk, which is exactly the drift
`security-ignore-audit.yml` checks for.

What does **not** qualify for dismissal:

- A finding below the CRITICAL/HIGH gate this repository enforces, or one
  with no accepted-risk record yet. Sub-gate findings and a genuinely
  unfixable one-off (this repository's `fuse` package has exactly one
  version available in the pinned Alpine release, so a CVE against it has no
  upgrade path either) still stay open until someone actually assesses them
  and writes down why, even when the assessment will likely end in "accept
  it". Dismissing a CVE nobody has individually looked at, just to make the
  Security tab read clean, is the failure mode this whole scheme exists to
  prevent: it grants blanket acceptance to risk nobody evaluated.
- A CVE in a transitive base-layer package that the base image's own next
  rebuild will fix (see "Accepted-risk CVEs" above). Dismissing it hides the
  day the fix actually lands instead of letting the scan confirm it.
- Anything without an `expired_at` and a `statement` describing what would
  resolve it. An ignore-list entry with no expiry is exactly the
  indefinitely-suppressed CVE this whole mechanism exists to rule out.

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
