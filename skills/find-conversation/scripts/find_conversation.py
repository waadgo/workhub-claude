#!/usr/bin/env python3
"""Find a past Claude Code or Codex conversation from a few words about what was done in it.

PURPOSE
    The user asks "in which conversation did we do X?". This script answers in a few seconds with
    the conversation's name, start date, last message, folder and session id, plus the sentence
    where the words were found, so they can go back to it (or resume it).

HOW IT STAYS FAST
    Reading gigabytes of transcripts for every question would take minutes. Instead the script keeps
    a small full-text index (an SQLite database with the FTS5 extension, the same idea as the
    index at the back of a book) in
        <paths.state_dir>/find-conversation/index.sqlite   (default ~/.local/state/workhub-claude/...)
    and, before each search, re-reads only the transcripts whose size or modification time changed
    since the last search. The first run builds the whole index (about a minute for a few thousand
    conversations); later runs take a few seconds.

WHAT IS INDEXED (per conversation)
    - title: the name shown in the window (the user's /rename, the naming hook's name, or Claude
      Code's automatic title; for Codex the thread name), plus the Work Hub Task ID and topic the
      conversation recorded in the conversation-title state (so "TK-42" finds the row's conversations);
    - requests: every message the user typed (not task notices, not messages from other sessions);
    - replies: the text of Claude's / Codex's answers;
    - commands: the shell commands and the descriptions of the tool calls (so a conversation can be
      found from a command that was run in it).
    Tool OUTPUTS (file contents, logs) are NOT indexed: they are most of the transcript bytes and
    mostly noise. Sub-agent transcripts (".../subagents/...") are skipped too.

INPUTS
    - The workhub-claude settings (~/.config/workhub-claude/config.json, or the file named by
      $WORKHUB_CLAUDE_CONFIG), read through workhub_config.py in this folder. Keys used:
      conversations.claude_projects_dir (default ~/.claude/projects), conversations.include_codex and
      conversations.codex_sessions_dir (default ~/.codex/sessions), paths.state_dir, owner.time_zone,
      system_name.*, workflow.conversation_names, machines.workstations[] and machines.clusters[].
    - Claude Code transcripts: <claude projects folder>/<folder>/<session id>.jsonl (JSON Lines: one
      JSON record per line). Records of type "user" and "assistant" carry the messages; records of
      type "custom-title" and "ai-title" carry the names.
    - Codex transcripts (only when conversations.include_codex is true): <codex home>/sessions/YYYY/MM/DD/
      rollout-*.jsonl and <codex home>/archived_sessions/*.jsonl; their names come from
      <codex home>/session_index.jsonl. <codex home> is the folder above codex_sessions_dir.
    - The conversation-title state (<paths.state_dir>/conversation_titles/<session id>.json, fields
      "task", "task_topic", "title"), written by the kit's hooks. Optional.
    - Optional: the same on every other site of the settings (each cluster, and each workstation with
      its own home folder and an ssh_alias) that already has an open shared SSH connection. The script
      sends ITSELF over that connection (`ssh host python3 - ...`), so nothing has to be installed
      there; the other site keeps its own index in its own home folder. A new login is never opened
      (BatchMode=yes: never a password or two-factor prompt).

OUTPUTS
    - Printed: the best matches, best first (or JSON with --json).
    - Written: only the index file above (re-creatable at any time with --rebuild; mode 600, folder
      mode 700, because it holds conversation text). Transcripts are only read, never changed.

DEPENDENCIES
    Python >= 3.8 standard library only (sqlite3 with FTS5, which the usual Linux Pythons have; the
    script stops with a clear message when FTS5 is missing).

EXAMPLES
    python3 find_conversation.py "survey table cleaning"
    python3 find_conversation.py "memory estimate" --after 2026-01-01 --top 5
    python3 find_conversation.py "endpoint setup" --sites local        # skip the other sites
    python3 find_conversation.py --rebuild "anything"                   # rebuild the index first

ASSUMPTIONS
    - Transcripts are only ever appended to (Claude Code and Codex never rewrite old lines), so an
      unchanged size + modification time means an unchanged conversation.
    - Machines that share one home folder share one index, which covers all of them. A closed
      conversation's machine is only known when its name carries the naming hook's machine tag (for
      example "[Jan-05 11:15][ws02][UC] ...", only when workflow.conversation_names is true);
      otherwise this machine's name is shown, which is fine for paths because the folders are shared.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports (all from the Python standard library, so the script also runs elsewhere as `ssh host python3 -`)
# ---------------------------------------------------------------------------------------------------------------------

# `argparse` builds the command-line interface and the --help text from the add_argument() calls in parse_arguments().
import argparse
# `concurrent.futures` runs the remote searches at the same time (threads), so 4 sites take as long as the slowest.
import concurrent.futures
# `datetime` turns stored POSIX times (seconds since 1970-01-01 UTC) into local dates for printing.
import datetime
# `glob` lists the transcript files with wildcard patterns such as "*/*.jsonl".
import glob
# `json` parses one transcript line (a JSON object) into a Python dictionary, and writes the --json output.
import json
# `os` gives file sizes, modification times, the home folder and environment variables.
import os
# `re` (regular expressions) splits the question into words and reads the machine tag out of a conversation name.
import re
# `shlex.quote` wraps one argument in shell quotes, so the remote shell passes it to Python unchanged.
import shlex
# `socket` gives this machine's host name, used on another site when no system name is passed.
import socket
# `sqlite3` is Python's interface to SQLite, a database kept in one ordinary file; its FTS5 extension does the search.
import sqlite3
# `subprocess` runs `ssh` to check for and use the open shared connections.
import subprocess
# `sys` gives the script's own path (to send it to the other sites), stderr for progress messages, and the exit status.
import sys
# `time` gives the current time, used to say how long indexing took.
import time

# ---------------------------------------------------------------------------------------------------------------------
# Constants (written in CAPITALS by Python convention: they are never changed while the script runs)
# ---------------------------------------------------------------------------------------------------------------------

# The skill's name: its folder under the state folder.
SKILL_NAME = "find-conversation"

# Version of the index layout. `PRAGMA user_version` stores it inside the database; when it differs (an older script
# made the index), the index is deleted and rebuilt, so a layout change can never mix two formats.
INDEX_LAYOUT_VERSION = 1

# Defaults used on ANOTHER site, where no settings file is read (the local side always passes explicit values; these
# only apply when an option is missing). They are the kit's default folders, relative to that site's home folder.
REMOTE_DEFAULT_INDEX_PATH = "~/.local/state/workhub-claude/find-conversation/index.sqlite"
REMOTE_DEFAULT_CLAUDE_ROOT = "~/.claude/projects"
REMOTE_DEFAULT_TITLES_DIR = "~/.local/state/workhub-claude/conversation_titles"
# The defaults in the settings themselves (conversations.*).
DEFAULT_CLAUDE_PROJECTS_SETTING = "~/.claude/projects"
DEFAULT_CODEX_SESSIONS_SETTING = "~/.codex/sessions"

# How much text is kept per message and per conversation. Very long replies (reports pasted into chat) would make the
# index large and slow without helping the search, because the words that identify a conversation appear early.
# `400_000`: underscores inside a number literal only group the digits for reading (Python >= 3.6).
MAX_CHARACTERS_PER_MESSAGE = 4000
MAX_CHARACTERS_PER_COMMAND = 400
MAX_CHARACTERS_PER_FIELD = 400_000

# Column weights for the ranking (BM25, explained in search_index()). A word in the conversation's NAME counts 6 times
# as much as the same word in a reply; a word the user TYPED counts 3 times as much: their own words describe the
# topic best. The order matches the columns of the full-text table: title, requests, replies, commands.
COLUMN_WEIGHTS = (6.0, 3.0, 1.0, 1.0)

# Words that carry no topic and only slow the search down or blur the ranking ("the conversation where we did X").
# A `frozenset` is a set that cannot be changed later; `word in STOP_WORDS` is then a fast membership test.
STOP_WORDS = frozenset("""
a an and are as at be but by can conversation conversations could did do does done for from had has have i in into is
it its me my of on or our remember session that the their them then there this to us was we were what when where which
who why will with you your about chat claude codex
""".split())

# Transcript lines that start with these texts are NOT typed by the user (task notices, messages from other sessions,
# summaries written after a compaction), so they are not indexed as their requests.
NOT_TYPED_BY_USER = ("<task-notification", "<cross-session-message", "<agent-message", "<local-command",
                     "<command-", "This session is being continued", "Caveat:")

# Pattern of a name made by the naming hook: "[Jan-05 11:15][ws02][UC] Topic".
#   ^              the name must START with this pattern;
#   \[ ... \]      literal square brackets (a backslash removes their special regex meaning);
#   [^\]]+         one or more characters that are not "]" (the date and time, e.g. "Jan-05 11:15");
#   ([a-z0-9_-]+)  group 1: the machine tag, lower-case letters, digits, "_" and "-" (e.g. "ws02").
HOOK_NAME_PATTERN = re.compile(r"^\[[^\]]+\]\[([a-z0-9_-]+)\]")
# Pattern of one search word: a run of letters or digits ("int16", "survey"). `\w` would also accept "_", which
# FTS5 treats as a separator anyway, so it is left out. Example: "model v6_light, fold-2" -> model v6 light fold 2.
WORD_PATTERN = re.compile(r"[A-Za-z0-9]+")


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------------

def expand_user_path(path_text):
    """Expand ~ and $VARIABLES in a path on the machine that runs this code ("" stays "").

    Paths sent to another site are written from the home folder ("~/.claude/projects") and quoted for the shell, so
    the shell does not expand them: this function does, there, with that site's home folder.
    """
    if not path_text:
        return ""
    return os.path.expandvars(os.path.expanduser(str(path_text)))


def portable_home_path(setting_value, default_form):
    """A path that means the same folder on another site.

    The setting itself when it starts at the home folder (~ or $HOME): the other site expands it to ITS home.
    Otherwise `default_form`, because an absolute path of this machine may not exist elsewhere.
    Example: "~/.claude/projects" -> "~/.claude/projects"; "/fast/disk/claude/projects" -> default_form.
    """
    text = str(setting_value or "").strip()
    if text.startswith(("~", "$HOME", "${HOME}")):
        return text
    return default_form


def codex_home_from_setting(sessions_setting):
    """Codex's home folder from conversations.codex_sessions_dir: "~/.codex/sessions" -> "~/.codex".

    The script needs the home folder because it also reads archived_sessions/ and session_index.jsonl next to
    sessions/. A setting whose last part is not "sessions" is taken as the home folder itself.
    """
    trimmed = str(sessions_setting or "").rstrip("/")
    if os.path.basename(trimmed) == "sessions":
        return os.path.dirname(trimmed)
    return trimmed


def load_zone(zone_name):
    """(tzinfo, label) for an IANA time zone name; UTC with a label saying so when it cannot be loaded.

    `zoneinfo` (Python >= 3.9) knows the daylight-saving rules. Without it (or without the zone data) the script
    falls back to UTC and says so, rather than printing a wrong local time.
    """
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(zone_name), zone_name
    except Exception:  # noqa: BLE001 - any failure (missing module or zone data) means "use UTC"
        return datetime.timezone.utc, "UTC (time zone %s not available)" % zone_name


def to_epoch_seconds(timestamp_text):
    """Turn an ISO-8601 time stamp such as "2026-01-09T15:20:13.981Z" into POSIX seconds (float), or None.

    Transcripts write UTC times ending in "Z". `datetime.fromisoformat` only accepts "+00:00" before Python 3.11, so
    the "Z" is replaced first. Anything unreadable gives None instead of an error: one bad line must not stop indexing.
    """
    if not isinstance(timestamp_text, str) or not timestamp_text:
        return None
    iso_text = timestamp_text.replace("Z", "+00:00")
    try:
        moment = datetime.datetime.fromisoformat(iso_text)
    except ValueError:
        return None
    # A time stamp without a zone is taken as UTC (both tools write UTC).
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def local_time_text(epoch_seconds, zone):
    """Format POSIX seconds in `zone`, e.g. "Fri Jan 09 2026 10:20". Returns "?" for None."""
    if epoch_seconds is None:
        return "?"
    moment = datetime.datetime.fromtimestamp(epoch_seconds, tz=zone)
    # strftime codes: %a weekday, %b month abbreviation, %d day, %Y year, %H:%M 24-hour clock.
    return moment.strftime("%a %b %d %Y %H:%M")


def shorten(text, limit):
    """Collapse all white space (new lines, tabs) to single spaces and cut the text to `limit` characters."""
    # `" ".join(text.split())` splits on any run of white space and glues the pieces back with one space.
    one_line_text = " ".join(str(text).split())
    if len(one_line_text) <= limit:
        return one_line_text
    return one_line_text[:limit] + " …"


def join_limited(pieces, limit):
    """Join text pieces with new lines, stopping before the total passes `limit` characters (keeps the start)."""
    kept_pieces = []
    total_characters = 0
    for piece in pieces:
        if total_characters + len(piece) > limit:
            break
        kept_pieces.append(piece)
        total_characters += len(piece) + 1
    return "\n".join(kept_pieces)


def parse_json_line(raw_line):
    """Parse one transcript line (bytes) into a dictionary, or None if it is not valid JSON or not an object."""
    try:
        record = json.loads(raw_line)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    return record


def read_title_state(titles_dir, session_id):
    """The conversation-title state of one conversation: {"task": ..., "task_topic": ..., "title": ...} or {}.

    The kit's hooks keep one small JSON file per conversation, <titles_dir>/<session id>.json, with the name they gave
    it and the Work Hub row it recorded ("task": "TK-42", "task_topic": "Dataset cleaning"). A missing or damaged
    file gives {} so that one bad file never stops indexing.
    """
    if not titles_dir or not session_id:
        return {}
    state_path = os.path.join(titles_dir, session_id + ".json")
    if not os.path.isfile(state_path):
        return {}
    try:
        with open(state_path, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


# ---------------------------------------------------------------------------------------------------------------------
# Reading one Claude Code transcript
# ---------------------------------------------------------------------------------------------------------------------

def claude_message_texts(record):
    """Return the plain-text parts of a Claude Code "user" or "assistant" record as a list of strings.

    A record's "message" holds "content", which is either one string or a list of blocks such as
    {"type": "text", "text": "..."}, {"type": "tool_use", ...} or {"type": "tool_result", ...}.
    Only the "text" blocks are returned; editor context ("<ide_selection>...") and system reminders are dropped.
    """
    message = record.get("message") or {}
    content = message.get("content")
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text") or "")
    kept_texts = []
    for text in texts:
        stripped_text = text.lstrip()
        if not stripped_text:
            continue
        if stripped_text.startswith(("<ide_", "<system-reminder>")):
            continue
        kept_texts.append(text)
    return kept_texts


def claude_tool_commands(record):
    """Return short texts describing the tool calls of an assistant record: "Bash: <command>", "Write: <path>", ..."""
    message = record.get("message") or {}
    content = message.get("content")
    commands = []
    if not isinstance(content, list):
        return commands
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_name = block.get("name") or "?"
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            continue
        # The fields that say what a call did. A Bash call has both "description" and "command": both are kept.
        described_parts = []
        for key in ("description", "command", "file_path", "path", "pattern", "url", "query", "skill", "prompt"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                described_parts.append(value)
        if described_parts:
            commands.append(shorten(tool_name + ": " + " | ".join(described_parts), MAX_CHARACTERS_PER_COMMAND))
    return commands


def read_claude_transcript(path):
    """Read one Claude Code transcript and return what the index needs.

    Parameters
        path: str, absolute path of a "<session id>.jsonl" file.
    Returns
        dict with keys source ("claude"), session_id, title, started, last_message (POSIX seconds or None), cwd,
        requests (list of str), replies (list of str), commands (list of str).
    Speed
        Most bytes are tool outputs. A line is parsed as JSON only when a cheap byte search shows it can matter
        (a user prompt, an assistant message, or a title record); tool-result lines are skipped unparsed.
    """
    # The file name without ".jsonl" is the session id (the id `claude --resume` takes).
    session_id = os.path.basename(path)[:-len(".jsonl")]
    conversation = {"source": "claude", "session_id": session_id, "title": "", "ai_title": "", "custom_title": "",
                    "started": None, "last_message": None, "cwd": "", "requests": [], "replies": [], "commands": []}
    # `open(path, "rb")` opens the file for reading as bytes (no text decoding), so the byte searches below are cheap;
    # `with` closes the file automatically at the end of the block, also after an error.
    with open(path, "rb") as transcript_file:
        # Iterating over a file object yields one line (bytes, ending in b"\n") at a time, never the whole file.
        for raw_line in transcript_file:
            # Title records: the user's or the hook's name ("custom-title") and Claude Code's own ("ai-title").
            # The last one in the file is the current one, so each new one overwrites the previous.
            if b'"type":"custom-title"' in raw_line or b'"type":"ai-title"' in raw_line:
                record = parse_json_line(raw_line)
                if record is None:
                    continue
                if record.get("type") == "custom-title" and record.get("customTitle"):
                    conversation["custom_title"] = record["customTitle"]
                if record.get("type") == "ai-title" and record.get("aiTitle"):
                    conversation["ai_title"] = record["aiTitle"]
                continue
            is_user_line = b'"type":"user"' in raw_line
            is_assistant_line = b'"type":"assistant"' in raw_line
            if not is_user_line and not is_assistant_line:
                continue
            # Sub-agent turns (sidechains) are not the conversation itself; tool results are outputs, not words.
            if b'"isSidechain":true' in raw_line:
                continue
            if is_user_line and b'"tool_use_id"' in raw_line:
                continue
            record = parse_json_line(raw_line)
            if record is None:
                continue
            # Start, last message and folder come from the messages themselves.
            moment = to_epoch_seconds(record.get("timestamp"))
            if moment is not None:
                if conversation["started"] is None:
                    conversation["started"] = moment
                conversation["last_message"] = moment
            if record.get("cwd"):
                conversation["cwd"] = record["cwd"]
            if record.get("type") == "user":
                # `isMeta` marks lines Claude Code adds itself (skill text, command output), not typed by the user.
                if record.get("isMeta"):
                    continue
                for text in claude_message_texts(record):
                    if text.lstrip().startswith(NOT_TYPED_BY_USER):
                        continue
                    conversation["requests"].append(shorten(text, MAX_CHARACTERS_PER_MESSAGE))
            else:
                for text in claude_message_texts(record):
                    conversation["replies"].append(shorten(text, MAX_CHARACTERS_PER_MESSAGE))
                conversation["commands"].extend(claude_tool_commands(record))
    # The name shown in the window: a /rename or the hook's name wins over Claude Code's automatic title.
    conversation["title"] = conversation["custom_title"] or conversation["ai_title"]
    return conversation


# ---------------------------------------------------------------------------------------------------------------------
# Reading one Codex transcript
# ---------------------------------------------------------------------------------------------------------------------

def read_codex_transcript(path):
    """Read one Codex "rollout-*.jsonl" transcript and return the same dictionary as read_claude_transcript().

    Codex records look like {"timestamp": ..., "type": ..., "payload": {...}}:
      - type "session_meta": payload.id (the thread id), payload.cwd, payload.timestamp;
      - type "event_msg" with payload.type "user_message" (the user's request, payload.message) or "agent_message"
        (Codex's reply, payload.message);
      - type "response_item" with payload.type "function_call" (payload.arguments, a JSON text holding the command)
        or "custom_tool_call" (payload.input, e.g. a patch).
    The name is not in the file (it is in session_index.jsonl), so "title" stays empty here and is added at search
    time by codex_thread_names().
    """
    conversation = {"source": "codex", "session_id": "", "title": "", "started": None, "last_message": None,
                    "cwd": "", "requests": [], "replies": [], "commands": []}
    interesting_markers = (b'"session_meta"', b'"user_message"', b'"agent_message"', b'"function_call"',
                           b'"custom_tool_call"')
    with open(path, "rb") as transcript_file:
        for raw_line in transcript_file:
            # `any(...)` is True when at least one marker occurs in the line; other lines are skipped unparsed.
            if not any(marker in raw_line for marker in interesting_markers):
                continue
            record = parse_json_line(raw_line)
            if record is None:
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            moment = to_epoch_seconds(record.get("timestamp"))
            if moment is not None:
                if conversation["started"] is None:
                    conversation["started"] = moment
                conversation["last_message"] = moment
            record_type = record.get("type")
            payload_type = payload.get("type")
            if record_type == "session_meta":
                conversation["session_id"] = payload.get("id") or conversation["session_id"]
                conversation["cwd"] = payload.get("cwd") or conversation["cwd"]
            elif record_type == "event_msg" and payload_type == "user_message":
                text = payload.get("message") or ""
                if text.strip() and not text.lstrip().startswith(NOT_TYPED_BY_USER):
                    conversation["requests"].append(shorten(text, MAX_CHARACTERS_PER_MESSAGE))
            elif record_type == "event_msg" and payload_type == "agent_message":
                text = payload.get("message") or ""
                if text.strip():
                    conversation["replies"].append(shorten(text, MAX_CHARACTERS_PER_MESSAGE))
            elif record_type == "response_item" and payload_type == "function_call":
                arguments_text = payload.get("arguments") or ""
                conversation["commands"].append(shorten((payload.get("name") or "?") + ": " + arguments_text,
                                                        MAX_CHARACTERS_PER_COMMAND))
            elif record_type == "response_item" and payload_type == "custom_tool_call":
                input_text = payload.get("input") or ""
                conversation["commands"].append(shorten((payload.get("name") or "?") + ": " + str(input_text),
                                                        MAX_CHARACTERS_PER_COMMAND))
    # Without a session_meta line, the id is the end of the file name: "rollout-<date>T<time>-<id>.jsonl".
    if not conversation["session_id"]:
        file_stem = os.path.basename(path)[:-len(".jsonl")]
        conversation["session_id"] = file_stem[-36:]
    return conversation


def codex_thread_names(codex_root):
    """Return {thread id: thread name} from <codex home>/session_index.jsonl (the last name of each id wins).

    An empty `codex_root` (Codex not included) gives {}.
    """
    names = {}
    if not codex_root:
        return names
    index_path = os.path.join(codex_root, "session_index.jsonl")
    if not os.path.isfile(index_path):
        return names
    with open(index_path, "rb") as index_file:
        for raw_line in index_file:
            record = parse_json_line(raw_line)
            if record and record.get("id") and record.get("thread_name"):
                names[record["id"]] = record["thread_name"]
    return names


# ---------------------------------------------------------------------------------------------------------------------
# Finding the transcript files
# ---------------------------------------------------------------------------------------------------------------------

def list_transcripts(claude_root, codex_root):
    """Return a list of (source, path) for every main transcript under the two roots.

    Claude Code: <claude_root>/<folder>/<session id>.jsonl (one folder level; sub-agent transcripts live deeper, in
    ".../<session id>/subagents/", and are therefore not matched by "*/*.jsonl").
    Codex (only when `codex_root` is not ""): <codex_root>/sessions/**/rollout-*.jsonl and
    <codex_root>/archived_sessions/**/*.jsonl.
    """
    transcripts = []
    if claude_root:
        for path in sorted(glob.glob(os.path.join(claude_root, "*", "*.jsonl"))):
            transcripts.append(("claude", path))
    if not codex_root:
        return transcripts
    # `recursive=True` lets "**" match any number of folder levels (YYYY/MM/DD).
    codex_patterns = (os.path.join(codex_root, "sessions", "**", "rollout-*.jsonl"),
                      os.path.join(codex_root, "archived_sessions", "**", "*.jsonl"))
    for pattern in codex_patterns:
        for path in sorted(glob.glob(pattern, recursive=True)):
            transcripts.append(("codex", path))
    return transcripts


# ---------------------------------------------------------------------------------------------------------------------
# The index (SQLite + FTS5)
# ---------------------------------------------------------------------------------------------------------------------

def open_index(index_path, rebuild):
    """Open (and create if needed) the index database and return the connection.

    Tables
        conversations: one row per transcript: path, size, mtime (to detect changes), source, session_id, title,
            task (the Work Hub row the conversation recorded, "" if none), started, last_message, cwd, requests
            (count), first_request.
        conversation_text: the FTS5 full-text table with columns title, requests, replies, commands. Its `rowid`
            (SQLite's built-in row number) equals the conversations row's id, which joins the two.
    FTS5 tokenizer "porter unicode61": text is cut into words at anything that is not a letter or digit, lower-cased,
    accents removed (unicode61), and reduced to a stem by the Porter algorithm, so "renamed", "renaming" and "rename"
    match each other.
    Raises
        SystemExit with a clear message when this Python's SQLite has no FTS5.
    """
    # The folder part of the path ("" for a bare file name, which means the current folder: nothing to create).
    index_folder = os.path.dirname(index_path)
    # `exist_ok=True`: no error when the folder exists. Mode 0o700 = only the owner can read the index (chat text).
    if index_folder:
        os.makedirs(index_folder, mode=0o700, exist_ok=True)
    if rebuild and os.path.exists(index_path):
        os.remove(index_path)
    # `timeout=60`: when two conversations search at the same moment, the second waits up to 60 s for the first's
    # write lock instead of failing at once.
    connection = sqlite3.connect(index_path, timeout=60)
    # `PRAGMA user_version` is an integer SQLite keeps in the file header for the application's own use.
    stored_version = connection.execute("PRAGMA user_version").fetchone()[0]
    has_tables = connection.execute(
        "SELECT count(*) FROM sqlite_master WHERE name = 'conversations'").fetchone()[0] > 0
    if has_tables and stored_version != INDEX_LAYOUT_VERSION:
        # An index from another layout version: start again from an empty file.
        connection.close()
        os.remove(index_path)
        connection = sqlite3.connect(index_path, timeout=60)
    # The index file holds conversation text: keep it private (owner read/write only).
    os.chmod(index_path, 0o600)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY, path TEXT UNIQUE, size INTEGER, mtime REAL, source TEXT, session_id TEXT,
            title TEXT, task TEXT, started REAL, last_message REAL, cwd TEXT, requests INTEGER, first_request TEXT)""")
    try:
        connection.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS conversation_text
            USING fts5(title, requests, replies, commands, tokenize = 'porter unicode61')""")
    except sqlite3.OperationalError as error:
        raise SystemExit("This Python's SQLite has no FTS5 full-text search (%s). Run the script with another "
                         "python3 (the settings' runtime.python) or install a Python whose sqlite3 has FTS5." % error)
    connection.execute("PRAGMA user_version = %d" % INDEX_LAYOUT_VERSION)
    connection.commit()
    return connection


def update_index(connection, claude_root, codex_root, titles_dir, show_progress):
    """Bring the index up to date: (re)read changed or new transcripts, forget deleted ones.

    titles_dir: the conversation-title state folder ("" = none). Its "task" and "task_topic" are added to the
    indexed title, so a search for "TK-42" or the row's topic finds the conversations that worked on the row.
    Returns
        (number of transcripts re-read, seconds taken).
    """
    start_time = time.time()
    # What the index already knows: {path: (id, size, mtime)}.
    known_files = {}
    for row_id, path, size, mtime in connection.execute("SELECT id, path, size, mtime FROM conversations"):
        known_files[path] = (row_id, size, mtime)
    transcripts = list_transcripts(claude_root, codex_root)
    present_paths = set()
    reread_count = 0
    for source, path in transcripts:
        present_paths.add(path)
        try:
            file_status = os.stat(path)
        except OSError:
            continue
        known = known_files.get(path)
        # Unchanged size and modification time: the transcript has not grown since it was indexed.
        if known is not None and known[1] == file_status.st_size and known[2] == file_status.st_mtime:
            continue
        try:
            if source == "claude":
                conversation = read_claude_transcript(path)
            else:
                conversation = read_codex_transcript(path)
        except OSError:
            continue
        # The Work Hub row and the hook's name, from the conversation-title state (Claude Code conversations only).
        title_state = read_title_state(titles_dir, conversation["session_id"]) if source == "claude" else {}
        task_id = str(title_state.get("task") or "")
        task_topic = str(title_state.get("task_topic") or "")
        if not conversation["title"]:
            conversation["title"] = str(title_state.get("title") or "")
        # The text indexed as the title: the name plus the row's ID and topic (empty parts left out).
        title_parts = [part for part in (conversation["title"], task_id, task_topic) if part]
        indexed_title = " · ".join(title_parts)
        # A tab opened but never used has no message; there is nothing to find in it.
        conversation_has_text = bool(conversation["requests"] or conversation["replies"])
        if known is not None:
            connection.execute("DELETE FROM conversation_text WHERE rowid = ?", (known[0],))
            connection.execute("DELETE FROM conversations WHERE id = ?", (known[0],))
        first_request = conversation["requests"][0] if conversation["requests"] else ""
        # `?` placeholders let sqlite3 insert the values safely (no quoting problems with text that contains quotes).
        cursor = connection.execute(
            "INSERT INTO conversations (path, size, mtime, source, session_id, title, task, started, last_message, "
            "cwd, requests, first_request) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (path, file_status.st_size, file_status.st_mtime, source, conversation["session_id"],
             conversation["title"], task_id, conversation["started"], conversation["last_message"],
             conversation["cwd"], len(conversation["requests"]), shorten(first_request, 300)))
        # `cursor.lastrowid` is the id SQLite gave the new row; the text row gets the same number.
        new_row_id = cursor.lastrowid
        if conversation_has_text:
            connection.execute(
                "INSERT INTO conversation_text (rowid, title, requests, replies, commands) VALUES (?, ?, ?, ?, ?)",
                (new_row_id, indexed_title,
                 join_limited(conversation["requests"], MAX_CHARACTERS_PER_FIELD),
                 join_limited(conversation["replies"], MAX_CHARACTERS_PER_FIELD),
                 join_limited(conversation["commands"], MAX_CHARACTERS_PER_FIELD)))
        reread_count += 1
        # Commit every 25 files, so a long first build that is interrupted keeps what it already did.
        if reread_count % 25 == 0:
            connection.commit()
            if show_progress:
                print("  indexed %d transcripts so far ..." % reread_count, file=sys.stderr)
    # Transcripts deleted since the last run (or no longer included, such as Codex turned off): remove their rows.
    for path, (row_id, _size, _mtime) in known_files.items():
        if path not in present_paths:
            connection.execute("DELETE FROM conversation_text WHERE rowid = ?", (row_id,))
            connection.execute("DELETE FROM conversations WHERE id = ?", (row_id,))
    connection.commit()
    return reread_count, time.time() - start_time


# ---------------------------------------------------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------------------------------------------------

def query_words(question):
    """Split the question into search words, dropping filler words; returns a list of lower-case words.

    Example: "the conversation where we fixed the plotting slice bug" -> ["fixed", "plotting", "slice", "bug"].
    One-character words are dropped too: "0.3 mm" would otherwise search "0" and "3", which match almost every
    conversation. Write such numbers as one word when they matter ("0p3mm") or rely on the other words.
    """
    words = []
    for word in WORD_PATTERN.findall(question.lower()):
        if word in STOP_WORDS or word in words or len(word) < 2:
            continue
        words.append(word)
    return words


def search_index(connection, words, after_epoch, before_epoch, top_count, excluded_session_id=""):
    """Return the best-matching conversations as a list of dictionaries, best first.

    Ranking, in two steps:
      1. FTS5 finds every conversation containing at least ONE of the words (an OR query). Each word is written as
         "word"* : the quotes make it a literal word, the * a prefix, so "grid" also finds "grids" and "gridded"
         (words of 1–2 characters are matched exactly, see below).
         FTS5's bm25() score is computed with COLUMN_WEIGHTS. BM25 is the standard search-engine score: a rare word
         counts more than a common one, a word repeated in a short text counts more than in a long one. SQLite returns
         it as a NEGATIVE number (more negative = better), so the order is ascending.
      2. For the best 300 of those, the script counts how many of the DIFFERENT words each one contains (one small
         query per word) and sorts by that count first, then by bm25. A conversation that has all 4 words beats one
         that has 1 word 50 times.
    Date limits use the conversation's last message (after) and its start (before).
    excluded_session_id: a session id to leave out (the conversation that runs the search), or "".
    """
    if not words:
        return []
    # Two-character words ("mm", "v6", "gb") are matched exactly: as prefixes they would match far too much.
    quoted_words = []
    for word in words:
        if len(word) <= 2:
            quoted_words.append('"%s"' % word)
        else:
            quoted_words.append('"%s"*' % word)
    or_query = " OR ".join(quoted_words)
    # The bm25 weights are inserted as numbers (they are constants, not user text).
    weights_text = ", ".join("%.1f" % weight for weight in COLUMN_WEIGHTS)
    sql = ("SELECT c.id, c.path, c.source, c.session_id, c.title, c.task, c.started, c.last_message, c.cwd, "
           "c.requests, c.first_request, bm25(conversation_text, %s) AS score, "
           "snippet(conversation_text, -1, '»', '«', ' … ', 18) AS excerpt "
           "FROM conversation_text JOIN conversations c ON c.id = conversation_text.rowid "
           "WHERE conversation_text MATCH ? " % weights_text)
    parameters = [or_query]
    if after_epoch is not None:
        sql += "AND c.last_message >= ? "
        parameters.append(after_epoch)
    if before_epoch is not None:
        sql += "AND c.started < ? "
        parameters.append(before_epoch)
    if excluded_session_id:
        sql += "AND c.session_id != ? "
        parameters.append(excluded_session_id)
    sql += "ORDER BY score LIMIT 300"
    column_names = ("id", "path", "source", "session_id", "title", "task", "started", "last_message", "cwd",
                    "requests", "first_request", "score", "excerpt")
    candidates = []
    for row in connection.execute(sql, parameters):
        # `zip` pairs each column name with its value; `dict(...)` turns the pairs into a dictionary.
        candidates.append(dict(zip(column_names, row)))
    if not candidates:
        return []
    # Step 2: which candidates contain each word. `IN (...)` with one `?` per candidate id.
    candidate_ids = [candidate["id"] for candidate in candidates]
    placeholders = ", ".join("?" for _ in candidate_ids)
    words_found = {candidate_id: [] for candidate_id in candidate_ids}
    for word, quoted_word in zip(words, quoted_words):
        word_sql = ("SELECT rowid FROM conversation_text WHERE conversation_text MATCH ? AND rowid IN (%s)"
                    % placeholders)
        for (row_id,) in connection.execute(word_sql, [quoted_word] + candidate_ids):
            words_found[row_id].append(word)
    for candidate in candidates:
        candidate["words_found"] = words_found[candidate["id"]]
    # Sort key: more different words first (minus sign = descending), then the better (more negative) bm25 score.
    candidates.sort(key=lambda candidate: (-len(candidate["words_found"]), candidate["score"]))
    return candidates[:top_count]


def describe_hits(hits, words, system_name, codex_root, zone, use_name_tags):
    """Add the display fields (name, machine, dates as text) to each hit and return plain JSON-ready dictionaries.

    use_name_tags: True when the naming hook writes "[date][machine][role]" names (workflow.conversation_names);
    then a closed conversation's machine is read from its name, else `system_name` is shown.
    """
    thread_names = codex_thread_names(codex_root)
    described = []
    for hit in hits:
        title = hit["title"] or ""
        if hit["source"] == "codex":
            title = thread_names.get(hit["session_id"], title)
        if not title:
            title = "(no name yet) " + shorten(hit["first_request"], 60)
        # The machine: from the hook's name tag when names carry one, else this machine (the home folder is shared).
        name_match = HOOK_NAME_PATTERN.match(title) if use_name_tags else None
        machine = name_match.group(1) if name_match else system_name
        described.append({
            "rank": len(described) + 1, "name": title, "source": hit["source"], "system": machine,
            "task": hit["task"] or "", "session_id": hit["session_id"],
            "started": hit["started"], "last_message": hit["last_message"],
            "started_text": local_time_text(hit["started"], zone),
            "last_message_text": local_time_text(hit["last_message"], zone),
            "folder": hit["cwd"], "requests": hit["requests"], "first_request": hit["first_request"],
            "words_found": hit["words_found"], "words_asked": len(words), "excerpt": shorten(hit["excerpt"], 400),
            "transcript": hit["path"]})
    return described


def search_this_site(arguments):
    """Update this site's index and search it. Returns (hits, note) where note says what was indexed."""
    connection = open_index(arguments.index_path, arguments.rebuild)
    note = ""
    if not arguments.no_update:
        reread_count, seconds = update_index(connection, arguments.claude_root, arguments.codex_root,
                                             arguments.titles_dir, show_progress=not arguments.json)
        note = "re-read %d changed transcripts in %.1f s" % (reread_count, seconds)
    total = connection.execute("SELECT count(*) FROM conversations").fetchone()[0]
    note = "%d conversations indexed; %s" % (total, note) if note else "%d conversations indexed" % total
    words = query_words(" ".join(arguments.question))
    # Claude Code sets CLAUDE_CODE_SESSION_ID in the shells it starts. The conversation running this search always
    # contains the question's words (in its own commands), so it is left out unless --include-current is given.
    excluded_session_id = "" if arguments.include_current else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    zone, _zone_label = load_zone(arguments.time_zone)
    hits = search_index(connection, words, parse_date(arguments.after, zone), parse_date(arguments.before, zone),
                        arguments.top, excluded_session_id)
    connection.close()
    return describe_hits(hits, words, arguments.system_name, arguments.codex_root, zone, arguments.name_tags), note


# ---------------------------------------------------------------------------------------------------------------------
# Other sites (through already-open shared SSH connections only)
# ---------------------------------------------------------------------------------------------------------------------

def remote_sites_from_settings(wc, config, here):
    """The other sites this machine can search: [(name, ssh target)], in the order of the settings.

    A site is a machine with its own home folder (shares_home_with_this_machine false): every cluster, and every
    workstation that has an ssh_alias. The machine running the search (`here`) and machines that share its home
    folder are left out (their conversations are already in the local index).
    """
    sites = []
    listed = [("workstation", m) for m in wc.workstations(config)] + [("cluster", c) for c in wc.clusters(config)]
    for kind, machine in listed:
        name = str(machine.get("name") or "").strip().lower()
        alias = str(machine.get("ssh_alias") or "").strip()
        if not name or name == here or machine.get("shares_home_with_this_machine"):
            continue
        if kind == "workstation" and not alias:
            continue
        sites.append((name, alias or name))
    return sites


def open_sites(requested_sites, remote_sites):
    """Return (open, closed) lists of (name, ssh target) among the requested other sites.

    requested_sites: "all", "local", or a comma list of site names ("local,mycluster").
    `ssh -O check <target>` asks the ssh "master" process of an existing shared connection (ControlMaster) whether
    it is alive. It never opens a new login, so it can never trigger a password or two-factor prompt. Exit status
    0 = open. A check that hangs counts as closed after 10 s.
    """
    if requested_sites == "local":
        return [], []
    if requested_sites == "all":
        candidates = list(remote_sites)
    else:
        wanted = [site.strip().lower() for site in requested_sites.split(",") if site.strip()]
        candidates = [site for site in remote_sites if site[0] in wanted]
    open_ones = []
    closed_ones = []
    for name, target in candidates:
        try:
            result = subprocess.run(["ssh", "-O", "check", target], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=10)
            is_open = result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            is_open = False
        if is_open:
            open_ones.append((name, target))
        else:
            closed_ones.append((name, target))
    return open_ones, closed_ones


def remote_command_words(site_name, arguments, portable_paths):
    """The remote command line, one shell-quoted word per item: python3 - --remote-run ... -- <question words>.

    ssh joins the remote arguments into one shell command line, so every item goes through `shlex.quote`, which wraps
    it in single quotes when needed ("it's" -> 'it'"'"'s'). Paths are sent in their home-relative form and expanded
    by the remote copy itself (expand_user_path), because quoting stops the shell from expanding "~".
    """
    items = ["python3", "-", "--remote-run", "--sites", "local", "--json", "--top", str(arguments.top),
             "--index-path", portable_paths["index"], "--claude-root", portable_paths["claude"],
             "--codex-root", portable_paths["codex"], "--titles-dir", portable_paths["titles"],
             "--time-zone", arguments.time_zone, "--system-name", site_name,
             "--name-tags" if arguments.name_tags else "--no-name-tags"]
    if arguments.after:
        items += ["--after", arguments.after]
    if arguments.before:
        items += ["--before", arguments.before]
    if arguments.rebuild:
        items += ["--rebuild"]
    # "--" ends the options: a question word that starts with "-" is then not read as an option.
    items.append("--")
    items += list(arguments.question)
    return [shlex.quote(item) for item in items]


def search_remote_site(site, arguments, portable_paths):
    """Run this same script on another site over its open connection and return (name, hits, note or error text).

    `ssh -o BatchMode=yes <target> python3 - <arguments>` starts Python there and makes it read the program from
    standard input ("-"); `input=` sends this file's own text. The remote copy uses its own home folder's transcripts
    and index, prints JSON, and that JSON is parsed here. BatchMode=yes forbids any password or two-factor question.
    """
    name, target = site
    with open(os.path.abspath(__file__), "rb") as script_file:
        script_bytes = script_file.read()
    remote_command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", target]
    remote_command += remote_command_words(name, arguments, portable_paths)
    try:
        result = subprocess.run(remote_command, input=script_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=arguments.cluster_timeout)
    except subprocess.TimeoutExpired:
        return name, [], "no answer within %d s" % arguments.cluster_timeout
    if result.returncode != 0:
        error_text = shorten(result.stderr.decode("utf-8", "replace"), 200)
        return name, [], "failed (exit %d): %s" % (result.returncode, error_text)
    try:
        answer = json.loads(result.stdout.decode("utf-8", "replace"))
    except ValueError:
        return name, [], "answer was not JSON"
    return name, answer.get("hits", []), answer.get("note", "")


# ---------------------------------------------------------------------------------------------------------------------
# Settings (only on the machine where the user asked; another site gets explicit options instead)
# ---------------------------------------------------------------------------------------------------------------------

def load_settings():
    """Import workhub_config.py from this script's folder and return (module, settings dict).

    workhub_config.py (an identical copy of the kit's lib/workhub_config.py) finds and reads the settings file and
    answers small questions about it: get_setting(), state_dir() (creates the folder, mode 700), system_name(),
    time_zone(), workstations(), clusters(), expand_path(). Exits with status 2 and the setup hint when the settings
    file is missing or unreadable.
    """
    script_folder = os.path.dirname(os.path.abspath(__file__))
    if script_folder not in sys.path:
        sys.path.insert(0, script_folder)
    import workhub_config
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
    return workhub_config, config


def fill_from_settings(arguments, wc, config):
    """Fill every option the user did not give from the settings; return the portable paths for other sites.

    Returns
        {"index", "claude", "codex", "titles"}: the same folders in a form that means the same thing on another site
        (home-relative), or the kit's defaults when a setting is an absolute path of this machine. "codex" is ""
        when Codex is not included.
    """
    claude_setting = wc.get_setting(config, "conversations.claude_projects_dir", default=DEFAULT_CLAUDE_PROJECTS_SETTING)
    include_codex = bool(wc.get_setting(config, "conversations.include_codex", default=False))
    codex_sessions_setting = wc.get_setting(config, "conversations.codex_sessions_dir",
                                            default=DEFAULT_CODEX_SESSIONS_SETTING)
    codex_home_setting = codex_home_from_setting(codex_sessions_setting) if include_codex else ""
    state_setting = wc.get_setting(config, "paths.state_dir", default="~/.local/state/workhub-claude")
    if arguments.index_path is None:
        arguments.index_path = os.path.join(wc.state_dir(config, SKILL_NAME), "index.sqlite")
    if arguments.claude_root is None:
        arguments.claude_root = wc.expand_path(claude_setting)
    if arguments.codex_root is None:
        arguments.codex_root = wc.expand_path(codex_home_setting)
    if arguments.titles_dir is None:
        arguments.titles_dir = wc.state_dir(config, "conversation_titles")
    if arguments.time_zone is None:
        arguments.time_zone = wc.time_zone(config)
    if arguments.system_name is None:
        arguments.system_name = wc.system_name(config)
    if arguments.name_tags is None:
        arguments.name_tags = bool(wc.get_setting(config, "workflow.conversation_names", default=False))
    state_portable = portable_home_path(state_setting, "~/.local/state/workhub-claude")
    codex_portable = portable_home_path(codex_home_setting, "~/.codex") if include_codex else ""
    return {"index": state_portable.rstrip("/") + "/" + SKILL_NAME + "/index.sqlite",
            "claude": portable_home_path(claude_setting, REMOTE_DEFAULT_CLAUDE_ROOT),
            "codex": codex_portable,
            "titles": state_portable.rstrip("/") + "/conversation_titles"}


def fill_remote_defaults(arguments):
    """On another site (--remote-run, no settings file): use the defaults for every option not given, expanded here."""
    if arguments.index_path is None:
        arguments.index_path = REMOTE_DEFAULT_INDEX_PATH
    if arguments.claude_root is None:
        arguments.claude_root = REMOTE_DEFAULT_CLAUDE_ROOT
    if arguments.codex_root is None:
        arguments.codex_root = ""
    if arguments.titles_dir is None:
        arguments.titles_dir = REMOTE_DEFAULT_TITLES_DIR
    if arguments.time_zone is None:
        arguments.time_zone = "UTC"
    if arguments.system_name is None:
        arguments.system_name = (os.environ.get("CC_CLUSTER") or socket.gethostname().split(".")[0]).lower()
    if arguments.name_tags is None:
        arguments.name_tags = False


# ---------------------------------------------------------------------------------------------------------------------
# Command line and output
# ---------------------------------------------------------------------------------------------------------------------

def parse_date(date_text, zone):
    """Turn "YYYY-MM-DD" (midnight in `zone`) into POSIX seconds, or None for an empty value."""
    if not date_text:
        return None
    # `strptime` reads the text with the given layout; ValueError (bad date) stops the script with a clear message.
    day = datetime.datetime.strptime(date_text, "%Y-%m-%d")
    return day.replace(tzinfo=zone).timestamp()


def parse_arguments():
    """Define and read the command-line options (path options are None when not given; main() fills them)."""
    parser = argparse.ArgumentParser(
        description="Find a past Claude Code or Codex conversation from words about what was done in it.",
        epilog='Example: python3 find_conversation.py "survey table cleaning" --after 2026-01-01')
    # `nargs="+"`: one or more words; quoting the whole question is also fine ("survey table").
    parser.add_argument("question", nargs="+", help="words about the conversation (what was done, files, tools)")
    parser.add_argument("--top", type=int, default=8, help="how many matches to show per site (default 8)")
    parser.add_argument("--after", help="only conversations with a message on or after this day, YYYY-MM-DD")
    parser.add_argument("--before", help="only conversations started before this day, YYYY-MM-DD")
    parser.add_argument("--sites", default="all",
                        help='"all" (default: this site + every other site of the settings with an open shared '
                             'connection), "local", or a comma list such as "local,mycluster"')
    parser.add_argument("--cluster-timeout", type=int, default=120,
                        help="seconds to wait for each other site (default 120; the first run there builds its index)")
    parser.add_argument("--rebuild", action="store_true", help="delete and rebuild the index before searching")
    parser.add_argument("--no-update", action="store_true",
                        help="search the index as it is, without re-reading changed transcripts (fastest)")
    parser.add_argument("--include-current", action="store_true",
                        help="also list the conversation that runs this search (left out by default)")
    parser.add_argument("--json", action="store_true", help="print the matches as JSON instead of text")
    parser.add_argument("--index-path", default=None,
                        help="index file (default <paths.state_dir>/find-conversation/index.sqlite)")
    parser.add_argument("--claude-root", default=None,
                        help="Claude Code transcripts folder (default conversations.claude_projects_dir)")
    parser.add_argument("--codex-root", default=None,
                        help="Codex home folder, '' for none (default: from conversations.codex_sessions_dir when "
                             "conversations.include_codex is true, else none)")
    parser.add_argument("--titles-dir", default=None,
                        help="conversation-title state folder (default <paths.state_dir>/conversation_titles)")
    parser.add_argument("--time-zone", default=None, help="IANA time zone for dates (default owner.time_zone)")
    parser.add_argument("--system-name", default=None, help="name of this site in system:/path labels")
    # `BooleanOptionalAction`-like pair written out for Python 3.8: --name-tags sets True, --no-name-tags False,
    # neither leaves None (then the settings decide).
    parser.add_argument("--name-tags", dest="name_tags", action="store_const", const=True, default=None,
                        help="read the machine from '[date][machine]...' names (default workflow.conversation_names)")
    parser.add_argument("--no-name-tags", dest="name_tags", action="store_const", const=False)
    # Internal: set by the copy sent to another site; no settings file is read there.
    parser.add_argument("--remote-run", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.top < 1:
        parser.error("--top must be at least 1")
    # Check the dates now, so a typo fails before any indexing (the zone does not matter for this check).
    for date_text in (arguments.after, arguments.before):
        if date_text:
            try:
                parse_date(date_text, datetime.timezone.utc)
            except ValueError:
                parser.error("dates must look like 2026-01-09, got %r" % date_text)
    return arguments


def print_hits(site_label, hits, note):
    """Print one site's matches as short text blocks."""
    print("== %s (%s)" % (site_label, note))
    if not hits:
        print("   no conversation contains these words")
        return
    for hit in hits:
        tool_label = "Claude Code" if hit["source"] == "claude" else "Codex"
        print("%d. %s" % (hit["rank"], hit["name"]))
        row_text = " · row %s" % hit["task"] if hit.get("task") else ""
        print("   %s · started %s · last message %s · %s requests%s" % (
            tool_label, hit["started_text"], hit["last_message_text"], hit["requests"], row_text))
        print("   folder %s:%s · id %s" % (hit["system"], hit["folder"], hit["session_id"]))
        print("   words found %d/%d (%s): %s" % (len(hit["words_found"]), hit["words_asked"],
                                                 ", ".join(hit["words_found"]), hit["excerpt"]))


def main():
    """Search this site and the reachable other sites, then print the matches (exit status 0, 2 for bad options or
    missing settings)."""
    arguments = parse_arguments()
    if arguments.remote_run:
        fill_remote_defaults(arguments)
        remote_sites = []
        portable_paths = {}
    else:
        wc, config = load_settings()
        portable_paths = fill_from_settings(arguments, wc, config)
        remote_sites = remote_sites_from_settings(wc, config, arguments.system_name)
    # Every path option is expanded HERE (~ and $VARIABLES), also on another site, where the shell did not do it.
    arguments.index_path = expand_user_path(arguments.index_path)
    arguments.claude_root = expand_user_path(arguments.claude_root)
    arguments.codex_root = expand_user_path(arguments.codex_root)
    arguments.titles_dir = expand_user_path(arguments.titles_dir)
    sites_to_search, closed_sites = open_sites(arguments.sites, remote_sites)
    # The other sites are searched in background threads while this site is searched in the main thread.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(sites_to_search))) as pool:
        site_futures = [pool.submit(search_remote_site, site, arguments, portable_paths) for site in sites_to_search]
        local_hits, local_note = search_this_site(arguments)
        site_results = [future.result() for future in site_futures]
    _zone, zone_label = load_zone(arguments.time_zone)
    closed_names = [name for name, _target in closed_sites]
    if arguments.json:
        answer = {"system": arguments.system_name, "time_zone": zone_label, "hits": local_hits, "note": local_note,
                  "sites": [{"site": name, "hits": hits, "note": note} for name, hits, note in site_results],
                  "sites_not_connected": closed_names}
        print(json.dumps(answer, ensure_ascii=False, indent=1))
        return 0
    print("Times in %s." % zone_label)
    print_hits(arguments.system_name + " (this site)", local_hits, local_note)
    for name, hits, note in site_results:
        print_hits(name, hits, note)
    if closed_sites:
        print("== not searched (no open shared SSH connection; open one, for example with cluster-login or "
              "`ssh -fN <alias>`, to add them): " + ", ".join(closed_names))
    return 0


# Run main() only when the file is executed as a script (also as `python3 -` on another site), not when imported.
if __name__ == "__main__":
    sys.exit(main())
