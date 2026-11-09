#!/usr/bin/env python3
"""
set_conversation_task.py: record which Work Hub row (Task ID) this Claude Code conversation works on, so that the
naming hook conversation_title.py (same folder) puts the Task ID into the conversation's name, and so that the other
workhub-claude skills (they read the same state file) know the conversation's row.

PURPOSE
    In the workhub-claude way of working, one unitopic conversation (UC) = one Work Hub row, and the row's automatic
    Task ID (TK-1, TK-2 ..., the Notion "ID" property; the prefix comes from the setting notion.task_id_prefix)
    appears in the conversation's name, for example "[Nov-04 09:10][ws01][UC] TK-42 Data cleaning". The naming hook
    runs at the user's prompts and cannot know the row; the conversation knows it once it has found or created the
    row in its first reply. This script, run by the conversation itself through Bash, writes the row into the hook's
    state file:
      1. It finds this conversation's session ID: the environment variable CLAUDE_CODE_SESSION_ID that Claude Code
         sets for the commands a conversation runs, or else <Claude config folder>/sessions/<CLAUDE_PID>.json
         (Claude config folder = $CLAUDE_CONFIG_DIR, default ~/.claude).
      2. It adds "task" (e.g. "TK-42") and "task_topic" (e.g. "Data cleaning") to <state folder>/<session_id>.json,
         keeping every other field. The state folder is the hook's (workhub_config.state_dir(config,
         "conversation_titles"), default ~/.local/state/workhub-claude/conversation_titles).
      3. It says what happens next:
         - the conversation has no name yet: the hook names it, with the Task ID, at the user's next message;
         - the name was set by the hook and is still shown unchanged (read from the transcript's latest
           "custom-title" record): the hook adds the Task ID ONCE at the user's next message, keeping the tags;
           nothing to paste;
         - the name was set by the user, or cannot be read: the hook leaves it alone, so the script prints a
           ready-to-paste "/rename <name>" line for the conversation to give the user. The tags of the shown name are
           kept, so an orchestrator named "[...][ORCH] Orchestrator" with /rename keeps its "[ORCH]" tag;
         - the setting workflow.conversation_names is false (the user does not want conversations named after their
           rows): the row is still recorded (step 2), the naming hook does nothing, and the script says so and
           prints no /rename line.

INPUT
    --task TK-42                 the row's Task ID: the prefix (setting notion.task_id_prefix, default "TK"), "-",
                                 then 1 to 5 digits.
    --topic "Data cleaning"      the row's short title (2-5 plain words; non-ASCII letters and brackets are removed,
                                 and it is cut to fit the name, as the hook does).
    --session-id ID              optional, for tests or for another conversation; default: this conversation.
    --overwrite                  allow replacing a DIFFERENT Task ID already recorded for this conversation.
    --dry-run                    print what would be written; writes no state file (the empty private state folder
                                 may be created, mode 700, because finding it creates it).
    Settings file: ~/.config/workhub-claude/config.json, or the file named by $WORKHUB_CLAUDE_CONFIG. Also read:
    conversations.claude_projects_dir (where transcripts are, default <Claude config folder>/projects) and
    workflow.conversation_names (default true; false = record the row only, never suggest a name).

OUTPUT
    Standard output: what was recorded and what happens next (and the /rename line when needed and names are on).
    Side effect: the state file above (written atomically). Exit codes: 0 = done (or dry run), 2 = bad input, no
    settings file, or no session ID found, 3 = refused because another Task ID is already recorded (use --overwrite).

DEPENDENCIES
    Python >= 3.9, standard library; imports from the same folder:
      - conversation_title.py (the hook): clean_topic (topic cleaning), read_state / write_state (the state file),
        state_folder (where the state files are), task_id_pattern / task_id_prefix (the Task ID form), TAG_PATTERN,
        ROLE_TAG ("[UC]"), names_enabled (the setting workflow.conversation_names), machine_name, latest_custom_title
        (the name shown now, from the transcript) and name_with_task (tags kept, topic replaced by "TK-n <topic>").
        Same rules as the hook, because it is the same code.
      - workhub_config.py: load_config / ConfigError / SETUP_HINT (the settings file), get_setting, expand_path.

EXAMPLE
    python3 ~/.local/share/workhub-claude/hooks/set_conversation_task.py --task TK-42 --topic "Data cleaning"
    -> Recorded TK-42 "Data cleaning" for conversation 69d46213-....
       The naming hook will name this conversation "[...][ws01][UC] TK-42 Data cleaning" at the user's next message.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).

DOCUMENTATION LEVEL
    ULTRA-style for a small tool, like conversation_title.py: every block has an explanation above it.
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------------------------------------------------

# argparse: the command-line options and --help. glob: find this conversation's transcript by file-name pattern.
# json: the <Claude config folder>/sessions/<pid>.json file. os: paths and environment variables. sys: exit codes and
# the import path.
import argparse
import glob
import json
import os
import sys

# No __pycache__ folder next to the imported modules (the install folder keeps only the installed files).
sys.dont_write_bytecode = True
# The naming hook and the settings reader live in the same folder; putting that folder first on the import path lets
# `import conversation_title` and `import workhub_config` load them, so this script uses exactly the hook's rules.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conversation_title as title_hook  # noqa: E402 - the import path must be set first
import workhub_config  # noqa: E402 - the import path must be set first


# ---------------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------------

def claude_config_folder():
    """Return Claude Code's configuration folder: $CLAUDE_CONFIG_DIR when set, else ~/.claude."""
    chosen = os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or "~/.claude"
    return workhub_config.expand_path(chosen)


