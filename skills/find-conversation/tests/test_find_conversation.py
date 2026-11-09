"""Smoke tests of find_conversation.py on small synthetic transcripts (no real conversation is read).

PURPOSE
    Each test writes fake Claude Code and Codex transcripts and a temporary settings file into pytest's temporary
    folder (`tmp_path`, a new empty folder per test), runs the script on them with --sites local --json (so no other
    site is contacted), and checks the JSON answer. The settings file is passed with $WORKHUB_CLAUDE_CONFIG, and
    $CC_CLUSTER=ws01 fixes this machine's name.

RUN
    python3 -m pytest -q <skill folder>/tests/test_find_conversation.py
    (needs a Python whose sqlite3 has FTS5, as the script itself)

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# `importlib.util` loads the script as a module (without running main()) for the tests of its small functions.
import importlib.util
# `json` writes the fake transcript lines and the settings, and reads the script's --json answer.
import json
# `os` builds paths.
import os
# `subprocess` runs the script exactly as the skill does, as a separate program.
import subprocess
# `sys.executable` is the Python running the tests, so the script runs with the same interpreter.
import sys

# Absolute path of the script under test: this file is tests/test_find_conversation.py, the script is
# scripts/find_conversation.py next to the tests folder.
SCRIPTS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
SCRIPT_PATH = os.path.join(SCRIPTS_FOLDER, "find_conversation.py")

# `spec_from_file_location` + `exec_module` load the script as a module; main() does not run (it runs only when the
# file is executed as a program).
SPEC = importlib.util.spec_from_file_location("find_conversation", SCRIPT_PATH)
find_conversation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(find_conversation)

# Session ids of the fixture. They contain letters that are not hexadecimal, so they cannot be mistaken for real ids.
SURVEY_ID = "testconv-0000-0000-0000-000000000001"
ENDPOINT_ID = "testconv-0000-0000-0000-000000000002"
CODEX_ID = "testcdx0-0000-0000-0000-000000000003"
# The hook-style name of the first conversation: "[date][machine][role] Task ID + topic".
SURVEY_NAME = "[Jan-05 11:15][ws02][UC] TK-42 Survey cleaning"


def write_jsonl(path, records, compact=True):
    """Write `records` (a list of dictionaries) as JSON Lines: one JSON object per line.

    compact=True writes no spaces after "," and ":" (separators=(",", ":")), as Claude Code does; the script's quick
    byte filter looks for '"type":"user"' in exactly that form. Codex files are written with spaces (compact=False).
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    separators = (",", ":") if compact else (", ", ": ")
    with open(path, "w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, separators=separators) + "\n")


def claude_records(session_title, request_text, reply_text, command_text):
    """Return the records of a minimal Claude Code transcript with one request, one reply and one Bash call."""
    records = [
        {"type": "user", "timestamp": "2026-01-05T02:15:00.000Z", "cwd": "/home/alex/projects/demo",
         "message": {"role": "user", "content": request_text}},
        {"type": "assistant", "timestamp": "2026-01-05T02:16:00.000Z", "cwd": "/home/alex/projects/demo",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": reply_text},
             {"type": "tool_use", "id": "t1", "name": "Bash",
              "input": {"command": command_text, "description": "run it"}}]}},
        # A tool result: its text must NOT be indexed (outputs are skipped).
        {"type": "user", "timestamp": "2026-01-05T02:16:30.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": "zebracorn appears only in an output"}]}},
    ]
    if session_title:
        records.append({"type": "custom-title", "customTitle": session_title, "sessionId": "x"})
    return records


