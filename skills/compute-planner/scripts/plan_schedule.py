#!/usr/bin/env python3
"""
plan_schedule.py - propose where and when the pending compute tasks run during the next days.

PURPOSE
    The compute-planner skill asks the user which compute tasks exist and in which order they
    want them (compute_tasks.json), knows their usage rules per machine (machines.json, built
    from the workhub-claude settings by planner_settings.py) and reads what is already running
    or booked from the latest run-status timeline (the commitments file). This script turns
    those three files into one deterministic proposal: a list of time blocks ("run task X on
    ws02 from Thu 18:00 to Fri 08:00"), the tasks that could not be placed and why, the
    deadlines that will be missed, the cluster budget overruns, the free GPU windows that
    remain, and the numbered yes/no decisions the user answers in chat. It never starts
    anything and never reads a live system: every fact enters through the three input files
    (references/planning_rules.md §6).

INPUTS (all JSON, formats in references/formats.md)
    --tasks        compute_tasks.json, schema "compute-tasks/1": one card per compute task with
                   rank, state, deadline, dependencies, needs (GPU, memory, threads, allowed
                   machines), cost (hours + basis: measured | scaled | estimate | none), the
                   measurement slot, can_pause, and an optional cluster option.
    --machines     machines.json, schema "compute-machines/1", written by
                   `planner_settings.py machines`: the workstations (GPU model, memory and card
                   count, RAM, threads, ram_heavy_gb, weekly blocked periods, one-off exceptions)
                   and the clusters (GPU types, measured queue waits, max walltime, outages,
                   budget), the IANA time zone of the local times ("Europe/Lisbon"), and the
                   optional `prefer_cluster_for_cpu`.
    --commitments  a run-status JSON (schema "run-status/1", format in
                   run-status-figure/references/status_schema.md): every run with status
                   `running` or `queued` whose stage `model` equals a machine `label` (or its
                   `name`) is a commitment on that machine, with the optional run field `uses`.

OUTPUTS (in -o OUTDIR, created if missing; existing files are replaced only with --overwrite;
         with --dry-run nothing is written and plan.md is printed)
    plan.json         schema "compute-plan/1": blocks, unplaced, waiting_for_measurement,
                      deadline_misses, promotions, budget_warnings, free_windows, decisions.
    plan.md           the same facts as a short Markdown page for the user.
    status_plan.json  the commitments file with the proposed blocks appended as `proposed`
                      runs, one stage "PLAN · <rank>. <title>" per task and machine, ready for
                      run-status-figure/scripts/render_status.py (drawn hatched).

USAGE
    python3 plan_schedule.py --tasks T.json --machines M.json --commitments C.json -o OUT
    python3 plan_schedule.py --tasks T.json --machines M.json --commitments C.json -o OUT \\
        --now 2030-05-06T17:00:00-04:00 --days 7 --overwrite --print
    python3 plan_schedule.py --tasks T.json --machines M.json --commitments C.json -o OUT --dry-run

ALGORITHM (references/planning_rules.md, applied in this order)
    §0  now = --now or the current time in machines.timezone; horizon = now → now + --days
        (the skill passes skill_settings.compute-planner.horizon_days, default 7).
        Tasks considered: state pending or approved AND rank set, in rank order.
    §1  Per workstation, a busy timeline of "occupants": the weekly blocked periods (built day
        by day in local time, so the DST change is handled by the zone rules, never by adding
        24 h), the one-off exceptions (block true; block false openings cancel the blocks they
        overlap), and the commitments with their `uses`. A task fits at a moment when no
        occupant blocks everything, a GPU card is free for a GPU task (a machine has
        `gpu.count` cards, default 1; each GPU task takes one), and GPU memory, RAM and threads
        of everything overlapping stay within the machine's capacity; a task at or above
        `ram_heavy_gb` never overlaps another such job.
    §2  Greedy placement in rank order: earliest start = max(now, not_before, end of every
        dependency); duration = cost.per_machine[m] or cost.hours; measured or scaled cost →
        the production block, otherwise a short measure block (unless accept_estimate);
        candidates = allowed machines with enough capacity (+ clusters); fit = earliest moment
        where the block fits (can_pause false: inside one free window; true: accumulated over
        windows, pieces under 30 min skipped); choice = earliest end, then the order of
        needs.machines, then the smaller GPU, except that a CPU-only task goes to the cluster
        named by machines.prefer_cluster_for_cpu whenever that cluster is a candidate and meets
        the task's deadline; the placed block consumes resources.
    §3  Cluster candidates: start = earliest start + measured queue wait, end = start +
        walltime; removed by an overlapping outage or a walltime above the maximum; a budget
        overrun is a warning, not a removal (a budget with `applies_to` counts only jobs of
        those resource types).
    §4  Hard deadlines: a task that would miss its hard deadline is moved to the top of the
        order and the greedy pass is repeated; the new order is kept only when the task then
        meets the deadline. One task at a time, in rank order. Soft misses are only reported.
    §5  Decisions: one per block (or group of parts) and one per unplaced task the user could
        unblock.

DEPENDENCIES
    Python >= 3.9 (`zoneinfo`), standard library only. No PyYAML, no third-party package.
    workhub_config.py (same folder) gives the system name written in front of paths.

ASSUMPTIONS (choices where the rules left room; each is also noted where it is applied)
    - Times without a UTC offset (the run-status files write naive local times) are read in
      machines.timezone.
    - An opening (exception with block false) cancels both the weekly blocks and the block-true
      exceptions it overlaps: it is the user's explicit, later statement about that moment.
    - The "one job at or above ram_heavy_gb" rule is applied to every task, GPU or CPU: it is a
      RAM rule, and applying it to GPU tasks too is the conservative reading.
    - When a task depends on a lower-ranked task, the dependency is moved ahead of it (stable
      topological order) and the move is noted; a dependency can never run after the task.
    - A cluster option is trusted when `cluster.basis` (or, when absent, `cost.basis`) is
      measured or scaled; a workstation option when `cost.basis` is. When neither is trusted and
      accept_estimate is false, a measure block is placed on a workstation.
    - Cluster blocks do not consume workstation resources, and a cluster has no capacity model:
      several cluster jobs may be proposed on the same cluster.
    - `free_windows` lists the intervals with at least one GPU card free (a CPU-only machine:
      the free intervals) that remain AFTER the proposed blocks.
    - A commitment belongs to a machine when its stage `model` equals the machine's `label`
      ("ws01 · RTX 4090") or its plain `name` ("ws01").

EXIT STATUS
    0 = success (a plan with unplaced tasks is still a success);
    1 = an input file is missing or malformed, or an output file exists without --overwrite;
    2 = invalid command-line arguments (reported by argparse).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library)
# ----------------------------------------------------------------------------------------------

# `argparse` turns the words typed after the script name into a Namespace object; it also
# prints --help and exits with status 2 on invalid input.
import argparse

# `copy.deepcopy(x)` makes an independent copy of nested lists and dictionaries, so that one
# greedy pass can add occupants without changing the base timeline used by the next pass.
import copy

# `datetime.datetime` is one moment (date + clock time + UTC offset); `datetime.timedelta` is a
# duration; `datetime.date` is a calendar day.
import datetime

# `json` reads the three input files and writes plan.json and status_plan.json.
import json

# `os` gives environment variables, path helpers and `os.makedirs`.
import os

# `sys` gives `sys.exit(status)`, `sys.stderr` and `sys.path` (see below).
import sys

# `ZoneInfo("Europe/Lisbon")` is a time-zone object with that zone's daylight-saving rules.
from zoneinfo import ZoneInfo

# workhub_config.py (the kit's settings reader) sits in this script's folder. Python adds the
# folder of the script being RUN to sys.path, but not that of a script loaded by a test through
# importlib, so the folder is added explicitly before the import.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# `load_config(required=False)` returns the settings, or {} when the kit is not set up;
# `system_name(config)` returns this machine's name as written in front of paths ("ws01").
from workhub_config import load_config, system_name as settings_system_name  # noqa: E402


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# Schema names checked on the input files and written on the output.
TASKS_SCHEMA = "compute-tasks/1"
MACHINES_SCHEMA = "compute-machines/1"
COMMITMENTS_SCHEMA = "run-status/1"
PLAN_SCHEMA = "compute-plan/1"

# The time zone when machines.json names none (planner_settings.py always writes owner.time_zone).
DEFAULT_TIMEZONE_NAME = "UTC"

# Every moment inside the planner is kept in UTC (see parse_moment for the reason).
UTC = datetime.timezone.utc

# Only these task states are placed (planning_rules.md §0).
PLACEABLE_STATES = ("pending", "approved")

# Only these cost bases are trusted for a production block (planning_rules.md §2.3).
TRUSTED_BASES = ("measured", "scaled")

# Commitments with these statuses occupy a machine; the others (done, failed, stopped) do not.
ACTIVE_RUN_STATUSES = ("running", "queued")

# A piece of a pausable task shorter than this is skipped: not worth a restart (§2.5).
MIN_PIECE = datetime.timedelta(minutes=30)

# Default length of a measure block when the card gives none (formats.md §1).
DEFAULT_MEASURE_HOURS = 1.0

# Weekday names used in machines.json `blocked[].days`, in Python's weekday() order (Mon = 0).
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Prefix of the stages that receive the proposed runs in status_plan.json ("PLAN · <rank>. <title>", one per
# task and machine lane).
PLAN_STAGE_PREFIX = "PLAN · "


# ----------------------------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------------------------

class PlannerInputError(Exception):
    """An input file is missing, malformed or inconsistent, or an output exists (exit status 1).

    `main` catches this one class, prints the message and exits with status 1, so every
    validation in the script can simply `raise PlannerInputError("...")`.
    """


# ----------------------------------------------------------------------------------------------
# Small helpers: names, times, text
# ----------------------------------------------------------------------------------------------

def system_name():
    """Return the system name written in front of paths shown to the user.

    The rule is the kit's (workhub_config.system_name): the variable named by
    system_name.override_env in the settings (default CC_CLUSTER), else the short host name,
    then the settings' system_name.aliases. Without settings the same rule runs on {}.

    Returns:
        str: for example "ws01", or "mycluster" on a cluster login node.
    """
    # `required=False`: a machine without the settings file still gets the host name.
    config = load_config(required=False)
    return settings_system_name(config)


def parse_moment(text, timezone, what="time"):
    """Convert an ISO 8601 text into an aware datetime (one that carries its UTC offset).

    Accepted: "2030-05-06T17:30:00-04:00" (offset given), "2030-05-06T17:30:00" (read in
    `timezone`), "2030-05-06" (midnight at the start of that day in `timezone`).

    Parameters:
        text (str): the ISO 8601 text.
        timezone (ZoneInfo): zone attached to a text without an offset.
        what (str): name of the value, for the error message.

    Returns:
        datetime.datetime: the aware moment, converted to `timezone`.

    Raises:
        PlannerInputError: when the text is not a time.
    """
    # `.strip()` removes spaces and line breaks around the text.
    cleaned_text = str(text).strip()

    # Python 3.9-3.10 refuse a final "Z" (= UTC); "+00:00" means the same.
    if cleaned_text.endswith("Z"):
        cleaned_text = cleaned_text[:-1] + "+00:00"

    # `fromisoformat` parses the text; a date alone gives midnight at the start of that day.
    try:
        moment = datetime.datetime.fromisoformat(cleaned_text)
    except ValueError:
        raise PlannerInputError("%s is not an ISO 8601 time: %r" % (what, text))

    # Without an offset ("naive" datetime) the clock time is read in `timezone`:
    # `.replace(tzinfo=...)` attaches the zone without changing the clock time.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone)

    # `.astimezone(UTC)` converts the moment to UTC. Every moment inside the planner is kept
    # in UTC and converted back to the local zone only when it is printed: adding a duration to
    # a zone-aware local datetime moves the WALL CLOCK (Python keeps the clock time and
    # recomputes the offset), so a 10 h block starting the Saturday evening before a fall-back
    # change at 20:00 (summer time) would "end" Sunday 06:00 (winter time),
    # 11 real hours later. In UTC, additions, subtractions and comparisons are real time.
    # DEBUG CHECK: `moment.tzinfo` is `UTC`; `moment.astimezone(timezone)` shows the local clock.
    return moment.astimezone(UTC)


def format_iso(moment, timezone):
    """Format a moment as ISO 8601 in the local zone with its offset: "2030-05-06T17:30:00-04:00".

    Parameters:
        moment (datetime.datetime): an aware moment (UTC inside the planner).
        timezone (ZoneInfo): the zone to print it in.

    Returns:
        str: the ISO text, seconds included, no microseconds.
    """
    # `.astimezone(timezone)` gives the local clock time and offset of that day (summer or winter);
    # `.replace(microsecond=0)` drops sub-second digits; `.isoformat()` writes the ISO form.
    local_moment = moment.astimezone(timezone).replace(microsecond=0)
    return local_moment.isoformat()


def format_when(moment, timezone):
    """Format a moment for people in the local zone: "Thu Oct 29 18:00".

    Parameters:
        moment (datetime.datetime): an aware moment (UTC inside the planner).
        timezone (ZoneInfo): the zone to print it in.

    Returns:
        str: weekday, month, day and clock time.
    """
    # `strftime` codes: %a weekday name, %b month name, %d day (two digits), %H:%M clock time.
    return moment.astimezone(timezone).strftime("%a %b %d %H:%M")


def hours_between(start, end):
    """Return the length of [start, end] in hours, rounded to 2 decimals.

    Parameters:
        start, end (datetime.datetime): aware moments.

    Returns:
        float: hours, e.g. 14.83.
    """
    # `(end - start)` is a timedelta; `.total_seconds()` its length in seconds. Both moments
    # are UTC, so the difference is real time even across the DST change.
    seconds = (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds()
    return round(seconds / 3600.0, 2)


def hours_to_delta(hours):
    """Convert a number of hours into a timedelta.

    Parameters:
        hours (int or float): a duration in hours.

    Returns:
        datetime.timedelta: the same duration.
    """
    return datetime.timedelta(seconds=float(hours) * 3600.0)


def number_or_zero(value):
    """Return a numeric field as a float, with None (unknown) read as 0.

    Used for resource needs and uses: an unknown value cannot be checked, so it counts as
    nothing (the conservative alternative, refusing the task, would leave every unmeasured task
    unplaced; the measure block exists to find the real value).

    Parameters:
        value: a number or None.

    Returns:
        float: the value, or 0.0.
    """
    if value is None:
        return 0.0
    return float(value)


# ----------------------------------------------------------------------------------------------
# Reading and validating the three input files
# ----------------------------------------------------------------------------------------------

def read_json_file(path, expected_schema, what):
    """Read one JSON input file and check its `schema` field.

    Parameters:
        path (str): the file.
        expected_schema (str): the required value of the top-level "schema" key.
        what (str): name of the file for error messages ("tasks file").

    Returns:
        dict: the parsed JSON object.

    Raises:
        PlannerInputError: the file is missing, is not JSON, is not an object, or has
            another schema.
    """
    # `os.path.isfile` is False for a missing path and for a folder.
    if not os.path.isfile(path):
        raise PlannerInputError("%s not found: %s" % (what, path))

    # `open(..., encoding="utf-8")` reads the bytes as UTF-8 text; the `with` block closes the
    # file when it ends, also on an error. `json.load` parses the text into Python values
    # (objects -> dict, arrays -> list, strings, numbers, true/false -> bool, null -> None).
    with open(path, mode="r", encoding="utf-8") as json_file:
        try:
            content = json.load(json_file)
        except json.JSONDecodeError as error:
            raise PlannerInputError("%s is not valid JSON (%s): %s" % (what, error, path))

    # The planner reads keys from the top level, so it must be an object.
    if not isinstance(content, dict):
        raise PlannerInputError("%s must contain a JSON object: %s" % (what, path))

    # A wrong schema usually means the wrong file was passed (tasks instead of machines).
    schema = content.get("schema")
    if schema != expected_schema:
        raise PlannerInputError("%s has schema %r, expected %r: %s" % (what, schema, expected_schema, path))

    # DEBUG CHECK: `content["schema"]` equals `expected_schema`.
    return content


def validate_task(task, index):
    """Check the fields of one task card that the planner relies on.

    Only the keys that the placement reads are checked here; task_file.py validates every key
    when a card is added. A card that fails here stops the whole run, because a wrong rank or
    state would silently change the order.

    Parameters:
        task (dict): one entry of tasks[].
        index (int): its position in the list, for the error message.

    Raises:
        PlannerInputError: a required key is missing or has the wrong type or value.
    """
    # A card must be an object with a text id.
    if not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]:
        raise PlannerInputError("tasks[%d] must be an object with a text 'id'" % index)
    task_id = task["id"]

    # `rank` is an integer or null; `state` one of five words.
    rank = task.get("rank")
    if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool)):
        raise PlannerInputError("task %s: 'rank' must be an integer or null" % task_id)
    if task.get("state") not in ("pending", "approved", "running", "done", "parked"):
        raise PlannerInputError("task %s: 'state' must be pending, approved, running, done or parked" % task_id)

    # `needs` must be an object with a Boolean `gpu` and a list `machines`.
    needs = task.get("needs")
    if not isinstance(needs, dict) or not isinstance(needs.get("gpu"), bool):
        raise PlannerInputError("task %s: 'needs' must be an object with a Boolean 'gpu'" % task_id)
    if not isinstance(needs.get("machines", []), list):
        raise PlannerInputError("task %s: 'needs.machines' must be a list" % task_id)

    # `cost` must be an object with a known basis; hours is a number or null.
    cost = task.get("cost")
    if not isinstance(cost, dict) or cost.get("basis") not in ("measured", "scaled", "estimate", "none"):
        raise PlannerInputError("task %s: 'cost.basis' must be measured, scaled, estimate or none" % task_id)
    cost_hours = cost.get("hours")
    if cost_hours is not None and (not isinstance(cost_hours, (int, float)) or isinstance(cost_hours, bool)):
        raise PlannerInputError("task %s: 'cost.hours' must be a number or null" % task_id)

    # `deadline` is null or an object with a date text and a Boolean `hard`.
    deadline = task.get("deadline")
    if deadline is not None:
        if not isinstance(deadline, dict) or not isinstance(deadline.get("date"), str) \
                or not isinstance(deadline.get("hard"), bool):
            raise PlannerInputError("task %s: 'deadline' must be null or {date, hard}" % task_id)

    # `depends_on` is a list of names; `cluster` is null or an object with `allowed` and `gpu`.
    if not isinstance(task.get("depends_on", []), list):
        raise PlannerInputError("task %s: 'depends_on' must be a list" % task_id)
    cluster = task.get("cluster")
    if cluster is not None:
        if not isinstance(cluster, dict) or not isinstance(cluster.get("allowed"), list) \
                or not isinstance(cluster.get("gpu"), str):
            raise PlannerInputError("task %s: 'cluster' must be null or {allowed, gpu, gpus, walltime_h}" % task_id)


def validate_machine(machine, index):
    """Check the fields of one machine entry that the planner relies on.

    Parameters:
        machine (dict): one entry of machines[].
        index (int): its position in the list, for the error message.

    Raises:
        PlannerInputError: a required key is missing or wrong.
    """
    if not isinstance(machine, dict) or not isinstance(machine.get("name"), str) or not machine["name"]:
        raise PlannerInputError("machines[%d] must be an object with a text 'name'" % index)
    name = machine["name"]

    # Every machine is a workstation or a cluster and has the lane label used in the timeline.
    if machine.get("kind") not in ("workstation", "cluster"):
        raise PlannerInputError("machine %s: 'kind' must be workstation or cluster" % name)
    if not isinstance(machine.get("label"), str) or not machine["label"]:
        raise PlannerInputError("machine %s: 'label' (the run-status lane name) is required" % name)

    # A workstation's lists must be lists; its gpu is null or {model, memory_gb, count}, where
    # `count` (the number of cards, default 1) is a whole number of at least 1.
    if machine["kind"] == "workstation":
        for key in ("blocked", "exceptions"):
            if not isinstance(machine.get(key, []), list):
                raise PlannerInputError("machine %s: '%s' must be a list" % (name, key))
        gpu = machine.get("gpu")
        if gpu is not None and not isinstance(gpu, dict):
            raise PlannerInputError("machine %s: 'gpu' must be null or {model, memory_gb, count}" % name)
        if gpu is not None:
            card_count = gpu.get("count", 1)
            if isinstance(card_count, bool) or not isinstance(card_count, int) or card_count < 1:
                raise PlannerInputError("machine %s: 'gpu.count' must be a whole number >= 1" % name)

    # A cluster's gpus and queue waits are objects keyed by GPU type.
    if machine["kind"] == "cluster":
        if not isinstance(machine.get("gpus", {}), dict) or not isinstance(machine.get("queue_wait_hours", {}), dict):
            raise PlannerInputError("machine %s: 'gpus' and 'queue_wait_hours' must be objects" % name)


def load_inputs(tasks_path, machines_path, commitments_path):
    """Read and validate the three input files.

    Parameters:
        tasks_path, machines_path, commitments_path (str): the files.

    Returns:
        tuple (tasks_file, machines_file, commitments_file): the three parsed dicts.

    Raises:
        PlannerInputError: see read_json_file, validate_task, validate_machine.
    """
    tasks_file = read_json_file(tasks_path, TASKS_SCHEMA, "tasks file")
    machines_file = read_json_file(machines_path, MACHINES_SCHEMA, "machines file")
    commitments_file = read_json_file(commitments_path, COMMITMENTS_SCHEMA, "commitments file")

    # The lists that the rest of the script walks must exist and be lists.
    if not isinstance(tasks_file.get("tasks"), list):
        raise PlannerInputError("tasks file: 'tasks' must be a list")
    if not isinstance(machines_file.get("machines"), list):
        raise PlannerInputError("machines file: 'machines' must be a list")
    if not isinstance(commitments_file.get("stages"), list):
        raise PlannerInputError("commitments file: 'stages' must be a list")

    # `enumerate(list)` yields (index, item) pairs, so error messages can name the position.
    for index, task in enumerate(tasks_file["tasks"]):
        validate_task(task, index)
    for index, machine in enumerate(machines_file["machines"]):
        validate_machine(machine, index)

    # Task ids and machine names must be unique: a dependency names them.
    task_ids = [task["id"] for task in tasks_file["tasks"]]
    if len(task_ids) != len(set(task_ids)):
        raise PlannerInputError("tasks file: duplicate task ids")
    machine_names = [machine["name"] for machine in machines_file["machines"]]
    if len(machine_names) != len(set(machine_names)):
        raise PlannerInputError("machines file: duplicate machine names")

    # The optional CPU preference must name a cluster of this file ("" or missing = none).
    preferred_cluster = machines_file.get("prefer_cluster_for_cpu") or ""
    cluster_names = [machine["name"] for machine in machines_file["machines"] if machine["kind"] == "cluster"]
    if preferred_cluster and preferred_cluster not in cluster_names:
        raise PlannerInputError("machines file: prefer_cluster_for_cpu %r is not a cluster in the file" % preferred_cluster)

    # Ranks must be unique among ranked tasks (formats.md §1 rules).
    ranks = [task["rank"] for task in tasks_file["tasks"] if task.get("rank") is not None]
    if len(ranks) != len(set(ranks)):
        raise PlannerInputError("tasks file: duplicate ranks")

    # DEBUG CHECK: three dicts; `len(tasks_file["tasks"])` cards, `len(machines_file["machines"])` machines.
    return tasks_file, machines_file, commitments_file


# ----------------------------------------------------------------------------------------------
# §1 Occupants: the busy timeline of a workstation
# ----------------------------------------------------------------------------------------------
#
# An occupant is one dict describing something that uses a machine during [start, end):
#     {"start": datetime, "end": datetime, "what": text shown in reasons,
#      "blocks_all": bool,        # a blocked period or an exception: nothing may run
#      "gpu_cards": int,          # GPU cards held (0 = none; a GPU task of this plan holds 1)
#      "gpu_memory_gb": float, "ram_gb": float, "threads": float}
# Blocks, exceptions, commitments and the blocks placed by this plan all become occupants, so
# one fitting function serves them all.

def make_occupant(start, end, what, blocks_all=False, gpu_cards=0, gpu_memory_gb=0.0, ram_gb=0.0, threads=0.0):
    """Build one occupant dict (see the comment above).

    Parameters:
        start, end (datetime.datetime): the interval [start, end).
        what (str): a short description ("blocked: shared during the day", "commitment X").
        blocks_all (bool): True for blocked periods and exceptions.
        gpu_cards (int): number of GPU cards held (0 = none).
        gpu_memory_gb, ram_gb, threads (float): resources used while it runs.

    Returns:
        dict: the occupant.
    """
    return {"start": start, "end": end, "what": what, "blocks_all": blocks_all, "gpu_cards": int(gpu_cards),
            "gpu_memory_gb": gpu_memory_gb, "ram_gb": ram_gb, "threads": threads}


def weekly_block_intervals(machine, horizon_from, horizon_to, timezone):
    """Expand a workstation's weekly `blocked` periods into concrete intervals inside the horizon.

    Each day's window is built from its local clock times with the zone attached, so the
    change from daylight to standard time (for example the first Sunday of November at 02:00 in
    America/New_York) is handled by `zoneinfo`: Monday 08:50 after the change is still 08:50 on
    the wall clock, which adding 24 h to Sunday 08:50 would not give.

    Parameters:
        machine (dict): the workstation entry (its `blocked` list).
        horizon_from, horizon_to (datetime.datetime): the planning horizon.
        timezone (ZoneInfo): zone of the local clock times in `blocked`.

    Returns:
        list of (start, end, reason) tuples, clipped to the horizon, in time order.

    Raises:
        PlannerInputError: a day name or clock time cannot be read.
    """
    intervals = []

    # The first and last calendar days to build: one day before the horizon (a period that
    # started yesterday may still be running at `now`) up to one day after it.
    # `.astimezone(timezone).date()` is the LOCAL calendar day of a UTC moment (at 23:00 in New
    # York the UTC date is already tomorrow).
    first_day = horizon_from.astimezone(timezone).date() - datetime.timedelta(days=1)
    last_day = horizon_to.astimezone(timezone).date() + datetime.timedelta(days=1)

    for period in machine.get("blocked", []):
        # Day names -> weekday numbers (Mon = 0 ... Sun = 6), as `date.weekday()` returns them.
        weekday_numbers = []
        for day_name in period.get("days", []):
            if day_name not in WEEKDAY_NAMES:
                raise PlannerInputError("machine %s: unknown day name %r in 'blocked'" % (machine["name"], day_name))
            weekday_numbers.append(WEEKDAY_NAMES.index(day_name))

        # "08:50" -> (8, 50). `split(":")` cuts at the colon; `int` converts each part.
        try:
            from_hour, from_minute = [int(part) for part in str(period["from"]).split(":")]
            to_hour, to_minute = [int(part) for part in str(period["to"]).split(":")]
        except (KeyError, ValueError):
            raise PlannerInputError("machine %s: 'blocked' needs 'from' and 'to' as HH:MM" % machine["name"])
        reason = period.get("reason", "blocked period")

        # Walk the calendar days one by one. `timedelta(days=1)` added to a `date` (not a
        # datetime) is a pure calendar step, which is exactly what the DST rule requires.
        day = first_day
        while day <= last_day:
            if day.weekday() in weekday_numbers:
                # `datetime.datetime.combine(day, time, tzinfo=zone)` builds the local moment
                # "that day at that clock time" with the zone's offset of that day.
                block_start = datetime.datetime.combine(day, datetime.time(from_hour, from_minute), tzinfo=timezone)
                block_end = datetime.datetime.combine(day, datetime.time(to_hour, to_minute), tzinfo=timezone)

                # A period that ends at or before it starts crosses midnight ("22:00" to "06:00").
                if block_end <= block_start:
                    next_day = day + datetime.timedelta(days=1)
                    block_end = datetime.datetime.combine(next_day, datetime.time(to_hour, to_minute), tzinfo=timezone)

                # Back to UTC, the planner's internal frame; then keep the part inside the
                # horizon (`max`/`min` on aware datetimes compare the real moments).
                block_start = block_start.astimezone(UTC)
                block_end = block_end.astimezone(UTC)
                clipped_start = max(block_start, horizon_from)
                clipped_end = min(block_end, horizon_to)
                if clipped_start < clipped_end:
                    intervals.append((clipped_start, clipped_end, reason))
            day = day + datetime.timedelta(days=1)

    # DEBUG CHECK: for a rule "Mon-Fri 08:50-18:00" and a 7-day horizon there are at most 5 or
    # 6 intervals, each 9 h 10 min long except the clipped ones.
    return intervals


def subtract_openings(intervals, openings):
    """Remove the opening intervals from a list of blocked intervals.

    Example: blocked Thu 08:50-18:00, opening Thu 13:00-18:00 -> blocked Thu 08:50-13:00.

    Parameters:
        intervals (list of (start, end, reason)): the blocked intervals.
        openings (list of (start, end)): the openings (exceptions with block false).

    Returns:
        list of (start, end, reason): the blocked intervals with the openings cut out.
    """
    result = list(intervals)

    # Each opening is cut out of every interval; an interval may be split into two.
    for opening_start, opening_end in openings:
        cut_result = []
        for start, end, reason in result:
            # No overlap: keep the interval unchanged.
            if opening_end <= start or opening_start >= end:
                cut_result.append((start, end, reason))
                continue
            # The part before the opening survives, and the part after it.
            if start < opening_start:
                cut_result.append((start, opening_start, reason))
            if opening_end < end:
                cut_result.append((opening_end, end, reason))
        result = cut_result

    return result


def commitment_runs(commitments_file, machine):
    """Collect the active commitments (running or queued runs) of one machine lane.

    A stage belongs to the machine when its `model` equals the machine's `label`
    ("ws01 · RTX 4090", the lane name planner_settings.py writes) or its plain `name` ("ws01"),
    so a timeline drawn by hand with plain machine names is understood too.

    Parameters:
        commitments_file (dict): the run-status JSON.
        machine (dict): the machine entry (its `label` and `name`).

    Returns:
        list of (run, stage) pairs for the runs with status running or queued.
    """
    lane_names = (machine["label"], machine["name"])
    found = []
    for stage in commitments_file.get("stages", []):
        # Only the stages of this lane; `.get("model")` is None for a stage without a model.
        if stage.get("model") not in lane_names:
            continue
        for run in stage.get("runs", []):
            if run.get("status") in ACTIVE_RUN_STATUSES:
                found.append((run, stage))
    return found


def commitment_interval(run, now, timezone):
    """Return the [start, end] of a commitment run, or None when it has no end.

    A running run goes from `start` to `estimate_end`; a queued run from `estimate_start` to
    `estimate_end`. A missing start is read as `now` (it is already running or starts any
    moment); a missing end gives None: the run cannot be put on the timeline, and a task
    depending on it is unplaced (planning_rules.md §2.1).

    Parameters:
        run (dict): the run.
        now (datetime.datetime): the planning clock.
        timezone (ZoneInfo): zone of naive times.

    Returns:
        tuple (start, end) of aware datetimes, or None.
    """
    # `or` picks the first non-empty text: `start` for a running run, `estimate_start` for a
    # queued one; both missing -> None -> now.
    start_text = run.get("start") or run.get("estimate_start")
    end_text = run.get("estimate_end") or run.get("end")
    if not end_text:
        return None
    start = parse_moment(start_text, timezone, "commitment start") if start_text else now
    end = parse_moment(end_text, timezone, "commitment end")
    return start, end


def base_occupants(machine, commitments_file, now, horizon_to, timezone):
    """Build the occupant list of one workstation before any block is placed (§1).

    Parameters:
        machine (dict): the workstation entry.
        commitments_file (dict): the run-status JSON.
        now, horizon_to (datetime.datetime): the planning horizon.
        timezone (ZoneInfo): zone of local times.

    Returns:
        list of occupant dicts.
    """
    occupants = []

    # 1. Weekly blocked periods, minus the openings among the exceptions.
    weekly = weekly_block_intervals(machine, now, horizon_to, timezone)
    openings = []
    for exception in machine.get("exceptions", []):
        if exception.get("block") is False:
            openings.append((parse_moment(exception["from"], timezone, "exception from"),
                             parse_moment(exception["to"], timezone, "exception to")))
    weekly = subtract_openings(weekly, openings)
    for start, end, reason in weekly:
        occupants.append(make_occupant(start, end, "blocked: " + reason, blocks_all=True))

    # 2. One-off blocks (exceptions with block true), also minus the openings: an opening is
    #    the user's explicit later statement about that moment (assumption in the header).
    one_off = []
    for exception in machine.get("exceptions", []):
        if exception.get("block", True) is not False:
            one_off.append((parse_moment(exception["from"], timezone, "exception from"),
                            parse_moment(exception["to"], timezone, "exception to"),
                            exception.get("reason", "exception")))
    one_off = subtract_openings(one_off, openings)
    for start, end, reason in one_off:
        occupants.append(make_occupant(start, end, "exception: " + reason, blocks_all=True))

    # 3. Commitments of this lane with their resource use. Without `uses`, the run holds one
    #    GPU card unless the stage says `gpu_time` false, and uses no counted memory or threads.
    #    `uses.gpu_cards` (a whole number) says how many cards a multi-GPU run holds.
    for run, stage in commitment_runs(commitments_file, machine):
        interval = commitment_interval(run, now, timezone)
        if interval is None:
            continue
        start, end = interval
        uses = run.get("uses") or {}
        stage_is_gpu_work = stage.get("gpu_time", True) is not False
        holds_gpu = bool(uses.get("gpu", stage_is_gpu_work))
        held_cards = 0
        if holds_gpu:
            held_cards = int(uses.get("gpu_cards") or 1)
        occupants.append(make_occupant(start, end, "commitment: " + str(run.get("name", "?")),
                                       gpu_cards=held_cards,
                                       gpu_memory_gb=number_or_zero(uses.get("gpu_memory_gb")),
                                       ram_gb=number_or_zero(uses.get("ram_gb")),
                                       threads=number_or_zero(uses.get("threads"))))

    # DEBUG CHECK: print `[(o["what"], o["start"], o["end"]) for o in occupants]` to see the
    # busy timeline of this machine.
    return occupants


# ----------------------------------------------------------------------------------------------
# §1 Fitting: does a task fit on a machine at a moment, and in which windows
# ----------------------------------------------------------------------------------------------

def task_needs(task):
    """Return the resource needs of a task as plain numbers.

    Parameters:
        task (dict): the task card.

    Returns:
        dict: {"gpu": bool, "gpu_memory_gb", "ram_gb", "threads": float}.
    """
    needs = task["needs"]
    return {"gpu": bool(needs.get("gpu")),
            "gpu_memory_gb": number_or_zero(needs.get("gpu_memory_gb")),
            "ram_gb": number_or_zero(needs.get("ram_gb")),
            "threads": number_or_zero(needs.get("threads"))}


def fits_with(machine, overlapping, needs):
    """Tell whether a task with `needs` fits beside the given overlapping occupants (§1).

    Parameters:
        machine (dict): the workstation (capacities gpu.memory_gb and gpu.count, ram_gb,
            threads, ram_heavy_gb).
        overlapping (list of occupant dicts): what runs at the moment examined.
        needs (dict): from task_needs().

    Returns:
        bool: True when nothing blocks everything, a GPU card is free for a GPU task, the summed
            GPU memory, RAM and threads stay within capacity, and the ram_heavy rule holds.
    """
    # A blocked period or exception blocks everything.
    for occupant in overlapping:
        if occupant["blocks_all"]:
            return False

    # Sums of what the overlapping occupants already use.
    used_gpu_memory = sum(occupant["gpu_memory_gb"] for occupant in overlapping)
    used_ram = sum(occupant["ram_gb"] for occupant in overlapping)
    used_threads = sum(occupant["threads"] for occupant in overlapping)

    # A GPU task needs one free card: the cards held by the overlapping occupants plus this task
    # must not exceed the machine's `gpu.count` (default 1; a machine without a GPU has 0). The
    # GPU memory of everything overlapping must stay within count × memory per card. With one
    # card this is "the GPU is free and the memory fits", the planner's original rule.
    if needs["gpu"]:
        gpu = machine.get("gpu") or {}
        card_count = int(gpu.get("count", 1)) if machine.get("gpu") else 0
        held_cards = sum(occupant["gpu_cards"] for occupant in overlapping)
        if held_cards + 1 > card_count:
            return False
        gpu_capacity = gpu.get("memory_gb")
        if gpu_capacity is not None and used_gpu_memory + needs["gpu_memory_gb"] > float(gpu_capacity) * card_count:
            return False

    # RAM and threads: a null capacity means "not limited here".
    ram_capacity = machine.get("ram_gb")
    if ram_capacity is not None and used_ram + needs["ram_gb"] > float(ram_capacity):
        return False
    thread_capacity = machine.get("threads")
    if thread_capacity is not None and used_threads + needs["threads"] > float(thread_capacity):
        return False

    # One job at or above ram_heavy_gb at a time (applied to GPU tasks too: header assumption).
    ram_heavy = machine.get("ram_heavy_gb")
    if ram_heavy is not None and needs["ram_gb"] >= float(ram_heavy):
        for occupant in overlapping:
            if occupant["ram_gb"] >= float(ram_heavy):
                return False

    return True


def feasible_windows(machine, occupants, needs, from_time, to_time):
    """List the intervals of [from_time, to_time] where a task with `needs` fits (§1, §2.5).

    Method: every occupant start and end inside the range is a breakpoint; between two
    consecutive breakpoints the set of overlapping occupants is constant, so one check at the
    midpoint decides the whole piece. Consecutive feasible pieces are merged.

    Parameters:
        machine (dict): the workstation.
        occupants (list of occupant dicts): its busy timeline.
        needs (dict): from task_needs().
        from_time, to_time (datetime.datetime): the range examined.

    Returns:
        list of (start, end) tuples, in time order, non-overlapping, each at least one piece.
    """
    if from_time >= to_time:
        return []

    # Breakpoints: the range ends plus every occupant boundary strictly inside the range.
    # A `set` removes duplicates; `sorted` orders the moments.
    breakpoints = {from_time, to_time}
    for occupant in occupants:
        for boundary in (occupant["start"], occupant["end"]):
            if from_time < boundary < to_time:
                breakpoints.add(boundary)
    ordered = sorted(breakpoints)

    windows = []
    # `zip(ordered, ordered[1:])` pairs each breakpoint with the next one: (b0, b1), (b1, b2)...
    for piece_start, piece_end in zip(ordered, ordered[1:]):
        # The occupants overlapping this piece are those covering its midpoint.
        midpoint = piece_start + (piece_end - piece_start) / 2
        overlapping = [occupant for occupant in occupants if occupant["start"] < midpoint < occupant["end"]]
        if not fits_with(machine, overlapping, needs):
            continue
        # Merge with the previous window when they touch.
        if windows and windows[-1][1] == piece_start:
            windows[-1] = (windows[-1][0], piece_end)
        else:
            windows.append((piece_start, piece_end))

    # DEBUG CHECK: with no occupants the result is [(from_time, to_time)].
    return windows


def place_in_windows(windows, duration, can_pause):
    """Choose the pieces of a block of `duration` inside the feasible windows (§2.5).

    Parameters:
        windows (list of (start, end)): from feasible_windows(), in time order.
        duration (datetime.timedelta): the work to place.
        can_pause (bool): False = the whole duration inside one window; True = accumulated
            over windows, each piece at least MIN_PIECE long (shorter windows are skipped).

    Returns:
        list of (start, end) pieces, or [] when the duration does not fit.
    """
    # Not pausable: the first window long enough, used from its start.
    if not can_pause:
        for window_start, window_end in windows:
            if window_end - window_start >= duration:
                return [(window_start, window_start + duration)]
        return []

    # Pausable: fill windows in order until the duration is covered.
    pieces = []
    remaining = duration
    for window_start, window_end in windows:
        window_length = window_end - window_start
        if window_length < MIN_PIECE:
            continue
        piece_length = min(window_length, remaining)
        pieces.append((window_start, window_start + piece_length))
        remaining = remaining - piece_length
        if remaining <= datetime.timedelta(0):
            break

    # Not enough room in the horizon: nothing is placed (a partial placement would hide it).
    if remaining > datetime.timedelta(0):
        return []
    return pieces


# ----------------------------------------------------------------------------------------------
# §2 The greedy pass
# ----------------------------------------------------------------------------------------------

def ordered_task_ids(tasks):
    """Return the ids of the placeable tasks in planning order (§0, plus the dependency move).

    Placeable = state pending or approved AND rank set, sorted by rank. When a task depends on
    a placeable task of lower priority (higher rank number), that dependency is moved just
    ahead of it: a dependency can never run after the task that waits for it. The move is
    stable (every other relative order is kept) and reported by the caller.

    Parameters:
        tasks (list of dict): tasks[] of the tasks file.

    Returns:
        tuple (order, moves): `order` the task ids; `moves` texts describing each move.
    """
    placeable = [task for task in tasks if task.get("state") in PLACEABLE_STATES and task.get("rank") is not None]
    # `sorted(..., key=...)` orders by the rank number; the `lambda` returns the key of one task.
    placeable = sorted(placeable, key=lambda task: task["rank"])
    by_id = {task["id"]: task for task in placeable}

    order = []
    moves = []

    def add_with_dependencies(task_id, chain):
        """Append `task_id` after its placeable dependencies (depth-first, no repeats)."""
        if task_id in order:
            return
        # A cycle (A depends on B depends on A) would loop forever; it is reported instead.
        if task_id in chain:
            raise PlannerInputError("tasks file: dependency cycle through %s" % " -> ".join(chain + [task_id]))
        for dependency_id in by_id[task_id].get("depends_on", []):
            if dependency_id in by_id and dependency_id not in order:
                add_with_dependencies(dependency_id, chain + [task_id])
                if by_id[dependency_id]["rank"] > by_id[task_id]["rank"]:
                    moves.append("%s moved ahead of %s (it is a dependency of it)" % (dependency_id, task_id))
        order.append(task_id)

    for task in placeable:
        add_with_dependencies(task["id"], [])

    # DEBUG CHECK: `order` holds every placeable id exactly once.
    return order, moves


def resolve_earliest_start(task, now, timezone, placed_by_task, tasks_by_id, commitments_file):
    """Compute a task's earliest start from now, not_before and its dependencies (§2.1).

    Parameters:
        task (dict): the task card.
        now (datetime.datetime): the planning clock.
        timezone (ZoneInfo): zone of naive times.
        placed_by_task (dict): task id -> {"end": datetime or None, "kind": "run"|"measure"} for
            the tasks already handled in this pass.
        tasks_by_id (dict): every task card by id (also done or running ones).
        commitments_file (dict): the run-status JSON (commitment run names).

    Returns:
        tuple (earliest_start, blocking_reason): the moment, or None with a text explaining
            why the task cannot be placed.
    """
    earliest_start = now

    # `not_before`: a moment chosen by the user.
    if task.get("not_before"):
        earliest_start = max(earliest_start, parse_moment(task["not_before"], timezone, "not_before"))

    # Every item of depends_on must have an end.
    for dependency in task.get("depends_on", []):
        dependency_end = None

        if dependency in placed_by_task:
            # A task handled earlier in this pass: its production block's end. A task that
            # only got a measure block has no production end yet.
            placed = placed_by_task[dependency]
            if placed["end"] is None:
                return None, "depends on %s, unplaced" % dependency
            if placed["kind"] == "measure":
                return None, "depends on %s, which waits for its measurement" % dependency
            dependency_end = placed["end"]

        elif dependency in tasks_by_id and tasks_by_id[dependency].get("state") == "done":
            # A finished task: no constraint.
            continue

        else:
            # A commitment run name (the orchestrator's timeline) or a task without a block.
            commitment = find_commitment(commitments_file, dependency)
            if commitment is None:
                if dependency in tasks_by_id:
                    return None, "depends on %s, unplaced" % dependency
                return None, "depends on %s, which is neither a task nor a commitment" % dependency
            run = commitment
            if run.get("status") == "done":
                continue
            if run.get("status") in ("failed", "stopped"):
                return None, "depends on %s, which %s" % (dependency, run["status"])
            interval = commitment_interval(run, now, timezone)
            if interval is None:
                return None, "depends on %s, which has no end time" % dependency
            dependency_end = interval[1]

        earliest_start = max(earliest_start, dependency_end)

    # DEBUG CHECK: `earliest_start >= now`.
    return earliest_start, None


def find_commitment(commitments_file, run_name):
    """Find a run by name in the commitments file (any stage, any status).

    Parameters:
        commitments_file (dict): the run-status JSON.
        run_name (str): the run's `name`.

    Returns:
        dict: the run, or None.
    """
    for stage in commitments_file.get("stages", []):
        for run in stage.get("runs", []):
            if run.get("name") == run_name:
                return run
    return None


def duration_on(task, machine_name):
    """Return the task's duration on one workstation as a timedelta, or None when unknown (§2.2).

    Parameters:
        task (dict): the task card.
        machine_name (str): the workstation.

    Returns:
        datetime.timedelta or None.
    """
    cost = task["cost"]
    per_machine = cost.get("per_machine") or {}
    hours = per_machine.get(machine_name, cost.get("hours"))
    if hours is None:
        return None
    return hours_to_delta(hours)


def workstation_candidates(task, machines, warnings):
    """List the workstations allowed for a task that have enough capacity (§2.4).

    Parameters:
        task (dict): the task card.
        machines (list of dict): machines[] of the machines file.
        warnings (list of str): receives one line per allowed machine that is unknown or too
            small.

    Returns:
        list of machine dicts, in the order of needs.machines (or the file order when empty).
    """
    needs = task_needs(task)
    allowed_names = task["needs"].get("machines") or []
    workstations = [machine for machine in machines if machine["kind"] == "workstation"]
    by_name = {machine["name"]: machine for machine in workstations}

    # Empty list = any workstation, in file order.
    if allowed_names:
        chosen = []
        for name in allowed_names:
            if name not in by_name:
                warnings.append("%s is not a workstation in machines.json" % name)
                continue
            chosen.append(by_name[name])
    else:
        chosen = list(workstations)

    # Capacity filter: GPU presence and memory, RAM, threads.
    fitting = []
    for machine in chosen:
        gpu = machine.get("gpu")
        if needs["gpu"] and gpu is None:
            warnings.append("%s has no GPU" % machine["name"])
            continue
        if needs["gpu"] and gpu.get("memory_gb") is not None and needs["gpu_memory_gb"] > float(gpu["memory_gb"]):
            warnings.append("%s: GPU memory %s GB < %s GB needed" % (machine["name"], gpu["memory_gb"], needs["gpu_memory_gb"]))
            continue
        if machine.get("ram_gb") is not None and needs["ram_gb"] > float(machine["ram_gb"]):
            warnings.append("%s: RAM %s GB < %s GB needed" % (machine["name"], machine["ram_gb"], needs["ram_gb"]))
            continue
        if machine.get("threads") is not None and needs["threads"] > float(machine["threads"]):
            warnings.append("%s: %s threads < %s needed" % (machine["name"], machine["threads"], needs["threads"]))
            continue
        fitting.append(machine)

    return fitting


def cluster_candidate(task, cluster, earliest_start, timezone, warnings, budget_warnings):
    """Evaluate one cluster for a task: submit time, start, end, or a reason to drop it (§3).

    Parameters:
        task (dict): the task card (its `cluster` option).
        cluster (dict): the cluster entry of machines.json.
        earliest_start (datetime.datetime): when the job can be submitted.
        timezone (ZoneInfo): zone of naive outage times and of the printed times.
        warnings (list of str): receives the reason when the cluster is dropped.
        budget_warnings (list of dict): receives a budget entry when the job overruns it.

    Returns:
        dict candidate {"machine", "kind": "cluster", "submit", "start", "end", "hours",
            "wait_hours"} or None when the cluster is not usable.
    """
    option = task["cluster"]
    gpu_key = option["gpu"]
    name = cluster["name"]

    # The cluster must offer that GPU type.
    if gpu_key not in (cluster.get("gpus") or {}):
        warnings.append("%s has no GPU type %r" % (name, gpu_key))
        return None

    # A measured queue wait is required (§3).
    wait_entry = (cluster.get("queue_wait_hours") or {}).get(gpu_key)
    if not wait_entry or wait_entry.get("typical") is None:
        warnings.append("%s: no measured queue wait for %s" % (name, gpu_key))
        return None
    wait_hours = float(wait_entry["typical"])

    # Walltime of the job, and the cluster's maximum.
    walltime_hours = option.get("walltime_h")
    if walltime_hours is None:
        warnings.append("%s: cluster.walltime_h is null" % name)
        return None
    walltime_hours = float(walltime_hours)
    max_walltime = cluster.get("max_walltime_h")
    if max_walltime is not None and walltime_hours > float(max_walltime):
        warnings.append("%s: walltime %.1f h > max %.1f h" % (name, walltime_hours, float(max_walltime)))
        return None

    # Submit at the earliest start; start after the queue wait; end after the walltime.
    submit = earliest_start
    start = submit + hours_to_delta(wait_hours)
    end = start + hours_to_delta(walltime_hours)

    # An outage overlapping [submit, end] removes the cluster.
    for outage in cluster.get("outages") or []:
        outage_start = parse_moment(outage["from"], timezone, "outage from")
        outage_end = parse_moment(outage["to"], timezone, "outage to")
        if outage_start < end and outage_end > submit:
            warnings.append("%s: outage %s → %s (%s)" % (name, format_when(outage_start, timezone),
                                                        format_when(outage_end, timezone),
                                                        outage.get("reason", "")))
            return None

    # Budget: added = walltime × gpus (for a CPU resource such as "cpu", `gpus` is the number
    # of cores, so the unit is core-hours); an overrun is a warning, the cluster stays a
    # candidate. A budget with `applies_to` (resource keys) counts only jobs of those types;
    # without it, every job on the cluster counts. An unknown `used` counts as 0.
    budget = cluster.get("budget")
    applies_to = (budget or {}).get("applies_to") or []
    budget_counts_this_job = bool(budget) and (not applies_to or gpu_key in applies_to)
    if budget_counts_this_job:
        gpus = float(option.get("gpus") or 1)
        added = walltime_hours * gpus
        used = float(budget.get("used") or 0)
        allocated = float(budget.get("allocated") or 0)
        if used + added > allocated:
            budget_warnings.append({"cluster": name, "task_id": task["id"], "unit": budget.get("unit", "gpu-hours"),
                                    "used": used, "allocated": allocated, "added": added,
                                    "account": budget.get("account") or ""})

    return {"machine": name, "kind": "cluster", "submit": submit, "start": start, "end": end,
            "hours": walltime_hours, "wait_hours": wait_hours}


def candidate_sort_key(candidate, task, machines_by_name):
    """Sort key for the choice among candidates (§2.6): earliest end, needs.machines order, GPU size.

    Parameters:
        candidate (dict): a workstation or cluster candidate with "end" and "machine".
        task (dict): the task card.
        machines_by_name (dict): name -> machine entry.

    Returns:
        tuple (end, position, gpu_memory): smaller sorts first.
    """
    allowed = task["needs"].get("machines") or []
    cluster_allowed = (task.get("cluster") or {}).get("allowed") or []
    name = candidate["machine"]

    # Position in the user's preferred order; clusters come after the workstations.
    if name in allowed:
        position = allowed.index(name)
    elif name in cluster_allowed:
        position = len(allowed) + cluster_allowed.index(name)
    else:
        position = len(allowed) + len(cluster_allowed)

    # The smaller GPU wins a tie (keep the big card free); a cluster or a CPU box counts as 0.
    gpu = machines_by_name[name].get("gpu") or {}
    gpu_memory = float(gpu.get("memory_gb") or 0)
    return (candidate["end"], position, gpu_memory)


def place_one_task(task, context, pass_state):
    """Place one task (production or measure block) on the best candidate (§2, §3).

    Parameters:
        task (dict): the task card.
        context (dict): shared inputs: now, horizon_to, timezone, machines, machines_by_name,
            tasks_by_id, commitments_file.
        pass_state (dict): the state of this greedy pass, changed in place: occupants (per
            workstation), blocks, unplaced, waiting_for_measurement, budget_warnings,
            placed_by_task.

    Returns:
        None (the result is appended to pass_state).
    """
    task_id = task["id"]
    rank = task["rank"]
    warnings = []

    # §2.1 Earliest start.
    earliest_start, blocking_reason = resolve_earliest_start(
        task, context["now"], context["timezone"], pass_state["placed_by_task"],
        context["tasks_by_id"], context["commitments_file"])
    if earliest_start is None:
        pass_state["unplaced"].append({"task_id": task_id, "reason": blocking_reason, "category": "dependency"})
        pass_state["placed_by_task"][task_id] = {"end": None, "kind": None}
        return

    # §2.3 What to place: production block or measure block.
    cost_basis = task["cost"].get("basis")
    accept_estimate = bool(task.get("accept_estimate"))
    workstation_trusted = cost_basis in TRUSTED_BASES or accept_estimate
    cluster_option = task.get("cluster")
    cluster_basis = (cluster_option or {}).get("basis") or cost_basis
    cluster_trusted = cluster_option is not None and (cluster_basis in TRUSTED_BASES or accept_estimate)
    place_measure = not workstation_trusted and not cluster_trusted
    if accept_estimate and cost_basis not in TRUSTED_BASES:
        warnings.append("estimate, not measured (accept_estimate)")

    needs = task_needs(task)
    candidates = []

    # §2.4-§2.5 Workstation candidates and their earliest fit.
    if place_measure or workstation_trusted:
        for machine in workstation_candidates(task, context["machines"], warnings):
            if place_measure:
                measure = task.get("measure") or {}
                duration = hours_to_delta(measure.get("hours") or DEFAULT_MEASURE_HOURS)
                can_pause = False
            else:
                duration = duration_on(task, machine["name"])
                can_pause = bool(task.get("can_pause"))
            if duration is None:
                warnings.append("%s: cost.hours is null" % machine["name"])
                continue
            occupants = pass_state["occupants"][machine["name"]]
            windows = feasible_windows(machine, occupants, needs, earliest_start, context["horizon_to"])
            pieces = place_in_windows(windows, duration, can_pause)
            if not pieces:
                warnings.append("%s: no room for %.1f h before %s" % (machine["name"], duration.total_seconds() / 3600,
                                                                      format_when(context["horizon_to"], context["timezone"])))
                continue
            candidates.append({"machine": machine["name"], "kind": "workstation", "pieces": pieces,
                               "end": pieces[-1][1], "hours": duration.total_seconds() / 3600})

    # §3 Cluster candidates (production only: a measurement runs on a workstation).
    if cluster_trusted and not place_measure:
        for cluster_name in cluster_option.get("allowed") or []:
            cluster = context["machines_by_name"].get(cluster_name)
            if cluster is None or cluster["kind"] != "cluster":
                warnings.append("%s is not a cluster in machines.json" % cluster_name)
                continue
            candidate = cluster_candidate(task, cluster, earliest_start, context["timezone"], warnings,
                                          pass_state["budget_warnings"])
            if candidate is not None:
                candidates.append(candidate)

    # No candidate: unplaced, with every reason collected.
    if not candidates:
        reason = "no room on any allowed machine" if not warnings else "; ".join(warnings)
        pass_state["unplaced"].append({"task_id": task_id, "reason": reason, "category": "no_room"})
        pass_state["placed_by_task"][task_id] = {"end": None, "kind": None}
        return

    # §2.6 Choice: earliest end, then the user's order, then the smaller GPU.
    candidates = sorted(candidates, key=lambda candidate: candidate_sort_key(candidate, task, context["machines_by_name"]))
    reason = "rank %d; earliest end among %s" % (rank, ", ".join(candidate["machine"] for candidate in candidates))

    # §2.6 CPU preference: a CPU-only task goes to the cluster named by
    # machines.prefer_cluster_for_cpu whenever that cluster is a candidate AND (when the task has
    # a deadline) ends by it. The preferred candidate is moved to the front; the order of the
    # others is kept, so the runner-up and the warnings below still list them.
    deadline_moment = deadline_end_of_day(task, context["timezone"])
    preferred_cluster = context["prefer_cluster_for_cpu"]
    if preferred_cluster and not needs["gpu"] and not place_measure:
        for index, candidate in enumerate(candidates):
            if candidate["machine"] != preferred_cluster or candidate["kind"] != "cluster":
                continue
            if deadline_moment is not None and candidate["end"] > deadline_moment:
                warnings.append("%s (preferred for CPU work) would miss the deadline" % preferred_cluster)
                break
            if index > 0:
                candidates = [candidate] + candidates[:index] + candidates[index + 1:]
            reason = "rank %d; CPU work goes to %s when it can (setting prefer_cluster_for_cpu)" % (rank, preferred_cluster)
            break

    chosen = candidates[0]
    other_names = [candidate["machine"] for candidate in candidates[1:]]
    if place_measure:
        reason = "rank %d; measurement first (cost basis %s)" % (rank, cost_basis)

    # The runner-up of the other kind (cluster vs workstation) is recorded as the alternative.
    alternative = None
    for candidate in candidates[1:]:
        if candidate["kind"] != chosen["kind"]:
            alternative = {"machine": candidate["machine"], "end": format_iso(candidate["end"], context["timezone"]),
                           "hours": round(candidate["hours"], 2)}
            break
    for candidate in candidates[1:]:
        warnings.append("%s would end %s (%.1f h)" % (candidate["machine"], format_when(candidate["end"], context["timezone"]),
                                                      candidate["hours"]))

    # Deadline check of the production block (`deadline_moment` was computed above).
    deadline_ok = None
    if deadline_moment is not None and not place_measure:
        deadline_ok = chosen["end"] <= deadline_moment
    if deadline_moment is not None and place_measure:
        warnings.append("deadline %s: the production run is not placed until measured" % task["deadline"]["date"])

    # Build the block dicts.
    basis = cost_basis if chosen["kind"] == "workstation" else cluster_basis
    common = {"task_id": task_id, "rank": rank, "title": task.get("title", task_id), "machine": chosen["machine"],
              "basis": basis, "source": task["cost"].get("source") or "", "deadline_ok": deadline_ok,
              "reason": reason, "warnings": warnings}
    if alternative is not None:
        common["alternative"] = alternative
    if place_measure:
        common["basis"] = "measure"
        common["measure_what"] = (task.get("measure") or {}).get("what") or "a short profiling run"

    new_blocks = []
    if chosen["kind"] == "workstation":
        total_pieces = len(chosen["pieces"])
        for piece_index, (piece_start, piece_end) in enumerate(chosen["pieces"]):
            block = dict(common)
            block["kind"] = "measure" if place_measure else "run"
            block["start"] = piece_start
            block["end"] = piece_end
            block["hours"] = hours_between(piece_start, piece_end)
            if total_pieces > 1:
                block["part"] = "%d/%d" % (piece_index + 1, total_pieces)
            new_blocks.append(block)
            # §2.7 The placed piece consumes the machine's resources for later tasks.
            pass_state["occupants"][chosen["machine"]].append(make_occupant(
                piece_start, piece_end, "plan: " + task_id, gpu_cards=1 if needs["gpu"] else 0,
                gpu_memory_gb=needs["gpu_memory_gb"],
                ram_gb=needs["ram_gb"], threads=needs["threads"]))
    else:
        submit_block = dict(common)
        submit_block["kind"] = "submit"
        submit_block["start"] = chosen["submit"]
        submit_block["end"] = chosen["start"]
        submit_block["hours"] = hours_between(chosen["submit"], chosen["start"])
        submit_block["wait_hours"] = chosen["wait_hours"]
        new_blocks.append(submit_block)
        run_block = dict(common)
        run_block["kind"] = "run"
        run_block["start"] = chosen["start"]
        run_block["end"] = chosen["end"]
        run_block["hours"] = hours_between(chosen["start"], chosen["end"])
        new_blocks.append(run_block)

    pass_state["blocks"].extend(new_blocks)
    if place_measure:
        pass_state["waiting_for_measurement"].append({"task_id": task_id, "measure_block": len(pass_state["blocks"]) - 1})
        pass_state["placed_by_task"][task_id] = {"end": chosen["end"], "kind": "measure"}
    else:
        pass_state["placed_by_task"][task_id] = {"end": chosen["end"], "kind": "run"}

    # DEBUG CHECK: `pass_state["blocks"][-1]["end"]` equals `chosen["end"]`; `other_names` lists
    # the candidates that lost (they are also in `warnings` with their end times).
    assert pass_state["blocks"][-1]["end"] == chosen["end"], other_names


def deadline_end_of_day(task, timezone):
    """Return the last moment of the task's deadline day, or None without a deadline (§4).

    Parameters:
        task (dict): the task card.
        timezone (ZoneInfo): the local zone.

    Returns:
        datetime.datetime or None: the deadline date at 23:59:59 local time.
    """
    deadline = task.get("deadline")
    if not deadline:
        return None
    # The date text is read as local midnight, so its local calendar day is the deadline day;
    # the last second of that day is built in the local zone and converted to UTC.
    day = parse_moment(deadline["date"], timezone, "deadline date").astimezone(timezone).date()
    end_of_day = datetime.datetime.combine(day, datetime.time(23, 59, 59), tzinfo=timezone)
    return end_of_day.astimezone(UTC)


def run_greedy_pass(order, context):
    """Run one greedy placement pass over the tasks in `order` (§2).

    Parameters:
        order (list of str): task ids in planning order.
        context (dict): shared inputs (see place_one_task) including base_occupants.

    Returns:
        dict pass_state: blocks (in placement order), unplaced, waiting_for_measurement,
            budget_warnings, placed_by_task, occupants (after placement).
    """
    # A deep copy of the base occupants: this pass adds its blocks without touching the base.
    pass_state = {"occupants": copy.deepcopy(context["base_occupants"]), "blocks": [], "unplaced": [],
                  "waiting_for_measurement": [], "budget_warnings": [], "placed_by_task": {}}
    for task_id in order:
        place_one_task(context["tasks_by_id"][task_id], context, pass_state)
    return pass_state


# ----------------------------------------------------------------------------------------------
# §4 Hard deadlines: promotion one task at a time
# ----------------------------------------------------------------------------------------------

def task_meets_deadline(task, pass_state, context):
    """Tell whether a task's production block ends by its deadline in a pass.

    Parameters:
        task (dict): the task card (with a deadline).
        pass_state (dict): a finished greedy pass.
        context (dict): shared inputs.

    Returns:
        tuple (meets, earliest_end, category): meets is True/False; earliest_end the block end
            or None; category "placed", "measure", "unplaced_room" or "unplaced_dependency".
    """
    placed = pass_state["placed_by_task"].get(task["id"], {"end": None, "kind": None})
    if placed["kind"] == "run":
        deadline_moment = deadline_end_of_day(task, context["timezone"])
        return placed["end"] <= deadline_moment, placed["end"], "placed"
    if placed["kind"] == "measure":
        # Not decidable before the measurement; counted as met so no promotion is tried.
        return True, None, "measure"
    for entry in pass_state["unplaced"]:
        if entry["task_id"] == task["id"]:
            if entry["category"] == "no_room":
                return False, None, "unplaced_room"
            return False, None, "unplaced_dependency"
    return True, None, "absent"


def apply_hard_deadlines(order, context):
    """Promote hard-deadline tasks that would miss their deadline, one at a time (§4).

    Parameters:
        order (list of str): the planning order after §0.
        context (dict): shared inputs.

    Returns:
        tuple (final_order, final_pass, promotions, deadline_misses).
    """
    current_order = list(order)
    current_pass = run_greedy_pass(current_order, context)
    promotions = []

    # Hard-deadline tasks in rank order (the order list is already by rank, with moves).
    for task_id in list(order):
        task = context["tasks_by_id"][task_id]
        deadline = task.get("deadline")
        if not deadline or not deadline.get("hard"):
            continue
        meets, _, category = task_meets_deadline(task, current_pass, context)
        if meets or category == "unplaced_dependency":
            continue

        # Try the task at the top of the order.
        trial_order = [task_id] + [other for other in current_order if other != task_id]
        trial_pass = run_greedy_pass(trial_order, context)
        trial_meets, _, _ = task_meets_deadline(task, trial_pass, context)
        if trial_meets:
            jumped = current_order[:current_order.index(task_id)]
            promotions.append({"task_id": task_id, "above": jumped,
                               "reason": "hard deadline %s (tie rule)" % deadline["date"]})
            current_order = trial_order
            current_pass = trial_pass

    # Misses that remain, hard and soft, from the final pass.
    deadline_misses = []
    for task_id in current_order:
        task = context["tasks_by_id"][task_id]
        deadline = task.get("deadline")
        if not deadline:
            continue
        meets, earliest_end, category = task_meets_deadline(task, current_pass, context)
        if meets:
            continue
        deadline_misses.append({"task_id": task_id, "deadline": deadline["date"], "hard": bool(deadline.get("hard")),
                                "earliest_end": format_iso(earliest_end, context["timezone"]) if earliest_end else None,
                                "note": "unplaced" if category.startswith("unplaced") else "placed too late"})

    return current_order, current_pass, promotions, deadline_misses


# ----------------------------------------------------------------------------------------------
# §5 Decisions, free windows, and the output files
# ----------------------------------------------------------------------------------------------

def build_decisions(blocks, unplaced, budget_warnings, tasks_by_id, now, timezone):
    """Write the numbered yes/no decisions (§5).

    Parameters:
        blocks (list of dict): the blocks in start order.
        unplaced (list of dict): unplaced tasks.
        budget_warnings (list of dict): budget overruns.
        tasks_by_id (dict): task cards.
        now (datetime.datetime): the planning clock ("submit now" vs "submit at ...").
        timezone (ZoneInfo): zone of the printed times.

    Returns:
        list of str: "D1: ...", "D2: ...".
    """
    decisions = []
    seen_groups = set()

    # One decision per block or per group of parts (same task, same machine).
    for block in blocks:
        group_key = (block["task_id"], block["machine"], block["kind"] == "measure")
        if group_key in seen_groups:
            continue
        seen_groups.add(group_key)
        group = [other for other in blocks if (other["task_id"], other["machine"], other["kind"] == "measure") == group_key]
        first_start = min(other["start"] for other in group)
        last_end = max(other["end"] for other in group)
        total_hours = round(sum(other["hours"] for other in group if other["kind"] != "submit"), 2)
        number = len(decisions) + 1
        if any(other["kind"] == "submit" for other in group):
            when = "now" if first_start <= now else "at %s" % format_when(first_start, timezone)
            decisions.append("D%d: submit %s to %s %s → start ≈ %s, end ≈ %s (%.1f h, %s) — yes/no" % (
                number, block["title"], block["machine"], when, format_when(group[-1]["start"], timezone),
                format_when(last_end, timezone), total_hours, block["basis"]))
        elif block["kind"] == "measure":
            decisions.append("D%d: measure %s on %s %s → %s (%.1f h: %s) — yes/no" % (
                number, block["title"], block["machine"], format_when(first_start, timezone),
                format_when(last_end, timezone), total_hours, block.get("measure_what", "")))
        else:
            parts = " in %d parts" % len(group) if len(group) > 1 else ""
            decisions.append("D%d: %s on %s %s → %s%s (%.1f h, %s) — yes/no" % (
                number, block["title"], block["machine"], format_when(first_start, timezone),
                format_when(last_end, timezone), parts, total_hours, block["basis"]))

    # One per unplaced task the user could unblock.
    for entry in unplaced:
        number = len(decisions) + 1
        title = tasks_by_id[entry["task_id"]].get("title", entry["task_id"])
        decisions.append("D%d: %s is unplaced (%s) — what do you want to change?" % (number, title, entry["reason"]))

    # One per budget overrun (the account is named when the settings give one).
    for entry in budget_warnings:
        number = len(decisions) + 1
        account_text = " (account %s)" % entry["account"] if entry.get("account") else ""
        decisions.append("D%d: %s on %s%s adds %.0f %s to %.0f used of %.0f allocated — accept the overrun? yes/no" % (
            number, entry["task_id"], entry["cluster"], account_text, entry["added"], entry["unit"], entry["used"],
            entry["allocated"]))

    return decisions


def remaining_free_windows(context, pass_state):
    """List, per workstation, the intervals where a GPU card is still free after the plan.

    A machine without a GPU lists the intervals where it is free (no block, no exception);
    its entries carry "gpu_free": false.

    Parameters:
        context (dict): shared inputs.
        pass_state (dict): the final pass (its occupants include the placed blocks).

    Returns:
        dict: machine name -> list of {"from", "to", "hours", "gpu_free": bool}.
    """
    result = {}
    for machine in context["machines"]:
        if machine["kind"] != "workstation":
            continue
        # The probe is an empty task: a GPU task on a GPU machine (one free card is enough),
        # a CPU task on a machine without GPU.
        has_gpu = machine.get("gpu") is not None
        probe = {"gpu": has_gpu, "gpu_memory_gb": 0.0, "ram_gb": 0.0, "threads": 0.0}
        windows = feasible_windows(machine, pass_state["occupants"][machine["name"]], probe,
                                   context["now"], context["horizon_to"])
        entries = []
        for window_start, window_end in windows:
            if window_end - window_start < MIN_PIECE:
                continue
            entries.append({"from": format_iso(window_start, context["timezone"]),
                            "to": format_iso(window_end, context["timezone"]),
                            "hours": hours_between(window_start, window_end), "gpu_free": has_gpu})
        result[machine["name"]] = entries
    return result


def serialize_block(block, timezone):
    """Convert a block dict with datetimes into the JSON form of plan.json.

    Parameters:
        block (dict): an internal block.
        timezone (ZoneInfo): zone of the printed times.

    Returns:
        dict: the same facts with ISO times, keys in the order of formats.md §4.
    """
    output = {"task_id": block["task_id"], "rank": block["rank"], "title": block["title"], "kind": block["kind"],
              "machine": block["machine"], "start": format_iso(block["start"], timezone),
              "end": format_iso(block["end"], timezone),
              "hours": block["hours"]}
    if "part" in block:
        output["part"] = block["part"]
    output["basis"] = block["basis"]
    output["source"] = block["source"]
    output["deadline_ok"] = block["deadline_ok"]
    output["reason"] = block["reason"]
    output["warnings"] = list(block["warnings"])
    if "alternative" in block:
        output["alternative"] = block["alternative"]
    if "measure_what" in block:
        output["measure_what"] = block["measure_what"]
    if "wait_hours" in block:
        output["wait_hours"] = block["wait_hours"]
    return output


def footer_lists(tasks):
    """Collect the tasks that are listed but never placed (§0): unranked, running, done, parked.

    Parameters:
        tasks (list of dict): tasks[].

    Returns:
        dict: {"unranked": [ids], "running": [ids], "done": [ids], "parked": [ids]}.
    """
    footer = {"unranked": [], "running": [], "done": [], "parked": []}
    for task in tasks:
        state = task.get("state")
        if state in PLACEABLE_STATES and task.get("rank") is None:
            footer["unranked"].append(task["id"])
        elif state in ("running", "done", "parked"):
            footer[state].append(task["id"])
    return footer


def block_label(block, timezone):
    """Return the run label of a block in status_plan.json ("<rank>. <title>").

    Parameters:
        block (dict): an internal block.
        timezone (ZoneInfo): zone of the printed start time of a submit block.

    Returns:
        str: the label; measure blocks "<rank>. measure: <what>"; submit blocks
            "<rank>. submit … → start ≈ …"; accepted estimates get a "?".
    """
    rank = block["rank"]
    if block["kind"] == "measure":
        return "%d. measure: %s" % (rank, block.get("measure_what", ""))
    if block["kind"] == "submit":
        return "%d. submit %s → start ≈ %s" % (rank, block["title"], format_when(block["end"], timezone))
    label = "%d. %s" % (rank, block["title"])
    if "part" in block:
        label = label + " (" + block["part"] + ")"
    if block["basis"] not in TRUSTED_BASES:
        label = label + " ?"
    return label


def short_block_label(block, timezone):
    """Return the SHORT label written above a proposed bar in the figure.

    The task's name is already the stage name ("PLAN · <rank>. <title>"), so the bar itself only says what
    kind of block it is: "measure 1.0 h", "submit → start ≈ Fri Oct 30 05:00", "(1/2)" for a part, or
    "20.0 h measured" for a whole run. A long label here collides with its neighbours in the figure, which is
    why the full label (`block_label`) is kept for plan.json only.

    Parameters:
        block (dict): an internal block.
        timezone (ZoneInfo): zone of the printed start time of a submit block.

    Returns:
        str: the short label; accepted estimates get a "?".
    """
    if block["kind"] == "measure":
        return "measure %.1f h" % block["hours"]
    if block["kind"] == "submit":
        return "submit → start ≈ %s" % format_when(block["end"], timezone)
    if "part" in block:
        label = "(" + block["part"] + ")"
    else:
        label = "%.1f h %s" % (block["hours"], block["basis"])
    if block["basis"] not in TRUSTED_BASES:
        label = label + " ?"
    return label


def build_status_plan(commitments_file, blocks, machines_by_name, generated_text, timezone):
    """Append the proposed blocks to a copy of the commitments file (formats.md §4).

    One stage per task and machine, named "PLAN · <rank>. <title>" (the run-status stage name), with the
    machine name on its `session` line and the lane colour of that machine (`model` = machine label). Its
    runs are the block(s) of that task there: parts, a measure block, or the submit + run pair of a
    cluster job. One stage per machine (the first design) put several bars in one row, and their labels
    collided; one row per task keeps every label readable.

    Parameters:
        commitments_file (dict): the run-status JSON.
        blocks (list of dict): the blocks in start order.
        machines_by_name (dict): name -> machine entry (for the lane labels).
        generated_text (str): ISO time of the plan, for the note.
        timezone (ZoneInfo): zone of the written times.

    Returns:
        dict: the status_plan JSON.
    """
    status_plan = copy.deepcopy(commitments_file)
    stages = status_plan["stages"]

    for block in blocks:
        label = machines_by_name[block["machine"]]["label"]
        stage_name = "%s%d. %s" % (PLAN_STAGE_PREFIX, block["rank"], block["title"])

        # Find this task's plan stage on this lane, or insert one after the last stage of that lane
        # (plan stages included, so tasks keep their start order within the lane).
        plan_stage = None
        for stage in stages:
            if stage.get("name") == stage_name and stage.get("model") == label:
                plan_stage = stage
                break
        if plan_stage is None:
            plan_stage = {"name": stage_name, "model": label, "session": block["machine"], "runs": []}
            last_index = -1
            for index, stage in enumerate(stages):
                if stage.get("model") == label:
                    last_index = index
            # A lane with stages gets its plan stage right after them; a lane that has no
            # stage yet (no commitment on that machine) is appended at the bottom.
            if last_index == -1:
                stages.append(plan_stage)
            else:
                stages.insert(last_index + 1, plan_stage)

        run_name = block["task_id"]
        if block["kind"] in ("measure", "submit"):
            run_name = run_name + " " + block["kind"]
        if "part" in block:
            run_name = run_name + " part " + block["part"]
        run_label = short_block_label(block, timezone)

        # A cluster job is drawn as two bars: the queue wait ("submit → start ≈ …") and the run right after
        # it. Both labels start at the left end of their own bar, so after a short queue wait (2 h on a CPU
        # partition, for example) the run's label is written over the submit label. The two labels are
        # therefore joined on the submit bar ("submit → start ≈ Wed Oct 30 19:00 · 45.0 h measured") and the
        # run bar gets an empty label (the renderer then writes nothing above it).
        # DEBUG CHECK: in status_plan.json, a submit run's label holds " · " and the next run's label is "".
        previous_run = plan_stage["runs"][-1] if plan_stage["runs"] else None
        follows_its_submit = (previous_run is not None and block["kind"] != "submit"
                              and previous_run["name"] == block["task_id"] + " submit")
        if follows_its_submit:
            previous_run["label"] = previous_run["label"] + " · " + run_label
            run_label = ""
        plan_stage["runs"].append({"name": run_name, "label": run_label, "status": "queued",
                                   "proposed": True, "estimate_start": format_iso(block["start"], timezone),
                                   "estimate_end": format_iso(block["end"], timezone), "note": block["basis"]})

    # A footer note so the figure says where the hatched bars come from.
    notes = status_plan.get("notes") or []
    notes.append("Hatched bars: blocks proposed by compute-planner (plan generated %s), not booked." % generated_text)
    status_plan["notes"] = notes
    return status_plan


def build_plan_markdown(plan, tasks_by_id, machines_by_name, system):
    """Write plan.md: the plan's facts as short Markdown for the user (formats.md §4).

    Parameters:
        plan (dict): the plan.json content.
        tasks_by_id (dict): task cards.
        machines_by_name (dict): machine entries.
        system (str): system name for paths.

    Returns:
        str: the Markdown text.
    """
    lines = []
    lines.append("# Compute plan %s" % plan["generated"])
    lines.append("")
    lines.append("Horizon: %s → %s. Inputs: `%s:%s`, `%s:%s`, `%s:%s`." % (
        plan["horizon"]["from"], plan["horizon"]["to"], system, plan["inputs"]["tasks"], system,
        plan["inputs"]["machines"], system, plan["inputs"]["commitments"]))
    lines.append("")

    # Blocks table.
    lines.append("## Blocks")
    lines.append("")
    lines.append("| Rank | Task | Machine | From → to | Hours | Basis + source | Why here |")
    lines.append("|---|---|---|---|---|---|---|")
    for block in plan["blocks"]:
        task_text = block["title"]
        if block["kind"] == "measure":
            task_text = "measure: " + block.get("measure_what", "") + " (" + block["title"] + ")"
        if block["kind"] == "submit":
            task_text = "submit: " + block["title"]
        if block.get("part"):
            task_text = task_text + " (part " + block["part"] + ")"
        basis_text = block["basis"]
        if block["source"]:
            basis_text = basis_text + ": " + block["source"]
        why = block["reason"]
        if block["warnings"]:
            why = why + "; " + "; ".join(block["warnings"])
        if block["deadline_ok"] is False:
            why = "MISSES DEADLINE; " + why
        lines.append("| %s | %s | %s | %s → %s | %.1f | %s | %s |" % (
            block["rank"], task_text, block["machine"], block["start"], block["end"], block["hours"], basis_text, why))
    if not plan["blocks"]:
        lines.append("| – | nothing placed | | | | | |")
    lines.append("")

    # Unplaced, misses, promotions, budget.
    lines.append("## Unplaced")
    lines.append("")
    for entry in plan["unplaced"]:
        lines.append("- %s: %s" % (entry["task_id"], entry["reason"]))
    if not plan["unplaced"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Waiting for measurement")
    lines.append("")
    for entry in plan["waiting_for_measurement"]:
        lines.append("- %s: production block after the measure block (block index %d)" % (entry["task_id"], entry["measure_block"]))
    if not plan["waiting_for_measurement"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Deadline misses")
    lines.append("")
    for entry in plan["deadline_misses"]:
        lines.append("- %s: %s deadline %s, earliest end %s (%s)" % (
            entry["task_id"], "hard" if entry["hard"] else "soft", entry["deadline"], entry["earliest_end"], entry["note"]))
    if not plan["deadline_misses"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Promotions (hard deadlines)")
    lines.append("")
    for entry in plan["promotions"]:
        lines.append("- %s jumped %s: %s" % (entry["task_id"], ", ".join(entry["above"]) or "nobody", entry["reason"]))
    if not plan["promotions"]:
        lines.append("- none")
    lines.append("")
    lines.append("## Budget warnings")
    lines.append("")
    for entry in plan["budget_warnings"]:
        lines.append("- %s on %s%s: used %.0f + added %.0f > allocated %.0f %s" % (
            entry["task_id"], entry["cluster"], " (account %s)" % entry["account"] if entry.get("account") else "",
            entry["used"], entry["added"], entry["allocated"], entry["unit"]))
    if not plan["budget_warnings"]:
        lines.append("- none")
    lines.append("")

    # Free windows left.
    lines.append("## Free GPU windows left")
    lines.append("")
    for machine_name, windows in plan["free_windows"].items():
        if windows:
            texts = ["%s → %s (%.1f h)" % (window["from"], window["to"], window["hours"]) for window in windows]
            lines.append("- %s: %s" % (machine_name, "; ".join(texts)))
        else:
            lines.append("- %s: none" % machine_name)
    lines.append("")

    # Machine rules, printed for the reader (with the Slurm accounts of a cluster, when known).
    lines.append("## Machine rules")
    lines.append("")
    for machine_name, machine in machines_by_name.items():
        rules = list(machine.get("rules") or [])
        if machine.get("accounts"):
            rules.append("accounts: " + ", ".join(machine["accounts"]))
        if rules:
            lines.append("- %s: %s" % (machine_name, "; ".join(rules)))
    lines.append("")

    # Order notes and the footer lists.
    if plan["notes"]:
        lines.append("## Notes")
        lines.append("")
        for note in plan["notes"]:
            lines.append("- %s" % note)
        lines.append("")
    footer = plan["not_placed"]
    lines.append("## Not placed by design")
    lines.append("")
    for key in ("unranked", "running", "done", "parked"):
        lines.append("- %s: %s" % (key, ", ".join(footer[key]) or "none"))
    lines.append("")

    # Decisions.
    lines.append("## Decisions")
    lines.append("")
    for decision in plan["decisions"]:
        lines.append("- %s" % decision)
    if not plan["decisions"]:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def write_text(path, text):
    """Write a text file in UTF-8.

    Parameters:
        path (str): the file (its folder must exist).
        text (str): the content.
    """
    with open(path, mode="w", encoding="utf-8") as text_file:
        text_file.write(text)


def write_json(path, content):
    """Write a JSON file, indented, keys in insertion order, non-ASCII characters kept.

    Parameters:
        path (str): the file.
        content (dict): the JSON object.
    """
    # `ensure_ascii=False` keeps "·" and "→" readable; `indent=1` is what the status files use.
    with open(path, mode="w", encoding="utf-8") as json_file:
        json.dump(content, json_file, indent=1, ensure_ascii=False)
        json_file.write("\n")


# ----------------------------------------------------------------------------------------------
# The planner: from the three files to the plan dict
# ----------------------------------------------------------------------------------------------

def make_plan(tasks_file, machines_file, commitments_file, now, days, input_paths):
    """Run the whole algorithm and return the plan and the status_plan dicts.

    Parameters:
        tasks_file, machines_file, commitments_file (dict): the parsed inputs.
        now (datetime.datetime or None): the planning clock; None = current time.
        days (float): the horizon length in days.
        input_paths (dict): {"tasks", "machines", "commitments"} absolute paths, recorded.

    Returns:
        tuple (plan, status_plan): the two output dicts (plan.json and status_plan.json).
    """
    # §0 Clock and horizon.
    timezone_name = machines_file.get("timezone") or DEFAULT_TIMEZONE_NAME
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        raise PlannerInputError("machines file: unknown timezone %r" % timezone_name)
    # `now` in UTC (parse_moment converts); the horizon is `days` × 24 real hours later.
    if now is None:
        now = datetime.datetime.now(UTC).replace(microsecond=0)
    else:
        now = parse_moment(now, timezone, "--now")
    horizon_to = now + hours_to_delta(float(days) * 24.0)

    # Shared context for every pass.
    machines = machines_file["machines"]
    machines_by_name = {machine["name"]: machine for machine in machines}
    tasks_by_id = {task["id"]: task for task in tasks_file["tasks"]}
    base = {}
    for machine in machines:
        if machine["kind"] == "workstation":
            base[machine["name"]] = base_occupants(machine, commitments_file, now, horizon_to, timezone)
    context = {"now": now, "horizon_to": horizon_to, "timezone": timezone, "machines": machines,
               "machines_by_name": machines_by_name, "tasks_by_id": tasks_by_id,
               "commitments_file": commitments_file, "base_occupants": base,
               "prefer_cluster_for_cpu": machines_file.get("prefer_cluster_for_cpu") or ""}

    # §0 Order, §2 greedy, §4 deadlines.
    order, moves = ordered_task_ids(tasks_file["tasks"])
    final_order, final_pass, promotions, deadline_misses = apply_hard_deadlines(order, context)

    # Blocks in start order (then rank, for blocks starting together).
    blocks = sorted(final_pass["blocks"], key=lambda block: (block["start"], block["rank"], block["kind"] != "submit"))

    # Re-point waiting_for_measurement at the sorted indices.
    waiting = []
    for entry in final_pass["waiting_for_measurement"]:
        for index, block in enumerate(blocks):
            if block["task_id"] == entry["task_id"] and block["kind"] == "measure":
                waiting.append({"task_id": entry["task_id"], "measure_block": index})
                break

    generated_text = format_iso(datetime.datetime.now(UTC), timezone)
    plan = {"schema": PLAN_SCHEMA, "generated": generated_text,
            "timezone": timezone_name,
            "horizon": {"from": format_iso(now, timezone), "to": format_iso(horizon_to, timezone)},
            "inputs": dict(input_paths),
            "order": final_order,
            "blocks": [serialize_block(block, timezone) for block in blocks],
            "unplaced": [{"task_id": entry["task_id"], "reason": entry["reason"]} for entry in final_pass["unplaced"]],
            "waiting_for_measurement": waiting,
            "deadline_misses": deadline_misses,
            "promotions": promotions,
            "budget_warnings": final_pass["budget_warnings"],
            "free_windows": remaining_free_windows(context, final_pass),
            "decisions": build_decisions(blocks, final_pass["unplaced"], final_pass["budget_warnings"], tasks_by_id,
                                         now, timezone),
            "notes": list(moves) + ["machines file: " + str(warning) for warning in machines_file.get("warnings") or []],
            "not_placed": footer_lists(tasks_file["tasks"])}

    status_plan = build_status_plan(commitments_file, blocks, machines_by_name, generated_text, timezone)

    # DEBUG CHECK: every id in `plan["order"]` is either in a block, in `unplaced`, or both
    # (a measure block AND waiting_for_measurement).
    return plan, status_plan


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def parse_arguments(argument_list=None):
    """Define and parse the command-line arguments.

    Parameters:
        argument_list (list of str or None): the arguments; None = sys.argv[1:].

    Returns:
        argparse.Namespace: tasks, machines, commitments, output_dir, now, days, overwrite, dry_run,
        print_plan.
    """
    parser = argparse.ArgumentParser(
        description="Propose where and when the pending compute tasks run (compute-planner skill).",
        epilog="Formats: references/formats.md. Rules: references/planning_rules.md.")
    parser.add_argument("--tasks", required=True, help="compute_tasks.json (schema compute-tasks/1).")
    parser.add_argument("--machines", required=True, help="machines.json (schema compute-machines/1).")
    parser.add_argument("--commitments", required=True, help="run-status JSON of the current timeline (run-status/1).")
    parser.add_argument("-o", "--output-dir", required=True, help="Folder for plan.json, plan.md, status_plan.json.")
    parser.add_argument("--now", default=None, help="Planning clock, ISO 8601 (default: the current time).")
    parser.add_argument("--days", type=float, default=7.0, help="Horizon length in days (default 7).")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing output files.")
    # `--dry-run` plans and prints plan.md but writes no file, so a check of the inputs never leaves a
    # half-finished plan folder behind.
    parser.add_argument("--dry-run", action="store_true", help="Plan and print plan.md; write nothing.")
    parser.add_argument("--print", dest="print_plan", action="store_true", help="Also print plan.md to stdout.")
    return parser.parse_args(argument_list)


def main(argument_list=None):
    """Entry point: read the inputs, plan, write the three outputs.

    Parameters:
        argument_list (list of str or None): for tests; None = the real command line.

    Returns:
        int: the exit status (0 success, 1 input or overwrite problem).
    """
    arguments = parse_arguments(argument_list)
    try:
        # Output files are checked first: refusing late would waste the planning.
        output_dir = os.path.abspath(arguments.output_dir)
        output_paths = {name: os.path.join(output_dir, name) for name in ("plan.json", "plan.md", "status_plan.json")}
        for path in output_paths.values():
            if os.path.exists(path) and not arguments.overwrite and not arguments.dry_run:
                raise PlannerInputError("output exists, use --overwrite: %s" % path)

        tasks_file, machines_file, commitments_file = load_inputs(arguments.tasks, arguments.machines, arguments.commitments)
        input_paths = {"tasks": os.path.abspath(arguments.tasks), "machines": os.path.abspath(arguments.machines),
                       "commitments": os.path.abspath(arguments.commitments)}
        plan, status_plan = make_plan(tasks_file, machines_file, commitments_file, arguments.now, arguments.days, input_paths)

        machines_by_name = {machine["name"]: machine for machine in machines_file["machines"]}
        tasks_by_id = {task["id"]: task for task in tasks_file["tasks"]}
        plan_markdown = build_plan_markdown(plan, tasks_by_id, machines_by_name, system_name())

        # With --dry-run the plan is only printed (below); no folder or file is created.
        if not arguments.dry_run:
            # `os.makedirs(..., exist_ok=True)` creates the folder and its parents, no error if present.
            os.makedirs(output_dir, exist_ok=True)
            write_json(output_paths["plan.json"], plan)
            write_text(output_paths["plan.md"], plan_markdown)
            write_json(output_paths["status_plan.json"], status_plan)
    except PlannerInputError as error:
        print("plan_schedule.py: %s" % error, file=sys.stderr)
        return 1

    if arguments.print_plan or arguments.dry_run:
        print(plan_markdown)
    # The summary line names the folder, or says that nothing was written.
    destination = "(dry run, nothing written)" if arguments.dry_run else output_dir
    print("plan: %d blocks, %d unplaced, %d deadline misses, %d promotions -> %s" % (
        len(plan["blocks"]), len(plan["unplaced"]), len(plan["deadline_misses"]), len(plan["promotions"]), destination))
    return 0


if __name__ == "__main__":
    sys.exit(main())
