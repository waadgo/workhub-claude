#!/usr/bin/env python3
"""
conversation_title.py: name every Claude Code conversation automatically, in the form
"[Nov-04 09:10][ws01][UC] TK-42 Data cleaning" (start time, machine, role tag, topic).

PURPOSE
    Claude cannot rename a conversation itself, but Claude Code lets a "UserPromptSubmit" hook (a script that Claude
    Code runs just before each prompt the user sends) return a new conversation title. This script does that, without
    calling any model (no usage cost). With many conversations open on several machines, a name that says when and
    where a conversation started, and which Work Hub row it works on, lets the user (and other conversations, which
    address each other by name) find the right one at a glance.
      1. TAGS. "[Mon-DD HH:MM]" = when the conversation started (first time stamp of its transcript, else now), in the
         user's time zone (setting owner.time_zone), 24 h, English month; "[machine]" = this machine's name as used in
         file paths shown to people (workhub_config.system_name: the variable named by system_name.override_env,
         default CC_CLUSTER, else the short host name, then the renames in system_name.aliases); "[UC]" = role tag
         "unitopic conversation" (a conversation that works on one topic = one Work Hub row). The orchestrator
         conversation (when workflow.orchestrator.enabled is true) gets "[ORCH]"
         instead through the /rename line of its first reply; this script never writes "[ORCH]", and a name set with
         /rename always wins (point 3).
      2. TOPIC. Claude Code's own automatic title (the transcript's latest "ai-title" record, e.g. "Timeline figure date
         range"). Claude Code makes that title from the FIRST prompt, but only while the conversation has no title
         (observed with Claude Code 2.1: after a hook set a title, no "ai-title" record was ever written). So the script
         sets NOTHING at the first prompt; it remembers the start time and the prompt's first words. At the second
         prompt it sets "[tags] <automatic title>", or "[tags] <first words of the first prompt>" when no automatic
         title exists. The name is set once and never changed again by the script (except point 5).
      3. NEVER OVERRIDE THE USER. The current name is read from the transcript: each time a name is set (by this
         script or by the user's /rename), Claude Code writes a record {"type": "custom-title", "customTitle": "..."}
         and writes it again from time to time, so the latest one near the end is the name shown now. The hook input
         may also carry the name in "session_title" (often empty); the script uses it when it is not empty, and
         otherwise reads the transcript. When the current name is neither empty, nor Claude Code's automatic title,
         nor the name this script set, the user (or another tool) renamed the conversation: the script stops touching
         it.
      4. TASK ID. When the conversation has found or created its Work Hub row during its first reply, it records the
         row's Task ID and short title in this script's state file with set_conversation_task.py (in this folder):
         "task": "TK-42", "task_topic": "Data cleaning". The prefix "TK" comes from the setting notion.task_id_prefix.
         At the second prompt the topic then becomes "TK-42 Data cleaning" instead of the automatic title, e.g.
         "[Nov-04 09:10][ws01][UC] TK-42 Data cleaning". Without these fields the script behaves as in point 2.
      5. TASK ID AFTER NAMING. When the row is recorded only after the name became final, the script adds it ONCE, at
         the user's next message: the tags stay and the topic becomes "TK-42 Data cleaning". It does so only while the
         current name (point 3) is still exactly the name this script set. A name the user set stays as it is;
         set_conversation_task.py then prints a ready-to-paste /rename line instead. When the current name cannot be
         read, nothing changes (the script looks again at the next prompt).
      6. ONLY THE USER'S PROMPTS COUNT. Claude Code also runs this hook for prompts that the user did not type: a
         subagent's report ("<agent-message ..."), a message from another conversation ("<cross-session-message ..."),
         a background-task notice ("<task-notification>"). Counting such a prompt as the user's second message would
         fix the name before the conversation had a chance to record its row, so these prompts are ignored completely.
    Consequence: a conversation gets its tagged name when the user sends their SECOND message. After that, the script
    reads its small state file (and, while a recorded Task ID is not yet in the name, the end of the transcript) and
    exits. Renaming also changes the name other sessions use to message this one (ListAgents / SendMessage address).

SETTINGS (read with workhub_config.py, an identical copy of the kit's lib/workhub_config.py, in this folder)
    owner.time_zone               IANA zone of the start-time tag, e.g. "Europe/Lisbon" (default UTC; an unknown
                                  zone name also falls back to UTC).
    system_name.override_env      variable whose value names this machine (default CC_CLUSTER), else the host name.
    system_name.aliases           renames of machine names, e.g. {"login1": "mycluster"}.
    notion.task_id_prefix         prefix of Task IDs (default "TK"), used by set_conversation_task.py.
    workflow.conversation_names   true (default): name conversations as described above. false: the user does not
                                  want conversations named after their rows; this script then does nothing at all.
    paths.state_dir               base folder of the state files (default ~/.local/state/workhub-claude).
    The settings file is ~/.config/workhub-claude/config.json, or the file named by $WORKHUB_CLAUDE_CONFIG.
    NOTHING HAPPENS when there is no settings file (workhub-claude is not set up on this machine) or when
    workflow.conversation_names is false: the script exits 0 at once, prints nothing, writes no state and never
    renames. (The work-hub-setup skill installs the hook in both cases, so turning names on later only needs the
    setting.)

INPUT (standard input: one JSON object written by Claude Code)
    {"session_id": "...", "transcript_path": "/.../<session>.jsonl", "cwd": "...",
     "hook_event_name": "UserPromptSubmit", "prompt": "...", "session_title": "<current title; may be empty or absent>"}

OUTPUT
    - Nothing (the usual case once the title is final), or
    - {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": "<new title>"}}
    Side effects: one state file per conversation, <state folder>/<session_id>.json, where the state folder is
    workhub_config.state_dir(config, "conversation_titles") (default ~/.local/state/workhub-claude/conversation_titles,
    mode 700). Its fields: after the first prompt {"start": "Nov-04 09:10", "provisional": "<first words>"}; after
    naming {"title": "<title set>", "final": true, "start": ...}; after a rename by the user {"final": true, ...}; the
    optional "task" and "task_topic" fields written by set_conversation_task.py are kept in every case; after point 5
    was handled "task_title_checked": "TK-42", so the transcript is not read again for that row. Other workhub-claude
    skills (work-hub-cleanup, find-conversation) read these files, so the format must stay the same. One line per
    change is appended to <state folder>/changes.log (provenance: when, which conversation, old -> new, why).

SAFETY
    "Fails open": any error ends the script silently with exit code 0, so a prompt is never blocked and no title is
    changed by a bug. Claude Code also stops it after the timeout given in settings.json (5 s). It prints no
    "additionalContext", so it adds nothing to Claude's context. An import error before main() starts (Python older
    than 3.9 has no zoneinfo; workhub_config.py missing) ends with exit code 1, which Claude Code also treats as a
    non-blocking error (only exit code 2 blocks a prompt); install_hooks.py checks both before it installs.

DEPENDENCIES
    Python >= 3.9 (zoneinfo), standard library only, plus workhub_config.py in the same folder.

INSTALLATION
    The work-hub-setup skill runs install_hooks.py (in this folder), which copies these files to
    ~/.local/share/workhub-claude/hooks/ and adds to ~/.claude/settings.json:
        "UserPromptSubmit": python3 ~/.local/share/workhub-claude/hooks/conversation_title.py   (timeout 5)
    `python3 install_hooks.py --uninstall --apply` removes that entry again.

EXAMPLE (manual test with a fake event and a temporary state folder)
    E='{"session_id":"t1","transcript_path":"/nonexistent.jsonl","prompt":"Check the fold sync"}'
    echo "$E" | python3 conversation_title.py     # first prompt: prints nothing
    echo "$E" | python3 conversation_title.py     # second prompt:
    -> {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": "[Nov-04 09:10][ws01][UC] Check the fold sync"}}
    (delete <state folder>/t1.json afterwards)

TESTS
    python3 -m pytest -q tests/        (from this folder; temporary HOME and settings file)

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).

DOCUMENTATION LEVEL
    ULTRA-style for a small tool: every block has an explanation above it.
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports (standard library, then the settings reader next to this file)
# ---------------------------------------------------------------------------------------------------------------------

# datetime: parse the transcript's ISO time stamps and convert them to the user's time zone. json: the hook event, the
# transcript lines, the state file. os: paths, folders, environment variables. re: recognise a title that already has
# the tag form. sys: standard input/output and the import path. unicodedata: turn "Müller" into "Muller".
# zoneinfo: IANA time zones such as "Europe/Lisbon" (with daylight saving).
import datetime
import json
import os
import re
import sys
import unicodedata
import zoneinfo

# `sys.dont_write_bytecode = True` stops Python from writing a __pycache__ folder next to the imported module below,
# so the install folder keeps only the files the installer copied (and an uninstall leaves nothing behind).
sys.dont_write_bytecode = True
# The folder of this file. `os.path.abspath(__file__)` is this script's full path; `dirname` removes the file name.
HOOK_FOLDER = os.path.dirname(os.path.abspath(__file__))
# Putting that folder first on the import path makes `import workhub_config` load the copy next to this file, even
# when Claude Code starts the hook from another working directory.
sys.path.insert(0, HOOK_FOLDER)
# workhub_config.py (same folder) finds and reads the settings file. Used here: load_config(required=False) -> dict
# ({} when there is no settings file), get_setting(config, "a.b", default) -> one value, time_zone(config) -> IANA
# name, system_name(config) -> this machine's name, state_dir(config, sub) -> the state folder (created, mode 700).
import workhub_config  # noqa: E402 - the import path must be set first

# ---------------------------------------------------------------------------------------------------------------------
# Fixed settings
# ---------------------------------------------------------------------------------------------------------------------

# English month abbreviations, indexed 1-12, so the result never depends on the machine's language settings (locale).
MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# A title that already starts with the two tags, e.g. "[Nov-04 09:10][ws01]" (a role tag such as "[UC]" or "[ORCH]"
# may follow; the pattern only needs the first two).
# Regular expression, part by part: ^ start of the text; \[ a literal "["; [A-Z][a-z]{2} a month like "Nov";
# - a hyphen; \d{2} two digits (day); a space; \d{2}:\d{2} hour and minute; \] a literal "]"; \[[^\]]+\] a second
# bracketed tag with any characters except "]" inside.
TAG_PATTERN = re.compile(r"^\[[A-Z][a-z]{2}-\d{2} \d{2}:\d{2}\]\[[^\]]+\]")
# Role tag of every conversation this script names: "unitopic conversation". The orchestrator is renamed to "[ORCH]"
# with /rename, so it never keeps this tag.
ROLE_TAG = "[UC]"
# The topic is cut to this many characters (whole name about 55 characters: the tags take about 27, "[UC]" included).
TOPIC_MAX_CHARACTERS = 28
# The fallback topic (first words of the first prompt, used when Claude Code made no title) keeps at most this many words.
PROVISIONAL_TOPIC_WORDS = 5
# Only the end of a transcript is searched for the automatic title (Claude Code writes it again and again, so the
# latest copy is near the end); transcripts can be 100 MB or more, and this script must stay well under its timeout.
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024
# Sub-folder of the workhub-claude state folder for these state files. Shared with set_conversation_task.py and read
# by other workhub-claude skills (work-hub-cleanup, find-conversation), so the name must not change.
STATE_SUBFOLDER = "conversation_titles"
# Task ID prefix used when the setting notion.task_id_prefix is empty.
DEFAULT_TASK_ID_PREFIX = "TK"
# How prompts that the user did not type begin (point 6 of the header). A tuple (a fixed, unchangeable list) because
# `str.startswith` accepts a tuple and is then True when the text starts with ANY of its items. Prompts that start
# with "<ide_opened_file" or "<ide_selection" are the user's own messages with editor context in front, so they are
# NOT in this list.
NOT_USER_PROMPT_STARTS = ("<agent-message", "<cross-session-message", "<task-notification")


# ---------------------------------------------------------------------------------------------------------------------
# Helpers that read the settings
# ---------------------------------------------------------------------------------------------------------------------

def local_zone(config):
    """Return the user's time zone (setting owner.time_zone) as a zoneinfo object; UTC when unset or unknown.

    Example: {"owner": {"time_zone": "Europe/Lisbon"}} -> ZoneInfo("Europe/Lisbon").
    """
    zone_name = workhub_config.time_zone(config)
    try:
        # `zoneinfo.ZoneInfo(name)` loads the zone's rules (offsets, daylight saving) from the system's zone database.
        return zoneinfo.ZoneInfo(str(zone_name))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        # A misspelled zone name must not stop the naming; UTC is the neutral fallback.
        return datetime.timezone.utc


def machine_name(config):
    """Return the machine tag, e.g. "ws01": workhub_config.system_name (override variable, host name, aliases)."""
    return workhub_config.system_name(config)


def names_enabled(config):
    """Return True when conversations are to be named after their rows (setting workflow.conversation_names).

    `get_setting` returns False as False (only None, "", [] and {} count as unset), so the test `is False` switches
    the names off only for an explicit false; a missing key gives the default True.
    """
    names_on = workhub_config.get_setting(config, "workflow.conversation_names", default=True)
    return names_on is not False


def task_id_prefix(config):
    """Return the Task ID prefix (setting notion.task_id_prefix), e.g. "TK"; DEFAULT_TASK_ID_PREFIX when unset."""
    prefix = str(workhub_config.get_setting(config, "notion.task_id_prefix", default=DEFAULT_TASK_ID_PREFIX))
    return prefix.strip() or DEFAULT_TASK_ID_PREFIX


def task_id_pattern(config):
    """Return the compiled regular expression a Task ID must match: the prefix, "-", then 1 to 5 digits.

    Regular expression, part by part: ^ start; re.escape(prefix) the literal prefix (re.escape puts a backslash before
    any character that has a special meaning in a regular expression, so a prefix such as "A.B" matches only itself);
    - a hyphen; [0-9]{1,5} one to five digits; $ end (nothing may follow). Example with prefix "TK": "TK-42" matches,
    "TK42", "T-42", "TK-123456" and "TK-4a" do not.
    """
    return re.compile("^" + re.escape(task_id_prefix(config)) + r"-[0-9]{1,5}$")


def state_folder(config):
    """Return the folder of the state files (created with mode 700 by workhub_config.state_dir when missing)."""
    return workhub_config.state_dir(config, STATE_SUBFOLDER)


# ---------------------------------------------------------------------------------------------------------------------
# Helpers for names, transcripts and state files
# ---------------------------------------------------------------------------------------------------------------------

def start_stamp(moment, zone):
    """Format an aware datetime as "Nov-04 09:10" in the given time zone (day always two digits)."""
    local = moment.astimezone(zone)
    # `:02d` pads with a zero to two digits: 3 -> "03".
    return f"{MONTHS[local.month]}-{local.day:02d} {local.hour:02d}:{local.minute:02d}"


def transcript_start(transcript_path):
    """Return the first time stamp of the transcript as an aware datetime, or None.

    The transcript is a JSON-lines file; most records carry "timestamp": "2026-11-04T09:10:50.700Z" (UTC).
    Only the first 200 lines are read: the first record with a time stamp is near the top.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    # `errors="ignore"` skips bytes that are not valid UTF-8 instead of stopping with an error.
    with open(transcript_path, encoding="utf-8", errors="ignore") as handle:
        for line_number, line in enumerate(handle):
            if line_number >= 200:
                break
            try:
                record = json.loads(line)
            except ValueError:
                continue
            stamp = record.get("timestamp") if isinstance(record, dict) else None
            if isinstance(stamp, str):
                # `fromisoformat` does not accept the final "Z" (UTC) before Python 3.11, so it becomes "+00:00".
                return datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return None


