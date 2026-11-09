"""Tests for scripts/triage_conversations.py (the warm / redundant / finished triage).

PURPOSE
    Check every labelling rule of triage_conversations.py on small synthetic inputs: a fake
    conversations.json, a fake NOW.md, a fake conversation-title state folder, fake transcripts
    and a temporary settings file. No real conversation, transcript, settings file or Notion page
    is read or changed.

RUN
    python3 -m pytest -q <skill folder>/tests/test_triage_conversations.py

INPUTS AND OUTPUTS
    Everything is written into pytest's temporary folder (`tmp_path`) and removed by pytest.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# `importlib.util` loads a Python file by its path. It is needed because scripts/ is not a
# package (it has no __init__.py) and is not on Python's import path.
import importlib.util

# `json` writes the synthetic JSON inputs.
import json

# `os` builds paths and copies the environment for the command-line test.
import os

# `socket` gives this machine's name, as the collector records it.
import socket

# `subprocess` runs the script as a command, the way SKILL.md does; `sys.executable` is this Python.
import subprocess
import sys


# ---------------------------------------------------------------------------------------------
# Load the module under test
# ---------------------------------------------------------------------------------------------

# Path of the script: ../scripts/triage_conversations.py seen from this tests/ folder.
SCRIPT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "scripts", "triage_conversations.py")
# `spec_from_file_location(name, path)` describes how to load the file as a module named
# "triage_conversations"; `module_from_spec` creates the empty module object;
# `exec_module` runs the file's code inside it (main() does not run, because the module's
# __name__ is "triage_conversations", not "__main__").
SPEC = importlib.util.spec_from_file_location("triage_conversations", SCRIPT_PATH)
triage_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(triage_module)

# The collector's clock in the synthetic data (a fixed time, in seconds since 1970).
NOW_SECONDS = 1793811600.0
# One hour and one day in seconds, to place the synthetic messages in time.
HOUR = 3600.0
DAY = 86400.0
# The default Task ID patterns ("TK-42").
TK = triage_module.task_id_patterns("TK")


# ---------------------------------------------------------------------------------------------
# Helpers that build synthetic inputs
# ---------------------------------------------------------------------------------------------

def make_conversation(short_id, title, started, last_message, status="idle", background=None,
                      first_prompt="", last_reply="", processes=1, transcript="",
                      last_notice=None, compactions=0):
    """Build one conversation dictionary in the collector's schema-2 format.

    Parameters
        short_id (str): 8 characters; the session id becomes short_id + a fixed tail. The ids
            used below contain letters after "f", so they never look like real session ids.
        title (str): the conversation's name.
        started, last_message (float): seconds since 1970.
        status (str): "busy", "idle" or "background".
        background (list of str): running background shells.
        first_prompt, last_reply (str): digest texts.
        processes (int): how many windows have the conversation open.
        transcript (str): path of a transcript file ("" for none).
        last_notice (str or None): the digest's last background notice.
        compactions (int): number of context compactions.
    Returns
        dict: one conversation as collect_conversations.py writes it.
    """
    # One fake process per window; the first one carries the status.
    process_list = []
    for index in range(processes):
        process_list.append({"app": "VS Code" if index == 0 else "VS Code Remote-SSH",
                             "status": status if index == 0 else "idle"})
    # The digest stores [time, text] pairs, or None when there is nothing.
    return {
        "session_id": short_id + "-0000-0000-0000-000000000000",
        "title": title,
        "status": status,
        "processes": process_list,
        "background": background or [],
        "started": started,
        "last_message": last_message,
        "transcript": transcript,
        "digest": {
            "first_prompt": [started, first_prompt] if first_prompt else None,
            "last_reply": [last_message, last_reply] if last_reply else None,
            "last_notice": [last_message, last_notice] if last_notice else None,
            "compactions": compactions,
        },
    }


def write_transcript(folder, short_id, reply_text):
    """Write a two-line JSON-lines transcript whose last assistant reply is `reply_text`.

    Returns
        str: the path of the written file.
    """
    transcript_path = os.path.join(str(folder), short_id + ".jsonl")
    # Line 1: a user message (must be ignored). Line 2: an assistant reply with one text part
    # and one tool call (the tool call must be ignored).
    lines = [
        {"type": "user", "message": {"content": "hello"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": reply_text},
            {"type": "tool_use", "name": "Bash"}]}},
    ]
    with open(transcript_path, "w", encoding="utf-8") as transcript_file:
        for record in lines:
            transcript_file.write(json.dumps(record) + "\n")
    return transcript_path


NOW_MD_TEXT = """# NOW

