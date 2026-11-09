#!/usr/bin/env python3
"""
test_scripts.py - tests of the compute-planner scripts on synthetic inputs.

PURPOSE
    Check, with small synthetic files and no real machine, task, settings file or Notion page,
    that:
      - plan_schedule.py builds the weekly blocked windows correctly across a daylight-saving
        change (local clock times, real durations; the 2026 dates are used only because the
        clocks change on Sun 2026-11-01 in the test zone);
      - a task that does not fit one free window is unplaced with can_pause false and split in
        parts with can_pause true;
      - a task whose cost basis is "none" gets a measure block and waits for measurement;
      - a hard deadline promotes a task only when the promotion makes it meet the deadline;
      - a dependency on a queued commitment without estimate_end leaves the task unplaced;
      - the cluster is chosen when it ends earlier, with the budget warning, and the
        workstation otherwise (the cluster recorded as the alternative);
      - a workstation with two GPU cards runs two GPU tasks at once; a commitment matches its
        machine by label or by name; a CPU-only machine still reports free windows;
      - CPU work goes to the cluster named by prefer_cluster_for_cpu unless that misses the
        deadline; a budget with applies_to counts only jobs of those resource types;
      - the CLI refuses to overwrite outputs, writes the three files with --overwrite, and
        writes nothing with --dry-run;
      - task_file.py add / order / rank / measured / set / state round-trip through the file,
        its default file and time zone come from the settings, and --dry-run writes nothing;
      - planner_settings.py turns the settings (usage windows, GPU lists, budgets) and the
        facts file into machines.json, refuses rule words it does not understand, and its
        get / stamp / show / machines commands behave as documented;
      - the example in assets/ runs end to end and gives the documented plan.

INPUTS
    None: every input file (including a temporary workhub-claude settings file, named by the
    environment variable WORKHUB_CLAUDE_CONFIG for this process and its child processes) is
    written into temporary folders by the tests, and the files in ../assets.

OUTPUTS
    Test results on the screen. The temporary folders are deleted afterwards.

USAGE
    python3 -m pytest tests -q                    # from the skill folder
    python3 tests/test_scripts.py -v              # without pytest, one line per test

DEPENDENCIES
    Python >= 3.9, standard library only (`unittest`); pytest is optional.

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

# `json` writes the synthetic inputs and reads the outputs.
import json

# `os` builds paths and sets the environment variables of the settings file.
import os

# `re` checks the format of the time stamp printed by `planner_settings.py stamp`.
import re

# `shutil.rmtree` deletes the temporary folders; `tempfile.mkdtemp` creates them.
import shutil
import tempfile

# `subprocess.run` runs the scripts as the user would, from the command line.
import subprocess

# `sys.executable` is the Python running the tests; the scripts are run with it.
import sys

# `unittest` is Python's test framework (pytest runs these classes too).
import unittest

# `ZoneInfo` builds the test zone for the expected local times.
from zoneinfo import ZoneInfo


# ----------------------------------------------------------------------------------------------
# A temporary settings file for the whole test run
# ----------------------------------------------------------------------------------------------

# The scripts read the workhub-claude settings when they are imported (task_file.py takes its
# time zone from them) and when they run. The tests must never read the real settings of the
# person running them, so a temporary settings file is written FIRST and named by
# WORKHUB_CLAUDE_CONFIG, before any script is loaded. `os.environ[...] = ...` changes the
# environment of this process and of every child process started by subprocess.run.
# America/New_York has the same daylight-saving dates as the expected values below
# (EDT -04:00 until Sun 2026-11-01 02:00, then EST -05:00).
SETTINGS_ROOT = tempfile.mkdtemp(prefix="planner_settings_test_")
SETTINGS_PATH = os.path.join(SETTINGS_ROOT, "config.json")
TEST_PLANNER_DIR = os.path.join(SETTINGS_ROOT, "planner")
TEST_SETTINGS = {
    "schema_version": 1,
    "owner": {"name": "Alex", "time_zone": "America/New_York"},
    "paths": {"outputs_dir": os.path.join(SETTINGS_ROOT, "outputs"), "state_dir": os.path.join(SETTINGS_ROOT, "state")},
    "workflow": {"orchestrator": {"enabled": False, "folder": ""}},
    "machines": {
        "workstations": [
            {"name": "ws01", "ssh_alias": "", "cpus": 32, "ram_gb": 128,
             "gpus": [{"model": "RTX 4090", "memory_gb": 24, "count": 1}], "usage_windows": [], "notes": "any time"},
        ],
        "clusters": [
            {"name": "mycluster", "ssh_alias": "mycluster", "scheduler": "slurm", "slurm_accounts": ["def-example_gpu"]},
        ],
    },
    "skill_settings": {"compute-planner": {"planner_dir": TEST_PLANNER_DIR, "horizon_days": 5}},
}
with open(SETTINGS_PATH, mode="w", encoding="utf-8") as settings_file:
    json.dump(TEST_SETTINGS, settings_file)
os.environ["WORKHUB_CLAUDE_CONFIG"] = SETTINGS_PATH
# A COMPUTE_PLANNER_DIR of the person running the tests would win over the settings: removed.
# `dict.pop(key, None)` removes the key and returns None when it was not there (no error).
os.environ.pop("COMPUTE_PLANNER_DIR", None)


def tearDownModule():
    """Delete the temporary settings folder after the last test (unittest and pytest call this)."""
    shutil.rmtree(SETTINGS_ROOT, ignore_errors=True)


# ----------------------------------------------------------------------------------------------
# Paths of the scripts under test, loaded as modules
# ----------------------------------------------------------------------------------------------

# `os.path.dirname(__file__)` is the tests folder; the scripts are in ../scripts.
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(TESTS_DIR)
PLAN_SCRIPT = os.path.join(SKILL_DIR, "scripts", "plan_schedule.py")
TASK_SCRIPT = os.path.join(SKILL_DIR, "scripts", "task_file.py")
SETTINGS_SCRIPT = os.path.join(SKILL_DIR, "scripts", "planner_settings.py")
ASSETS_DIR = os.path.join(SKILL_DIR, "assets")


def load_module(name, path):
    """Load a script file as a module so its functions can be called directly."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan_schedule = load_module("plan_schedule", PLAN_SCRIPT)
task_file = load_module("task_file", TASK_SCRIPT)
planner_settings = load_module("planner_settings", SETTINGS_SCRIPT)

ZONE = ZoneInfo("America/New_York")


# ----------------------------------------------------------------------------------------------
# Builders of synthetic inputs
# ----------------------------------------------------------------------------------------------

