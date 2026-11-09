"""Tests for scripts/notion_report.py (the arguments of the Notion calls, the on/off check, the schema).

PURPOSE
    Check on a synthetic conversations.json and a temporary settings file that:
      - --check says "off" (exit 3) when the reports are disabled or a data source is missing, "on" otherwise;
      - the report page, the rows and the view carry the right data sources, site options and links;
      - every property the script writes exists in the schema that --schema prints (one source of truth);
      - an unreached site gets the hint that opens its shared connection.
    Nothing is sent to Notion. The data source and page ids are placeholders.

RUN
    python3 -m pytest -q <skill folder>/tests/test_notion_report.py

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# importlib.util: load the script by path. json: inputs and outputs. os: paths and environment.
# subprocess/sys: run the script as a command, the way SKILL.md does.
import importlib.util
import json
import os
import subprocess
import sys

SCRIPTS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
REPORT_SCRIPT = os.path.join(SCRIPTS_FOLDER, "notion_report.py")

# `spec_from_file_location` + `exec_module` load the script as a module without running main().
SPEC = importlib.util.spec_from_file_location("notion_report", REPORT_SCRIPT)
notion_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notion_report)

# Placeholder Notion addresses (not real ids).
REPORTS_DATA_SOURCE = "collection://00000000-0000-0000-0000-000000000000"
CONVERSATIONS_DATA_SOURCE = "collection://01234567-89ab-cdef-0123-456789abcdef"
REPORT_PAGE_URL = "https://www.notion.so/Report-0123456789abcdef0123456789abcdef"
SESSION_ID = "testconv-0000-0000-0000-000000000001"
GENERATED_AT = 1767888000.0


def settings_dict(tmp_path, enabled=True, conversations_data_source=CONVERSATIONS_DATA_SOURCE):
    """Settings with one home workstation and one cluster; Notion reports on or off."""
    return {
        "owner": {"name": "Alex", "time_zone": "Europe/Lisbon"},
        "notion": {"conversation_reports": {"enabled": enabled, "parent_page_url": "",
                                            "data_source": REPORTS_DATA_SOURCE}},
        "machines": {
            "workstations": [{"name": "ws01", "ssh_alias": "", "shares_home_with_this_machine": True}],
            "clusters": [{"name": "mycluster", "ssh_alias": "mycluster", "shares_home_with_this_machine": False}],
        },
        "cluster_login": {"enabled": True},
        "paths": {"state_dir": os.path.join(str(tmp_path), "state")},
        "skill_settings": {"active-conversations": {"conversations_data_source": conversations_data_source}},
    }


def conversations_json():
    """A conversations.json with one open conversation at home and one cluster not reached."""
    conversation = {
        "session_id": SESSION_ID, "title": "Survey cleaning", "title_source": "custom",
        "cwd": "/home/alex/projects/demo", "system": "ws01", "status": "idle",
        "processes": [{"host": "ws01", "pid": 1234, "app": "tmux", "tmux": "work:0.0", "status": "idle",
                       "shells": []}],
        "background": [], "started": GENERATED_AT - 3600, "last_message": GENERATED_AT - 60, "prompts": 3,
        "transcript": "", "transcript_mb": 0.1,
        "digest": {"first_prompt": None, "recent_prompts": [], "last_reply": None, "recent_tools": [],
                   "pending_tool": None, "last_notice": None, "compactions": 0},
    }
    return {
        "schema": 2, "generated_at": GENERATED_AT, "timezone": "Europe/Lisbon",
        "query": {"mode": "open", "last": None, "match": None, "since_hours": None},
        "requested_from": {"system": "ws01", "host": "ws01", "site": "home", "site_label": "Workstations",
                           "skill_dir": "/home/alex/.claude/skills/active-conversations"},
        "site_labels": ["Workstations", "mycluster"], "duration_s": 1.0,
        "sites": [
            {"site": "home", "label": "Workstations", "reached": True, "checked_at": GENERATED_AT,
             "nodes": [{"name": "ws01", "ok": True, "error": "", "auto": False, "skipped": False, "open": 1}],
             "conversations": [conversation], "empty": [], "unverified": [], "closed_entries": 0,
             "registry_entries": 1, "matched": None, "via": "this machine"},
            {"site": "mycluster", "label": "mycluster", "reached": False, "ssh": "mycluster",
             "reason": "no open shared SSH connection from ws01", "reason_code": "no_master", "shareable": True,
             "checked_at": GENERATED_AT, "checked_from": "ws01"},
        ],
    }


def run_report(tmp_path, settings, arguments):
    """Run notion_report.py with the settings in a temporary file (CC_CLUSTER=ws01)."""
    settings_path = os.path.join(str(tmp_path), "config.json")
    with open(settings_path, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file)
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=settings_path, CC_CLUSTER="ws01")
    return subprocess.run([sys.executable, REPORT_SCRIPT] + arguments, env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, timeout=60)


def write_inputs(tmp_path):
    """Write conversations.json and descriptions.json; return their paths."""
    collected_path = os.path.join(str(tmp_path), "conversations.json")
    with open(collected_path, "w", encoding="utf-8") as collected_file:
        json.dump(conversations_json(), collected_file)
    descriptions_path = os.path.join(str(tmp_path), "descriptions.json")
    with open(descriptions_path, "w", encoding="utf-8") as descriptions_file:
        json.dump({SESSION_ID[:8]: "Cleaning the survey table for Project A."}, descriptions_file)
    return collected_path, descriptions_path


def load(path):
    """Read one JSON output file."""
    with open(path, encoding="utf-8") as json_file:
        return json.load(json_file)


def schema_property_names(properties):
    """The property names of one database in the schema constants."""
    return set(name for name, _ in properties)


def written_property_names(properties):
    """Property names as written to notion-create-pages ("date:Started:start" -> "Started")."""
    names = set()
    for key in properties:
        names.add(key.split(":")[1] if key.startswith("date:") else key)
    return names


def test_check_on_and_off(tmp_path):
    """--check: on with both data sources; off (exit 3) when disabled or the rows' data source is missing."""
    on = run_report(tmp_path, settings_dict(tmp_path), ["--check"])
    assert on.returncode == 0 and on.stdout.startswith("on:")
    off = run_report(tmp_path, settings_dict(tmp_path, enabled=False), ["--check"])
    assert off.returncode == 3 and "enabled is false" in off.stdout
    missing = run_report(tmp_path, settings_dict(tmp_path, conversations_data_source=""), ["--check"])
    assert missing.returncode == 3 and "conversations_data_source is empty" in missing.stdout
    # The normal mode refuses too, so a chat-only setup never builds Notion calls.
    collected_path, descriptions_path = write_inputs(tmp_path)
    refused = run_report(tmp_path, settings_dict(tmp_path, enabled=False), [collected_path, "-o", str(tmp_path)])
    assert refused.returncode == 3 and "chat only" in refused.stderr


