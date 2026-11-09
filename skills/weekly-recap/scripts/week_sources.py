#!/usr/bin/env python3
"""
week_sources.py - the written record of one Monday-to-Sunday week, grouped by Work Hub row:
the conversations' requests and replies, the orchestrator's log entries and the user's recorded
decisions, and the session records (docs/sessions/*.md) written that week. Read-only.

PURPOSE
    The weekly recap needs, per row (Task ID such as TK-42), what was asked, what was done and
    decided, and where the results are. Those facts are spread over many files. This script
    gathers the ones of the week into one index plus one file per row, which Claude reads to
    write the recap. The Work Hub itself (statuses, Done reports, topic cards) and the Research
    log are read by Claude through Notion, not by this script.

SOURCES
    1. Conversations: sessions.json written by work-hub-cleanup's session_digest.py with
       --since <Monday 00:00>. That digest has no end time, so turns after the week are dropped
       here. Each turn keeps its request and the final reply (cut to --prompt-chars / --reply-chars).
    2. Orchestrator log (<workflow.orchestrator.folder>/log/YYYY-MM.md): one entry per "- " line
       plus its indented continuation; an entry's day is read from its text ("(Thu Nov 5)", "Fri
       Nov 6 10:19", "Tue Nov 3 12:41 —"); an entry without a day inherits the previous entry's
       day (the log is append-only, in time order).
    3. The user's decisions (<workflow.orchestrator.folder>/DECISIONS.md): the sections whose
       heading starts with a day of the week ("## Wed Nov 4 2026 (13:11)"), kept whole.
    4. Session records: paths ".../<workflow.session_records_subdir>/<day of the week>_*.md"
       (default subfolder docs/sessions) written in any transcript of the week (this machine and
       the reachable clusters), checked with `ls`.
    Without an orchestrator (workflow.orchestrator.enabled false), sources 2 and 3 are skipped
    and index.txt says so in one line.

INPUTS
    --week              last (default), this, 2026-W45 or a day (see recap_common.py)
    --sessions-json     sessions.json of session_digest.py (required)
    --orchestrator-dir  "auto" (default: workflow.orchestrator.folder of the settings), "none", or a folder
    --hosts             clusters for the session-record search: "auto" (default: every cluster of
                        the settings that does not share this machine's home), "none", or names
    --prompt-chars, --reply-chars   text limits per turn (default 400 and 900)
    -o / --output-dir   folder for the outputs (default <paths.outputs_dir>/weekly-recap/<week>/sources);
                        --overwrite to replace them

OUTPUTS (in --output-dir; folders mode 700, files mode 600)
    index.txt           read first: one line per row (conversations, turns, log entries, days,
                        its file), the conversations with no row, the orchestrator conversations,
                        and the session records found;
    rows/TK-n.txt       per row: each conversation's turns (request + reply) and its log entries;
    rows/no_row.txt     the same for work with no row;
    rows/orchestrator.txt  the [ORCH] conversations' turns (their events are also in the log);
    decisions.txt       the user's decisions of the week, verbatim;
    week_sources.json   everything as data.

USAGE
    python3 week_sources.py --week 2026-W45 \
        --sessions-json ~/workhub/outputs/weekly-recap/2026-W45/sessions/sessions.json

DEPENDENCIES
    Python >= 3.9, standard library only; bash, find, grep and xargs for the session-record
    search; ssh for the clusters (already-open shared connections only).

LIMITS
    - A log entry is filed under every Task ID it names; an entry with none goes to "no row".
    - A day written only as "Nov 5" without a weekday is not recognised; such entries inherit.
    - Day and month names are read in English (the orchestrator templates write them so).

EXIT STATUS
    0 = success; 1 = missing sessions.json, existing output without --overwrite, or bad --week;
    2 = bad arguments or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------------------------------

# `argparse` reads the options; `datetime` handles days; `json` reads and writes data; `os`
# gives paths; `re` finds days and clock times in texts; `sys` gives sys.exit.
import argparse
import datetime
import json
import os
import re
import sys

# The skill's shared helpers (same folder): settings, week window, Task IDs, remote commands,
# private files. recap_common imports workhub_config.py (the settings reader) from the same folder.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recap_common  # noqa: E402
import workhub_config  # noqa: E402


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# Month abbreviations -> month numbers, for reading "Nov 5".
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
          "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}

# A day written in the log or the decisions, character by character:
# `(Mon|Tue|Wed|Thu|Fri|Sat|Sun)` a weekday name; `\s+` spaces; `(Jan|...|Dec)` a month name
# (captured); `\s+`; `(\d{1,2})` the day of the month, one or two digits (captured); `\b` its end.
# It matches "Thu Nov 5", "Thu Nov 05" and "Fri Nov 6".
DAY_PATTERN = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b")

# Characters allowed in the session-records subfolder setting (it goes into a grep pattern):
# letters, digits, "_", ".", "/" and "-". `fullmatch` requires the whole text to be made of them.
SAFE_SUBDIR_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")


# ----------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------

def week_days(start):
    """Return the seven days (datetime.date) of the week starting at `start`."""
    return [start.date() + datetime.timedelta(days=offset) for offset in range(7)]


def day_in_text(text, year):
    """Find the first "Thu Nov 5"-style day in the first 60 characters of a text.

    Parameters:
        text (str): a log entry or a heading.
        year (int): the year to attach (the log files are monthly, so the year is known).

    Returns:
        datetime.date or None.
    """
    # Only the start of the text: a day mentioned later ("ends Fri Nov 6") is not the entry's day.
    match = DAY_PATTERN.search(text[:60])
    if not match:
        return None
    try:
        return datetime.date(year, MONTHS[match.group(2)], int(match.group(3)))
    except ValueError:
        return None


# A clock time "HH:MM", character by character: `(?<!\d)` no digit before, `([01]?\d|2[0-3])` the
# hour 0-23, `:` the colon, `([0-5]\d)` the minutes 00-59, `(?!\d)` no digit after.
CLOCK_PATTERN = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


def clock_minutes(text):
    """Return the first clock time in the first 25 characters of a log entry, in minutes.

    Parameters:
        text (str): an entry such as "Tue Nov 3 12:41 — ..." or "00:08 the user: ...".
    Returns:
        int or None: e.g. 761 for 12:41; None when the entry starts without a time.
    """
    match = CLOCK_PATTERN.search(text[:25])
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def shorten(text, limit):
    """Cut a text to `limit` characters on one line, marking the cut with " …"."""
    one_line = " ".join((text or "").split())
    if len(one_line) <= limit:
        return one_line
    return one_line[:limit - 2] + " …"


# ----------------------------------------------------------------------------------------------
# 1. Conversations
# ----------------------------------------------------------------------------------------------

def conversations_of_week(sessions_json_path, start, end, prompt_chars, reply_chars, timezone):
    """Keep the conversations and turns of the week.

    Parameters:
        sessions_json_path (str): session_digest.py's sessions.json.
        start, end (datetime.datetime): the week.
        prompt_chars, reply_chars (int): text limits.
        timezone (ZoneInfo): for printing times.

    Returns:
        list of dict: {"id", "title", "system", "cwd", "task", "client",
                       "turns": [{"time", "date", "kind", "prompt", "reply"}]}.
    """
    with open(sessions_json_path, encoding="utf-8") as sessions_file:
        report = json.load(sessions_file)
    start_epoch = start.timestamp()
    end_epoch = end.timestamp()

    conversations = []
    for session in report.get("sessions", []):
        kept_turns = []
        for turn in session.get("turns", []):
            moment = turn.get("time")
            # A turn belongs to the week when its request was made inside the window.
            if moment is None or not (start_epoch <= moment < end_epoch):
                continue
            when = datetime.datetime.fromtimestamp(moment, timezone)
            kept_turns.append({"time": "%s %s" % (recap_common.readable_day(when), when.strftime("%H:%M")),
                               "date": when.date().isoformat(),
                               "kind": turn.get("kind") or "",
                               "prompt": shorten(turn.get("prompt"), prompt_chars),
                               "reply": shorten(turn.get("reply"), reply_chars)})
        if not kept_turns:
            continue
        conversations.append({"id": (session.get("id") or "")[:8], "title": session.get("title") or "",
                              "system": session.get("system") or "", "cwd": session.get("cwd") or "",
                              "task": session.get("task") or "", "client": session.get("client") or "",
                              "turns": kept_turns})
    return conversations


# ----------------------------------------------------------------------------------------------
# 2. and 3. The orchestrator's log and decisions
# ----------------------------------------------------------------------------------------------

def log_entries_of_week(start, orchestrator_dir, task_pattern, prefix):
    """Read the orchestrator log entries whose day lies in the week.

    Parameters:
        start (datetime.datetime): Monday 00:00.
        orchestrator_dir (str): the orchestrator folder ("" = none).
        task_pattern (re.Pattern), prefix (str): Task ID finder (recap_common.task_id_pattern).

    Returns:
        list of dict: {"day" (str), "date" (ISO), "text", "tasks"}.
    """
    days = set(week_days(start))
    entries = []
    for path, year in recap_common.orchestrator_log_files(orchestrator_dir, start):
        current_text = None
        current_day = None
        # Clock time (minutes after midnight) of the last entry, to notice midnight.
        last_minutes = None
        with open(path, encoding="utf-8") as log_file:
            lines = log_file.readlines()
        # A sentinel heading at the end closes the last entry with the same code as the others.
        for line in lines + ["# end\n"]:
            continues_entry = current_text is not None and line.startswith(" ")
            if continues_entry:
                current_text += " " + line.strip()
                continue
            # Anything else closes the open entry first.
            if current_text is not None:
                written_day = day_in_text(current_text[2:], year)
                minutes = clock_minutes(current_text[2:])
                if written_day is not None:
                    current_day = written_day
                elif current_day is not None and minutes is not None and last_minutes is not None:
                    # An entry with only a clock time ("00:08 ...") after a late one ("23:34
                    # ...") is on the next day. Entries are sometimes a little out of order, so
                    # only a step back of more than 6 hours counts as midnight.
                    if minutes < last_minutes - 6 * 60:
                        current_day = current_day + datetime.timedelta(days=1)
                if minutes is not None:
                    last_minutes = minutes
                if current_day in days:
                    entries.append({"day": recap_common.readable_day(current_day),
                                    "date": current_day.isoformat(), "text": current_text[2:],
                                    "tasks": recap_common.task_ids_in(current_text, task_pattern, prefix)})
                current_text = None
            if line.startswith("- "):
                current_text = line.strip()
    return entries


def decisions_of_week(start, orchestrator_dir):
    """Return the DECISIONS.md sections whose heading names a day of the week.

    Returns:
        list of dict: {"heading", "text"}.
    """
    if not orchestrator_dir:
        return []
    path = os.path.join(orchestrator_dir, "DECISIONS.md")
    if not os.path.isfile(path):
        return []
    days = set(week_days(start))
    sections = []
    current = None
    with open(path, encoding="utf-8") as decisions_file:
        for line in decisions_file:
            if line.startswith("## "):
                if current is not None:
                    sections.append(current)
                current = None
                day = day_in_text(line[3:], start.year)
                # A heading written in January about late December is not expected; the week's
                # own year is used.
                if day in days:
                    current = {"heading": line[3:].strip(), "text": ""}
            elif current is not None:
                current["text"] += line
    if current is not None:
        sections.append(current)
    for section in sections:
        section["text"] = section["text"].strip()
    return sections


# ----------------------------------------------------------------------------------------------
# 4. Session records
# ----------------------------------------------------------------------------------------------

def ere_escape(text):
    """Escape the characters that have a meaning in a grep -E pattern ("." -> "\\.").

    Only "." can occur after the SAFE_SUBDIR_PATTERN check; the others are escaped too, so
    the function is correct for any text.
    """
    special_characters = ".^$*+?()[]{}|\\"
    escaped = []
    for character in text:
        if character in special_characters:
            escaped.append("\\" + character)
        else:
            escaped.append(character)
    return "".join(escaped)


def session_record_command(start, transcript_dirs, records_subdir="docs/sessions"):
    """Build the shell line that lists the week's session-record paths found in transcripts.

    The line greps every Claude Code (and, when enabled, Codex) transcript changed since Monday
    for paths like /x/y/docs/sessions/2026-11-04_1912_demo-handover.md, then keeps those that
    still exist.

    Parameters:
        start (datetime.datetime): Monday 00:00.
        transcript_dirs (list of str): folders as in the settings ("~/.claude/projects"); "~" is
            expanded by the shell that runs the line, so the same line works on a cluster.
        records_subdir (str): workflow.session_records_subdir, e.g. "docs/sessions".

    Returns:
        str: a bash command line, safe to run locally or through SSH.
    """
    dates = "|".join(day.isoformat() for day in week_days(start))
    cleaned_subdir = records_subdir.strip("/")
    if not SAFE_SUBDIR_PATTERN.fullmatch(cleaned_subdir):
        cleaned_subdir = "docs/sessions"
    # grep -E pattern, part by part: `/[A-Za-z0-9_./-]*` a path start (slash, then path
    # characters); `docs/sessions/` the literal folder (dots escaped); `(dates)` one of the seven
    # days; `_[A-Za-z0-9_.-]*\.md` the rest of the file name ending in ".md".
    pattern = "/[A-Za-z0-9_./-]*%s/(%s)_[A-Za-z0-9_.-]*\\.md" % (ere_escape(cleaned_subdir), dates)
    newer = start.strftime("%Y-%m-%d")
    folders = " ".join(recap_common.shell_path(folder) for folder in transcript_dirs)
    # find ... -newermt DAY: files modified since that day; -print0 / xargs -0: safe with odd
    # names; grep -h no file names, -o only the matching part, -E extended patterns;
    # sort -u removes repeats; the loop keeps paths that exist (`[ -f "$p" ]`); the final `true`
    # makes the line's exit status 0 even when the last path does not exist.
    return ("find %s -name '*.jsonl' -newermt %s -print0 2>/dev/null"
            " | xargs -0 -r grep -hoE '%s' 2>/dev/null | sort -u"
            " | while read -r p; do if [ -f \"$p\" ]; then echo \"$p\"; fi; done; true"
            % (folders, newer, pattern))


def session_records_of_week(start, hosts, this_system, machine_label, transcript_dirs, records_subdir):
    """List the week's session records on this machine and the reachable clusters.

    Parameters:
        start (datetime.datetime): Monday 00:00.
        hosts (list of dict): {"name", "ssh_alias"} from recap_common.hosts_from_option.
        this_system (str): this machine's system name.
        machine_label (str): the system name written before local paths ("ws01").
        transcript_dirs (list of str), records_subdir (str): see session_record_command.

    Returns:
        tuple (records, notes): records = list of "system:/path" texts; notes = unreached hosts.
    """
    records = []
    notes = []
    command = session_record_command(start, transcript_dirs, records_subdir)
    status, stdout, _ = recap_common.run_command(["bash", "-c", command])
    if status == 0:
        records.extend(clean_record_paths(machine_label, stdout))
    for host in hosts:
        if host["name"] == this_system:
            continue
        remote, note = recap_common.remote_command(host, command, this_system)
        if remote is None:
            notes.append("%s not searched: %s" % (host["name"], note))
            continue
        status, stdout, stderr = recap_common.run_command(remote)
        if status != 0:
            notes.append("%s search failed: %s" % (host["name"], shorten(stderr, 150)))
            continue
        records.extend(clean_record_paths(host["name"], stdout))
    return records, notes


def clean_record_paths(machine, output_text):
    """Turn the search output into unique "system:/path" texts.

    Transcripts also hold relative forms such as "/../../srv/x/docs/sessions/a.md" (the grep
    starts at the first slash of "../../srv/..."); `os.path.normpath` resolves "/../../srv/x"
    to "/srv/x", so each record appears once.

    Parameters:
        machine (str): the system name to prefix.
        output_text (str): one path per line.

    Returns:
        list of str, sorted.
    """
    unique_paths = set()
    for path in output_text.split():
        unique_paths.add(os.path.normpath(path))
    return ["%s:%s" % (machine, path) for path in sorted(unique_paths)]


# ----------------------------------------------------------------------------------------------
# The digest
# ----------------------------------------------------------------------------------------------

def is_orchestrator(conversation):
    """Tell whether a conversation is an orchestrator ([ORCH]); those record no row by design."""
    title = conversation["title"]
    return "[ORCH]" in title or "orchestrator" in title.lower()


def group_by_row(conversations, log_entries):
    """Collect, per row, its conversations and log entries.

    Returns:
        tuple (rows, unfiled, orchestrators):
            rows = {task: {"conversations": [...], "log": [...]}} sorted by Task number;
            unfiled = {"conversations": [...], "log": [...]}: work with no row;
            orchestrators = list of the [ORCH] conversations (their facts are in the log).
    """
    rows = {}
    unfiled = {"conversations": [], "log": []}
    orchestrators = []
    for conversation in conversations:
        if conversation["task"]:
            rows.setdefault(conversation["task"], {"conversations": [], "log": []})["conversations"].append(conversation)
        elif is_orchestrator(conversation):
            orchestrators.append(conversation)
        else:
            unfiled["conversations"].append(conversation)
    for entry in log_entries:
        if not entry["tasks"]:
            unfiled["log"].append(entry)
        for task in entry["tasks"]:
            rows.setdefault(task, {"conversations": [], "log": []})["log"].append(entry)

    # Sort rows by their number: "TK-9" before "TK-110" (a plain text sort would not).
    ordered = {task: rows[task] for task in sorted(rows, key=recap_common.task_number)}
    return ordered, unfiled, orchestrators


def format_conversation(conversation, lines):
    """Append one conversation and its turns to `lines`."""
    lines.append("CONVERSATION %s %s | %s:%s | %d turn(s) this week"
                 % (conversation["id"], conversation["title"], conversation["system"], conversation["cwd"],
                    len(conversation["turns"])))
    for turn in conversation["turns"]:
        lines.append("  [%s] ASKED: %s" % (turn["time"], turn["prompt"]))
        if turn["reply"]:
            lines.append("           REPLY: %s" % turn["reply"])
    lines.append("")


def format_group(title, content, log_chars):
    """Text of one row file (or of the no-row file): its conversations, then its log entries."""
    lines = [title, ""]
    for conversation in content["conversations"]:
        format_conversation(conversation, lines)
    if content["log"]:
        lines.append("ORCHESTRATOR LOG ENTRIES")
        for entry in content["log"]:
            lines.append("  %s: %s" % (entry["day"], shorten(entry["text"], log_chars)))
    return "\n".join(lines) + "\n"


def days_active(content):
    """The first and last day a row was touched this week ("Tue Nov 3 - Thu Nov 5").

    Parameters:
        content (dict): a row's {"conversations", "log"}; turns and entries carry an ISO "date".

    Returns:
        str: one day, a range, or "-".
    """
    # ISO dates ("2026-11-03") sort correctly as plain texts, unlike "Tue Nov 3".
    dates = [entry["date"] for entry in content["log"]]
    for conversation in content["conversations"]:
        dates.extend(turn["date"] for turn in conversation["turns"])
    if not dates:
        return "-"
    first = recap_common.readable_day(datetime.date.fromisoformat(min(dates)))
    last = recap_common.readable_day(datetime.date.fromisoformat(max(dates)))
    return first if first == last else "%s - %s" % (first, last)


def format_index(report, file_names):
    """Build index.txt: one line per row, then the work with no row, decisions and records."""
    lines = ["Week %s: written record by Work Hub row" % report["week"],
             "(collected %s on %s; %s time). Read a row's file for its turns and log entries." %
             (report["collected"], report["system"], report["time_zone"]), ""]
    for note in report["notes"]:
        lines.append("Note: " + note)
    lines.append("")
    lines.append("ROWS (row | conversations, turns | log entries | days | file)")
    for task, content in report["rows"].items():
        turns = sum(len(conversation["turns"]) for conversation in content["conversations"])
        titles = "; ".join(conversation["title"] for conversation in content["conversations"])
        lines.append("  %-7s %d conv., %3d turns | %3d log | %s | %s"
                     % (task, len(content["conversations"]), turns, len(content["log"]),
                        days_active(content), file_names[task]))
        if titles:
            lines.append("          " + titles)
    lines.append("")
    lines.append("NO ROW (%d conversations, %d log entries) -> rows/no_row.txt"
                 % (len(report["unfiled"]["conversations"]), len(report["unfiled"]["log"])))
    for conversation in report["unfiled"]["conversations"]:
        lines.append("  %s %3d turns  %s | %s:%s" % (conversation["id"], len(conversation["turns"]),
                                                     conversation["title"], conversation["system"],
                                                     conversation["cwd"]))
    lines.append("")
    lines.append("ORCHESTRATOR CONVERSATIONS (%d) -> rows/orchestrator.txt (their events are in the log entries)"
                 % len(report["orchestrators"]))
    for conversation in report["orchestrators"]:
        lines.append("  %s %3d turns  %s" % (conversation["id"], len(conversation["turns"]), conversation["title"]))
    lines.append("")
    lines.append("DECISIONS: %d section(s) -> decisions.txt" % len(report["decisions"]))
    lines.append("")
    lines.append("SESSION RECORDS WRITTEN THIS WEEK (%d, each checked to exist)" % len(report["session_records"]))
    for record in report["session_records"]:
        lines.append("  " + record)
    return "\n".join(lines) + "\n"


def format_decisions(report):
    """Build decisions.txt: the week's DECISIONS.md sections, whole."""
    lines = ["Decisions of the week %s (from DECISIONS.md, verbatim)" % report["week"], ""]
    for section in report["decisions"]:
        lines.append("## " + section["heading"])
        lines.append(section["text"])
        lines.append("")
    return "\n".join(lines) + "\n"


