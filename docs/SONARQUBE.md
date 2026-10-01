# SonarQube Cloud

How this repository uses SonarQube Cloud, what is checked where, and how to
see the same findings in VS Code before pushing. Project:
[`ivan-pinatti-labs_rsync-crypt`](https://sonarcloud.io/project/overview?id=ivan-pinatti-labs_rsync-crypt),
in the `ivan-pinatti-labs` organization, on the Free plan.

## What is checked, and where

| Check | Where it runs | What fails it |
| --- | --- | --- |
| SonarQube Cloud analysis and quality gate | `SonarQube` job in `sonarqube.yml`, every pull request and every push to `main` | Any new issue in new code, an unreviewed security hotspot, or new Python under 80% covered |
| 100% Python coverage, lines and branches | The same job, after the scan (`coverage report`) | Any line or branch under `scripts/` that no test reaches |
| 100% Python coverage, before a push | `python-coverage` pre-push hook in `.pre-commit-config.yaml` | The same, measured on the tests marked `scripts` |
| Findings while editing | SonarQube for IDE in VS Code, below | Nothing; it is advice, not a gate |

`SonarQube` is a required status check on `main`. Why it passes without
scanning on a merge queue commit, and why a fork's pull request fails it, is in
[MERGE_PIPELINE.md](MERGE_PIPELINE.md). Why Sonar replaced CodeQL and how
findings are marked is in [SECURITY.md](SECURITY.md#what-scans-what).

## Python coverage is held at 100%

The Free plan's quality gate is fixed at 80% coverage on new code and cannot be
raised, so the higher bar lives in `.coveragerc` instead: `fail_under = 100`,
with `branch = true` so every `if` has to be seen going both ways. The only
excluded line is `if __name__ == "__main__":`; each script's `main()` takes
`argv` so the tests drive it directly.

Run it locally the same way the hook does:

```bash
python3 -m venv tests/.venv
tests/.venv/bin/pip install --require-hashes --only-binary=:all: -r tests/requirements.txt
tests/.venv/bin/coverage run -m pytest -q -m scripts tests
tests/.venv/bin/coverage report
```

`report` lists every missing line and branch. The `scripts` tests need no
Docker; they replace the one `docker run` each script makes with a stand-in.
Shell has no coverage in SonarQube Cloud, so this is Python only.

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

Measured 2026-10-01, VS Code attached to a workbench: the workbench image
carries SonarQube for IDE, and this repository lists the `sonarqube-cloud`
egress set in `.devcontainer/egress-sets`, which connected mode needs
(`sonarcloud.io`, its API, scanner and events hosts, and SonarSource's
analyzer downloads). The extension's analysis runs in a JVM that ignores the
workbench's proxy variables; the workbench passes it the egress proxy as JVM
options at start, so nothing is configured here.

Bind the folder to `ivan-pinatti-labs_rsync-crypt` once in the workbench
(connected mode keeps its token in the editor's secret storage). Bound, it
analyzes with SonarQube Cloud's profile and reports the probe below as
`python:S1764`. Opened as a `git worktree` it logs a harmless JGit
"repository not found" error, since JGit cannot read linked worktrees.

```python
def _sonar_probe(value):
    if value == value:
        return 1
    return 0
```

Use that probe rather than a hardcoded password: a password is a security
hotspot, and hotspots never appear in the Problems panel.

---

See also: [SECURITY.md](SECURITY.md), [MERGE_PIPELINE.md](MERGE_PIPELINE.md),
[USAGE.md](USAGE.md#tests)