def test_schema_lists_the_sites(tmp_path):
    """--schema: the site options come from the settings, in order, with colors; the relation has a placeholder."""
    completed = run_report(tmp_path, settings_dict(tmp_path), ["--schema"])
    assert completed.returncode == 0, completed.stderr
    schema = json.loads(completed.stdout)
    assert schema["site_options"] == ["Workstations", "mycluster"]
    assert "'Workstations':blue, 'mycluster':orange" in schema["reports_create"]
    assert '"Sites checked" MULTI_SELECT(' in schema["reports_create"]
    assert "RELATION('<reports data source id>', DUAL 'Conversations')" in schema["conversations_create"]
    assert schema["conversations_alter"] == ["ALTER COLUMN \"Site\" SET SELECT('Workstations':blue, "
                                             "'mycluster':orange)"]
    # A quote inside an option name is doubled, as SQL strings need.
    assert notion_report.quote_option("Alex's lab") == "'Alex''s lab'"


def test_report_rows_and_view(tmp_path):
    """Both runs: report.json, then rows.json and view.json linked to the report page."""
    collected_path, descriptions_path = write_inputs(tmp_path)
    settings = settings_dict(tmp_path)
    first = run_report(tmp_path, settings, [collected_path, "--descriptions", descriptions_path, "-o", str(tmp_path)])
    assert first.returncode == 0, first.stderr
    report = load(os.path.join(str(tmp_path), "report.json"))
    assert report["parent"]["data_source_id"] == "00000000-0000-0000-0000-000000000000"
    page = report["pages"][0]
    properties = page["properties"]
    assert properties["Kind"] == "Open now"
    assert properties["Sites checked"] == ["Workstations"]
    assert properties["Not checked"] == ["mycluster"]
    assert properties["Found"] == 1 and properties["Waiting for you"] == 1
    assert properties["Report"].endswith("· Open now · ws01")
    # Lisbon is UTC+0 in January: the date carries that offset.
    assert properties["date:Date:start"] == "2026-01-08T16:00:00+00:00"
    assert "## Sites" in page["content"] and "## Conversations" in page["content"]
    assert "cluster-login mycluster" in page["content"]
    assert written_property_names(properties) <= schema_property_names(notion_report.REPORT_PROPERTIES)

    second = run_report(tmp_path, settings, [collected_path, "--descriptions", descriptions_path, "-o", str(tmp_path),
                                             "--report-url", REPORT_PAGE_URL])
    assert second.returncode == 0, second.stderr
    rows = load(os.path.join(str(tmp_path), "rows.json"))
    assert rows["parent"]["data_source_id"] == "01234567-89ab-cdef-0123-456789abcdef"
    row = rows["pages"][0]["properties"]
    assert row["Site"] == "Workstations" and row["State"] == "💬 Waiting for you"
    assert row["Working directory"] == "ws01:/home/alex/projects/demo"
    assert row["Opened in"] == "tmux session work on ws01"
    assert row["What's going on"] == "Cleaning the survey table for Project A."
    assert row["Report"] == ["01234567-89ab-cdef-0123-456789abcdef"]
    assert row["Resume"] == "cd /home/alex/projects/demo && claude --resume %s" % SESSION_ID
    assert written_property_names(row) <= schema_property_names(notion_report.CONVERSATION_PROPERTIES)
    view = load(os.path.join(str(tmp_path), "view.json"))
    assert view["parent_page_id"] == "01234567-89ab-cdef-0123-456789abcdef"
    assert view["configure"].startswith('FILTER "Report" = "01234567-89ab-cdef-0123-456789abcdef"')
    assert set(notion_report.ROW_COLUMNS) <= schema_property_names(notion_report.CONVERSATION_PROPERTIES)


def test_site_not_an_option_is_left_out(tmp_path):
    """A site the settings no longer list is not written as a select value (Notion would refuse it)."""
    collected_path, descriptions_path = write_inputs(tmp_path)
    settings = settings_dict(tmp_path)
    settings["machines"]["clusters"] = []
    completed = run_report(tmp_path, settings, [collected_path, "-o", str(tmp_path)])
    assert completed.returncode == 0, completed.stderr
    properties = load(os.path.join(str(tmp_path), "report.json"))["pages"][0]["properties"]
    assert properties["Not checked"] == []
    assert "Not a site option in Notion yet (left out): mycluster" in completed.stdout