def latest_record_value(transcript_path, record_type, field_name):
    """Return the `field_name` text of the latest record of type `record_type` near the end of the transcript, or None.

    Only the last TRANSCRIPT_TAIL_BYTES are read. Examples of records (one JSON object per transcript line):
      {"type": "ai-title", "aiTitle": "Timeline figure date range", ...}            -> Claude Code's automatic title
      {"type": "custom-title", "customTitle": "[Nov-04 09:10][ws01][UC] ...", ...}  -> the name set (hook or /rename)
    A cut first line (the tail starts in the middle of a line) fails to parse and is skipped.
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    size = os.path.getsize(transcript_path)
    # Binary mode ("rb") so that `seek` can jump to a byte position; the first, possibly cut, line is skipped below.
    with open(transcript_path, "rb") as handle:
        handle.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
        tail_text = handle.read().decode("utf-8", errors="ignore")
    # The quoted type, e.g. '"custom-title"', for a quick text test before any JSON parsing.
    quoted_type = '"' + record_type + '"'
    value = None
    for line in tail_text.splitlines():
        # A quick text test first, so that only the few matching lines are parsed as JSON.
        if quoted_type not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("type") == record_type and record.get(field_name):
            # Later lines overwrite earlier ones, so the latest value wins.
            value = str(record[field_name])
    return value


def latest_automatic_title(transcript_path):
    """Return Claude Code's latest automatic title ("ai-title" record), or None."""
    return latest_record_value(transcript_path, "ai-title", "aiTitle")


