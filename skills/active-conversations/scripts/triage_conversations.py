#!/usr/bin/env python3
"""Sort the open Claude Code conversations into warm, look closer, redundant and finished.

PURPOSE
    Someone who works with many Claude Code windows at once asks "which conversations are warm?":
    which ones still have work to do, and which ones can be closed because another conversation
    took their place (redundant) or because their work is over (finished).
    This script makes a first, rule-based proposal for every open conversation, with the reason
    for it, so that the conversation running the skill only has to confirm or correct it.
    The proposal is a starting point: the final answer is always written by the model after it
    has read the reasons (see SKILL.md, "Warm, redundant or finished").

INPUTS
    1. conversations.json, written by collect_conversations.py of this skill (schema 2). Only the
       conversations that are open now are triaged (the default collector mode collects just those).
    2. The orchestrator's NOW.md (optional; only when the settings turn the orchestrator on,
       workflow.orchestrator.enabled = true). Its "## Active rows" table lists the Work Hub rows
       (Task IDs such as TK-42) that still have work. Default path:
       <workflow.orchestrator.folder>/NOW.md; override with --now-file.
       If the file is missing (for example on a cluster), the row rules are skipped.
    3. The conversation-title state folder (optional), default
       <paths.state_dir>/conversation_titles (the folder the kit's naming hooks write).
       Its file <session id>.json holds {"task": "TK-42", ...} when a conversation recorded its
       row with set_conversation_task.py. Override with --titles-dir.
    4. The transcripts of the conversations of this machine's site (path stored in
       conversations.json), to read the full last reply. Other sites use the shortened reply
       stored in the digest.
    5. The workhub-claude settings file (~/.config/workhub-claude/config.json, or the file named by
       $WORKHUB_CLAUDE_CONFIG), read through workhub_config.py in this folder. Keys used:
       notion.task_id_prefix (default "TK"), workflow.orchestrator.enabled and .folder,
       paths.state_dir.

OUTPUTS
    - A grouped list printed to standard output (the text the model reads).
    - With -o PATH: the same result as JSON (one entry per conversation: label, reason, notes).
    Nothing else is written; no conversation, file or Notion page is changed.

RULES (the first one that matches decides the label)
    1. this request   the conversation running this script (--self, else found through the
                      parent processes, else the one whose shell ran the collector)
    2. orchestrator   only when the orchestrator is on in the settings: the newest conversation
                      with [ORCH] in its name is warm; older ones are redundant
    3. same row       an older conversation whose Task ID a newer open conversation also has is redundant
    4. busy           Claude is working in it now: warm
    5. background     a background command it started still runs: warm
    6. active row     its Task ID is in NOW.md's active rows: warm
    7. row not listed its Task ID is not in NOW.md's active rows: finished if the last reply says the
                      work is done, otherwise look closer
    8. question       the last reply ends with a question to the user: look closer
    9. done marker    the last reply says the work is over (for example "you can /clear"): finished
    10. stale         no message for more than --stale-days days (default 2): finished
    11. otherwise     look closer
    Notes never change the label: open in several windows (any label), a background task that
    died with an earlier window (warm and look closer only), context compacted (warm only).

DEPENDENCIES
    Python >= 3.8, standard library only, plus workhub_config.py from this folder (read only in
    main(), so that the tests can import the rules without a settings file).

EXAMPLE
    python3 <skill folder>/scripts/collect_conversations.py -o /tmp/me/conversations.json
    python3 <skill folder>/scripts/triage_conversations.py /tmp/me/conversations.json \
        -o /tmp/me/triage.json

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# `argparse` is Python's standard command-line parser: it reads the options below from
# `sys.argv`, prints --help, and stops with exit status 2 when an option is wrong.
import argparse

# `json` converts JSON text (conversations.json, the title state files) into Python
# dictionaries and lists, and writes the result back as JSON text.
import json

# `os` and `os.path` build file paths and test whether a file exists.
import os

# `re` is Python's regular-expression module; it finds Task IDs and web links in text.
import re

# `socket.gethostname()` gives this machine's name, to match the collector's process list.
import socket

# `sys` gives access to standard error, where warnings go so that they do not mix with the list.
import sys

# `time` gives the current time in seconds since 1970 (the unit used by conversations.json).
import time


# ---------------------------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------------------------

# The skill's name, used for its folder under the state folder.
SKILL_NAME = "active-conversations"

# The Task ID prefix used when the settings name none (notion.task_id_prefix).
DEFAULT_TASK_ID_PREFIX = "TK"

# The labels, in the order in which the printed list shows them. Each key is the short label
# stored in the JSON output; each value is the heading printed for that group.
# Example: a conversation labelled "warm" is printed under "WARM (still has work)".
LABEL_HEADINGS = {
    "warm": "WARM (still has work)",
    "check": "LOOK CLOSER (read its last reply, then decide)",
    "redundant": "REDUNDANT (another conversation took its place)",
    "finished": "FINISHED (nothing left to do)",
    "self": "THIS REQUEST",
}

# Phrases that, in a last reply, mean the work of that conversation is over. They are compared
# in lower case, so "Marked Done" and "marked done" both match.
# Example: "This task is finished, so you can `/clear` before the next topic." matches twice.
DONE_MARKERS = (
    "/clear",
    "task is finished",
    "is now marked done",
    "marked done",
    "nothing more to do",
    "nothing is running or booked for this task",
)

# A notice that Claude Code writes when a background task belonged to an earlier window of
# the same conversation and stopped when that window closed.
DIED_WITH_WINDOW_MARKER = "didn't finish before the previous session ended"

# How many characters at the end of the last reply are searched for a closing question.
# 600 characters is about the last paragraph; a question further up was usually answered later.
QUESTION_WINDOW_CHARACTERS = 600

# How many bytes at the end of a transcript are read to find the last reply. Transcripts can be
# tens of megabytes; the last reply is always near the end, so reading only the tail is faster.
TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024

# Regular expression for a web link: "https?" is "http" with an optional "s", "://" is literal,
# and "\S+" is one or more characters that are not white space (the rest of the link).
# Links are removed before looking for a question mark, because Notion links contain "?pvs=204".
WEB_LINK_PATTERN = re.compile(r"https?://\S+")


# ---------------------------------------------------------------------------------------------
# Task ID patterns (built from the prefix in the settings, never hard-coded)
# ---------------------------------------------------------------------------------------------

def task_id_patterns(prefix):
    """Build the three regular expressions that find Task IDs with the given prefix.

    Parameters
        prefix (str): the Task ID prefix, for example "TK" (IDs "TK-42") or "JOB" ("JOB-7").
    Returns
        dict with the prefix and three compiled patterns:
            "prefix":   the prefix itself, used to rebuild normalised IDs ("TK" + "-" + "42");
            "any":      an ID anywhere in a text, with or without the hyphen ("TK-42", "TK42");
            "exact":    a whole table cell that is exactly one ID ("TK-42");
            "continue": the phrase "continue TK-42" that starts a conversation on a row.
    Example
        patterns = task_id_patterns("TK")
        patterns["any"].search("notes TK42 draft").group(1)  ->  "42"
    """
    # `re.escape` puts a backslash before every character that has a special meaning in a
    # regular expression, so a prefix such as "A.B" matches only the text "A.B" (the "." would
    # otherwise mean "any character").
    escaped_prefix = re.escape(prefix)
    # Pattern parts, used in all three:
    #   \b      a word boundary, so that "XTK-1" does not match;
    #   -?      an optional hyphen ("?" means zero or one of the character before it);
    #   (\d+)   one or more digits, captured as group 1 (the row number);
    #   \b      another word boundary, so that "TK-12a" does not match.
    # re.IGNORECASE also accepts "tk-42".
    any_pattern = re.compile(r"\b" + escaped_prefix + r"-?(\d+)\b", re.IGNORECASE)
    # "^" and "$" anchor the pattern at the start and end of the text: the whole cell is the ID.
    exact_pattern = re.compile(r"^" + escaped_prefix + r"-\d+$")
    # "continue", then one or more white-space characters (\s+), then the ID.
    continue_pattern = re.compile(r"\bcontinue\s+" + escaped_prefix + r"-?(\d+)\b", re.IGNORECASE)
    # DEBUG CHECK: `any_pattern.pattern` for "TK" is r"\bTK-?(\d+)\b".
    return {"prefix": prefix, "any": any_pattern, "exact": exact_pattern, "continue": continue_pattern}


# ---------------------------------------------------------------------------------------------
# Reading the inputs
# ---------------------------------------------------------------------------------------------

def load_json_file(path):
    """Read one JSON file and return its content.

    Parameters
        path (str): path of the JSON file.
    Returns
        dict or list: the parsed JSON value.
    Raises
        SystemExit: with a readable message when the file is missing or is not valid JSON.
    """
    # `try:` runs the block below; if one of the listed errors happens, Python jumps to the
    # matching `except` block instead of stopping with a long traceback.
    try:
        # `open(..., encoding="utf-8")` opens the file as text; `with` closes it at the end of
        # the block, also when an error happens.
        with open(path, encoding="utf-8") as json_file:
            # `json.load` parses the whole file into Python values (dict, list, str, ...).
            # DEBUG CHECK: for conversations.json, `parsed_value["schema"]` should be 2.
            parsed_value = json.load(json_file)
    # OSError covers "file not found" and "permission denied"; ValueError covers bad JSON.
    except (OSError, ValueError) as error:
        # `raise SystemExit(text)` prints the text on standard error and ends the program with
        # exit status 1, which tells the caller that the input was unusable.
        raise SystemExit("cannot read %s: %s" % (path, error))
    # The parsed value goes back to the caller.
    return parsed_value


def read_now_active_rows(now_file_path, patterns):
    """Read the "## Active rows" table of the orchestrator's NOW.md.

    The table looks like this (Markdown; cells are separated by "|"):
        | Task ID | Row | Owner | State |
        |---|---|---|---|
        | TK-42 | Dataset cleaning: ... | [UC] TK-42 Dataset cleaning | step 2 of 3 running ... |

    Parameters
        now_file_path (str): path of NOW.md, or "" for none.
        patterns (dict): from `task_id_patterns`; its "exact" pattern recognises the Task ID cell.
    Returns
        dict or None: {"TK-42": {"row": ..., "owner": ..., "state": ...}, ...}, in table order;
        None when there is no NOW.md to read (the row rules are then skipped).
    """
    # No path given, or the file does not exist on this machine (for example on a cluster).
    if not now_file_path or not os.path.isfile(now_file_path):
        return None
    # Read the whole file as one string; NOW.md is one screen long by design.
    with open(now_file_path, encoding="utf-8") as now_file:
        now_text = now_file.read()
    # The dictionary that collects one entry per active row.
    active_rows = {}
    # `inside_table_section` becomes True after the "## Active rows" heading and False again at
    # the next "## " heading, so that tables in other sections are ignored.
    inside_table_section = False
    # `str.splitlines()` cuts the text into a list of lines without their line breaks.
    for line in now_text.splitlines():
        # `str.strip()` removes spaces at both ends, so that indented lines also match.
        stripped_line = line.strip()
        # A level-2 heading starts a new section.
        if stripped_line.startswith("## "):
            # `in` tests whether the text "Active rows" occurs anywhere in the heading.
            inside_table_section = "Active rows" in stripped_line
            # `continue` skips the rest of the loop body and goes to the next line.
            continue
        # Lines outside the section, and lines that are not table rows, are skipped.
        if not inside_table_section or not stripped_line.startswith("|"):
            continue
        # `split("|")` cuts "| TK-42 | title | owner | state |" into
        # ["", " TK-42 ", " title ", " owner ", " state ", ""]; the list comprehension
        # `[cell.strip() for cell in ...]` builds a new list with each piece stripped.
        # Simpler equivalent: an empty list plus a for loop that appends `cell.strip()`.
        cells = [cell.strip() for cell in stripped_line.split("|")]
        # Drop the empty first and last pieces that come from the outer "|" characters.
        # `cells[1:-1]` is a slice: from index 1 up to, but not including, the last index.
        cells = cells[1:-1]
        # The header row and the "|---|" separator row do not start with a Task ID.
        if not cells or not patterns["exact"].match(cells[0]):
            continue
        # Store the row under its Task ID. Missing cells become "" so that short rows still work.
        active_rows[cells[0]] = {
            "row": cells[1] if len(cells) > 1 else "",
            "owner": cells[2] if len(cells) > 2 else "",
            "state": cells[3] if len(cells) > 3 else "",
        }
    # DEBUG CHECK: `list(active_rows)` should list the Task IDs of the table, in its order.
    return active_rows


def read_task_from_title_state(titles_directory, session_id):
    """Return the Task ID that a conversation recorded with set_conversation_task.py, or "".

    Parameters
        titles_directory (str): the conversation-title state folder.
        session_id (str): the conversation's full session id.
    Returns
        str: for example "TK-42", or "" when nothing was recorded.
    """
    # The hooks keep one small JSON file per conversation, named after its session id.
    state_file_path = os.path.join(titles_directory, session_id + ".json")
    # Conversations that never recorded a row have no file.
    if not os.path.isfile(state_file_path):
        return ""
    # A damaged state file must not stop the triage, so errors give "" instead.
    try:
        with open(state_file_path, encoding="utf-8") as state_file:
            title_state = json.load(state_file)
    except (OSError, ValueError):
        return ""
    # A state file that holds something other than a JSON object is ignored as well.
    if not isinstance(title_state, dict):
        return ""
    # `or ""` turns a missing or null "task" value into an empty string.
    recorded_task = title_state.get("task") or ""
    return recorded_task


def find_task_id(conversation, titles_directory, patterns):
    """Find the Work Hub Task ID of a conversation.

    Sources, in order: the conversation-title state file, the conversation's name, the
    "continue <prefix>-n" phrase of its first request.

    Parameters
        conversation (dict): one conversation from conversations.json.
        titles_directory (str): the conversation-title state folder.
        patterns (dict): from `task_id_patterns`.
    Returns
        str: a normalised Task ID such as "TK-42", or "" when none is found.
    """
    # 1. The state file is the most reliable source: it is written on purpose.
    recorded_task = read_task_from_title_state(titles_directory, conversation["session_id"])
    if recorded_task:
        return recorded_task
    # 2. The name, for example "[Jan-05 09:10][ws01][UC] TK-42 Dataset cleaning" or an
    #    automatic title such as "TK42 handover notes" (no hyphen).
    # `re.search` (here the compiled pattern's `.search`) returns a match object for the first
    # place where the pattern occurs, or None.
    title_match = patterns["any"].search(conversation.get("title") or "")
    if title_match:
        # `match.group(1)` is the captured row number; the ID is rebuilt as "<prefix>-<number>".
        return patterns["prefix"] + "-" + title_match.group(1)
    # 3. The first request, for example "continue TK-42 (https://...)".
    # `conversation["digest"]["first_prompt"]` is [time, text] or None; `or [0, ""]` covers None.
    first_prompt_text = (conversation.get("digest", {}).get("first_prompt") or [0, ""])[1]
    # Only the "continue TK-n" form counts: a request that merely mentions a row is not about it.
    continue_match = patterns["continue"].search(first_prompt_text)
    if continue_match:
        return patterns["prefix"] + "-" + continue_match.group(1)
    # No Task ID anywhere.
    return ""


def is_orchestrator(conversation):
    """Return True when a conversation is (or was) an orchestrator.

    A conversation counts as an orchestrator when its name has the [ORCH] tag, or when its first
    request was the orchestrator start prompt (a new orchestrator before it was renamed).
    The caller uses this only when the settings turn the orchestrator on.

    Parameters
        conversation (dict): one conversation from conversations.json.
    Returns
        bool
    """
    # The name; "or ''" protects against a missing or null title.
    title = conversation.get("title") or ""
    # The [ORCH] tag is given to the orchestrator conversation when it starts (see the kit's
    # orchestrator templates).
    if "[ORCH]" in title:
        return True
    # The first request of a brand-new orchestrator names the start prompt file.
    first_prompt_text = (conversation.get("digest", {}).get("first_prompt") or [0, ""])[1]
    if "START_ORCHESTRATOR_PROMPT" in first_prompt_text:
        return True
    return False


def parent_process_id(process_id):
    """Return the parent process id of a Linux process, or 0 when it cannot be read.

    /proc/<pid>/stat looks like "1234 (bash) S 1200 ...": the process id, the program name in
    parentheses (which may itself contain spaces or parentheses), the state, then the parent id.

    Parameters
        process_id (int): a process id on this machine.
    Returns
        int: the parent's process id; 0 for none (process 1, a vanished process, or no /proc).
    """
    try:
        with open("/proc/%d/stat" % process_id, encoding="utf-8") as stat_file:
            stat_text = stat_file.read()
    except OSError:
        return 0
    # `rsplit(")", 1)` cuts at the LAST ")", so a ")" inside the program name does no harm;
    # `[1]` is the part after it, " S 1200 ...", and `split()` turns it into ["S", "1200", ...].
    fields_after_name = stat_text.rsplit(")", 1)[1].split()
    # The parent id is the second field after the name. `int("1200")` converts text to a number.
    return int(fields_after_name[1]) if len(fields_after_name) > 1 else 0


def find_own_session_id(collected):
    """Find the conversation that runs this script by walking up its parent processes.

    The script is started by a shell, which is started by the Claude Code process of the
    conversation that runs the skill. That process id is in conversations.json (each open
    conversation lists its processes with host and pid). This is more reliable than the shell
    text, which the collector shortens to about 240 characters.

    Parameters
        collected (dict): the parsed conversations.json.
    Returns
        str: the session id of the running conversation, or "" when none of its processes is
        an ancestor of this script (for example when the script runs from a plain terminal).
    """
    # `split(".")[0]` keeps "ws01" from a full name such as "ws01.example.org".
    this_host = socket.gethostname().split(".")[0]
    # Process id -> session id, for the processes of this machine only.
    session_by_process = {}
    for site in collected.get("sites", []):
        for conversation in site.get("conversations", []):
            for process in conversation.get("processes") or []:
                if (process.get("host") or "").split(".")[0] == this_host and process.get("pid"):
                    session_by_process[int(process["pid"])] = conversation["session_id"]
    # Walk from this script's own process (`os.getpid()`) up through its parents.
    # 40 steps is far more than shell -> Claude Code ever needs; it also stops endless loops.
    process_id = os.getpid()
    for _ in range(40):
        if process_id in session_by_process:
            # DEBUG CHECK: run from a conversation, this is that conversation's session id.
            return session_by_process[process_id]
        process_id = parent_process_id(process_id)
        # 0 = no parent could be read; 1 = the system's first process, the top of the tree.
        if process_id <= 1:
            break
    return ""


def is_this_request(conversation, self_session_id):
    """Return True for the conversation that is running this skill right now.

    Parameters
        conversation (dict): one conversation from conversations.json.
        self_session_id (str): a session id or its first characters given with --self, or "".
    Returns
        bool
    """
    # An explicit --self wins. `str.startswith` lets the user give only the 8-character short id.
    if self_session_id:
        return conversation["session_id"].startswith(self_session_id)
    # Otherwise (no --self and no parent process found): the conversation whose own shell was
    # running the collector when it looked.
    for shell in conversation.get("background", []):
        # The full script name anywhere in the command.
        if "collect_conversations.py" in shell:
            return True
        # The collector cuts shell text after about 240 characters, which can cut the name too,
        # for example "... && python3 scripts/collec". So the last word also counts when its
        # file-name part ("collec") is the start of "collect_conversations.py", at least 6 letters.
        # `split()` cuts at white space; `[-1]` is the last word; `split("/")[-1]` drops folders.
        words = shell.split()
        last_name = words[-1].split("/")[-1] if words else ""
        if len(last_name) >= 6 and "collect_conversations.py".startswith(last_name):
            return True
    return False


def read_last_reply(conversation, transcript_is_local):
    """Return the full text of the conversation's last reply.

    The transcript is a JSON-lines file: one JSON object per line. Claude's replies are the
    objects with "type": "assistant"; their text parts are {"type": "text", "text": "..."}.

    Parameters
        conversation (dict): one conversation from conversations.json.
        transcript_is_local (bool): True when the transcript file is on this machine's site.
    Returns
        str: the last reply's text; the shortened digest copy when the transcript cannot be read.
    """
    # The digest keeps the start of the last reply (cut to a few hundred characters).
    digest_reply_text = (conversation.get("digest", {}).get("last_reply") or [0, ""])[1]
    # Transcripts of other sites are on other file systems: use the digest copy.
    transcript_path = conversation.get("transcript") or ""
    if not transcript_is_local or not os.path.isfile(transcript_path):
        return digest_reply_text
    # Read only the end of the file (see TRANSCRIPT_TAIL_BYTES).
    try:
        # "rb" opens the file in binary mode, which is needed for `seek` relative to the end.
        with open(transcript_path, "rb") as transcript_file:
            # `seek(0, 2)` moves to the end of the file (whence 2 = end); `tell()` gives the size.
            transcript_file.seek(0, 2)
            file_size_bytes = transcript_file.tell()
            # `max(0, ...)` starts at the beginning when the file is smaller than the tail.
            transcript_file.seek(max(0, file_size_bytes - TRANSCRIPT_TAIL_BYTES))
            # `decode("utf-8", "replace")` turns bytes into text; a character cut in half at the
            # start of the tail becomes "?" (the replacement character) instead of an error.
            tail_text = transcript_file.read().decode("utf-8", "replace")
    except OSError:
        return digest_reply_text
    # The newest reply text found so far; stays "" if the tail holds no reply.
    last_reply_text = ""
    # Each line is one JSON object; the first line may be cut and simply fails to parse.
    for line in tail_text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        # Only Claude's replies are of interest; tool results and the user's messages are skipped.
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        # `record["message"]["content"]` is a list of parts: text, tool calls, thinking.
        content_parts = (record.get("message") or {}).get("content") or []
        # Keep the text parts only, in their order.
        text_parts = []
        for part in content_parts:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(part.get("text") or "")
        # `"\n".join(list)` glues the parts together with line breaks between them.
        reply_text = "\n".join(text_parts).strip()
        # A reply made only of tool calls has no text and does not replace the previous reply.
        if reply_text:
            last_reply_text = reply_text
    # DEBUG CHECK: the start of `last_reply_text` should equal the digest's shortened copy.
    return last_reply_text or digest_reply_text


def closing_question(reply_text):
    """Return the question that ends a reply, or "" when the reply does not end with one.

    Parameters
        reply_text (str): the full text of a reply.
    Returns
        str: the sentence that ends with "?", at most 200 characters, or "".
    Example
        closing_question("All done. Should I send the six issues to the orchestrator?")
        returns "Should I send the six issues to the orchestrator?"
    """
    # Remove web links first: "https://www.notion.so/...?pvs=204" contains a "?" that is no
    # question. `pattern.sub("", text)` replaces every match with nothing.
    text_without_links = WEB_LINK_PATTERN.sub("", reply_text)
    # Keep only the last paragraph or so (see QUESTION_WINDOW_CHARACTERS).
    # `text[-n:]` is a slice of the last n characters (all of them when the text is shorter).
    reply_ending = text_without_links[-QUESTION_WINDOW_CHARACTERS:]
    # `str.rfind` returns the position of the LAST "?" or -1 when there is none.
    question_mark_position = reply_ending.rfind("?")
    if question_mark_position < 0:
        return ""
    # The question starts after the previous sentence end or line break before the "?".
    text_before_mark = reply_ending[:question_mark_position]
    # `max` of the three positions gives the boundary closest to the "?"; -1 means "none".
    sentence_start = max(text_before_mark.rfind(". "), text_before_mark.rfind("\n"),
                         text_before_mark.rfind(": "))
    # The slice runs from just after the boundary character (`sentence_start + 1`) up to and
    # including the "?" (`question_mark_position + 1`, because a slice end is excluded);
    # `strip()` removes the space that follows ". " or ": ". With no boundary, -1 + 1 = 0.
    question_text = reply_ending[sentence_start + 1:question_mark_position + 1].strip()
    # Markdown emphasis such as "**Question 10 of 10:**" is kept; only the length is limited.
    return question_text[:200]


def has_done_marker(reply_text):
    """Return True when a reply says that the conversation's work is over (see DONE_MARKERS).

    Parameters
        reply_text (str): the full text of a reply.
    Returns
        bool
    """
    # Compare in lower case so that capitalisation does not matter.
    lower_reply_text = reply_text.lower()
    # True as soon as one marker occurs in the reply.
    return any(marker in lower_reply_text for marker in DONE_MARKERS)


# ---------------------------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------------------------

def open_conversations_with_site(collected):
    """List every open conversation of conversations.json together with its site.

    Parameters
        collected (dict): the parsed conversations.json.
    Returns
        list of (str, dict): (site key, conversation) pairs, for example ("home", {...}).
    """
    # The pairs collected over all sites.
    pairs = []
    # `collected["sites"]` holds one dictionary per site (the home site, each cluster ...),
    # reached or not.
    for site in collected.get("sites", []):
        # Each site lists its conversations; a site that was not reached has no list.
        for conversation in site.get("conversations", []):
            # In --last/--match mode the collector also lists closed conversations
            # (status "closed"); they need no triage because no window has them open.
            if conversation.get("status") == "closed":
                continue
            # `(a, b)` is a tuple: a fixed pair that the caller unpacks again.
            pairs.append((site.get("site", ""), conversation))
    # DEBUG CHECK: `len(pairs)` should equal the number of open conversations in the digest.
    return pairs


def notes_for(conversation, label):
    """Return extra remarks about a conversation that do not change its label.

    Only remarks that matter for the label are kept: the window count always (a redundant
    conversation open twice still has to be closed twice), a stopped background task only for
    warm and look-closer conversations, compactions only for warm ones.

    Parameters
        conversation (dict): one conversation from conversations.json.
        label (str): the label chosen by `label_one`.
    Returns
        list of str: for example ["open in 2 windows (VS Code, VS Code Remote-SSH): keep one"].
    """
    # The remarks collected below, in a fixed order.
    notes = []
    # Several processes on one session id = the same conversation open in several windows
    # (for example local VS Code and VS Code Remote-SSH). Both can write, so one should close.
    processes = conversation.get("processes") or []
    if len(processes) > 1:
        # The app names of the windows, for example "VS Code, VS Code Remote-SSH".
        app_names = ", ".join(process.get("app", "?") for process in processes)
        # The busy window is the one to keep, because it is in the middle of something.
        busy_apps = [process.get("app", "?") for process in processes
                     if process.get("status") == "busy"]
        keep_hint = "keep the busy one (%s)" % busy_apps[0] if busy_apps else "keep one"
        notes.append("open in %d windows (%s): %s" % (len(processes), app_names, keep_hint))
    # A background task that belonged to an earlier window stopped when that window closed.
    # The digest's "last_notice" is [time, text] or None.
    last_notice_text = (conversation.get("digest", {}).get("last_notice") or [0, ""])[1]
    if label in ("warm", "check") and DIED_WITH_WINDOW_MARKER in last_notice_text:
        notes.append("a background task stopped with an earlier window: " + last_notice_text[:120])
    # Context compactions: a long conversation that was compacted forgets details, so a warm one
    # that was compacted is a candidate for a handover to a fresh conversation.
    compactions = conversation.get("digest", {}).get("compactions") or 0
    if label == "warm" and compactions > 0:
        # "1 time" but "4 times": the conditional expression `a if condition else b` picks one.
        times_word = "time" if compactions == 1 else "times"
        notes.append("context compacted %d %s (consider a handover to a fresh conversation)"
                     % (compactions, times_word))
    return notes


def label_one(conversation, context):
    """Apply rules 1-11 of the module docstring to one conversation.

    Parameters
        conversation (dict): one conversation from conversations.json.
        context (dict): facts shared by all conversations, built in `triage`:
            "self_session_id", "orchestrator_ids" (session ids of all orchestrators; empty
            when the orchestrator is off), "newest_orchestrator_id", "task_ids" (session id ->
            Task ID), "newest_by_task" (Task ID -> session id of its newest open conversation),
            "titles_by_id", "now_rows" (dict or None), "now_seconds", "stale_seconds",
            "last_replies" (session id -> full last reply text).
    Returns
        tuple (str, str): (label, reason). The label is a key of LABEL_HEADINGS.
    """
    # Short names for the values used by several rules.
    session_id = conversation["session_id"]
    task_id = context["task_ids"].get(session_id, "")
    last_reply_text = context["last_replies"].get(session_id, "")
    now_rows = context["now_rows"]

    # Rule 1: this request.
    if is_this_request(conversation, context["self_session_id"]):
        return "self", "this conversation ran the collector"

    # Rule 2: orchestrators. Only the newest one coordinates; the others were replaced.
    # The set is empty when the settings turn the orchestrator off, so the rule never applies.
    if session_id in context["orchestrator_ids"]:
        if session_id == context["newest_orchestrator_id"]:
            return "warm", "current orchestrator (newest [ORCH] conversation)"
        newest_title = context["titles_by_id"].get(context["newest_orchestrator_id"], "?")
        return "redundant", "older orchestrator; the current one is \"%s\"" % newest_title

    # Rule 3: an older conversation on a row that a newer open conversation also has.
    if task_id and context["newest_by_task"].get(task_id) != session_id:
        newer_title = context["titles_by_id"].get(context["newest_by_task"][task_id], "?")
        return "redundant", "older conversation on %s; newer: \"%s\"" % (task_id, newer_title)

    # Rule 4: Claude is working in it now.
    if conversation.get("status") == "busy":
        return "warm", "Claude is working in it now"

    # Rule 5: a background command it started still runs (a wait, a watch, a copy ...).
    background_shells = conversation.get("background") or []
    if background_shells:
        # " ".join(text.split()) squeezes line breaks and repeated spaces into single spaces.
        first_shell = " ".join(background_shells[0].split())[:90]
        return "warm", "a background command still runs: " + first_shell

    # Rules 6 and 7: the Work Hub row, when NOW.md was read and the conversation has a row.
    if task_id and now_rows is not None:
        if task_id in now_rows:
            row_state = now_rows[task_id]["state"][:140]
            return "warm", "%s is an active row in NOW.md: %s" % (task_id, row_state)
        if has_done_marker(last_reply_text):
            return "finished", "%s is not in NOW.md's active rows and its last reply says done" % task_id
        return "check", "%s is not in NOW.md's active rows (Done, parked or dropped?)" % task_id

    # Rule 8: the last reply ends with a question to the user.
    question_text = closing_question(last_reply_text)
    if question_text:
        return "check", "its last reply ends with a question: " + question_text

    # Rule 9: the last reply says the work is over.
    if has_done_marker(last_reply_text):
        return "finished", "its last reply says the work is over"

    # Rule 10: no message for a long time.
    idle_seconds = context["now_seconds"] - (conversation.get("last_message") or 0)
    if idle_seconds > context["stale_seconds"]:
        # 86,400 seconds = 1 day; `round(..., 1)` keeps one decimal, for example 6.1.
        idle_days = round(idle_seconds / 86400.0, 1)
        return "finished", "no message for %s days" % idle_days

    # Rule 11: nothing decided it.
    return "check", "no rule decided it; read its last reply"


def triage(collected, now_rows, titles_directory, stale_days, self_session_id, patterns=None,
           orchestrator_enabled=True):
    """Label every open conversation and find active rows that have no open conversation.

    Parameters
        collected (dict): the parsed conversations.json.
        now_rows (dict or None): result of `read_now_active_rows`.
        titles_directory (str): the conversation-title state folder.
        stale_days (float): days without a message after which a conversation counts as finished.
        self_session_id (str): --self value, or "" to detect the running conversation.
        patterns (dict or None): from `task_id_patterns`; None = the default prefix "TK".
        orchestrator_enabled (bool): False skips rule 2 (no conversation counts as orchestrator).
    Returns
        dict: {"conversations": [entry, ...], "rows_without_window": [...], "now_file_read": bool}.
        Each entry has session_id, short_id, title, site, task_id, label, reason, notes,
        started, last_message.
    """
    # Default Task ID patterns when the caller gave none (tests, or no prefix in the settings).
    if patterns is None:
        patterns = task_id_patterns(DEFAULT_TASK_ID_PREFIX)
    # The site of the machine that ran the collector; only its transcripts are on this disk.
    local_site = (collected.get("requested_from") or {}).get("site", "")
    # Every open conversation with its site.
    pairs = open_conversations_with_site(collected)

    # --- Facts shared by all rules ---------------------------------------------------------
    # Session id -> name, for the reasons that point to another conversation.
    titles_by_id = {}
    # Session id -> Task ID ("" when none).
    task_ids = {}
    # Session id -> full last reply.
    last_replies = {}
    # `for site_key, conversation in pairs` unpacks each (site, conversation) tuple.
    for site_key, conversation in pairs:
        session_id = conversation["session_id"]
        # A conversation without a name shows its 8-character short id instead.
        titles_by_id[session_id] = conversation.get("title") or session_id[:8]
        # DEBUG CHECK: `task_ids` maps each session id to e.g. "TK-42", and chats without a row to "".
        task_ids[session_id] = find_task_id(conversation, titles_directory, patterns)
        # `site_key == local_site` is True only for this machine's site (its transcripts are here).
        last_replies[session_id] = read_last_reply(conversation, site_key == local_site)

    # Orchestrators, and the newest of them by start time. The list comprehension builds one
    # (start time, session id) tuple per orchestrator; `_` is the usual name for the unused
    # site key. Simpler equivalent: a for loop that appends the tuple when is_orchestrator(c).
    # With the orchestrator off in the settings, the list stays empty.
    orchestrator_pairs = []
    if orchestrator_enabled:
        orchestrator_pairs = [(c.get("started") or 0, c["session_id"]) for _, c in pairs
                              if is_orchestrator(c)]
    # `max` of (start, id) pairs compares the start times first, so it picks the newest start.
    # `default=(0, "")` is used when there is no orchestrator at all; `[1]` takes the session id.
    newest_orchestrator_id = max(orchestrator_pairs, default=(0, ""))[1]
    # A set allows the fast test `session_id in orchestrator_ids` in the rules.
    orchestrator_ids = set(session_id for _, session_id in orchestrator_pairs)

    # Task ID -> session id of the newest open conversation that has it. Orchestrators and the
    # running request are left out, so that they never "take over" a row.
    newest_by_task = {}
    # Task ID -> start time of that newest conversation, used for the comparison below.
    newest_start_by_task = {}
    for _, conversation in pairs:
        session_id = conversation["session_id"]
        task_id = task_ids[session_id]
        # Conversations without a row, and orchestrators, own no row.
        if not task_id or session_id in orchestrator_ids:
            continue
        # The running request may mention a row without working on it.
        if is_this_request(conversation, self_session_id):
            continue
        # Start time in seconds since 1970; 0 when unknown, so a known start always wins.
        started = conversation.get("started") or 0
        # Keep this conversation when it is the first one seen for the row or a newer one.
        if task_id not in newest_by_task or started > newest_start_by_task[task_id]:
            newest_by_task[task_id] = session_id
            newest_start_by_task[task_id] = started

    # The dictionary handed to `label_one` (see its docstring for each key).
    context = {
        "self_session_id": self_session_id,
        "orchestrator_ids": orchestrator_ids,
        "newest_orchestrator_id": newest_orchestrator_id,
        "task_ids": task_ids,
        "newest_by_task": newest_by_task,
        "titles_by_id": titles_by_id,
        "now_rows": now_rows,
        # The collector's own time, so that a later re-run of the triage gives the same result.
        "now_seconds": collected.get("generated_at") or time.time(),
        "stale_seconds": stale_days * 86400.0,
        "last_replies": last_replies,
    }

    # --- One entry per conversation --------------------------------------------------------
    # The list of result entries, in the collector's order (newest message first per site).
    entries = []
    for site_key, conversation in pairs:
        # `a, b = function()` unpacks the returned (label, reason) tuple into two names.
        label, reason = label_one(conversation, context)
        session_id = conversation["session_id"]
        # One dictionary per conversation; these keys are also the keys of the JSON output.
        entries.append({
            "session_id": session_id,
            "short_id": session_id[:8],
            "title": titles_by_id[session_id],
            "site": site_key,
            "task_id": task_ids[session_id],
            "label": label,
            "reason": reason,
            "notes": notes_for(conversation, label),
            "started": conversation.get("started"),
            "last_message": conversation.get("last_message"),
        })

    # --- Active rows with no open conversation ---------------------------------------------
    rows_without_window = []
    # Only possible when NOW.md was read.
    if now_rows is not None:
        # The Task IDs that some open conversation has (empty strings left out).
        open_task_ids = set(task_id for task_id in task_ids.values() if task_id)
        # `dict.items()` gives (key, value) pairs: here (Task ID, row dictionary).
        for task_id, row in now_rows.items():
            # A row in NOW.md that no open window works on still has work but nobody on it.
            if task_id not in open_task_ids:
                rows_without_window.append({"task_id": task_id, "row": row["row"],
                                            "owner": row["owner"], "state": row["state"]})

    # DEBUG CHECK: every entry has a label that is a key of LABEL_HEADINGS.
    return {"conversations": entries, "rows_without_window": rows_without_window,
            "now_file_read": now_rows is not None}


# ---------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------

def print_triage(result, now_file_path, stale_days):
    """Print the triage grouped by label, in the order of LABEL_HEADINGS.

    Parameters
        result (dict): the value returned by `triage`.
        now_file_path (str): the NOW.md path that was tried, for the first line ("" = none).
        stale_days (float): the --stale-days value, for the first line.
    Returns
        None (prints to standard output).
    """
    entries = result["conversations"]
    # First line: how many conversations, whether NOW.md was read. `"%s" % value` puts the
    # value into the text at the place of %s (%d does the same for whole numbers).
    if result["now_file_read"]:
        now_text = "NOW.md read: %s" % now_file_path
    else:
        now_text = "NOW.md not read (%s): row rules skipped" % (now_file_path or "no path")
    print("Triage of %d open conversations · %s · stale after %s days"
          % (len(entries), now_text, stale_days))
    # One block per label, in the order of LABEL_HEADINGS (dictionaries keep insertion order).
    for label, heading in LABEL_HEADINGS.items():
        # The entries with this label, in the collector's order.
        group = [entry for entry in entries if entry["label"] == label]
        # Empty groups are not printed.
        if not group:
            continue
        # "\n" starts the group with an empty line.
        print("\n%s: %d" % (heading, len(group)))
        # Each conversation: short id and name, then the reason, then any notes.
        for entry in group:
            print("  [%s] %s" % (entry["short_id"], entry["title"]))
            print("      why: %s" % entry["reason"])
            for note in entry["notes"]:
                print("      note: %s" % note)
    # Rows that still have work but no window (empty list = nothing printed).
    if result["rows_without_window"]:
        print("\nACTIVE ROWS IN NOW.md WITH NO OPEN CONVERSATION:")
        for row in result["rows_without_window"]:
            # `[:70]` and `[:100]` shorten long cells so that each row stays on one line.
            print("  %s %s · owner %s · %s" % (row["task_id"], row["row"][:70],
                                              row["owner"] or "none", row["state"][:100]))


# ---------------------------------------------------------------------------------------------
# Settings (read only when the script runs from the command line)
# ---------------------------------------------------------------------------------------------

def load_settings():
    """Import workhub_config.py from this script's folder and return (module, settings dict).

    workhub_config.py (an identical copy of the kit's lib/workhub_config.py) finds the settings
    file (~/.config/workhub-claude/config.json or $WORKHUB_CLAUDE_CONFIG) and answers questions
    about it: get_setting(config, "a.b", default), state_dir(config, sub) (creates the folder,
    mode 700), expand_path(text).
    Exits with status 2 and the setup hint when the settings file is missing or unreadable.
    """
    # The folder of this file; putting it first on `sys.path` makes `import workhub_config`
    # find the copy next to this script, not some other module of the same name.
    script_folder = os.path.dirname(os.path.abspath(__file__))
    if script_folder not in sys.path:
        sys.path.insert(0, script_folder)
    # The import is here, not at the top, so that the tests can import this module without
    # a settings file.
    import workhub_config
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        sys.stderr.write(str(error) + "\n")
        sys.exit(2)
    return workhub_config, config


def default_now_file_path(wc, config):
    """Return <workflow.orchestrator.folder>/NOW.md when the orchestrator is on, else "".

    Parameters
        wc: the workhub_config module.
        config (dict): the settings.
    Returns
        str: an absolute path (~ expanded), or "" when the orchestrator is off or has no folder.
    """
    # `bool(...)` turns a missing value (None) into False: the orchestrator is off by default.
    orchestrator_enabled = bool(wc.get_setting(config, "workflow.orchestrator.enabled", default=False))
    if not orchestrator_enabled:
        return ""
    # The folder where the orchestrator keeps NOW.md, QUESTIONS.md and DECISIONS.md.
    orchestrator_folder = wc.expand_path(wc.get_setting(config, "workflow.orchestrator.folder", default=""))
    if not orchestrator_folder:
        return ""
    return os.path.join(orchestrator_folder, "NOW.md")


def parse_arguments():
    """Define and read the command-line options.

    Returns
        argparse.Namespace: the parsed options (conversations_json, now_file, titles_dir,
        stale_days, self_session, output). now_file and titles_dir are None when not given;
        main() then fills them from the settings.
    """
    # `ArgumentParser(description=...)` creates the parser; the description is shown by --help.
    # Each `add_argument` call below declares one option: a name without "--" is a required
    # positional value; "--name" is optional; `default` is used when the option is absent;
    # `type=float` converts the text to a number; `dest` names the attribute that holds the
    # value; `help` is the --help text, where %(default)s shows the default value.
    parser = argparse.ArgumentParser(
        description="Sort the open conversations of conversations.json into warm, look closer, "
                    "redundant and finished, with the reason for each (rules in the file header).")
    parser.add_argument("conversations_json",
                        help="conversations.json written by collect_conversations.py")
    parser.add_argument("--now-file", default=None,
                        help="the orchestrator's NOW.md (default: <workflow.orchestrator.folder>/NOW.md "
                             "when the orchestrator is on; '' for none); a missing file only skips "
                             "the row rules")
    parser.add_argument("--titles-dir", default=None,
                        help="the conversation-title state folder (default: "
                             "<paths.state_dir>/conversation_titles)")
    parser.add_argument("--stale-days", type=float, default=2.0,
                        help="days without a message after which a conversation counts as "
                             "finished (default: %(default)s)")
    parser.add_argument("--self", dest="self_session", default="",
                        help="session id (or its first characters) of the conversation running "
                             "the skill; default: found through this script's parent processes, "
                             "else the one whose shell ran the collector")
    parser.add_argument("-o", "--output", default="",
                        help="also write the result as JSON to this file")
    # `parse_args()` reads sys.argv; on a wrong option it prints the usage and exits with 2.
    return parser.parse_args()


def main():
    """Read the settings and inputs, triage, print, and write the JSON file when -o is given."""
    # The parsed command-line options (see parse_arguments).
    arguments = parse_arguments()
    # The settings (exit 2 with the setup hint when they are missing).
    wc, config = load_settings()
    # The Task ID prefix ("TK" unless the settings say otherwise) and its patterns.
    prefix = str(wc.get_setting(config, "notion.task_id_prefix", default=DEFAULT_TASK_ID_PREFIX))
    patterns = task_id_patterns(prefix)
    # Is the orchestrator part of this user's workflow? Off = rule 2 and NOW.md are skipped.
    orchestrator_enabled = bool(wc.get_setting(config, "workflow.orchestrator.enabled", default=False))
    # NOW.md: the option when given (also "" = none), else the default from the settings.
    if arguments.now_file is None:
        now_file_path = default_now_file_path(wc, config)
    else:
        now_file_path = wc.expand_path(arguments.now_file)
    # The title state folder: the option when given, else <state_dir>/conversation_titles.
    if arguments.titles_dir is None:
        titles_directory = wc.state_dir(config, "conversation_titles")
    else:
        titles_directory = wc.expand_path(arguments.titles_dir)
    # The collector's output; SystemExit with a message when it is unreadable.
    collected = load_json_file(arguments.conversations_json)
    # A file from an older collector would lack fields that the rules need.
    if collected.get("schema") != 2:
        raise SystemExit("%s: expected schema 2 from collect_conversations.py, found %r"
                         % (arguments.conversations_json, collected.get("schema")))
    # The active rows of NOW.md, or None.
    now_rows = read_now_active_rows(now_file_path, patterns)
    if now_rows is None and now_file_path:
        # Warnings go to standard error so that the printed list stays clean.
        sys.stderr.write("note: %s not found; the row rules are skipped\n" % now_file_path)
    # The running conversation: --self if given, else found through the parent processes.
    self_session_id = arguments.self_session or find_own_session_id(collected)
    # The labels and reasons.
    result = triage(collected, now_rows, titles_directory, arguments.stale_days,
                    self_session_id, patterns, orchestrator_enabled)
    # The grouped list on standard output.
    print_triage(result, now_file_path, arguments.stale_days)
    # Optional JSON copy of the result. Only the file named by -o is written.
    if arguments.output:
        with open(arguments.output, "w", encoding="utf-8") as output_file:
            # `indent=1` makes the file readable; `ensure_ascii=False` keeps "·" and "é" as is.
            json.dump(result, output_file, indent=1, ensure_ascii=False)
        print("\nWrote %s" % arguments.output)


# This block runs only when the file is executed as a script, not when tests import it.
if __name__ == "__main__":
    main()
