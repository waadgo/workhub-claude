"""
test_conversation_task.py: tests for the naming hook conversation_title.py, its helper set_conversation_task.py and
the SessionStart hook session_start_system_name.py.

PURPOSE
    Check, on fake conversations in a temporary HOME with a temporary settings file (environment variable
    WORKHUB_CLAUDE_CONFIG), so that no real state or settings file is touched, that:
      1. without a Task ID the hook names conversations from the first words of the first prompt;
      2. a Task ID recorded during the first reply makes the name "[...][UC] TK-42 Data cleaning";
      3. a Task ID recorded AFTER the hook's name became final is added once at the user's next message, but only
         while the shown name (the fake transcript's latest "custom-title" record) is still the hook's;
      4. a name the user set (before or after the hook's) is never changed, and the helper then prints a /rename line;
      5. prompts the user did not type (<agent-message, <cross-session-message, <task-notification) change nothing;
      6. the helper refuses a different Task ID without --overwrite, and rejects a malformed one;
      7. --dry-run writes no state file;
      8. the settings are used: time zone (owner.time_zone), machine name (CC_CLUSTER and system_name.aliases), Task ID
         prefix (notion.task_id_prefix), role tag "[UC]", state folder (paths.state_dir);
      9. without a settings file the hooks do nothing and exit 0 (and the helper says how to set up, exit 2);
     10. with workflow.conversation_names false the naming hook does nothing at all (no output, no state, no rename),
         while the helper still records the row and prints no /rename line.

RUN
    python3 -m pytest -q tests/          (from the kit's hooks/ folder; needs pytest)

DEPENDENCIES
    pytest (its tmp_path fixture gives each test an empty temporary folder); the scripts run with the `python3` found
    on PATH, as Claude Code runs them.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# json: build the fake hook events and settings, read the state files. os, shutil, sys: environment, the python3
# path. subprocess: run the scripts as Claude Code would, as separate processes.
import json
import os
import shutil
import subprocess
import sys

# The scripts under test, in the folder above this tests folder.
HOOKS_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(HOOKS_FOLDER, "conversation_title.py")
HELPER = os.path.join(HOOKS_FOLDER, "set_conversation_task.py")
SESSION_START = os.path.join(HOOKS_FOLDER, "session_start_system_name.py")
# The interpreter Claude Code uses for the hooks ("python3" on PATH); the test interpreter as a fallback.
PYTHON = shutil.which("python3") or sys.executable
# The first time stamp of every fake transcript: 01:12 UTC on Nov 4 (= 01:12 in Lisbon, 10:12 in Tokyo,
# 20:12 on Nov 3 in New York).
FIRST_STAMP = "2026-11-04T01:12:00.000Z"


# ---------------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------------

def write_settings(home, **changes):
    """Write a small settings file inside the temporary HOME and return its path.

    The defaults: time zone Europe/Lisbon, Task ID prefix TK, conversation names on, state under <home>/state. Keyword
    arguments replace top-level sections, e.g. notion={"task_id_prefix": "PRJ"}.
    """
    settings = {
        "schema_version": 1,
        "owner": {"name": "Alex", "time_zone": "Europe/Lisbon"},
        "system_name": {"override_env": "CC_CLUSTER", "aliases": {}},
        "notion": {"task_id_prefix": "TK"},
        "workflow": {"conversation_names": True, "orchestrator": {"enabled": True}},
        "paths": {"state_dir": os.path.join(str(home), "state")},
    }
    settings.update(changes)
    path = os.path.join(str(home), "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle)
    return path


def fake_environment(home, settings_path=None):
    """Return an environment with HOME = the temporary folder, the settings file, and machine name "ws01".

    settings_path=None writes the default settings; "" points to a file that does not exist (kit not set up).
    """
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    if settings_path is None:
        # The default settings, written once per temporary HOME and then reused by every later call.
        default_path = os.path.join(str(home), "config.json")
        if not os.path.exists(default_path):
            write_settings(home)
        settings_path = default_path
    if not settings_path:
        # A path that does not exist: workhub-claude is not set up.
        settings_path = os.path.join(str(home), "missing", "config.json")
    environment["WORKHUB_CLAUDE_CONFIG"] = settings_path
    environment["CC_CLUSTER"] = "ws01"
    # The real conversation's IDs and Claude folder must not leak into the tests.
    for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CONFIG_DIR"):
        environment.pop(name, None)
    return environment


def transcript_path_for(home, session_id):
    """Return where Claude Code would keep the fake session's transcript inside the temporary HOME."""
    return os.path.join(str(home), ".claude", "projects", "-fake-project", session_id + ".jsonl")


