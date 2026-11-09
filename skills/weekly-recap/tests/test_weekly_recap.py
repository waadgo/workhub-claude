#!/usr/bin/env python3
"""
test_weekly_recap.py - tests of the weekly-recap scripts on synthetic data, with a temporary
workhub-claude settings file.

PURPOSE
    Check, without touching any real settings file, transcript, log, cluster or Notion page, that:
      - recap_common.week_window gives Monday 00:00 to the next Monday 00:00 (also in the week
        of the daylight-saving change) and refuses a malformed week;
      - Task IDs are found with the prefix of the settings (default "TK", or another one);
      - the settings are read correctly: Slurm clusters and hops, transcript hosts, the label of a
        shared home, the orchestrator folder (or none), the output folder, the transcript folders;
      - slurm_week.py reads sacct lines (job + steps), clips hours to the week, computes CPU and
        memory efficiency and per-account totals, and links jobs to rows in its order of trust;
      - week_sources.py finds the orchestrator log entries and DECISIONS.md sections of a week,
        with days written in the entry or inherited from the entry above, and session records
        mentioned in transcripts;
      - claude_tokens_week.py counts each request once (last record of a requestId) and puts
        subagent transcripts under their parent conversation;
      - week_timeline.py builds one lane per (cluster, row) and maps Slurm states to bar states;
      - every script runs end to end from the command line with the temporary settings: a fake
        `sacct` on PATH plays the cluster this machine pretends to be (CC_CLUSTER), a second
        cluster with a closed SSH connection is reported as not reached, and the figure is drawn
        when a renderer and matplotlib are available.

INPUTS
    None: every input is built in memory or written into a temporary folder by the tests.
    Optional environment variables for the figure test:
        WEEKLY_RECAP_TEST_PYTHON     a Python with matplotlib (default: the one running the tests)
        WEEKLY_RECAP_TEST_RENDERER   render_status.py (default: the sibling run-status-figure skill)
    The figure test is skipped when neither gives a renderer that can run.

OUTPUTS
    Test results on the screen. The temporary folders are deleted afterwards.

USAGE
    python3 -m pytest -q tests/                  # from the skill folder
    python3 tests/test_weekly_recap.py -v        # without pytest

DEPENDENCIES
    Python >= 3.9 (zoneinfo), standard library only (`unittest`); bash, find, grep, xargs and
    ssh for the end-to-end tests.

EXIT STATUS
    0 when every test passes, 1 otherwise (unittest's convention).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library)
# ----------------------------------------------------------------------------------------------

# `datetime` builds the synthetic moments (job starts, week windows).
import datetime

# `json` writes the synthetic settings, transcripts and sessions files.
import json

# `os` builds paths and environments; `stat` reads file modes; `sys` gives sys.executable and sys.path.
import os
import stat
import sys

# `shutil.rmtree` deletes a temporary folder after the test; `subprocess` runs the scripts.
import shutil
import subprocess

# `tempfile.mkdtemp` creates a fresh empty folder.
import tempfile

# `unittest`: classes derived from `unittest.TestCase` hold methods named test_*; the
# `self.assert...` methods check the results. pytest runs these classes too.
import unittest

# `ZoneInfo("America/New_York")`: a zone with the same daylight-saving dates as many North
# American users (the change of 2026 is on Sunday Nov 1), used as the synthetic owner's zone.
from zoneinfo import ZoneInfo


# ----------------------------------------------------------------------------------------------
# Make the scripts importable
# ----------------------------------------------------------------------------------------------

# The scripts are plain files in ../scripts, not a package: putting that folder first on
# `sys.path` makes `import slurm_week` find scripts/slurm_week.py (which itself imports
# recap_common and workhub_config from the same folder).
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import recap_common  # noqa: E402  (import after the sys.path change, on purpose)
import slurm_week  # noqa: E402
import week_sources  # noqa: E402
import claude_tokens_week  # noqa: E402
import week_timeline  # noqa: E402

# The synthetic owner's zone, shared by all tests.
ZONE_NAME = "America/New_York"
ZONE = ZoneInfo(ZONE_NAME)

# The Task ID prefix and its pattern for the default settings.
PREFIX = "TK"
PATTERN = recap_common.task_id_pattern(PREFIX)


def make_settings(root):
    """Return a synthetic settings dict whose folders all live under `root` (a temporary folder).

    Machines: this machine pretends to be the cluster "testcluster" (CC_CLUSTER is set in the
    end-to-end tests); "offline" is a cluster whose shared SSH connection is never open;
    "ws01" and "ws02" are workstations sharing the home folder.
    """
    return {
        "schema_version": 1,
        "owner": {"name": "Alex", "time_zone": ZONE_NAME},
        "system_name": {"override_env": "CC_CLUSTER", "aliases": {}},
        "notion": {"tasks_data_source": "", "task_id_prefix": PREFIX,
                   "research_log": {"enabled": False, "data_source": ""}},
        "workflow": {"orchestrator": {"enabled": True, "folder": os.path.join(root, "orchestrator")},
                     "session_records_subdir": "docs/sessions"},
        "machines": {
            "workstations": [
                {"name": "ws01", "shares_home_with_this_machine": True},
                {"name": "ws02", "shares_home_with_this_machine": True},
                {"name": "ws03", "shares_home_with_this_machine": False},
            ],
            "clusters": [
                {"name": "testcluster", "ssh_alias": "testcluster", "scheduler": "slurm",
                 "slurm_accounts": ["def-pi_cpu"], "shares_home_with_this_machine": False},
                {"name": "offline", "ssh_alias": "workhub-test-offline-alias", "scheduler": "slurm",
                 "slurm_accounts": [], "shares_home_with_this_machine": False},
                {"name": "sharedhome", "ssh_alias": "sharedhome", "scheduler": "none",
                 "shares_home_with_this_machine": True},
            ],
            "cluster_hops": [{"name": "testcluster-gpu", "through": "testcluster", "login_node": "gpu-login1"},
                             {"name": "broken-hop", "through": "not-a-cluster", "login_node": "x"}],
        },
        "cluster_login": {"enabled": False},
        "conversations": {"claude_projects_dir": os.path.join(root, "claude", "projects"),
                          "include_codex": False},
        "paths": {"outputs_dir": os.path.join(root, "outputs"), "state_dir": os.path.join(root, "state")},
        "runtime": {"python": sys.executable},
        "skill_settings": {"weekly-recap": {}},
    }


def sacct_line(**values):
    """Build one "|"-separated sacct line in slurm_week.SACCT_FIELDS order.

    Parameters:
        **values: field name -> text, e.g. JobID="1000001", State="COMPLETED". Missing fields are "".
    Returns:
        str: the line, as `sacct -n -P` prints it.
    """
    fields = []
    for name in slurm_week.SACCT_FIELDS:
        fields.append(values.get(name, ""))
    return "|".join(fields)


# ----------------------------------------------------------------------------------------------
# recap_common: the week window and Task IDs
# ----------------------------------------------------------------------------------------------

class WeekWindowTests(unittest.TestCase):
    """week_window turns "last", "this", "YYYY-Www" or a date into Monday 00:00 → next Monday 00:00."""

    def test_last_week_from_a_monday(self):
        # "now" on Monday Nov 9 2026 at 10:00: last week is Mon Nov 2 - Sun Nov 8 (2026-W45).
        now = datetime.datetime(2026, 11, 9, 10, 0, tzinfo=ZONE)
        start, end, label = recap_common.week_window("last", ZONE, now=now)
        self.assertEqual(start, datetime.datetime(2026, 11, 2, 0, 0, tzinfo=ZONE))
        self.assertEqual(end, datetime.datetime(2026, 11, 9, 0, 0, tzinfo=ZONE))
        self.assertEqual(recap_common.week_id(start), "2026-W45")
        self.assertIn("Mon Nov 2", label)

    def test_week_with_daylight_saving_change(self):
        # Week 2026-W44 holds Sun Nov 1, when daylight time (-04:00) ends and standard time
        # (-05:00) starts: the week lasts 169 real hours, and both ends are still midnights.
        # (Python subtracts two times of the same zone by their wall clocks, 168 h, so compare in UTC.)
        start, end, _ = recap_common.week_window("2026-W44", ZONE)
        self.assertEqual(start.utcoffset(), datetime.timedelta(hours=-4))
        self.assertEqual(end.utcoffset(), datetime.timedelta(hours=-5))
        utc = datetime.timezone.utc
        self.assertEqual((end.astimezone(utc) - start.astimezone(utc)).total_seconds() / 3600.0, 169.0)

    def test_job_through_the_change_gets_real_hours(self):
        # A job running Sun Nov 1 00:00 → 04:00 wall clock lasted 5 real hours (01:00-02:00 twice).
        start, end, _ = recap_common.week_window("2026-W44", ZONE)
        job = {"start": datetime.datetime(2026, 11, 1, 0, 0, tzinfo=ZONE),
               "end": datetime.datetime(2026, 11, 1, 4, 0, tzinfo=ZONE),
               "submit": datetime.datetime(2026, 10, 31, 23, 0, tzinfo=ZONE),
               "cpus": 1, "gpus": 0, "elapsed_seconds": 18000.0, "cpu_seconds_used": 0.0,
               "max_rss_gb": None, "requested_memory_gb": None, "state": "COMPLETED"}
        slurm_week.add_week_numbers(job, start, end, now=end)
        self.assertAlmostEqual(job["hours_in_week"], 5.0)
        self.assertAlmostEqual(job["wait_hours"], 1.0)

    def test_date_inside_the_week(self):
        # Any day of the week names that week (Thu Nov 5 → Mon Nov 2).
        start, _, _ = recap_common.week_window("2026-11-05", ZONE)
        self.assertEqual(start.date(), datetime.date(2026, 11, 2))

    def test_malformed_week_is_refused(self):
        with self.assertRaises(ValueError):
            recap_common.week_window("next tuesday", ZONE)


class TaskIdTests(unittest.TestCase):
    """Task IDs come from the prefix of the settings, never from a hard-coded "TK"."""

    def test_default_prefix_forms(self):
        text = "tk118_stage_b, TK-7 and TK 7 again, atk-12 is not one, tk042"
        self.assertEqual(recap_common.task_ids_in(text, PATTERN, PREFIX), ["TK-118", "TK-7", "TK-42"])

    def test_other_prefix(self):
        pattern = recap_common.task_id_pattern("WH")
        found = recap_common.task_ids_in("wh7_run and TK-9", pattern, "WH")
        self.assertEqual(found, ["WH-7"])

    def test_prefix_with_special_characters_is_literal(self):
        # re.escape makes "A.B" match only the literal text "A.B", not "AxB".
        pattern = recap_common.task_id_pattern("A.B")
        self.assertEqual(recap_common.task_ids_in("A.B-3 AxB-4", pattern, "A.B"), ["A.B-3"])

    def test_task_number_sorting(self):
        rows = ["TK-110", "TK-9", "TK-42"]
        self.assertEqual(sorted(rows, key=recap_common.task_number), ["TK-9", "TK-42", "TK-110"])


# ----------------------------------------------------------------------------------------------
# recap_common: values from the settings
# ----------------------------------------------------------------------------------------------

class SettingsTests(unittest.TestCase):
    """The machines, folders and options are read from the settings dict."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="weekly_recap_settings_")
        self.config = make_settings(self.root)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_slurm_targets_and_hops(self):
        targets = recap_common.slurm_targets(self.config)
        names = [target["name"] for target in targets]
        # "sharedhome" has scheduler "none"; "broken-hop" goes through a cluster that is not listed.
        self.assertEqual(names, ["testcluster", "offline", "testcluster-gpu"])
        self.assertEqual(targets[0]["accounts"], ["def-pi_cpu"])
        self.assertEqual(targets[1]["ssh_alias"], "workhub-test-offline-alias")
        self.assertEqual((targets[2]["via_alias"], targets[2]["hop_host"]), ("testcluster", "gpu-login1"))

    def test_transcript_hosts_skip_shared_home_and_this_machine(self):
        hosts = recap_common.transcript_hosts(self.config, "testcluster")
        self.assertEqual(hosts, [{"name": "offline", "ssh_alias": "workhub-test-offline-alias"}])
        self.assertEqual(recap_common.hosts_from_option("none", self.config, "ws01"), [])
        chosen = recap_common.hosts_from_option("offline,unknown", self.config, "ws01")
        self.assertEqual([host["ssh_alias"] for host in chosen], ["workhub-test-offline-alias", "unknown"])

    def test_machine_label_for_shared_home(self):
        self.assertEqual(recap_common.this_machine_label(self.config, "ws01"), "ws01+ws02")
        self.assertEqual(recap_common.this_machine_label({}, "laptop"), "laptop")

    def test_orchestrator_folder_enabled_and_disabled(self):
        self.assertEqual(recap_common.orchestrator_dir(self.config), os.path.join(self.root, "orchestrator"))
        self.config["workflow"]["orchestrator"]["enabled"] = False
        self.assertEqual(recap_common.orchestrator_dir(self.config), "")
        self.assertEqual(recap_common.orchestrator_dir({}), "")

    def test_output_and_transcript_folders(self):
        folder = recap_common.output_dir_for(self.config, "2026-W45", "slurm")
        self.assertEqual(folder, os.path.join(self.root, "outputs", "weekly-recap", "2026-W45", "slurm"))
        self.assertEqual(recap_common.transcript_dirs(self.config), [os.path.join(self.root, "claude", "projects")])
        self.config["conversations"]["include_codex"] = True
        self.assertEqual(recap_common.transcript_dirs(self.config)[1], "~/.codex/sessions")

    def test_shell_path_keeps_tilde_for_the_remote_shell(self):
        self.assertEqual(recap_common.shell_path("~/.claude/projects"), "\"$HOME\"/.claude/projects")
        self.assertEqual(recap_common.shell_path("/srv/my dir"), "'/srv/my dir'")

    def test_week_facts(self):
        now = datetime.datetime(2026, 11, 9, 10, 0, tzinfo=ZONE)
        facts = recap_common.week_facts(self.config, "last", now=now)
        self.assertEqual(facts["WEEK_ID"], "2026-W45")
        self.assertEqual(facts["PREVIOUS_WEEK_ID"], "2026-W44")
        self.assertEqual(facts["OUT"], os.path.join(self.root, "outputs", "weekly-recap", "2026-W45"))
        self.assertEqual(facts["TASK_PREFIX"], "TK")

    def test_private_folders(self):
        target = os.path.join(self.root, "outputs", "weekly-recap", "2026-W45", "slurm")
        recap_common.prepare_output_dir(target)
        for folder in (target, os.path.dirname(target), os.path.dirname(os.path.dirname(target))):
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)


