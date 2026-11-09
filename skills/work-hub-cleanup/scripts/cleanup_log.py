#!/usr/bin/env python3
"""
cleanup_log.py - remember when the Work Hub was last cleaned up.

PURPOSE
    A Work Hub cleanup (the skill `work-hub-cleanup`) reads the conversations and jobs of one
    time window. That window should start where the previous cleanup stopped reading, so that
    nothing is missed and nothing is read twice. This script keeps that memory in a small log
    file, one line per cleanup, and answers the question "from when should the next cleanup
    read?". It also finds the next free question code (CL1, CL2, ...), because every cleanup
    question gets a code that is never reused.

COMMANDS
    last       Print the most recent cleanup, the --since value for the next one (where its
               reading of conversations stopped), and the moment its Notion writes finished (rows
               edited after that were changed by someone else), also in UTC for the Notion
               query. Read-only.
    add        Append one line describing a cleanup that has just been written to Notion.
    next-code  Print the next free question code: one more than the highest "CL<n>" found in
               the log and, when the orchestrator is enabled in the settings, in its
               DECISIONS.md and QUESTIONS.md. Read-only.
    settings   Print every setting the cleanup uses, already resolved (Notion addresses, Task
               ID prefix, projects and project rules, orchestrator folder, Now limits, time
               zone, machine name, log path), and say which optional features are off.
               Exit status 2 when notion.tasks_data_source is empty. Read-only.

INPUT AND OUTPUT
    The settings file of workhub-claude (read with workhub_config.py, which sits next to this
    script; default ~/.config/workhub-claude/config.json, or $WORKHUB_CLAUDE_CONFIG).
    The log file. Default:
        <state dir>/work-hub-cleanup/cleanup_log.jsonl
    where <state dir> is the setting paths.state_dir (default ~/.local/state/workhub-claude).
    - It lives outside the skill folder, so reinstalling or updating the skill never erases it.
    - Machines that share one home folder share one log; a machine with its own home folder
      (for example a cluster) has its own.
    - Format: JSON Lines, one JSON object per line. Example line:
        {"window_start": "2026-03-02T00:00:00+00:00", "window_end": "2026-03-02T14:41:00+00:00",
         "finished": "2026-03-02T15:06:00+00:00", "system": "ws01", "client": "claude-code",
         "summary": "8 conversations; 2 Done, 1 dropped, 5 new rows; questions CL1-CL4"}
      window_start, window_end: the period whose conversations were read;
      finished: when the Notion changes were written.
    - The next cleanup starts at window_end, not at finished: a conversation that kept going
      while the previous cleanup was asking its questions is then read again, not skipped.

SETTINGS USED
    owner.time_zone              times typed without a UTC offset, and printed times
    paths.state_dir              where the log lives (through workhub_config.state_dir)
    system_name.*                the machine name written into each line
    workflow.orchestrator.enabled, workflow.orchestrator.folder   (next-code, settings)
    settings only: notion.tasks_data_source, notion.views.now, notion.views.focus,
    notion.hub_page_url, notion.task_id_prefix, notion.projects,
    notion.research_log.enabled, notion.research_log.data_source,
    workflow.now_limits.max_rows, workflow.now_limits.max_in_progress, cluster_login.enabled,
    skill_settings.work-hub-cleanup.project_rules (list of sentences, default [])

USAGE
    python3 cleanup_log.py last
    python3 cleanup_log.py add --window-start 2026-03-02T14:41+00:00 \
        --window-end 2026-03-03T11:58+00:00 --client claude-code \
        --summary "15 conversations; 3 Done, 4 new rows, 2 due dates moved; questions CL5-CL9"
    python3 cleanup_log.py add ... --dry-run        # show the line, write nothing
    python3 cleanup_log.py next-code
    python3 cleanup_log.py settings

DEPENDENCIES
    Python >= 3.9 (for `zoneinfo`), standard library only, plus workhub_config.py (an identical
    copy of the kit's settings reader) in the same folder.

ASSUMPTIONS
    - Times typed without a UTC offset are read in the user's time zone (owner.time_zone),
      unless --timezone says otherwise.
    - A malformed line in the log is skipped with a warning rather than stopping the script.

EXIT STATUS
    0 = success; 1 = the log could not be read or written, or the times are inconsistent;
    2 = invalid command-line arguments (reported by argparse), or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (Python's standard library, then the kit's settings reader)
# ----------------------------------------------------------------------------------------------

# `argparse` turns the words typed after the script name into a Namespace object whose
# attributes hold the values. It also prints --help and exits with status 2 on invalid input.
import argparse

# `datetime` provides `datetime.datetime` (one moment: date + clock time, optionally with a
# UTC offset) and `datetime.timedelta` (a duration, used for "72 hours ago").
import datetime

# `json` converts JSON text to Python values (dict, list, str, int, float, bool, None) and back.
import json

# `os` gives path helpers (join, dirname, exists).
import os

# `re` is Python's regular-expression module, used to find the "CL<number>" question codes.
import re

# `sys` gives `sys.exit(status)` and `sys.stderr` for error messages.
import sys

# `ZoneInfo("Europe/Lisbon")` is a time-zone object that knows the daylight-saving rules of
# that zone, read from the operating system's time-zone database (Python >= 3.9).
from zoneinfo import ZoneInfo

# `workhub_config` is the kit's settings reader (workhub_config.py in this same folder; Python
# finds it because the folder of the script being run is searched first). Functions used here:
#   load_config()            -> the settings as a dict (raises ConfigError when the file is missing);
#   get_setting(c, "a.b")    -> one value, addressed by a dotted key, or a default;
#   time_zone(c)             -> the user's IANA time-zone name, e.g. "Europe/Lisbon";
#   system_name(c)           -> this machine's short name, e.g. "ws01";
#   state_dir(c, "sub")      -> the state folder <paths.state_dir>/sub (created, mode 700);
#   expand_path(text)        -> the text with ~ and $VARIABLES expanded.
import workhub_config


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# This skill's name: its state sub-folder is called the same.
SKILL_NAME = "work-hub-cleanup"

# How far back the next cleanup reads when the log is empty (first cleanup with this log).
# 72 hours covers a long weekend; the skill tells the agent to ask the user if more is needed.
DEFAULT_LOOKBACK_HOURS_WITHOUT_LOG = 72

# The programs that can run a cleanup. `--client` must be one of them.
CLIENT_CHOICES = ["claude-code", "codex", "other"]

# A question code such as "CL12". Pattern parts: `\b` = a word boundary (so "XCL12" does not
# count); `CL` = these two letters literally; `([0-9]+)` = group 1, one or more digits (the
# number); `\b` = another word boundary (so "CL12b" does not count).
QUESTION_CODE_PATTERN = re.compile(r"\bCL([0-9]+)\b")


# ----------------------------------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------------------------------

def default_log_path(config):
    """Return the default path of the cleanup log, from the settings.

    Parameters:
        config (dict): the settings, from workhub_config.load_config().

    Returns:
        str: absolute path, e.g. "/home/alex/.local/state/workhub-claude/work-hub-cleanup/
            cleanup_log.jsonl". The folder is created (mode 700) by state_dir(); the file may
            not exist yet.
    """
    # `state_dir(config, "work-hub-cleanup")` is <paths.state_dir>/work-hub-cleanup. It is
    # created if missing, with mode 700 (only the owner can open it), because the log names
    # the user's conversations and machines.
    folder = workhub_config.state_dir(config, SKILL_NAME)

    # `os.path.join` glues path parts with "/" between them.
    log_path = os.path.join(folder, "cleanup_log.jsonl")

    # DEBUG CHECK: `log_path` should end with "work-hub-cleanup/cleanup_log.jsonl".
    return log_path


def parse_moment(text, timezone):
    """Convert a moment typed on the command line into a time-zone-aware datetime.

    Accepted forms (examples):
        "2026-03-02T14:41+00:00"   ISO 8601 with a UTC offset
        "2026-03-02T18:41:00Z"     ISO 8601 in UTC; "Z" means "+00:00"
        "2026-03-02 14:41"         no offset: read in `timezone`
        "2026-03-02"               midnight at the start of that day, in `timezone`

    Parameters:
        text (str): the moment as typed.
        timezone (ZoneInfo): zone used when `text` has no offset.

    Returns:
        datetime.datetime: an aware datetime (it carries its UTC offset).

    Raises:
        ValueError: when `text` is not one of the accepted forms.
    """
    # `.strip()` removes spaces and line breaks around the text.
    cleaned_text = text.strip()

    # Python 3.9 and 3.10 do not accept a final "Z" in `fromisoformat`, so it is replaced by
    # the equivalent offset "+00:00". Example: "2026-03-02T18:41:00Z" -> "2026-03-02T18:41:00+00:00".
    if cleaned_text.endswith("Z"):
        cleaned_text = cleaned_text[:-1] + "+00:00"

    # `datetime.datetime.fromisoformat` parses ISO 8601 text. A date alone gives midnight.
    # It raises ValueError for text it cannot read; that error is passed on to the caller.
    moment = datetime.datetime.fromisoformat(cleaned_text)

    # `tzinfo is None` means the text had no offset ("naive" datetime). `.replace(tzinfo=...)`
    # attaches the user's zone without changing the clock time: 14:41 stays 14:41 local time.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone)

    # DEBUG CHECK: `moment.utcoffset()` must not be None from here on.
    return moment


def read_log_entries(log_path):
    """Read every valid line of the log.

    Parameters:
        log_path (str): path of the JSON Lines log.

    Returns:
        list of dict: one dictionary per valid line, in file order. Empty when the file does
            not exist yet.

    Raises:
        OSError: when the file exists but cannot be read (for example, no permission).
    """
    # A missing file simply means that no cleanup was recorded yet.
    if not os.path.exists(log_path):
        return []

    # This list collects the dictionaries read from the file.
    entries = []

    # `open(..., "r", encoding="utf-8")` opens the file for reading text. The `with` block
    # closes it automatically at the end, even if an error happens inside.
    with open(log_path, "r", encoding="utf-8") as log_file:

        # `enumerate(log_file, start=1)` yields (line number, line text) pairs, counting from 1,
        # so that a warning can name the bad line.
        for line_number, line_text in enumerate(log_file, start=1):

            # Blank lines (for example a final empty line) carry no entry.
            if not line_text.strip():
                continue

            # `json.loads` parses one line of JSON text into a Python value.
            # A malformed line is reported and skipped instead of stopping the whole command.
            try:
                entry = json.loads(line_text)
            except ValueError:
                print("warning: %s line %d is not valid JSON; skipped" % (log_path, line_number),
                      file=sys.stderr)
                continue

            # Each entry must be a JSON object with at least a window end.
            if not isinstance(entry, dict) or "window_end" not in entry:
                print("warning: %s line %d has no window_end; skipped" % (log_path, line_number),
                      file=sys.stderr)
                continue

            entries.append(entry)

    # DEBUG CHECK: `len(entries)` = number of cleanups recorded.
    return entries


def format_moment(moment, timezone):
    """Format an aware datetime for people: "Tue 2026-03-03 14:41" in `timezone`.

    Parameters:
        moment (datetime.datetime): an aware datetime.
        timezone (ZoneInfo): the zone in which to show the clock time.

    Returns:
        str: the formatted moment.
    """
    # `.astimezone(zone)` converts the moment to that zone's clock time (same instant).
    local_moment = moment.astimezone(timezone)

    # `strftime` formats with codes: %a short weekday, %Y year, %m month, %d day, %H:%M clock.
    return local_moment.strftime("%a %Y-%m-%d %H:%M")


def notion_edit_filter_text(finished):
    """Describe how to query Notion for the rows edited at or after `finished`.

    Notion's date filter on "Last edited" needs a plain date (a date-and-time value silently
    returns no row), and "Last edited" itself is stored in UTC. So the query filters on a plain
    date one day before the UTC date of the moment (no time zone can then hide a row), and the
    rows are then kept when their "Last edited" is at or after the moment in UTC.

    Parameters:
        finished (datetime.datetime): an aware datetime.

    Returns:
        str: e.g. "filter on the plain date 2026-03-02, then keep rows edited at or after
            2026-03-03T16:20Z (UTC)".
    """
    # `.astimezone(datetime.timezone.utc)` gives the same instant on the UTC clock.
    finished_utc = finished.astimezone(datetime.timezone.utc)

    # `.date()` keeps the calendar day; `- timedelta(days=1)` steps one day back.
    filter_day = finished_utc.date() - datetime.timedelta(days=1)

    # `strftime("%Y-%m-%dT%H:%MZ")` writes the UTC moment with a final "Z" (= UTC).
    utc_text = finished_utc.strftime("%Y-%m-%dT%H:%MZ")
    return ("filter on the plain date %s, then keep rows edited at or after %s (UTC)"
            % (filter_day.isoformat(), utc_text))


def highest_question_code(texts_by_source):
    """Find the highest "CL<n>" question code in some texts.

    Parameters:
        texts_by_source (dict): source name (e.g. a file path) -> its text.

    Returns:
        tuple (number, source): the highest n found (0 when none) and the source holding it
            ("" when none).

    Example:
        highest_question_code({"log": "questions CL3-CL7", "notes": "CL12 A"}) -> (12, "notes")
    """
    highest_number = 0
    highest_source = ""

    # `.items()` yields (key, value) pairs: (source name, text).
    for source, text in texts_by_source.items():

        # `pattern.finditer(text)` yields one match object per "CL<digits>" found;
        # `.group(1)` is the digits captured by the first (...) of the pattern.
        for match in QUESTION_CODE_PATTERN.finditer(text):
            number = int(match.group(1))
            if number > highest_number:
                highest_number = number
                highest_source = source

    # DEBUG CHECK: `highest_number` is 0 for a first cleanup without an orchestrator.
    return highest_number, highest_source


# ----------------------------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------------------------

def command_last(args, timezone):
    """`last`: print the latest cleanup and the --since value for the next one.

    Parameters:
        args (argparse.Namespace): parsed arguments (uses args.log).
        timezone (ZoneInfo): zone used to print times.

    Returns:
        int: exit status (0 = success, 1 = the log could not be read).
    """
    # Reading can fail only for an existing but unreadable file; that is reported as status 1.
    try:
        entries = read_log_entries(args.log)
    except OSError as error:
        print("error: cannot read %s: %s" % (args.log, error), file=sys.stderr)
        return 1

    # Without any recorded cleanup, the next one reads the last DEFAULT_LOOKBACK_HOURS_WITHOUT_LOG
    # hours. `datetime.datetime.now(zone)` is the current moment in that zone.
    if not entries:
        now_moment = datetime.datetime.now(timezone)
        lookback = datetime.timedelta(hours=DEFAULT_LOOKBACK_HOURS_WITHOUT_LOG)
        suggested_since = now_moment - lookback
        print("Log: %s (no cleanup recorded yet)" % args.log)
        print("Next cleanup: --since %s   (the last %d hours; ask the user if a longer period is needed)"
              % (suggested_since.isoformat(timespec="minutes"), DEFAULT_LOOKBACK_HOURS_WITHOUT_LOG))
        print("Hub edits by others since: %s" % suggested_since.isoformat(timespec="minutes"))
        print("Notion query for those edits: %s" % notion_edit_filter_text(suggested_since))
        return 0

    # The latest cleanup is the one whose window ended last. The log is normally in time order,
    # but taking the maximum also works if lines were added by hand out of order.
    # `max(items, key=function)` returns the item for which `function(item)` is largest; here
    # the function turns each entry's window_end text into a datetime so that moments, not
    # texts, are compared. A `lambda` is a small unnamed function written in one expression;
    # the equivalent named function would be:
    #     def window_end_of(entry): return parse_moment(entry["window_end"], timezone)
    # A line with an unreadable time raises ValueError; it is reported as status 1.
    try:
        latest_entry = max(entries, key=lambda entry: parse_moment(entry["window_end"], timezone))

        # The three moments of that cleanup, as aware datetimes. `entry.get(key, default)`
        # gives the default when an older line lacks the key.
        window_start = parse_moment(latest_entry.get("window_start", latest_entry["window_end"]), timezone)
        window_end = parse_moment(latest_entry["window_end"], timezone)
        finished = parse_moment(latest_entry.get("finished", latest_entry["window_end"]), timezone)
    except ValueError as error:
        print("error: unreadable time in %s: %s" % (args.log, error), file=sys.stderr)
        return 1

    # Human-readable report.
    print("Log: %s (%d cleanup(s) recorded)" % (args.log, len(entries)))
    print("Last cleanup: finished %s on %s (%s); it read conversations from %s to %s."
          % (format_moment(finished, timezone), latest_entry.get("system", "?"),
             latest_entry.get("client", "?"), format_moment(window_start, timezone),
             format_moment(window_end, timezone)))
    print("Summary: %s" % latest_entry.get("summary", ""))

    # The values the next cleanup needs:
    # - conversations are read from the previous window's end (--since), so that turns that
    #   happened while the previous cleanup was asking its questions are read again;
    # - Work Hub rows are compared from the previous cleanup's finish, so that its own writes
    #   are not mistaken for changes made by the user or by another conversation; the last
    #   line says how to ask Notion for those rows (plain date, then UTC comparison).
    print("Next cleanup: --since %s" % window_end.isoformat(timespec="minutes"))
    print("Hub edits by others since: %s" % finished.isoformat(timespec="minutes"))
    print("Notion query for those edits: %s" % notion_edit_filter_text(finished))
    return 0


def command_add(args, timezone, config):
    """`add`: append one cleanup to the log (or only show it with --dry-run).

    Parameters:
        args (argparse.Namespace): parsed arguments (window_start, window_end, finished,
            client, summary, system, log, dry_run).
        timezone (ZoneInfo): zone for times typed without an offset.
        config (dict): the settings (for the default system name).

    Returns:
        int: exit status (0 = success, 1 = inconsistent times or the log could not be written).
    """
    # Convert the typed moments; a typo is reported as status 1 with the offending text.
    try:
        window_start = parse_moment(args.window_start, timezone)
        window_end = parse_moment(args.window_end, timezone)
        if args.finished:
            finished = parse_moment(args.finished, timezone)
        else:
            # Without --finished, the cleanup is taken as finished now (seconds dropped).
            finished = datetime.datetime.now(timezone).replace(second=0, microsecond=0)
    except ValueError as error:
        print("error: unreadable time: %s" % error, file=sys.stderr)
        return 1

    # A window must not end before it starts, and the cleanup cannot finish before its
    # window ends. Both mistakes would make the next cleanup read the wrong period.
    if window_end < window_start:
        print("error: --window-end is before --window-start", file=sys.stderr)
        return 1
    if finished < window_end:
        print("error: --finished is before --window-end", file=sys.stderr)
        return 1

    # The line to store. `isoformat(timespec="minutes")` writes "2026-03-02T14:41+00:00";
    # minutes are enough for a window boundary. The system name comes from --system, else
    # from the settings (workhub_config.system_name: $CC_CLUSTER or the short host name,
    # with the aliases of the settings applied).
    entry = {
        "window_start": window_start.isoformat(timespec="minutes"),
        "window_end": window_end.isoformat(timespec="minutes"),
        "finished": finished.isoformat(timespec="minutes"),
        "system": args.system or workhub_config.system_name(config),
        "client": args.client,
        "summary": args.summary.strip(),
    }

    # `json.dumps(..., ensure_ascii=False)` keeps accents and arrows readable instead of "é".
    line_text = json.dumps(entry, ensure_ascii=False)

    # --dry-run: show what would be written and stop without touching the file.
    if args.dry_run:
        print("dry run, nothing written. Would append to %s:" % args.log)
        print(line_text)
        return 0

    # `os.makedirs(folder, exist_ok=True)` creates the folder and any missing parents, and does
    # nothing if it already exists. Mode "a" opens the file for appending: existing lines are
    # kept and the new line goes at the end, so no earlier cleanup is ever overwritten.
    try:
        os.makedirs(os.path.dirname(args.log), exist_ok=True)
        with open(args.log, "a", encoding="utf-8") as log_file:
            log_file.write(line_text + "\n")
    except OSError as error:
        print("error: cannot write %s: %s" % (args.log, error), file=sys.stderr)
        return 1

    # DEBUG CHECK: `tail -n 1 <log>` should now print `line_text`.
    print("Appended to %s:" % args.log)
    print(line_text)
    print("Next cleanup: --since %s" % entry["window_end"])
    return 0


def command_next_code(args, config):
    """`next-code`: print the next free question code (CL<n>).

    Codes already used are found in the cleanup log (the --summary of each cleanup lists its
    codes) and, when the orchestrator is enabled, in its DECISIONS.md and QUESTIONS.md, where
    answered and open cleanup questions keep their codes.

    Parameters:
        args (argparse.Namespace): parsed arguments (uses args.log).
        config (dict): the settings (workflow.orchestrator.*).

    Returns:
        int: exit status (0 = success, 1 = a file exists but cannot be read).
    """
    # The files to search: the log first, then the orchestrator's two files when enabled.
    candidate_paths = [args.log]
    orchestrator_enabled = workhub_config.get_setting(config, "workflow.orchestrator.enabled", default=False)
    orchestrator_folder = workhub_config.expand_path(
        workhub_config.get_setting(config, "workflow.orchestrator.folder", default=""))
    if orchestrator_enabled and orchestrator_folder:
        candidate_paths.append(os.path.join(orchestrator_folder, "DECISIONS.md"))
        candidate_paths.append(os.path.join(orchestrator_folder, "QUESTIONS.md"))

    # Read each existing file whole (they are small). A missing file holds no code yet.
    texts_by_source = {}
    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                texts_by_source[path] = handle.read()
        except OSError as error:
            print("error: cannot read %s: %s" % (path, error), file=sys.stderr)
            return 1

    # The highest code so far, and the next one.
    highest_number, highest_source = highest_question_code(texts_by_source)
    next_number = highest_number + 1

    # Report. The first line is the answer; the others say where it came from.
    print("Next free question code: CL%d" % next_number)
    if highest_number:
        print("Highest so far: CL%d in %s" % (highest_number, highest_source))
    else:
        print("No question code used yet.")

    # `", ".join(list) or "none"` prints "none" for an empty list (an empty text is false).
    print("Searched: %s" % (", ".join(sorted(texts_by_source)) or "none (no file exists yet)"))
    return 0


def describe_address(config, dotted_key, when_empty):
    """Return one value of the settings for the `settings` report, or what happens without it.

    Parameters:
        config (dict): the settings.
        dotted_key (str): e.g. "notion.views.now".
        when_empty (str): what the cleanup does when the value is empty, e.g. "rows query instead".

    Returns:
        str: the value, or "(empty: <when_empty>)".
    """
    value = workhub_config.get_setting(config, dotted_key, default="")
    if value:
        return str(value)
    return "(empty: %s)" % when_empty


def command_settings(args, config):
    """`settings`: print every value the cleanup needs, already resolved, one per line.

    The skill reads this once at the start instead of many `workhub_config.py get` calls, and
    learns at the same time which optional features are off (empty address = skipped).

    Parameters:
        args (argparse.Namespace): parsed arguments (uses args.log and args.timezone).
        config (dict): the settings.

    Returns:
        int: exit status: 0, or 2 when the Tasks data source is empty (nothing can be cleaned).
    """
    # The Tasks data source is the one address the cleanup cannot do without.
    tasks_data_source = workhub_config.get_setting(config, "notion.tasks_data_source", default="")

    # Projects: a list of names (notion.projects). The user's own rules for which work goes in
    # which project: skill_settings.work-hub-cleanup.project_rules, a list of plain sentences.
    projects = workhub_config.get_setting(config, "notion.projects", default=[])
    project_names = []
    for project_name in projects:
        project_names.append(str(project_name))
    project_rules = workhub_config.skill_setting(config, SKILL_NAME, "project_rules", default=[])
    # A single sentence typed as plain text instead of a list counts as a list of one.
    if isinstance(project_rules, str):
        project_rules = [project_rules]

    # The Research log is evidence only when it is enabled AND its data source is filled.
    research_log_enabled = workhub_config.get_setting(config, "notion.research_log.enabled", default=False)
    if research_log_enabled:
        research_log_text = describe_address(config, "notion.research_log.data_source",
                                             "not built yet; step 3.5 is skipped")
    else:
        research_log_text = "(off: notion.research_log.enabled is false; step 3.5 is skipped)"

    # The orchestrator's files are read only when it is enabled.
    orchestrator_enabled = workhub_config.get_setting(config, "workflow.orchestrator.enabled", default=False)
    orchestrator_folder = workhub_config.get_setting(config, "workflow.orchestrator.folder", default="")
    if orchestrator_enabled and orchestrator_folder:
        orchestrator_text = workhub_config.expand_path(orchestrator_folder)
    else:
        orchestrator_text = "(off: questions and answers stay in this chat; no DECISIONS.md)"

    # The Now limits, written into the rules block by work-hub-setup.
    max_rows = workhub_config.get_setting(config, "workflow.now_limits.max_rows", default=7)
    max_in_progress = workhub_config.get_setting(config, "workflow.now_limits.max_in_progress", default=3)

    # Whether the kit's cluster-login tool is installed (step 1 uses it to check connections).
    cluster_login_enabled = workhub_config.get_setting(config, "cluster_login.enabled", default=False)

    # Each line: a label, then the value. `%-22s` pads the label to 22 characters so that the
    # values line up in a column.
    lines = [
        ("Settings file", workhub_config.config_path()),
        ("Tasks data source", tasks_data_source or "(empty: the cleanup cannot run; say \"set up my work hub\")"),
        ("Now view", describe_address(config, "notion.views.now", "Now rows come from a rows query")),
        ("Focus view", describe_address(config, "notion.views.focus", "Focus rows come from a rows query")),
        ("Hub page", describe_address(config, "notion.hub_page_url", "no hub link in the report")),
        ("Task ID prefix", workhub_config.get_setting(config, "notion.task_id_prefix", default="TK")),
        ("Projects", "; ".join(project_names) or "(none listed)"),
        ("Research log", research_log_text),
        ("Orchestrator folder", orchestrator_text),
        ("Now limits", "%s rows, %s In progress" % (max_rows, max_in_progress)),
        ("Time zone", args.timezone),
        ("This machine", workhub_config.system_name(config)),
        ("cluster-login", "on" if cluster_login_enabled else "off"),
        ("Cleanup log", args.log),
    ]
    for label, value in lines:
        print("%-22s %s" % (label + ":", value))

    # The project rules, one per line, numbered; they decide the Project of new rows.
    # `enumerate(list, start=1)` gives (1, first item), (2, second item), ...
    if project_rules:
        print("Project rules:")
        for number, rule in enumerate(project_rules, start=1):
            print("  %d. %s" % (number, rule))
    else:
        print("%-22s %s" % ("Project rules:", "(none: choose the closest project, else Miscellaneous)"))

    # Exit status 2 when the one required address is missing.
    if not tasks_data_source:
        return 2
    return 0


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def build_parser():
    """Build the command-line parser with its sub-commands `last`, `add`, `next-code`, `settings`.

    Defaults that come from the settings are left as None here and filled in main(), so that
    `--help` works even on a machine where the kit is not set up yet.

    Returns:
        argparse.ArgumentParser: the parser.
    """
    # `RawDescriptionHelpFormatter` keeps the line breaks of the description in --help.
    parser = argparse.ArgumentParser(
        description="Remember when the Work Hub was last cleaned up (one JSON line per cleanup).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Options shared by every command.
    parser.add_argument("--log", default=None,
                        help="log file (default: <paths.state_dir>/work-hub-cleanup/cleanup_log.jsonl)")
    parser.add_argument("--timezone", default=None,
                        help="time zone for times without an offset and for printing "
                             "(default: owner.time_zone of the settings)")

    # `add_subparsers(dest="command")` makes the first word a sub-command and stores which one
    # was typed in `args.command`. `required=True` makes it mandatory.
    subparsers = parser.add_subparsers(dest="command", required=True)

    # `last` has no options of its own.
    subparsers.add_parser("last", help="print the latest cleanup and the --since value for the next one")

    # `add` describes one finished cleanup.
    add_parser = subparsers.add_parser("add", help="append one cleanup to the log")
    add_parser.add_argument("--window-start", required=True,
                            help="start of the period whose conversations were read (ISO 8601)")
    add_parser.add_argument("--window-end", required=True,
                            help="end of that period: when session_digest.py collected them (ISO 8601)")
    add_parser.add_argument("--finished", default=None,
                            help="when the Notion changes were written (default: now)")
    add_parser.add_argument("--client", required=True, choices=CLIENT_CHOICES,
                            help="program that ran the cleanup")
    add_parser.add_argument("--summary", required=True,
                            help="one line: conversations read, changes made, question codes used")
    add_parser.add_argument("--system", default=None,
                            help="system name (default: this machine's name from the settings)")
    add_parser.add_argument("--dry-run", action="store_true",
                            help="print the line that would be appended, write nothing")

    # `next-code` and `settings` have no options of their own.
    subparsers.add_parser("next-code", help="print the next free question code (CL<n>)")
    subparsers.add_parser("settings", help="print every setting the cleanup uses, already resolved")
    return parser


def main():
    """Parse the command line, read the settings, run the chosen command and exit with its status."""
    # Stage 1: read the command line. argparse exits with status 2 on invalid input.
    parser = build_parser()
    args = parser.parse_args()

    # Stage 2: read the settings. Without a settings file the kit is not set up: the message
    # tells the user what to say ("set up my work hub"), and the exit status is 2.
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        print("error: %s" % error, file=sys.stderr)
        sys.exit(2)

    # Stage 3: fill the defaults that come from the settings.
    if args.log is None:
        args.log = default_log_path(config)
    if args.timezone is None:
        args.timezone = workhub_config.time_zone(config)

    # Stage 4: build the time-zone object. An unknown zone name is an input error (status 1).
    try:
        timezone = ZoneInfo(args.timezone)
    except Exception as error:  # ZoneInfoNotFoundError and ValueError, depending on the name
        print("error: unknown time zone %r: %s" % (args.timezone, error), file=sys.stderr)
        sys.exit(1)

    # Stage 5: run the command and use its return value as the exit status.
    if args.command == "last":
        status = command_last(args, timezone)
    elif args.command == "add":
        status = command_add(args, timezone, config)
    elif args.command == "next-code":
        status = command_next_code(args, config)
    else:
        status = command_settings(args, config)
    sys.exit(status)


# `__name__ == "__main__"` is true only when this file is run as a program (not imported), so
# importing it in a test does not run main().
if __name__ == "__main__":
    main()
