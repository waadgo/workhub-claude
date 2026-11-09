"""Tests of the research-log helper script (scripts/research_log.py).

Run:   python3 -m pytest <this skill>/tests -q

Every test uses a temporary settings file (environment variable WORKHUB_CLAUDE_CONFIG) and synthetic files in
pytest's temporary folder; no machine and no Notion page is contacted.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# The scripts are plain files in ../scripts (not a package): put that folder first on the import path.
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import research_log  # noqa: E402


def make_settings(folder, research_log_block=None, skill=None):
    """A small fictional settings file in `folder`; returns (path, dict). The machine name comes from the
    environment variable WORKHUB_TEST_MACHINE (system_name.override_env), which each test sets."""
    settings = {
        "schema_version": 1,
        "owner": {"name": "Alex", "time_zone": "Europe/Lisbon"},
        "system_name": {"override_env": "WORKHUB_TEST_MACHINE", "aliases": {}},
        "notion": {
            "projects": ["Project A", "Data (cleaning)", "Miscellaneous"],
            "research_log": research_log_block if research_log_block is not None else {
                "enabled": True, "parent_page_url": "",
                "data_source": "collection://01234567-89ab-cdef-0123-456789abcdef"},
        },
        "machines": {
            "workstations": [
                {"name": "ws01", "ssh_alias": "", "shares_home_with_this_machine": True},
                {"name": "ws02", "ssh_alias": "ws02", "shares_home_with_this_machine": True},
                {"name": "ws03", "ssh_alias": "ws03", "shares_home_with_this_machine": False},
            ],
            "clusters": [{"name": "mycluster", "ssh_alias": "mycluster", "shares_home_with_this_machine": False}],
        },
        "workflow": {"session_records_subdir": "docs/sessions"},
        "paths": {"state_dir": str(folder / "state"), "outputs_dir": str(folder / "outputs")},
        "skill_settings": {"research-log": skill or {}},
    }
    path = folder / "config.json"
    path.write_text(json.dumps(settings))
    return path, settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path, loaded = make_settings(tmp_path)
    monkeypatch.setenv("WORKHUB_CLAUDE_CONFIG", str(path))
    monkeypatch.setenv("WORKHUB_TEST_MACHINE", "ws01")
    return loaded


def run_cli(*arguments):
    return subprocess.run([sys.executable, str(SCRIPTS / "research_log.py"), *arguments], capture_output=True,
                          text=True, env=dict(os.environ))


def test_project_keys():
    assert research_log.project_key("Project A") == "project-a"
    assert research_log.project_key("Data (cleaning)") == "data-cleaning"
    assert research_log.project_key("Thesis chapter 1") == "thesis-chapter-1"
    assert research_log.project_key("Project A", {"Project A": "pa"}) == "pa"


def test_machine_labels(settings, tmp_path):
    assert research_log.machine_label(settings, "ws01") == "ws01"
    assert research_log.machine_label(settings, "mycluster") == "Mycluster"
    _, labelled = make_settings(tmp_path, skill={"machine_labels": {"ws": "Lab", "ws03": "Lab GPU"}})
    assert research_log.machine_label(labelled, "ws02") == "Lab"
    assert research_log.machine_label(labelled, "ws03") == "Lab GPU"
    assert research_log.all_labels(labelled) == ["Lab", "Lab GPU", "Mycluster"]


def test_settings_ready_and_defaults(settings):
    resolved = research_log.resolved_settings(settings)
    assert resolved["state"] == "ready"
    assert resolved["publish_mode"] == "auto"
    assert resolved["projects"][1] == {"name": "Data (cleaning)", "key": "data-cleaning"}
    assert resolved["this_machine"] == "ws01" and resolved["this_label"] == "ws01"
    # "auto": every listed machine that does not share this home folder, never this machine.
    assert resolved["publish_hosts"] == ["ws03", "mycluster"]
    assert resolved["pending_file"].endswith(os.path.join("state", "research-log", "pending.txt"))
    folder_mode = stat.S_IMODE(os.stat(os.path.dirname(resolved["pending_file"])).st_mode)
    assert folder_mode == 0o700


def test_settings_states(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKHUB_TEST_MACHINE", "ws01")
    path, disabled = make_settings(tmp_path, research_log_block={"enabled": False, "data_source": "x"})
    assert research_log.resolved_settings(disabled)["state"] == "disabled"
    path, empty = make_settings(tmp_path, research_log_block={"enabled": True, "data_source": ""})
    assert research_log.resolved_settings(empty)["state"] == "not built"
    _, odd = make_settings(tmp_path, skill={"publish_mode": "sometimes", "publish_hosts": ["ws02"]})
    resolved = research_log.resolved_settings(odd)
    assert resolved["publish_mode"] == "ask"                  # unknown value -> the conservative choice
    assert resolved["publish_hosts"] == ["ws02"]


def test_pending_queue_cli(settings, tmp_path):
    record = tmp_path / "project" / "docs" / "sessions" / "2026-01-15_1400_qc-run.md"
    record.parent.mkdir(parents=True)
    record.write_text("# QC run\n")

    dry = run_cli("pending", "add", str(record), "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "would queue" in dry.stdout
    assert run_cli("pending", "list").stdout.strip() == ""      # dry run wrote nothing

    assert run_cli("pending", "add", str(record)).returncode == 0
    assert "already queued" in run_cli("pending", "add", str(record)).stdout
    assert run_cli("pending", "list").stdout.strip() == str(record)
    pending = Path(research_log.resolved_settings(settings)["pending_file"])
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600

    assert run_cli("pending", "add", str(tmp_path / "missing.md")).returncode == 2
    assert "removed" in run_cli("pending", "remove", str(record)).stdout
    assert run_cli("pending", "list").stdout.strip() == ""


def test_hash_label_and_key_cli(settings, tmp_path):
    document = tmp_path / "upload.md"
    document.write_bytes(b"# A record\n\nSome text.\n")
    expected = hashlib.sha256(document.read_bytes()).hexdigest()[:12]
    assert run_cli("hash", str(document)).stdout.strip() == expected
    assert run_cli("label").stdout.strip() == "ws01"
    assert run_cli("label", "mycluster").stdout.strip() == "Mycluster"
    assert run_cli("project-key", "Project A").stdout.strip() == "project-a"
    shown = json.loads(run_cli("settings").stdout)
    assert shown["data_source"] == "collection://01234567-89ab-cdef-0123-456789abcdef"


def test_missing_settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKHUB_CLAUDE_CONFIG", str(tmp_path / "nowhere.json"))
    result = run_cli("settings")
    assert result.returncode == 2 and "set up my work hub" in result.stderr