# ----------------------------------------------------------------------------------------------
# slurm_week: parsing, week numbers, linking to rows
# ----------------------------------------------------------------------------------------------

class SlurmParsingTests(unittest.TestCase):
    """parse_sacct_output + add_week_numbers on a synthetic job with two steps."""

    def setUp(self):
        # One 8-CPU, 1-GPU job of 2 h (7200 s) from Sun Nov 1 23:00 to Mon Nov 2 01:00: only the
        # hour after Monday 00:00 belongs to W45. The job line has no TotalCPU; its two steps
        # used 3600 + 7200 CPU-seconds and at most 6 GB of 16 GB requested.
        lines = [
            sacct_line(JobID="1000006", JobName="tk118_stage_b", Account="def-x", Partition="gpu",
                       State="COMPLETED", ExitCode="0:0", Submit="2026-11-01T22:00:00",
                       Start="2026-11-01T23:00:00", End="2026-11-02T01:00:00", ElapsedRaw="7200",
                       Timelimit="03:00:00", AllocTRES="billing=8,cpu=8,gres/gpu:h100=1,mem=16G,node=1",
                       TotalCPU="", MaxRSS="", ReqMem="16G", NNodes="1", WorkDir="/scratch/alex/demo/tk118"),
            sacct_line(JobID="1000006.batch", TotalCPU="01:00:00", MaxRSS="2G"),
            sacct_line(JobID="1000006.0", TotalCPU="02:00:00", MaxRSS="6G"),
        ]
        self.jobs, self.problems = slurm_week.parse_sacct_output(lines, "mycluster", ZONE)
        self.start, self.end, _ = recap_common.week_window("2026-W45", ZONE)

    def test_one_record_per_job_with_step_totals(self):
        self.assertEqual(self.problems, [])
        self.assertEqual(len(self.jobs), 1)
        job = self.jobs[0]
        self.assertEqual((job["cpus"], job["gpus"], job["gpu_type"]), (8, 1, "h100"))
        self.assertAlmostEqual(job["cpu_seconds_used"], 10800.0)
        self.assertAlmostEqual(job["max_rss_gb"], 6.0, places=2)

    def test_hours_clipped_to_the_week(self):
        job = self.jobs[0]
        slurm_week.add_week_numbers(job, self.start, self.end, now=self.end)
        # 1 h inside the week → 8 core-h and 1 GPU-h.
        self.assertAlmostEqual(job["hours_in_week"], 1.0)
        self.assertAlmostEqual(job["core_hours"], 8.0)
        self.assertAlmostEqual(job["gpu_hours"], 1.0)
        # Efficiency uses the whole run: 10800 / (8 × 7200) = 0.1875.
        self.assertAlmostEqual(job["cpu_efficiency"], 0.1875)
        self.assertAlmostEqual(job["memory_efficiency"], 6.0 / 16.0, places=2)
        # The job started before the week, so its wait belongs to the week before.
        self.assertIsNone(job["wait_hours"])
        self.assertEqual(job["week_status"], "completed")

    def test_account_totals(self):
        job = self.jobs[0]
        slurm_week.add_week_numbers(job, self.start, self.end, now=self.end)
        totals = slurm_week.cluster_totals(self.jobs)
        self.assertEqual(totals["by_account"]["def-x"]["jobs"], 1)
        self.assertAlmostEqual(totals["by_account"]["def-x"]["core_hours"], 8.0)

    def test_short_line_is_reported_not_crashed(self):
        jobs, problems = slurm_week.parse_sacct_output(["123|only|three"], "mycluster", ZONE)
        self.assertEqual(jobs, [])
        self.assertEqual(len(problems), 1)

    def test_sacct_command_uses_owner_zone_and_accounts(self):
        line = slurm_week.sacct_shell_command(self.start, self.end, ZONE_NAME, ["def-a", "def-b"])
        self.assertIn("TZ=America/New_York sacct -u \"$USER\" -A def-a,def-b", line)
        self.assertIn("-S 2026-11-02T00:00:00 -E 2026-11-09T00:00:00", line)
        self.assertNotIn(" -A ", slurm_week.sacct_shell_command(self.start, self.end, ZONE_NAME))