def make_fixture(tmp_path):
    """Create two Claude Code conversations and one Codex conversation; return (claude_root, codex_home)."""
    claude_root = os.path.join(str(tmp_path), "claude_projects")
    codex_home = os.path.join(str(tmp_path), "codex_home")
    write_jsonl(os.path.join(claude_root, "-home-alex-projects-demo", SURVEY_ID + ".jsonl"),
                claude_records(SURVEY_NAME, "clean the survey tables and convert the dates",
                               "The cleaned tables keep every row.", "python3 clean_tables.py survey.csv"))
    # The second conversation has no name of its own: the conversation-title state gives it one (see settings).
    write_jsonl(os.path.join(claude_root, "-home-alex-projects-demo", ENDPOINT_ID + ".jsonl"),
                claude_records("", "set up a file transfer endpoint on the workstation",
                               "Done: the endpoint is running.", "transfer endpoint list"))
    write_jsonl(os.path.join(codex_home, "sessions", "2025", "12", "20",
                             "rollout-2025-12-20T15-23-59-" + CODEX_ID + ".jsonl"), [
        {"timestamp": "2025-12-20T19:24:00.000Z", "type": "session_meta",
         "payload": {"id": CODEX_ID, "cwd": "/home/alex"}},
        {"timestamp": "2025-12-20T19:25:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "train the forecast model on fold 3"}},
        {"timestamp": "2025-12-20T19:26:00.000Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "Fold 3 training started."}}], compact=False)
    write_jsonl(os.path.join(codex_home, "session_index.jsonl"),
                [{"id": CODEX_ID, "thread_name": "Forecast fold 3 training"}])
    return claude_root, codex_home


def write_settings(tmp_path, include_codex=True, conversation_names=True):
    """Write a temporary settings file for the fixture and the conversation-title state; return the file's path."""
    claude_root = os.path.join(str(tmp_path), "claude_projects")
    codex_home = os.path.join(str(tmp_path), "codex_home")
    state_folder = os.path.join(str(tmp_path), "state")
    settings = {
        "owner": {"name": "Alex", "time_zone": "Asia/Tokyo"},
        "conversations": {"claude_projects_dir": claude_root, "include_codex": include_codex,
                          "codex_sessions_dir": os.path.join(codex_home, "sessions")},
        "paths": {"state_dir": state_folder},
        "workflow": {"conversation_names": conversation_names},
        "machines": {
            "workstations": [{"name": "ws01", "ssh_alias": "", "shares_home_with_this_machine": True},
                             {"name": "ws02", "ssh_alias": "", "shares_home_with_this_machine": True}],
            "clusters": [{"name": "mycluster", "ssh_alias": "mycluster", "shares_home_with_this_machine": False}],
        },
    }
    settings_path = os.path.join(str(tmp_path), "config.json")
    with open(settings_path, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file)
    # The conversation-title state: the second conversation recorded row TK-77 and has the hook's fallback name.
    titles_folder = os.path.join(state_folder, "conversation_titles")
    os.makedirs(titles_folder, exist_ok=True)
    with open(os.path.join(titles_folder, ENDPOINT_ID + ".json"), "w", encoding="utf-8") as state_file:
        json.dump({"task": "TK-77", "task_topic": "Transfer endpoint", "title": "Endpoint setup"}, state_file)
    return settings_path


def run_search(tmp_path, words, extra_options=(), environment_changes=None, settings_path=None, check=True):
    """Run the script with --sites local --json and return the completed process (stdout parsed when check=True)."""
    if settings_path is None:
        settings_path = write_settings(tmp_path)
    command = [sys.executable, SCRIPT_PATH, "--sites", "local", "--json"] + list(extra_options) + list(words)
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=settings_path, CC_CLUSTER="ws01")
    # The tests may run inside a Claude Code shell; its session id must not hide anything here unless a test sets it.
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    if environment_changes:
        environment.update(environment_changes)
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=120)
    if not check:
        return completed
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return json.loads(completed.stdout.decode("utf-8"))


def test_best_match_has_all_words_and_name_date_machine(tmp_path):
    """The conversation with all the words comes first, with its name, machine tag and local start time."""
    make_fixture(tmp_path)
    answer = run_search(tmp_path, ["survey", "table", "converting"])
    best = answer["hits"][0]
    assert best["name"] == SURVEY_NAME
    # The machine comes from the name's tag; the start is shown in the owner's zone (02:15 UTC = 11:15 in Tokyo).
    assert best["system"] == "ws02"
    assert best["started_text"] == "Mon Jan 05 2026 11:15"
    assert answer["time_zone"] == "Asia/Tokyo"
    assert best["session_id"] == SURVEY_ID
    assert best["folder"] == "/home/alex/projects/demo"
    # "converting" matches the request's "convert" (Porter stem "convert"); "table" matches "tables".
    assert set(best["words_found"]) == {"survey", "table", "converting"}


def test_codex_conversation_found_with_thread_name(tmp_path):
    """A Codex thread is found and shown with its name from session_index.jsonl."""
    make_fixture(tmp_path)
    answer = run_search(tmp_path, ["forecast", "fold"])
    assert answer["hits"][0]["name"] == "Forecast fold 3 training"
    assert answer["hits"][0]["source"] == "codex"


def test_codex_left_out_when_not_included(tmp_path):
    """conversations.include_codex = false: Codex threads are not indexed (and leave the index when switched off)."""
    make_fixture(tmp_path)
    assert run_search(tmp_path, ["forecast"])["hits"]
    settings_path = write_settings(tmp_path, include_codex=False)
    assert run_search(tmp_path, ["forecast"], settings_path=settings_path)["hits"] == []


def test_tool_outputs_are_not_indexed(tmp_path):
    """A word that appears only in a tool output is not found."""
    make_fixture(tmp_path)
    assert run_search(tmp_path, ["zebracorn"])["hits"] == []


def test_task_from_the_title_state_is_searchable(tmp_path):
    """The Task ID and topic recorded in the conversation-title state are indexed with the name."""
    make_fixture(tmp_path)
    answer = run_search(tmp_path, ["77"])
    assert len(answer["hits"]) == 1
    hit = answer["hits"][0]
    assert hit["session_id"] == ENDPOINT_ID and hit["task"] == "TK-77"
    # The state's "title" is the name when the transcript has none.
    assert hit["name"] == "Endpoint setup"


def test_without_name_tags_the_machine_is_this_one(tmp_path):
    """workflow.conversation_names = false: names are not read for a machine tag; this machine's name is shown."""
    make_fixture(tmp_path)
    settings_path = write_settings(tmp_path, conversation_names=False)
    best = run_search(tmp_path, ["survey"], settings_path=settings_path)["hits"][0]
    assert best["system"] == "ws01"


def test_new_lines_are_found_after_the_transcript_grows(tmp_path):
    """Only the transcript that grew is read again, and its new words are found."""
    claude_root, _codex_home = make_fixture(tmp_path)
    assert run_search(tmp_path, ["renderer"])["hits"] == []
    transcript_path = os.path.join(claude_root, "-home-alex-projects-demo", ENDPOINT_ID + ".jsonl")
    with open(transcript_path, "a", encoding="utf-8") as transcript_file:
        new_record = {"type": "user", "timestamp": "2026-01-06T15:00:00.000Z",
                      "message": {"role": "user", "content": "how much memory does the renderer need"}}
        transcript_file.write(json.dumps(new_record, separators=(",", ":")) + "\n")
    answer = run_search(tmp_path, ["renderer"])
    assert answer["hits"][0]["name"] == "Endpoint setup"
    assert "re-read 1 changed" in answer["note"]


def test_deleted_transcript_disappears(tmp_path):
    """A deleted transcript leaves the index at the next search."""
    claude_root, _codex_home = make_fixture(tmp_path)
    assert len(run_search(tmp_path, ["endpoint"])["hits"]) == 1
    os.remove(os.path.join(claude_root, "-home-alex-projects-demo", ENDPOINT_ID + ".jsonl"))
    assert run_search(tmp_path, ["endpoint"])["hits"] == []


def test_current_conversation_is_left_out_unless_asked(tmp_path):
    """The conversation running the search ($CLAUDE_CODE_SESSION_ID) is left out unless --include-current."""
    make_fixture(tmp_path)
    own_id = {"CLAUDE_CODE_SESSION_ID": ENDPOINT_ID}
    assert run_search(tmp_path, ["endpoint"], environment_changes=own_id)["hits"] == []
    with_current = run_search(tmp_path, ["endpoint"], ["--include-current"], own_id)
    assert len(with_current["hits"]) == 1


def test_date_filter(tmp_path):
    """--after removes conversations whose last message is earlier."""
    make_fixture(tmp_path)
    # The Codex conversation is from Dec 20, 2025; --after 2026-01-01 removes it.
    assert run_search(tmp_path, ["forecast"], ["--after", "2026-01-01"])["hits"] == []


def test_index_lives_in_the_state_folder(tmp_path):
    """The default index is <paths.state_dir>/find-conversation/index.sqlite, private to the owner."""
    make_fixture(tmp_path)
    run_search(tmp_path, ["survey"])
    index_path = os.path.join(str(tmp_path), "state", "find-conversation", "index.sqlite")
    assert os.path.isfile(index_path)
    # `& 0o777` keeps only the permission bits; 0o600 = owner read/write only.
    assert os.stat(index_path).st_mode & 0o777 == 0o600


def test_no_settings_file_exits_2(tmp_path):
    """Without a settings file the script stops with status 2 and the setup hint."""
    missing_path = os.path.join(str(tmp_path), "no_such_config.json")
    completed = run_search(tmp_path, ["survey"], settings_path=missing_path, check=False)
    assert completed.returncode == 2
    assert "set up my work hub" in completed.stderr.decode("utf-8")


def test_remote_run_reads_no_settings_and_runs_from_stdin(tmp_path):
    """The copy sent to another site (`python3 - --remote-run ...`) needs no settings file and no file of its own."""
    claude_root, _codex_home = make_fixture(tmp_path)
    with open(SCRIPT_PATH, "rb") as script_file:
        script_bytes = script_file.read()
    command = [sys.executable, "-", "--remote-run", "--sites", "local", "--json",
               "--index-path", os.path.join(str(tmp_path), "remote_index.sqlite"), "--claude-root", claude_root,
               "--codex-root", "", "--titles-dir", "", "--time-zone", "UTC", "--system-name", "mycluster",
               "--no-name-tags", "--", "survey"]
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=os.path.join(str(tmp_path), "no_such_config.json"))
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    completed = subprocess.run(command, input=script_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment, timeout=120, cwd=str(tmp_path))
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    answer = json.loads(completed.stdout.decode("utf-8"))
    best = answer["hits"][0]
    assert best["system"] == "mycluster" and best["started_text"] == "Mon Jan 05 2026 02:15"


def test_remote_sites_and_command_from_settings():
    """Other sites = clusters and own-home workstations with an alias; the remote command is shell-quoted."""
    sys.path.insert(0, SCRIPTS_FOLDER)
    import workhub_config
    config = {"machines": {
        "workstations": [{"name": "ws01", "ssh_alias": "", "shares_home_with_this_machine": True},
                         {"name": "ws02", "ssh_alias": "ws02", "shares_home_with_this_machine": True},
                         {"name": "ws03", "ssh_alias": "ws03.example.org", "shares_home_with_this_machine": False},
                         {"name": "ws04", "ssh_alias": "", "shares_home_with_this_machine": False}],
        "clusters": [{"name": "mycluster", "ssh_alias": "mc", "shares_home_with_this_machine": False},
                     {"name": "othercluster", "ssh_alias": "", "shares_home_with_this_machine": False}]}}
    sites = find_conversation.remote_sites_from_settings(workhub_config, config, "ws01")
    assert sites == [("ws03", "ws03.example.org"), ("mycluster", "mc"), ("othercluster", "othercluster")]
    # "local" never contacts anything.
    assert find_conversation.open_sites("local", sites) == ([], [])

    class Options:
        """The few options remote_command_words() reads."""
        top = 5
        time_zone = "Asia/Tokyo"
        name_tags = False
        after = "2026-01-01"
        before = None
        rebuild = False
        question = ["Alex's", "survey"]

    portable = {"index": "~/.local/state/workhub-claude/find-conversation/index.sqlite",
                "claude": "~/.claude/projects", "codex": "", "titles": "~/.local/state/workhub-claude/conversation_titles"}
    words = find_conversation.remote_command_words("mycluster", Options, portable)
    assert words[:4] == ["python3", "-", "--remote-run", "--sites"]
    assert "--no-name-tags" in words and "''" in words
    # The quote in the question is kept by shell quoting; "--" comes before the question words.
    assert words[-3:] == ["--", "'Alex'\"'\"'s'", "survey"]
    # Paths of this machine that do not start at the home folder fall back to the kit's defaults elsewhere.
    assert find_conversation.portable_home_path("/fast/disk/claude", "~/.claude/projects") == "~/.claude/projects"
    assert find_conversation.portable_home_path("~/alt/claude", "~/.claude/projects") == "~/alt/claude"
    assert find_conversation.codex_home_from_setting("~/.codex/sessions") == "~/.codex"