def show_name(home, session_id, name):
    """Append a "custom-title" record to the fake transcript, as Claude Code does when a name is set or /rename runs.

    The first call also writes one record with a time stamp, like the top of a real transcript.
    """
    path = transcript_path_for(home, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as handle:
        if is_new:
            handle.write(json.dumps({"type": "user", "timestamp": FIRST_STAMP}) + "\n")
        handle.write(json.dumps({"type": "custom-title", "customTitle": name, "sessionId": session_id}) + "\n")


def start_transcript(home, session_id):
    """Write a fake transcript with only its first time-stamped record (no name yet)."""
    path = transcript_path_for(home, session_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "timestamp": FIRST_STAMP}) + "\n")


def run_hook(home, session_id, prompt, title="", with_transcript=False, settings_path=None):
    """Run the hook once with a fake UserPromptSubmit event; return the new title it sets, or "".

    with_transcript=False gives a path that does not exist (shown name unknown); True gives the fake transcript.
    """
    transcript = transcript_path_for(home, session_id) if with_transcript else "/nonexistent.jsonl"
    event = {"session_id": session_id, "transcript_path": transcript, "prompt": prompt,
             "session_title": title}
    result = subprocess.run([PYTHON, HOOK], input=json.dumps(event), capture_output=True, text=True,
                            env=fake_environment(home, settings_path), check=True)
    if not result.stdout.strip():
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"]["sessionTitle"]


def run_helper(home, *arguments, settings_path=None):
    """Run the helper; return (exit code, standard output)."""
    result = subprocess.run([PYTHON, HELPER, *arguments], capture_output=True, text=True,
                            env=fake_environment(home, settings_path))
    return result.returncode, result.stdout


def state_file(home, session_id):
    """Return the path of the hook's state file for a fake session (state folder from paths.state_dir)."""
    return os.path.join(str(home), "state", "conversation_titles", session_id + ".json")


