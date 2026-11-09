#!/usr/bin/env python3
"""
planner_settings.py - the compute-planner's view of the workhub-claude settings, and the machines
file built from them.

PURPOSE
    The compute planner (plan_schedule.py) is a pure function of three JSON files: the task cards,
    the machines file and the commitments (what already runs). Everything personal about the
    machines lives in ONE settings file written by the work-hub-setup skill
    (~/.config/workhub-claude/config.json, or $WORKHUB_CLAUDE_CONFIG): the workstations with their
    GPUs, RAM, CPUs and weekly usage windows (machines.workstations[]), the clusters
    (machines.clusters[]), the owner's time zone, and the planner's own options
    (skill_settings.compute-planner.*). Skills never write that file.

    Facts the planner learns while it works (a measured queue wait on a cluster, a maintenance
    outage, a one-off block because another person uses a shared machine tonight, the budget used
    so far) change from week to week, so they live in the planner's own file
    <planner_dir>/machine_facts.json (schema "compute-machine-facts/1", format in
    references/formats.md §2b), which the skill edits after showing the evidence.

    This script joins the two into machines.json (schema "compute-machines/1"), the exact input
    plan_schedule.py expects, and answers small questions about the planner's settings
    (where the planner folder is, the horizon, the time stamp of a new plan folder).

INPUTS
    The settings file (found by workhub_config.py: $WORKHUB_CLAUDE_CONFIG or the default path).
    --facts   <planner_dir>/machine_facts.json (optional; a missing file means "no facts yet").

SETTINGS READ (defaults in DEFAULTS below; meaning in references/formats.md §2a)
    owner.time_zone, paths.outputs_dir, runtime.python, workflow.orchestrator.enabled / .folder,
    machines.workstations[], machines.clusters[],
    skill_settings.compute-planner.{planner_dir, horizon_days, ram_heavy_gb, shared_machines,
    shared_cpu_percent, cluster_budgets, prefer_cluster_for_cpu}.

SUBCOMMANDS
    show                     the resolved planner settings (defaults applied), as JSON.
    get KEY                  one resolved value; KEY is a planner setting above or one of
                             time_zone, python, orchestrator_enabled, orchestrator_folder,
                             skills_root (the folder that holds this skill and its sibling skills).
    stamp                    the current time as YYYY-MM-DD_HHMM in owner.time_zone (plan folder name).
    machines -o FILE         build machines.json from the settings and --facts.
             [--facts F] [--overwrite] [--dry-run] [--print]

USAGE
    python3 planner_settings.py get planner_dir
    python3 planner_settings.py stamp
    python3 planner_settings.py machines --facts ~/workhub/outputs/compute-planner/machine_facts.json \\
        -o ~/workhub/outputs/compute-planner/plans/2030-01-07_0900/machines.json --print

OUTPUTS
    Text on standard output; `machines` writes FILE (refused when it exists, unless --overwrite;
    nothing is written with --dry-run, the result is printed instead).

DEPENDENCIES
    Python >= 3.9 (`zoneinfo`), standard library only, plus workhub_config.py from the same folder.

ASSUMPTIONS
    - A usage window's `rule` must be "blocked" (or empty, read as "blocked"): the planner places
      nothing in it. Time outside every blocked window is free. Any other rule word stops the build
      with an explanation, because guessing what it means could put work on another person's
      machine at the wrong time.
    - A workstation with several GPUs is planned as `count` cards that each take one task. When the
      cards differ, every card is treated as the one with the least memory (conservative), and a
      rule line says so.
    - An unknown budget `used` value counts as 0 in plan_schedule.py; the machines file then carries
      a warning so the reader knows the check is incomplete.

EXIT STATUS
    0 = success; 1 = the settings or facts file is missing, malformed, or holds a value the planner
    cannot use, or FILE exists without --overwrite; 2 = invalid command-line arguments (argparse).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library, plus the kit's settings reader)
# ----------------------------------------------------------------------------------------------

# `argparse` parses the subcommands; it prints --help and exits with status 2 on invalid input.
import argparse

# `datetime.datetime.now(zone)` gives the current moment in the owner's zone (stamp, `updated`).
import datetime

# `json` reads the facts file and writes machines.json.
import json

# `os` gives path helpers and `os.makedirs`.
import os

# `sys` gives `sys.exit`, `sys.stderr` and `sys.path` (see the next block).
import sys

# `ZoneInfo("Europe/Lisbon")` is a time-zone object with that zone's daylight-saving rules.
from zoneinfo import ZoneInfo

# workhub_config.py sits in the same folder as this script. Python puts the folder of the script
# being RUN on sys.path, but not the folder of a script loaded by a test with importlib, so the
# folder is added explicitly. `insert(0, ...)` puts it first, so the kit's copy is the one found.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# load_config: reads the settings file (raises ConfigError when it is missing);
# get_setting(config, "a.b"): one nested value or a default; skill_setting(config, skill, key, d):
# config["skill_settings"][skill][key] or d; time_zone(config): owner.time_zone or "UTC";
# workstations(config) / clusters(config): the lists under machines.*; expand_path: ~ and $VARS;
# config_path(): where the settings file is (recorded in machines.json `built_from`).
from workhub_config import (ConfigError, clusters, config_path, expand_path, get_setting,  # noqa: E402
                            load_config, skill_setting, time_zone, workstations)


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# The skill's name: its options live under skill_settings.compute-planner in the settings file.
SKILL_NAME = "compute-planner"

# Schema names of the facts file (read) and the machines file (written).
FACTS_SCHEMA = "compute-machine-facts/1"
MACHINES_SCHEMA = "compute-machines/1"

# The planner's options and their defaults. `None` for planner_dir means "derived from
# paths.outputs_dir" (see resolved_settings).
DEFAULTS = {
    # Folder of compute_tasks.json, machine_facts.json, plans/<stamp>/ and planner_log.jsonl.
    "planner_dir": None,
    # Length of the plan in days.
    "horizon_days": 7,
    # One job at or above this many GB of RAM per workstation at a time; None = no such rule.
    "ram_heavy_gb": None,
    # Workstations shared with other people: busy while another user runs a GPU process or keeps
    # the CPU above shared_cpu_percent (the skill checks this live and writes an exception).
    "shared_machines": [],
    "shared_cpu_percent": 30,
    # Allocations per cluster: {"<cluster>": {"unit": "gpu-hours", "allocated": 2000,
    # "applies_to": ["h100_80gb"], "account": "<slurm account>"}}.
    "cluster_budgets": {},
    # Name of the cluster where CPU-only work goes whenever it can run there; "" = no preference.
    "prefer_cluster_for_cpu": "",
}

# Default outputs folder when paths.outputs_dir is not set (the same default as the kit's example).
DEFAULT_OUTPUTS_DIR = "~/workhub/outputs"

# Day names in Python's weekday() order (Mon = 0). machines.json uses these three-letter names.
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Words accepted for whole groups of days in a usage window's `days` text.
DAY_GROUPS = {
    "daily": WEEKDAY_NAMES,
    "every day": WEEKDAY_NAMES,
    "all": WEEKDAY_NAMES,
    "weekdays": WEEKDAY_NAMES[:5],
    "weekend": WEEKDAY_NAMES[5:],
    "weekends": WEEKDAY_NAMES[5:],
}

# The only usage-window rule the planner understands (an empty rule means the same).
BLOCKED_RULE = "blocked"


# ----------------------------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------------------------

class PlannerSettingsError(Exception):
    """A settings or facts value the planner cannot use, or an output exists (exit status 1)."""


# ----------------------------------------------------------------------------------------------
# Resolved settings
# ----------------------------------------------------------------------------------------------

def resolved_settings(config):
    """Return the planner's options with the defaults applied and paths expanded.

    Parameters:
        config (dict): the settings, from workhub_config.load_config().

    Returns:
        dict: every key of DEFAULTS. Example:
            {"planner_dir": "/home/alex/workhub/outputs/compute-planner", "horizon_days": 7, ...}

    Raises:
        PlannerSettingsError: a value has the wrong type.
    """
    resolved = {}
    for key, default in DEFAULTS.items():
        # skill_setting returns the default when the key is missing, None or "".
        resolved[key] = skill_setting(config, SKILL_NAME, key, default)

    # planner_dir: given, else <paths.outputs_dir>/compute-planner (the kit's rule for outputs).
    if not resolved["planner_dir"]:
        outputs_dir = get_setting(config, "paths.outputs_dir", default=DEFAULT_OUTPUTS_DIR)
        resolved["planner_dir"] = os.path.join(outputs_dir, SKILL_NAME)
    # `expand_path` turns "~/x" into "/home/<user>/x"; `abspath` removes "..".
    resolved["planner_dir"] = os.path.abspath(expand_path(resolved["planner_dir"]))

    # Type checks: a wrong type here would silently change a plan.
    horizon = resolved["horizon_days"]
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float)) or horizon <= 0:
        raise PlannerSettingsError("skill_settings.compute-planner.horizon_days must be a positive number")
    heavy = resolved["ram_heavy_gb"]
    if heavy is not None and (isinstance(heavy, bool) or not isinstance(heavy, (int, float))):
        raise PlannerSettingsError("skill_settings.compute-planner.ram_heavy_gb must be a number or null")
    if not isinstance(resolved["shared_machines"], list):
        raise PlannerSettingsError("skill_settings.compute-planner.shared_machines must be a list of names")
    if not isinstance(resolved["cluster_budgets"], dict):
        raise PlannerSettingsError("skill_settings.compute-planner.cluster_budgets must be an object")
    if not isinstance(resolved["prefer_cluster_for_cpu"], str):
        raise PlannerSettingsError("skill_settings.compute-planner.prefer_cluster_for_cpu must be a text")

    # DEBUG CHECK: `resolved["planner_dir"]` is an absolute path; `resolved["horizon_days"]` is 7 by default.
    return resolved


def derived_value(config, key):
    """Return one value that SKILL.md asks for: a planner option or a derived value.

    Parameters:
        config (dict): the settings.
        key (str): a key of DEFAULTS, or time_zone, python, orchestrator_enabled,
            orchestrator_folder, skills_root.

    Returns:
        the value (str, number, list, dict or bool).

    Raises:
        PlannerSettingsError: the key is unknown.
    """
    if key in DEFAULTS:
        return resolved_settings(config)[key]
    if key == "time_zone":
        return time_zone(config)
    if key == "python":
        # The interpreter that has matplotlib (used to draw the plan with run-status-figure).
        return get_setting(config, "runtime.python", default="python3")
    if key == "orchestrator_enabled":
        return bool(get_setting(config, "workflow.orchestrator.enabled", default=False))
    if key == "orchestrator_folder":
        folder = get_setting(config, "workflow.orchestrator.folder", default="")
        return os.path.abspath(expand_path(folder)) if folder else ""
    if key == "skills_root":
        # scripts/ -> compute-planner/ -> the folder that holds every kit skill side by side.
        return os.path.dirname(os.path.dirname(SCRIPT_DIR))
    raise PlannerSettingsError("unknown key %r; known: %s" % (
        key, ", ".join(list(DEFAULTS) + ["time_zone", "python", "orchestrator_enabled", "orchestrator_folder",
                                         "skills_root"])))


def stamp_now(config):
    """Return the current time as YYYY-MM-DD_HHMM in owner.time_zone (the name of a plan folder)."""
    zone = owner_zone(config)
    # `strftime` codes: %Y year, %m month, %d day, %H hour (24 h), %M minute.
    return datetime.datetime.now(zone).strftime("%Y-%m-%d_%H%M")


def owner_zone(config):
    """Return the owner's ZoneInfo; raise PlannerSettingsError for an unknown zone name."""
    zone_name = time_zone(config)
    try:
        return ZoneInfo(zone_name)
    except Exception:
        raise PlannerSettingsError("owner.time_zone %r is not an IANA time zone (for example Europe/Lisbon)" % zone_name)