def find_session_id():
    """Return this conversation's session ID, or "" when it cannot be found.

    Claude Code sets CLAUDE_CODE_SESSION_ID in the environment of every command a conversation runs. Fallback:
    CLAUDE_PID (the conversation's process) and the file <Claude config folder>/sessions/<pid>.json, whose
    "sessionId" field names the session.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if session_id:
        return session_id
    process_id = os.environ.get("CLAUDE_PID", "").strip()
    if not process_id:
        return ""
    session_file = os.path.join(claude_config_folder(), "sessions", process_id + ".json")
    try:
        with open(session_file, encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return ""
    return str(record.get("sessionId") or "").strip() if isinstance(record, dict) else ""


def find_transcript(config, session_id):
    """Return the path of this conversation's transcript, or "" when it is not found.

    Claude Code keeps each conversation as <projects folder>/<folder name made from the working directory>/
    <session_id>.jsonl; the projects folder is the setting conversations.claude_projects_dir (default
    <Claude config folder>/projects). `glob.glob` returns every path that matches the pattern; "*" stands for any
    project folder. Example: "5799dbc5-..." -> "/home/alex/.claude/projects/-home-alex/5799dbc5-....jsonl".
    """
    default_projects = os.path.join(claude_config_folder(), "projects")
    projects_folder = workhub_config.expand_path(
        workhub_config.get_setting(config, "conversations.claude_projects_dir", default=default_projects))
    pattern = os.path.join(projects_folder, "*", session_id + ".jsonl")
    matches = sorted(glob.glob(pattern))
    return matches[0] if matches else ""


# ---------------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------------

def main():
    """Parse the options, check them, merge the task into the state file, and say what happens next."""
    parser = argparse.ArgumentParser(
        description="Record this conversation's Work Hub row (Task ID) for the naming hook conversation_title.py.")
    parser.add_argument("--task", required=True, help='the row\'s Task ID, e.g. "TK-42" (prefix from the settings)')
    parser.add_argument("--topic", required=True, help='the row\'s short title, e.g. "Data cleaning"')
    parser.add_argument("--session-id", default="", help="default: this conversation (CLAUDE_CODE_SESSION_ID)")
    parser.add_argument("--overwrite", action="store_true", help="replace a different Task ID already recorded")
    parser.add_argument("--dry-run", action="store_true", help="print what would be written, write no state file")
    options = parser.parse_args()

    # ---- 1. The settings (no settings file = workhub-claude is not set up: say how to set it up) -------------------
    try:
        config = workhub_config.load_config(required=True)
    except workhub_config.ConfigError as error:
        print("ERROR: " + str(error))
        return 2

    # ---- 2. Check the inputs ---------------------------------------------------------------------------------------
    task_id = options.task.strip()
    prefix = title_hook.task_id_prefix(config)
    if not title_hook.task_id_pattern(config).match(task_id):
        print(f'ERROR: --task must look like "{prefix}-42" (prefix from notion.task_id_prefix), got "{task_id}".')
        return 2
    task_topic = title_hook.clean_topic(options.topic)
    if not task_topic:
        print("ERROR: --topic is empty after cleaning (use plain words).")
        return 2
    session_id = options.session_id.strip() or find_session_id()
    # A session ID is a file name here, so it must not contain a path separator.
    if not session_id or "/" in session_id:
        print("ERROR: could not find this conversation's session ID (CLAUDE_CODE_SESSION_ID is not set).")
        return 2

    # ---- 3. Read the hook's state and refuse a silent change of row --------------------------------------------------
    state_path = os.path.join(title_hook.state_folder(config), session_id + ".json")
    state = title_hook.read_state(state_path)
    recorded_task = str(state.get("task") or "")
    if recorded_task and recorded_task != task_id and not options.overwrite:
        print(f"REFUSED: this conversation already records {recorded_task}; one conversation = one row. "
              f"Use --overwrite only if the user moved it to {task_id}.")
        return 3

    # ---- 4. Merge and write (or only show) ---------------------------------------------------------------------------
    state["task"] = task_id
    state["task_topic"] = task_topic
    if options.dry_run:
        print(f"DRY RUN: would write {json.dumps(state)} to {state_path}")
        return 0
    title_hook.write_state(state_path, state)
    print(f'Recorded {task_id} "{task_topic}" for conversation {session_id}.')

    # ---- 5. What happens next ---------------------------------------------------------------------------------------
    our_title = str(state.get("title") or "")
    if not title_hook.names_enabled(config):
        # workflow.conversation_names is false: the naming hook does nothing, so no name is announced or suggested
        # (no /rename line); the row recorded above is still read by the other workhub-claude skills.
        print("Conversation names are off (workflow.conversation_names is false): the row is recorded for the "
              "other workhub-claude skills, and the conversation keeps its name.")
    elif state.get("final"):
        # The name shown now: the transcript's latest "custom-title" record ("" when it cannot be read).
        shown_title = title_hook.latest_custom_title(find_transcript(config, session_id)) or ""
        if task_id in our_title.split():
            # The hook already named the conversation with this row.
            print(f'The name "{our_title}" already holds {task_id}; nothing else to do.')
        elif our_title and shown_title == our_title and title_hook.TAG_PATTERN.match(our_title):
            # The name is the hook's and unchanged: the hook adds the Task ID itself (its header, point 5).
            new_name = title_hook.name_with_task(our_title, task_id, task_topic)
            print(f'The naming hook will rename this conversation to "{new_name}" at the user\'s next message.')
        else:
            # The user chose the name, or it cannot be read: the hook leaves it alone, so the user pastes the new
            # name. The tags come from the shown name when it has the tag form (it keeps an "[ORCH]" role tag, for
            # example), else from the hook's own last name.
            base_title = shown_title if title_hook.TAG_PATTERN.match(shown_title) else our_title
            new_name = title_hook.name_with_task(base_title, task_id, task_topic)
            print("The name was set by the user (or cannot be read), so the hook will not change it. "
                  "Give the user this line to paste:")
            print(f"/rename {new_name}")
    else:
        future_topic = title_hook.clean_topic(task_id + " " + task_topic)
        future_tags = f"[...][{title_hook.machine_name(config)}]{title_hook.ROLE_TAG}"
        print(f'The naming hook will name this conversation "{future_tags} {future_topic}" '
              f"at the user's next message.")
    return 0


# Run main() only when executed as a script; its return value becomes the exit code.
if __name__ == "__main__":
    sys.exit(main())
