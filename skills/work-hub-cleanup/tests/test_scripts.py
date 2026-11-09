#!/usr/bin/env python3
"""
test_scripts.py - smoke tests of the work-hub-cleanup scripts on synthetic data.

PURPOSE
    Check, without touching any real transcript, settings file, log, cluster or Notion page, that:
      - session_digest.py finds the turns of a period in a Claude Code transcript and in a
        Codex rollout, skips injected context, sub-agent records and housekeeping commands,
        keeps the answers to questions and the job notices, takes its folders, time zone,
        machine name and Task ID prefix from the settings, refuses to overwrite outputs, runs
        as `python3 -` on a "remote" machine, and reads other machines only through `ssh`
        (a fake `ssh` here) without any prompt;
      - cleanup_log.py keeps its log in the settings' state folder, records a cleanup,
        proposes the right --since for the next one, prints the UTC Notion filter, finds the
        next free CL code (also in the orchestrator's files when it is on) and prints the
        resolved settings;
      - live_state.py takes its machines from the settings, parses squeue, sacct, tmux,
        nvidia-smi and ps output, runs the Slurm commands with TZ=UTC, converts their times to
        the user's zone, and handles closed connections, hops and other schedulers.

INPUTS
    None: every input (settings file, transcripts, fake `ssh`, `squeue`, `sacct`, `tmux`,
    `nvidia-smi`, `ps` and `python3` programs) is written into a temporary folder by the tests.
    The environment variable WORKHUB_CLAUDE_CONFIG points the scripts to the temporary settings
    file, so the user's real settings are never read.

OUTPUTS
    Test results on the screen. The temporary folders are deleted afterwards.

USAGE
    python3 -m pytest tests -q              # from the skill folder
    python3 tests/test_scripts.py -v        # without pytest, one line per test

DEPENDENCIES
    Python >= 3.9, standard library only (`unittest`; pytest runs unittest classes too).
    The fake programs are small bash scripts, so bash must exist (any Linux or macOS).

EXIT STATUS
    0 when every test passes, 1 otherwise (unittest's convention).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library)
# ----------------------------------------------------------------------------------------------

# `importlib.util` loads a Python file as a module from its path (the scripts are not in a
# package, so a plain `import` would not find them).
import importlib.util

# `json` writes the synthetic settings and transcript lines and reads the scripts' JSON outputs.
import json

# `os` builds paths, sets file modes and builds the environment of the child processes.
import os

# `stat` names the file-mode bits; `stat.S_IRWXU` = read, write and execute for the owner.
import stat

# `subprocess.run` runs the scripts as the user would, from the command line.
import subprocess

# `sys.executable` is the path of the Python running the tests; the scripts are run with it.
import sys

# `tempfile.mkdtemp` creates a fresh empty folder for each test; `shutil.rmtree` deletes it.
import shutil
import tempfile

# `unittest` is Python's test framework: classes derived from `unittest.TestCase` hold test
# methods whose names start with "test"; `self.assert...` methods check results.
import unittest

# `ZoneInfo("UTC")` is a time-zone object (Python >= 3.9), used by the snapshot tests.
from zoneinfo import ZoneInfo


# ----------------------------------------------------------------------------------------------
# Paths of the scripts under test
# ----------------------------------------------------------------------------------------------

# This file is <skill>/tests/test_scripts.py, so the skill folder is two levels up.
# `os.path.abspath(__file__)` is this file's absolute path; `dirname` twice removes
# "test_scripts.py" and then "tests".
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_DIGEST = os.path.join(SKILL_DIR, "scripts", "session_digest.py")
CLEANUP_LOG = os.path.join(SKILL_DIR, "scripts", "cleanup_log.py")
LIVE_STATE = os.path.join(SKILL_DIR, "scripts", "live_state.py")

# The settings name an environment variable whose value wins over the host name as this
# machine's name (system_name.override_env). The tests set it, so the machine name is fixed.
SYSTEM_NAME_VARIABLE = "WORKHUB_TEST_SYSTEM_NAME"


# ----------------------------------------------------------------------------------------------
# Helpers shared by the tests
# ----------------------------------------------------------------------------------------------

def synthetic_uuid(digit):
    """Return a clearly synthetic UUID made of one repeated digit, e.g. "1111…-1111-4111-…".

    The ids are built here instead of being written out, so that the kit's leak check (which
    flags every UUID written in a file) only sees placeholders. The "4" and "8" keep the shape
    of a version-4 UUID.

    Parameters:
        digit (str): one character, "1" to "9".

    Returns:
        str: 36 characters, 8-4-4-4-12 groups.
    """
    return "-".join([digit * 8, digit * 4, "4" + digit * 3, "8" + digit * 3, digit * 12])


# Ids of the synthetic conversations.
CLAUDE_SESSION_ID = synthetic_uuid("1")
CODEX_THREAD_ID = synthetic_uuid("2")
CODEX_HELPER_ID = synthetic_uuid("3")
REMOTE_SESSION_ID = synthetic_uuid("5")

# The placeholder ids the kit allows in files: a Notion page id and a data source id.
PLACEHOLDER_PAGE_ID = "01234567-89ab-cdef-0123-456789abcdef"
PLACEHOLDER_DATA_SOURCE_ID = "00000000-0000-0000-0000-000000000000"


def write_jsonl(path, records):
    """Write a list of dictionaries as a JSON Lines file (one JSON object per line).

    Parameters:
        path (str): file to create (its folder is created if needed).
        records (list of dict): the lines.
    """
    # `os.makedirs(..., exist_ok=True)` creates the folder and its parents if missing.
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record) + "\n")


def write_text(path, text, executable=False):
    """Write a text file (its folder is created if needed); optionally make it executable."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as output_file:
        output_file.write(text)
    if executable:
        # `os.chmod(path, mode)` sets the permission bits: rwx for the owner only.
        os.chmod(path, stat.S_IRWXU)


def base_settings(temp_dir):
    """Return a complete synthetic settings dict whose folders all lie inside `temp_dir`.

    Parameters:
        temp_dir (str): the test's temporary folder.

    Returns:
        dict: settings in the format of config/config.example.json.
    """
    return {
        "owner": {"name": "Alex", "time_zone": "UTC"},
        "system_name": {"override_env": SYSTEM_NAME_VARIABLE, "aliases": {}},
        "notion": {
            "hub_page_url": "https://www.notion.so/0123456789abcdef0123456789abcdef",
            "tasks_database_url": "https://www.notion.so/0123456789abcdef0123456789abcdef",
            "tasks_data_source": "collection://" + PLACEHOLDER_DATA_SOURCE_ID,
            "views": {"now": "view://" + PLACEHOLDER_DATA_SOURCE_ID, "focus": ""},
            "task_id_prefix": "TK",
            "projects": ["Project A", "Project B", "Miscellaneous"],
            "research_log": {"enabled": False, "parent_page_url": "", "data_source": ""},
        },
        "workflow": {"orchestrator": {"enabled": False, "folder": os.path.join(temp_dir, "orchestrator")},
                     "now_limits": {"max_rows": 7, "max_in_progress": 3}},
        "machines": {"workstations": [{"name": "ws01", "ssh_alias": "", "shares_home_with_this_machine": True}],
                     "clusters": []},
        "cluster_login": {"enabled": True},
        "conversations": {"claude_projects_dir": os.path.join(temp_dir, "claude", "projects"),
                          "include_codex": True,
                          "codex_sessions_dir": os.path.join(temp_dir, "codex", "sessions")},
        "paths": {"state_dir": os.path.join(temp_dir, "state"), "outputs_dir": os.path.join(temp_dir, "outputs")},
        "skill_settings": {},
    }


def write_settings(temp_dir, settings):
    """Write `settings` as <temp_dir>/config.json and return that path."""
    settings_path = os.path.join(temp_dir, "config.json")
    write_text(settings_path, json.dumps(settings, indent=1))
    return settings_path