class SlurmLinkingTests(unittest.TestCase):
    """link_jobs_to_rows: job name, then conversation, then log, then same folder."""

    def make_job(self, job_id, name, workdir="/home/alex"):
        return {"job_id": job_id, "name": name, "workdir": workdir}

    def test_order_of_trust(self):
        jobs = [
            self.make_job("1000001", "tk42_merge"),                                    # 1. job name
            self.make_job("1000002", "segment"),                                       # 2. conversation
            self.make_job("1000003_[2-63]", "segarray", "/scratch/alex/demo/seg/run1"),  # 3. log
            self.make_job("1000004", "segretry", "/scratch/alex/demo/seg/run1"),       # 4. same folder
            self.make_job("1000005", "interactive"),                                   # no row
        ]
        # The conversation of TK-80 mentions job 1000002; the orchestrator conversation (no row)
        # mentions 1000003, which must not count.
        session_pairs = [("TK-80", "submitted 1000002 on mycluster"), ("", "watching 1000003")]
        # The log entry names TK-121 first, but the job ID sits next to TK-95 in its own sentence.
        log_entries = ["TK-121 not started yet. TK-95 [a2f4d1] submitted array 1000003_[2-63] on mycluster."]
        slurm_week.link_jobs_to_rows(jobs, log_entries, session_pairs, PATTERN, PREFIX)
        found = [(job["task_id"], job["task_source"]) for job in jobs]
        self.assertEqual(found, [("TK-42", "job name"), ("TK-80", "conversation"),
                                 ("TK-95", "orchestrator log"), ("TK-95", "same folder"), ("", "")])

    def test_job_id_before_underscore_is_found(self):
        # "1000003_[2-63]": the digits end at "_", which \b would not treat as a boundary.
        pairs = slurm_week.tasks_next_to_job_ids("TK-121 submitted 1000003_[2-63]", {"1000003"}, PATTERN, PREFIX)
        self.assertEqual(pairs, [("1000003", "TK-121")])

    def test_project_rules_first_match(self):
        rules = [{"label": "Project A segmentation", "patterns": ["segment"]},
                 {"label": "Project A", "patterns": ["proja_"]}]
        job = {"name": "proja_segment", "workdir": "/x"}
        self.assertEqual(slurm_week.project_label(job, rules), "Project A segmentation")
        self.assertEqual(slurm_week.project_label({"name": "misc-job", "workdir": "/x"}, rules), "other: misc")
        self.assertEqual(slurm_week.project_label({"name": "misc-job", "workdir": "/x"}, []), "other: misc")

    def test_rules_file_from_settings(self):
        # Default: no rules file → no rules.
        self.assertEqual(slurm_week.rules_path_from_option("auto", {}), "")
        config = {"skill_settings": {"weekly-recap": {"project_rules_file": "/srv/rules.json"}}}
        self.assertEqual(slurm_week.rules_path_from_option("auto", config), "/srv/rules.json")
        self.assertEqual(slurm_week.rules_path_from_option("none", config), "")

    def test_example_rules_file_is_valid(self):
        path = os.path.join(SKILL_DIR, "references", "project_rules.example.json")
        rules = slurm_week.load_rules(path)
        self.assertTrue(rules)
        for rule in rules:
            self.assertTrue(rule["label"])
            self.assertTrue(all(isinstance(pattern, str) for pattern in rule["patterns"]))