def workstation(name, gpu_memory=24, blocked_weekdays=False, exceptions=None, gpu_count=1):
    """One machines.json workstation entry; blocked_weekdays adds the Mon-Fri 08:50-18:00 rule."""
    blocked = []
    if blocked_weekdays:
        blocked = [{"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "from": "08:50", "to": "18:00",
                    "reason": "shared in the day"}]
    gpu = {"model": "GPU", "memory_gb": gpu_memory, "count": gpu_count}
    return {"name": name, "kind": "workstation", "label": name + " · GPU", "gpu": gpu,
            "ram_gb": 125, "threads": 32, "ram_heavy_gb": 60, "blocked": blocked, "exceptions": exceptions or [], "rules": []}


def cpu_workstation(name):
    """One machines.json workstation without a GPU."""
    return {"name": name, "kind": "workstation", "label": name + " · CPU", "gpu": None,
            "ram_gb": 64, "threads": 16, "ram_heavy_gb": None, "blocked": [], "exceptions": [], "rules": []}


def cluster(name="mycluster", wait=36.0, budget=None, outages=None, cpu_wait=None):
    """One machines.json cluster entry with a measured queue wait (and optionally CPU nodes)."""
    gpus = {"h100": {"model": "H100"}}
    waits = {"h100": {"typical": wait, "basis": "measured", "source": "test"}}
    if cpu_wait is not None:
        gpus["cpu"] = {"model": "CPU nodes"}
        waits["cpu"] = {"typical": cpu_wait, "basis": "measured", "source": "test"}
    return {"name": name, "kind": "cluster", "label": name + " · H100", "gpus": gpus,
            "queue_wait_hours": waits, "max_walltime_h": 168, "outages": outages or [], "budget": budget, "rules": []}


def machines_file(machines, prefer_cluster_for_cpu=""):
    return {"schema": "compute-machines/1", "timezone": "America/New_York", "updated": "2026-10-28T17:00:00-04:00",
            "prefer_cluster_for_cpu": prefer_cluster_for_cpu, "machines": machines}


def task(task_id, rank, hours=10.0, basis="measured", gpu=True, machines=None, can_pause=False, deadline=None,
         depends_on=None, cluster_option=None, ram_gb=20, gpu_memory_gb=10, accept_estimate=False):
    """One task card with sensible defaults."""
    return {"id": task_id, "title": task_id.replace("_", " "), "project": "test", "work_hub": None, "rank": rank,
            "state": "pending", "deadline": deadline, "depends_on": depends_on or [], "not_before": None,
            "needs": {"gpu": gpu, "gpu_memory_gb": gpu_memory_gb, "ram_gb": ram_gb, "threads": 8, "disk_gb": 10,
                      "machines": machines or []},
            "cost": {"hours": hours, "basis": basis, "source": "test", "measured_on": None, "date": None},
            "measure": {"hours": 1.0, "what": "a short run"}, "accept_estimate": accept_estimate,
            "can_pause": can_pause, "cluster": cluster_option, "notes": "", "history": []}


def tasks_file(tasks):
    return {"schema": "compute-tasks/1", "updated": "2026-10-28T17:00:00-04:00", "tasks": tasks}


def commitments_file(stages=None):
    return {"schema": "run-status/1", "title": "test", "timezone": "EDT", "generated": "2026-10-28T17:00:00",
            "stages": stages or []}


def run_plan(tasks, machines, commitments, now, days=7, prefer_cluster_for_cpu=""):
    """Call make_plan directly and return (plan, status_plan)."""
    inputs = {"tasks": "T", "machines": "M", "commitments": "C"}
    return plan_schedule.make_plan(tasks_file(tasks), machines_file(machines, prefer_cluster_for_cpu),
                                   commitments or commitments_file(), now, days, inputs)


def blocks_of(plan, task_id):
    return [block for block in plan["blocks"] if block["task_id"] == task_id]


def write_json(path, content):
    """Write `content` as JSON to `path` (helper of the command-line tests)."""
    with open(path, mode="w", encoding="utf-8") as json_file:
        json.dump(content, json_file)


def read_json(path):
    """Read a JSON file (helper of the command-line tests)."""
    with open(path, mode="r", encoding="utf-8") as json_file:
        return json.load(json_file)


# ----------------------------------------------------------------------------------------------
# plan_schedule.py
# ----------------------------------------------------------------------------------------------

class WeeklyBlocksAcrossDST(unittest.TestCase):
    """The weekday block keeps its local clock times across 2026-11-01 and has real durations."""

    def test_block_intervals_after_the_change(self):
        machine = workstation("ws", blocked_weekdays=True)
        horizon_from = plan_schedule.parse_moment("2026-10-30T12:00:00", ZONE)   # Fri, EDT
        horizon_to = plan_schedule.parse_moment("2026-11-04T12:00:00", ZONE)     # Wed, EST
        intervals = plan_schedule.weekly_block_intervals(machine, horizon_from, horizon_to, ZONE)
        local = [(start.astimezone(ZONE).isoformat(), end.astimezone(ZONE).isoformat()) for start, end, _ in intervals]
        # Friday is clipped to start at the horizon; Monday to Wednesday keep 08:50-18:00 in EST.
        self.assertEqual(local[0], ("2026-10-30T12:00:00-04:00", "2026-10-30T18:00:00-04:00"))
        self.assertEqual(local[1], ("2026-11-02T08:50:00-05:00", "2026-11-02T18:00:00-05:00"))
        self.assertEqual(local[2], ("2026-11-03T08:50:00-05:00", "2026-11-03T18:00:00-05:00"))
        self.assertEqual(len(local), 4)
        # Each full block lasts 9 h 10 min of real time.
        self.assertEqual(plan_schedule.hours_between(intervals[1][0], intervals[1][1]), 9.17)

    def test_free_window_over_the_change_counts_real_hours(self):
        # Fri 18:00 EDT -> Mon 08:50 EST is 62 h 50 min on the wall clock but 63 h 50 min of real time.
        plan, _ = run_plan([], [workstation("ws", blocked_weekdays=True)], None, "2026-10-30T12:00:00", days=4)
        windows = plan["free_windows"]["ws"]
        self.assertEqual(windows[0]["from"], "2026-10-30T18:00:00-04:00")
        self.assertEqual(windows[0]["to"], "2026-11-02T08:50:00-05:00")
        self.assertEqual(windows[0]["hours"], 63.83)