def run_script(arguments, settings_path, extra_environment=None, stdin_text=None, cwd=None):
    """Run a script with the current Python and capture its output.

    Parameters:
        arguments (list of str): the script path (or "-") and its arguments.
        settings_path (str or None): the settings file (WORKHUB_CLAUDE_CONFIG); None removes
            the variable (a machine without the kit's reader, as on a remote run).
        extra_environment (dict or None): variables to add to (or override in) the environment.
        stdin_text (str or None): text fed to standard input (for `python3 -`).
        cwd (str or None): folder to run in.

    Returns:
        subprocess.CompletedProcess: with .returncode, .stdout and .stderr (texts).
    """
    # A copy of the current environment, so that changes do not leak into other tests.
    environment = dict(os.environ)
    # The synthetic conversation must not be mistaken for the real one running the tests, and
    # the user's own folders must never be read.
    for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "CC_CLUSTER"):
        environment.pop(name, None)
    environment["WORKHUB_CLAUDE_CONFIG"] = settings_path or ""
    if settings_path is None:
        environment.pop("WORKHUB_CLAUDE_CONFIG")
    environment[SYSTEM_NAME_VARIABLE] = "ws01"
    if extra_environment:
        environment.update(extra_environment)
    return subprocess.run([sys.executable] + arguments, capture_output=True, text=True, env=environment,
                          input=stdin_text, cwd=cwd)


def load_module(name, path):
    """Load a script as a module (its main() is not run) and return it."""
    # The scripts do `import workhub_config`, which Python finds next to them only when the
    # script itself is run (its folder is then searched first). Loaded from a test, that folder
    # must be put on `sys.path` (the list of folders searched on `import`) by hand.
    # DEBUG CHECK: sys.path[0] is <skill>/scripts after the first call.
    scripts_folder = os.path.dirname(os.path.abspath(path))
    if scripts_folder not in sys.path:
        sys.path.insert(0, scripts_folder)
    # `spec_from_file_location` describes how to load the file; `module_from_spec` creates an
    # empty module; `exec_module` runs the file's top level (imports, constants, functions).
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The fake `ssh`. It never connects anywhere:
#   `ssh -O check <alias>` exits 0 only for the aliases listed in $FAKE_SSH_OPEN;
#   `ssh -o X -o Y <host> <command>` drops the -o pairs, refuses the hosts listed in
#   $FAKE_SSH_REFUSE, and otherwise runs <command> with bash in $FAKE_REMOTE_ROOT/<host>
#   (that folder plays the remote machine's home), without the test's settings variable.
FAKE_SSH = r'''#!/bin/bash
if [ "$1" = "-O" ]; then
  for open_alias in $FAKE_SSH_OPEN; do
    if [ "$open_alias" = "$3" ]; then exit 0; fi
  done
  echo "Control socket connect($3): No such file or directory" >&2
  exit 255
fi
while [ "$1" = "-o" ]; do shift 2; done
host="$1"; shift
for refused in $FAKE_SSH_REFUSE; do
  if [ "$refused" = "$host" ]; then echo "ssh: connect to host $host: Connection refused" >&2; exit 255; fi
done
remote_home="$FAKE_REMOTE_ROOT/$host"
mkdir -p "$remote_home"
cd "$remote_home" || exit 255
exec env -u WORKHUB_CLAUDE_CONFIG HOME="$remote_home" bash -c "$*"
'''


def make_fake_bin(temp_dir):
    """Create <temp_dir>/bin with the fake ssh and a `python3` that is the test's Python."""
    bin_dir = os.path.join(temp_dir, "bin")
    write_text(os.path.join(bin_dir, "ssh"), FAKE_SSH, executable=True)
    # The remote command is "python3 - …": a link named python3 to the running Python.
    os.symlink(sys.executable, os.path.join(bin_dir, "python3"))
    return bin_dir


# ----------------------------------------------------------------------------------------------
# session_digest.py
# ----------------------------------------------------------------------------------------------

def claude_records(title="Synthetic survey run"):
    """The synthetic Claude Code transcript: one turn before the period, two inside it.

    The period used by the tests starts at 2026-03-02T13:00:00Z.
    """
    def human(time, text):
        # A request typed by the user (origin.kind = "human").
        return {"type": "user", "timestamp": time, "cwd": "/tmp/synthetic_project",
                "origin": {"kind": "human"}, "message": {"role": "user", "content": text}}

    def assistant_text(time, text):
        # An assistant message with one text block.
        return {"type": "assistant", "timestamp": time,
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    return [
        {"type": "ai-title", "aiTitle": title},
        # Turn 1, before the period: must not be shown.
        human("2026-03-02T12:00:00.000Z", "Old request"),
        assistant_text("2026-03-02T12:01:00.000Z", "Old answer"),
        # Turn 2, in the period: injected context must be removed from the request. The request
        # is long (over 300 characters) so that the default cut and --full can be told apart.
        human("2026-03-02T14:00:00.000Z",
              "Start the synthetic run <system-reminder>hidden context</system-reminder> " + "with care " * 50),
        {"type": "assistant", "timestamp": "2026-03-02T14:01:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_ask1", "name": "AskUserQuestion",
              "input": {"questions": [{"question": "Go?"}]}}]}},
        {"type": "user", "timestamp": "2026-03-02T14:02:00.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_ask1",
              "content": "Your questions have been answered: \"Go?\"=\"Yes\". "
                         "You can now continue with these answers in mind."}]}},
        # A Notion write during the turn: must be listed as "update-page 01234567 (update_properties)".
        {"type": "assistant", "timestamp": "2026-03-02T14:02:30.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_notion1", "name": "mcp__claude_ai_Notion__notion-update-page",
              "input": {"page_id": PLACEHOLDER_PAGE_ID, "command": "update_properties",
                        "properties": {"Status": "Done"}}}]}},
        assistant_text("2026-03-02T14:03:00.000Z", "Started job 123 on mycluster."),
        # A second question that the user closed without answering.
        {"type": "assistant", "timestamp": "2026-03-02T14:03:40.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "toolu_ask2", "name": "AskUserQuestion",
              "input": {"questions": [{"question": "Also run the second wave?"}]}}]}},
        {"type": "user", "timestamp": "2026-03-02T14:03:50.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_ask2",
              "content": "The user doesn't want to proceed with this tool use. The tool use was rejected."}]}},
        # The marker of an interrupted turn: must not start a turn.
        human("2026-03-02T14:03:55.000Z", "[Request interrupted by user]"),
        # A sub-agent record: must be ignored entirely.
        {"type": "user", "timestamp": "2026-03-02T14:04:00.000Z", "isSidechain": True,
         "origin": {"kind": "human"}, "message": {"role": "user", "content": "sub-agent request"}},
        # Turn 3, a background-job notice.
        {"type": "user", "timestamp": "2026-03-02T15:00:00.000Z", "origin": {"kind": "task-notification"},
         "message": {"role": "user",
                     "content": "<task-notification><summary>Job 123 finished</summary></task-notification>"}},
        assistant_text("2026-03-02T15:01:00.000Z", "Job 123 completed."),
        # A housekeeping command: must not start a turn.
        {"type": "user", "timestamp": "2026-03-02T15:02:00.000Z",
         "message": {"role": "user",
                     "content": "<command-name>/compact</command-name> <command-args></command-args>"}},
        # The user's own name for the conversation wins over the automatic title.
        {"type": "custom-title", "customTitle": title + " (renamed)"},
    ]