# ----------------------------------------------------------------------------------------------
# Usage windows -> weekly blocked periods
# ----------------------------------------------------------------------------------------------

def parse_days(days, where):
    """Turn a usage window's `days` into a list of three-letter day names.

    Accepted forms (case does not matter; full names are cut to their first three letters):
        ["Mon", "Tue"]     a list of names
        "Mon-Fri"          a range; "Fri-Mon" wraps over the weekend (Fri, Sat, Sun, Mon)
        "Mon,Wed,Fri-Sun"  comma-separated names and ranges
        "daily", "every day", "all", "weekdays", "weekend", "weekends"

    Parameters:
        days (str or list): the value from the settings.
        where (str): the window's place, for the error message ("ws02 usage_windows[0]").

    Returns:
        list of str: unique day names in week order, e.g. ["Mon", "Tue", "Wed", "Thu", "Fri"].

    Raises:
        PlannerSettingsError: a part is not a day name or a known group.
    """
    # A list is joined into the text form so both forms go through the same parser.
    if isinstance(days, list):
        text = ",".join(str(item) for item in days)
    else:
        text = str(days or "")
    if not text.strip():
        raise PlannerSettingsError("%s: 'days' is empty" % where)

    chosen = set()
    for raw_part in text.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        if part in DAY_GROUPS:
            chosen.update(DAY_GROUPS[part])
            continue
        # "mon-fri" -> ["mon", "fri"]; a single name gives a list of one.
        ends = [end.strip() for end in part.split("-")]
        names = []
        for end in ends:
            # "monday"[:3] -> "mon" -> "Mon" (`capitalize` upper-cases the first letter only).
            short_name = end[:3].capitalize()
            if short_name not in WEEKDAY_NAMES:
                raise PlannerSettingsError("%s: %r is not a day name (use Mon..Sun, a range Mon-Fri, or daily)"
                                           % (where, raw_part.strip()))
            names.append(short_name)
        if len(names) == 1:
            chosen.add(names[0])
        elif len(names) == 2:
            # Walk from the first day to the last, wrapping after Sunday (`% 7` keeps 0..6).
            index = WEEKDAY_NAMES.index(names[0])
            last_index = WEEKDAY_NAMES.index(names[1])
            chosen.add(WEEKDAY_NAMES[index])
            while index != last_index:
                index = (index + 1) % 7
                chosen.add(WEEKDAY_NAMES[index])
        else:
            raise PlannerSettingsError("%s: %r has more than one '-'" % (where, raw_part.strip()))

    # Back to week order (a set has no order).
    ordered = [name for name in WEEKDAY_NAMES if name in chosen]
    # DEBUG CHECK: parse_days("Mon-Fri", "x") == ["Mon", "Tue", "Wed", "Thu", "Fri"].
    return ordered