def write_digest(report, output_dir, overwrite):
    """Write index.txt, decisions.txt, week_sources.json and rows/<file>.txt.

    Returns:
        str: the path of index.txt.
    """
    rows_dir = os.path.join(output_dir, "rows")
    recap_common.prepare_output_dir(output_dir)
    recap_common.prepare_output_dir(rows_dir)

    # Row files: "TK-126" -> rows/TK-126.txt.
    file_names = {}
    for task, content in report["rows"].items():
        file_names[task] = "rows/%s.txt" % task
        recap_common.write_private_text(os.path.join(output_dir, file_names[task]),
                                        format_group("%s, week %s" % (task, report["week"]), content, 900),
                                        overwrite)
    recap_common.write_private_text(os.path.join(rows_dir, "no_row.txt"),
                                    format_group("Work with no row, week %s" % report["week"],
                                                 report["unfiled"], 700), overwrite)
    recap_common.write_private_text(os.path.join(rows_dir, "orchestrator.txt"),
                                    format_group("Orchestrator conversations, week %s" % report["week"],
                                                 {"conversations": report["orchestrators"], "log": []}, 0),
                                    overwrite)
    recap_common.write_private_text(os.path.join(output_dir, "decisions.txt"), format_decisions(report), overwrite)
    recap_common.write_private_text(os.path.join(output_dir, "week_sources.json"),
                                    json.dumps(report, indent=1) + "\n", overwrite)
    index_path = os.path.join(output_dir, "index.txt")
    recap_common.write_private_text(index_path, format_index(report, file_names), overwrite)
    return index_path


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def orchestrator_dir_from_option(option_text, config):
    """Turn --orchestrator-dir into a folder: "auto" = the settings, "none" = "", else the path."""
    cleaned = (option_text or "").strip()
    if cleaned == "none":
        return ""
    if cleaned in ("", "auto"):
        return recap_common.orchestrator_dir(config)
    return os.path.abspath(workhub_config.expand_path(cleaned))