def codex_records(thread_id):
    """The synthetic Codex rollout: one turn inside the period, with injected context."""
    def user_item(time, text):
        # A Codex "user" message item (real request or injected context).
        return {"timestamp": time, "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

    return [
        {"timestamp": "2026-03-02T13:30:00.000Z", "type": "session_meta",
         "payload": {"id": thread_id, "cwd": "/tmp/codex_project"}},
        {"timestamp": "2026-03-02T13:30:00.100Z", "type": "event_msg", "payload": {"type": "task_started"}},
        user_item("2026-03-02T13:30:00.200Z", "<environment_context>\n<cwd>/tmp</cwd>\n</environment_context>"),
        user_item("2026-03-02T13:30:00.300Z", "# AGENTS.md instructions for /tmp\n\nbe careful"),
        user_item("2026-03-02T13:30:00.400Z", "Please summarise the results"),
        {"timestamp": "2026-03-02T13:31:00.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Working on it"}]}},
        {"timestamp": "2026-03-02T13:35:00.000Z", "type": "event_msg",
         "payload": {"type": "task_complete", "last_agent_message": "Summary written to results.md"}},
    ]


def write_transcripts(claude_projects_dir, codex_sessions_dir):
    """Write the synthetic Claude Code transcript and Codex rollouts into the given folders."""
    write_jsonl(os.path.join(claude_projects_dir, "-tmp-synthetic", CLAUDE_SESSION_ID + ".jsonl"),
                claude_records())
    if codex_sessions_dir is None:
        return
    write_jsonl(os.path.join(codex_sessions_dir, "2026", "03", "02",
                             "rollout-2026-03-02T09-30-00-%s.jsonl" % CODEX_THREAD_ID), codex_records(CODEX_THREAD_ID))
    # The thread names live next to the sessions folder.
    codex_root = os.path.dirname(codex_sessions_dir)
    write_jsonl(os.path.join(codex_root, "session_index.jsonl"),
                [{"id": CODEX_THREAD_ID, "thread_name": "Synthetic Codex thread"}])
    # A Codex helper thread ("Guardian review") in the same period: must be skipped.
    helper_records = codex_records(CODEX_HELPER_ID)
    helper_records[0] = {"timestamp": "2026-03-02T13:40:00.000Z", "type": "session_meta",
                         "payload": {"id": CODEX_HELPER_ID, "session_id": CODEX_THREAD_ID,
                                     "parent_thread_id": CODEX_THREAD_ID,
                                     "source": {"subagent": {"other": "guardian"}},
                                     "cwd": "/tmp/codex_project"}}
    write_jsonl(os.path.join(codex_sessions_dir, "2026", "03", "02",
                             "rollout-2026-03-02T09-40-00-%s.jsonl" % CODEX_HELPER_ID), helper_records)


