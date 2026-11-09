#!/usr/bin/env python3
"""
session_digest.py - what happened in Claude Code and Codex conversations since a given moment.

PURPOSE
    Before a Work Hub cleanup (skill `work-hub-cleanup`) can propose status changes, new tasks
    or due-date moves, it must know what was actually done since the previous cleanup. Every
    Claude Code conversation keeps a transcript and every Codex thread keeps a "rollout" file;
    both are text files with one JSON object per line. This script reads the ones that changed
    since --since, keeps the "turns" of that period (one turn = one request of the user plus
    the final answer to it), and writes a compact digest to read before asking any question.

INPUTS (read-only; nothing is ever modified)
    Settings:    the workhub-claude settings file, read with workhub_config.py (next to this
                 script). When the script runs on a remote machine through `python3 -` (see
                 Remote machines), workhub_config.py is not there; the few values needed are
                 then passed on the command line or read straight from that machine's own
                 settings file, if it has one.
    Claude Code: <projects dir>/<folder>/<session id>.jsonl, where <projects dir> is the setting
                 conversations.claude_projects_dir (default $CLAUDE_CONFIG_DIR/projects or
                 ~/.claude/projects). Main conversations only; the sub-agent transcripts stored
                 deeper in <session id>/subagents/ are ignored.
    Codex:       only when conversations.include_codex is true: <codex sessions dir>/YYYY/MM/DD/
                 rollout-*.jsonl (setting conversations.codex_sessions_dir, default
                 ~/.codex/sessions), plus, next to that folder, archived_sessions/rollout-*.jsonl
                 and session_index.jsonl (thread names). Codex's own helper threads (e.g.
                 "Guardian review") are skipped.
    Remote machines: the same files on each machine of the settings whose home folder is not
                 shared with this one (machines.clusters[] and machines.workstations[] with
                 shares_home_with_this_machine false), read by running this same script there.
                 A cluster is read only through an SSH connection that is ALREADY open (for
                 example opened with `cluster-login`); a new login is never attempted, so there
                 is never a password or two-factor prompt. A workstation is read through its open
                 shared connection, or else with `ssh -o BatchMode=yes` (SSH keys only).
    Work Hub row: <title state dir>/<session id>.json, the conversation-naming hook's state
                 file; <title state dir> is <paths.state_dir>/conversation_titles (default
                 ~/.local/state/workhub-claude/conversation_titles). Its field "task" is the
                 row's Task ID (e.g. "TK-42"), written when the conversation recorded its row with
                 the kit's set_conversation_task.py hook helper. Without it, a word of the
                 conversation's name such as "TK-42" is used.

OUTPUTS (in --output-dir, created if missing; existing files are replaced only with --overwrite)
    digest.txt     the human-readable digest: first an index of the conversations by Work Hub
                   row (Task ID) and of those with no row; then per conversation its name, row,
                   folder, and the turns of the period with the request, the user's answers to
                   questions, and the reply.
    sessions.json  the same facts as structured data (times are Unix seconds).
    With --json-only (used internally on remote machines) the JSON goes to standard output
    instead and no file is written.

USAGE
    # Everything since the previous cleanup (the value printed by `cleanup_log.py last`):
    python3 session_digest.py --since 2026-03-02T14:41+00:00 -o /tmp/hub/sessions
    # The last 36 hours, this machine only:
    python3 session_digest.py --since 36h --hosts none -o /tmp/hub/sessions
    # Two conversations in more detail (prefixes of their ids), this machine only:
    python3 session_digest.py --since 2026-03-02T14:41+00:00 --session 2d805a22,58fe4496 \
        --hosts none --prompt-chars 4000 --reply-chars 2500 --max-turns 40 -o /tmp/hub/detail
    # One conversation word for word (no limits), printed to the screen as well:
    python3 session_digest.py --since 2026-03-02T14:41+00:00 --session 2d805a22 --hosts none \
        --full -o /tmp/hub/full_2d805a22 --print

    Texts: requests keep their beginning; replies and answers keep their beginning and their
    end (the middle is cut), because decisions and next steps are usually written last. Each
    turn also lists the Notion changes it made ("NOTION WRITES: update-page 0a1b2c3d …").

SETTINGS USED
    owner.time_zone, system_name.*, notion.task_id_prefix (default "TK"),
    conversations.claude_projects_dir, conversations.include_codex,
    conversations.codex_sessions_dir, paths.state_dir, machines.clusters[],
    machines.workstations[] (name, ssh_alias, shares_home_with_this_machine),
    cluster_login.enabled (for the hints).

DEPENDENCIES
    Python >= 3.9 (for `zoneinfo`), standard library only, so it also runs with a cluster's
    python3. workhub_config.py (an identical copy of the kit's settings reader) in the same
    folder, except when the script runs remotely through `python3 -`.

ASSUMPTIONS
    - Transcript timestamps are UTC ISO 8601 texts such as "2026-03-02T15:05:12.345Z".
    - A file's modification time is at least as late as its last message, so files not modified
      since --since are skipped without being opened.
    - Claude Code marks typed requests with origin.kind = "human" and background-job notices
      with origin.kind = "task-notification" (versions of 2026). Older transcripts without
      `origin` are handled by looking at the text (injected context starts with "<").
    - Codex starts each turn with an event "task_started" and ends it with "task_complete",
      whose field last_agent_message is the final answer (Codex 0.15x-0.16x, 2026).
    - The conversation that runs this script (Claude Code exports CLAUDE_CODE_SESSION_ID) is
      kept, because its earlier turns may hold real work; it is marked "(this conversation)".
    - Work Hub Task IDs are the prefix of notion.task_id_prefix (default "TK"), a dash and 1 to
      5 digits (Notion's automatic "ID" property). A recorded Task ID wins over one in the name.

PRIVACY
    The digest is a local scratch file. Transcripts can contain personal identifiers (study
    participants, patients, clients); the skill copies facts into Notion only after removing them.

EXIT STATUS
    0 = success (unreachable machines are reported in the digest, not as errors);
    1 = an output file exists without --overwrite, or --since/--timezone cannot be read;
    2 = invalid command-line arguments (reported by argparse), or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library)
# ----------------------------------------------------------------------------------------------

# `argparse` turns the words typed after the script name into a Namespace object whose
# attributes hold the values; it also prints --help and exits with status 2 on invalid input.
import argparse

# `datetime.datetime` is one moment (date + clock time, here always with a UTC offset);
# `datetime.timedelta` is a duration, used for "--since 36h".
import datetime

# `glob.glob(pattern)` lists the paths that match a shell-style pattern such as "*/*.jsonl".
import glob

# `json` converts JSON text to Python values (dict, list, str, int, float, bool, None) and back.
import json

# `os` gives environment variables, file modification times and path helpers.
import os

# `re` is Python's regular-expression module, used to remove injected blocks from requests and
# to recognize Task IDs.
import re

# `shlex.quote(text)` wraps a text in quotes that a remote shell reads back unchanged; used for
# the arguments of the copy of this script that runs on a remote machine.
import shlex

# `socket.gethostname()` returns this machine's network name, for example "ws01"; used only
# when neither the settings nor --system-name give the machine's name.
import socket

# `subprocess.run` starts another program (here `ssh`) and waits for it to finish.
import subprocess

# `sys` gives `sys.exit(status)`, `sys.path`, `sys.stdout` and `sys.stderr`.
import sys

# `ZoneInfo("Europe/Lisbon")` is a time-zone object with that zone's daylight-saving rules,
# read from the operating system's time-zone database (Python >= 3.9).
from zoneinfo import ZoneInfo

# The kit's settings reader, workhub_config.py, is NOT imported here at the top: on a remote
# machine this script arrives through `python3 -` (standard input) without its folder, so the
# import would fail there. load_settings_reader() below imports it only when it sits next to this file.


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# This skill's name (used in messages).
SKILL_NAME = "work-hub-cleanup"

# Slash commands that only manage the conversation itself; they say nothing about the work and
# are left out of the digest. Any other command (for example a skill typed as /name) is shown.
HOUSEKEEPING_COMMANDS = {"/compact", "/clear", "/model", "/fast", "/config", "/context", "/cost",
                         "/status", "/help", "/resume", "/rename", "/exit", "/login", "/logout"}

# How long one remote machine may take to answer. Large transcripts make the remote run take a
# few seconds; two minutes leaves a wide margin while keeping a hung connection from blocking.
REMOTE_TIMEOUT_SECONDS = 120

# The Task ID prefix when the settings do not give one (notion.task_id_prefix): "TK" -> "TK-42".
DEFAULT_TASK_ID_PREFIX = "TK"

# Where a settings file lives when workhub_config.py is not available (remote runs). These two
# values repeat the reader's own defaults (DEFAULT_CONFIG_PATH and CONFIG_PATH_ENV in
# workhub_config.py); keep them equal.
FALLBACK_SETTINGS_PATH = os.path.join("~", ".config", "workhub-claude", "config.json")
SETTINGS_PATH_ENVIRONMENT_VARIABLE = "WORKHUB_CLAUDE_CONFIG"

# The kit's default state folder (paths.state_dir) and the sub-folder of the conversation-naming
# hook, which writes one "<session id>.json" per Claude Code conversation.
FALLBACK_STATE_DIR = os.path.join("~", ".local", "state", "workhub-claude")
TITLE_STATE_SUBFOLDER = "conversation_titles"


# ----------------------------------------------------------------------------------------------
# Small text and time helpers
# ----------------------------------------------------------------------------------------------

def parse_json_object(line_text):
    """Parse one line of a JSON Lines file into a dictionary.

    Parameters:
        line_text (str): one line of the file.

    Returns:
        dict or None: the parsed object, or None for a blank, malformed or non-object line
            (a transcript being written at this very moment can end with a half-written line).
    """
    # `json.loads` raises ValueError on malformed text; that line is simply ignored.
    try:
        value = json.loads(line_text)
    except ValueError:
        return None

    # Only JSON objects ({...}) are records; anything else is ignored.
    if isinstance(value, dict):
        return value
    return None


def iso_to_epoch(timestamp_text):
    """Convert a transcript timestamp into Unix seconds (seconds since 1970-01-01 UTC).

    Parameters:
        timestamp_text (str or None): e.g. "2026-03-02T15:05:12.345Z".

    Returns:
        float or None: e.g. 1791990312.345, or None when the text is missing or unreadable.
    """
    # Records without a timestamp (titles, settings) give None.
    if not isinstance(timestamp_text, str) or not timestamp_text:
        return None

    # Python 3.9-3.10 `fromisoformat` does not accept the final "Z" (= UTC), so it is replaced
    # by the equivalent "+00:00". Example: "...12.345Z" -> "...12.345+00:00".
    cleaned_text = timestamp_text
    if cleaned_text.endswith("Z"):
        cleaned_text = cleaned_text[:-1] + "+00:00"

    # Python 3.9-3.10 accept only 3 or 6 fractional digits, but some writers use other counts
    # (e.g. 9 = nanoseconds). A regular expression finds the fraction, which is then cut or
    # padded with zeros to exactly 6 digits (microseconds): ".1234" -> ".123400". Pattern parts:
    #   \.        a literal dot (the decimal point)
    #   (\d+)     group 1: one or more digits (the fraction)
    #   (?=[+-])  look-ahead: the fraction must be followed by "+" or "-" (the offset), which is
    #             checked but not consumed.
    match = re.search(r"\.(\d+)(?=[+-])", cleaned_text)
    if match:
        fraction_digits = match.group(1)[:6].ljust(6, "0")
        cleaned_text = cleaned_text[:match.start()] + "." + fraction_digits + cleaned_text[match.end():]

    # `fromisoformat` builds an aware datetime; `.timestamp()` gives Unix seconds.
    try:
        moment = datetime.datetime.fromisoformat(cleaned_text)
    except ValueError:
        return None

    # A timestamp without an offset is taken as UTC, as transcripts write UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def format_epoch(epoch_seconds, timezone):
    """Format Unix seconds as "Wed 10-14 11:52" in the user's time zone ("?" when unknown).

    Parameters:
        epoch_seconds (float or None): Unix seconds.
        timezone (ZoneInfo): zone in which to show the clock time.

    Returns:
        str: the formatted time.
    """
    # Unknown times are shown as "?" rather than raising an error.
    if epoch_seconds is None:
        return "?"

    # `fromtimestamp(seconds, zone)` builds the aware moment; `strftime` formats it:
    # %a weekday, %m month, %d day, %H:%M clock.
    moment = datetime.datetime.fromtimestamp(epoch_seconds, timezone)
    return moment.strftime("%a %m-%d %H:%M")


def shorten(text, limit):
    """Put a text on one line and cut it to at most `limit` characters, keeping its beginning.

    Used for requests, whose beginning says what was asked.

    Parameters:
        text (str or None): any text.
        limit (int or None): maximum length; the cut is marked with "…". None = no cut.

    Returns:
        str: e.g. shorten("a\\n  b  c", 4) -> "a b…".
    """
    # `re.sub(r"\s+", " ", text)` replaces every run of white space (spaces, tabs, line breaks;
    # `\s` = one white-space character, `+` = one or more) by a single space.
    one_line_text = re.sub(r"\s+", " ", text or "").strip()

    # No limit (--full), or a text within the limit: returned whole.
    if limit is None or len(one_line_text) <= limit:
        return one_line_text

    # Longer texts keep their first `limit - 1` characters followed by "…" (one character), so
    # the result is at most `limit` long (one shorter when `.rstrip()` removes a space left just
    # before the "…"). `max(limit - 1, 0)` avoids a negative index when limit is 0.
    return one_line_text[:max(limit - 1, 0)].rstrip() + "…"


def shorten_middle(text, limit):
    """Put a text on one line and cut its middle, keeping its beginning AND its end.

    Used for replies and answers: a reply often ends with what matters for the hub ("Decisions
    for you", "Next", the final numbers), so its end is kept in full. The beginning gets 40% of
    the limit, the end the rest.
    Example: shorten_middle("abcdefghijklmnopqrst", 10) -> "abcd … rst": 4 characters of the
    beginning, " … " (3 characters), 3 characters of the end = 10.

    Parameters:
        text (str or None): any text.
        limit (int or None): maximum length. None = no cut.

    Returns:
        str: the shortened text, with " … " where the middle was removed.
    """
    # Same white-space cleaning as shorten().
    one_line_text = re.sub(r"\s+", " ", text or "").strip()

    # No limit, or short enough: returned whole.
    if limit is None or len(one_line_text) <= limit:
        return one_line_text

    # Characters kept at each end. `int(...)` drops the decimals; the 3 characters of " … "
    # are subtracted so that the result stays within the limit.
    head_length = int(limit * 0.4)
    tail_length = max(limit - head_length - 3, 0)

    # `text[:n]` = the first n characters; `text[-n:]` = the last n characters.
    head_text = one_line_text[:head_length].rstrip()
    tail_text = one_line_text[len(one_line_text) - tail_length:].lstrip()
    return head_text + " … " + tail_text


# ----------------------------------------------------------------------------------------------
# Settings (the kit's settings reader, or a minimal fallback on remote machines)
# ----------------------------------------------------------------------------------------------

def load_settings_reader():
    """Import workhub_config.py from this script's own folder, or return None.

    The script runs in two ways:
      - as a file (`python3 scripts/session_digest.py`): workhub_config.py sits next to it and is
        imported;
      - through `python3 -` on a remote machine: Python then sets `__file__` to "<stdin>", there
        is no folder next to the script, and None is returned. Importing anyway would search the
        remote shell's current folder, where an unrelated file of that name could be picked up.

    Returns:
        module or None: the workhub_config module, or None.
    """
    # `globals()` is the dictionary of this module's global names; `.get("__file__", "")` gives
    # the path Python ran this file from ("<stdin>" for `python3 -`), or "" if it is unknown.
    script_path = globals().get("__file__", "")
    if not script_path or not os.path.isfile(script_path):
        return None

    # The folder holding this script, e.g. ".../work-hub-cleanup/scripts".
    script_folder = os.path.dirname(os.path.abspath(script_path))
    reader_path = os.path.join(script_folder, "workhub_config.py")
    if not os.path.isfile(reader_path):
        return None

    # `sys.path` is the list of folders Python searches on `import`. Putting this script's
    # folder first makes `import workhub_config` load the copy next to this file (also when the
    # tests load this file as a module, where the folder is not searched by itself).
    if script_folder not in sys.path:
        sys.path.insert(0, script_folder)

    # A plain `import` inside a function binds the module to a local name and returns it.
    import workhub_config
    return workhub_config


def read_settings_file_directly():
    """Read the settings JSON without workhub_config.py (remote runs); {} when there is none.

    Used only when load_settings_reader() returned None, so that a remote machine that has the
    kit set up still uses its own folders (paths.state_dir, conversations.*).

    Returns:
        dict: the settings, or {} when the file is missing, unreadable or not a JSON object.
    """
    # The same rule as workhub_config.config_path(): the environment variable wins over the
    # default place. `os.path.expanduser` replaces "~" by the home folder.
    chosen_path = os.environ.get(SETTINGS_PATH_ENVIRONMENT_VARIABLE) or FALLBACK_SETTINGS_PATH
    settings_path = os.path.expanduser(chosen_path)

    # `json.load` parses the whole file. OSError = missing or unreadable; ValueError = not
    # valid JSON. Either way the machine is treated as not set up ({}), and defaults are used.
    try:
        with open(settings_path, "r", encoding="utf-8") as settings_file:
            loaded = json.load(settings_file)
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def settings_value(settings, section, key):
    """Return settings[section][key], or None when either level is missing or empty.

    A deliberately small lookup for the fallback path (no dotted keys, no defaults), so that
    this script does not repeat workhub_config.get_setting().

    Example: settings_value({"paths": {"state_dir": "~/state"}}, "paths", "state_dir") -> "~/state"
    """
    section_value = settings.get(section)
    if not isinstance(section_value, dict):
        return None
    value = section_value.get(key)
    if value is None or value == "":
        return None
    return value


def expand_folder(text):
    """Return `text` with ~ and $VARIABLES expanded (same rule as workhub_config.expand_path)."""
    return os.path.expandvars(os.path.expanduser(str(text)))


def fallback_system_name():
    """Return a machine name when neither the settings nor --system-name give one.

    $CC_CLUSTER (set on some clusters) wins, else the short host name in lower case:
    "ws01.example.org" -> "ws01".
    """
    # `os.environ.get` returns the variable's text, or None when it is not set.
    cluster_name = os.environ.get("CC_CLUSTER")
    if cluster_name:
        return cluster_name.lower()

    # "ws01.example.org".split(".") -> ["ws01", "example", "org"]; `[0]` keeps "ws01".
    return socket.gethostname().split(".")[0].lower()


def task_id_pattern(prefix):
    """Build the pattern of a Work Hub Task ID such as "TK-42" for the given prefix.

    `re.escape(prefix)` puts a backslash before any character of the prefix that has a special
    meaning in a pattern, so the prefix is matched literally. Pattern parts: `^` = start of the
    text; the prefix; `-` = a dash; `[0-9]` = one digit; `{1,5}` = 1 to 5 of the item before;
    `$` = end of the text. With prefix "TK", "TK-42" fits, while "TK-42b", "xTK-42" and "TK-"
    do not. `re.compile` turns the pattern text into a reusable pattern object whose
    `.match(text)` gives a match object when the text fits, else None.

    Parameters:
        prefix (str): notion.task_id_prefix, e.g. "TK".

    Returns:
        re.Pattern: the compiled pattern.
    """
    return re.compile("^" + re.escape(prefix) + r"-[0-9]{1,5}$")


def parse_since(text, timezone):
    """Convert the --since argument into Unix seconds.

    Accepted forms:
        "2026-03-02T14:41+01:00"  ISO 8601 with offset ("Z" also accepted)
        "2026-03-02 14:41"        no offset: read in `timezone`
        "2026-03-02"              midnight at the start of that day, in `timezone`
        "36h", "2d"               that many hours or days before now
        "@1791990312"             Unix seconds (used when this script calls itself on a cluster)

    Parameters:
        text (str): the --since value.
        timezone (ZoneInfo): zone for texts without an offset.

    Returns:
        float: Unix seconds.

    Raises:
        ValueError: for any other text.
    """
    # Surrounding spaces are ignored.
    cleaned_text = text.strip()

    # "@<seconds>": exact Unix seconds.
    if cleaned_text.startswith("@"):
        return float(cleaned_text[1:])

    # "<number>h" or "<number>d": a duration before now. `cleaned_text[:-1]` drops the last
    # character ("36h" -> "36"); `float("36")` -> 36.0.
    if cleaned_text.endswith("h") or cleaned_text.endswith("d"):
        amount = float(cleaned_text[:-1])

        # `timedelta(hours=36)` is 36 hours; `timedelta(days=2)` is 2 days.
        if cleaned_text.endswith("h"):
            duration = datetime.timedelta(hours=amount)
        else:
            duration = datetime.timedelta(days=amount)

        # Now minus the duration; `.timestamp()` converts the moment into Unix seconds.
        now_moment = datetime.datetime.now(timezone)
        return (now_moment - duration).timestamp()

    # ISO 8601. The final "Z" is replaced for Python 3.9-3.10 (see iso_to_epoch).
    if cleaned_text.endswith("Z"):
        cleaned_text = cleaned_text[:-1] + "+00:00"

    # `fromisoformat` raises ValueError for unreadable text; main() reports it.
    moment = datetime.datetime.fromisoformat(cleaned_text)

    # Without an offset the clock time is read in the user's zone (`.replace` attaches the
    # zone without changing the clock time).
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone)

    # DEBUG CHECK: for "2026-03-02T14:41+01:00" the result is 1772458860.0 (Unix seconds).
    return moment.timestamp()


# ----------------------------------------------------------------------------------------------
# Claude Code transcripts
# ----------------------------------------------------------------------------------------------

def join_text_blocks(content):
    """Return the text of a message's content: a plain string, or the "text" blocks of a list.

    Parameters:
        content (str or list or None): the "content" field of a transcript message.

    Returns:
        str: the texts joined with spaces ("" when there is none).
    """
    # Claude Code stores short prompts as a plain string.
    if isinstance(content, str):
        return content

    # Otherwise content is a list of blocks such as {"type": "text", "text": "..."} or images.
    texts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text") or "")
    return " ".join(texts)


def clean_request_text(text):
    """Remove the context blocks that the editor and Claude Code add around a typed request.

    Removed: <system-reminder>...</system-reminder>, <ide_selection>...</ide_selection>,
    <ide_opened_file>...</ide_opened_file>. Pasted text blocks (<pasted_content ...> ...
    </pasted_content ...>) are replaced by the marker "(pasted text)".

    Parameters:
        text (str): the raw request.

    Returns:
        str: the request as the user typed it, on the lines kept.
    """
    # Each pattern below reads: the opening tag, then `.*?` = any characters, as few as
    # possible (so that two blocks are removed separately, not everything between the first
    # opening and the last closing tag), then the closing tag. `re.DOTALL` lets "." match
    # line breaks too, as these blocks span several lines.
    for tag_name in ("system-reminder", "ide_selection", "ide_opened_file"):
        pattern = r"<%s>.*?</%s>" % (tag_name, tag_name)
        text = re.sub(pattern, " ", text, flags=re.DOTALL)

    # Pasted blocks may carry an id: <pasted_content id="be2f"> ... </pasted_content id="be2f">.
    # `[^>]*` = any characters except ">", i.e. whatever attributes the tag has.
    text = re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", " (pasted text) ", text,
                  flags=re.DOTALL)
    return text.strip()


def command_text(text):
    """Turn a slash-command record into "/name args".

    Example: "<command-name>/compact</command-name> <command-args></command-args>" -> "/compact".

    Parameters:
        text (str): the record's text, starting with "<command-name>".

    Returns:
        str: the command name with its arguments.
    """
    # `([^<]*)` captures every character up to the next "<", i.e. the tag's content.
    name_match = re.search(r"<command-name>([^<]*)</command-name>", text)
    args_match = re.search(r"<command-args>([^<]*)</command-args>", text)

    # A missing tag gives an empty part. `X if condition else Y` is Python's conditional
    # expression: X when the condition is true, else Y. `re.search` returns None when nothing
    # matches, and `.group(1)` is the text captured by the first (...) of the pattern.
    name = name_match.group(1).strip() if name_match else ""
    arguments = args_match.group(1).strip() if args_match else ""

    # Skills typed without the slash still read as commands.
    if name and not name.startswith("/"):
        name = "/" + name
    return (name + " " + arguments).strip()


def notice_summary(text):
    """Summarize a background-job notice (a <task-notification> block) in one short text.

    Parameters:
        text (str): the notice record's text.

    Returns:
        str: its <summary> (or <status>) content, or the text without tags.
    """
    # `(.*?)` captures the content between the tags (as few characters as possible).
    for tag_name in ("summary", "status"):
        match = re.search(r"<%s>(.*?)</%s>" % (tag_name, tag_name), text, flags=re.DOTALL)
        if match:
            return match.group(1).strip()

    # Fallback: `<[^>]+>` = "<", one or more characters that are not ">", then ">": any tag.
    return re.sub(r"<[^>]+>", " ", text).strip()


def classify_claude_user_record(record, content):
    """Decide what a "user" record of a Claude Code transcript is.

    Parameters:
        record (dict): the transcript record.
        content (str or list): its message content.

    Returns:
        tuple (kind, text): kind is
            "request" - a request typed by the user (text = the cleaned request);
            "notice"  - a background job finished and Claude Code told the assistant;
            "command" - a slash command that is not housekeeping (text = "/name args");
            "skip"    - anything else (injected context, summaries after /compact, ...).
    """
    # Records flagged as meta-information or as the summary written by /compact are not
    # requests (the summary only restates earlier turns).
    if record.get("isMeta") or record.get("isCompactSummary"):
        return "skip", ""

    # The record's plain text.
    text = join_text_blocks(content)
    stripped_text = text.lstrip()

    # Slash commands: keep them unless they only manage the conversation (/compact, /model ...).
    if stripped_text.startswith("<command-name>"):
        command = command_text(stripped_text)
        command_name = command.split(" ")[0]
        if not command_name or command_name in HOUSEKEEPING_COMMANDS:
            return "skip", ""
        return "command", command

    # The marker written when the user interrupts a turn (Escape) is not a request.
    if stripped_text.startswith("[Request interrupted"):
        return "skip", ""

    # Recent Claude Code versions say who wrote the record in `origin.kind`.
    origin = record.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        if origin["kind"] == "human":
            return "request", clean_request_text(text)
        if origin["kind"] == "task-notification":
            return "notice", notice_summary(text)
        # Other kinds ("peer" = a report from another agent, ...) are model output, not requests.
        return "skip", ""

    # Older transcripts without `origin`: injected context starts with "<"; the summary of a
    # compacted conversation starts with "This session is being continued"; "[Request
    # interrupted by user]" only marks that the user pressed Escape, it is not a request.
    # `text.startswith((a, b, c))` with a tuple is true when the text starts with any of them.
    if not stripped_text:
        return "skip", ""
    if stripped_text.startswith(("<", "This session is being continued", "Caveat:", "[Request interrupted")):
        return "skip", ""
    return "request", clean_request_text(text)


def tool_result_texts(content, wanted_tool_ids):
    """Return the texts of the tool results that answer the given tool calls.

    Used for AskUserQuestion: its result holds the user's answers, which are decisions.

    Parameters:
        content (list or other): the content of a "user" record.
        wanted_tool_ids (set of str): ids of the tool calls whose results are wanted.

    Returns:
        list of str: one text per matching result (empty when none).
    """
    # Tool results are blocks {"type": "tool_result", "tool_use_id": "...", "content": ...}.
    texts = []
    if not isinstance(content, list):
        return texts
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        if block.get("tool_use_id") not in wanted_tool_ids:
            continue
        # The result's own content is a string or a list of text blocks.
        answer_text = join_text_blocks(block.get("content"))

        # A question the user closed without answering: Claude Code then writes a long fixed
        # text ("The user doesn't want to proceed with this tool use. The tool use was
        # rejected ..."). It is replaced by a short, clear label.
        if "doesn't want to proceed with this tool use" in answer_text:
            texts.append("(the user dismissed the question without answering)")
            continue

        # Claude Code wraps the answers in fixed sentences that add nothing; they are removed.
        # Example: 'Your questions have been answered: "Q"="A". You can now continue with these
        # answers in mind.' -> '"Q"="A".'
        for boilerplate in ("Your questions have been answered:", "User has answered your questions:",
                            "You can now continue with these answers in mind.",
                            "You can now continue with the user's answers in mind."):
            answer_text = answer_text.replace(boilerplate, " ")
        texts.append(answer_text.strip())
    return texts


def new_turn(moment, kind, prompt):
    """Create an empty turn.

    Parameters:
        moment (float or None): Unix seconds when the turn started.
        kind (str): "request", "notice" or "command".
        prompt (str): what started the turn.

    Returns:
        dict: {"time", "end_time", "kind", "prompt", "answers", "reply", "reply_time",
            "notion_writes"}. end_time is the last activity of the turn; reply is the latest
            answer text; notion_writes lists the Notion changes made during the turn.
    """
    return {"time": moment, "end_time": moment, "kind": kind, "prompt": prompt,
            "answers": [], "reply": "", "reply_time": None, "notion_writes": []}


# Parts of tool names that mean "this call changes something in Notion". Tool names differ by
# program ("mcp__claude_ai_Notion__notion-update-page" in Claude Code; Codex apps may use "_"),
# so names are compared in lower case with "_" turned into "-".
NOTION_WRITE_VERBS = ("create-pages", "update-page", "move-pages", "delete-pages", "duplicate-page",
                      "create-comment", "update-data-source", "create-database", "delete-databases",
                      "create-view", "update-view")


def describe_notion_write(tool_name, tool_input):
    """Describe a tool call that writes to Notion in a few words, or return None.

    Lets the digest show which conversation changed which Work Hub row, and when.
    Examples: "update-page 0a1b2c3d (update_properties)", "create-pages ×5 in 4e5f6a7b".

    Parameters:
        tool_name (str): the tool's name as recorded in the transcript.
        tool_input (dict or other): the call's arguments.

    Returns:
        str or None: the description, or None when the call does not write to Notion.
    """
    # Normalize the name: "mcp__claude_ai_Notion__notion-update-page" -> lower case, "_" -> "-".
    normalized_name = (tool_name or "").lower().replace("_", "-")
    if "notion" not in normalized_name:
        return None

    # Find which write verb the name contains; none means a read (fetch, search, query).
    found_verb = None
    for verb in NOTION_WRITE_VERBS:
        if verb in normalized_name:
            found_verb = verb
            break
    if found_verb is None:
        return None

    # The arguments: a dictionary in Claude Code. Anything else gives a bare description.
    if not isinstance(tool_input, dict):
        return found_verb

    # A single page: its id's first 8 characters (enough to match a row URL), plus the command.
    page_id = tool_input.get("page_id") or ""
    if page_id:
        command = tool_input.get("command") or ""
        description = "%s %s" % (found_verb, page_id.replace("-", "")[:8])
        if command:
            description += " (%s)" % command
        return description

    # Several new pages: how many, and under which parent (data source, database or page).
    pages = tool_input.get("pages")
    parent = tool_input.get("parent")
    if isinstance(pages, list):
        parent_id = ""
        if isinstance(parent, dict):
            parent_id = parent.get("data_source_id") or parent.get("database_id") or parent.get("page_id") or ""
        return "%s ×%d in %s" % (found_verb, len(pages), parent_id.replace("-", "")[:8] or "?")
    return found_verb


def read_claude_transcript(path):
    """Read a whole Claude Code transcript into a session dictionary with every turn.

    Parameters:
        path (str): the transcript (.jsonl).

    Returns:
        dict: {"client": "claude", "id", "title", "cwd", "path", "started", "last_activity",
            "turns": list of turn dicts with full (uncut) texts}.
    """
    # The file name is the session id: "<uuid>.jsonl" -> "<uuid>".
    session_id = os.path.basename(path)[:-len(".jsonl")]

    # Values filled while reading. An empty text "" or None means "not seen yet".
    custom_title = ""          # name given by the user (/rename); wins over the others
    ai_title = ""              # automatic title written by Claude Code
    summary_title = ""         # title used by older versions
    working_directory = ""     # folder the conversation was opened in
    first_moment = None        # first message time (Unix seconds)
    last_moment = None         # last message time (Unix seconds)
    ask_tool_ids = set()       # ids of AskUserQuestion calls, to recognize their answers later
    turns = []                 # every turn, in order
    current_turn = None        # the turn that the next assistant messages belong to

    # `open(..., "r", encoding="utf-8", errors="replace")` opens the transcript as text;
    # `errors="replace"` turns any invalid byte into "?" instead of stopping the read. The `with`
    # block closes the file at its end. Iterating over the file yields one line at a time, so a
    # transcript of tens of megabytes is never held in memory whole.
    with open(path, "r", encoding="utf-8", errors="replace") as transcript_file:
        for line_text in transcript_file:

            # One record per line; unreadable lines (e.g. half-written) give None and are skipped.
            record = parse_json_object(line_text)
            if record is None:
                continue

            # Records of sub-agents ("sidechains") are internal work, not the conversation.
            if record.get("isSidechain"):
                continue

            # Title records: the last one of each kind wins (titles are rewritten over time).
            record_type = record.get("type")
            if record_type == "custom-title":
                custom_title = record.get("customTitle") or custom_title
                continue
            if record_type == "ai-title":
                ai_title = record.get("aiTitle") or ai_title
                continue
            if record_type == "summary":
                summary_title = record.get("summary") or summary_title
                continue

            # Only messages ("user" and "assistant" records) carry the conversation.
            if record_type not in ("user", "assistant"):
                continue

            # Times: first and last message of the whole conversation.
            moment = iso_to_epoch(record.get("timestamp"))
            if moment is not None:
                if first_moment is None:
                    first_moment = moment
                last_moment = moment

            # The folder the conversation was opened in: the first one recorded. (Later records
            # can show another folder after a `cd`, but the conversation is resumed from, and
            # listed under, the folder it was opened in.)
            if record.get("cwd") and not working_directory:
                working_directory = record["cwd"]

            # The message content: a string or a list of blocks.
            content = (record.get("message") or {}).get("content")

            # Assistant messages: the last text block of a turn is its final answer; questions
            # asked with AskUserQuestion are remembered to pick up their answers. Every
            # assistant message extends the activity of the running turn (`end_time`).
            if record_type == "assistant":
                if current_turn is not None and moment is not None:
                    current_turn["end_time"] = moment

                # Assistant content is a list of blocks: "text", "tool_use", "thinking", ...
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue

                    # A non-empty text block replaces the turn's reply, so after the loop over
                    # the whole file the reply is the LAST text of the turn: its final answer.
                    block_text = block.get("text") or ""
                    if block.get("type") == "text" and block_text.strip() and current_turn is not None:
                        current_turn["reply"] = block_text
                        current_turn["reply_time"] = moment

                    # A question tool call: remember its id; the user's answer arrives later as
                    # a "tool_result" block with the same id.
                    elif block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                        ask_tool_ids.add(block.get("id"))

                    # A tool call that writes to Notion: noted on the turn, so the cleanup knows
                    # which rows a conversation already changed.
                    elif block.get("type") == "tool_use" and current_turn is not None:
                        notion_write = describe_notion_write(block.get("name"), block.get("input"))
                        if notion_write:
                            current_turn["notion_writes"].append(notion_write)
                continue

            # User records that answer an AskUserQuestion call: the answers are decisions.
            answer_texts = tool_result_texts(content, ask_tool_ids)
            if answer_texts:
                if current_turn is not None:
                    current_turn["answers"].extend(answer_texts)
                    if moment is not None:
                        current_turn["end_time"] = moment
                continue

            # Other user records: a typed request, a job notice or a command starts a new turn.
            # Skipped records (ordinary tool results, injected context) belong to the running
            # turn and extend its activity; a record that starts a new turn does not extend the
            # previous one.
            kind, text = classify_claude_user_record(record, content)
            if kind == "skip":
                if current_turn is not None and moment is not None:
                    current_turn["end_time"] = moment
                continue

            # A new turn begins; the following assistant messages belong to it.
            current_turn = new_turn(moment, kind, text)
            turns.append(current_turn)

    # The name shown: the user's name first, then the automatic one, then the session id.
    # `a or b or c` gives the first value that is not empty.
    title = custom_title or ai_title or summary_title or session_id[:8]

    # DEBUG CHECK: `len(turns)` should match the number of requests visible in the conversation.
    return {"client": "claude", "id": session_id, "title": title, "cwd": working_directory,
            "path": path, "started": first_moment, "last_activity": last_moment, "turns": turns}


def find_recent_files(patterns, since_epoch):
    """List the files matching the patterns whose modification time is at or after since_epoch.

    Parameters:
        patterns (list of str): shell-style patterns, e.g. ".../projects/*/*.jsonl".
        since_epoch (float): Unix seconds.

    Returns:
        list of str: matching paths, newest first.
    """
    # (modification time, path) pairs of the files modified in the period.
    candidates = []
    for pattern in patterns:
        # `glob.glob(pattern)` lists the existing paths matching the pattern ("*" = any name).
        for path in glob.glob(pattern):
            # `os.path.getmtime` gives the last modification time in Unix seconds. A file that
            # disappears in the meantime raises OSError and is skipped.
            try:
                modified = os.path.getmtime(path)
            except OSError:
                continue
            # A file not modified since the start of the period cannot hold a newer message.
            if modified >= since_epoch:
                candidates.append((modified, path))

    # `sort(reverse=True)` orders the pairs by time, newest first (the first element of each
    # pair, the time, is compared first).
    candidates.sort(reverse=True)

    # Keep only the paths, in that order.
    paths = []
    for modified, path in candidates:
        paths.append(path)
    return paths


# ----------------------------------------------------------------------------------------------
# Codex rollouts
# ----------------------------------------------------------------------------------------------

def read_codex_thread_names(codex_dir):
    """Read Codex's index of thread names.

    Parameters:
        codex_dir (str): the Codex root folder that holds session_index.jsonl, i.e. the
            parent of the sessions folder (usually ~/.codex).

    Returns:
        dict: thread id -> latest name (empty when the index is missing).
    """
    # id -> name; stays empty on a machine where Codex never ran.
    names = {}
    index_path = os.path.join(codex_dir, "session_index.jsonl")
    if not os.path.exists(index_path):
        return names

    # Each line: {"id": "...", "thread_name": "...", "updated_at": "..."}. A thread renamed
    # twice has two lines; the later line overwrites the earlier name in the dictionary.
    with open(index_path, "r", encoding="utf-8", errors="replace") as index_file:
        for line_text in index_file:
            record = parse_json_object(line_text)
            if record and record.get("id") and record.get("thread_name"):
                names[record["id"]] = record["thread_name"]
    return names


def codex_message_text(payload):
    """Join the texts of a Codex message item ("input_text" and "output_text" parts).

    Parameters:
        payload (dict): the payload of a "response_item" record of type "message".

    Returns:
        str: the joined text.
    """
    # The content is a list of parts such as {"type": "input_text", "text": "..."}; images and
    # other parts have no text and are skipped. `or []` turns a missing content into an empty list.
    texts = []
    for part in payload.get("content") or []:
        if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text"):
            texts.append(part.get("text") or "")

    # `" ".join(list)` glues the texts with one space between them.
    return " ".join(texts)


def classify_codex_user_text(text):
    """Decide what a Codex user message is.

    Codex puts its own context into "user" messages (instructions from AGENTS.md, the
    environment, the list of plugins); those are skipped.

    Parameters:
        text (str): the message text.

    Returns:
        tuple (kind, text): "request", "answers" (replies to Codex's own questions),
            "pasted" (the list of files the user pasted) or "skip".
    """
    stripped_text = text.strip()

    # Replies to questions Codex asked with its question tool: they are decisions.
    if stripped_text.startswith("<send_user_message_question_reply>"):
        return "answers", stripped_text

    # Injected context: XML-like blocks and the AGENTS.md instructions.
    if not stripped_text or stripped_text.startswith("<") or stripped_text.startswith("# AGENTS.md instructions"):
        return "skip", ""

    # Pasted files are listed by Codex itself; only a marker is kept.
    if stripped_text.startswith("# Files pasted by the user"):
        return "pasted", "(pasted files)"
    return "request", stripped_text


def read_codex_rollout(path, thread_names):
    """Read a whole Codex rollout into a session dictionary with every turn.

    Parameters:
        path (str): the rollout file (rollout-<date>-<id>.jsonl).
        thread_names (dict): id -> name, from read_codex_thread_names().

    Returns:
        dict: same layout as read_claude_transcript(), with "client": "codex", plus
            "is_subagent" (True for Codex's own helper threads, which the caller skips).
    """
    # The thread id is the last 36 characters of the file name before ".jsonl" (a UUID),
    # e.g. "rollout-2026-03-02T18-55-14-01234567-89ab-cdef-0123-456789abcdef.jsonl".
    session_id = os.path.basename(path)[:-len(".jsonl")][-36:]

    # Values filled while reading.
    working_directory = ""     # folder the thread was started in
    is_subagent = False        # True for Codex's own helper threads (e.g. "Guardian review")
    event_title = ""           # latest name from a "thread_name_updated" event
    first_moment = None        # first record time (Unix seconds)
    last_moment = None         # last record time (Unix seconds)
    turns = []                 # every turn, in order
    current_turn = None        # the turn that the next records belong to

    # Read the rollout line by line (see read_claude_transcript for the `open` arguments).
    with open(path, "r", encoding="utf-8", errors="replace") as rollout_file:
        for line_text in rollout_file:

            # One record per line; unreadable lines give None and are skipped.
            record = parse_json_object(line_text)
            if record is None:
                continue

            # Every record has a timestamp; track the first and last ones.
            moment = iso_to_epoch(record.get("timestamp"))
            if moment is not None:
                if first_moment is None:
                    first_moment = moment
                last_moment = moment

            # `payload` holds the record's data; its "type" says what it is.
            record_type = record.get("type")
            payload = record.get("payload") or {}
            payload_type = payload.get("type")

            # Every record except the start of a new turn extends the running turn's activity.
            starts_new_turn = (record_type == "event_msg" and payload_type == "task_started")
            if current_turn is not None and moment is not None and not starts_new_turn:
                current_turn["end_time"] = moment

            # Session metadata: the real id, the working folder, and whether the thread is a
            # helper that Codex started by itself. A helper thread (for example the "Guardian
            # review" that checks commands before they run) has a `parent_thread_id`, and its
            # `source` is a dictionary such as {"subagent": {"other": "guardian"}} instead of
            # a text such as "vscode". Its "id" is its own id; its "session_id" is the parent's.
            if record_type == "session_meta":
                session_id = payload.get("id") or payload.get("session_id") or session_id
                working_directory = payload.get("cwd") or working_directory
                source_value = payload.get("source")
                if payload.get("parent_thread_id") or (isinstance(source_value, dict) and "subagent" in source_value):
                    is_subagent = True
                continue
            if record_type == "turn_context":
                working_directory = payload.get("cwd") or working_directory
                continue

            # Events: start and end of a turn, typed messages (older Codex), new thread names.
            if record_type == "event_msg":
                if payload_type == "task_started":
                    # A new turn. Its request is collected from two possible sources, kept in
                    # two helper lists and resolved after reading (see "Finish each turn").
                    current_turn = new_turn(moment, "request", "")
                    current_turn["event_prompts"] = []
                    current_turn["item_prompts"] = []
                    turns.append(current_turn)
                elif payload_type == "user_message" and current_turn is not None:
                    # Older Codex versions record exactly what was typed in this event.
                    current_turn["event_prompts"].append(payload.get("message") or "")
                elif payload_type == "task_complete" and current_turn is not None:
                    # End of the turn: `last_agent_message` is the final answer shown to the user.
                    final_answer = payload.get("last_agent_message") or ""
                    if final_answer.strip():
                        current_turn["reply"] = final_answer
                        current_turn["reply_time"] = moment
                elif payload_type == "thread_name_updated":
                    # The thread was (re)named.
                    event_title = payload.get("thread_name") or event_title
                continue

            # Tool calls: only Notion writes are noted. Depending on the tool, Codex records a
            # "function_call" (arguments in "arguments"), a "custom_tool_call" (in "input") or an
            # "mcp_tool_call"; the arguments are a JSON text, parsed into a dictionary when
            # possible (parse_json_object returns None otherwise, which still gives the verb).
            tool_call_types = ("function_call", "custom_tool_call", "mcp_tool_call")
            if record_type == "response_item" and payload_type in tool_call_types and current_turn is not None:
                raw_arguments = payload.get("arguments") or payload.get("input") or ""
                if isinstance(raw_arguments, dict):
                    arguments = raw_arguments
                else:
                    arguments = parse_json_object(str(raw_arguments))
                tool_name = payload.get("name") or payload.get("tool") or ""
                notion_write = describe_notion_write(tool_name, arguments)
                if notion_write:
                    current_turn["notion_writes"].append(notion_write)
                continue

            # Message items: user messages (requests or injected context) and assistant texts.
            if record_type == "response_item" and payload_type == "message" and current_turn is not None:
                text = codex_message_text(payload)
                if payload.get("role") == "user":
                    # Keep requests and pasted-file markers; keep answers to questions apart;
                    # drop injected context.
                    kind, kept_text = classify_codex_user_text(text)
                    if kind == "answers":
                        current_turn["answers"].append(kept_text)
                    elif kind in ("request", "pasted"):
                        current_turn["item_prompts"].append(kept_text)
                elif payload.get("role") == "assistant" and text.strip() and not current_turn["reply"]:
                    # Used only when the turn has no task_complete (stopped or still running);
                    # `fallback_reply` keeps the latest assistant text.
                    current_turn["fallback_reply"] = text
                    current_turn["fallback_time"] = moment

    # Finish each turn: typed messages from events win (they are exactly what was typed);
    # otherwise the filtered message items are used. Several messages are joined with " / ".
    finished_turns = []
    for turn in turns:
        if turn["event_prompts"]:
            turn["prompt"] = " / ".join(turn["event_prompts"])
        else:
            turn["prompt"] = " / ".join(turn["item_prompts"])

        # Without a final answer, the latest assistant text stands in for it.
        # `turn.get(key)` returns None instead of failing when the key is absent.
        if not turn["reply"] and turn.get("fallback_reply"):
            turn["reply"] = turn["fallback_reply"]
            turn["reply_time"] = turn.get("fallback_time")

        # Remove the helper fields so that Claude and Codex turns have the same layout.
        # `dict.pop(key, None)` removes the key if present and does nothing otherwise.
        for helper_key in ("event_prompts", "item_prompts", "fallback_reply", "fallback_time"):
            turn.pop(helper_key, None)

        # Turns with neither a request nor an answer (e.g. an automatic compaction) are dropped.
        if turn["prompt"].strip() or turn["reply"].strip():
            finished_turns.append(turn)

    # The name: Codex's index first, then the latest rename event, then the first request.
    title = thread_names.get(session_id) or event_title
    if not title and finished_turns:
        title = shorten(finished_turns[0]["prompt"], 60)
    if not title:
        title = session_id[:8]

    # DEBUG CHECK: every turn has the keys of new_turn() and nothing else.
    return {"client": "codex", "id": session_id, "title": title, "cwd": working_directory,
            "path": path, "started": first_moment, "last_activity": last_moment,
            "turns": finished_turns, "is_subagent": is_subagent}


# ----------------------------------------------------------------------------------------------
# The Work Hub row of each conversation
# ----------------------------------------------------------------------------------------------

def find_work_hub_task(session_id, title, title_state_dir, task_pattern):
    """Find the Work Hub row (its Task ID, such as "TK-42") that a conversation works on.

    Each conversation works on one row and records it in its first reply with the kit's
    set_conversation_task.py hook helper, which writes the Task ID into the naming hook's state
    file; the hook then also puts it into the conversation's name. With the row known, the
    cleanup compares the conversation with that row's topic card instead of reading the whole
    transcript.

    Parameters:
        session_id (str): Claude Code session id or Codex thread id.
        title (str): the conversation's shown name, from read_claude_transcript() or
            read_codex_rollout().
        title_state_dir (str): folder of the hook's state files ("<session id>.json"), e.g.
            {"title": "...", "final": true, "task": "TK-42", "task_topic": "..."}.
        task_pattern (re.Pattern): from task_id_pattern(prefix).

    Returns:
        tuple (task_id, source): task_id is "<prefix>-<n>", or "" when no row is recorded;
            source is "recorded" (from the state file), "name" (a word of the name) or "" (none).

    Example:
        find_work_hub_task("5799dbc5-e09a-...", "[Mar-03 20:12][ws01][UC] TK-42 Demo analysis",
                           "/home/alex/.local/state/workhub-claude/conversation_titles",
                           task_id_pattern("TK"))  ->  ("TK-42", "recorded")
    """
    # Source 1: the hook's state file. Only Claude Code conversations have one; for a Codex
    # thread the path does not exist and this step is skipped.
    state_path = os.path.join(title_state_dir, session_id + ".json")
    if os.path.isfile(state_path):

        # `json.load` parses the whole (small) file into Python values. A file being rewritten
        # at this moment, or damaged, can fail: OSError = cannot be read, ValueError = not valid
        # JSON (json.JSONDecodeError is a kind of ValueError). It is then ignored.
        try:
            with open(state_path, "r", encoding="utf-8") as state_file:
                state = json.load(state_file)
        except (OSError, ValueError):
            state = None

        # `isinstance(state, dict)` guards against a file that holds a list or a number.
        # `state.get("task") or ""` gives "" when the field is missing or null; `str(...)` turns
        # an unexpected number into text, so the pattern test below cannot fail.
        if isinstance(state, dict):
            recorded_task = str(state.get("task") or "").strip()
            if task_pattern.match(recorded_task):
                return recorded_task, "recorded"

    # Source 2: a word of the shown name that is a Task ID, e.g. "TK-42" in
    # "[Mar-03 20:12][ws01][UC] TK-42 Demo analysis" (names from the hook, /rename, or a Codex
    # thread name). `title.split()` cuts the name at every run of spaces;
    # `.strip(":,;.()[]")` removes those characters from both ends of a word, e.g. "TK-42:" ->
    # "TK-42".
    for word in title.split():
        candidate = word.strip(":,;.()[]")
        if task_pattern.match(candidate):
            return candidate, "name"

    # Neither source names a row.
    return "", ""


def task_id_number(task_id):
    """Return the number of a Task ID, for sorting: "TK-103" -> 103.

    Sorting the texts themselves would put "TK-103" before "TK-24" (character by character,
    "1" < "2"); sorting by the number gives TK-24 before TK-103. Any prefix works.
    """
    # `.rsplit("-", 1)` cuts the text at its LAST dash only: "TK-103" -> ["TK", "103"];
    # `[1]` is the number part, which `int` turns into a number.
    number_text = task_id.rsplit("-", 1)[1]
    return int(number_text)


# ----------------------------------------------------------------------------------------------
# Selecting the turns of the period
# ----------------------------------------------------------------------------------------------

def select_turns_in_window(session, since_epoch, limits):
    """Keep the turns active at or after since_epoch, newest last, with cut texts.

    A turn counts when it started in the period or was still active in it (a long turn that
    started before since_epoch but finished after it is kept).

    Parameters:
        session (dict): from read_claude_transcript() or read_codex_rollout().
        since_epoch (float): start of the period, Unix seconds.
        limits (dict): "prompt_chars", "reply_chars", "last_reply_chars", "answer_chars",
            "max_turns".

    Returns:
        dict or None: a copy of the session whose "turns" are the kept turns (cut), with
            "turns_in_window" (how many turns were in the period) and "turns_total"; None
            when no turn falls in the period.
    """
    # Turns active in the period. `turn["end_time"] or turn["time"] or 0` takes the first
    # value that is not None (0 when both are unknown, which excludes the turn).
    turns_in_window = []
    for turn in session["turns"]:
        last_activity_of_turn = turn["end_time"] or turn["time"] or 0
        if last_activity_of_turn >= since_epoch:
            turns_in_window.append(turn)

    # Nothing happened in the period in this conversation.
    if not turns_in_window:
        return None

    # Keep only the newest `max_turns` turns (all of them with --full, where max_turns is None).
    # `[-n:]` takes the last n items of a list.
    if limits["max_turns"] is None:
        kept_turns = list(turns_in_window)
    else:
        kept_turns = turns_in_window[-limits["max_turns"]:]

    # Cut the texts. The final answer of the newest kept turn gets a larger limit: it usually
    # says where the work stands at the end of the period. With --full every limit is None
    # (no cut). Requests keep their beginning (shorten); answers and replies keep beginning
    # and end (shorten_middle), because decisions and "what is next" sit at the end.
    # `enumerate(list)` yields (position, item) pairs: (0, first turn), (1, second turn), ...
    cut_turns = []
    for position, turn in enumerate(kept_turns):
        is_newest_turn = (position == len(kept_turns) - 1)
        if is_newest_turn:
            reply_limit = limits["last_reply_chars"]
        else:
            reply_limit = limits["reply_chars"]

        # Each set of answers is cut on its own.
        cut_answers = []
        for answer_text in turn["answers"]:
            cut_answers.append(shorten_middle(answer_text, limits["answer_chars"]))

        # A new dictionary per turn, so the full texts of `session` stay untouched.
        # `list(...)` copies the list of Notion writes for the same reason.
        cut_turns.append({"time": turn["time"], "end_time": turn["end_time"], "kind": turn["kind"],
                          "prompt": shorten(turn["prompt"], limits["prompt_chars"]),
                          "answers": cut_answers,
                          "reply": shorten_middle(turn["reply"], reply_limit),
                          "reply_time": turn["reply_time"],
                          "notion_writes": list(turn.get("notion_writes") or [])})

    # A new dictionary: the session's facts plus the kept turns and the counts.
    # `dict(session)` makes a shallow copy (a new dictionary with the same keys and values).
    selected = dict(session)
    selected["turns"] = cut_turns
    selected["turns_in_window"] = len(turns_in_window)
    selected["turns_total"] = len(session["turns"])
    return selected


def session_is_selected(session_id, session_prefixes):
    """Tell whether a conversation passes the --session filter.

    Parameters:
        session_id (str): the conversation's full id.
        session_prefixes (list of str): id prefixes from --session; empty = no filter.

    Returns:
        bool: True when there is no filter or the id starts with one of the prefixes.
    """
    # No --session given: every conversation is selected.
    if not session_prefixes:
        return True

    # Otherwise the id must start with at least one prefix.
    for prefix in session_prefixes:
        if session_id.startswith(prefix):
            return True
    return False


def collect_local_sessions(args, since_epoch, limits):
    """Read this machine's Claude Code transcripts and Codex rollouts of the period.

    Parameters:
        args (argparse.Namespace): parsed arguments, with the values resolved in main():
            claude_projects_dir (str), codex_sessions_dir (str, or None = Codex not read),
            title_state_dir (str), task_pattern (re.Pattern), system_name (str),
            session_prefixes (list of str).
        since_epoch (float): start of the period, Unix seconds.
        limits (dict): text limits, see select_turns_in_window().

    Returns:
        tuple (sessions, counts): the selected sessions (dicts), and {"claude": n, "codex": n}.
    """
    # This machine's name, written into every session it finds.
    this_system = args.system_name

    # Claude Code exports the id of the conversation that runs this script; that conversation
    # is marked, not skipped (its earlier turns can hold real work). "" when not in Claude Code.
    current_claude_session = os.environ.get("CLAUDE_CODE_SESSION_ID", "")

    # The sessions found, and how many of each program.
    sessions = []
    counts = {"claude": 0, "codex": 0}

    # Claude Code: one transcript per conversation, directly inside each project folder
    # (pattern "<projects dir>/<any folder>/<any name>.jsonl"; sub-agent transcripts sit one
    # level deeper and do not match).
    claude_pattern = os.path.join(args.claude_projects_dir, "*", "*.jsonl")
    for path in find_recent_files([claude_pattern], since_epoch):
        session = read_claude_transcript(path)

        # --session: keep only the conversations whose id starts with one of the given texts.
        if not session_is_selected(session["id"], args.session_prefixes):
            continue

        # The turns of the period; None when the file changed but no turn falls in the period.
        selected = select_turns_in_window(session, since_epoch, limits)
        if selected is None:
            continue

        # Where it was found, and whether it is the conversation running this script.
        selected["system"] = this_system
        selected["this_conversation"] = (session["id"] == current_claude_session)

        # Its Work Hub row: Task ID ("" when none) and where it was found (see find_work_hub_task).
        task_id, task_source = find_work_hub_task(session["id"], session["title"], args.title_state_dir,
                                                  args.task_pattern)
        selected["task"] = task_id
        selected["task_source"] = task_source
        sessions.append(selected)
        counts["claude"] += 1

    # Codex is read only when the settings include it (conversations.include_codex).
    if args.codex_sessions_dir is None:
        return sessions, counts

    # Codex keeps its rollouts in dated folders (<sessions dir>/YYYY/MM/DD/), its archived
    # threads in "archived_sessions" next to that folder, and its thread names in
    # "session_index.jsonl" next to it too. `os.path.dirname` of ".../.codex/sessions" is
    # ".../.codex", the folder holding all three.
    codex_root = os.path.dirname(os.path.normpath(args.codex_sessions_dir))
    codex_patterns = [os.path.join(args.codex_sessions_dir, "*", "*", "*", "rollout-*.jsonl"),
                      os.path.join(codex_root, "archived_sessions", "rollout-*.jsonl")]
    thread_names = read_codex_thread_names(codex_root)
    for path in find_recent_files(codex_patterns, since_epoch):
        session = read_codex_rollout(path, thread_names)

        # Codex's own helper threads are not the user's conversations: skipped.
        if session["is_subagent"]:
            continue

        # Same filters as for Claude Code above.
        if not session_is_selected(session["id"], args.session_prefixes):
            continue
        selected = select_turns_in_window(session, since_epoch, limits)
        if selected is None:
            continue

        # Codex does not export its thread id the same way, so no Codex thread is marked.
        selected["system"] = this_system
        selected["this_conversation"] = False

        # Codex threads have no hook state file, so only a Task ID in the thread name is found.
        task_id, task_source = find_work_hub_task(session["id"], session["title"], args.title_state_dir,
                                                  args.task_pattern)
        selected["task"] = task_id
        selected["task_source"] = task_source
        sessions.append(selected)
        counts["codex"] += 1

    # DEBUG CHECK: counts["claude"] + counts["codex"] == len(sessions).
    return sessions, counts


# ----------------------------------------------------------------------------------------------
# Remote machines (clusters and workstations with their own home folder)
# ----------------------------------------------------------------------------------------------

def connection_hint(ssh_alias, cluster_login_enabled):
    """Return what the user should do to open a shared SSH connection to `ssh_alias`.

    Parameters:
        ssh_alias (str): the machine's SSH alias.
        cluster_login_enabled (bool): the setting cluster_login.enabled (the kit's
            `cluster-login` tool is installed).

    Returns:
        str: e.g. "run `cluster-login mycluster` on this machine".
    """
    if cluster_login_enabled:
        return "run `cluster-login %s` on this machine" % ssh_alias
    return "open a shared SSH connection to %s on this machine first (ssh ControlMaster)" % ssh_alias


def unreached_source(host, note):
    """Return the "source" dict of a remote machine that could not be read."""
    return {"system": host["name"], "reached": False, "claude": 0, "codex": 0, "note": note}


def collect_remote_sessions(host, since_epoch, args):
    """Run this script on a remote machine through SSH, without ever prompting.

    Parameters:
        host (dict): {"name", "ssh_alias", "kind"} from resolve_hosts(); kind is "cluster" or
            "workstation".
        since_epoch (float): start of the period, Unix seconds.
        args (argparse.Namespace): parsed arguments (limits, --session, --full, the time zone,
            the Task ID prefix, whether Codex is read, cluster_login_enabled).

    Returns:
        tuple (sessions, source): the machine's selected sessions, and a dict describing the
            source: {"system", "reached", "claude", "codex", "note"}.
    """
    # Step 1: `ssh -O check <alias>` asks the local "ControlMaster" process (a shared connection
    # opened earlier, e.g. by cluster-login) whether it is alive. Exit status 0 = open. This
    # never starts a new login, so it can never trigger a password or two-factor prompt.
    try:
        check = subprocess.run(["ssh", "-O", "check", host["ssh_alias"]], capture_output=True, text=True,
                               timeout=15, stdin=subprocess.DEVNULL)
        connection_open = (check.returncode == 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        return [], unreached_source(host, "ssh check failed (%s)" % error)

    # A cluster is read ONLY through an open shared connection (its login needs the user's
    # two-factor approval). A workstation may also be read with SSH keys alone: BatchMode below
    # forbids any prompt, so a missing key just fails fast.
    if not connection_open and host["kind"] == "cluster":
        return [], unreached_source(host, "no open shared SSH connection; %s"
                                    % connection_hint(host["ssh_alias"], args.cluster_login_enabled))

    # Step 2: read this script's own source, to send it to the remote python3 on its standard
    # input (`python3 -` runs the program it reads from standard input).
    with open(os.path.abspath(__file__), "r", encoding="utf-8") as source_file:
        source_code = source_file.read()

    # Step 3: the remote command. "@<seconds>" passes the exact start of the period;
    # --hosts none stops the remote copy from contacting other machines; --json-only makes it
    # print JSON instead of writing files; --system-name labels its sessions with the name the
    # settings give this machine; the Task ID prefix and the time zone are this machine's.
    # The remote copy finds its own folders (its own settings file, else the usual defaults).
    remote_arguments = ["python3", "-", "--json-only", "--hosts", "none",
                        "--since", "@%d" % int(since_epoch),
                        "--system-name", host["name"],
                        "--task-id-prefix", args.task_id_prefix,
                        "--prompt-chars", str(args.prompt_chars),
                        "--reply-chars", str(args.reply_chars),
                        "--last-reply-chars", str(args.last_reply_chars),
                        "--answer-chars", str(args.answer_chars),
                        "--max-turns", str(args.max_turns),
                        "--timezone", args.timezone]

    # Codex: when this machine does not read Codex, the remote copy does not either.
    if args.codex_sessions_dir is None:
        remote_arguments += ["--codex-sessions-dir", "none"]

    # --session and --full are passed on only when given.
    if args.session:
        remote_arguments += ["--session", args.session]
    if args.full:
        remote_arguments += ["--full"]

    # ssh passes the remote command to the remote shell as ONE text, so every argument is
    # quoted with `shlex.quote` (e.g. a time zone or a name with unusual characters stays one
    # argument). Plain words stay as they are: shlex.quote("--hosts") == "--hosts".
    quoted_arguments = []
    for argument in remote_arguments:
        quoted_arguments.append(shlex.quote(argument))
    remote_command = " ".join(quoted_arguments)

    # `BatchMode=yes` forbids any password or two-factor prompt; `ConnectTimeout=10` gives up
    # after 10 seconds. `input=source_code` feeds the script to the remote `python3 -` through
    # standard input.
    ssh_command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host["ssh_alias"], remote_command]
    try:
        completed = subprocess.run(ssh_command, input=source_code, capture_output=True, text=True,
                                   timeout=REMOTE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as error:
        return [], unreached_source(host, "remote run failed (%s)" % error)

    # Step 4: the JSON is the last line of standard output that starts with "{" (a login
    # message, if any, comes before it).
    json_line = ""
    for output_line in completed.stdout.splitlines():
        if output_line.startswith("{"):
            json_line = output_line

    # Parse it; no JSON line (or unreadable JSON) means the remote run failed, and its error
    # message (cut to 200 characters) explains why.
    remote_report = None
    if json_line:
        remote_report = parse_json_object(json_line)
    if remote_report is None:
        error_text = shorten(completed.stderr, 200) or "no JSON output"
        return [], unreached_source(host, "remote run failed: %s" % error_text)

    # Step 5: the remote copy already filled "system" for each session; `or []` and `or {}`
    # guard against missing keys.
    sessions = remote_report.get("sessions") or []
    counts = remote_report.get("counts") or {}
    note = ""
    if not connection_open:
        note = "read with SSH keys (no shared connection)"
    return sessions, {"system": host["name"], "reached": True,
                      "claude": counts.get("claude", 0), "codex": counts.get("codex", 0), "note": note}


# ----------------------------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------------------------

def format_digest(report, timezone):
    """Build the text of digest.txt.

    Parameters:
        report (dict): {"since", "collected", "system", "sources", "sessions"}.
        timezone (ZoneInfo): zone for every printed time.

    Returns:
        str: the digest.
    """
    # The digest is built as a list of lines, joined at the end.
    lines = []

    # Count the conversations of each program.
    total_claude = 0
    total_codex = 0
    for session in report["sessions"]:
        if session["client"] == "claude":
            total_claude += 1
        else:
            total_codex += 1

    # Header: the period, when and where the digest was made, and how many conversations.
    # `timezone.key` is the zone's name, e.g. "Europe/Paris".
    lines.append("Conversations with activity since %s (%s)"
                 % (format_epoch(report["since"], timezone), timezone.key))
    lines.append("Collected %s on %s · %d conversation(s): Claude Code %d, Codex %d"
                 % (format_epoch(report["collected"], timezone), report["system"],
                    len(report["sessions"]), total_claude, total_codex))

    # Sources: what was read, and what could not be reached (with the fix).
    lines.append("Sources:")
    for source in report["sources"]:
        if source["reached"]:
            note_text = ""
            if source["note"]:
                note_text = " (" + source["note"] + ")"
            lines.append("  %s: Claude Code %d, Codex %d%s"
                         % (source["system"], source["claude"], source["codex"], note_text))
        else:
            lines.append("  %s: NOT REACHED - %s" % (source["system"], source["note"]))
    lines.append("")

    # Index by Work Hub row. Conversations with a Task ID are compared with that row's topic card;
    # those without one are read in full by the cleanup. `session.get("task") or ""` also
    # accepts sessions from an older copy of this script on a cluster, which have no "task" key.
    sessions_by_task = {}
    sessions_without_task = []
    for session in report["sessions"]:
        task_id = session.get("task") or ""
        if not task_id:
            sessions_without_task.append(session)
            continue
        if task_id not in sessions_by_task:
            sessions_by_task[task_id] = []
        sessions_by_task[task_id].append(session)

    # `sorted(..., key=task_id_number)` orders the Task IDs by their number (TK-9 before
    # TK-42); `key` names the function applied to each item before comparing.
    task_ids_in_order = sorted(sessions_by_task.keys(), key=task_id_number)
    lines.append("Work Hub rows of these conversations (%d row(s)):" % len(task_ids_in_order))
    if not task_ids_in_order:
        lines.append("  (none)")
    for task_id in task_ids_in_order:
        for session in sessions_by_task[task_id]:
            lines.append("  %s: [%s %s] %s · %s" % (task_id, session["client"], session["id"][:8],
                                                    session["system"], session["title"]))
    lines.append("No Work Hub row recorded (%d conversation(s)):" % len(sessions_without_task))
    if not sessions_without_task:
        lines.append("  (none)")
    for session in sessions_without_task:
        lines.append("  [%s %s] %s · %s" % (session["client"], session["id"][:8],
                                            session["system"], session["title"]))
    lines.append("")

    # The label printed before each kind of turn.
    labels = {"request": "ASK", "notice": "JOB NOTICE", "command": "COMMAND"}

    # One block per conversation, in the order of report["sessions"] (newest activity first).
    for session in report["sessions"]:
        # Title line: program, short id (first 8 characters), name, and the current-conversation mark.
        marker = ""
        if session.get("this_conversation"):
            marker = "  (this conversation: its newest turn is the current request)"
        lines.append("== [%s %s] %s%s" % (session["client"], session["id"][:8], session["title"], marker))

        # Its Work Hub row, and how it was found ("recorded" = by the conversation itself).
        if session.get("task"):
            lines.append("   Work Hub row: %s (%s)" % (session["task"], session.get("task_source") or "?"))
        else:
            lines.append("   Work Hub row: none recorded")

        # Where it lives and when it was active; the folder is written as system:/path.
        lines.append("   %s:%s · started %s · last activity %s · %d of %d turn(s) in the period"
                     % (session["system"], session["cwd"] or "?", format_epoch(session["started"], timezone),
                        format_epoch(session["last_activity"], timezone), session["turns_in_window"],
                        session["turns_total"]))
        lines.append("   transcript: %s:%s" % (session["system"], session["path"]))

        # Turns of the period not shown because of --max-turns.
        hidden_turns = session["turns_in_window"] - len(session["turns"])
        if hidden_turns > 0:
            lines.append("   (%d earlier turn(s) of the period not shown: rerun with --session %s)"
                         % (hidden_turns, session["id"][:8]))

        # Each turn: when, what was asked, the answers given to questions, the final reply.
        # `labels.get(kind, "ASK")` falls back to "ASK" for an unexpected kind.
        for turn in session["turns"]:
            lines.append("   %s %s: %s" % (format_epoch(turn["time"], timezone),
                                           labels.get(turn["kind"], "ASK"), turn["prompt"] or "(no text)"))
            for answer_text in turn["answers"]:
                lines.append("        ANSWERED: %s" % answer_text)

            # Notion changes made during the turn: at most 12 listed, then a count of the rest.
            notion_writes = turn.get("notion_writes") or []
            if notion_writes:
                listed_text = "; ".join(notion_writes[:12])
                if len(notion_writes) > 12:
                    listed_text += "; … (+%d more)" % (len(notion_writes) - 12)
                lines.append("        NOTION WRITES: %s" % listed_text)
            if turn["reply"]:
                lines.append("        REPLY (%s): %s" % (format_epoch(turn["reply_time"], timezone), turn["reply"]))
            else:
                lines.append("        REPLY: (none yet)")
        lines.append("")

    # One text with a line break between lines and at the end.
    return "\n".join(lines) + "\n"


def write_outputs(report, digest_text, output_dir, overwrite):
    """Write digest.txt and sessions.json into output_dir.

    Parameters:
        report (dict): the report (written as JSON).
        digest_text (str): the digest.
        output_dir (str): destination folder (created if missing).
        overwrite (bool): allow replacing existing files.

    Returns:
        tuple (digest_path, json_path).

    Raises:
        FileExistsError: when a file exists and overwrite is False.
    """
    # Paths of the two outputs.
    digest_path = os.path.join(output_dir, "digest.txt")
    json_path = os.path.join(output_dir, "sessions.json")

    # Existing outputs are never replaced silently.
    for path in (digest_path, json_path):
        if os.path.exists(path) and not overwrite:
            raise FileExistsError("%s exists; use --overwrite or another --output-dir" % path)

    # `os.makedirs(..., exist_ok=True)` creates the folder (and parents) if needed.
    os.makedirs(output_dir, exist_ok=True)

    # Mode "w" creates or replaces the file. `json.dump(..., indent=1)` writes readable JSON.
    with open(digest_path, "w", encoding="utf-8") as digest_file:
        digest_file.write(digest_text)
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(report, json_file, ensure_ascii=False, indent=1)
    return digest_path, json_path


# ----------------------------------------------------------------------------------------------
# Command line and main workflow
# ----------------------------------------------------------------------------------------------

def build_parser():
    """Build the command-line parser.

    Returns:
        argparse.ArgumentParser: the parser.
    """
    parser = argparse.ArgumentParser(
        description="Digest of the Claude Code and Codex conversations active since a moment "
                    "(requests, answers to questions, final replies). Folders, machines, the "
                    "time zone and the Task ID prefix come from the workhub-claude settings.")

    # The period (required).
    parser.add_argument("--since", required=True,
                        help="start of the period: ISO 8601 (2026-03-02T14:41+00:00), a day "
                             "(2026-03-02), hours or days before now (36h, 2d), or @<unix seconds>")

    # Outputs. `dest="print_digest"` stores --print under that name (`print` is a Python function).
    parser.add_argument("-o", "--output-dir",
                        help="folder for digest.txt and sessions.json (required unless --json-only)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace digest.txt and sessions.json if they exist")
    parser.add_argument("--print", dest="print_digest", action="store_true",
                        help="also print the digest on the screen")

    # What to read: which other machines, which conversation.
    parser.add_argument("--hosts", default="auto",
                        help="other machines to read, comma-separated names or SSH aliases from the "
                             "settings (machines.clusters, machines.workstations); 'none' for this "
                             "machine only; 'auto' (default) = every listed machine that does not share "
                             "this machine's home folder, or none when running on a cluster")
    parser.add_argument("--session", default="",
                        help="only the conversations whose id starts with one of these texts, "
                             "comma-separated (e.g. 2d805a22 or 2d805a22,58fe4496)")
    parser.add_argument("--full", action="store_true",
                        help="no text limits and every turn of the period (for reading one or two "
                             "conversations word for word, with --session)")

    # How much text to keep. `type=int` makes argparse convert the typed text into a number
    # (and refuse anything that is not one).
    parser.add_argument("--prompt-chars", type=int, default=300, help="max characters per request (default 300)")
    parser.add_argument("--reply-chars", type=int, default=500, help="max characters per reply (default 500)")
    parser.add_argument("--last-reply-chars", type=int, default=1500,
                        help="max characters for the newest reply of each conversation (default 1500)")
    parser.add_argument("--answer-chars", type=int, default=600,
                        help="max characters per set of answers to questions (default 600)")
    parser.add_argument("--max-turns", type=int, default=10,
                        help="newest turns shown per conversation (default 10)")

    # Values that normally come from the settings. They exist (1) for the copy of this script
    # that runs on a remote machine, which receives them from the calling machine, and (2) for
    # the tests. `default=None` means "not given: use the settings".
    parser.add_argument("--claude-projects-dir", default=None,
                        help="Claude Code projects folder (default: conversations.claude_projects_dir, "
                             "else $CLAUDE_CONFIG_DIR/projects or ~/.claude/projects)")
    parser.add_argument("--codex-sessions-dir", default=None,
                        help="Codex sessions folder, or 'none' to skip Codex (default: "
                             "conversations.codex_sessions_dir when conversations.include_codex is true)")
    parser.add_argument("--title-state-dir", default=None,
                        help="naming hook state folder with each conversation's Task ID "
                             "(default: <paths.state_dir>/conversation_titles)")
    parser.add_argument("--task-id-prefix", default=None,
                        help="Work Hub Task ID prefix, e.g. TK for TK-42 (default: notion.task_id_prefix, else TK)")
    parser.add_argument("--system-name", default=None,
                        help="name of this machine in the digest (default: the settings' system name)")
    parser.add_argument("--timezone", default=None,
                        help="IANA time zone for reading and printing times (default: owner.time_zone, else UTC)")

    # The internal mode used when this script runs itself on a remote machine.
    parser.add_argument("--json-only", action="store_true",
                        help="internal: print the JSON report on standard output, write no file")
    return parser


def load_settings_or_exit(args):
    """Return (reader, config): the settings reader module (or None) and the settings dict.

    Parameters:
        args (argparse.Namespace): parsed arguments (only args.json_only is used).

    Returns:
        tuple (module or None, dict).

    Exits with status 2 when this is a normal run (not --json-only) and the settings file is
    missing or unreadable, after telling the user how to create it.
    """
    reader = load_settings_reader()

    # Remote run through `python3 -`: no reader next to the script; the machine's own settings
    # file is read directly when there is one ({} otherwise).
    if reader is None:
        if not args.json_only:
            print("error: workhub_config.py was not found next to this script; run the script "
                  "from the skill's scripts folder", file=sys.stderr)
            sys.exit(2)
        return None, read_settings_file_directly()

    # Normal run: the settings are required. `required=not args.json_only` makes them optional
    # in the internal JSON mode (a machine without the kit set up then uses the defaults).
    try:
        config = reader.load_config(required=not args.json_only)
    except reader.ConfigError as error:
        print("error: %s" % error, file=sys.stderr)
        print("Say \"set up my work hub\" to create the settings (skill work-hub-setup).", file=sys.stderr)
        sys.exit(2)
    return reader, config


def resolve_folders_and_names(args, reader, config, parser):
    """Fill the arguments left at None from the settings, then from the usual defaults.

    Sets on `args`: claude_projects_dir, codex_sessions_dir (None = Codex not read),
    title_state_dir, task_id_prefix, task_pattern, timezone, system_name,
    cluster_login_enabled.

    Parameters:
        args (argparse.Namespace): parsed arguments; changed in place.
        reader (module or None): workhub_config, or None on a remote run.
        config (dict): the settings ({} when there are none).
        parser (argparse.ArgumentParser): used to report an unusable --task-id-prefix.
    """
    # Claude Code projects folder: argument, then settings, then $CLAUDE_CONFIG_DIR/projects,
    # then ~/.claude/projects. `a or b` gives a when it is a non-empty text, else b.
    claude_folder = args.claude_projects_dir or settings_value(config, "conversations", "claude_projects_dir")
    if not claude_folder:
        claude_config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join("~", ".claude")
        claude_folder = os.path.join(claude_config_dir, "projects")
    args.claude_projects_dir = expand_folder(claude_folder)

    # Codex sessions folder. "none" (any case) = Codex not read. Not given: with settings, Codex
    # is read only when conversations.include_codex is true; without settings (a remote machine
    # without the kit), Codex is read when its usual folder exists.
    codex_folder = args.codex_sessions_dir
    if codex_folder is not None and codex_folder.lower() == "none":
        codex_folder = None
    elif codex_folder is None:
        codex_home = os.environ.get("CODEX_HOME") or os.path.join("~", ".codex")
        default_codex_folder = os.path.join(codex_home, "sessions")
        if config:
            if settings_value(config, "conversations", "include_codex") is True:
                codex_folder = (settings_value(config, "conversations", "codex_sessions_dir")
                                or default_codex_folder)
        else:
            codex_folder = default_codex_folder
    if codex_folder is not None:
        codex_folder = expand_folder(codex_folder)
    args.codex_sessions_dir = codex_folder

    # The naming hook's state folder. The reader's state_dir() creates it (mode 700) when it is
    # missing; on a remote run the folder is only computed, never created.
    if args.title_state_dir:
        args.title_state_dir = expand_folder(args.title_state_dir)
    elif reader is not None:
        args.title_state_dir = reader.state_dir(config, TITLE_STATE_SUBFOLDER)
    else:
        state_folder = settings_value(config, "paths", "state_dir") or FALLBACK_STATE_DIR
        args.title_state_dir = os.path.join(expand_folder(state_folder), TITLE_STATE_SUBFOLDER)

    # Task ID prefix and its pattern. A prefix with a space would never match a Task ID.
    prefix = args.task_id_prefix or settings_value(config, "notion", "task_id_prefix") or DEFAULT_TASK_ID_PREFIX
    prefix = str(prefix)
    if not prefix or prefix.split() != [prefix]:
        parser.error("the Task ID prefix must be one word without spaces, e.g. TK")
    args.task_id_prefix = prefix
    args.task_pattern = task_id_pattern(prefix)

    # Time zone name (checked later, when ZoneInfo reads it).
    args.timezone = args.timezone or settings_value(config, "owner", "time_zone") or "UTC"

    # This machine's name.
    if not args.system_name:
        if reader is not None:
            args.system_name = reader.system_name(config)
        else:
            args.system_name = fallback_system_name()

    # Whether the kit's cluster-login tool is installed (only changes the hint texts).
    args.cluster_login_enabled = (settings_value(config, "cluster_login", "enabled") is True)


def machine_entries(reader, config):
    """List the machines of the settings as {"name", "ssh_alias", "kind", "shares_home"} dicts.

    Parameters:
        reader (module or None): workhub_config; None gives [] (remote runs read no machine list).
        config (dict): the settings.

    Returns:
        list of dict: clusters first, then workstations. ssh_alias defaults to the name.
    """
    entries = []
    if reader is None:
        return entries

    # Clusters, then workstations. `machine.get("ssh_alias") or name` uses the name as alias
    # when no alias is given. Entries without a name are skipped.
    for kind, machines in (("cluster", reader.clusters(config)), ("workstation", reader.workstations(config))):
        for machine in machines:
            name = str(machine.get("name") or "").strip().lower()
            if not name:
                continue
            entries.append({"name": name,
                            "ssh_alias": str(machine.get("ssh_alias") or name).strip(),
                            "kind": kind,
                            "shares_home": machine.get("shares_home_with_this_machine") is True})
    return entries


def resolve_hosts(hosts_argument, reader, config, this_system):
    """Turn --hosts into the list of other machines to read.

    Parameters:
        hosts_argument (str): "auto", "none" or names / SSH aliases such as "mycluster,ws02".
        reader (module or None): workhub_config, or None on a remote run.
        config (dict): the settings.
        this_system (str): this machine's name (never contacted through SSH).

    Returns:
        list of dict: {"name", "ssh_alias", "kind"} with kind "cluster" or "workstation".
    """
    known = machine_entries(reader, config)
    cluster_names = []
    for entry in known:
        if entry["kind"] == "cluster":
            cluster_names.append(entry["name"])

    hosts = []
    if hosts_argument == "none":
        return hosts

    if hosts_argument == "auto":
        # On a cluster there are no shared connections to the other machines: this machine only.
        if this_system in cluster_names:
            return hosts
        # Elsewhere: every listed machine with its own home folder (a machine that shares this
        # home folder already shows its conversations in the local folders).
        for entry in known:
            if not entry["shares_home"]:
                hosts.append(entry)
    else:
        # "mycluster, ws02" -> ["mycluster", "ws02"]: cut at commas, strip spaces, drop empty
        # parts. A word matching a listed name or alias takes that entry; an unknown word is
        # treated as a cluster alias (read only through an already open connection).
        for part in hosts_argument.split(","):
            word = part.strip()
            if not word:
                continue
            match = None
            for entry in known:
                if word.lower() == entry["name"] or word == entry["ssh_alias"]:
                    match = entry
                    break
            if match is None:
                match = {"name": word.lower(), "ssh_alias": word, "kind": "cluster", "shares_home": False}
            hosts.append(match)

    # Never contact this machine through SSH (it is read directly), and never one machine twice.
    other_hosts = []
    seen_names = set()
    for host in hosts:
        if host["name"] == this_system or host["name"] in seen_names:
            continue
        seen_names.add(host["name"])
        other_hosts.append({"name": host["name"], "ssh_alias": host["ssh_alias"], "kind": host["kind"]})
    return other_hosts


def main():
    """Collect the conversations of the period and write (or print) the digest."""
    # ---- Stage 1: read and check the command line and the settings --------------------------
    # argparse exits with status 2 on invalid arguments; `parser.error` does the same with a
    # message, for the checks argparse cannot express by itself.
    parser = build_parser()
    args = parser.parse_args()
    if not args.json_only and not args.output_dir:
        parser.error("--output-dir is required (unless --json-only)")
    if args.max_turns < 1:
        parser.error("--max-turns must be at least 1")

    # The settings (required for a normal run; status 2 when missing), then every folder and
    # name the run needs. DEBUG CHECK: args.claude_projects_dir, args.title_state_dir.
    reader, config = load_settings_or_exit(args)
    resolve_folders_and_names(args, reader, config, parser)

    # --session: "2d805a22,58fe4496" -> ["2d805a22", "58fe4496"]. Each prefix must contain only
    # hexadecimal digits and dashes, as session ids do. The pattern reads: `^` start,
    # `[0-9a-fA-F-]+` one or more of those characters, `$` end.
    args.session_prefixes = []
    for part in args.session.split(","):
        if part.strip():
            if not re.match(r"^[0-9a-fA-F-]+$", part.strip()):
                parser.error("--session takes conversation id prefixes such as 2d805a22")
            args.session_prefixes.append(part.strip())

    # Existing outputs are never replaced silently. Checked now, before any reading or SSH,
    # so that a forgotten --overwrite costs nothing (write_outputs checks again at the end).
    if not args.json_only and not args.overwrite:
        for file_name in ("digest.txt", "sessions.json"):
            existing_path = os.path.join(args.output_dir, file_name)
            if os.path.exists(existing_path):
                print("error: %s exists; use --overwrite or another --output-dir" % existing_path,
                      file=sys.stderr)
                sys.exit(1)

    # The time zone and the start of the period; unreadable values stop with status 1.
    try:
        timezone = ZoneInfo(args.timezone)
        since_epoch = parse_since(args.since, timezone)
    except Exception as error:  # unknown zone name, or unreadable --since
        print("error: %s" % error, file=sys.stderr)
        sys.exit(1)

    # The text limits, gathered in one dictionary passed to the selection step. With --full,
    # every limit is None, which select_turns_in_window() reads as "no limit".
    if args.full:
        limits = {"prompt_chars": None, "reply_chars": None, "last_reply_chars": None,
                  "answer_chars": None, "max_turns": None}
    else:
        limits = {"prompt_chars": args.prompt_chars, "reply_chars": args.reply_chars,
                  "last_reply_chars": args.last_reply_chars, "answer_chars": args.answer_chars,
                  "max_turns": args.max_turns}

    # When the evidence was collected. The cleanup records it as the end of its window, so the
    # next cleanup starts exactly here. DEBUG CHECK: close to the current time.
    collected_epoch = datetime.datetime.now(timezone).timestamp()

    # ---- Stage 2: this machine ---------------------------------------------------------------
    # The local conversations of the period, and a "source" line describing this machine.
    sessions, local_counts = collect_local_sessions(args, since_epoch, limits)
    sources = [{"system": args.system_name, "reached": True, "claude": local_counts["claude"],
                "codex": local_counts["codex"], "note": "this machine"}]

    # Internal mode used on remote machines: print the JSON on one line and stop. The calling
    # machine reads that line (see collect_remote_sessions).
    if args.json_only:
        print(json.dumps({"system": args.system_name, "counts": local_counts, "sessions": sessions},
                         ensure_ascii=False))
        return

    # ---- Stage 3: other machines, never with a login prompt ----------------------------------
    # One machine after the other; an unreachable one adds a "NOT REACHED" source, no session.
    for host in resolve_hosts(args.hosts, reader, config, args.system_name):
        remote_sessions, source = collect_remote_sessions(host, since_epoch, args)
        sessions.extend(remote_sessions)
        sources.append(source)

    # ---- Stage 4: order, write and report ----------------------------------------------------
    # Newest activity first. `key=lambda session: ...` sorts by the value that the small unnamed
    # function returns for each session (its last activity); `or 0` puts conversations with an
    # unknown time last. Equivalent named function: def last_activity_of(s): return s["last_activity"] or 0
    sessions.sort(key=lambda session: session["last_activity"] or 0, reverse=True)

    # The full report, its text version, and both files.
    report = {"since": since_epoch, "collected": collected_epoch, "system": args.system_name,
              "timezone": args.timezone, "task_id_prefix": args.task_id_prefix,
              "sources": sources, "sessions": sessions}
    digest_text = format_digest(report, timezone)
    try:
        digest_path, json_path = write_outputs(report, digest_text, args.output_dir, args.overwrite)
    except (FileExistsError, OSError) as error:
        print("error: %s" % error, file=sys.stderr)
        sys.exit(1)

    # The window end to record with `cleanup_log.py add --window-end`: the moment collection
    # started, written as ISO 8601 with its offset, e.g. "2026-03-02T11:58+01:00".
    collected_text = datetime.datetime.fromtimestamp(collected_epoch, timezone).isoformat(timespec="minutes")

    # Screen output: the whole digest with --print, then a short summary.
    if args.print_digest:
        print(digest_text)
    print("%d conversation(s) with activity since %s" % (len(sessions), format_epoch(since_epoch, timezone)))
    for source in sources:
        if not source["reached"]:
            print("not reached: %s - %s" % (source["system"], source["note"]))
    print("digest: %s (%d characters)" % (digest_path, len(digest_text)))
    print("data:   %s" % json_path)

    # The window end matters only for the main digest of a cleanup. A detail run (--session)
    # would print a later moment, which must not replace it, so it prints a reminder instead.
    if args.session_prefixes:
        print("(detail run: keep the window end printed by the main digest)")
    else:
        print("window end for cleanup_log.py: --window-end %s" % collected_text)


# Run main() only when the file is executed as a program (or fed to `python3 -`), not when it
# is imported by the tests.
if __name__ == "__main__":
    main()
