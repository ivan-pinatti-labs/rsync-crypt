"""Keep every Trivy this repository runs on one image, pinned the same way.

Trivy runs in five places: the publish gate, the nightly report, the
ignore-list audit, the apk pin resolver and the two pre-push hooks. Each one
runs the official `docker.io/aquasec/trivy` image, pinned by tag and digest,
so Renovate's one custom manager for that image moves them together and the
shared `Pin Only` check grades the bump with its `image_ref_pin` grammar.

They used to drift. Workflows ran `aquasecurity/trivy-action`, which
downloaded a Trivy release named by its own `version:` input, a `with:` value
neither Renovate nor `Pin Only` could move, while the hooks and the resolver
named the image by tag alone; all of them sat on one release for months while
Trivy shipped two more. This test is what keeps that from coming back: a copy
left on an older pin, one pinned by tag alone, or a step that installs Trivy
some other way, fails here before it can disagree with the gate.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml"))
HOOKS = REPO_ROOT / ".pre-commit-config.yaml"
FILES = [*WORKFLOWS, HOOKS]

# Any mention of the image, pinned or not, so an unpinned one is caught too.
ANY_REFERENCE = re.compile(r"aquasec/trivy\S*")
PINNED = re.compile(r"^docker\.io/aquasec/trivy:[0-9][\w.-]*@sha256:[0-9a-f]{64}$")

# The workflows that scan, each declaring the image once for its steps.
SCANNING_WORKFLOWS = {
    "nightly-build.yml",
    "publish-image.yml",
    "resolve-apk-pins.yml",
    "security-ignore-audit.yml",
}
DECLARATION = re.compile(r"^  TRIVY_IMAGE: (?P<ref>\S+)$", re.MULTILINE)


def _references() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in FILES:
        for line in path.read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            for match in ANY_REFERENCE.finditer(line):
                # A reference written as `docker.io/aquasec/...` is matched
                # from `aquasec`; put the registry back to compare it whole.
                start = match.start()
                prefix = "docker.io/" if line[:start].endswith("docker.io/") else ""
                found.setdefault(path.name, []).append(prefix + match.group(0))
    return found


def test_every_reference_is_pinned_by_tag_and_digest():
    unpinned = sorted(
        f"{name}: {ref}"
        for name, refs in _references().items()
        for ref in refs
        if not PINNED.match(ref)
    )
    assert not unpinned, (
        "Trivy's image has to be docker.io/aquasec/trivy:<tag>@sha256:<digest> "
        "everywhere, or Renovate and Pin Only cannot move it:\n" + "\n".join(unpinned)
    )


def test_every_reference_is_the_same_pin():
    pins = {ref for refs in _references().values() for ref in refs}
    assert len(pins) == 1, (
        f"expected one Trivy pin across the repository, found {len(pins)}: "
        f"{sorted(pins)}"
    )


def test_each_scanning_workflow_declares_the_image_once():
    for name in sorted(SCANNING_WORKFLOWS):
        text = (REPO_ROOT / ".github/workflows" / name).read_text()
        declared = DECLARATION.findall(text)
        assert len(declared) == 1, (
            f"{name} should declare TRIVY_IMAGE once in its top-level env, "
            f"found {len(declared)}"
        )


def test_both_pre_push_scans_run_the_image():
    refs = _references().get(HOOKS.name, [])
    assert len(refs) == 2, (
        f"expected the image scan and the binary scan in {HOOKS.name}, "
        f"found {len(refs)} references"
    )


def test_nothing_installs_trivy_another_way():
    """A Trivy installed around the pin would scan with a version nobody bumps."""
    offenders = sorted(
        f"{path.name}: {line.strip()}"
        for path in FILES
        for line in path.read_text().splitlines()
        if re.search(r"uses:\s*aquasecurity/(trivy-action|setup-trivy)@", line)
    )
    assert not offenders, "\n".join(offenders)