class CanPauseSplitting(unittest.TestCase):
    """A 20 h task on a weekday-blocked machine: nights of 14 h 50 min only (2-day horizon)."""

    def test_cannot_pause_is_unplaced(self):
        plan, _ = run_plan([task("big", 1, hours=20.0, can_pause=False)], [workstation("ws", blocked_weekdays=True)],
                           None, "2026-10-26T17:00:00", days=2)     # Mon 17:00 -> Wed 17:00
        self.assertEqual(len(plan["blocks"]), 0)
        self.assertEqual(plan["unplaced"][0]["task_id"], "big")
        self.assertIn("no room", plan["unplaced"][0]["reason"])

    def test_can_pause_is_split_in_parts(self):
        plan, status_plan = run_plan([task("big", 1, hours=20.0, can_pause=True)], [workstation("ws", blocked_weekdays=True)],
                                     None, "2026-10-26T17:00:00", days=2)
        parts = blocks_of(plan, "big")
        self.assertEqual([part["part"] for part in parts], ["1/2", "2/2"])
        self.assertEqual(parts[0]["start"], "2026-10-26T18:00:00-04:00")
        self.assertEqual(parts[0]["end"], "2026-10-27T08:50:00-04:00")
        self.assertEqual(parts[1]["start"], "2026-10-27T18:00:00-04:00")
        self.assertEqual(round(parts[0]["hours"] + parts[1]["hours"], 2), 20.0)
        # The status plan carries both parts as proposed queued runs with the rank label.
        plan_stage = [stage for stage in status_plan["stages"] if stage["name"] == "PLAN · 1. big"][0]
        self.assertEqual(plan_stage["model"], "ws · GPU")
        self.assertEqual(plan_stage["session"], "ws")
        self.assertEqual(len(plan_stage["runs"]), 2)
        self.assertEqual(plan_stage["runs"][0]["label"], "(1/2)")
        self.assertTrue(plan_stage["runs"][0]["proposed"])
        self.assertEqual(plan_stage["runs"][0]["status"], "queued")

    def test_short_window_is_skipped(self):
        # An exception leaves a 20 min hole before the night: too short for a piece.
        exceptions = [{"from": "2026-10-26T17:00:00-04:00", "to": "2026-10-26T17:40:00-04:00", "block": True, "reason": "x"}]
        machine = workstation("ws", blocked_weekdays=False, exceptions=exceptions)
        machine["exceptions"].append({"from": "2026-10-26T18:00:00-04:00", "to": "2026-10-26T20:00:00-04:00", "block": True,
                                      "reason": "y"})
        plan, _ = run_plan([task("t", 1, hours=5.0, can_pause=True)], [machine], None, "2026-10-26T17:00:00", days=2)
        self.assertEqual(blocks_of(plan, "t")[0]["start"], "2026-10-26T20:00:00-04:00")
        self.assertNotIn("part", blocks_of(plan, "t")[0])


class MeasureBlocks(unittest.TestCase):
    """A task with cost basis none gets a 1 h measure block and waits for measurement."""

    def test_measure_block_for_basis_none(self):
        plan, status_plan = run_plan([task("unknown", 1, hours=None, basis="none")], [workstation("ws")], None,
                                     "2026-10-28T17:00:00")
        block = blocks_of(plan, "unknown")[0]
        self.assertEqual(block["kind"], "measure")
        self.assertEqual(block["hours"], 1.0)
        self.assertEqual(plan["waiting_for_measurement"], [{"task_id": "unknown", "measure_block": 0}])
        run = status_plan["stages"][-1]["runs"][0]
        self.assertEqual(run["label"], "measure 1.0 h")

    def test_accept_estimate_places_production_with_question_mark(self):
        plan, status_plan = run_plan([task("guess", 1, hours=4.0, basis="estimate", accept_estimate=True)],
                                     [workstation("ws")], None, "2026-10-28T17:00:00")
        block = blocks_of(plan, "guess")[0]
        self.assertEqual(block["kind"], "run")
        self.assertIn("estimate, not measured (accept_estimate)", block["warnings"])
        self.assertEqual(status_plan["stages"][-1]["runs"][0]["label"], "4.0 h estimate ?")
        self.assertEqual(status_plan["stages"][-1]["name"], "PLAN · 1. guess")


class HardDeadlinePromotion(unittest.TestCase):
    """Rank 2 with a hard deadline jumps rank 1 only when that makes it meet the deadline."""

    def test_promoted_when_it_then_meets_the_deadline(self):
        # One machine, no pause: rank 1 takes 30 h from now (Wed 17:00 -> Thu 23:00), rank 2 (3 h)
        # with a hard deadline of Thursday would end Fri 02:00 -> promoted to the top.
        tasks = [task("first", 1, hours=30.0), task("urgent", 2, hours=3.0, deadline={"date": "2026-10-29", "hard": True})]
        plan, _ = run_plan(tasks, [workstation("ws")], None, "2026-10-28T17:00:00")
        self.assertEqual(plan["promotions"], [{"task_id": "urgent", "above": ["first"], "reason": "hard deadline 2026-10-29 (tie rule)"}])
        self.assertEqual(blocks_of(plan, "urgent")[0]["start"], "2026-10-28T17:00:00-04:00")
        self.assertTrue(blocks_of(plan, "urgent")[0]["deadline_ok"])
        self.assertEqual(blocks_of(plan, "first")[0]["start"], "2026-10-28T20:00:00-04:00")
        self.assertEqual(plan["deadline_misses"], [])

    def test_not_promoted_when_it_still_misses(self):
        # The urgent task needs 40 h: even at the top it ends Fri 09:00 > Thu 23:59. Order unchanged.
        tasks = [task("first", 1, hours=30.0), task("urgent", 2, hours=40.0, deadline={"date": "2026-10-29", "hard": True})]
        plan, _ = run_plan(tasks, [workstation("ws")], None, "2026-10-28T17:00:00")
        self.assertEqual(plan["promotions"], [])
        self.assertEqual(blocks_of(plan, "first")[0]["start"], "2026-10-28T17:00:00-04:00")
        self.assertEqual(len(plan["deadline_misses"]), 1)
        self.assertEqual(plan["deadline_misses"][0]["task_id"], "urgent")
        self.assertTrue(plan["deadline_misses"][0]["hard"])

    def test_soft_deadline_never_reorders(self):
        tasks = [task("first", 1, hours=30.0), task("soft", 2, hours=3.0, deadline={"date": "2026-10-29", "hard": False})]
        plan, _ = run_plan(tasks, [workstation("ws")], None, "2026-10-28T17:00:00")
        self.assertEqual(plan["promotions"], [])
        self.assertEqual(plan["deadline_misses"][0]["task_id"], "soft")
        self.assertFalse(plan["deadline_misses"][0]["hard"])