def latest_custom_title(transcript_path):
    """Return the name the conversation has now (latest "custom-title" record), or None when none is found.

    None means "unknown": no name was set yet, or the latest record is further back than TRANSCRIPT_TAIL_BYTES (in
    practice the latest record is very close to the end, but two records can be many megabytes apart).
    """
    return latest_record_value(transcript_path, "custom-title", "customTitle")


def prompt_is_from_user(prompt):
    """Return False for a prompt the user did not type (point 6 of the header), True otherwise.

    Example: '<agent-message from="ac59...">\\n[Subagent hand-back] ...' -> False; "Ok, do it" -> True.
    """
    # `.lstrip()` removes spaces and line breaks at the start, so "  <agent-message" is recognised too.
    prompt_start = str(prompt or "").lstrip()
    return not prompt_start.startswith(NOT_USER_PROMPT_STARTS)


def clean_topic(text, max_words=None):
    """Make a topic: plain ASCII letters, no brackets, single spaces, at most TOPIC_MAX_CHARACTERS (whole words).

    Example: "Müller [real] pair:  scoring" -> "Muller real pair: scoring".
    """
    # NFKD splits "ü" into "u" + a combining diaeresis; encoding to ASCII with errors="ignore" drops the diaeresis.
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", errors="ignore").decode("ascii")
    # Brackets are reserved for tags, and a name must not end with "]" (message addresses end in " [ref]").
    ascii_text = ascii_text.replace("[", " ").replace("]", " ")
    # `.split()` without an argument splits at any run of spaces, tabs or line breaks and drops empty pieces.
    words = ascii_text.split()
    if max_words is not None:
        words = words[:max_words]
    # Add whole words while they fit in TOPIC_MAX_CHARACTERS.
    topic = ""
    for word in words:
        candidate = word if not topic else topic + " " + word
        if len(candidate) > TOPIC_MAX_CHARACTERS:
            break
        topic = candidate
    # A single very long first word is cut instead of giving an empty topic.
    if not topic and words:
        topic = words[0][:TOPIC_MAX_CHARACTERS]
    # Strip punctuation at the end ("scoring:" -> "scoring").
    return topic.rstrip(" .,:;-")


