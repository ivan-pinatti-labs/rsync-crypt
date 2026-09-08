#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ivan Pinatti
"""Check `.trivyignore.yaml` against this repository's own code scanning
history and report what needs a human decision.

Every accepted-risk entry in `.trivyignore.yaml` carries an `expired_at`
date, which is what actually forces a re-decision (see docs/SECURITY.md's
"Every entry expires"). This script is the early warning ahead of that: it
flags an entry that is approaching or past its expiry, and an entry whose
CVE ID no longer reproduces at all (meaning the underlying issue was fixed
upstream and the entry is now suppressing nothing). It also flags the other
direction: a dismissed code scanning alert with no matching
`.trivyignore.yaml` entry, which is the "two halves of one decision" drifting
apart that docs/SECURITY.md's "Dismissal guidelines" warns against.

Reproduction is judged from `--unfiltered-findings`, a Trivy JSON report the
caller produced with no ignore file, and not from code scanning alert state.
Alert state cannot answer the question: the SARIF those alerts come from
already had `.trivyignore.yaml` applied, so an ignored CVE is missing from it
because it is ignored. Reading that absence as "fixed upstream" is circular,
and would have this script recommend dropping the very entries doing the
suppressing.

This never edits `.trivyignore.yaml`, never dismisses or reopens an alert,
and never fails a build. It only produces a report; whoever reads the
tracking issue it feeds decides what to do. See "CI never autofixes" in
CLAUDE.md.

Used by .github/workflows/security-ignore-audit.yml, which fetches the open
and dismissed alert lists with `gh api` and passes them to this script as
JSON files, so the parsing and decision logic here can be exercised with no
network access and no `gh` binary at all; see tests/test_audit_security_ignores.py.

Also runnable by hand, from a checkout with `gh` authenticated:

    gh api "repos/OWNER/REPO/code-scanning/alerts?state=open&per_page=100" \\
        --paginate > /tmp/open.json
    gh api "repos/OWNER/REPO/code-scanning/alerts?state=dismissed&per_page=100" \\
        --paginate > /tmp/dismissed.json
    python3 scripts/audit-security-ignores.py \\
        --ignorefile .trivyignore.yaml \\
        --open-alerts /tmp/open.json \\
        --dismissed-alerts /tmp/dismissed.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IGNOREFILE = REPO_ROOT / ".trivyignore.yaml"

DEFAULT_WARN_DAYS = 14

# Matches one entry's `id:` line under `vulnerabilities:`, and the
# `expired_at:`/`statement:` lines that can follow it before the next `- id:`
# or a top-level key. This is a narrow, line-oriented reader for the one flat
# shape `.trivyignore.yaml` actually uses here (a plain list of id/statement/
# expired_at mappings under `vulnerabilities:`), not a general YAML parser: a
# full parser is not worth a dependency this repo's other scripts have none
# of (see scripts/resolve-apk-pins.py's own header) for a file this script
# also writes the only entries in.
_ID_LINE = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$")
_EXPIRED_AT_LINE = re.compile(r"^\s*expired_at:\s*(\d{4}-\d{2}-\d{2})\s*$")
_STATEMENT_LINE = re.compile(r"^\s*statement:\s*(.*\S)\s*$")
_TOP_LEVEL_KEY = re.compile(r"^\S")


@dataclass(frozen=True)
class IgnoreEntry:
    id: str
    expired_at: date | None
    statement: str | None


def parse_ignorefile(text: str) -> list[IgnoreEntry]:
    """Parse the `vulnerabilities:` entries out of a `.trivyignore.yaml`."""
    entries: list[IgnoreEntry] = []
    current_id: str | None = None
    current_expired_at: date | None = None
    current_statement_lines: list[str] = []
    in_vulnerabilities = False

    def flush() -> None:
        if current_id is not None:
            statement = " ".join(current_statement_lines).strip() or None
            entries.append(IgnoreEntry(current_id, current_expired_at, statement))

    for raw_line in text.splitlines():
        line = (
            raw_line.split(" #", 1)[0].rstrip()
            if not raw_line.lstrip().startswith("#")
            else ""
        )
        if not line.strip():
            continue

        if line.strip() == "vulnerabilities:":
            in_vulnerabilities = True
            continue

        if not in_vulnerabilities:
            continue

        # A new top-level key (no leading whitespace) other than
        # `vulnerabilities:` itself ends that section.
        if _TOP_LEVEL_KEY.match(line):
            flush()
            current_id = None
            in_vulnerabilities = False
            continue

        id_match = _ID_LINE.match(line)
        if id_match:
            flush()
            current_id = id_match.group(1)
            current_expired_at = None
            current_statement_lines = []
            continue

        expired_match = _EXPIRED_AT_LINE.match(line)
        if expired_match:
            current_expired_at = date.fromisoformat(expired_match.group(1))
            continue

        statement_match = _STATEMENT_LINE.match(line)
        if statement_match:
            value = statement_match.group(1).strip()
            # A YAML folded block scalar ('>-') or quoted string: strip the
            # scalar indicator and surrounding quotes from the first line;
            # continuation lines (plain indented text, matched by neither
            # pattern above) are appended as-is by the fallback branch below.
            value = value.lstrip(">-|").strip()
            value = value.strip("\"'")
            current_statement_lines.append(value)
            continue

        # A continuation line of a multi-line statement (folded or quoted):
        # anything else indented under the current entry that isn't an id/
        # expired_at line.
        if current_id is not None and line.strip():
            current_statement_lines.append(line.strip().strip("\"'"))

    flush()
    return entries


def load_unfiltered_finding_ids(path: Path | None) -> set[str] | None:
    """CVE IDs from a `trivy --format json` report run with no ignore file.

    Returns None when no report was supplied, which is what tells the caller
    to fall back to inferring reproduction from alert state. That fallback is
    strictly worse and the workflow always passes a report: an alert list is
    built from SARIF that already had `.trivyignore.yaml` applied, so an
    ignored CVE is missing from it because it is ignored, not because it
    stopped reproducing. Only an unfiltered scan can tell those apart.
    """
    if path is None:
        return None
    data = json.loads(path.read_text())
    ids: set[str] = set()
    for result in data.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            cve_id = vuln.get("VulnerabilityID")
            if cve_id:
                ids.add(cve_id)
    return ids


def load_alert_rule_ids(path: Path | None) -> set[str]:
    """The set of `rule.id` values across a `gh api` alert-list JSON dump.

    Accepts a plain JSON array (`gh api ... > file.json`) and whatever
    `gh api --paginate` produces: per `gh help api`, "each page is a
    separate JSON array", written back to back with no guaranteed
    separator between them, so this reads the file as a stream of
    consecutive JSON documents rather than assuming one page per line.
    """
    if path is None:
        return set()
    text = path.read_text()
    ids: set[str] = set()
    for chunk in _iter_json_chunks(text):
        for alert in chunk:
            rule_id = (alert.get("rule") or {}).get("id")
            if rule_id:
                ids.add(rule_id)
    return ids


def _iter_json_chunks(text: str) -> list[list[dict]]:
    """Split a `gh api [--paginate]` dump into its top-level JSON values.

    Each value is normally a JSON array (one page of alerts); a lone object
    is wrapped in a list so callers can iterate uniformly either way.
    """
    decoder = json.JSONDecoder()
    chunks = []
    idx = 0
    length = len(text)
    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        data, end = decoder.raw_decode(text, idx)
        chunks.append(data if isinstance(data, list) else [data])
        idx = end
    return chunks


@dataclass
class EntryStatus:
    entry: IgnoreEntry
    reproduces: bool
    dismissed_on_github: bool
    days_to_expiry: int


def evaluate_entries(
    entries: list[IgnoreEntry],
    open_ids: set[str],
    dismissed_ids: set[str],
    today: date,
    unfiltered_ids: set[str] | None = None,
) -> list[EntryStatus]:
    statuses = []
    for entry in entries:
        days_to_expiry = (entry.expired_at - today).days if entry.expired_at else 10**9
        if unfiltered_ids is not None:
            # An unfiltered scan was supplied, so it is the authority on
            # whether the finding is still there. Alert state says only
            # whether GitHub currently displays it, which for an ignored CVE
            # is a different question entirely.
            reproduces = entry.id in unfiltered_ids
        else:
            reproduces = entry.id in open_ids or entry.id in dismissed_ids
        statuses.append(
            EntryStatus(
                entry=entry,
                reproduces=reproduces,
                dismissed_on_github=entry.id in dismissed_ids,
                days_to_expiry=days_to_expiry,
            )
        )
    return statuses


def find_unmatched_dismissals(
    entries: list[IgnoreEntry], dismissed_ids: set[str]
) -> list[str]:
    entry_ids = {e.id for e in entries}
    return sorted(dismissed_ids - entry_ids)


def render_report(
    statuses: list[EntryStatus],
    unmatched_dismissals: list[str],
    today: date,
    warn_days: int,
) -> tuple[bool, str]:
    """Return (needs_issue, markdown_report)."""
    expired = [s for s in statuses if s.entry.expired_at and s.days_to_expiry < 0]
    expiring_soon = [
        s for s in statuses if s.entry.expired_at and 0 <= s.days_to_expiry <= warn_days
    ]
    stale = [s for s in statuses if not s.reproduces]
    out_of_sync = [s for s in statuses if s.reproduces and not s.dismissed_on_github]
    no_expiry = [s for s in statuses if s.entry.expired_at is None]

    needs_issue = bool(
        expired
        or expiring_soon
        or stale
        or out_of_sync
        or no_expiry
        or unmatched_dismissals
    )

    lines = [
        f"Audit run: {today.isoformat()}",
        f"Ignore-list entries checked: {len(statuses)}",
        "",
    ]

    def section(title: str, items: list[str]) -> None:
        lines.append(f"### {title}")
        if not items:
            lines.append("None.")
        else:
            lines.extend(f"- {item}" for item in items)
        lines.append("")

    section(
        "Expired (Trivy no longer suppresses these; re-decide now)",
        [
            f"`{s.entry.id}` expired {-s.days_to_expiry} day(s) ago "
            f"(expired_at: {s.entry.expired_at})."
            for s in expired
        ],
    )
    section(
        f"Expiring within {warn_days} days",
        [
            f"`{s.entry.id}` expires in {s.days_to_expiry} day(s) "
            f"(expired_at: {s.entry.expired_at})."
            for s in expiring_soon
        ],
    )
    section(
        "No longer reproducing (candidate for removal)",
        [
            f"`{s.entry.id}` no longer reproduces, so the entry is now "
            "suppressing nothing and looks fixed upstream. Confirm against a "
            "live scan and remove it, per docs/SECURITY.md."
            for s in stale
        ],
    )
    section(
        "Reproducing but not dismissed on GitHub (out of sync)",
        [
            f"`{s.entry.id}` is documented as accepted risk in "
            ".trivyignore.yaml but its code scanning alert is still open, "
            "not dismissed."
            for s in out_of_sync
        ],
    )
    section(
        "Missing an expiry date",
        [
            f"`{s.entry.id}` has no `expired_at`; every entry needs one."
            for s in no_expiry
        ],
    )
    section(
        "Dismissed alerts with no matching .trivyignore.yaml entry",
        [
            f"Rule `{rule_id}` is dismissed on GitHub but "
            ".trivyignore.yaml has no entry for it. Confirm it still cites "
            "a written accepted-risk record (docs/SECURITY.md)."
            for rule_id in unmatched_dismissals
        ],
    )

    return needs_issue, "\n".join(lines).rstrip() + "\n"


def _parse_date(value: str) -> date:
    """Parse a `YYYY-MM-DD` `--today` override as a plain calendar date."""
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).date()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ignorefile", type=Path, default=DEFAULT_IGNOREFILE)
    parser.add_argument("--open-alerts", type=Path, default=None)
    parser.add_argument("--dismissed-alerts", type=Path, default=None)
    parser.add_argument(
        "--unfiltered-findings",
        type=Path,
        default=None,
        help=(
            "A `trivy --format json` report produced with no ignore file. "
            "When given, it decides whether an entry still reproduces, "
            "instead of inferring it from already-filtered alert state."
        ),
    )
    parser.add_argument(
        "--warn-days",
        type=int,
        default=DEFAULT_WARN_DAYS,
        help="Flag an entry as 'expiring soon' this many days before expired_at.",
    )
    parser.add_argument(
        "--today",
        type=_parse_date,
        default=None,
        help="Override 'today' (testing only); defaults to the real UTC date.",
    )
    args = parser.parse_args()

    # UTC, not local time: this runs on a GitHub-hosted runner in
    # security-ignore-audit.yml, and `expired_at` dates are calendar dates
    # with no timezone of their own, so UTC is the one unambiguous "today"
    # to compare them against.
    today = args.today or datetime.now(timezone.utc).date()
    entries = parse_ignorefile(args.ignorefile.read_text())
    open_ids = load_alert_rule_ids(args.open_alerts)
    dismissed_ids = load_alert_rule_ids(args.dismissed_alerts)

    unfiltered_ids = load_unfiltered_finding_ids(args.unfiltered_findings)

    statuses = evaluate_entries(entries, open_ids, dismissed_ids, today, unfiltered_ids)
    unmatched_dismissals = find_unmatched_dismissals(entries, dismissed_ids)
    needs_issue, report = render_report(
        statuses, unmatched_dismissals, today, args.warn_days
    )

    print(f"needs_issue={'true' if needs_issue else 'false'}")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
