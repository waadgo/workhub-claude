"""
test_hub_context.py: smoke tests for scripts/hub_context.py of the work-hub skill.

PURPOSE
    Run hub_context.py as the skill runs it (a separate Python process) against small, synthetic settings files,
    and check its exit status and its values: defaults applied, required Notion addresses reported, invalid
    options replaced by the defaults with a warning. No Notion access, no real settings file: every test writes
    its own settings into pytest's temporary folder and points the environment variable WORKHUB_CLAUDE_CONFIG at it.

RUN
    python3 -m pytest kit/skills/work-hub/tests -q

DEPENDENCIES
    pytest; Python >= 3.9.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# json: write the settings files and read the --json output. os: paths and environment. subprocess: run the
# script as a separate process. sys: the path of the Python running the tests (used to run the script).
import json
import os
import subprocess
import sys

# The script under test: ../scripts/hub_context.py relative to this test file.
SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "hub_context.py")

# Placeholder Notion addresses (all-zero ids, never a real page).
ZERO_ID = "00000000-0000-0000-0000-000000000000"
HUB_URL = "https://www.notion.so/00000000000000000000000000000000"
DATA_SOURCE = "collection://" + ZERO_ID


def complete_settings():
    """Return a synthetic settings dict with every key the skill reads filled in."""
    return {
        "schema_version": 1,
        "owner": {"name": "Alex", "time_zone": "Europe/Lisbon"},
        "notion": {
            "hub_page_url": HUB_URL,
            "tasks_database_url": HUB_URL,
            "tasks_data_source": DATA_SOURCE,
            "views": {"now": "view://" + ZERO_ID, "focus": "view://" + ZERO_ID},
            "task_id_prefix": "TK",
            "projects": ["Project A", "Project B", "Miscellaneous"],
        },
        "workflow": {
            "conversation_names": True,
            "orchestrator": {"enabled": False, "folder": "~/workhub/orchestrator"},
            "morning_brief": {"enabled": True, "days": "Mon-Fri", "time": "07:40", "scheduler": "claude-app"},
            "now_limits": {"max_rows": 7, "max_in_progress": 3},
            "max_active_conversations": 4,
            "session_records_subdir": "docs/sessions",
        },
        "skill_settings": {},
    }


def run_script(tmp_path, settings, *extra_arguments):
    """
    Write `settings` (a dict, or None for "no file") and run hub_context.py on it.

    Returns the finished process (exit status in .returncode, output in .stdout and .stderr).
    """
    settings_path = tmp_path / "config.json"
    if settings is not None:
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
    environment = dict(os.environ)
    environment["WORKHUB_CLAUDE_CONFIG"] = str(settings_path)
    # The state folder is not used by this script, but pointing HOME at the temporary folder guarantees that no
    # test reads or creates anything in the real home folder.
    environment["HOME"] = str(tmp_path)
    command = [sys.executable, SCRIPT] + list(extra_arguments)
    return subprocess.run(command, capture_output=True, text=True, env=environment, timeout=60)


def run_json(tmp_path, settings):
    """Run the script with --json and return (exit status, parsed JSON dict)."""
    finished = run_script(tmp_path, settings, "--json")
    return finished.returncode, json.loads(finished.stdout)


def test_missing_settings_file_exits_2_with_setup_hint(tmp_path):
    """No settings file: exit status 2 and the advice to say "set up my work hub"."""
    finished = run_script(tmp_path, None)
    assert finished.returncode == 2
    assert "set up my work hub" in finished.stderr


def test_complete_settings_give_values_and_defaults(tmp_path):
    """A finished setup: exit 0, no warnings, the prefix, limits and brief defaults as documented."""
    status, context = run_json(tmp_path, complete_settings())
    assert status == 0
    assert context["missing"] == []
    assert context["warnings"] == []
    assert context["task_id_example"] == "TK-42"
    assert context["tasks_data_source_id"] == ZERO_ID
    assert context["catch_all_project"] == "Miscellaneous"
    assert context["now_limits_max_rows"] == 7
    assert context["now_limits_max_in_progress"] == 3
    # This skill's own options: not in the file, so the defaults apply.
    assert context["brief_destination"] == "notion"
    assert context["brief_page_url"] == ""
    assert context["brief_sources"] == []
    # The date is the user's local date: ten characters, YYYY-MM-DD.
    assert len(context["today"]) == 10 and context["today"][4] == "-"
    assert context["morning_schedule"] == "Mon-Fri 07:40 (claude-app)"


def test_empty_required_address_exits_3(tmp_path):
    """An empty Tasks data source address is a required value: exit status 3 and a "missing" entry."""
    settings = complete_settings()
    settings["notion"]["tasks_data_source"] = ""
    status, context = run_json(tmp_path, settings)
    assert status == 3
    assert context["missing"] == ["notion.tasks_data_source"]


def test_empty_optional_view_gives_warning_not_failure(tmp_path):
    """An empty view address only gives a warning; the skill still runs."""
    settings = complete_settings()
    settings["notion"]["views"]["focus"] = ""
    status, context = run_json(tmp_path, settings)
    assert status == 0
    assert any(text.startswith("notion.views.focus is empty") for text in context["warnings"])


def test_brief_options_are_checked(tmp_path):
    """Unknown brief destination and sources fall back to the defaults, with warnings; repeats are dropped."""
    settings = complete_settings()
    settings["skill_settings"] = {"work-hub": {
        "brief_destination": "Slack",
        "brief_page_url": "https://www.notion.so/0123456789abcdef0123456789abcdef",
        "brief_sources": ["Calendar", "fax", "calendar"],
    }}
    status, context = run_json(tmp_path, settings)
    assert status == 0
    assert context["brief_destination"] == "notion"
    assert context["brief_sources"] == ["calendar"]
    assert context["brief_page_url"].endswith("0123456789abcdef0123456789abcdef")
    joined = " ".join(context["warnings"])
    assert "brief_destination" in joined and "'fax'" in joined


def test_chat_destination_is_kept(tmp_path):
    """"chat" is a valid destination, and a single source word becomes a one-item list."""
    settings = complete_settings()
    settings["skill_settings"] = {"work-hub": {"brief_destination": "chat", "brief_sources": "email"}}
    status, context = run_json(tmp_path, settings)
    assert status == 0
    assert context["brief_destination"] == "chat"
    assert context["brief_sources"] == ["email"]


def test_bad_time_zone_and_limits_fall_back(tmp_path):
    """Unknown time zone -> UTC; invalid limits -> defaults; a trailing "-" in the prefix is removed."""
    settings = complete_settings()
    settings["owner"]["time_zone"] = "Mars/Olympus_Mons"
    settings["workflow"]["now_limits"] = {"max_rows": 0, "max_in_progress": "three"}
    settings["notion"]["task_id_prefix"] = "JOB-"
    status, context = run_json(tmp_path, settings)
    assert status == 0
    assert context["now"].endswith("UTC")
    assert context["now_limits_max_rows"] == 7
    assert context["now_limits_max_in_progress"] == 3
    assert context["task_id_example"] == "JOB-42"
    joined = " ".join(context["warnings"])
    assert "time_zone" in joined and "max_rows" in joined and "max_in_progress" in joined


def test_readable_output_lists_missing_and_warnings(tmp_path):
    """The default (readable) output prints key: value lines, then the missing and warning lines."""
    settings = complete_settings()
    settings["notion"]["hub_page_url"] = ""
    settings["notion"]["views"]["now"] = ""
    finished = run_script(tmp_path, settings)
    assert finished.returncode == 3
    lines = finished.stdout.splitlines()
    assert "task_id_prefix: TK" in lines
    assert any(line.startswith("missing: notion.hub_page_url") for line in lines)
    assert any(line.startswith("warning: notion.views.now is empty") for line in lines)