class SessionDigestTests(unittest.TestCase):
    """session_digest.py on one synthetic Claude Code transcript and one Codex rollout."""

    def setUp(self):
        """Create the synthetic settings, Claude Code and Codex folders in a temporary folder."""
        self.temp_dir = tempfile.mkdtemp(prefix="hub_digest_test_")
        self.settings = base_settings(self.temp_dir)
        self.settings_path = write_settings(self.temp_dir, self.settings)
        write_transcripts(self.settings["conversations"]["claude_projects_dir"],
                          self.settings["conversations"]["codex_sessions_dir"])
        # The naming hook's state folder: <paths.state_dir>/conversation_titles.
        self.title_state_dir = os.path.join(self.temp_dir, "state", "conversation_titles")

    def tearDown(self):
        """Delete the temporary folder and everything in it."""
        shutil.rmtree(self.temp_dir)

    def rewrite_settings(self):
        """Write self.settings again after a test changed it."""
        write_settings(self.temp_dir, self.settings)

    def run_digest(self, since, output_name, extra=None, environment=None, hosts="none"):
        """Run session_digest.py with the synthetic settings; return (process, report or None)."""
        output_dir = os.path.join(self.temp_dir, output_name)
        arguments = [SESSION_DIGEST, "--since", since, "--hosts", hosts, "-o", output_dir] + (extra or [])
        process = run_script(arguments, self.settings_path, environment)
        json_path = os.path.join(output_dir, "sessions.json")
        report = None
        if os.path.exists(json_path):
            with open(json_path, encoding="utf-8") as json_file:
                report = json.load(json_file)
        return process, report

    def read_digest(self, output_name):
        """Return the text of <output_name>/digest.txt."""
        with open(os.path.join(self.temp_dir, output_name, "digest.txt"), encoding="utf-8") as digest_file:
            return digest_file.read()

    def test_turns_in_the_period(self):
        """Both conversations are found, with only the turns of the period and clean texts."""
        process, report = self.run_digest("2026-03-02T13:00:00Z", "out")
        # DEBUG CHECK: on failure, process.stderr holds the script's error message.
        self.assertEqual(process.returncode, 0, process.stderr)

        # The machine name and the time zone come from the settings.
        self.assertEqual(report["system"], "ws01")
        self.assertEqual(report["timezone"], "UTC")
        self.assertEqual(report["task_id_prefix"], "TK")

        # Exactly two conversations: the Codex helper thread ("Guardian review") is skipped.
        self.assertEqual(len(report["sessions"]), 2)

        # One per program. The dictionary maps "claude"/"codex" to its session.
        sessions_by_client = {}
        for session in report["sessions"]:
            sessions_by_client[session["client"]] = session
            self.assertEqual(session["system"], "ws01")
        self.assertEqual(sorted(sessions_by_client), ["claude", "codex"])

        # Claude Code: the renamed title, 2 of 3 turns in the period, injected text removed.
        claude = sessions_by_client["claude"]
        self.assertEqual(claude["title"], "Synthetic survey run (renamed)")
        self.assertEqual(claude["cwd"], "/tmp/synthetic_project")
        self.assertEqual(claude["turns_total"], 3)
        self.assertEqual(claude["turns_in_window"], 2)
        first_turn, second_turn = claude["turns"]
        self.assertEqual(first_turn["kind"], "request")
        self.assertIn("Start the synthetic run", first_turn["prompt"])
        self.assertNotIn("hidden context", first_turn["prompt"])
        # The request is cut to at most the default 300 characters, ending with "…".
        self.assertLessEqual(len(first_turn["prompt"]), 300)
        self.assertGreater(len(first_turn["prompt"]), 290)
        self.assertTrue(first_turn["prompt"].endswith("…"))
        # Two answers: the real one (without the fixed sentences) and the dismissed question.
        self.assertEqual(len(first_turn["answers"]), 2)
        self.assertIn('"Go?"="Yes"', first_turn["answers"][0])
        self.assertNotIn("Your questions have been answered", first_turn["answers"][0])
        self.assertEqual(first_turn["answers"][1], "(the user dismissed the question without answering)")
        self.assertEqual(first_turn["notion_writes"], ["update-page 01234567 (update_properties)"])
        self.assertEqual(first_turn["reply"], "Started job 123 on mycluster.")
        self.assertEqual(second_turn["kind"], "notice")
        self.assertEqual(second_turn["prompt"], "Job 123 finished")
        self.assertEqual(second_turn["reply"], "Job 123 completed.")
        self.assertFalse(claude["this_conversation"])

        # Codex: the indexed name, the real request only, the final answer from task_complete.
        codex = sessions_by_client["codex"]
        self.assertEqual(codex["title"], "Synthetic Codex thread")
        self.assertEqual(codex["cwd"], "/tmp/codex_project")
        self.assertEqual(len(codex["turns"]), 1)
        self.assertEqual(codex["turns"][0]["prompt"], "Please summarise the results")
        self.assertEqual(codex["turns"][0]["reply"], "Summary written to results.md")

        # The digest text names both conversations, with system:/path folders.
        digest_text = self.read_digest("out")
        self.assertIn("Synthetic survey run (renamed)", digest_text)
        self.assertIn("JOB NOTICE: Job 123 finished", digest_text)
        self.assertIn("NOTION WRITES: update-page 01234567 (update_properties)", digest_text)
        self.assertIn("Synthetic Codex thread", digest_text)
        self.assertIn("ws01:/tmp/synthetic_project", digest_text)
        self.assertIn("(UTC)", digest_text)

        # The main digest prints the window end to record in the cleanup log.
        self.assertIn("window end for cleanup_log.py: --window-end", process.stdout)

    def test_time_zone_from_the_settings(self):
        """Printed times follow owner.time_zone (Europe/Paris = UTC+1 in March)."""
        self.settings["owner"]["time_zone"] = "Europe/Paris"
        self.rewrite_settings()
        process, report = self.run_digest("2026-03-02T13:00:00Z", "paris")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["timezone"], "Europe/Paris")
        # The notice at 15:00 UTC is printed at 16:00 Paris time.
        self.assertIn("16:00 JOB NOTICE: Job 123 finished", self.read_digest("paris"))

    def test_work_hub_row_from_the_hook_state(self):
        """A Task ID recorded in the hook's state file is shown per conversation and in the index."""
        # The state file that set_conversation_task.py writes for a conversation of row TK-42.
        os.makedirs(self.title_state_dir)
        state_path = os.path.join(self.title_state_dir, CLAUDE_SESSION_ID + ".json")
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump({"title": "Synthetic survey run (renamed)", "final": True, "task": "TK-42",
                       "task_topic": "Synthetic run"}, state_file)
        process, report = self.run_digest("2026-03-02T13:00:00Z", "rows")
        self.assertEqual(process.returncode, 0, process.stderr)

        # The Claude Code conversation has the recorded row; the Codex thread (no Task ID in its
        # name, no state file) has none.
        tasks_by_client = {}
        for session in report["sessions"]:
            tasks_by_client[session["client"]] = (session["task"], session["task_source"])
        self.assertEqual(tasks_by_client["claude"], ("TK-42", "recorded"))
        self.assertEqual(tasks_by_client["codex"], ("", ""))

        # The digest's index lists the row and the conversation without a row.
        digest_text = self.read_digest("rows")
        self.assertIn("Work Hub rows of these conversations (1 row(s)):", digest_text)
        self.assertIn("  TK-42: [claude 11111111]", digest_text)
        self.assertIn("No Work Hub row recorded (1 conversation(s)):", digest_text)
        self.assertIn("Work Hub row: TK-42 (recorded)", digest_text)
        self.assertIn("Work Hub row: none recorded", digest_text)

    def test_task_id_prefix_from_the_settings(self):
        """With prefix PRJ, "PRJ-7" is a Task ID and "TK-42" is not."""
        self.settings["notion"]["task_id_prefix"] = "PRJ"
        self.rewrite_settings()
        os.makedirs(self.title_state_dir)
        with open(os.path.join(self.title_state_dir, CLAUDE_SESSION_ID + ".json"), "w", encoding="utf-8") as state_file:
            json.dump({"task": "PRJ-7"}, state_file)
        # A Codex thread named with the old prefix: not a row with prefix PRJ.
        codex_root = os.path.dirname(self.settings["conversations"]["codex_sessions_dir"])
        write_jsonl(os.path.join(codex_root, "session_index.jsonl"),
                    [{"id": CODEX_THREAD_ID, "thread_name": "TK-42 Synthetic Codex thread"}])
        process, report = self.run_digest("2026-03-02T13:00:00Z", "prefix")
        self.assertEqual(process.returncode, 0, process.stderr)
        tasks_by_client = {}
        for session in report["sessions"]:
            tasks_by_client[session["client"]] = session["task"]
        self.assertEqual(tasks_by_client, {"claude": "PRJ-7", "codex": ""})

    def test_codex_is_skipped_when_the_settings_say_so(self):
        """conversations.include_codex false: only the Claude Code conversation is read."""
        self.settings["conversations"]["include_codex"] = False
        self.rewrite_settings()
        process, report = self.run_digest("2026-03-02T13:00:00Z", "nocodex")
        self.assertEqual(process.returncode, 0, process.stderr)
        clients = []
        for session in report["sessions"]:
            clients.append(session["client"])
        self.assertEqual(clients, ["claude"])

    def test_missing_settings_stop_with_status_2(self):
        """Without a settings file the script stops with status 2 and says how to set it up."""
        output_dir = os.path.join(self.temp_dir, "none")
        process = run_script([SESSION_DIGEST, "--since", "36h", "--hosts", "none", "-o", output_dir],
                             os.path.join(self.temp_dir, "missing.json"))
        self.assertEqual(process.returncode, 2)
        self.assertIn("set up my work hub", process.stderr)
        self.assertFalse(os.path.exists(output_dir))

    def test_nothing_after_the_last_activity(self):
        """A period starting after every message finds no conversation."""
        process, report = self.run_digest("2026-03-02T16:00:00Z", "late")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["sessions"], [])

    def test_this_conversation_is_marked(self):
        """The conversation whose id is in CLAUDE_CODE_SESSION_ID is marked as the current one."""
        process, report = self.run_digest("2026-03-02T13:00:00Z", "marked",
                                          environment={"CLAUDE_CODE_SESSION_ID": CLAUDE_SESSION_ID})
        self.assertEqual(process.returncode, 0, process.stderr)
        claude = [session for session in report["sessions"] if session["client"] == "claude"][0]
        self.assertTrue(claude["this_conversation"])

    def test_session_filter_and_max_turns(self):
        """--session keeps one conversation; --max-turns 1 keeps its newest turn only."""
        process, report = self.run_digest("2026-03-02T13:00:00Z", "one",
                                          extra=["--session", CLAUDE_SESSION_ID[:8], "--max-turns", "1"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(report["sessions"]), 1)
        self.assertEqual(report["sessions"][0]["turns_in_window"], 2)
        self.assertEqual([turn["kind"] for turn in report["sessions"][0]["turns"]], ["notice"])
        # A detail run must not print a window end (only the main digest's one counts).
        self.assertIn("detail run", process.stdout)
        self.assertNotIn("--window-end", process.stdout)

    def test_several_sessions_and_full_text(self):
        """--session takes several prefixes; --full keeps every text whole."""
        process, report = self.run_digest(
            "2026-03-02T13:00:00Z", "full",
            extra=["--session", CLAUDE_SESSION_ID[:8] + "," + CODEX_THREAD_ID[:8], "--full"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(len(report["sessions"]), 2)
        claude = [session for session in report["sessions"] if session["client"] == "claude"][0]
        # The long request is kept whole: over 300 characters, without the "…" of a cut.
        self.assertGreater(len(claude["turns"][0]["prompt"]), 300)
        self.assertFalse(claude["turns"][0]["prompt"].endswith("…"))

    def test_invalid_session_prefix_is_refused(self):
        """A --session value that is not an id prefix is refused by argparse (status 2)."""
        process, report = self.run_digest("2026-03-02T13:00:00Z", "bad", extra=["--session", "abc;rm"])
        self.assertEqual(process.returncode, 2)
        self.assertIsNone(report)

    def test_outputs_are_not_overwritten(self):
        """A second run into the same folder fails without --overwrite and works with it."""
        first, _ = self.run_digest("2026-03-02T13:00:00Z", "same")
        self.assertEqual(first.returncode, 0, first.stderr)
        second, _ = self.run_digest("2026-03-02T13:00:00Z", "same")
        self.assertEqual(second.returncode, 1)
        self.assertIn("--overwrite", second.stderr)
        third, _ = self.run_digest("2026-03-02T13:00:00Z", "same", extra=["--overwrite"])
        self.assertEqual(third.returncode, 0, third.stderr)

    def test_remote_mode_through_standard_input(self):
        """`python3 -` on a machine without the kit: default folders, the passed name and prefix."""
        # The "remote" home: Claude Code's default folder and the kit's default state folder.
        remote_home = os.path.join(self.temp_dir, "remote_home")
        write_jsonl(os.path.join(remote_home, ".claude", "projects", "-tmp-remote", REMOTE_SESSION_ID + ".jsonl"),
                    claude_records("Remote cluster run"))
        remote_state = os.path.join(remote_home, ".local", "state", "workhub-claude", "conversation_titles")
        write_text(os.path.join(remote_state, REMOTE_SESSION_ID + ".json"), json.dumps({"task": "TK-9"}))
        with open(SESSION_DIGEST, encoding="utf-8") as source_file:
            source_code = source_file.read()

        # Run from an empty folder, with HOME = the remote home and no settings variable.
        empty_cwd = os.path.join(self.temp_dir, "empty_cwd")
        os.makedirs(empty_cwd)
        process = run_script(["-", "--json-only", "--hosts", "none", "--since", "2026-03-02T13:00:00Z",
                              "--system-name", "mycluster", "--task-id-prefix", "TK", "--timezone", "UTC"],
                             None, {"HOME": remote_home}, stdin_text=source_code, cwd=empty_cwd)
        self.assertEqual(process.returncode, 0, process.stderr)

        # One JSON line: the remote session, labelled with the passed name, with its row.
        report = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(report["system"], "mycluster")
        self.assertEqual(report["counts"], {"claude": 1, "codex": 0})
        self.assertEqual(report["sessions"][0]["title"], "Remote cluster run (renamed)")
        self.assertEqual(report["sessions"][0]["task"], "TK-9")
        # Nothing was written in the folder it ran from.
        self.assertEqual(os.listdir(empty_cwd), [])

    def test_remote_machine_uses_its_own_settings(self):
        """On a remote machine with its own settings file, its folders are used (no reader needed)."""
        remote_home = os.path.join(self.temp_dir, "remote_home")
        remote_projects = os.path.join(remote_home, "elsewhere", "projects")
        write_jsonl(os.path.join(remote_projects, "-tmp-remote", REMOTE_SESSION_ID + ".jsonl"),
                    claude_records("Remote run with settings"))
        write_text(os.path.join(remote_home, ".config", "workhub-claude", "config.json"),
                   json.dumps({"conversations": {"claude_projects_dir": remote_projects, "include_codex": False}}))
        with open(SESSION_DIGEST, encoding="utf-8") as source_file:
            source_code = source_file.read()
        process = run_script(["-", "--json-only", "--hosts", "none", "--since", "2026-03-02T13:00:00Z",
                              "--system-name", "mycluster", "--timezone", "UTC"],
                             None, {"HOME": remote_home}, stdin_text=source_code, cwd=remote_home)
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(report["counts"], {"claude": 1, "codex": 0})
        self.assertEqual(report["sessions"][0]["title"], "Remote run with settings (renamed)")

    def test_other_machines_through_ssh_without_prompts(self):
        """--hosts auto: open cluster read, closed cluster NOT REACHED with the fix, workstation by keys."""
        # The machines: two clusters (one with an open shared connection), one workstation with
        # its own home folder, one that shares this home folder (never contacted).
        self.settings["machines"]["clusters"] = [
            {"name": "mycluster", "ssh_alias": "mycluster", "scheduler": "slurm", "shares_home_with_this_machine": False},
            {"name": "othercluster", "ssh_alias": "othercluster", "scheduler": "slurm",
             "shares_home_with_this_machine": False}]
        self.settings["machines"]["workstations"].append(
            {"name": "ws02", "ssh_alias": "ws02", "shares_home_with_this_machine": False})
        self.settings["machines"]["workstations"].append(
            {"name": "ws03", "ssh_alias": "ws03", "shares_home_with_this_machine": True})
        self.settings["conversations"]["include_codex"] = False
        self.rewrite_settings()

        # The remote homes of the fake ssh: mycluster has one conversation, ws02 none.
        remote_root = os.path.join(self.temp_dir, "remote")
        write_jsonl(os.path.join(remote_root, "mycluster", ".claude", "projects", "-tmp-remote",
                                 REMOTE_SESSION_ID + ".jsonl"), claude_records("Remote cluster run"))
        bin_dir = make_fake_bin(self.temp_dir)
        environment = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""), "FAKE_SSH_OPEN": "mycluster",
                       "FAKE_SSH_REFUSE": "", "FAKE_REMOTE_ROOT": remote_root}
        process, report = self.run_digest("2026-03-02T13:00:00Z", "remote", environment=environment, hosts="auto")
        self.assertEqual(process.returncode, 0, process.stderr)

        # Sources: this machine, then the clusters, then ws02; ws03 shares the home folder.
        sources = {}
        for source in report["sources"]:
            sources[source["system"]] = source
        self.assertEqual(sorted(sources), ["mycluster", "othercluster", "ws01", "ws02"])
        self.assertTrue(sources["mycluster"]["reached"])
        self.assertEqual(sources["mycluster"]["claude"], 1)
        self.assertFalse(sources["othercluster"]["reached"])
        self.assertIn("cluster-login othercluster", sources["othercluster"]["note"])
        self.assertTrue(sources["ws02"]["reached"])
        self.assertIn("SSH keys", sources["ws02"]["note"])

        # The remote conversation is labelled with the cluster's name.
        remote_sessions = [session for session in report["sessions"] if session["system"] == "mycluster"]
        self.assertEqual(len(remote_sessions), 1)
        self.assertEqual(remote_sessions[0]["title"], "Remote cluster run (renamed)")
        self.assertIn("othercluster: NOT REACHED", self.read_digest("remote"))


class SessionDigestHostTests(unittest.TestCase):
    """resolve_hosts() of session_digest.py, imported directly."""

    @classmethod
    def setUpClass(cls):
        """Load session_digest.py and its settings reader once."""
        cls.digest = load_module("session_digest", SESSION_DIGEST)
        cls.reader = cls.digest.load_settings_reader()

    def setUp(self):
        """Settings with two clusters and two workstations."""
        self.temp_dir = tempfile.mkdtemp(prefix="hub_hosts_test_")
        self.config = base_settings(self.temp_dir)
        self.config["machines"]["clusters"] = [
            {"name": "mycluster", "ssh_alias": "mc", "shares_home_with_this_machine": False}]
        self.config["machines"]["workstations"].append(
            {"name": "ws02", "ssh_alias": "ws02", "shares_home_with_this_machine": False})

    def tearDown(self):
        """Delete the temporary folder."""
        shutil.rmtree(self.temp_dir)

    def names(self, hosts):
        """The names of a host list, for short assertions."""
        return [host["name"] for host in hosts]

    def test_reader_is_found_next_to_the_script(self):
        """Loaded from its file, the script finds workhub_config.py in its folder."""
        self.assertIsNotNone(self.reader)

    def test_auto_on_a_workstation(self):
        """From ws01: every machine that does not share its home folder, with its SSH alias."""
        hosts = self.digest.resolve_hosts("auto", self.reader, self.config, "ws01")
        self.assertEqual(self.names(hosts), ["mycluster", "ws02"])
        self.assertEqual(hosts[0], {"name": "mycluster", "ssh_alias": "mc", "kind": "cluster"})

    def test_auto_on_a_cluster(self):
        """On a cluster of the settings: this machine only."""
        self.assertEqual(self.digest.resolve_hosts("auto", self.reader, self.config, "mycluster"), [])

    def test_explicit_names_and_aliases(self):
        """Names or aliases pick their entries; an unknown word is a cluster alias; this machine is dropped."""
        hosts = self.digest.resolve_hosts("mc, ws02, ws01, newcluster", self.reader, self.config, "ws01")
        self.assertEqual(self.names(hosts), ["mycluster", "ws02", "newcluster"])
        self.assertEqual(hosts[2]["kind"], "cluster")
        self.assertEqual(self.digest.resolve_hosts("none", self.reader, self.config, "ws01"), [])


# ----------------------------------------------------------------------------------------------
# cleanup_log.py
# ----------------------------------------------------------------------------------------------

class CleanupLogTests(unittest.TestCase):
    """cleanup_log.py with a temporary settings file and its state folder."""

    def setUp(self):
        """A temporary folder and settings; the log inside the state folder does not exist yet."""
        self.temp_dir = tempfile.mkdtemp(prefix="hub_log_test_")
        self.settings = base_settings(self.temp_dir)
        self.settings_path = write_settings(self.temp_dir, self.settings)
        self.log_path = os.path.join(self.temp_dir, "state", "work-hub-cleanup", "cleanup_log.jsonl")

    def tearDown(self):
        """Delete the temporary folder."""
        shutil.rmtree(self.temp_dir)

    def rewrite_settings(self):
        """Write self.settings again after a test changed it."""
        write_settings(self.temp_dir, self.settings)

    def log(self, *arguments):
        """Run cleanup_log.py with the temporary settings; return the finished process."""
        return run_script([CLEANUP_LOG] + list(arguments), self.settings_path)

    def add_cleanup(self, summary="test cleanup"):
        """Record one synthetic cleanup; return the finished process."""
        return self.log("add", "--window-start", "2026-03-02T14:41+00:00", "--window-end",
                        "2026-03-03T11:58+00:00", "--finished", "2026-03-03T12:20+00:00",
                        "--client", "claude-code", "--summary", summary)

    def test_empty_log_proposes_a_default_period(self):
        """Without any cleanup, `last` proposes the last 72 hours and writes nothing."""
        process = self.log("last")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("no cleanup recorded yet", process.stdout)
        self.assertIn("72 hours", process.stdout)
        self.assertFalse(os.path.exists(self.log_path))

    def test_add_then_last(self):
        """After `add`, the log is in the state folder and `last` proposes the next --since."""
        added = self.add_cleanup()
        self.assertEqual(added.returncode, 0, added.stderr)
        # The default log path: <paths.state_dir>/work-hub-cleanup/cleanup_log.jsonl.
        self.assertTrue(os.path.isfile(self.log_path))
        with open(self.log_path, encoding="utf-8") as log_file:
            entry = json.loads(log_file.readline())
        self.assertEqual(entry["system"], "ws01")
        latest = self.log("last")
        self.assertEqual(latest.returncode, 0, latest.stderr)
        self.assertIn("Next cleanup: --since 2026-03-03T11:58+00:00", latest.stdout)
        self.assertIn("Hub edits by others since: 2026-03-03T12:20+00:00", latest.stdout)
        self.assertIn("filter on the plain date 2026-03-02, then keep rows edited at or after "
                      "2026-03-03T12:20Z (UTC)", latest.stdout)

    def test_time_zone_from_the_settings(self):
        """Times typed without an offset are read in owner.time_zone (Europe/Paris = +01:00 in March)."""
        self.settings["owner"]["time_zone"] = "Europe/Paris"
        self.rewrite_settings()
        added = self.log("add", "--window-start", "2026-03-02 14:41", "--window-end", "2026-03-03 11:58",
                         "--client", "codex", "--summary", "paris")
        self.assertEqual(added.returncode, 0, added.stderr)
        latest = self.log("last")
        self.assertIn("--since 2026-03-03T11:58+01:00", latest.stdout)

    def test_dry_run_writes_nothing(self):
        """`add --dry-run` shows the line and leaves the log absent."""
        process = self.log("add", "--window-start", "2026-03-02", "--window-end", "2026-03-03",
                           "--client", "codex", "--summary", "dry", "--dry-run")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("dry run", process.stdout)
        self.assertFalse(os.path.exists(self.log_path))

    def test_inconsistent_window_is_refused(self):
        """A window that ends before it starts is refused with status 1."""
        process = self.log("add", "--window-start", "2026-03-03", "--window-end", "2026-03-02",
                           "--client", "other", "--summary", "bad")
        self.assertEqual(process.returncode, 1)
        self.assertFalse(os.path.exists(self.log_path))

    def test_next_code_reads_the_log_and_the_orchestrator_files(self):
        """next-code: one more than the highest CL<n>; the orchestrator files count only when it is on."""
        self.assertEqual(self.add_cleanup("questions CL3-CL4").returncode, 0)
        orchestrator_folder = self.settings["workflow"]["orchestrator"]["folder"]
        write_text(os.path.join(orchestrator_folder, "DECISIONS.md"), "CL9 answered: A\n")
        write_text(os.path.join(orchestrator_folder, "QUESTIONS.md"), "CL7 open\n")

        # Orchestrator off (the default here): only the log counts.
        process = self.log("next-code")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("Next free question code: CL5", process.stdout)

        # Orchestrator on: DECISIONS.md holds the highest code.
        self.settings["workflow"]["orchestrator"]["enabled"] = True
        self.rewrite_settings()
        process = self.log("next-code")
        self.assertIn("Next free question code: CL10", process.stdout)

    def test_settings_report(self):
        """`settings` prints the resolved addresses, marks empty ones and lists the project rules."""
        self.settings["skill_settings"] = {"work-hub-cleanup": {"project_rules": ["Survey work goes to Project A."]}}
        self.rewrite_settings()
        process = self.log("settings")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("collection://" + PLACEHOLDER_DATA_SOURCE_ID, process.stdout)
        self.assertIn("(empty: Focus rows come from a rows query)", process.stdout)
        self.assertIn("Project A; Project B; Miscellaneous", process.stdout)
        self.assertIn("1. Survey work goes to Project A.", process.stdout)
        self.assertIn("(off: notion.research_log.enabled is false", process.stdout)
        self.assertIn("(off: questions and answers stay in this chat", process.stdout)
        self.assertIn(self.log_path, process.stdout)

        # Without the Tasks data source nothing can be cleaned: status 2.
        self.settings["notion"]["tasks_data_source"] = ""
        self.rewrite_settings()
        self.assertEqual(self.log("settings").returncode, 2)

    def test_missing_settings_stop_with_status_2(self):
        """Without a settings file the script stops with status 2."""
        process = run_script([CLEANUP_LOG, "last"], os.path.join(self.temp_dir, "missing.json"))
        self.assertEqual(process.returncode, 2)
        self.assertIn("set up my work hub", process.stderr)


# ----------------------------------------------------------------------------------------------
# live_state.py
# ----------------------------------------------------------------------------------------------

class LiveStateParsingTests(unittest.TestCase):
    """The parsers and helpers of live_state.py on typical command outputs (no programs run)."""

    @classmethod
    def setUpClass(cls):
        """Load the module once for all tests of this class."""
        cls.live_state = load_module("live_state", LIVE_STATE)

    def test_slurm_parsing(self):
        """squeue and sacct lines become jobs; error lines are kept as problems."""
        output = ("login banner\n@@SQUEUE@@\n"
                  "100001|demo_a|PENDING|0:00|23:50:00|2026-03-03T22:38:59|(Priority)\n"
                  "100002|demo_b|RUNNING|1:02:03|23:50:00|2026-03-02T10:00:00|node12\n"
                  "slurm_load_jobs error: timeout\n"
                  "@@SACCT@@\n"
                  "100003|demo_c|COMPLETED|00:04:20|2026-03-02T12:50:02|0:0\n"
                  "100004|still_running|RUNNING|00:10:00|Unknown|0:0\n")
        sections = self.live_state.split_sections(output, ["@@SQUEUE@@", "@@SACCT@@"])
        queued, squeue_problems = self.live_state.parse_squeue_lines(sections["@@SQUEUE@@"])
        ended, sacct_problems = self.live_state.parse_sacct_lines(sections["@@SACCT@@"])
        self.assertEqual([job["job_id"] for job in queued], ["100001", "100002"])
        self.assertEqual(squeue_problems, ["slurm_load_jobs error: timeout"])
        self.assertEqual([job["state"] for job in ended], ["COMPLETED"])
        self.assertEqual(sacct_problems, [])

    def test_slurm_command_runs_in_utc(self):
        """Both Slurm commands run with TZ=UTC and sacct starts at the given UTC moment."""
        command = self.live_state.slurm_shell_command("2026-03-02T11:41")
        self.assertIn("TZ=UTC squeue", command)
        self.assertIn("TZ=UTC sacct", command)
        self.assertIn("-S 2026-03-02T11:41", command)

    def test_utc_times_shown_in_the_user_zone(self):
        """A UTC Slurm time is shown in the user's zone; Unknown and unreadable texts stay as they are."""
        paris = ZoneInfo("Europe/Paris")
        self.assertEqual(self.live_state.utc_slurm_time_to_local("2026-03-02T13:00:00", paris), "2026-03-02 14:00")
        self.assertEqual(self.live_state.utc_slurm_time_to_local("Unknown", paris), "Unknown")
        self.assertEqual(self.live_state.utc_slurm_time_to_local("soon", paris), "soon")

    def test_workstation_parsing(self):
        """tmux, nvidia-smi and ps lines; only long, busy, non-desktop processes are kept."""
        tmux = self.live_state.parse_tmux_lines(["survey_clean|1|1772000000|1", "bad line"])
        self.assertEqual(tmux, [{"name": "survey_clean", "windows": 1, "created": 1772000000, "attached": True}])
        gpu = self.live_state.parse_gpu_lines(["4242, 2450", "5555, 100"])
        self.assertEqual(gpu, {4242: 2450, 5555: 100})
        processes = self.live_state.parse_ps_lines([
            "4242 7200 95.0 python3 train.py --fold 2",          # long, busy, on the GPU: kept
            "4300 7200 0.5 python3 idle_helper.py",              # long but idle, not on GPU: dropped
            "4400 60 99.0 python3 short_job.py",                 # busy but only 1 minute: dropped
            "4500 90000 65.0 /usr/share/code/code --type=gpu-process",  # desktop VS Code: dropped
        ])
        busy = self.live_state.busy_processes(processes, gpu)
        self.assertEqual([process["pid"] for process in busy], [4242])
        self.assertEqual(busy[0]["gpu_mib"], 2450)

    def test_targets_from_the_settings(self):
        """Clusters, hops (machines.cluster_hops) and workstations come from the settings."""
        config = {"machines": {
            "clusters": [{"name": "mycluster", "ssh_alias": "mc"}, {"name": "pbscluster", "scheduler": "pbs"}],
            "workstations": [{"name": "ws01"}, {"name": "ws02", "ssh_alias": "ws02"}],
            "cluster_hops": [
                {"name": "gpucluster", "through": "mycluster", "login_node": "gpu-login1"},
                {"name": "incomplete", "through": "mycluster"}]}}
        clusters = self.live_state.slurm_targets(config)
        self.assertEqual([target["name"] for target in clusters], ["mycluster", "pbscluster", "gpucluster"])
        self.assertEqual(clusters[1]["ssh_alias"], "pbscluster")
        self.assertEqual(clusters[1]["scheduler"], "pbs")
        self.assertEqual(clusters[2]["ssh_alias"], "mc")
        self.assertEqual(clusters[2]["login_node"], "gpu-login1")
        workstations = self.live_state.workstation_targets(config)
        self.assertEqual(workstations, [{"name": "ws01", "ssh_alias": ""}, {"name": "ws02", "ssh_alias": "ws02"}])
        # Explicit selection: by name or alias; an unknown word is a Slurm cluster alias.
        selected = self.live_state.select_targets("mc,newcluster", clusters, [], "cluster")
        self.assertEqual([target["name"] for target in selected], ["mycluster", "newcluster"])
        self.assertEqual(self.live_state.select_targets("none", clusters, ["mycluster"], "cluster"), [])

    def test_snapshot_text(self):
        """format_snapshot() lists reached and unreached machines, and says "none" explicitly."""
        report = {"collected": 1772460000, "system": "ws01", "sacct_start_utc": "2026-03-02T11:41",
                  "clusters": [{"system": "mycluster", "reached": True, "note": "", "running": [], "pending": [],
                                "ended": [], "problems": []},
                               {"system": "othercluster", "reached": False, "note": "no open shared SSH connection",
                                "running": [], "pending": [], "ended": [], "problems": []}],
                  "workstations": [{"system": "ws02", "reached": True, "note": "", "tmux": [],
                                    "gpu_mine_count": 0, "gpu_mine_mib": 0, "gpu_others_count": 0, "busy": []}]}
        text = self.live_state.format_snapshot(report, ZoneInfo("UTC"))
        self.assertIn("mycluster: no jobs", text)
        self.assertIn("othercluster: NOT REACHED", text)
        self.assertIn("ws02: tmux: none", text)
        self.assertIn("busy processes: none", text)


# The fake Slurm and workstation programs. `squeue` prints one running job (start time in UTC);
# `sacct` prints one finished job whose name carries the TZ variable it received, so the test
# can check that the command ran with TZ=UTC.
FAKE_SQUEUE = "#!/bin/bash\necho '100002|demo_running|RUNNING|1:02:03|23:50:00|2026-03-02T10:00:00|node12'\n"
FAKE_SACCT = "#!/bin/bash\necho \"100003|ran_in_${TZ}|COMPLETED|00:04:20|2026-03-02T12:50:02|0:0\"\n"
FAKE_TMUX = "#!/bin/bash\necho 'survey_clean|1|1772000000|0'\n"
FAKE_NVIDIA_SMI = "#!/bin/bash\necho '4242, 2450'\n"
FAKE_PS = "#!/bin/bash\necho '4242 7200 95.0 python3 train.py --fold 2'\n"


class LiveStateEndToEndTests(unittest.TestCase):
    """live_state.py run as a program, with fake ssh, Slurm, tmux, nvidia-smi and ps."""

    def setUp(self):
        """Settings with three clusters, a hop and three workstations; the fake programs."""
        self.temp_dir = tempfile.mkdtemp(prefix="hub_live_test_")
        self.settings = base_settings(self.temp_dir)
        self.settings["owner"]["time_zone"] = "Europe/Paris"
        self.settings["machines"]["clusters"] = [
            {"name": "mycluster", "ssh_alias": "mycluster", "scheduler": "slurm"},
            {"name": "othercluster", "ssh_alias": "othercluster", "scheduler": "slurm"},
            {"name": "pbscluster", "ssh_alias": "pbscluster", "scheduler": "pbs"}]
        self.settings["machines"]["workstations"] = [
            {"name": "ws01", "ssh_alias": ""}, {"name": "ws02", "ssh_alias": "ws02"}, {"name": "ws03", "ssh_alias": ""}]
        self.settings["machines"]["cluster_hops"] = [
            {"name": "gpucluster", "through": "mycluster", "login_node": "gpu-login1"}]
        self.settings_path = write_settings(self.temp_dir, self.settings)

        # The fake programs, first on PATH.
        bin_dir = make_fake_bin(self.temp_dir)
        fake_programs = {"squeue": FAKE_SQUEUE, "sacct": FAKE_SACCT, "tmux": FAKE_TMUX,
                         "nvidia-smi": FAKE_NVIDIA_SMI, "ps": FAKE_PS}
        for program_name, program_text in fake_programs.items():
            write_text(os.path.join(bin_dir, program_name), program_text, executable=True)
        self.environment = {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", ""),
                            "FAKE_SSH_OPEN": "mycluster", "FAKE_SSH_REFUSE": "",
                            "FAKE_REMOTE_ROOT": os.path.join(self.temp_dir, "remote")}

    def tearDown(self):
        """Delete the temporary folder."""
        shutil.rmtree(self.temp_dir)

    def run_live_state(self, arguments, system_name="ws01"):
        """Run live_state.py with the settings and the fake programs; return the finished process."""
        environment = dict(self.environment)
        environment[SYSTEM_NAME_VARIABLE] = system_name
        return run_script([LIVE_STATE] + arguments, self.settings_path, environment)

    def read_report(self, output_dir):
        """Return live_state.json of `output_dir`, with its machines by name."""
        with open(os.path.join(output_dir, "live_state.json"), encoding="utf-8") as json_file:
            report = json.load(json_file)
        machines = {}
        for item in report["clusters"] + report["workstations"]:
            machines[item["system"]] = item
        return report, machines

    def test_snapshot_from_a_workstation(self):
        """From ws01: every cluster and workstation of the settings, with the right notes."""
        output_dir = os.path.join(self.temp_dir, "live")
        process = self.run_live_state(["--since", "2026-03-02T14:41+00:00", "-o", output_dir])
        self.assertEqual(process.returncode, 0, process.stderr)
        report, machines = self.read_report(output_dir)

        # sacct starts SACCT_MARGIN_HOURS (3) before the period, in UTC.
        self.assertEqual(report["sacct_start_utc"], "2026-03-02T11:41")

        # The open cluster: one running job, one ended job that ran with TZ=UTC; its UTC end
        # time 12:50 is shown as 13:50 in Paris.
        self.assertTrue(machines["mycluster"]["reached"])
        self.assertEqual(machines["mycluster"]["running"][0]["job_id"], "100002")
        self.assertEqual(machines["mycluster"]["ended"][0]["name"], "ran_in_UTC")
        self.assertEqual(machines["mycluster"]["ended"][0]["end"], "2026-03-02 13:50")

        # The hop through the open cluster is reached too.
        self.assertTrue(machines["gpucluster"]["reached"])

        # The closed cluster and the other scheduler are not read, with a note saying why.
        self.assertFalse(machines["othercluster"]["reached"])
        self.assertIn("cluster-login othercluster", machines["othercluster"]["note"])
        self.assertFalse(machines["pbscluster"]["reached"])
        self.assertIn("only Slurm", machines["pbscluster"]["note"])

        # Workstations: this machine directly, ws02 through ssh, ws03 has no alias.
        self.assertTrue(machines["ws01"]["reached"])
        self.assertEqual(machines["ws01"]["tmux"][0]["name"], "survey_clean")
        self.assertTrue(machines["ws02"]["reached"])
        self.assertFalse(machines["ws03"]["reached"])
        self.assertIn("no ssh_alias", machines["ws03"]["note"])

        # Existing outputs are not replaced without --overwrite.
        again = self.run_live_state(["--since", "2026-03-02T14:41+00:00", "-o", output_dir])
        self.assertEqual(again.returncode, 1)
        self.assertIn("--overwrite", again.stderr)

    def test_snapshot_on_a_cluster(self):
        """On mycluster: only that cluster, read directly; no workstation."""
        output_dir = os.path.join(self.temp_dir, "live_cluster")
        process = self.run_live_state(["--since", "2026-03-02T14:41+00:00", "-o", output_dir],
                                      system_name="mycluster")
        self.assertEqual(process.returncode, 0, process.stderr)
        report, machines = self.read_report(output_dir)
        self.assertEqual(sorted(machines), ["mycluster"])
        self.assertTrue(machines["mycluster"]["reached"])

    def test_connections_only(self):
        """--connections-only: one line per cluster, with the fix for the closed ones."""
        process = self.run_live_state(["--connections-only"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("mycluster: connected", process.stdout)
        self.assertIn("othercluster: NOT CONNECTED - run `cluster-login othercluster` on this machine",
                      process.stdout)
        self.assertIn("gpucluster: through mycluster (login node gpu-login1)", process.stdout)
        self.assertIn("2 cluster(s) not connected", process.stdout)

    def test_missing_settings_stop_with_status_2(self):
        """Without a settings file the script stops with status 2."""
        process = run_script([LIVE_STATE, "--connections-only"], os.path.join(self.temp_dir, "missing.json"),
                             self.environment)
        self.assertEqual(process.returncode, 2)


# ----------------------------------------------------------------------------------------------
# session_digest.py helpers (imported directly; main() is not run)
# ----------------------------------------------------------------------------------------------

class SessionDigestHelperTests(unittest.TestCase):
    """The text, Notion and Task ID helpers of session_digest.py."""

    @classmethod
    def setUpClass(cls):
        """Load session_digest.py as a module once."""
        cls.digest = load_module("session_digest", SESSION_DIGEST)

    def setUp(self):
        """An empty state folder for each test."""
        self.state_dir = tempfile.mkdtemp(prefix="hub_task_test_")
        self.pattern = self.digest.task_id_pattern("TK")

    def tearDown(self):
        """Delete the state folder."""
        shutil.rmtree(self.state_dir)

    def test_shorten_middle_keeps_both_ends(self):
        """The middle is cut; the beginning and the end stay; the limit is respected."""
        self.assertEqual(self.digest.shorten_middle("abcdefghijklmnopqrst", 10), "abcd … rst")
        self.assertEqual(self.digest.shorten_middle("short text", 50), "short text")
        self.assertEqual(self.digest.shorten_middle("a  b\n c", None), "a b c")

    def test_describe_notion_write(self):
        """Writes are described in a few words; reads give None."""
        created = self.digest.describe_notion_write(
            "mcp__claude_ai_Notion__notion-create-pages",
            {"parent": {"data_source_id": PLACEHOLDER_DATA_SOURCE_ID}, "pages": [{}, {}]})
        self.assertEqual(created, "create-pages ×2 in 00000000")
        self.assertIsNone(self.digest.describe_notion_write("mcp__claude_ai_Notion__notion-fetch", {"id": "x"}))
        self.assertIsNone(self.digest.describe_notion_write("Bash", {"command": "ls"}))

    def test_task_id_pattern_follows_the_prefix(self):
        """The pattern is built from the prefix, which is matched literally (re.escape)."""
        self.assertTrue(self.pattern.match("TK-42"))
        self.assertFalse(self.pattern.match("TK-42b"))
        self.assertFalse(self.pattern.match("xTK-42"))
        dotted = self.digest.task_id_pattern("A.B")
        self.assertTrue(dotted.match("A.B-3"))
        # Without re.escape, "." would match any character.
        self.assertFalse(dotted.match("AxB-3"))

    def test_task_in_the_name(self):
        """Without a state file, a Task ID word of the name is used; look-alikes are not."""
        found = self.digest.find_work_hub_task("abc", "[Mar-02 09:10][ws01][UC] TK-42: Survey cleaning",
                                               self.state_dir, self.pattern)
        self.assertEqual(found, ("TK-42", "name"))
        self.assertEqual(self.digest.find_work_hub_task("abc", "TK-42b and xTK-7 and TK-", self.state_dir,
                                                        self.pattern), ("", ""))

    def test_recorded_task_wins_and_damaged_file_is_ignored(self):
        """The state file wins over the name; an unreadable state file falls back to the name."""
        with open(os.path.join(self.state_dir, "good.json"), "w", encoding="utf-8") as state_file:
            json.dump({"task": "TK-103"}, state_file)
        self.assertEqual(self.digest.find_work_hub_task("good", "Old name TK-7", self.state_dir, self.pattern),
                         ("TK-103", "recorded"))
        with open(os.path.join(self.state_dir, "bad.json"), "w", encoding="utf-8") as state_file:
            state_file.write("{not json")
        self.assertEqual(self.digest.find_work_hub_task("bad", "Old name TK-7", self.state_dir, self.pattern),
                         ("TK-7", "name"))

    def test_task_ids_sort_by_number(self):
        """TK-24 comes before TK-103 (text order would put TK-103 first); any prefix works."""
        ordered = sorted(["TK-103", "TK-24", "TK-3"], key=self.digest.task_id_number)
        self.assertEqual(ordered, ["TK-3", "TK-24", "TK-103"])
        self.assertEqual(self.digest.task_id_number("A-B-12"), 12)


# `unittest.main()` finds the TestCase classes above, runs their tests and exits with status 0
# (all passed) or 1 (a failure). It runs only when this file is executed directly.
if __name__ == "__main__":
    unittest.main()