def check_clock(text, where):
    """Check an "HH:MM" clock time (00:00 to 23:59) and return it unchanged.

    Raises:
        PlannerSettingsError: the text is not HH:MM.
    """
    parts = str(text or "").split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise PlannerSettingsError("%s must be HH:MM, got %r" % (where, text))
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59:
        raise PlannerSettingsError("%s must be between 00:00 and 23:59, got %r" % (where, text))
    return "%02d:%02d" % (hour, minute)


def blocked_periods(machine):
    """Convert a workstation's usage_windows into machines.json `blocked` entries.

    Example: {"days": "Mon-Fri", "from": "09:00", "to": "17:00", "rule": "blocked",
              "reason": "shared with other lab members during the day"}
          -> {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "from": "09:00", "to": "17:00",
              "reason": "shared with other lab members during the day"}
    A window whose "to" is at or before its "from" crosses midnight (plan_schedule.py handles it).

    Parameters:
        machine (dict): one entry of machines.workstations[].

    Returns:
        list of dict: the blocked periods.

    Raises:
        PlannerSettingsError: a rule other than "blocked", or bad days or clock times.
    """
    periods = []
    windows = machine.get("usage_windows") or []
    if not isinstance(windows, list):
        raise PlannerSettingsError("workstation %s: usage_windows must be a list" % machine.get("name"))
    for index, window in enumerate(windows):
        where = "workstation %s usage_windows[%d]" % (machine.get("name"), index)
        if not isinstance(window, dict):
            raise PlannerSettingsError("%s must be an object {days, from, to, rule, reason}" % where)
        rule = str(window.get("rule") or BLOCKED_RULE).strip().lower()
        if rule != BLOCKED_RULE:
            raise PlannerSettingsError(
                "%s has rule %r; the planner knows only \"blocked\" (time outside blocked windows is free). "
                "Describe the times the machine must NOT be used as blocked windows (say \"set up my work hub\")."
                % (where, window.get("rule")))
        reason = str(window.get("reason") or "blocked in the settings")
        periods.append({"days": parse_days(window.get("days"), where),
                        "from": check_clock(window.get("from"), where + " 'from'"),
                        "to": check_clock(window.get("to"), where + " 'to'"),
                        "reason": reason})
    return periods