def read_state(home, session_id):
    """Return the hook's state dict for a fake session."""
    with open(state_file(home, session_id), encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------------------------------------------------
# Behaviour kept from the original hook
# ---------------------------------------------------------------------------------------------------------------------

def test_without_task_the_name_is_from_the_first_words(tmp_path):
    # First prompt: nothing is set; second prompt: the first words of the first prompt (no transcript here).
    assert run_hook(tmp_path, "s1", "Check the fold sync") == ""
    title = run_hook(tmp_path, "s1", "second message")
    assert title.endswith("][ws01][UC] Check the fold sync")
    assert "TK-" not in title


def test_task_recorded_in_first_reply_goes_into_the_name(tmp_path):
    assert run_hook(tmp_path, "s2", "continue the data cleaning") == ""
    code, output = run_helper(tmp_path, "--session-id", "s2", "--task", "TK-42", "--topic", "Data cleaning")
    assert code == 0 and "at the user's next message" in output
    title = run_hook(tmp_path, "s2", "second message")
    assert title.endswith("][UC] TK-42 Data cleaning")
    # The task fields stay in the final state.
    state = read_state(tmp_path, "s2")
    assert state["final"] is True and state["task"] == "TK-42"


def test_long_topic_keeps_the_task_id(tmp_path):
    run_hook(tmp_path, "s3", "first")
    run_helper(tmp_path, "--session-id", "s3", "--task", "TK-12345",
               "--topic", "Müller [real] pair scoring with many extra words")
    title = run_hook(tmp_path, "s3", "second")
    topic = title.split("][UC] ", 1)[1]
    assert topic.startswith("TK-12345 Muller")
    assert len(topic) <= 28 and "[" not in topic


def test_task_after_naming_is_added_once_at_the_users_next_message(tmp_path):
    run_hook(tmp_path, "s4", "Storage handover notes", with_transcript=True)
    first_name = run_hook(tmp_path, "s4", "second", with_transcript=True)
    # Claude Code writes the name the hook set into the transcript.
    show_name(tmp_path, "s4", first_name)
    code, output = run_helper(tmp_path, "--session-id", "s4", "--task", "TK-7", "--topic", "Storage notes")
    expected = first_name.split("][UC] ", 1)[0] + "][UC] TK-7 Storage notes"
    assert code == 0 and f'rename this conversation to "{expected}"' in output and "/rename" not in output
    # A subagent report arriving first changes nothing.
    assert run_hook(tmp_path, "s4", '<agent-message from="a1">\nreport', with_transcript=True) == ""
    # The user's next message: the hook adds the Task ID, keeping the tags.
    assert run_hook(tmp_path, "s4", "third", with_transcript=True) == expected
    show_name(tmp_path, "s4", expected)
    # Only once.
    assert run_hook(tmp_path, "s4", "fourth", with_transcript=True) == ""
    state = read_state(tmp_path, "s4")
    assert state["title"] == expected and state["task_title_checked"] == "TK-7"


def test_task_after_naming_keeps_a_name_the_user_set(tmp_path):
    run_hook(tmp_path, "s8", "Storage handover notes", with_transcript=True)
    first_name = run_hook(tmp_path, "s8", "second", with_transcript=True)
    show_name(tmp_path, "s8", first_name)
    # The user renames the conversation with /rename (the orchestrator, for example).
    user_name = first_name.split("][UC] ", 1)[0] + "][ORCH] Orchestrator"
    show_name(tmp_path, "s8", user_name)
    code, output = run_helper(tmp_path, "--session-id", "s8", "--task", "TK-7", "--topic", "Storage notes")
    # The helper gives a /rename line that keeps the user's role tag; the hook changes nothing.
    expected = first_name.split("][UC] ", 1)[0] + "][ORCH] TK-7 Storage notes"
    assert code == 0 and f"/rename {expected}" in output
    assert run_hook(tmp_path, "s8", "third", with_transcript=True) == ""
    assert read_state(tmp_path, "s8")["task_title_checked"] == "TK-7"


def test_task_after_naming_with_unknown_shown_name_changes_nothing(tmp_path):
    run_hook(tmp_path, "s9", "Storage handover notes")
    first_name = run_hook(tmp_path, "s9", "second")
    # No transcript: the shown name cannot be read, so the helper falls back to a /rename line ...
    code, output = run_helper(tmp_path, "--session-id", "s9", "--task", "TK-7", "--topic", "Storage notes")
    expected = first_name.split("][UC] ", 1)[0] + "][UC] TK-7 Storage notes"
    assert code == 0 and f"/rename {expected}" in output
    # ... and the hook changes nothing and does not mark the row as handled (it looks again next time).
    assert run_hook(tmp_path, "s9", "third") == ""
    assert "task_title_checked" not in read_state(tmp_path, "s9")


def test_prompts_the_user_did_not_type_are_ignored(tmp_path):
    assert run_hook(tmp_path, "s10", "Check the fold sync") == ""
    for prompt in ('<agent-message from="ac59">\n[Subagent hand-back] done',
                   '<cross-session-message from="uds:/run/x.sock" from-name="peer">\nhello',
                   "<task-notification>\n<task-id>b8ab</task-id>"):
        assert run_hook(tmp_path, "s10", prompt) == ""
    # The name is still not set: only the user's second message sets it.
    assert "final" not in read_state(tmp_path, "s10")
    # A prompt with editor context in front is the user's own message.
    title = run_hook(tmp_path, "s10", "<ide_opened_file>The user opened x.py</ide_opened_file> and this")
    assert title.endswith("][UC] Check the fold sync")


def test_user_rename_before_the_second_message_is_kept(tmp_path):
    run_hook(tmp_path, "s11", "first", with_transcript=True)
    # The user types /rename before their second message; session_title is empty, only the transcript shows the name.
    show_name(tmp_path, "s11", "My own name")
    assert run_hook(tmp_path, "s11", "second", with_transcript=True) == ""
    assert read_state(tmp_path, "s11")["final"] is True


def test_helper_refuses_a_second_row_and_bad_ids(tmp_path):
    run_helper(tmp_path, "--session-id", "s5", "--task", "TK-1", "--topic", "One")
    code, output = run_helper(tmp_path, "--session-id", "s5", "--task", "TK-2", "--topic", "Two")
    assert code == 3 and "REFUSED" in output
    code, _ = run_helper(tmp_path, "--session-id", "s5", "--task", "TK-2", "--topic", "Two", "--overwrite")
    assert code == 0 and read_state(tmp_path, "s5")["task"] == "TK-2"
    for bad in ("42", "TK42", "T-42", "TK-123456", "TK-4a"):
        code, _ = run_helper(tmp_path, "--session-id", "s5", "--task", bad, "--topic", "x")
        assert code == 2


def test_dry_run_writes_no_state_file(tmp_path):
    code, output = run_helper(tmp_path, "--session-id", "s6", "--task", "TK-3", "--topic", "Dry", "--dry-run")
    assert code == 0 and "DRY RUN" in output
    assert not os.path.exists(state_file(tmp_path, "s6"))


def test_user_rename_keeps_task_and_stays_final(tmp_path):
    run_hook(tmp_path, "s7", "first")
    run_helper(tmp_path, "--session-id", "s7", "--task", "TK-9", "--topic", "Nine")
    # The user renamed the conversation by hand before the second prompt: the hook stops for good, task kept.
    assert run_hook(tmp_path, "s7", "second", title="My own name") == ""
    state = read_state(tmp_path, "s7")
    assert state["final"] is True and state["task"] == "TK-9"


# ---------------------------------------------------------------------------------------------------------------------
# Settings-driven behaviour of the kit version
# ---------------------------------------------------------------------------------------------------------------------

def test_time_zone_comes_from_the_settings(tmp_path):
    # Lisbon is at UTC+0 in November; Tokyo at UTC+9; New York at UTC-5 (the day changes).
    expected_by_zone = {"Europe/Lisbon": "[Nov-04 01:12]", "Asia/Tokyo": "[Nov-04 10:12]",
                        "America/New_York": "[Nov-03 20:12]", "Not/AZone": "[Nov-04 01:12]"}
    for number, (zone, expected) in enumerate(expected_by_zone.items()):
        home = tmp_path / ("zone" + str(number))
        home.mkdir()
        settings_path = write_settings(home, owner={"name": "Alex", "time_zone": zone})
        session_id = "z" + str(number)
        start_transcript(home, session_id)
        run_hook(home, session_id, "first words", with_transcript=True, settings_path=settings_path)
        title = run_hook(home, session_id, "second", with_transcript=True, settings_path=settings_path)
        # An unknown zone name falls back to UTC (same as Lisbon in November).
        assert title.startswith(expected + "[ws01][UC] "), (zone, title)


def test_machine_alias_and_prefix_come_from_the_settings(tmp_path):
    settings_path = write_settings(tmp_path, system_name={"override_env": "CC_CLUSTER", "aliases": {"ws01": "lab1"}},
                                   notion={"task_id_prefix": "PRJ"})
    run_hook(tmp_path, "p1", "first", settings_path=settings_path)
    # The prefix of the settings is required: "TK-7" is refused, "PRJ-7" accepted.
    code, output = run_helper(tmp_path, "--session-id", "p1", "--task", "TK-7", "--topic", "x",
                              settings_path=settings_path)
    assert code == 2 and '"PRJ-42"' in output
    code, output = run_helper(tmp_path, "--session-id", "p1", "--task", "PRJ-7", "--topic", "Seven",
                              settings_path=settings_path)
    assert code == 0 and "[...][lab1][UC] PRJ-7 Seven" in output
    title = run_hook(tmp_path, "p1", "second", settings_path=settings_path)
    # Machine "ws01" renamed to "lab1".
    assert title.endswith("][lab1][UC] PRJ-7 Seven")


def test_names_off_the_hook_does_nothing_and_the_helper_only_records(tmp_path):
    settings_path = write_settings(tmp_path, workflow={"conversation_names": False})
    state_folder = os.path.join(str(tmp_path), "state")
    # The hook prints nothing and writes no state, at every prompt.
    for prompt in ("first words", "second", "third"):
        assert run_hook(tmp_path, "o1", prompt, settings_path=settings_path) == ""
    assert not os.path.exists(state_folder)
    # The helper still records the row (other skills read it), says names are off, and gives no /rename line.
    code, output = run_helper(tmp_path, "--session-id", "o1", "--task", "TK-9", "--topic", "Nine",
                              settings_path=settings_path)
    assert code == 0 and "names are off" in output and "/rename" not in output
    assert read_state(tmp_path, "o1") == {"task": "TK-9", "task_topic": "Nine"}
    # Later prompts still change nothing: no name, and the recorded row stays as it is.
    assert run_hook(tmp_path, "o1", "fourth", settings_path=settings_path) == ""
    assert read_state(tmp_path, "o1") == {"task": "TK-9", "task_topic": "Nine"}
    assert sorted(os.listdir(os.path.join(state_folder, "conversation_titles"))) == ["o1.json"]


def test_names_turned_off_after_a_user_name_gives_no_rename_line(tmp_path):
    # Names on: the hook names the conversation, then the user renames it (the case that normally gives /rename).
    run_hook(tmp_path, "o2", "Storage handover notes", with_transcript=True)
    run_hook(tmp_path, "o2", "second", with_transcript=True)
    show_name(tmp_path, "o2", "My own name")
    # The user then switches names off: the helper records the row and prints no /rename line.
    settings_path = write_settings(tmp_path, workflow={"conversation_names": False})
    code, output = run_helper(tmp_path, "--session-id", "o2", "--task", "TK-3", "--topic", "Three",
                              settings_path=settings_path)
    assert code == 0 and "names are off" in output and "/rename" not in output
    assert read_state(tmp_path, "o2")["task"] == "TK-3"
    assert run_hook(tmp_path, "o2", "third", with_transcript=True, settings_path=settings_path) == ""


def test_without_settings_the_hooks_do_nothing(tmp_path):
    # settings_path="" points to a file that does not exist: workhub-claude is not set up on this machine.
    for prompt in ("first", "second", "third"):
        assert run_hook(tmp_path, "n1", prompt, settings_path="") == ""
    # Nothing was written anywhere in the temporary HOME.
    assert sorted(os.listdir(tmp_path)) == []
    result = subprocess.run([PYTHON, SESSION_START], input="{}", capture_output=True, text=True,
                            env=fake_environment(tmp_path, ""))
    assert result.returncode == 0 and result.stdout == ""
    # The helper explains how to set up and stops with exit code 2.
    code, output = run_helper(tmp_path, "--session-id", "n1", "--task", "TK-1", "--topic", "x", settings_path="")
    assert code == 2 and "set up my work hub" in output


def test_broken_settings_file_fails_open(tmp_path):
    broken = tmp_path / "config.json"
    broken.write_text("{ not json", encoding="utf-8")
    event = {"session_id": "b1", "transcript_path": "/nonexistent.jsonl", "prompt": "hello"}
    result = subprocess.run([PYTHON, HOOK], input=json.dumps(event), capture_output=True, text=True,
                            env=fake_environment(tmp_path, str(broken)))
    # Exit code 0 and no output: the prompt goes through untouched.
    assert result.returncode == 0 and result.stdout == ""


def test_session_start_prints_the_system_name(tmp_path):
    settings_path = write_settings(tmp_path, system_name={"override_env": "CC_CLUSTER", "aliases": {"ws01": "lab1"}})
    result = subprocess.run([PYTHON, SESSION_START], input="{}", capture_output=True, text=True,
                            env=fake_environment(tmp_path, settings_path))
    assert result.returncode == 0 and result.stdout == "System name for file paths: lab1\n"


def test_state_folder_is_private_and_changes_are_logged(tmp_path):
    run_hook(tmp_path, "l1", "first")
    run_hook(tmp_path, "l1", "second")
    folder = os.path.join(str(tmp_path), "state", "conversation_titles")
    # Mode 700: only the owner can list or open the folder.
    assert os.stat(folder).st_mode & 0o777 == 0o700
    with open(os.path.join(folder, "changes.log"), encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle]
    assert len(lines) == 1 and lines[0]["session"] == "l1" and lines[0]["machine"] == "ws01"
