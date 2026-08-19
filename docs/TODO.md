# TODO

<!-- cspell:words checkmake coderabbit coderabbitai maxbodylength mbake minphony phonydeclared -->

## Outstanding: finish the pre-commit-checklists adoption

The adoption is built and verified but not yet merged. It is a stack of four
pull requests that must land in order, because each is based on the one below
it:

| PR                                                         | Branch                              | Contents                                                               | State                                 |
| ---------------------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------------- | ------------------------------------- |
| [#12](https://github.com/ivan-pinatti/rsync-crypt/pull/12) | `chore/add-lint-configs`            | `.editorconfig`, `.cspell.json`, `.yamllint.yml`, `.markdownlint.yaml` | ready, checks green, review requested |
| [#13](https://github.com/ivan-pinatti/rsync-crypt/pull/13) | `docs/readme-lint-clean`            | 144 markdownlint findings to 0                                         | draft, no CI yet                      |
| [#15](https://github.com/ivan-pinatti/rsync-crypt/pull/15) | `style/shfmt-and-ruff-format`       | shfmt + ruff sweep, one real typo                                      | draft, no CI yet                      |
| [#16](https://github.com/ivan-pinatti/rsync-crypt/pull/16) | `chore/adopt-pre-commit-checklists` | the swap itself, pinned `v2.2.0`                                       | draft, no CI yet                      |

**The state to preserve:** on `#16` all 14 checklists pass, with nothing
ignored or configured away. `pre-commit install` wires both the pre-commit and
commit-msg stages, and a real `git commit` with a non-conventional message was
confirmed rejected.

### Steps left

1. Merge `#12`, once its current head has been reviewed clean.
2. `#13` auto-retargets to `main`. **Mark it ready** so CodeRabbit reviews it
   (it skips drafts), wait for CI, which only runs now that its base is
   `main`, then merge. Repeat for `#15`, then `#16`.
3. After `#16`, delete the `ci:` block's replacement worry: it is already gone
   from `.pre-commit-config.yaml`, but confirm pre-commit.ci is still not
   installed on the repository.
4. Remaining docs pass: `README.md` still describes the old hook set and does
   not mention that local `pre-commit` runs now need Docker (for
   `hadolint-docker`, `actionlint-docker` and the dotenv image). `CLAUDE.md` is
   already updated.

### Two process traps found the hard way

Both cost a round trip; worth knowing before the next stacked change.

- **`pre-commit run --all-files` only sees tracked files.** A new file passes
  locally and fails in CI until it is `git add`ed. This bit the adoption twice.
- **CodeRabbit can stop reviewing without saying so**, through two separate
  mechanisms that are easy to confuse. The one that caused a twelve hour
  stall here was `auto_pause_after_reviewed_commits`, whose default of 5
  pauses automatic reviews on a branch once five commits have been reviewed;
  that is config-fixable and is now set to 0. The other is running out of the
  plan's included-review allowance, which **drops** a review rather than
  queueing it, so a push arriving during exhaustion is declined and never
  retried; that one has no setting at all. Either way a review has to be
  re-requested by hand, with `@coderabbitai review`, or
  `@coderabbitai full review` when the incremental logic has already marked
  the commits as seen.
- **Every CodeRabbit signal is time-independent unless you check it
  yourself.** A green _check_ is green on a skipped draft and on a
  rate-limited decline. A _resolved thread_ only means a later commit changed
  those lines, not that the fix was read. A _clean verdict comment_ can
  predate the current head by hours. And a clean review creates no review
  object at all, only a comment, so "no review object" does not mean "not
  reviewed". The only reliable test is comparing the verdict's timestamp
  against the head commit's.

### Once `pre-commit-checklists` ships its own TODO items

Tracked in that repo's `docs/TODO.md`; two of them change things here.

- When `templates/.yamllint.yml` stops fighting the Prettier hook inside
  `checklist-yaml`, the 14 permanent `too few spaces before comment` warnings
  on this repo's YAML go away. They are warnings only, since the upstream
  yamllint hook does not pass `--strict`.
- When `.markdown-link-check.json` support lands
  ([#12 there](https://github.com/ivan-pinatti/pre-commit-checklists/pull/12)),
  no change is needed here. The stars badge was repointed at the repository
  rather than ignored, so this repo needs no ignore file at all.

## Deferred items from the adoption

Each was found during the adoption and deliberately left alone rather than
folded into it, with the reason recorded so the decision does not have to be
re-derived.

Nothing here is broken today. Items 1 and 2 are the ones with real
consequences.

### 1. The Dockerfile's `USER` is inert at run time

`Dockerfile` ends with `USER 1000`, and `id` inside the image confirms that
resolves to `uid=1000(crypt) gid=1000(crypt)`. But **all ten `docker run`
invocations in the Makefile pass `--user root`**, so nothing that ships here
ever runs as `crypt`. The `USER` line only affects someone running the image
directly.

That is worth a decision rather than a change: either the Makefile has a
reason to force root that should be written down, or some of those targets
could drop to the unprivileged user. gocryptfs needs `SYS_ADMIN` and
`/dev/fuse`, which is the likely reason, but "likely" is not a recorded
rationale.

Not touched during adoption because it is a behavioural change to the backup
and restore paths, and the lint work had no business making one.

### 2. Pull requests targeting a non-`main` branch run no CI at all

`.github/workflows/pull-request-validation.yml` triggers on:

```yaml
on:
  pull_request:
    branches:
      - main
```

So a PR whose base is a feature branch runs neither the pre-commit job nor
the tests. This came up concretely during the adoption, which was a stack of
four PRs based on each other: three of them had **only** a CodeRabbit check
and no CI, and each only got a real run once it retargeted to `main` as the
one below it merged.

That is survivable but it forces strictly serial merging and makes a stacked
branch look greener than it is. Consider widening the trigger, for example:

```yaml
branches:
  - main
  - "chore/**"
  - "docs/**"
  - "fix/**"
  - "style/**"
```

### 3. `zizmor` flags the release action, and the finding is ignored

`.github/zizmor.yml` ignores `superfluous-actions` for `merge.yml`. zizmor
suggests replacing `ncipollo/release-action` with `gh release create` in a
script step, and **exits 11 on it even though the finding is informational**,
so it has to be either fixed or ignored; there is no "warn only" middle
ground reachable through `checklist-github-actions`.

Kept the action because it is pinned by SHA and carries `allowUpdates: true`,
which `gh release create` has no equivalent for: it errors on an existing tag
rather than updating it, so a faithful replacement needs a create-or-edit
fallback.

Worth doing on its own, where a mistake surfaces before the next release
rather than during it. If it is done, the ignore entry in
`.github/zizmor.yml` should go with it.

### 4. The Makefile is not linted, deliberately

This repo is Makefile-driven and its Makefile holds real logic, so the gap is
worth naming. Three tools were evaluated and all three were rejected:

- **checkmake** is wrong about this Makefile. It reported six
  `phonydeclared` findings (`r`, `ro`, `rr`, `rro`, `v`, `vr`) and a
  `minphony` for `clean`. **All seven are false**: every one of those targets
  is declared, on the `.PHONY` continuation lines. Isolated with a minimal
  fixture: an identical target set produces zero findings when `.PHONY` is on
  one line and a false finding when it uses a backslash continuation. Its
  `maxbodylength` default of 5 is also unusable on any real Makefile.
- **mbake** is a formatter, and reformats against this file's deliberate
  style: it collapses the aligned continuation indentation of the multi-line
  `docker run` blocks and the aligned `r:   restore` shorthands to a single
  tab. Exit 1 on the current file.
- **MegaLinter** has **no Make descriptor at all** across its 100+ linters,
  so "match what MegaLinter picks" and "lint nothing" are the same answer.

Revisit only if a Makefile linter appears that parses `.PHONY` continuations
correctly.

### 5. Files no hook covers

Minor, listed so it is a known set rather than a surprise.

| File                     | Why it is uncovered                                                                                                                                                                                                                                      |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CITATION.cff`           | YAML content, but `identify` tags it only `text`. Cannot be reached through `checklist-yaml`: `check-yaml` and `yamllint` both self-filter on `types: [yaml]`, so widening the id's own selector would not help. Needs its own hook entry if it matters. |
| `.secrets.baseline`      | Valid JSON with no extension, so untyped. Also deliberately in `.cspell.json`'s `ignorePaths`.                                                                                                                                                           |
| `tests/pytest.ini`       | Typed `ini`; no ini linter in the library.                                                                                                                                                                                                               |
| `.tool-versions`         | Plain text.                                                                                                                                                                                                                                              |
| `conf/*.txt`, `llms.txt` | Plain text by design. Spell-checked (`conf/*.txt` deliberately not excluded), but not otherwise validated.                                                                                                                                               |

### 6. `CLAUDE.md` records a stale gocryptfs revision

`CLAUDE.md` says:

> `GOCRYPTFS_VERSION="2.5"` resolves to `2.5.4-r8` in Alpine 3.23 community
> repo.

As of 2026-08-19 it resolves to `2.5.4-r11`, confirmed by running
`apk policy gocryptfs` in `alpine:3.23`; see the
[Alpine package page](https://pkgs.alpinelinux.org/packages?name=gocryptfs&branch=v3.23)
for the current revision. The `~=2.5` constraint in the Dockerfile is
unaffected and still correct; only the note is stale.

Worth deciding whether that line should name an exact revision at all, given
it will drift again on the next Alpine rebuild. The useful part of the note is
the `-bs` flag warning underneath it, which is not version-specific.

## CodeRabbit: automatic reviews stop silently on a busy branch

`.coderabbit.yaml` now sets `reviews.auto_review.auto_pause_after_reviewed_commits: 0`.
That is a change from the default of `5`, which pauses automatic reviews once
five commits on a branch have been reviewed.

**Why it matters:** the pause is silent. The CodeRabbit check still reports
green, so a pull request looks reviewed when nothing has read its current
head. Observed directly on
[pre-commit-checklists#12](https://github.com/ivan-pinatti/pre-commit-checklists/pull/12),
a branch with seven commits: reviews stopped after the fifth, and twelve
hours passed with no review of the head and no indication anything was
waiting. Recovery needs a manual `@coderabbitai review`.

**Still open, and not fixable by config:** CodeRabbit's included-review quota
**drops** a review rather than queueing it. A push arriving while the quota is
exhausted is declined and never retried.

The quota is plan-specific and rolling rather than a documented constant, so
take the figure from CodeRabbit itself rather than from here: on 2026-08-18 it
reported "Your plan provides up to 3 included reviews per hour" on a **Pro
Plus** plan, in the same comment that declined the review. Its own message
names the remaining allowance and the reset time, which is the only
authoritative source.

No configuration helps. Checked
[`schema.v2.json`](https://storage.googleapis.com/coderabbit_public_assets/schema.v2.json)
as fetched on 2026-08-19: no retry, backoff, queue or poll setting exists
anywhere in it. The only recovery is a manual `@coderabbitai review`, or
`@coderabbitai full review` when the incremental logic has already marked the
commits as seen.

Worth knowing when judging whether a pull request is really reviewed:

- A green CodeRabbit **check** does not mean a review happened. It is green on
  a skipped draft and on a rate-limited decline.
- A **resolved** thread does not mean a fix was verified. CodeRabbit
  auto-resolves threads whose lines a later commit changed, which means the
  code moved, not that it was re-read.
- The reliable signal is comparing CodeRabbit's latest review timestamp
  against the head commit's timestamp.

`drafts: false` is deliberate and stays. CodeRabbit is a GitHub App posting a
check, not a workflow job, so it cannot be ordered after the pre-commit job
with a `needs:` dependency. Skipping drafts is the lever instead: open as a
draft, let the pre-commit and test jobs run and report, fix whatever they find
and push, then mark the pull request ready once both pass. The checks
themselves change nothing in CI; the author does. The point is that a review
slot is spent on a diff that has already survived them, rather than on defects
a linter would have named for free.
