"""Tests for scripts/kcov-to-sonar.py.

The report fixtures are the shape kcov writes: a <class> per traced script,
its filename relative to the directory kcov ran in, and a <line> with a hit
count for every line kcov considers executable.

    pytest -m scripts tests/test_kcov_to_sonar.py
"""

from __future__ import annotations

import importlib.util
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "kcov-to-sonar.py"

pytestmark = pytest.mark.scripts


def _load_module():
    # A hyphen in the filename, like every script under scripts/, so it is
    # loaded from its path rather than imported by name.
    spec = importlib.util.spec_from_file_location("kcov_to_sonar", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kcov_to_sonar = _load_module()

REPORT = """<?xml version="1.0" ?>
<coverage line-rate="0.75" version="1.9">
  <sources><source>/srv/repo/</source></sources>
  <packages><package name="work"><classes>
    <class name="a_sh" filename="scripts/backup.sh" line-rate="0.75">
      <lines>
        <line number="3" hits="1"/>
        <line number="4" hits="0"/>
        <line number="7" hits="2"/>
      </lines>
    </class>
    <class name="t_sh" filename="tests/shell/run.sh" line-rate="1.0">
      <lines><line number="1" hits="1"/></lines>
    </class>
  </classes></package></packages>
</coverage>
"""


def write_report(tmp_path: Path, text: str = REPORT) -> Path:
    path = tmp_path / "cobertura.xml"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_cobertura_maps_each_line_to_covered(tmp_path):
    files = kcov_to_sonar.read_cobertura(str(write_report(tmp_path)), "/srv/repo")
    assert files["scripts/backup.sh"] == {3: True, 4: False, 7: True}
    assert files["tests/shell/run.sh"] == {1: True}


def test_read_cobertura_counts_a_line_once_it_ran_anywhere(tmp_path):
    """A script kcov reports twice is covered where either report ran it."""
    twice = REPORT.replace(
        '<class name="t_sh"',
        '<class name="b_sh" filename="scripts/backup.sh"><lines>'
        '<line number="4" hits="3"/><line number="3" hits="0"/></lines></class>'
        '<class name="t_sh"',
    )
    files = kcov_to_sonar.read_cobertura(
        str(write_report(tmp_path, twice)), "/srv/repo"
    )
    assert files["scripts/backup.sh"] == {3: True, 4: True, 7: True}


def test_read_cobertura_makes_absolute_names_relative_to_the_root(tmp_path):
    """kcov writes absolute names when it is given a file to include, with
    `/` as the source. A file outside the root keeps its absolute name."""
    absolute = REPORT.replace("<source>/srv/repo/</source>", "<source>/</source>")
    absolute = absolute.replace(
        'filename="scripts/backup.sh"', 'filename="/srv/repo/scripts/backup.sh"'
    )
    absolute = absolute.replace(
        'filename="tests/shell/run.sh"', 'filename="/usr/lib/helper.sh"'
    )
    files = kcov_to_sonar.read_cobertura(
        str(write_report(tmp_path, absolute)), "/srv/repo"
    )
    assert sorted(files) == ["/usr/lib/helper.sh", "scripts/backup.sh"]


def test_read_cobertura_without_sources_reads_names_from_the_filesystem_root(tmp_path):
    bare = REPORT.replace("<sources><source>/srv/repo/</source></sources>", "")
    bare = bare.replace(
        'filename="scripts/backup.sh"', 'filename="srv/repo/scripts/backup.sh"'
    )
    files = kcov_to_sonar.read_cobertura(str(write_report(tmp_path, bare)), "/srv/repo")
    assert "scripts/backup.sh" in files


def test_to_generic_writes_sonar_format(tmp_path):
    out = tmp_path / "shell.xml"
    kcov_to_sonar.to_generic({"b.sh": {2: True}, "a.sh": {9: False, 1: True}}).write(
        out
    )
    root = ET.parse(out).getroot()  # noqa: S314 (the file this test just wrote)
    assert root.tag == "coverage"
    assert root.get("version") == "1"
    assert [f.get("path") for f in root] == ["a.sh", "b.sh"]
    assert [(line.get("lineNumber"), line.get("covered")) for line in root[0]] == [
        ("1", "true"),
        ("9", "false"),
    ]


def test_shortfalls_names_missed_lines_and_untested_scripts():
    files = {"a.sh": {1: True, 2: False, 5: False}, "b.sh": {1: True}}
    assert kcov_to_sonar.shortfalls(files, ["a.sh", "b.sh", "c.sh"]) == [
        "a.sh: lines not covered: 2, 5",
        "c.sh: not in the report, so no test ran it",
    ]


def test_main_passes_at_100_percent(tmp_path, capsys):
    report = write_report(tmp_path, REPORT.replace('hits="0"', 'hits="1"'))
    out = tmp_path / "shell.xml"
    assert (
        kcov_to_sonar.main(["/srv/repo", str(report), str(out), "scripts/backup.sh"])
        == 0
    )
    assert "Shell coverage 100%: scripts/backup.sh" in capsys.readouterr().out
    written = ET.parse(out).getroot()  # noqa: S314 (the file main just wrote)
    assert written.find("file").get("path") == "scripts/backup.sh"


def test_main_fails_below_100_percent_but_still_writes_the_report(tmp_path, capsys):
    out = tmp_path / "shell.xml"
    assert (
        kcov_to_sonar.main(
            ["/srv/repo", str(write_report(tmp_path)), str(out), "scripts/backup.sh"]
        )
        == 1
    )
    assert "scripts/backup.sh: lines not covered: 4" in capsys.readouterr().err
    assert out.exists()


def test_main_refuses_too_few_arguments(capsys):
    assert kcov_to_sonar.main(["/srv/repo", "report.xml", "out.xml"]) == 2
    assert capsys.readouterr().err.startswith("Usage:")
