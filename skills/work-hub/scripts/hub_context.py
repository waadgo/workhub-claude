#!/usr/bin/env python3
"""
hub_context.py: print everything the work-hub skill needs from the settings file, with the skill's defaults.

PURPOSE
    The work-hub skill (SKILL.md next to this folder) runs the user's Notion Work Hub day to day: it files the
    Inbox, writes the Morning Brief, plans the week, and keeps one topic = one row = one conversation. Every value
    that differs between users (time zone, owner name, Notion addresses, Task ID prefix, projects, limits, brief
    options) lives in the one settings file written by the work-hub-setup skill. This script reads that file once
    and prints the values the skill uses, already checked and with this skill's defaults applied, so SKILL.md can
    say "run hub_context.py" instead of a dozen separate look-ups. It also prints today's date and the current
    time in the user's time zone, because new rows, "Done on" dates and topic-card times use that date.

    This script only reads. It never writes the settings file (only work-hub-setup does that) and never talks
    to Notion.

INPUTS
    The settings file found by workhub_config.py: ~/.config/workhub-claude/config.json, or the file named by the
    environment variable WORKHUB_CLAUDE_CONFIG (tests use it). Keys read (all optional except the two required
    Notion addresses):
        owner.name, owner.time_zone, system_name.*, notion.hub_page_url, notion.tasks_database_url,
        notion.tasks_data_source, notion.views.now, notion.views.focus, notion.task_id_prefix, notion.projects,
        workflow.conversation_names, workflow.orchestrator.{enabled,folder},
        workflow.morning_brief.{enabled,days,time,scheduler}, workflow.now_limits.{max_rows,max_in_progress},
        workflow.max_active_conversations, workflow.session_records_subdir,
        skill_settings.work-hub.{brief_destination,brief_page_url,brief_sources}   (this skill's own keys)

OUTPUTS
    Standard output: one "key: value" line per value (default), or one JSON object (--json). Lines that start
    with "missing:" or "warning:" tell the skill which features to skip. Nothing is written to disk.

USAGE
    python3 hub_context.py            # readable lines for the conversation to read
    python3 hub_context.py --json     # the same values as JSON (tests and other tools)

EXAMPLE OUTPUT (shortened)
    now: Mon 2026-11-09 08:05 WET
    today: 2026-11-09
    task_id_prefix: TK
    brief_destination: notion
    warning: notion.views.focus is empty: the Focus view is looked up by name in the fetched Tasks ...

EXIT STATUS
    0 = the settings file was read and both required Notion addresses are set;
    2 = no settings file, or it cannot be read (the user should say "set up my work hub");
    3 = the file was read, but notion.hub_page_url or notion.tasks_data_source is empty (same advice).

DEPENDENCIES
    Python >= 3.9 (the standard-library module zoneinfo; on 3.8 the time falls back to UTC with a warning).
    workhub_config.py in the same folder: an identical copy of the kit's settings reader (lib/workhub_config.py).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------------------------------------------------
# argparse: the --json option and --help text. datetime: the current date and time. json: the --json output.
# os: paths and "does this file exist?". sys: exit codes, error output and the import path.
import argparse
import datetime
import json
import os
import sys

# workhub_config.py lives in the same folder as this script. Python only finds a module that sits on its import
# path (sys.path), so the folder of this file is put first on that path before the import. This also works when
# Python is started with -I, which leaves the script's folder off the path.
# os.path.abspath(__file__) = the full path of this script; os.path.dirname(...) = its folder.
THIS_FOLDER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_FOLDER)

# The settings reader. Functions used below, all from workhub_config.py in this folder:
#   load_config()            -> dict of the whole settings file; raises ConfigError when it is missing/unreadable.
#   get_setting(cfg, "a.b")  -> one value addressed by a dotted key; None/""/[]/{} count as "not set".
#   skill_setting(cfg, skill, key, default) -> cfg["skill_settings"][skill][key], else default.
#   data_source_id(url)      -> "collection://1234-..." becomes "1234-..." (the form create-pages wants).
#   time_zone(cfg), owner_name(cfg), system_name(cfg), expand_path(text), ConfigError, SETUP_HINT.
import workhub_config  # noqa: E402 - the import path must be set first

# ---------------------------------------------------------------------------------------------------------------------
# Fixed values of this skill (the defaults promised in SKILL.md)
# ---------------------------------------------------------------------------------------------------------------------
# The name under which this skill keeps its own options in the settings file: skill_settings.work-hub.<key>.
SKILL_NAME = "work-hub"

# Where the Morning Brief goes: "notion" = a child page of the hub replaced every morning (the default),
# "chat" = only the reply in the conversation. Any other value falls back to the default with a warning.
BRIEF_DESTINATIONS = ("notion", "chat")
DEFAULT_BRIEF_DESTINATION = "notion"

# Extra sources the brief may read, and only when they are listed: today's calendar events and e-mails that need
# an action. The default is an empty list, which means the brief never reads a calendar or a mailbox.
ALLOWED_BRIEF_SOURCES = ("calendar", "email")

# The user's limits for the Now view and for open topic conversations, used when the settings leave them empty.
DEFAULT_MAX_ROWS = 7
DEFAULT_MAX_IN_PROGRESS = 3
DEFAULT_MAX_ACTIVE_CONVERSATIONS = 4

# The Task ID prefix Notion puts in front of each row number ("TK" gives "TK-42") when the setting is empty.
DEFAULT_TASK_ID_PREFIX = "TK"

# The folder (inside a project) where handover notes and session records go, when the setting is empty.
DEFAULT_SESSION_RECORDS_SUBDIR = "docs/sessions"

# The helper, installed by the kit's hooks, that records which row a conversation works on (SKILL.md section 4.1).
SET_TASK_SCRIPT = "~/.local/share/workhub-claude/hooks/set_conversation_task.py"

# The two Notion addresses without which the skill cannot work at all (exit status 3 when one is empty).
REQUIRED_ADDRESSES = ("notion.hub_page_url", "notion.tasks_data_source")

# Addresses the skill can do without. Each key maps to the warning shown when it is empty, which names what the
# skill does instead, so the conversation can say it in one line.
OPTIONAL_ADDRESSES = {
    "notion.tasks_database_url": "the view URLs are read from the <database> tag of the hub page instead.",
    "notion.views.now": "the Now view is looked up by name in the fetched Tasks database; without it, open rows "
                        "are read in rows mode, sorted by Priority, then Due.",
    "notion.views.focus": "the Focus view is looked up by name in the fetched Tasks database; without it, open "
                          "rows are read in rows mode, sorted by Priority, then Due.",
}


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------------
def local_now(zone_name, warnings):
    """
    Return the current date and time in the user's time zone, as a timezone-aware datetime.datetime.

    Parameters
        zone_name  IANA time zone name from owner.time_zone, for example "Europe/Lisbon".
        warnings   list of warning texts; one is appended when the zone cannot be used.

    The zone is resolved with the standard-library module zoneinfo (Python 3.9+). An unknown name, or a Python
    without zoneinfo, falls back to UTC, so dates are still produced; the warning tells the user to fix the setting.
    Example: local_now("Europe/Lisbon", []) -> datetime(2026, 11, 9, 8, 5, tzinfo=ZoneInfo("Europe/Lisbon")).
    """
    # datetime.timezone.utc is the fixed UTC zone; it is the fallback used in every failure branch below.
    fallback_zone = datetime.timezone.utc
    # `try ... except ImportError` catches a Python older than 3.9, which has no zoneinfo module.
    try:
        import zoneinfo
    except ImportError:
        warnings.append("this Python has no zoneinfo module (Python < 3.9): times are shown in UTC.")
        return datetime.datetime.now(fallback_zone)
    # zoneinfo.ZoneInfo(name) loads the rules of one time zone (offsets, daylight-saving dates).
    # An unknown name raises ZoneInfoNotFoundError, which is a subclass of KeyError; a malformed name (for example
    # one with ".." in it) raises ValueError. Both lead to the UTC fallback.
    try:
        user_zone = zoneinfo.ZoneInfo(str(zone_name))
    except (KeyError, ValueError):
        warnings.append("owner.time_zone '" + str(zone_name) + "' is not a known time zone: times are shown in UTC.")
        return datetime.datetime.now(fallback_zone)
    # datetime.datetime.now(zone) = the current moment expressed in that zone (aware datetime).
    # DEBUG CHECK: the returned value's .tzinfo should print as the user's zone name.
    return datetime.datetime.now(user_zone)


def positive_whole_number(config, dotted_key, default, warnings):
    """
    Return the setting `dotted_key` as a whole number of at least 1, or `default` when it is empty or invalid.

    Parameters
        config      the settings dict from workhub_config.load_config().
        dotted_key  for example "workflow.now_limits.max_rows".
        default     the number used when the setting is empty or not a whole number >= 1.
        warnings    list of warning texts; one is appended for an invalid (not merely empty) value.

    Example: a setting of 7 gives 7; "7" gives 7; 0, -2, 2.5 or "seven" give `default` plus a warning.
    """
    # get_setting returns None when the key is absent or empty, so None means "use the default silently".
    raw_value = workhub_config.get_setting(config, dotted_key, default=None)
    if raw_value is None:
        return default
    # `isinstance(raw_value, bool)` is checked first because in Python True and False are also integers (1 and 0),
    # and a Boolean is never a sensible limit.
    if isinstance(raw_value, bool):
        warnings.append(dotted_key + " must be a whole number, not " + str(raw_value) + ": using " + str(default) + ".")
        return default
    # int(...) converts 7, 7.0 and "7"; it raises ValueError for "seven" and TypeError for a list or a dict.
    try:
        number = int(raw_value)
    except (TypeError, ValueError):
        warnings.append(dotted_key + " must be a whole number, not '" + str(raw_value) + "': using " + str(default) + ".")
        return default
    # A value such as 2.5 converts to 2 silently, so the conversion is compared with the original value;
    # numbers below 1 make no sense as limits.
    if number < 1 or (isinstance(raw_value, float) and raw_value != number):
        warnings.append(dotted_key + " must be a whole number of at least 1, not " + str(raw_value)
                        + ": using " + str(default) + ".")
        return default
    return number


def true_or_false(config, dotted_key, default, warnings):
    """
    Return the setting `dotted_key` as True or False; `default` when it is empty or not a Boolean.

    Example: true -> True; false -> False; "yes" -> `default` plus a warning (JSON Booleans are expected).
    """
    # get_setting returns False unchanged (False is not one of the "empty" values it checks for).
    raw_value = workhub_config.get_setting(config, dotted_key, default=None)
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    warnings.append(dotted_key + " must be true or false, not '" + str(raw_value) + "': using "
                    + str(default).lower() + ".")
    return default


def brief_settings(config, warnings):
    """
    Return this skill's three Morning Brief options, checked, as a dict.

    Keys of the returned dict (all from skill_settings.work-hub in the settings file):
        brief_destination  "notion" (default) or "chat".
        brief_page_url     the Morning Brief page's URL, or "" (then the brief page is found by its title).
        brief_sources      list with "calendar" and/or "email"; default [] = never read a calendar or e-mail.
    Unknown values are dropped with a warning, so a typo never makes the brief read something unexpected.
    """
    # Destination: one of the two allowed words, compared in lower case so "Notion" also works.
    destination = workhub_config.skill_setting(config, SKILL_NAME, "brief_destination", DEFAULT_BRIEF_DESTINATION)
    destination_text = str(destination).strip().lower()
    if destination_text not in BRIEF_DESTINATIONS:
        warnings.append("skill_settings.work-hub.brief_destination '" + str(destination)
                        + "' is not 'notion' or 'chat': using '" + DEFAULT_BRIEF_DESTINATION + "'.")
        destination_text = DEFAULT_BRIEF_DESTINATION

    # Page URL: any text; an empty value stays "" and SKILL.md then finds the page by its title.
    page_url = str(workhub_config.skill_setting(config, SKILL_NAME, "brief_page_url", "")).strip()

    # Sources: a list is expected. A single word such as "calendar" is accepted as a one-item list.
    raw_sources = workhub_config.skill_setting(config, SKILL_NAME, "brief_sources", [])
    if isinstance(raw_sources, str):
        raw_sources = [raw_sources]
    if not isinstance(raw_sources, list):
        warnings.append("skill_settings.work-hub.brief_sources must be a list such as [\"calendar\"]: using [].")
        raw_sources = []
    # Keep only the allowed source names, in the order given, without repeats; report the others.
    kept_sources = []
    for source in raw_sources:
        source_text = str(source).strip().lower()
        if source_text in ALLOWED_BRIEF_SOURCES and source_text not in kept_sources:
            kept_sources.append(source_text)
        elif source_text not in ALLOWED_BRIEF_SOURCES:
            warnings.append("skill_settings.work-hub.brief_sources: '" + str(source)
                            + "' is not 'calendar' or 'email' and is ignored.")
    # DEBUG CHECK: kept_sources is a subset of ALLOWED_BRIEF_SOURCES, for example ["calendar"].
    return {"brief_destination": destination_text, "brief_page_url": page_url, "brief_sources": kept_sources}


# ---------------------------------------------------------------------------------------------------------------------
# Building the context
# ---------------------------------------------------------------------------------------------------------------------
def build_context(config):
    """
    Return a dict with every value SKILL.md uses (see the module docstring for the keys read).

    Parameters
        config  the settings dict from workhub_config.load_config().

    Returns
        An ordered dict-like Python dict (insertion order is kept since Python 3.7). Two keys are lists of texts:
        "missing" (required Notion addresses that are empty) and "warnings" (everything else worth one line).
    """
    warnings = []
    missing = []

    # --- Time: the user's local date and clock time -----------------------------------------------------------------
    # time_zone(config) returns owner.time_zone, or "UTC" when it is empty.
    zone_name = workhub_config.time_zone(config)
    moment = local_now(zone_name, warnings)
    # strftime codes: %a = short weekday (Mon), %Y-%m-%d = 2026-11-09, %H:%M = 24-hour clock, %Z = zone
    # abbreviation (WET, EST ...). %A = full weekday name (Monday). Python uses the C locale here, so the names
    # are English whatever the machine's language settings.
    now_text = moment.strftime("%a %Y-%m-%d %H:%M %Z")
    today_text = moment.strftime("%Y-%m-%d")
    weekday_text = moment.strftime("%A")

    # --- Notion addresses ------------------------------------------------------------------------------------------
    # Each address is read as text; an empty one is either "missing" (required) or a warning (optional).
    hub_page_url = str(workhub_config.get_setting(config, "notion.hub_page_url", default="")).strip()
    tasks_database_url = str(workhub_config.get_setting(config, "notion.tasks_database_url", default="")).strip()
    tasks_data_source = str(workhub_config.get_setting(config, "notion.tasks_data_source", default="")).strip()
    now_view = str(workhub_config.get_setting(config, "notion.views.now", default="")).strip()
    focus_view = str(workhub_config.get_setting(config, "notion.views.focus", default="")).strip()
    # The loop goes through the required keys in order; an empty value adds the key to `missing`.
    for dotted_key in REQUIRED_ADDRESSES:
        if not workhub_config.get_setting(config, dotted_key, default=""):
            missing.append(dotted_key)
    # `.items()` yields (key, warning text) pairs of the OPTIONAL_ADDRESSES dict.
    for dotted_key, fallback_text in OPTIONAL_ADDRESSES.items():
        if not workhub_config.get_setting(config, dotted_key, default=""):
            warnings.append(dotted_key + " is empty: " + fallback_text)

    # --- Task IDs and projects -------------------------------------------------------------------------------------
    # The prefix is shown as "<prefix>-<number>"; a trailing "-" typed into the setting is removed so the IDs never
    # read "TK--42".
    prefix = str(workhub_config.get_setting(config, "notion.task_id_prefix", default=DEFAULT_TASK_ID_PREFIX))
    prefix = prefix.strip().rstrip("-") or DEFAULT_TASK_ID_PREFIX
    # Projects: the options of the Project column. The last one is the catch-all ("Miscellaneous" by default).
    projects = workhub_config.get_setting(config, "notion.projects", default=[])
    if not isinstance(projects, list):
        warnings.append("notion.projects must be a list of project names: treating it as empty.")
        projects = []
    # List comprehension: [expression for item in list if condition] builds a new list. Here it keeps each project
    # name as text and skips blank names; the plain loop would be: project_names = []; for name in projects:
    # if str(name).strip(): project_names.append(str(name)).
    # DEBUG CHECK: project_names is a list of non-empty texts, for example ["Project A", "Miscellaneous"].
    project_names = [str(name) for name in projects if str(name).strip()]
    if project_names:
        catch_all_project = project_names[-1]
    else:
        catch_all_project = ""
        warnings.append("notion.projects is empty: new rows get no Project (ask the user, or run setup again).")

    # --- Limits ----------------------------------------------------------------------------------------------------
    max_rows = positive_whole_number(config, "workflow.now_limits.max_rows", DEFAULT_MAX_ROWS, warnings)
    max_in_progress = positive_whole_number(config, "workflow.now_limits.max_in_progress",
                                            DEFAULT_MAX_IN_PROGRESS, warnings)
    max_active = positive_whole_number(config, "workflow.max_active_conversations",
                                       DEFAULT_MAX_ACTIVE_CONVERSATIONS, warnings)

    # --- Conversation names and the orchestrator --------------------------------------------------------------------
    conversation_names = true_or_false(config, "workflow.conversation_names", True, warnings)
    # expand_path turns "~/..." into the absolute path of this user's home; os.path.isfile checks it exists.
    set_task_script = workhub_config.expand_path(SET_TASK_SCRIPT)
    set_task_script_exists = os.path.isfile(set_task_script)
    orchestrator_enabled = true_or_false(config, "workflow.orchestrator.enabled", False, warnings)
    orchestrator_folder = workhub_config.expand_path(
        workhub_config.get_setting(config, "workflow.orchestrator.folder", default=""))

    # --- Morning routine schedule (the time is used by the brief's header and by section 9 of SKILL.md) -----------
    brief_scheduled = true_or_false(config, "workflow.morning_brief.enabled", False, warnings)
    brief_days = str(workhub_config.get_setting(config, "workflow.morning_brief.days", default="Mon-Fri"))
    brief_time = str(workhub_config.get_setting(config, "workflow.morning_brief.time", default=""))
    brief_scheduler = str(workhub_config.get_setting(config, "workflow.morning_brief.scheduler", default=""))
    if brief_scheduled:
        schedule_text = (brief_days + " " + brief_time).strip()
        if brief_scheduler:
            schedule_text = schedule_text + " (" + brief_scheduler + ")"
    else:
        schedule_text = "not scheduled (runs when the user asks)"

    # --- This skill's own options -----------------------------------------------------------------------------------
    brief = brief_settings(config, warnings)

    # --- Folder for handover notes -----------------------------------------------------------------------------------
    records_subdir = str(workhub_config.get_setting(config, "workflow.session_records_subdir",
                                                    default=DEFAULT_SESSION_RECORDS_SUBDIR))

    # --- Assemble the result in the order a reader expects ----------------------------------------------------------
    context = {
        "now": now_text,
        "today": today_text,
        "weekday": weekday_text,
        "time_zone": zone_name,
        "owner_name": workhub_config.owner_name(config),
        "system_name": workhub_config.system_name(config),
        "hub_page_url": hub_page_url,
        "tasks_database_url": tasks_database_url,
        "tasks_data_source": tasks_data_source,
        "tasks_data_source_id": workhub_config.data_source_id(tasks_data_source),
        "now_view": now_view,
        "focus_view": focus_view,
        "task_id_prefix": prefix,
        "task_id_example": prefix + "-42",
        "projects": project_names,
        "catch_all_project": catch_all_project,
        "now_limits_max_rows": max_rows,
        "now_limits_max_in_progress": max_in_progress,
        "max_active_conversations": max_active,
        "conversation_names": conversation_names,
        "set_task_script": set_task_script,
        "set_task_script_exists": set_task_script_exists,
        "orchestrator_enabled": orchestrator_enabled,
        "orchestrator_folder": orchestrator_folder,
        "morning_schedule": schedule_text,
        "brief_destination": brief["brief_destination"],
        "brief_page_url": brief["brief_page_url"],
        "brief_sources": brief["brief_sources"],
        "session_records_subdir": records_subdir,
        "missing": missing,
        "warnings": warnings,
    }
    # DEBUG CHECK: context["task_id_example"] reads like "TK-42"; context["missing"] is [] on a finished setup.
    return context


# ---------------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------------
def format_value(value):
    """Return one value as text for the readable output: lists joined by "; ", Booleans as yes/no, "" as (empty)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        if not value:
            return "(none)"
        # "; ".join(...) glues texts together with "; " between them. `str(item) for item in value` is a generator
        # expression: it turns each list item into text, one at a time, for join to consume.
        # Example: ["calendar", "email"] -> "calendar; email".
        return "; ".join(str(item) for item in value)
    if value == "":
        return "(empty)"
    return str(value)


def print_readable(context):
    """Print one "key: value" line per value, then one line per missing address and per warning."""
    for key, value in context.items():
        # The two lists of messages are printed afterwards, one line each, so they stand out.
        if key in ("missing", "warnings"):
            continue
        print(key + ": " + format_value(value))
    for dotted_key in context["missing"]:
        print("missing: " + dotted_key + " is empty. The user should say \"set up my work hub\".")
    for warning_text in context["warnings"]:
        print("warning: " + warning_text)


def main():
    """Read the settings, print the context, return the exit status (0, 2 or 3; see the module docstring)."""
    parser = argparse.ArgumentParser(
        description="Print the work-hub skill's settings (with its defaults) and the user's local date and time."
    )
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of key: value lines")
    arguments = parser.parse_args()

    # load_config raises ConfigError when the settings file is missing or unreadable; its message already says
    # what to do (say "set up my work hub").
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2

    context = build_context(config)
    if arguments.json:
        # ensure_ascii=False keeps characters such as "≤" or accented project names readable.
        print(json.dumps(context, indent=2, ensure_ascii=False))
    else:
        print_readable(context)

    # Exit status 3 tells the caller that the hub cannot be used until setup fills the required addresses.
    if context["missing"]:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