# ----------------------------------------------------------------------------------------------
# Building one machine entry
# ----------------------------------------------------------------------------------------------

def number_or_none(value, where):
    """Return a number field as float, None when it is missing; raise for a non-number."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlannerSettingsError("%s must be a number, got %r" % (where, value))
    return float(value)


def gpu_summary(machine):
    """Summarize a workstation's gpus[] as the planner's one `gpu` object (or None for no GPU).

    Example: [{"model": "RTX 4090", "memory_gb": 24, "count": 2}]
          -> ({"model": "RTX 4090", "memory_gb": 24.0, "count": 2}, [])
    Mixed cards [{"model": "A", "memory_gb": 24}, {"model": "B", "memory_gb": 12}]
          -> ({"model": "A + B", "memory_gb": 12.0, "count": 2}, ["mixed GPU models: ..."])

    Parameters:
        machine (dict): one entry of machines.workstations[].

    Returns:
        tuple (gpu or None, notes): `notes` are rule lines for the plan.
    """
    name = machine.get("name")
    entries = machine.get("gpus") or []
    if not isinstance(entries, list):
        raise PlannerSettingsError("workstation %s: gpus must be a list" % name)

    total_count = 0
    models = []
    memories = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PlannerSettingsError("workstation %s gpus[%d] must be an object {model, memory_gb, count}" % (name, index))
        count = entry.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PlannerSettingsError("workstation %s gpus[%d].count must be a whole number" % (name, index))
        if count == 0:
            continue
        total_count = total_count + count
        model = str(entry.get("model") or "GPU")
        if model not in models:
            models.append(model)
        memory = number_or_none(entry.get("memory_gb"), "workstation %s gpus[%d].memory_gb" % (name, index))
        if memory is not None:
            memories.append(memory)

    if total_count == 0:
        return None, []

    notes = []
    # The smallest known card memory is used for every card (None = no memory check at all).
    memory_gb = min(memories) if memories else None
    if len(models) > 1:
        notes.append("mixed GPU models (%s): every card is planned as the smallest one (%s GB)"
                     % (", ".join(models), "?" if memory_gb is None else "%g" % memory_gb))
    gpu = {"model": " + ".join(models), "memory_gb": memory_gb, "count": total_count}
    return gpu, notes


def lane_label(name, gpu):
    """Return the run-status lane name of a workstation: "ws01 · RTX 4090", "ws03 · 2× A100", "ws04 · CPU"."""
    if gpu is None:
        return name + " · CPU"
    if gpu["count"] > 1:
        return "%s · %d× %s" % (name, gpu["count"], gpu["model"])
    return "%s · %s" % (name, gpu["model"])


def workstation_entry(machine, settings, facts_entry):
    """Build one machines.json workstation from its settings entry and its facts.

    Parameters:
        machine (dict): one entry of machines.workstations[].
        settings (dict): from resolved_settings().
        facts_entry (dict): facts["workstations"][name] or {}.

    Returns:
        dict: the machines.json entry (keys in the order of formats.md §2).
    """
    name = machine.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlannerSettingsError("every machines.workstations[] entry needs a 'name'")
    gpu, gpu_notes = gpu_summary(machine)

    # Rule lines printed in plan.md: the user's notes, the GPU note, the shared-machine rule, and
    # the rule lines of the facts file.
    rules = []
    if machine.get("notes"):
        rules.append(str(machine["notes"]))
    rules.extend(gpu_notes)
    is_shared = name in settings["shared_machines"]
    if is_shared:
        rules.append("Shared machine: never while another user runs a GPU process or keeps the CPU above %s %%"
                     % settings["shared_cpu_percent"])
    rules.extend(str(rule) for rule in (facts_entry.get("rules") or []))

    exceptions = facts_entry.get("exceptions") or []
    if not isinstance(exceptions, list):
        raise PlannerSettingsError("facts: workstations.%s.exceptions must be a list" % name)

    return {"name": name, "kind": "workstation", "label": lane_label(name, gpu), "gpu": gpu,
            "ram_gb": number_or_none(machine.get("ram_gb"), "workstation %s ram_gb" % name),
            "threads": number_or_none(machine.get("cpus"), "workstation %s cpus" % name),
            "ram_heavy_gb": settings["ram_heavy_gb"],
            "blocked": blocked_periods(machine),
            "exceptions": exceptions,
            "rules": rules,
            "ssh_alias": machine.get("ssh_alias") or "",
            "shared": is_shared}


def cluster_entry(cluster, settings, facts_entry, warnings):
    """Build one machines.json cluster from its settings entry, its budget setting and its facts.

    Parameters:
        cluster (dict): one entry of machines.clusters[].
        settings (dict): from resolved_settings() (its cluster_budgets).
        facts_entry (dict): facts["clusters"][name] or {}.
        warnings (list of str): receives a line when the budget used is unknown.

    Returns:
        dict: the machines.json entry.
    """
    name = cluster.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlannerSettingsError("every machines.clusters[] entry needs a 'name'")

    # Budget = allocation from the settings + the amount used from the facts.
    budget = None
    allocation = settings["cluster_budgets"].get(name)
    if allocation:
        if not isinstance(allocation, dict):
            raise PlannerSettingsError("cluster_budgets.%s must be an object {unit, allocated, ...}" % name)
        allocated = number_or_none(allocation.get("allocated"), "cluster_budgets.%s.allocated" % name)
        used_entry = facts_entry.get("budget_used") or {}
        used = number_or_none(used_entry.get("used"), "facts clusters.%s.budget_used.used" % name)
        if used is None:
            warnings.append("%s: budget used so far is unknown; the budget check counts only this plan's jobs" % name)
        budget = {"unit": allocation.get("unit") or "gpu-hours",
                  "allocated": allocated if allocated is not None else 0.0,
                  "used": used,
                  "as_of": used_entry.get("as_of"),
                  "source": used_entry.get("source") or "",
                  "applies_to": list(allocation.get("applies_to") or []),
                  "account": allocation.get("account") or ""}

    max_walltime = number_or_none(facts_entry.get("max_walltime_h"), "facts clusters.%s.max_walltime_h" % name)
    return {"name": name, "kind": "cluster", "label": name,
            "scheduler": cluster.get("scheduler") or "",
            "accounts": list(cluster.get("slurm_accounts") or []),
            "ssh_alias": cluster.get("ssh_alias") or "",
            "gpus": facts_entry.get("gpus") or {},
            "queue_wait_hours": facts_entry.get("queue_wait_hours") or {},
            "max_walltime_h": max_walltime,
            "outages": facts_entry.get("outages") or [],
            "budget": budget,
            "rules": [str(rule) for rule in (facts_entry.get("rules") or [])]}


# ----------------------------------------------------------------------------------------------
# The facts file and the whole machines file
# ----------------------------------------------------------------------------------------------

def read_facts(path):
    """Read machine_facts.json; a missing file gives empty facts.

    Parameters:
        path (str or None): the facts file, or None.

    Returns:
        dict: {"schema", "workstations": {...}, "clusters": {...}}.

    Raises:
        PlannerSettingsError: not JSON, not an object, or another schema.
    """
    empty = {"schema": FACTS_SCHEMA, "workstations": {}, "clusters": {}}
    if not path or not os.path.isfile(path):
        return empty
    with open(path, mode="r", encoding="utf-8") as facts_file:
        try:
            content = json.load(facts_file)
        except json.JSONDecodeError as error:
            raise PlannerSettingsError("facts file is not valid JSON (%s): %s" % (error, path))
    if not isinstance(content, dict) or content.get("schema") != FACTS_SCHEMA:
        raise PlannerSettingsError("facts file must be a JSON object with schema %r: %s" % (FACTS_SCHEMA, path))
    for key in ("workstations", "clusters"):
        if not isinstance(content.get(key, {}), dict):
            raise PlannerSettingsError("facts file: %r must be an object keyed by machine name: %s" % (key, path))
        content.setdefault(key, {})
    return content


def build_machines(config, facts, settings_path, facts_path):
    """Join the settings and the facts into the machines.json content (schema compute-machines/1).

    Parameters:
        config (dict): the settings.
        facts (dict): from read_facts().
        settings_path, facts_path (str): recorded in `built_from`.

    Returns:
        dict: the machines file. Its `warnings` list names facts about unknown machines, unknown
            budget use and an unknown preferred cluster.

    Raises:
        PlannerSettingsError: see blocked_periods, gpu_summary, cluster_entry.
    """
    settings = resolved_settings(config)
    zone = owner_zone(config)
    warnings = []

    machines = []
    for machine in workstations(config):
        facts_entry = facts["workstations"].get(machine.get("name"), {})
        machines.append(workstation_entry(machine, settings, facts_entry))
    for cluster in clusters(config):
        facts_entry = facts["clusters"].get(cluster.get("name"), {})
        machines.append(cluster_entry(cluster, settings, facts_entry, warnings))

    # Names must be unique across workstations and clusters (a task names them).
    names = [machine["name"] for machine in machines]
    duplicates = sorted(set(name for name in names if names.count(name) > 1))
    if duplicates:
        raise PlannerSettingsError("machine names must be unique across workstations and clusters: %s"
                                   % ", ".join(duplicates))

    # Facts about machines the settings do not list are ignored, with a warning.
    workstation_names = [machine["name"] for machine in machines if machine["kind"] == "workstation"]
    cluster_names = [machine["name"] for machine in machines if machine["kind"] == "cluster"]
    for name in facts["workstations"]:
        if name not in workstation_names:
            warnings.append("facts name a workstation %r that is not in the settings: ignored" % name)
    for name in facts["clusters"]:
        if name not in cluster_names:
            warnings.append("facts name a cluster %r that is not in the settings: ignored" % name)
    for name in settings["shared_machines"]:
        if name not in workstation_names:
            warnings.append("shared_machines names %r, which is not a workstation in the settings" % name)
    preferred = settings["prefer_cluster_for_cpu"]
    if preferred and preferred not in cluster_names:
        warnings.append("prefer_cluster_for_cpu names %r, which is not a cluster in the settings: ignored" % preferred)
        preferred = ""

    updated = datetime.datetime.now(zone).replace(microsecond=0).isoformat()
    return {"schema": MACHINES_SCHEMA, "timezone": time_zone(config), "updated": updated,
            "built_from": {"settings": settings_path, "facts": facts_path or ""},
            "prefer_cluster_for_cpu": preferred,
            "warnings": warnings,
            "machines": machines}


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def parse_arguments(argument_list=None):
    """Define and parse the command line (one subcommand per call)."""
    parser = argparse.ArgumentParser(
        description="The compute-planner's settings, and machines.json built from them.",
        epilog="Formats: references/formats.md §2.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show", help="Print the resolved planner settings as JSON.")
    get_parser = subparsers.add_parser("get", help="Print one resolved value.")
    get_parser.add_argument("key", help="planner_dir, horizon_days, time_zone, python, orchestrator_enabled, ...")
    subparsers.add_parser("stamp", help="Print the current time as YYYY-MM-DD_HHMM in owner.time_zone.")
    machines_parser = subparsers.add_parser("machines", help="Build machines.json from the settings and the facts.")
    machines_parser.add_argument("-o", "--output", required=True, help="The machines.json file to write.")
    machines_parser.add_argument("--facts", default=None, help="machine_facts.json (default: <planner_dir>/machine_facts.json).")
    machines_parser.add_argument("--overwrite", action="store_true", help="Replace an existing output file.")
    machines_parser.add_argument("--dry-run", action="store_true", help="Print the result, write nothing.")
    machines_parser.add_argument("--print", dest="print_result", action="store_true", help="Also print the result.")
    return parser.parse_args(argument_list)


def print_value(value):
    """Print a value: dicts and lists as JSON, Booleans as true/false, the rest as text."""
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    elif isinstance(value, bool):
        print("true" if value else "false")
    else:
        print(value)


def main(argument_list=None):
    """Entry point: run one subcommand. Returns the exit status (0 success, 1 a problem)."""
    arguments = parse_arguments(argument_list)
    try:
        config = load_config()
        if arguments.command == "show":
            print_value(resolved_settings(config))
            return 0
        if arguments.command == "get":
            print_value(derived_value(config, arguments.key))
            return 0
        if arguments.command == "stamp":
            print(stamp_now(config))
            return 0

        # machines: refuse an existing output first (a late refusal would waste the build).
        output_path = os.path.abspath(expand_path(arguments.output))
        if os.path.exists(output_path) and not arguments.overwrite and not arguments.dry_run:
            raise PlannerSettingsError("output exists, use --overwrite: %s" % output_path)
        facts_path = arguments.facts
        if facts_path is None:
            facts_path = os.path.join(resolved_settings(config)["planner_dir"], "machine_facts.json")
        facts_path = os.path.abspath(expand_path(facts_path))
        facts = read_facts(facts_path)
        machines_file = build_machines(config, facts, config_path(), facts_path if os.path.isfile(facts_path) else "")
        text = json.dumps(machines_file, indent=1, ensure_ascii=False) + "\n"
        if arguments.dry_run:
            print(text, end="")
            print("dry run: nothing written (would write %s)" % output_path, file=sys.stderr)
            return 0
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, mode="w", encoding="utf-8") as output_file:
            output_file.write(text)
        if arguments.print_result:
            print(text, end="")
        for warning in machines_file["warnings"]:
            print("warning: " + warning, file=sys.stderr)
        kinds = [machine["kind"] for machine in machines_file["machines"]]
        print("machines: %d workstation(s), %d cluster(s) -> %s" % (
            kinds.count("workstation"), kinds.count("cluster"), output_path))
        return 0
    except (ConfigError, PlannerSettingsError) as error:
        print("planner_settings.py: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
