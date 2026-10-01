# SonarQube Cloud

How this repository uses SonarQube Cloud, what is checked where, and how to
see the same findings in VS Code before pushing. Project:
[`ivan-pinatti-labs_rsync-crypt`](https://sonarcloud.io/project/overview?id=ivan-pinatti-labs_rsync-crypt),
in the `ivan-pinatti-labs` organization, on the Free plan.

## What is checked, and where

| Check | Where it runs | What fails it |
| --- | --- | --- |
| SonarQube Cloud analysis and quality gate | `SonarQube` job in `sonarqube.yml`, every pull request and every push to `main` | Any new issue in new code, an unreviewed security hotspot, or new code under 80% covered |
| 100% coverage: Python lines and branches, shell lines | The same job: `make coverage` before the scan, its verdict after it | Any line or branch under `scripts/*.py`, or any line of the shell scripts and dotfiles, that no test reaches |
| 100% coverage, before a push | `coverage` pre-push hook in `.pre-commit-config.yaml`, which runs `make coverage` | The same |
| Findings while editing | SonarQube for IDE in VS Code, below | Nothing; it is advice, not a gate |

`SonarQube` is a required status check on `main`. Why it passes without
scanning on a merge queue commit, and why a fork's pull request fails it, is in
[MERGE_PIPELINE.md](MERGE_PIPELINE.md). Why Sonar replaced CodeQL and how
findings are marked is in [SECURITY.md](SECURITY.md#what-scans-what).

## Coverage is held at 100%

The Free plan's quality gate is fixed at 80% coverage on new code and cannot be
raised, so the higher bar lives in this repository instead, in one Makefile
target, `make coverage`, which CI and the pre-push hook both run:

- **Python**, the tooling under `scripts/`, by lines and branches, under
  coverage.py. `.coveragerc` sets `fail_under = 100` and `branch = true`, so
  every `if` has to be seen going both ways. The only excluded line is
  `if __name__ == "__main__":`; each script's `main()` takes `argv` so the
  tests drive it directly. The tests marked `scripts` are the ones measured,
  so a test for a script under `scripts/` carries that mark.
- **Shell**, by lines (kcov has no branch data for bash), under kcov:
  `backup.sh`, `restore.sh` and `view.sh`, which the image runs, and
  `files/bash/.bashrc` and `.bash_aliases`, which it copies into each home
  directory. `tests/shell/run.sh` runs every `tests/shell/*.test.sh`, and each
  of those runs its script as its own bash process once per case, with
  gocryptfs, rsync, sshfs, fusermount, ssh-keygen, sshd, pkill and sleep
  replaced by stand-ins on `PATH`. The dotfiles are sourced, the way a shell
  reads them. The real tools are exercised by the image tests in the `Tests`
  job instead.

Both run in podman containers that see the source only as a tar stream on
standard input and write their report into one empty scratch directory; the
Makefile's comment above the target has the rest. Run it the same way CI
does:

```bash
make coverage
```

It prints every missing Python line and branch, and every uncovered shell line
by number, and leaves `coverage/coverage.xml` and `coverage/shell.xml` behind.
It needs podman, and network for the pip install inside the Python container.

SonarQube Cloud reads both reports. It has no importer of its own for shell
coverage, so `scripts/kcov-to-sonar.py` rewrites kcov's Cobertura report into
SonarQube's generic coverage format (`sonar.coverageReportPaths`); that same
script is what fails the shell below 100%, since kcov has no threshold.

kcov is told to leave out three kinds of line, all on the `coverage` target:

- An empty case arm (`a) ;;`) and the redirection after a loop
  (`done <file`). kcov lists both as code, but neither produces a trace line
  however the script runs, so they would always read as missed.
- A block between `kcov-exclude-start` and `kcov-exclude-end` comments, for
  code the script cannot reach on its own. There is one: the unknown value arm
  of `__build_network_mount_excludes` in `backup.sh`, which the script never
  reaches because it validates `BACKUP_EXCLUDE_NETWORK_MOUNTS` at startup, and
  which `tests/test_network_mounts.py` runs directly.

Three overrides exist in the scripts for the tests alone, and the Makefile
never sets them: `RSYNC_CRYPT_TEST_MOUNTINFO` hands `backup.sh` a synthetic
mount table instead of `/proc/self/mountinfo`, and `RSYNC_CRYPT_TEST_SSH_DIR`
and `RSYNC_CRYPT_TEST_SSHD` point `view.sh` at a scratch key directory and a
stand-in sshd instead of `/root/.ssh` and `/usr/sbin/sshd`.

## Seeing findings in VS Code

[SonarQube for IDE](https://marketplace.visualstudio.com/items?itemName=SonarSource.sonarlint-vscode)
(extension `SonarSource.sonarlint-vscode`) reports issues as you edit. In
connected mode it uses this project's rules and hides findings already marked
false positive or accepted in SonarQube Cloud, so what it shows matches what
the `SonarQube` check will say. Nothing in this repository's scripts needs
wiring for it.

It is not a git hook and cannot be one on the Free plan: the SonarQube CLI's
git hooks only scan for secrets (which `detect-secrets` already does here), and
its local code analysis needs a Team or Enterprise plan. Running the scanner
itself before a push would upload a branch analysis with your token for a
result the pull request produces anyway.

### Setup

1. Install the extension. `.vscode/extensions.json` recommends it, so VS Code
   offers it when the folder is opened.
2. Create a user token in SonarQube Cloud under
   [My Account, Security](https://sonarcloud.io/account/security), or reuse one
   you already have. Keep it out of the repository; a file such as
   `~/.config/sonarqube-cloud/token` with mode `600` is a reasonable home.
3. In VS Code, open **SonarQube Setup**, then **Connected Mode**, and choose
   **Add SonarQube Cloud Connection**. Pick the **EU** region, paste the token,
   select the `ivan-pinatti-labs` organization and save. VS Code keeps the token
   in its own secret storage, not in a settings file.
4. Reopen the folder. The extension finds `.sonarlint/connectedMode.json`,
   which names the organization and project key, and offers to bind to it.
   Accept.

The binding file holds no credential, which is why it is committed: every
contributor binds to the same project with their own token. If the extension
ever exports a binding file that differs from the committed one, commit the
exported version.

### What it shows, and what it does not

Measured on 2026-10-01 in connected mode, VS Code on the host:

- **Python issues show up on save**, in the Problems panel and in the
  extension's own output channel (View, Output, SonarQube for IDE). A
  deliberate `if value == value:` was reported as `python:S1764`.
- **Shell findings do not.** A single bracket test that the CI scan reports as
  `shelldre:S7688` raised nothing in the editor, so the extension does not run
  Sonar's shell analyzer locally. shellcheck still runs on every commit, and
  the `SonarQube` check reports Sonar's shell rules on the pull request.
- **Security hotspots are not issues.** A hard-coded `password = "..."` is a
  hotspot in Sonar way, not an issue, and does not appear in the Problems
  panel. Hotspots are reviewed in SonarQube Cloud.
- **Taint analysis runs on the server only**, so the `pythonsecurity` rules
  (the "LLM-supplied CLI arguments" family) appear in CI, never in the editor.

When nothing appears for a Python file, check the output channel first: no
"Analyzing" line on save means the extension is not running in that window.
In a VS Code window attached to a container, the Extensions view shows
whether it is installed there or only on the host.

### Inside a devcontainer-airlock workbench

Not tested yet. The measurements above were taken in VS Code on the host. The
extension needs `sonarcloud.io` (and SonarSource's download servers for its
analyzers), and `.devcontainer/egress-sets` has no set for either, so expect
it to need an egress set for SonarQube Cloud added to devcontainer-airlock
first. Use it from VS Code on the host until then. The CI check and the
pre-push hook do not depend on it. In a workbench, run the coverage target
as `l2 --engine --net -- make coverage`: it starts containers of its own and
installs `coverage` and `pytest` from PyPI.

---

See also: [SECURITY.md](SECURITY.md), [MERGE_PIPELINE.md](MERGE_PIPELINE.md),
[USAGE.md](USAGE.md#tests)