def main():
    """Gather the four sources of the week and write the digest."""
    parser = argparse.ArgumentParser(description="The written record of one Monday-to-Sunday week, "
                                                 "grouped by Work Hub row. Read-only.")
    parser.add_argument("--week", default="last", help="last (default), this, YYYY-Www or YYYY-MM-DD")
    parser.add_argument("--sessions-json", required=True, help="sessions.json of session_digest.py")
    parser.add_argument("--orchestrator-dir", default="auto",
                        help="'auto' (default: workflow.orchestrator.folder), 'none', or a folder")
    parser.add_argument("--hosts", default="auto",
                        help="clusters for the session-record search: 'auto' (default: from the settings), "
                             "'none', or comma-separated names")
    parser.add_argument("--prompt-chars", type=int, default=400, help="characters per request (default 400)")
    parser.add_argument("--reply-chars", type=int, default=900, help="characters per reply (default 900)")
    parser.add_argument("-o", "--output-dir", default="",
                        help="folder for index.txt, rows/, decisions.txt and week_sources.json "
                             "(default <paths.outputs_dir>/weekly-recap/<week>/sources)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    args = parser.parse_args()

    config = recap_common.load_settings()
    timezone = recap_common.owner_timezone(config)
    try:
        start, end, label = recap_common.week_window(args.week, timezone)
    except ValueError as error:
        print("week_sources.py: %s" % error, file=sys.stderr)
        sys.exit(1)
    output_dir = args.output_dir or recap_common.output_dir_for(config, recap_common.week_id(start), "sources")
    index_path = os.path.join(output_dir, "index.txt")
    for path in (index_path, os.path.join(output_dir, "week_sources.json")):
        if os.path.exists(path) and not args.overwrite:
            print("week_sources.py: %s exists; pass --overwrite to replace it" % path, file=sys.stderr)
            sys.exit(1)
    if not os.path.isfile(args.sessions_json):
        print("week_sources.py: %s not found (run session_digest.py first)" % args.sessions_json, file=sys.stderr)
        sys.exit(1)

    this_system = workhub_config.system_name(config)
    machine_label = recap_common.this_machine_label(config, this_system)
    hosts = recap_common.hosts_from_option(args.hosts, config, this_system)
    prefix = recap_common.task_id_prefix(config)
    task_pattern = recap_common.task_id_pattern(prefix)
    orchestrator_folder = orchestrator_dir_from_option(args.orchestrator_dir, config)
    records_subdir = str(workhub_config.get_setting(config, "workflow.session_records_subdir",
                                                    default="docs/sessions"))
    notes = []

    conversations = conversations_of_week(args.sessions_json, start, end, args.prompt_chars,
                                          args.reply_chars, timezone)
    if not orchestrator_folder:
        notes.append("no orchestrator in the settings (workflow.orchestrator): no log or decisions read")
    elif not recap_common.orchestrator_log_files(orchestrator_folder, start):
        notes.append("no orchestrator log for this week in %s/log" % orchestrator_folder)
    log_entries = log_entries_of_week(start, orchestrator_folder, task_pattern, prefix)
    decisions = decisions_of_week(start, orchestrator_folder)
    records, record_notes = session_records_of_week(start, hosts, this_system, this_system,
                                                    recap_common.transcript_dirs(config), records_subdir)
    notes.extend(record_notes)

    rows, unfiled, orchestrators = group_by_row(conversations, log_entries)
    report = {"schema": "weekly-recap-sources/1", "week": label,
              "collected": datetime.datetime.now(timezone).strftime("%Y-%m-%d %H:%M"),
              "time_zone": workhub_config.time_zone(config),
              "system": this_system, "transcripts_label": machine_label, "notes": notes,
              "rows": rows, "unfiled": unfiled, "orchestrators": orchestrators,
              "decisions": decisions, "session_records": records}

    index_path = write_digest(report, output_dir, args.overwrite)
    print("%d row(s), %d conversation(s) with no row, %d orchestrator conversation(s), %d log entries, "
          "%d decision section(s), %d session record(s); index %s"
          % (len(rows), len(unfiled["conversations"]), len(orchestrators), len(log_entries), len(decisions),
             len(records), index_path))


if __name__ == "__main__":
    main()