# ----------------------------------------------------------------------------------------------
# week_sources: orchestrator log, DECISIONS.md and session records
# ----------------------------------------------------------------------------------------------

class WeekSourcesTests(unittest.TestCase):
    """Log entries carry their own day or inherit the day of the entry above."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="weekly_recap_test_")
        os.makedirs(os.path.join(self.folder, "log"))
        log_text = ("- Sun Nov 1 23:50: TK-10 last entry of the week before.\n"
                    "- Mon Nov 2 09:00: TK-42 shard 0 started on ws01.\n"
                    "  continued on a second line.\n"
                    "- 09:30 TK-43 booked ws02 (no day written: inherits Mon Nov 2).\n"
                    "- 23:40 TK-44 late entry, still Mon Nov 2.\n"
                    "- 00:08 TK-45 after midnight: Tue Nov 3.\n"
                    "- Mon Nov 9 08:00: next week's entry.\n")
        with open(os.path.join(self.folder, "log", "2026-11.md"), "w", encoding="utf-8") as log_file:
            log_file.write(log_text)
        decisions_text = ("# Decisions\n\n## Sun Nov 1 2026\nold\n\n## Wed Nov 4 2026 (12:00)\n"
                          "Q150: \"yes\" (12:01).\n\n## Mon Nov 9 2026\nlater\n")
        with open(os.path.join(self.folder, "DECISIONS.md"), "w", encoding="utf-8") as decisions_file:
            decisions_file.write(decisions_text)
        self.start, _, _ = recap_common.week_window("2026-W45", ZONE)

    def tearDown(self):
        shutil.rmtree(self.folder)

    def test_log_entries_of_the_week(self):
        entries = week_sources.log_entries_of_week(self.start, self.folder, PATTERN, PREFIX)
        self.assertEqual(len(entries), 4)
        self.assertIn("continued on a second line", entries[0]["text"])
        self.assertEqual(entries[0]["tasks"], ["TK-42"])
        self.assertEqual(entries[1]["date"], "2026-11-02")
        self.assertEqual(entries[1]["tasks"], ["TK-43"])
        self.assertEqual(entries[2]["date"], "2026-11-02")
        self.assertEqual(entries[3]["date"], "2026-11-03")

    def test_no_orchestrator(self):
        self.assertEqual(week_sources.log_entries_of_week(self.start, "", PATTERN, PREFIX), [])
        self.assertEqual(week_sources.decisions_of_week(self.start, ""), [])

    def test_decisions_of_the_week(self):
        sections = week_sources.decisions_of_week(self.start, self.folder)
        self.assertEqual(len(sections), 1)
        self.assertTrue(sections[0]["heading"].startswith("Wed Nov 4"))
        self.assertIn("Q150", sections[0]["text"])

    def test_group_by_row(self):
        conversations = [{"task": "TK-42", "title": "[UC] TK-42 x"},
                         {"task": "", "title": "[Nov-02 08:00][ws01][ORCH] Orchestrator"},
                         {"task": "", "title": "quick question"}]
        entries = week_sources.log_entries_of_week(self.start, self.folder, PATTERN, PREFIX)
        rows, unfiled, orchestrators = week_sources.group_by_row(conversations, entries)
        self.assertEqual(list(rows), ["TK-42", "TK-43", "TK-44", "TK-45"])
        self.assertEqual(len(orchestrators), 1)
        self.assertEqual(len(unfiled["conversations"]), 1)

    def test_session_records_in_transcripts(self):
        # A transcript mentions two session records of the week (one exists, one does not) and one
        # of another week; only the existing record of the week is listed.
        projects = os.path.join(self.folder, "claude", "projects", "-demo")
        records = os.path.join(self.folder, "proj", "docs", "sessions")
        os.makedirs(projects)
        os.makedirs(records)
        kept = os.path.join(records, "2026-11-04_1200_demo.md")
        with open(kept, "w", encoding="utf-8") as record_file:
            record_file.write("# demo\n")
        missing = os.path.join(records, "2026-11-05_0900_gone.md")
        other_week = os.path.join(records, "2026-11-10_0900_later.md")
        with open(other_week, "w", encoding="utf-8") as record_file:
            record_file.write("# later\n")
        line = json.dumps({"message": "wrote %s, %s and %s" % (kept, missing, other_week)})
        with open(os.path.join(projects, "s1.jsonl"), "w", encoding="utf-8") as transcript:
            transcript.write(line + "\n")
        found, notes = week_sources.session_records_of_week(
            self.start, [], "ws01", "ws01", [os.path.join(self.folder, "claude", "projects")], "docs/sessions")
        self.assertEqual(found, ["ws01:" + kept])
        self.assertEqual(notes, [])


# ----------------------------------------------------------------------------------------------
# claude_tokens_week: counting requests once
# ----------------------------------------------------------------------------------------------

def token_record(request_id, timestamp, output_tokens):
    """One synthetic assistant record of a transcript, as Claude Code writes it."""
    return json.dumps({"type": "assistant", "requestId": request_id, "timestamp": timestamp,
                       "cwd": "/home/alex/demo",
                       "message": {"model": "claude-test-model",
                                   "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                                             "cache_read_input_tokens": 100,
                                             "output_tokens": output_tokens}}})


def write_token_transcripts(projects_dir):
    """Write a main transcript (sess1) and one subagent transcript under `projects_dir`."""
    project = os.path.join(projects_dir, "-home-alex-demo")
    os.makedirs(os.path.join(project, "sess1", "subagents"))
    main_lines = [token_record("req1", "2026-11-03T15:00:00Z", 5),     # first copy of req1
                  token_record("req1", "2026-11-03T15:00:01Z", 50),    # final copy: this one counts
                  token_record("req2", "2026-11-10T15:00:00Z", 999)]   # after the week: ignored
    with open(os.path.join(project, "sess1.jsonl"), "w", encoding="utf-8") as transcript:
        transcript.write("\n".join(main_lines) + "\n")
    with open(os.path.join(project, "sess1", "subagents", "agent-a.jsonl"), "w", encoding="utf-8") as transcript:
        transcript.write(token_record("req3", "2026-11-04T15:00:00Z", 7) + "\n")


class TokenTests(unittest.TestCase):
    """A request written twice (streaming updates) counts once; subagents join their parent."""

    def setUp(self):
        self.projects_dir = tempfile.mkdtemp(prefix="weekly_recap_tokens_")
        write_token_transcripts(self.projects_dir)

    def tearDown(self):
        shutil.rmtree(self.projects_dir)

    def test_dedupe_and_subagents(self):
        start, end, _ = recap_common.week_window("2026-W45", ZONE)
        totals = claude_tokens_week.scan_transcripts(self.projects_dir, start.timestamp(), end.timestamp())
        self.assertEqual(list(totals), ["sess1"])
        session = totals["sess1"]
        self.assertEqual((session["requests"], session["subagent_requests"]), (1, 1))
        self.assertEqual(session["output_tokens"], 50 + 7)
        self.assertEqual(session["cache_read_input_tokens"], 200)
        self.assertEqual(session["models"]["claude-test-model"], (10 + 100 + 50) + (10 + 100 + 7))

    def test_rows_and_orchestrator(self):
        per_machine = {"ws01+ws02": {"aaaa1111": {"output_tokens": 5}, "bbbb2222": {"output_tokens": 7},
                                     "cccc3333": {"output_tokens": 9}}}
        names = {"aaaa1111": {"task": "TK-42", "title": "TK-42 demo"},
                 "bbbb2222": {"task": "", "title": "[ORCH] Orchestrator"}}
        report = claude_tokens_week.build_report(per_machine, names, "2026-W45")
        self.assertEqual(sorted(report["by_row"]), ["ORCH", "TK-42", "no row"])
        self.assertEqual(report["totals"]["total"], 21)


# ----------------------------------------------------------------------------------------------
# week_timeline: lanes and bar states
# ----------------------------------------------------------------------------------------------

class TimelineTests(unittest.TestCase):
    """build_lanes groups jobs per (cluster, row) and marks running and failed jobs."""

    def make_job(self, job_id, task_id, label, state, week_status, start, end, cpus=4, gpus=0):
        return {"cluster": "mycluster", "job_id": job_id, "name": "job" + job_id, "task_id": task_id,
                "label": label, "state": state, "week_status": week_status, "start": start, "end": end,
                "hours_in_week": 1.0, "core_hours": float(cpus), "gpu_hours": float(gpus), "gpus": gpus}

    def test_lanes_and_states(self):
        start, end, _ = recap_common.week_window("2026-W45", ZONE)
        jobs = [
            self.make_job("1", "TK-80", "Project A", "COMPLETED", "completed",
                          "2026-11-03T10:00:00-05:00", "2026-11-03T11:00:00-05:00"),
            self.make_job("2", "TK-80", "Project A", "TIMEOUT", "timeout",
                          "2026-11-04T10:00:00-05:00", "2026-11-04T11:00:00-05:00"),
            self.make_job("3", "", "Storage and backups", "RUNNING", "running at week end",
                          "2026-11-08T23:00:00-05:00", None, gpus=1),
            self.make_job("4", "TK-81", "other: job4", "COMPLETED", "completed",
                          "2026-11-05T10:00:00-05:00", "2026-11-05T11:00:00-05:00"),
        ]
        lanes = week_timeline.build_lanes(jobs, start, end, max_lanes=16)
        names = sorted(lane["name"] for lane in lanes)
        # A row whose jobs matched no project rule is named by the row alone.
        self.assertEqual(names, ["mycluster · Storage and backups", "mycluster · TK-80 · Project A",
                                 "mycluster · TK-81"])
        by_name = {lane["name"]: lane for lane in lanes}
        statuses = [run["status"] for run in by_name["mycluster · TK-80 · Project A"]["runs"]]
        self.assertEqual(statuses, ["done", "failed"])
        running = by_name["mycluster · Storage and backups"]["runs"][0]
        self.assertEqual(running["status"], "running")
        self.assertIn("estimate_end", running)
        self.assertTrue(by_name["mycluster · Storage and backups"]["gpu"])

    def test_hours_text_keeps_small_gpu_hours(self):
        # A 0.5 GPU-h job must not print as "0 GPU-h".
        self.assertEqual(week_timeline.hours_text(0.5), "0.5")
        self.assertEqual(week_timeline.hours_text(64.0), "64")

    def test_small_lanes_are_merged(self):
        start, end, _ = recap_common.week_window("2026-W45", ZONE)
        jobs = []
        for number in range(5):
            jobs.append(self.make_job(str(number), "TK-%d" % (number + 1), "x", "COMPLETED", "completed",
                                      "2026-11-03T10:00:00-05:00", "2026-11-03T11:00:00-05:00", cpus=number + 1))
        lanes = week_timeline.build_lanes(jobs, start, end, max_lanes=2)
        self.assertEqual(len(lanes), 3)
        self.assertEqual(lanes[-1]["name"], "mycluster · other")
        self.assertEqual(len(lanes[-1]["runs"]), 3)


# ----------------------------------------------------------------------------------------------
# End to end: every script from the command line, with the temporary settings
# ----------------------------------------------------------------------------------------------

# The fake `sacct`: it prints three jobs of week 2026-W45 (and the steps of the first one), and
# writes the TZ it was given and its arguments to a file, so the test can check them.
FAKE_SACCT = """#!/bin/bash
echo "TZ=$TZ ARGS=$*" >> "%(log)s"
cat <<'LINES'
%(lines)s
LINES
"""


class EndToEndTests(unittest.TestCase):
    """Run the scripts as the user would, with WORKHUB_CLAUDE_CONFIG pointing at a temporary file."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="weekly_recap_e2e_")
        self.config = make_settings(self.root)
        self.settings_path = os.path.join(self.root, "config.json")
        with open(self.settings_path, "w", encoding="utf-8") as settings_file:
            json.dump(self.config, settings_file)
        self.out = os.path.join(self.root, "outputs", "weekly-recap", "2026-W45")

        # The fake sacct in its own bin folder, put first on PATH.
        bin_dir = os.path.join(self.root, "bin")
        os.makedirs(bin_dir)
        self.sacct_log = os.path.join(self.root, "sacct_calls.txt")
        lines = [
            sacct_line(JobID="1000001", JobName="tk42_train", Account="def-pi_cpu", Partition="gpu",
                       State="COMPLETED", ExitCode="0:0", Submit="2026-11-03T09:00:00",
                       Start="2026-11-03T10:00:00", End="2026-11-03T14:00:00", ElapsedRaw="14400",
                       Timelimit="06:00:00", AllocTRES="billing=4,cpu=4,gres/gpu:a100=1,gres/gpu=1,mem=32G,node=1",
                       TotalCPU="10:00:00", MaxRSS="", ReqMem="32G", NNodes="1",
                       WorkDir="/scratch/alex/demo/train"),
            sacct_line(JobID="1000001.batch", TotalCPU="10:00:00", MaxRSS="20G"),
            sacct_line(JobID="1000002", JobName="demo_prep", Account="def-other", Partition="cpu",
                       State="TIMEOUT", ExitCode="0:0", Submit="2026-11-04T08:00:00",
                       Start="2026-11-04T08:30:00", End="2026-11-04T10:30:00", ElapsedRaw="7200",
                       Timelimit="02:00:00", AllocTRES="billing=8,cpu=8,mem=16G,node=1",
                       TotalCPU="04:00:00", MaxRSS="", ReqMem="16G", NNodes="1",
                       WorkDir="/scratch/alex/demo/train"),
            sacct_line(JobID="1000003", JobName="backup_copy", Account="def-pi_cpu", Partition="cpu",
                       State="RUNNING", ExitCode="0:0", Submit="2026-11-08T19:00:00",
                       Start="2026-11-08T20:00:00", End="Unknown", ElapsedRaw="3600",
                       Timelimit="1-00:00:00", AllocTRES="billing=2,cpu=2,mem=4G,node=1",
                       TotalCPU="00:10:00", MaxRSS="", ReqMem="4G", NNodes="1", WorkDir="/home/alex"),
        ]
        with open(os.path.join(bin_dir, "sacct"), "w", encoding="utf-8") as fake:
            fake.write(FAKE_SACCT % {"log": self.sacct_log, "lines": "\n".join(lines)})
        os.chmod(os.path.join(bin_dir, "sacct"), 0o755)

        # The environment of every script run: the temporary settings, this machine = "testcluster".
        self.env = dict(os.environ)
        self.env["WORKHUB_CLAUDE_CONFIG"] = self.settings_path
        self.env["CC_CLUSTER"] = "testcluster"
        self.env["PATH"] = bin_dir + os.pathsep + self.env.get("PATH", "")

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_script(self, name, *arguments):
        """Run scripts/<name> with the test environment; return the CompletedProcess."""
        command = [sys.executable, os.path.join(SCRIPTS_DIR, name)] + list(arguments)
        return subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=180)

    def test_missing_settings_exit_2(self):
        self.env["WORKHUB_CLAUDE_CONFIG"] = os.path.join(self.root, "nothing_here.json")
        completed = self.run_script("recap_common.py", "--week", "2026-W45")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("set up my work hub", completed.stderr)

    def test_week_facts_shell_and_connections(self):
        completed = self.run_script("recap_common.py", "--week", "2026-W45", "--shell")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("WEEK_ID=2026-W45", completed.stdout)
        self.assertIn("OUT=" + self.out, completed.stdout)
        self.assertIn("HOSTS=offline", completed.stdout)
        completed = self.run_script("recap_common.py", "--connections")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("testcluster      this machine", completed.stdout)
        self.assertIn("Not connected: offline", completed.stdout)

    def test_slurm_week_end_to_end(self):
        completed = self.run_script("slurm_week.py", "--week", "2026-W45", "--clusters", "testcluster,offline")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with open(os.path.join(self.out, "slurm", "slurm_week.json"), encoding="utf-8") as report_file:
            report = json.load(report_file)
        reached = {cluster["cluster"]: cluster["reached"] for cluster in report["clusters"]}
        self.assertEqual(reached, {"testcluster": True, "offline": False})
        jobs = {job["job_id"]: job for job in report["jobs"]}
        self.assertEqual(sorted(jobs), ["1000001", "1000002", "1000003"])
        # Job name → row; same folder → same row; the bare home folder gives no row.
        self.assertEqual((jobs["1000001"]["task_id"], jobs["1000001"]["task_source"]), ("TK-42", "job name"))
        self.assertEqual((jobs["1000002"]["task_id"], jobs["1000002"]["task_source"]), ("TK-42", "same folder"))
        self.assertEqual(jobs["1000003"]["task_id"], "")
        self.assertEqual(jobs["1000003"]["week_status"], "running at week end")
        self.assertAlmostEqual(jobs["1000001"]["gpu_hours"], 4.0)
        # The sacct ran with the owner's zone and the week's window.
        with open(self.sacct_log, encoding="utf-8") as calls:
            call_text = calls.read()
        self.assertIn("TZ=America/New_York", call_text)
        self.assertIn("-S 2026-11-02T00:00:00 -E 2026-11-09T00:00:00", call_text)
        # The summary marks the account that the settings do not list; outputs are private.
        with open(os.path.join(self.out, "slurm", "slurm_week.txt"), encoding="utf-8") as summary_file:
            summary = summary_file.read()
        self.assertIn("def-other (not in the settings)", summary)
        self.assertIn("NOT reached: offline", summary)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.out, "slurm", "slurm_week.json")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.out).st_mode), 0o700)
        # A second run without --overwrite is refused.
        again = self.run_script("slurm_week.py", "--week", "2026-W45", "--clusters", "testcluster")
        self.assertEqual(again.returncode, 1)

    def test_tokens_and_sources_end_to_end(self):
        # Transcripts in the configured projects folder, and a sessions.json as session_digest.py writes it.
        write_token_transcripts(self.config["conversations"]["claude_projects_dir"])
        sessions_path = os.path.join(self.root, "sessions.json")
        tuesday = datetime.datetime(2026, 11, 3, 15, 0, tzinfo=ZONE).timestamp()
        sessions = {"sessions": [
            {"id": "sess1", "title": "[UC] TK-42 demo", "system": "ws01", "cwd": "/home/alex/demo",
             "task": "TK-42", "client": "claude",
             "turns": [{"time": tuesday, "kind": "human", "prompt": "train it", "reply": "job 1000001 done"}]},
        ]}
        with open(sessions_path, "w", encoding="utf-8") as sessions_file:
            json.dump(sessions, sessions_file)
        os.makedirs(os.path.join(self.root, "orchestrator", "log"))
        with open(os.path.join(self.root, "orchestrator", "log", "2026-11.md"), "w", encoding="utf-8") as log_file:
            log_file.write("- Tue Nov 3 10:05: TK-42 job 1000001 started on testcluster.\n")

        completed = self.run_script("claude_tokens_week.py", "--week", "2026-W45", "--hosts", "none",
                                    "--sessions-json", sessions_path)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with open(os.path.join(self.out, "tokens", "tokens_week.json"), encoding="utf-8") as tokens_file:
            tokens = json.load(tokens_file)
        # This machine is "testcluster"; ws01 and ws02 share the home, so the label names all three.
        self.assertEqual(list(tokens["by_machine"]), ["testcluster+ws01+ws02"])
        self.assertEqual(tokens["by_row"]["TK-42"]["output_tokens"], 57)

        completed = self.run_script("week_sources.py", "--week", "2026-W45", "--hosts", "none",
                                    "--sessions-json", sessions_path)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with open(os.path.join(self.out, "sources", "rows", "TK-42.txt"), encoding="utf-8") as row_file:
            row_text = row_file.read()
        self.assertIn("train it", row_text)
        self.assertIn("job 1000001 started", row_text)

    def test_timeline_figure_end_to_end(self):
        # The renderer of the sibling run-status-figure skill (or the one named in the environment)
        # and a Python with matplotlib are needed; without them the figure part is skipped.
        renderer = os.environ.get("WEEKLY_RECAP_TEST_RENDERER") or week_timeline.DEFAULT_RENDERER
        python = os.environ.get("WEEKLY_RECAP_TEST_PYTHON") or sys.executable
        if not os.path.isfile(renderer):
            self.skipTest("no run-status-figure renderer at %s" % renderer)
        probe = subprocess.run([python, "-c", "import matplotlib"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("%s has no matplotlib" % python)

        completed = self.run_script("slurm_week.py", "--week", "2026-W45", "--clusters", "testcluster")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        extra_path = os.path.join(self.out, "extra_runs.json")
        with open(extra_path, "w", encoding="utf-8") as extra_file:
            json.dump([{"lane": "ws02 · TK-43", "label": "night part", "status": "done",
                        "start": "2026-11-04T22:00:00", "end": "2026-11-05T03:00:00", "gpu": True}], extra_file)
        completed = self.run_script("week_timeline.py", "--week", "2026-W45", "--extra-runs", extra_path,
                                    "--renderer", renderer, "--python", python)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        with open(os.path.join(self.out, "figure", "week_timeline.png"), "rb") as png_file:
            self.assertEqual(png_file.read(8), b"\x89PNG\r\n\x1a\n")
        with open(os.path.join(self.out, "figure", "week_timeline.pdf"), "rb") as pdf_file:
            self.assertEqual(pdf_file.read(4), b"%PDF")
        with open(os.path.join(self.out, "figure", "week_timeline.json"), encoding="utf-8") as status_file:
            status = json.load(status_file)
        lane_names = [stage["name"] for stage in status["stages"]]
        self.assertEqual(lane_names[0], "ws02 · TK-43")
        self.assertIn("testcluster · TK-42", lane_names)


if __name__ == "__main__":
    unittest.main()
