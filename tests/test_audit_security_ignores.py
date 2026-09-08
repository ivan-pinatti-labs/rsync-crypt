"""Tests for scripts/audit-security-ignores.py.

Exercises only the pure parsing and decision functions
(`parse_ignorefile`, `load_alert_rule_ids`, `evaluate_entries`,
`find_unmatched_dismissals`, `render_report`), never `main`'s file/CLI
plumbing: these run anywhere, with no network access and no `gh` binary, the
same way the sibling `resolve-apk-pins.py` tests do.

    pytest -m scripts tests/test_audit_security_ignores.py
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "audit-security-ignores.py"

pytestmark = pytest.mark.scripts


def _load_module():
    # Hyphenated filename, same loading shape resolve-apk-pins.py's tests use.
    spec = importlib.util.spec_from_file_location("audit_security_ignores", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit = _load_module()

SAMPLE_IGNOREFILE = """\
# Header comment, and a second line.
#
# More header prose, deliberately containing a 'vulnerabilities:'-looking
# word to make sure the section only starts at the real top-level key below.

vulnerabilities:
  - id: CVE-2025-22869
    statement: >-
      golang.org/x/crypto v0.33.0 embedded in gocryptfs v2.6.1, fixed
      upstream in v0.52.0. Resolves on review at expiry.
    expired_at: 2026-12-07
  - id: CVE-2026-39828
    statement: "A quoted one-line statement."
    expired_at: 2026-12-07
  - id: CVE-2026-99999
    expired_at: 2026-01-01