class Dependencies(unittest.TestCase):
    """Dependencies on commitments and on other tasks."""

    def test_queued_commitment_without_end_leaves_the_task_unplaced(self):
        stages = [{"name": "Cluster job", "model": "mycluster · H100", "runs": [
            {"name": "cluster_job", "label": "job", "status": "queued", "estimate_start": "2026-10-29T22:00:00"}]}]
        plan, _ = run_plan([task("after", 1, depends_on=["cluster_job"])], [workstation("ws"), cluster()],
                           commitments_file(stages), "2026-10-28T17:00:00")
        self.assertEqual(plan["blocks"], [])
        self.assertEqual(plan["unplaced"], [{"task_id": "after", "reason": "depends on cluster_job, which has no end time"}])
        self.assertTrue(any("after" in decision for decision in plan["decisions"]))

    def test_queued_commitment_with_end_sets_the_earliest_start(self):
        stages = [{"name": "Cluster job", "model": "mycluster · H100", "runs": [
            {"name": "cluster_job", "label": "job", "status": "queued", "estimate_start": "2026-10-29T22:00:00",
             "estimate_end": "2026-10-30T08:00:00"}]}]
        plan, _ = run_plan([task("after", 1, depends_on=["cluster_job"])], [workstation("ws"), cluster()],
                           commitments_file(stages), "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "after")[0]["start"], "2026-10-30T08:00:00-04:00")

    def test_dependency_on_a_task_runs_after_it(self):
        plan, _ = run_plan([task("a", 1, hours=5.0), task("b", 2, hours=2.0, depends_on=["a"])], [workstation("ws")],
                           None, "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "b")[0]["start"], blocks_of(plan, "a")[0]["end"])

    def test_running_commitment_occupies_the_gpu(self):
        stages = [{"name": "scoring", "model": "ws · GPU", "runs": [
            {"name": "score", "label": "s", "status": "running", "start": "2026-10-28T16:00:00",
             "estimate_end": "2026-10-28T20:00:00"}]}]
        plan, _ = run_plan([task("t", 1, hours=2.0)], [workstation("ws")], commitments_file(stages), "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "t")[0]["start"], "2026-10-28T20:00:00-04:00")

    def test_cpu_task_ignores_the_gpu_but_respects_ram_heavy(self):
        stages = [{"name": "scoring", "model": "ws · GPU", "runs": [
            {"name": "score", "label": "s", "status": "running", "start": "2026-10-28T16:00:00",
             "estimate_end": "2026-10-28T20:00:00", "uses": {"gpu": True, "ram_gb": 70}}]}]
        cpu_light = task("light", 1, hours=2.0, gpu=False, ram_gb=10)
        cpu_heavy = task("heavy", 2, hours=2.0, gpu=False, ram_gb=100)
        plan, _ = run_plan([cpu_light, cpu_heavy], [workstation("ws")], commitments_file(stages), "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "light")[0]["start"], "2026-10-28T17:00:00-04:00")   # GPU ignored
        self.assertEqual(blocks_of(plan, "heavy")[0]["start"], "2026-10-28T20:00:00-04:00")   # 70 + 100 > 125, two heavy


class ClusterChoice(unittest.TestCase):
    """Cluster vs workstation: the earlier end wins; budget overruns are warnings."""

    def test_cluster_wins_and_budget_warning(self):
        option = {"allowed": ["mycluster"], "gpu": "h100", "gpus": 4, "walltime_h": 10.0, "basis": "measured"}
        budget = {"unit": "gpu-hours", "used": 100, "allocated": 120, "as_of": "2026-10-14", "source": "test"}
        # Workstation would need 60 h from now; cluster ends 36 + 10 = 46 h from now.
        plan, status_plan = run_plan([task("train", 1, hours=60.0, machines=["ws"], cluster_option=option)],
                                     [workstation("ws"), cluster(budget=budget)], None, "2026-10-28T17:00:00")
        kinds = [(block["kind"], block["machine"]) for block in blocks_of(plan, "train")]
        self.assertEqual(kinds, [("submit", "mycluster"), ("run", "mycluster")])
        submit, run = blocks_of(plan, "train")
        self.assertEqual(submit["start"], "2026-10-28T17:00:00-04:00")
        self.assertEqual(run["start"], "2026-10-30T05:00:00-04:00")
        self.assertEqual(run["end"], "2026-10-30T15:00:00-04:00")
        self.assertEqual(run["alternative"]["machine"], "ws")
        # The warning names the account of the budget ("" when the budget has none).
        self.assertEqual(plan["budget_warnings"], [{"cluster": "mycluster", "task_id": "train", "unit": "gpu-hours",
                                                    "used": 100.0, "allocated": 120.0, "added": 40.0,
                                                    "account": ""}])
        self.assertTrue(plan["decisions"][0].startswith("D1: submit train to mycluster now"))
        cluster_stage = [stage for stage in status_plan["stages"] if stage["name"] == "PLAN · 1. train"][0]
        self.assertEqual(cluster_stage["model"], "mycluster · H100")
        # The submit bar carries both labels; the run bar's label is empty (no overlap in the figure).
        self.assertEqual(cluster_stage["runs"][0]["label"], "submit → start ≈ Fri Oct 30 05:00 · 10.0 h measured")
        self.assertEqual(cluster_stage["runs"][1]["label"], "")

    def test_workstation_wins_when_earlier(self):
        option = {"allowed": ["mycluster"], "gpu": "h100", "gpus": 1, "walltime_h": 10.0, "basis": "measured"}
        plan, _ = run_plan([task("train", 1, hours=5.0, machines=["ws"], cluster_option=option)],
                           [workstation("ws"), cluster()], None, "2026-10-28T17:00:00")
        block = blocks_of(plan, "train")[0]
        self.assertEqual(block["machine"], "ws")
        self.assertEqual(block["alternative"]["machine"], "mycluster")
        self.assertEqual(plan["budget_warnings"], [])

    def test_outage_removes_the_cluster(self):
        option = {"allowed": ["mycluster"], "gpu": "h100", "gpus": 1, "walltime_h": 10.0, "basis": "measured"}
        outages = [{"from": "2026-10-29T00:00:00-04:00", "to": "2026-11-03T00:00:00-05:00", "reason": "maintenance"}]
        plan, _ = run_plan([task("train", 1, hours=60.0, machines=["ws"], cluster_option=option)],
                           [workstation("ws"), cluster(outages=outages)], None, "2026-10-28T17:00:00")
        block = blocks_of(plan, "train")[0]
        self.assertEqual(block["machine"], "ws")
        self.assertTrue(any("outage" in warning for warning in block["warnings"]))


class TieBreaks(unittest.TestCase):
    """Equal ends: first machine in needs.machines, then the smaller GPU."""

    def test_order_then_smaller_gpu(self):
        machines = [workstation("big", gpu_memory=32), workstation("small", gpu_memory=24)]
        plan, _ = run_plan([task("t", 1, hours=2.0, machines=["big", "small"])], machines, None, "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "t")[0]["machine"], "big")
        plan, _ = run_plan([task("t", 1, hours=2.0, machines=[])], machines, None, "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "t")[0]["machine"], "small")


class GpuCardsAndLanes(unittest.TestCase):
    """Several GPU cards, lane matching by name, free windows of a CPU-only machine."""

    def test_two_cards_run_two_gpu_tasks_at_once(self):
        tasks = [task("a", 1, hours=10.0), task("b", 2, hours=10.0)]
        # One card: b waits for a.
        plan, _ = run_plan(tasks, [workstation("ws", gpu_count=1)], None, "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "b")[0]["start"], blocks_of(plan, "a")[0]["end"])
        # Two cards: both start now (GPU memory 10 + 10 <= 2 × 24, RAM 20 + 20 <= 125).
        plan, _ = run_plan(tasks, [workstation("ws", gpu_count=2)], None, "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "a")[0]["start"], "2026-10-28T17:00:00-04:00")
        self.assertEqual(blocks_of(plan, "b")[0]["start"], "2026-10-28T17:00:00-04:00")

    def test_commitment_on_two_cards_blocks_both(self):
        stages = [{"name": "training", "model": "ws · GPU", "runs": [
            {"name": "train", "label": "t", "status": "running", "start": "2026-10-28T16:00:00",
             "estimate_end": "2026-10-28T20:00:00", "uses": {"gpu": True, "gpu_cards": 2}}]}]
        plan, _ = run_plan([task("t", 1, hours=2.0)], [workstation("ws", gpu_count=2)], commitments_file(stages),
                           "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "t")[0]["start"], "2026-10-28T20:00:00-04:00")

    def test_commitment_matches_the_machine_name(self):
        # The stage names the machine by its name ("ws"), not by its lane label ("ws · GPU").
        stages = [{"name": "scoring", "model": "ws", "runs": [
            {"name": "score", "label": "s", "status": "running", "start": "2026-10-28T16:00:00",
             "estimate_end": "2026-10-28T20:00:00"}]}]
        plan, _ = run_plan([task("t", 1, hours=2.0)], [workstation("ws")], commitments_file(stages), "2026-10-28T17:00:00")
        self.assertEqual(blocks_of(plan, "t")[0]["start"], "2026-10-28T20:00:00-04:00")

    def test_cpu_only_machine_reports_free_windows(self):
        # A CPU-only machine: a CPU task is placed on it, and its free windows are the times that
        # no block or exception holds (here an exception until 20:00); `gpu_free` is false there.
        machine = cpu_workstation("box")
        machine["exceptions"] = [{"from": "2026-10-28T17:00:00-04:00", "to": "2026-10-28T20:00:00-04:00",
                                  "block": True, "reason": "owner's own work"}]
        plan, _ = run_plan([task("cpu_job", 1, hours=3.0, gpu=False, gpu_memory_gb=None)], [machine],
                           None, "2026-10-28T17:00:00", days=1)
        self.assertEqual(blocks_of(plan, "cpu_job")[0]["machine"], "box")
        self.assertEqual(blocks_of(plan, "cpu_job")[0]["start"], "2026-10-28T20:00:00-04:00")
        windows = plan["free_windows"]["box"]
        self.assertEqual(windows[0]["from"], "2026-10-28T20:00:00-04:00")
        self.assertFalse(windows[0]["gpu_free"])


class CpuClusterPreference(unittest.TestCase):
    """prefer_cluster_for_cpu sends CPU work to that cluster unless it would miss the deadline."""

    def cpu_task(self, deadline=None):
        option = {"allowed": ["mycluster"], "gpu": "cpu", "gpus": 16, "walltime_h": 12.0, "basis": "measured"}
        return task("prep", 1, hours=10.0, gpu=False, gpu_memory_gb=None, machines=["ws"], cluster_option=option,
                    deadline=deadline)

    def test_without_preference_the_earlier_end_wins(self):
        plan, _ = run_plan([self.cpu_task()], [workstation("ws"), cluster(cpu_wait=2.0)], None, "2026-10-28T08:00:00")
        self.assertEqual(blocks_of(plan, "prep")[0]["machine"], "ws")

    def test_preference_sends_cpu_work_to_the_cluster(self):
        plan, _ = run_plan([self.cpu_task()], [workstation("ws"), cluster(cpu_wait=2.0)], None, "2026-10-28T08:00:00",
                           prefer_cluster_for_cpu="mycluster")
        kinds = [(block["kind"], block["machine"]) for block in blocks_of(plan, "prep")]
        self.assertEqual(kinds, [("submit", "mycluster"), ("run", "mycluster")])
        self.assertIn("prefer_cluster_for_cpu", blocks_of(plan, "prep")[1]["reason"])
        self.assertEqual(blocks_of(plan, "prep")[1]["alternative"]["machine"], "ws")

    def test_preference_yields_to_the_deadline(self):
        # Cluster: 10 h wait + 12 h = Thu 06:00, after the deadline (Wed 23:59); ws ends Wed 18:00.
        deadline = {"date": "2026-10-28", "hard": False}
        plan, _ = run_plan([self.cpu_task(deadline)], [workstation("ws"), cluster(cpu_wait=10.0)], None,
                           "2026-10-28T08:00:00", prefer_cluster_for_cpu="mycluster")
        block = blocks_of(plan, "prep")[0]
        self.assertEqual(block["machine"], "ws")
        self.assertTrue(any("preferred for CPU work" in warning for warning in block["warnings"]))


class BudgetAppliesTo(unittest.TestCase):
    """A budget with applies_to counts only jobs of those resource types."""

    def test_cpu_job_does_not_count_against_a_gpu_budget(self):
        budget = {"unit": "gpu-hours", "used": 100, "allocated": 101, "applies_to": ["h100"], "account": "def-example_gpu"}
        cpu_option = {"allowed": ["mycluster"], "gpu": "cpu", "gpus": 16, "walltime_h": 12.0, "basis": "measured"}
        gpu_option = {"allowed": ["mycluster"], "gpu": "h100", "gpus": 1, "walltime_h": 12.0, "basis": "measured"}
        tasks = [task("cpu_job", 1, hours=50.0, gpu=False, gpu_memory_gb=None, machines=["ws"], cluster_option=cpu_option),
                 task("gpu_job", 2, hours=80.0, machines=["ws"], cluster_option=gpu_option)]
        plan, _ = run_plan(tasks, [workstation("ws"), cluster(budget=budget, cpu_wait=2.0)], None, "2026-10-28T17:00:00")
        warned = [warning["task_id"] for warning in plan["budget_warnings"]]
        self.assertEqual(warned, ["gpu_job"])
        self.assertEqual(plan["budget_warnings"][0]["account"], "def-example_gpu")


class CommandLine(unittest.TestCase):
    """The CLI writes the three files, refuses to overwrite without --overwrite, and --dry-run writes nothing."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="planner_test_")
        self.paths = {}
        for name, content in (("tasks", tasks_file([task("t", 1, hours=2.0)])),
                              ("machines", machines_file([workstation("ws")])),
                              ("commitments", commitments_file())):
            path = os.path.join(self.folder, name + ".json")
            write_json(path, content)
            self.paths[name] = path

    def tearDown(self):
        shutil.rmtree(self.folder)

    def run_cli(self, *extra):
        command = [sys.executable, PLAN_SCRIPT, "--tasks", self.paths["tasks"], "--machines", self.paths["machines"],
                   "--commitments", self.paths["commitments"], "-o", os.path.join(self.folder, "out"),
                   "--now", "2026-10-28T17:00:00-04:00"] + list(extra)
        return subprocess.run(command, capture_output=True, text=True)

    def test_write_then_refuse_then_overwrite(self):
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        for name in ("plan.json", "plan.md", "status_plan.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.folder, "out", name)))
        second = self.run_cli()
        self.assertEqual(second.returncode, 1)
        self.assertIn("--overwrite", second.stderr)
        third = self.run_cli("--overwrite", "--print")
        self.assertEqual(third.returncode, 0)
        self.assertIn("## Decisions", third.stdout)
        plan = read_json(os.path.join(self.folder, "out", "plan.json"))
        self.assertEqual(plan["schema"], "compute-plan/1")
        self.assertEqual(plan["blocks"][0]["task_id"], "t")

    def test_dry_run_writes_nothing(self):
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## Decisions", result.stdout)
        self.assertIn("dry run, nothing written", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "out")))

    def test_bad_input_exits_1(self):
        with open(self.paths["tasks"], "w", encoding="utf-8") as json_file:
            json_file.write("{not json")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not valid JSON", result.stderr)

    def test_preferred_cluster_must_be_in_the_file(self):
        write_json(self.paths["machines"], machines_file([workstation("ws")], prefer_cluster_for_cpu="nowhere"))
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("prefer_cluster_for_cpu", result.stderr)


# ----------------------------------------------------------------------------------------------
# task_file.py
# ----------------------------------------------------------------------------------------------

class TaskFileRoundTrips(unittest.TestCase):
    """add / order / rank / measured / set / state through the command line."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="taskfile_test_")
        self.path = os.path.join(self.folder, "compute_tasks.json")

    def tearDown(self):
        shutil.rmtree(self.folder)

    def run_cli(self, *arguments):
        command = [sys.executable, TASK_SCRIPT, "--file", self.path] + list(arguments)
        return subprocess.run(command, capture_output=True, text=True)

    def read(self):
        return read_json(self.path)

    def card(self, task_id, rank=None):
        return json.dumps({"id": task_id, "title": task_id, "project": "p", "rank": rank,
                           "needs": {"gpu": True, "gpu_memory_gb": 8, "ram_gb": 16, "threads": 4, "machines": []},
                           "cost": {"hours": 2.0, "basis": "measured", "source": "s"}})

    def ranks(self):
        return {task["id"]: task["rank"] for task in self.read()["tasks"]}

    def test_add_order_rank_measured_set_state(self):
        for task_id in ("a", "b", "c"):
            result = self.run_cli("add", "--json", self.card(task_id))
            self.assertEqual(result.returncode, 0, result.stderr)
        content = self.read()
        self.assertEqual(content["schema"], "compute-tasks/1")
        self.assertIsNotNone(content["updated"])
        self.assertEqual(content["tasks"][0]["state"], "pending")
        self.assertEqual(content["tasks"][0]["can_pause"], False)
        self.assertEqual(content["tasks"][0]["history"][0]["event"], "added")

        # order: c first, a second, b unranked.
        self.assertEqual(self.run_cli("order", "c,a").returncode, 0)
        self.assertEqual(self.ranks(), {"a": 2, "b": None, "c": 1})

        # rank: b inserted at 1 -> b 1, c 2, a 3.
        self.assertEqual(self.run_cli("rank", "b", "1").returncode, 0)
        self.assertEqual(self.ranks(), {"a": 3, "b": 1, "c": 2})

        # rank beyond the end puts the task last.
        self.assertEqual(self.run_cli("rank", "b", "9").returncode, 0)
        self.assertEqual(self.ranks(), {"a": 2, "b": 3, "c": 1})

        # measured: cost fields and a history event.
        result = self.run_cli("measured", "a", "--hours", "13.5", "--source", "run log of the last batch", "--on", "ws02")
        self.assertEqual(result.returncode, 0, result.stderr)
        card = [task for task in self.read()["tasks"] if task["id"] == "a"][0]
        self.assertEqual(card["cost"]["hours"], 13.5)
        self.assertEqual(card["cost"]["basis"], "measured")
        self.assertEqual(card["cost"]["measured_on"], "ws02")
        self.assertIn("measured 13.50 h on ws02", card["history"][-1]["event"])

        # set with JSON-typed values and a dotted path into a null deadline.
        self.assertEqual(self.run_cli("set", "a", "deadline.date=2026-11-05").returncode, 0)
        self.assertEqual(self.run_cli("set", "a", "deadline.hard=true").returncode, 0)
        self.assertEqual(self.run_cli("set", "a", "needs.machines=[\"ws02\", \"ws03\"]").returncode, 0)
        self.assertEqual(self.run_cli("set", "a", "cost.source=run log: 18.6 tasks/h").returncode, 0)
        card = [task for task in self.read()["tasks"] if task["id"] == "a"][0]
        self.assertEqual(card["deadline"], {"date": "2026-11-05", "hard": True})
        self.assertEqual(card["needs"]["machines"], ["ws02", "ws03"])
        self.assertEqual(card["cost"]["source"], "run log: 18.6 tasks/h")

        # state.
        self.assertEqual(self.run_cli("state", "c", "done").returncode, 0)
        card = [task for task in self.read()["tasks"] if task["id"] == "c"][0]
        self.assertEqual(card["state"], "done")
        self.assertEqual(card["history"][-1]["event"], "state pending -> done")

        # list prints every task.
        listed = self.run_cli("list")
        self.assertEqual(listed.returncode, 0)
        self.assertIn("3 tasks", listed.stdout)

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_cli("add", "--json", self.card("a")).returncode, 0)
        before = self.read()
        result = self.run_cli("--dry-run", "state", "a", "approved")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry run", result.stdout)
        self.assertIn('"approved"', result.stdout)
        self.assertEqual(self.read(), before)

    def test_validation_errors_exit_1(self):
        bad_id = self.run_cli("add", "--json", json.dumps({"id": "Bad-Id", "title": "x", "needs": {"gpu": True}}))
        self.assertEqual(bad_id.returncode, 1)
        self.assertIn("[a-z0-9_]+", bad_id.stderr)
        self.assertEqual(self.run_cli("add", "--json", self.card("a")).returncode, 0)
        duplicate = self.run_cli("add", "--json", self.card("a"))
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("already exists", duplicate.stderr)
        bad_state = self.run_cli("set", "a", "state=flying")
        self.assertEqual(bad_state.returncode, 1)
        unknown = self.run_cli("rank", "zzz", "1")
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("unknown task id", unknown.stderr)
        # The file is unchanged after a refused command.
        self.assertEqual(len(self.read()["tasks"]), 1)


class TaskFileSettings(unittest.TestCase):
    """The default tasks file and the time zone come from the settings."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="taskfile_settings_test_")

    def tearDown(self):
        shutil.rmtree(self.folder)
        shutil.rmtree(TEST_PLANNER_DIR, ignore_errors=True)

    def test_zone_of_the_module_is_the_owner_zone(self):
        self.assertEqual(task_file.TIMEZONE.key, "America/New_York")

    def test_default_file_is_in_planner_dir(self):
        card = json.dumps({"id": "a", "title": "a", "needs": {"gpu": False}, "cost": {"hours": 1, "basis": "measured"}})
        result = subprocess.run([sys.executable, TASK_SCRIPT, "add", "--json", card], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isfile(os.path.join(TEST_PLANNER_DIR, "compute_tasks.json")))

    def test_environment_variable_wins_and_zone_follows_the_settings(self):
        # A second settings file with a zone without daylight saving (+09:00 all year).
        settings_path = os.path.join(self.folder, "config.json")
        write_json(settings_path, {"owner": {"time_zone": "Asia/Tokyo"}})
        environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=settings_path, COMPUTE_PLANNER_DIR=self.folder)
        card = json.dumps({"id": "a", "title": "a", "needs": {"gpu": False}, "cost": {"hours": 1, "basis": "measured"}})
        result = subprocess.run([sys.executable, TASK_SCRIPT, "add", "--json", card], capture_output=True, text=True,
                                env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        content = read_json(os.path.join(self.folder, "compute_tasks.json"))
        self.assertTrue(content["updated"].endswith("+09:00"), content["updated"])

    def test_no_settings_and_no_file_is_an_error(self):
        environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=os.path.join(self.folder, "missing.json"))
        result = subprocess.run([sys.executable, TASK_SCRIPT, "list"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("set up my work hub", result.stderr)


# ----------------------------------------------------------------------------------------------
# planner_settings.py
# ----------------------------------------------------------------------------------------------

def settings_with(workstations=None, clusters=None, planner=None, zone="America/New_York"):
    """A settings dict for build_machines (no file needed)."""
    return {"owner": {"time_zone": zone},
            "machines": {"workstations": workstations or [], "clusters": clusters or []},
            "skill_settings": {"compute-planner": planner or {}}}


class UsageWindowDays(unittest.TestCase):
    """parse_days accepts lists, ranges, wrapping ranges, comma lists and day groups."""

    def test_accepted_forms(self):
        parse = planner_settings.parse_days
        self.assertEqual(parse("Mon-Fri", "w"), ["Mon", "Tue", "Wed", "Thu", "Fri"])
        self.assertEqual(parse("Fri-Mon", "w"), ["Mon", "Fri", "Sat", "Sun"])
        self.assertEqual(parse("mon,Wed, friday", "w"), ["Mon", "Wed", "Fri"])
        self.assertEqual(parse(["Sat", "Sun"], "w"), ["Sat", "Sun"])
        self.assertEqual(parse("weekends", "w"), ["Sat", "Sun"])
        self.assertEqual(parse("daily", "w"), ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])

    def test_refused_forms(self):
        for bad in ("", "Someday", "Mon-Wed-Fri"):
            with self.assertRaises(planner_settings.PlannerSettingsError):
                planner_settings.parse_days(bad, "w")


class MachinesFromSettings(unittest.TestCase):
    """build_machines joins the settings and the facts into the plan_schedule input."""

    def test_usage_window_becomes_a_blocked_period(self):
        machine = {"name": "ws02", "cpus": 24, "ram_gb": 64, "gpus": [{"model": "RTX 3060", "memory_gb": 12}],
                   "usage_windows": [{"days": "Mon-Fri", "from": "09:00", "to": "17:00", "rule": "blocked",
                                      "reason": "shared in the day"}]}
        built = planner_settings.build_machines(settings_with([machine]), planner_settings.read_facts(None), "S", "")
        entry = built["machines"][0]
        self.assertEqual(entry["label"], "ws02 · RTX 3060")
        self.assertEqual(entry["gpu"], {"model": "RTX 3060", "memory_gb": 12.0, "count": 1})
        self.assertEqual(entry["threads"], 24.0)
        self.assertEqual(entry["blocked"], [{"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "from": "09:00",
                                             "to": "17:00", "reason": "shared in the day"}])
        # The built entry passes plan_schedule's own check of a machine (it raises when a field is wrong).
        plan_schedule.validate_machine(entry, 0)

    def test_unknown_rule_word_is_refused(self):
        machine = {"name": "ws02", "gpus": [], "usage_windows": [{"days": "Mon-Fri", "from": "09:00", "to": "17:00",
                                                                   "rule": "nice"}]}
        with self.assertRaises(planner_settings.PlannerSettingsError) as caught:
            planner_settings.build_machines(settings_with([machine]), planner_settings.read_facts(None), "S", "")
        self.assertIn("nice", str(caught.exception))

    def test_gpu_lists(self):
        two_cards = {"name": "ws03", "gpus": [{"model": "A100", "memory_gb": 40, "count": 2}]}
        gpu, notes = planner_settings.gpu_summary(two_cards)
        self.assertEqual(gpu, {"model": "A100", "memory_gb": 40.0, "count": 2})
        self.assertEqual(notes, [])
        self.assertEqual(planner_settings.lane_label("ws03", gpu), "ws03 · 2× A100")
        mixed = {"name": "ws04", "gpus": [{"model": "A", "memory_gb": 24}, {"model": "B", "memory_gb": 12}]}
        gpu, notes = planner_settings.gpu_summary(mixed)
        self.assertEqual(gpu, {"model": "A + B", "memory_gb": 12.0, "count": 2})
        self.assertEqual(len(notes), 1)
        gpu, notes = planner_settings.gpu_summary({"name": "ws05", "gpus": []})
        self.assertIsNone(gpu)
        self.assertEqual(planner_settings.lane_label("ws05", gpu), "ws05 · CPU")

    def test_facts_budget_and_warnings(self):
        workstations = [{"name": "ws02", "gpus": [{"model": "G", "memory_gb": 12}], "usage_windows": []}]
        clusters = [{"name": "mycluster", "scheduler": "slurm", "slurm_accounts": ["def-example_gpu"]},
                    {"name": "othercluster", "scheduler": "slurm"}]
        planner = {"shared_machines": ["ws02", "ws09"], "shared_cpu_percent": 25, "prefer_cluster_for_cpu": "nowhere",
                   "cluster_budgets": {"mycluster": {"unit": "gpu-hours", "allocated": 100, "applies_to": ["h100"],
                                                     "account": "def-example_gpu"},
                                       "othercluster": {"allocated": 50}}}
        facts = {"schema": "compute-machine-facts/1",
                 "workstations": {"ws02": {"exceptions": [{"from": "2030-01-01T00:00:00+00:00",
                                                           "to": "2030-01-02T00:00:00+00:00", "block": True,
                                                           "reason": "x"}]},
                                  "ghost": {}},
                 "clusters": {"mycluster": {"budget_used": {"used": 40, "as_of": "2030-01-01", "source": "report"},
                                            "max_walltime_h": 72}}}
        built = planner_settings.build_machines(settings_with(workstations, clusters, planner), facts, "S", "F")
        by_name = {entry["name"]: entry for entry in built["machines"]}
        self.assertEqual(len(by_name["ws02"]["exceptions"]), 1)
        self.assertTrue(by_name["ws02"]["shared"])
        self.assertTrue(any("25 %" in rule for rule in by_name["ws02"]["rules"]))
        self.assertEqual(by_name["mycluster"]["budget"]["used"], 40.0)
        self.assertEqual(by_name["mycluster"]["budget"]["applies_to"], ["h100"])
        self.assertEqual(by_name["mycluster"]["accounts"], ["def-example_gpu"])
        self.assertEqual(by_name["mycluster"]["max_walltime_h"], 72.0)
        self.assertEqual(built["prefer_cluster_for_cpu"], "")
        warnings = " | ".join(built["warnings"])
        self.assertIn("ghost", warnings)
        self.assertIn("ws09", warnings)
        self.assertIn("nowhere", warnings)
        self.assertIn("othercluster", warnings)

    def test_duplicate_names_are_refused(self):
        with self.assertRaises(planner_settings.PlannerSettingsError):
            planner_settings.build_machines(settings_with([{"name": "x", "gpus": []}], [{"name": "x"}]),
                                            planner_settings.read_facts(None), "S", "")

    def test_wrong_option_type_is_refused(self):
        with self.assertRaises(planner_settings.PlannerSettingsError):
            planner_settings.resolved_settings(settings_with(planner={"horizon_days": "seven"}))


class PlannerSettingsCommandLine(unittest.TestCase):
    """get / stamp / show / machines (with --dry-run and --overwrite) on the temporary settings."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="planner_settings_cli_")

    def tearDown(self):
        shutil.rmtree(self.folder)

    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, SETTINGS_SCRIPT] + list(arguments), capture_output=True, text=True)

    def test_get_stamp_show(self):
        self.assertEqual(self.run_cli("get", "planner_dir").stdout.strip(), TEST_PLANNER_DIR)
        self.assertEqual(self.run_cli("get", "horizon_days").stdout.strip(), "5")
        self.assertEqual(self.run_cli("get", "time_zone").stdout.strip(), "America/New_York")
        self.assertEqual(self.run_cli("get", "orchestrator_enabled").stdout.strip(), "false")
        self.assertEqual(self.run_cli("get", "skills_root").stdout.strip(), os.path.dirname(SKILL_DIR))
        self.assertEqual(self.run_cli("get", "no_such_key").returncode, 1)
        # `re.fullmatch` checks the whole text: 4 digits, -, 2, -, 2, _, 4 digits (YYYY-MM-DD_HHMM).
        self.assertIsNotNone(re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}", self.run_cli("stamp").stdout.strip()))
        shown = json.loads(self.run_cli("show").stdout)
        self.assertEqual(shown["shared_cpu_percent"], 30)
        self.assertEqual(shown["cluster_budgets"], {})

    def test_machines_dry_run_refuse_overwrite(self):
        output = os.path.join(self.folder, "machines.json")
        dry = self.run_cli("machines", "-o", output, "--dry-run")
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertFalse(os.path.exists(output))
        self.assertIn("compute-machines/1", dry.stdout)
        first = self.run_cli("machines", "-o", output)
        self.assertEqual(first.returncode, 0, first.stderr)
        built = read_json(output)
        self.assertEqual([entry["name"] for entry in built["machines"]], ["ws01", "mycluster"])
        second = self.run_cli("machines", "-o", output)
        self.assertEqual(second.returncode, 1)
        self.assertIn("--overwrite", second.stderr)
        self.assertEqual(self.run_cli("machines", "-o", output, "--overwrite").returncode, 0)

    def test_missing_settings_file(self):
        environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=os.path.join(self.folder, "missing.json"))
        result = subprocess.run([sys.executable, SETTINGS_SCRIPT, "show"], capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 1)
        self.assertIn("set up my work hub", result.stderr)


# ----------------------------------------------------------------------------------------------
# The example in assets/, end to end
# ----------------------------------------------------------------------------------------------

class ExampleEndToEnd(unittest.TestCase):
    """settings + facts -> machines.json -> plan, on the fictional example (ws01, ws02, mycluster)."""

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="planner_example_")
        self.environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=os.path.join(ASSETS_DIR, "example_settings.json"))

    def tearDown(self):
        shutil.rmtree(self.folder)

    def test_example_chain(self):
        # 1. The machines file built from the example settings and facts equals the shipped example
        #    (apart from the build time and the input paths).
        machines_path = os.path.join(self.folder, "machines.json")
        built = subprocess.run([sys.executable, SETTINGS_SCRIPT, "machines", "--facts",
                                os.path.join(ASSETS_DIR, "example_machine_facts.json"), "-o", machines_path],
                               capture_output=True, text=True, env=self.environment)
        self.assertEqual(built.returncode, 0, built.stderr)
        fresh = read_json(machines_path)
        shipped = read_json(os.path.join(ASSETS_DIR, "example_machines.json"))
        for content in (fresh, shipped):
            content.pop("updated")
            content.pop("built_from")
        self.assertEqual(fresh, shipped)

        # 2. The plan at Wed 2030-10-30 17:00.
        output = os.path.join(self.folder, "plan")
        planned = subprocess.run([sys.executable, PLAN_SCRIPT, "--tasks", os.path.join(ASSETS_DIR, "example_tasks.json"),
                                  "--machines", machines_path,
                                  "--commitments", os.path.join(ASSETS_DIR, "example_commitments.json"),
                                  "-o", output, "--now", "2030-10-30T17:00:00"],
                                 capture_output=True, text=True, env=self.environment)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        plan = read_json(os.path.join(output, "plan.json"))
        machine_of = {}
        for block in plan["blocks"]:
            machine_of.setdefault(block["task_id"], set()).add((block["kind"], block["machine"]))
        # CPU work goes to mycluster (prefer_cluster_for_cpu) although ws01 would be free.
        self.assertEqual(machine_of["dataset_b_preprocess"], {("submit", "mycluster"), ("run", "mycluster")})
        # The unmeasured task gets a measure block only.
        self.assertEqual(machine_of["denoise_batch"], {("measure", "ws01")})
        self.assertEqual([entry["task_id"] for entry in plan["waiting_for_measurement"]], ["denoise_batch"])
        # The inference waits for the cluster training (ends Sun 2030-11-03 08:00 EST, after the clock change).
        inference = blocks_of(plan, "test_set_inference")[0]
        self.assertEqual(inference["start"], "2030-11-03T08:00:00-05:00")
        # The 4-GPU pilot overruns the GPU budget of def-example_gpu; the CPU job does not count.
        self.assertEqual([(warning["task_id"], warning["account"]) for warning in plan["budget_warnings"]],
                         [("model_d_pilot", "def-example_gpu")])
        # The hard deadline is met without a promotion; unranked and done cards are never placed.
        self.assertTrue(blocks_of(plan, "project_c_maps")[0]["deadline_ok"])
        self.assertEqual(plan["deadline_misses"], [])
        self.assertEqual(blocks_of(plan, "archive_check"), [])
        self.assertEqual(blocks_of(plan, "template_build"), [])


if __name__ == "__main__":
    unittest.main()