def name_with_task(current_title, task_id, task_topic):
    """Return the current name with its topic replaced by "<task_id> <task_topic>", keeping the tags.

    Example: ("[Nov-03 11:49][ws01][UC] Planning chat", "TK-42", "Data cleaning")
             -> "[Nov-03 11:49][ws01][UC] TK-42 Data cleaning".
    A name without the tag form gets no tags (the caller then decides what to do).
    Used by this hook (point 5) and by set_conversation_task.py (its /rename line).
    """
    topic = clean_topic(f"{task_id} {task_topic}")
    tag_match = TAG_PATTERN.match(current_title)
    if not tag_match:
        return topic
    tags = tag_match.group(0)
    # The role tag follows the two tags directly, e.g. "[UC]" or "[ORCH]": keep it when present.
    # Regular expression: ^ start; \[ "["; [A-Z]+ one or more capital letters; \] "]".
    rest = current_title[len(tags):]
    role_match = re.match(r"^\[[A-Z]+\]", rest)
    if role_match:
        tags = tags + role_match.group(0)
    return f"{tags} {topic}"


def read_state(path):
    """Return the state dict of this conversation, or {} when there is none (or it is unreadable)."""
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path, state):
    """Write the state dict atomically (temporary file, then rename), so a killed hook never leaves half a file."""
    # mode=0o700: only the user can list the folder (the state files name the user's conversations).
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary_path = path + ".partial"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    # `os.replace` renames in one step: a reader sees either the old file or the new one, never half of it.
    os.replace(temporary_path, path)