## Running jobs

| Task ID | What | Machine | Owner | Ends |
|---|---|---|---|---|
| TK-7 | a job table row that must be ignored | ws01 | x | soon |

## Active rows (at most 4 with an open conversation)

| Task ID | Row | Owner | State |
|---|---|---|---|
| TK-5 | Row five | [UC] TK-5 Row five | merge next |
| TK-6 | Row six | [UC] TK-6 Row six | Done report not shown yet |
| TK-9 | Row nine | none | waits for a window |

Count: text after the table.
"""


def build_inputs(tmp_path):
    """Write NOW.md, a titles folder and transcripts, and return the collected data.

    Returns
        tuple (dict, str, str): (conversations.json content, NOW.md path, titles folder).
    """
    # NOW.md with three active rows (TK-5, TK-6, TK-9) and a job table that has TK-7.
    now_path = os.path.join(str(tmp_path), "NOW.md")
    with open(now_path, "w", encoding="utf-8") as now_file:
        now_file.write(NOW_MD_TEXT)
    # The conversation-title state folder: conversation "rowfive2" recorded TK-5 although its
    # automatic title has no Task ID.
    titles_dir = os.path.join(str(tmp_path), "titles")
    os.mkdir(titles_dir)
    with open(os.path.join(titles_dir, "rowfive2-0000-0000-0000-000000000000.json"), "w") as f:
        json.dump({"task": "TK-5", "title": "handover notes"}, f)
    # A transcript whose full last reply ends with a question; the digest holds only its start.
    question_transcript = write_transcript(
        tmp_path, "quiz0001",
        "The quiz is over. See https://www.notion.so/p/abc?pvs=204 for the row.\n"
        "Should I send the six issues to the orchestrator?")
    # The synthetic conversations, one per rule (comments give the expected label).
    conversations = [
        # Rule 1: this request (its shell runs the collector).
        make_conversation("selfself", "This request", NOW_SECONDS - 60, NOW_SECONDS, "busy",
                          background=["python3 collect_conversations.py -o c.json"]),
        # Rule 2: two orchestrators; the newer one is warm, the older one redundant.
        make_conversation("orchnew1", "[Jan-07 11:27][ws01][ORCH] Orchestrator",
                          NOW_SECONDS - HOUR, NOW_SECONDS - 60, processes=2, compactions=1),
        make_conversation("orchold1", "[Jan-06 14:15][ws01][ORCH] Orchestrator",
                          NOW_SECONDS - DAY, NOW_SECONDS - 2 * HOUR, compactions=8),
        # Rule 3: two conversations on TK-5; the newer one (state file) is busy and warm.
        make_conversation("rowfive1", "[Jan-06 17:21][ws01][UC] TK-5 Row five",
                          NOW_SECONDS - DAY, NOW_SECONDS - 10 * HOUR),
        make_conversation("rowfive2", "handover notes", NOW_SECONDS - 120, NOW_SECONDS, "busy"),
        # Rule 5: a background wait still runs.
        make_conversation("bgwait01", "TK-10 night launch", NOW_SECONDS - HOUR,
                          NOW_SECONDS - 600, "background", background=["sleep 23275"]),
        # Rule 6: TK-6 is an active row in NOW.md.
        make_conversation("rowsix01", "[Jan-06 20:12][ws01][UC] TK-6 Row six",
                          NOW_SECONDS - DAY, NOW_SECONDS - 12 * HOUR, compactions=2),
        # Rule 7: TK-7 is not active and its last reply says done -> finished.
        make_conversation("rowsvn01", "[Jan-06 21:14][ws01][UC] TK-7 Cleanup",
                          NOW_SECONDS - DAY, NOW_SECONDS - 14 * HOUR,
                          last_reply="TK-7 is now marked Done in the Work Hub."),
        # Rule 7: TK-8 is not active and nothing says done -> look closer.
        make_conversation("rowght01", "Work on TK-8", NOW_SECONDS - DAY, NOW_SECONDS - 14 * HOUR,
                          last_reply="I copied the files."),
        # Rule 8: the full reply (from the transcript) ends with a question -> look closer.
        make_conversation("quiz0001", "[Jan-06 22:47][ws01][UC] Quiz", NOW_SECONDS - DAY,
                          NOW_SECONDS - 12 * HOUR, last_reply="The quiz is over.",
                          transcript=question_transcript),
        # Rule 9: the last reply says /clear -> finished.
        make_conversation("header02", "Header change", NOW_SECONDS - DAY,
                          NOW_SECONDS - 13 * HOUR,
                          last_reply="This task is finished, so you can `/clear` now."),
        # Rule 10: stale for 6 days; the only "?" is inside a link -> finished, not a question.
        make_conversation("oldchat3", "[Jan-01 12:09][ws01] Old chat", NOW_SECONDS - 7 * DAY,
                          NOW_SECONDS - 6 * DAY,
                          last_reply="Uploaded: https://example.org/file/d/x?usp=sharing"),
        # Rule 11: recent, nothing decides it; its background task died with an old window.
        make_conversation("storage4", "Storage report", NOW_SECONDS - DAY, NOW_SECONDS - 16 * HOUR,
                          last_reply="When it finishes I'll tell you.",
                          last_notice="Background shell command didn't finish before the "
                                      "previous session ended"),
    ]
    # The collector's top-level structure, with one reached site that is also the local site.
    collected = {
        "schema": 2,
        "generated_at": NOW_SECONDS,
        "requested_from": {"site": "home"},
        "sites": [{"site": "home", "conversations": conversations},
                  {"site": "mycluster", "conversations": []}],
    }
    return collected, now_path, titles_dir


def labels_by_short_id(result):
    """Turn the triage result into {short id: label} for short assertions."""
    return {entry["short_id"]: entry["label"] for entry in result["conversations"]}


# The labels expected with every rule on (orchestrator enabled, NOW.md read).
EXPECTED_LABELS = {
    "selfself": "self",
    "orchnew1": "warm",
    "orchold1": "redundant",
    "rowfive1": "redundant",
    "rowfive2": "warm",
    "bgwait01": "warm",
    "rowsix01": "warm",
    "rowsvn01": "finished",
    "rowght01": "check",
    "quiz0001": "check",
    "header02": "finished",
    "oldchat3": "finished",
    "storage4": "check",
}


# ---------------------------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------------------------
# Most tests follow the same four steps, so they share this explanation:
#   1. `build_inputs(tmp_path)` writes the synthetic NOW.md, state folder and transcript and
#      returns (collected data, NOW.md path, titles folder); `_` receives a value not needed.
#   2. `read_now_active_rows(path, TK)` parses NOW.md's Active rows table (None when missing).
#   3. `triage(collected, now_rows, titles_dir, 2.0, "", TK, True)` labels every conversation,
#      with a stale limit of 2 days, the running request detected from its collector shell,
#      the "TK" prefix and the orchestrator on.
#   4. `assert condition` stops the test with a failure report when the condition is False;
#      pytest shows both sides of a failed `==` comparison.
# `tmp_path` is a pytest fixture: pytest passes a new empty temporary folder to each test.

def test_every_rule_gives_the_expected_label(tmp_path):
    """Each synthetic conversation gets the label written next to it in build_inputs."""
    collected, now_path, titles_dir = build_inputs(tmp_path)
    now_rows = triage_module.read_now_active_rows(now_path, TK)
    result = triage_module.triage(collected, now_rows, titles_dir, 2.0, "", TK, True)
    assert labels_by_short_id(result) == EXPECTED_LABELS


def test_orchestrator_off_skips_rule_two(tmp_path):
    """With the orchestrator off in the settings, [ORCH] in a name means nothing special."""
    collected, now_path, titles_dir = build_inputs(tmp_path)
    now_rows = triage_module.read_now_active_rows(now_path, TK)
    result = triage_module.triage(collected, now_rows, titles_dir, 2.0, "", TK, False)
    labels = labels_by_short_id(result)
    # Both former orchestrators are recent, have no row and no decisive reply: look closer.
    assert labels["orchnew1"] == "check"
    assert labels["orchold1"] == "check"
    # Every other label is unchanged.
    for short_id, label in EXPECTED_LABELS.items():
        if short_id not in ("orchnew1", "orchold1"):
            assert labels[short_id] == label


def test_custom_task_id_prefix(tmp_path):
    """Task IDs follow notion.task_id_prefix: with "JOB", "TK-5" is no Task ID but "JOB-5" is."""
    job = triage_module.task_id_patterns("JOB")
    now_path = os.path.join(str(tmp_path), "NOW.md")
    with open(now_path, "w", encoding="utf-8") as now_file:
        now_file.write(NOW_MD_TEXT.replace("TK-", "JOB-"))
    now_rows = triage_module.read_now_active_rows(now_path, job)
    assert list(now_rows) == ["JOB-5", "JOB-6", "JOB-9"]
    conversation = make_conversation("prefix01", "[Jan-06 20:12][ws01][UC] JOB-6 Row six", 0, 0)
    assert triage_module.find_task_id(conversation, str(tmp_path), job) == "JOB-6"
    old_style = make_conversation("prefix02", "TK-6 Row six", 0, 0)
    assert triage_module.find_task_id(old_style, str(tmp_path), job) == ""
    continued = make_conversation("prefix03", "chat", 0, 0, first_prompt="continue job-12 now")
    assert triage_module.find_task_id(continued, str(tmp_path), job) == "JOB-12"
    # A prefix with a regular-expression character is matched literally (re.escape).
    dotted = triage_module.task_id_patterns("A.B")
    assert dotted["any"].search("A.B-3 and AxB-4").group(1) == "3"


def test_reasons_and_notes(tmp_path):
    """Reasons name the newer conversation; notes appear only where they matter."""
    collected, now_path, titles_dir = build_inputs(tmp_path)
    now_rows = triage_module.read_now_active_rows(now_path, TK)
    result = triage_module.triage(collected, now_rows, titles_dir, 2.0, "", TK, True)
    # `{e["short_id"]: e for e in ...}` is a dictionary comprehension: short id -> entry.
    entries = {entry["short_id"]: entry for entry in result["conversations"]}
    # The redundant TK-5 conversation points to its newer one by name.
    assert "TK-5" in entries["rowfive1"]["reason"]
    assert "handover notes" in entries["rowfive1"]["reason"]
    # The question is quoted without the link that contained "?pvs=204".
    assert entries["quiz0001"]["reason"].endswith(
        "Should I send the six issues to the orchestrator?")
    # Two windows are noted; compactions only on warm conversations (not on the old ORCH).
    assert any("open in 2 windows" in note for note in entries["orchnew1"]["notes"])
    assert any("compacted 1 time " in note for note in entries["orchnew1"]["notes"])
    assert entries["orchold1"]["notes"] == []
    # The stopped background task is noted on the look-closer conversation.
    assert any("stopped with an earlier window" in note for note in entries["storage4"]["notes"])


def test_rows_without_window(tmp_path):
    """TK-9 is active in NOW.md but no conversation has it; TK-7 (job table only) is not listed."""
    collected, now_path, titles_dir = build_inputs(tmp_path)
    now_rows = triage_module.read_now_active_rows(now_path, TK)
    result = triage_module.triage(collected, now_rows, titles_dir, 2.0, "", TK, True)
    missing_ids = [row["task_id"] for row in result["rows_without_window"]]
    assert missing_ids == ["TK-9"]


def test_missing_now_file_skips_row_rules(tmp_path):
    """Without NOW.md, TK-6 is no longer warm by rule 6 and no rows are reported."""
    collected, _, titles_dir = build_inputs(tmp_path)
    now_rows = triage_module.read_now_active_rows(os.path.join(str(tmp_path), "absent.md"), TK)
    assert now_rows is None
    result = triage_module.triage(collected, now_rows, titles_dir, 2.0, "", TK, True)
    labels = labels_by_short_id(result)
    # TK-6 has no done marker and is 12 hours old: rule 11 (look closer).
    assert labels["rowsix01"] == "check"
    assert result["rows_without_window"] == []
    assert result["now_file_read"] is False


def test_now_table_parsing(tmp_path):
    """Only the Active rows section is read; header, separator and other tables are skipped."""
    _, now_path, _ = build_inputs(tmp_path)
    now_rows = triage_module.read_now_active_rows(now_path, TK)
    assert list(now_rows) == ["TK-5", "TK-6", "TK-9"]
    assert now_rows["TK-6"]["state"] == "Done report not shown yet"
    assert now_rows["TK-9"]["owner"] == "none"


def test_find_task_id_sources(tmp_path):
    """The Task ID comes from the state file, then the title (with or without hyphen), then
    a "continue TK-n" first request; a mere mention in the first request does not count."""
    _, _, titles_dir = build_inputs(tmp_path)
    state_conversation = make_conversation("rowfive2", "handover notes", 0, 0)
    assert triage_module.find_task_id(state_conversation, titles_dir, TK) == "TK-5"
    title_conversation = make_conversation("titlenum", "TK118 handover notes", 0, 0)
    assert triage_module.find_task_id(title_conversation, titles_dir, TK) == "TK-118"
    continue_conversation = make_conversation("continu6", "chat-7a", 0, 0,
                                              first_prompt="continue TK-110. Launch it.")
    assert triage_module.find_task_id(continue_conversation, titles_dir, TK) == "TK-110"
    mention_conversation = make_conversation("mention7", "Question", 0, 0,
                                             first_prompt="What happened in TK-110?")
    assert triage_module.find_task_id(mention_conversation, titles_dir, TK) == ""


def test_start_prompt_counts_as_orchestrator():
    """A new orchestrator that has not been renamed yet is recognised by its start prompt."""
    conversation = make_conversation("startorc", "START_ORCHESTRATOR_PROMPT.md", 0, 0,
                                     first_prompt="~/workhub/orchestrator/START_ORCHESTRATOR_PROMPT.md")
    assert triage_module.is_orchestrator(conversation) is True


def test_find_own_session_id_through_parent_processes():
    """A conversation whose process is an ancestor of this test (here: the test process itself)
    is found; one on another host, or with an unrelated pid, is not."""
    this_host = socket.gethostname().split(".")[0]
    own = make_conversation("0wn0wn00", "Running conversation", 0, 0)
    own["processes"] = [{"host": this_host, "pid": os.getpid()}]
    other_host = make_conversation("0ther000", "Elsewhere", 0, 0)
    other_host["processes"] = [{"host": "another-machine", "pid": os.getppid()}]
    collected = {"sites": [{"site": "home", "conversations": [other_host, own]}]}
    assert triage_module.find_own_session_id(collected) == own["session_id"]
    # Without the own conversation, nothing is found (the other one is on another host).
    collected_without_own = {"sites": [{"site": "home", "conversations": [other_host]}]}
    assert triage_module.find_own_session_id(collected_without_own) == ""


def test_shortened_collector_shell_still_counts_as_this_request():
    """The collector cuts shell text; "python3 scripts/collec" must still be recognised."""
    conversation = make_conversation("selfcut0", "Cut", 0, 0, "busy",
                                     background=["cd x && python3 scripts/collec"])
    assert triage_module.is_this_request(conversation, "") is True


def test_closing_question():
    """Questions are found only near the end and never inside links."""
    assert triage_module.closing_question("Done. Shall I go on?") == "Shall I go on?"
    assert triage_module.closing_question("See https://example.org/p?a=1 for it.") == ""
    long_reply = "Is this early? " + "x" * 700 + " The end."
    assert triage_module.closing_question(long_reply) == ""


def test_command_line_reads_paths_from_settings(tmp_path):
    """The script run as a command takes NOW.md, the title state folder and the prefix from a
    temporary settings file (WORKHUB_CLAUDE_CONFIG) and writes the JSON result."""
    collected, now_path, titles_dir = build_inputs(tmp_path)
    # The settings: orchestrator on, its folder = tmp_path (where NOW.md is), state under tmp_path.
    state_root = os.path.join(str(tmp_path), "state")
    os.makedirs(os.path.join(state_root, "conversation_titles"))
    # The title state of "rowfive2" goes where the settings say the title state is.
    os.replace(os.path.join(titles_dir, "rowfive2-0000-0000-0000-000000000000.json"),
               os.path.join(state_root, "conversation_titles", "rowfive2-0000-0000-0000-000000000000.json"))
    settings = {"notion": {"task_id_prefix": "TK"},
                "workflow": {"orchestrator": {"enabled": True, "folder": str(tmp_path)}},
                "paths": {"state_dir": state_root}}
    settings_path = os.path.join(str(tmp_path), "config.json")
    with open(settings_path, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file)
    collected_path = os.path.join(str(tmp_path), "conversations.json")
    with open(collected_path, "w", encoding="utf-8") as collected_file:
        json.dump(collected, collected_file)
    output_path = os.path.join(str(tmp_path), "triage.json")
    # `dict(os.environ, KEY=value)` copies the environment and adds one variable.
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=settings_path)
    completed = subprocess.run([sys.executable, SCRIPT_PATH, collected_path, "-o", output_path],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert "NOW.md read: %s" % now_path in completed.stdout
    with open(output_path, encoding="utf-8") as output_file:
        result = json.load(output_file)
    assert labels_by_short_id(result) == EXPECTED_LABELS


def test_command_line_without_settings_says_how_to_set_up(tmp_path):
    """No settings file: exit status 2 and the hint to run the setup skill."""
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=os.path.join(str(tmp_path), "absent.json"))
    collected_path = os.path.join(str(tmp_path), "conversations.json")
    with open(collected_path, "w", encoding="utf-8") as collected_file:
        json.dump({"schema": 2, "sites": []}, collected_file)
    completed = subprocess.run([sys.executable, SCRIPT_PATH, collected_path], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=60)
    assert completed.returncode == 2
    assert "set up my work hub" in completed.stderr