"""


def test_parse_ignorefile_reads_every_entry():
    entries = audit.parse_ignorefile(SAMPLE_IGNOREFILE)
    assert [e.id for e in entries] == [
        "CVE-2025-22869",
        "CVE-2026-39828",
        "CVE-2026-99999",
    ]


def test_parse_ignorefile_folds_a_multiline_statement():
    entries = audit.parse_ignorefile(SAMPLE_IGNOREFILE)
    first = entries[0]
    assert first.expired_at == date(2026, 12, 7)
    assert "golang.org/x/crypto v0.33.0" in first.statement
    assert "Resolves on review at expiry." in first.statement


def test_parse_ignorefile_strips_quotes_from_a_quoted_statement():
    entries = audit.parse_ignorefile(SAMPLE_IGNOREFILE)
    second = entries[1]
    assert second.statement == "A quoted one-line statement."


def test_parse_ignorefile_allows_a_missing_statement():
    entries = audit.parse_ignorefile(SAMPLE_IGNOREFILE)
    third = entries[2]
    assert third.statement is None
    assert third.expired_at == date(2026, 1, 1)


def test_parse_ignorefile_matches_the_real_repository_file():
    # The actual file this script audits in CI. Twelve entries today (see
    # .trivyignore.yaml's own header for why); this assertion is meant to
    # break, loudly, the day that count changes without this test being
    # updated alongside it.
    real_file = REPO_ROOT / ".trivyignore.yaml"
    entries = audit.parse_ignorefile(real_file.read_text())
    assert len(entries) == 12
    assert all(e.expired_at is not None for e in entries)
    assert all(e.statement for e in entries)


def test_load_alert_rule_ids_reads_a_plain_json_array(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text(
        '[{"rule": {"id": "CVE-2025-22869"}}, {"rule": {"id": "CVE-2026-1"}}]'
    )
    assert audit.load_alert_rule_ids(path) == {"CVE-2025-22869", "CVE-2026-1"}


def test_load_alert_rule_ids_reads_paginated_pages_on_separate_lines(tmp_path):
    path = tmp_path / "alerts.json"
    path.write_text(
        '[{"rule": {"id": "CVE-2025-22869"}}]\n[{"rule": {"id": "CVE-2026-1"}}]\n'
    )
    assert audit.load_alert_rule_ids(path) == {"CVE-2025-22869", "CVE-2026-1"}


def test_load_alert_rule_ids_reads_paginated_pages_with_no_separator(tmp_path):
    # `gh help api`: "--paginate" writes one JSON array per page with no
    # guaranteed separator between them, so two pages can land back to back
    # on the same line with nothing between the closing and opening bracket.
    path = tmp_path / "alerts.json"
    path.write_text(
        '[{"rule": {"id": "CVE-2025-22869"}}][{"rule": {"id": "CVE-2026-1"}}]'
    )
    assert audit.load_alert_rule_ids(path) == {"CVE-2025-22869", "CVE-2026-1"}


def test_load_alert_rule_ids_of_none_is_empty_set():
    assert audit.load_alert_rule_ids(None) == set()


def test_evaluate_entries_flags_expired_and_reproducing():
    entries = [
        audit.IgnoreEntry("CVE-A", date(2026, 1, 1), "old, expired"),
        audit.IgnoreEntry("CVE-B", date(2027, 1, 1), "future, fine"),
        audit.IgnoreEntry("CVE-C", None, "no expiry at all"),
    ]
    statuses = audit.evaluate_entries(
        entries, open_ids={"CVE-A"}, dismissed_ids={"CVE-B"}, today=date(2026, 6, 1)
    )
    by_id = {s.entry.id: s for s in statuses}

    assert by_id["CVE-A"].days_to_expiry < 0
    assert by_id["CVE-A"].reproduces is True
    assert by_id["CVE-A"].dismissed_on_github is False

    assert by_id["CVE-B"].days_to_expiry > 0
    assert by_id["CVE-B"].reproduces is True
    assert by_id["CVE-B"].dismissed_on_github is True

    assert by_id["CVE-C"].reproduces is False
    assert by_id["CVE-C"].dismissed_on_github is False


TRIVY_REPORT = """\
{
  "Results": [
    {
      "Target": "bin/gocryptfs",
      "Type": "gobinary",
      "Vulnerabilities": [
        {"VulnerabilityID": "CVE-A", "Severity": "HIGH"},
        {"VulnerabilityID": "CVE-B", "Severity": "CRITICAL"}
      ]
    },
    {"Target": "no vulnerabilities key at all", "Type": "gobinary"}
  ]
}
"""


def test_load_unfiltered_finding_ids_reads_a_trivy_report(tmp_path):
    path = tmp_path / "unfiltered.json"
    path.write_text(TRIVY_REPORT)
    assert audit.load_unfiltered_finding_ids(path) == {"CVE-A", "CVE-B"}


def test_load_unfiltered_finding_ids_of_none_is_none():
    # None, not an empty set: an empty set would mean "nothing reproduces",
    # which would mark every entry stale. None means "no signal supplied".
    assert audit.load_unfiltered_finding_ids(None) is None


def test_load_unfiltered_finding_ids_handles_a_clean_report(tmp_path):
    path = tmp_path / "clean.json"
    path.write_text('{"Results": []}')
    assert audit.load_unfiltered_finding_ids(path) == set()


def test_unfiltered_scan_overrides_alert_state():
    """An unfiltered scan decides reproduction, not the alert lists.

    This is the circularity the audit exists to avoid: both CI workflows
    apply `.trivyignore.yaml` before uploading their SARIF, so an ignored CVE
    is absent from the alert lists because it is ignored. Reading that
    absence as "fixed upstream" would recommend deleting a live suppression.
    """
    entries = [
        audit.IgnoreEntry("CVE-STILL-THERE", date(2027, 1, 1), "s"),
        audit.IgnoreEntry("CVE-GONE", date(2027, 1, 1), "s"),
    ]
    # Neither CVE appears in either alert list, which is exactly what an
    # ignore-filtered upload produces. The scan is what tells them apart.
    statuses = audit.evaluate_entries(
        entries,
        open_ids=set(),
        dismissed_ids=set(),
        today=date(2026, 6, 1),
        unfiltered_ids={"CVE-STILL-THERE"},
    )
    by_id = {s.entry.id: s for s in statuses}

    assert by_id["CVE-STILL-THERE"].reproduces is True
    assert by_id["CVE-GONE"].reproduces is False


def test_absent_unfiltered_scan_falls_back_to_alert_state():
    entries = [audit.IgnoreEntry("CVE-A", date(2027, 1, 1), "s")]
    statuses = audit.evaluate_entries(
        entries,
        open_ids={"CVE-A"},
        dismissed_ids=set(),
        today=date(2026, 6, 1),
        unfiltered_ids=None,
    )
    assert statuses[0].reproduces is True


def test_find_unmatched_dismissals():
    entries = [audit.IgnoreEntry("CVE-A", date(2026, 1, 1), "s")]
    result = audit.find_unmatched_dismissals(
        entries, dismissed_ids={"CVE-A", "CVE-ORPHAN"}
    )
    assert result == ["CVE-ORPHAN"]


def test_render_report_clean_state_needs_no_issue():
    entries = [audit.IgnoreEntry("CVE-A", date(2027, 1, 1), "s")]
    statuses = audit.evaluate_entries(
        entries, open_ids=set(), dismissed_ids={"CVE-A"}, today=date(2026, 6, 1)
    )
    needs_issue, report = audit.render_report(
        statuses, [], date(2026, 6, 1), warn_days=14
    )
    assert needs_issue is False
    # In sync, not expiring soon, not expired: every section reports clean,
    # and CVE-A itself never appears as a finding.
    assert "`CVE-A`" not in report


@pytest.mark.parametrize(
    "open_ids,dismissed_ids,expired_at,expect_needs_issue",
    [
        (set(), set(), date(2026, 1, 1), True),  # stale: reproduces nowhere
        ({"CVE-A"}, set(), date(2027, 1, 1), True),  # open but not dismissed
        (set(), {"CVE-A"}, date(2026, 1, 1), True),  # expired
        (set(), {"CVE-A"}, date(2026, 6, 15), True),  # expiring within 14 days
        (set(), {"CVE-A"}, date(2027, 1, 1), False),  # healthy: in sync, not expiring
    ],
)
def test_render_report_needs_issue_matrix(
    open_ids, dismissed_ids, expired_at, expect_needs_issue
):
    entries = [audit.IgnoreEntry("CVE-A", expired_at, "s")]
    statuses = audit.evaluate_entries(
        entries, open_ids, dismissed_ids, today=date(2026, 6, 1)
    )
    needs_issue, _ = audit.render_report(statuses, [], date(2026, 6, 1), warn_days=14)
    assert needs_issue is expect_needs_issue


def test_render_report_flags_an_unmatched_dismissal():
    needs_issue, report = audit.render_report(
        [], ["CVE-ORPHAN"], date(2026, 6, 1), warn_days=14
    )
    assert needs_issue is True
    assert "CVE-ORPHAN" in report