def log_change(config, session_id, old_title, new_title, reason):
    """Append one line to changes.log (provenance: when, which conversation, old -> new, why, on which machine)."""
    now = datetime.datetime.now(datetime.timezone.utc).astimezone(local_zone(config)).isoformat(timespec="seconds")
    log_path = os.path.join(state_folder(config), "changes.log")
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": now, "session": session_id, "old": old_title, "new": new_title,
                                 "reason": reason, "machine": machine_name(config)}) + "\n")


def add_task_to_final_name(config, session_id, state_path, state, current_title):
    """Point 5 of the header: once a row is recorded, put "TK-n <topic>" into a FINAL name this script had set.

    Inputs: the settings, the state dict of a conversation whose name is final, and the name it has now ("" when
    unknown). Returns the new name to send to Claude Code, or None when nothing changes. Writes the state when it
    decides.
    """
    task_id = str(state.get("task") or "").strip()
    our_title = str(state.get("title") or "").strip()
    # No row recorded, or this row was already handled (added to the name, or a name by the user was found): stop.
    if not task_id or state.get("task_title_checked") == task_id:
        return None
    # The current name is unknown (no "custom-title" record near the end of the transcript): change nothing now and
    # look again at the next prompt, rather than risk replacing a name the user chose.
    if not current_title:
        return None
    # `our_title.split()` lists the words of the name; the Task ID is one whole word in it when the name already
    # holds the row (it was named with the row at the second prompt). A whole-word test, so "TK-4" is not found
    # inside "TK-42".
    already_named = task_id in our_title.split()
    # Only a name this script set, still shown unchanged and still in the tag form, may be changed.
    may_change = bool(our_title) and current_title == our_title and bool(TAG_PATTERN.match(our_title))
    if already_named or not may_change:
        # Remember the decision, so the transcript is not read again for this row.
        state["task_title_checked"] = task_id
        write_state(state_path, state)
        return None
    # Example: "[Nov-03 20:12][ws01][UC] Task cleanup skill" + TK-7 "Harness tools update"
    #       -> "[Nov-03 20:12][ws01][UC] TK-7 Harness tools update".
    new_title = name_with_task(our_title, task_id, str(state.get("task_topic") or ""))
    state["title"] = new_title
    state["task_title_checked"] = task_id
    write_state(state_path, state)
    log_change(config, session_id, current_title, new_title, "Work Hub task " + task_id + " recorded after naming")
    return new_title


# ---------------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------------

def main():
    """Decide whether to set a title, and print the hook answer when one is set."""
    # ---- 0. Is workhub-claude set up on this machine, and does the user want names? -------------------------------
    # required=False: a missing settings file gives {} instead of an error; then this machine is left alone.
    config = workhub_config.load_config(required=False)
    if not config:
        return
    # workflow.conversation_names false: no naming at all (no state file, no rename), before anything is read.
    if not names_enabled(config):
        return
    # The event from Claude Code (one JSON object on standard input).
    event = json.load(sys.stdin)
    session_id = str(event.get("session_id") or "")
    # The session ID becomes a file name, so it must not contain a path separator.
    if not session_id or "/" in session_id:
        return
    # Only the user's own prompts count (point 6 of the header): a subagent report, a message from another
    # conversation or a background-task notice changes nothing at all.
    if not prompt_is_from_user(event.get("prompt")):
        return
    transcript_path = event.get("transcript_path") or ""
    state_path = os.path.join(state_folder(config), session_id + ".json")
    state = read_state(state_path)

    # ---- 1. Is there anything left to do? -------------------------------------------------------------------------
    # The name is final (set by this script, or the user renamed): the usual case. Only point 5 can still apply, and
    # only while a recorded row is not yet handled; otherwise the transcript is not even opened.
    if state.get("final"):
        if state.get("task") and state.get("task_title_checked") != state.get("task"):
            # The name shown now (point 3): `or` takes the first non-empty value, so a non-empty session_title from
            # the hook input wins, else the transcript's latest "custom-title" record, else "" (unknown).
            current_title = str(event.get("session_title") or latest_custom_title(transcript_path) or "").strip()
            new_title = add_task_to_final_name(config, session_id, state_path, state, current_title)
            if new_title:
                answer = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": new_title}}
                print(json.dumps(answer))
        return
    # The name shown now (point 3), as above; "" when the conversation has no name yet.
    current_title = str(event.get("session_title") or latest_custom_title(transcript_path) or "").strip()
    automatic_title = latest_automatic_title(transcript_path)
    our_last_title = state.get("title")
    # The current title is ours, or Claude Code's automatic one, or none: the script may (re)name. Anything else is a
    # name the user (or another tool) chose: stop for good. A title that already has the tag form but was not set by
    # this script (e.g. typed by the user with /rename) also counts as theirs.
    title_is_ours_or_automatic = (not current_title or current_title == our_last_title
                                  or (automatic_title is not None and current_title == automatic_title))
    if not title_is_ours_or_automatic or (TAG_PATTERN.match(current_title) and current_title != our_last_title):
        state["final"] = True
        write_state(state_path, state)
        return

    # ---- 2. The tags (start time fixed at the first prompt) --------------------------------------------------------
    stamp = state.get("start")
    if not stamp:
        start_moment = transcript_start(transcript_path) or datetime.datetime.now(datetime.timezone.utc)
        stamp = start_stamp(start_moment, local_zone(config))
    # Example: "[Nov-04 09:10]" + "[ws01]" + "[UC]" -> "[Nov-04 09:10][ws01][UC]".
    tags = f"[{stamp}][{machine_name(config)}]{ROLE_TAG}"

    # ---- 3. The topic -----------------------------------------------------------------------------------------------
    if automatic_title:
        # Claude Code's automatic title exists (normally from the second prompt on; at once for an old conversation).
        topic = clean_topic(automatic_title)
        reason = "automatic title"
    elif "provisional" not in state:
        # First prompt of a new conversation: setting a title now would stop Claude Code from making its automatic
        # title from this prompt, so only remember the start time and the prompt's first words, and set nothing.
        # The state is updated, not replaced, so that "task"/"task_topic" fields already present are kept.
        provisional = clean_topic(str(event.get("prompt") or ""), max_words=PROVISIONAL_TOPIC_WORDS)
        state["start"] = stamp
        state["provisional"] = provisional
        write_state(state_path, state)
        return
    else:
        # Second prompt and still no automatic title: use the first prompt's first words.
        topic = state.get("provisional") or ""
        reason = "no automatic title; first words of the first prompt"
    # The Work Hub row, when the conversation recorded one (point 4 of the header): its Task ID goes first and its
    # short title replaces the automatic topic. clean_topic keeps whole words up to TOPIC_MAX_CHARACTERS, so the ID,
    # being the first word, is never cut off. Example: "TK-42" + "Data cleaning" -> "TK-42 Data cleaning".
    task_id = str(state.get("task") or "").strip()
    if task_id:
        task_topic = str(state.get("task_topic") or "").strip() or topic
        topic = clean_topic(f"{task_id} {task_topic}")
        reason = reason + "; Work Hub task " + task_id
    if not topic:
        topic = "Conversation"
    new_title = f"{tags} {topic}"

    # ---- 4. Answer Claude Code and remember ------------------------------------------------------------------------
    # The task fields stay in the state, so set_conversation_task.py can later see which row this conversation owns.
    final_state = {"title": new_title, "final": True, "start": stamp}
    for key in ("task", "task_topic"):
        if state.get(key):
            final_state[key] = state[key]
    write_state(state_path, final_state)
    if new_title == current_title:
        return
    log_change(config, session_id, current_title, new_title, reason)
    answer = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "sessionTitle": new_title}}
    print(json.dumps(answer))


# Run main() only when executed as a script (not when set_conversation_task.py imports this file); any error is
# swallowed so that the prompt always goes through, and the exit code is always 0.
if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - fail open on purpose (see SAFETY)
        pass
    sys.exit(0)
