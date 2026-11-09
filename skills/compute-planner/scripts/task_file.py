#!/usr/bin/env python3
"""
task_file.py - maintain compute_tasks.json, the ranked list of compute tasks of the planner.

PURPOSE
    The compute-planner skill keeps one card per compute task (a thing that occupies a machine
    for more than a few minutes) in compute_tasks.json (schema "compute-tasks/1", format in
    references/formats.md §1). This script is the only writer of that file: it adds cards,
    orders them, records measured runtimes and state changes, and writes every change
    atomically (a temporary file in the same folder, then a rename, so a crash never leaves a
    half-written file). Every write updates the file's `updated` time and appends a dated
    event to the task's `history`.

INPUTS
    --file   the tasks file; default $COMPUTE_PLANNER_DIR/compute_tasks.json when that variable
             is set, else <planner_dir>/compute_tasks.json, where planner_dir comes from the
             workhub-claude settings (skill_settings.compute-planner.planner_dir, default
             <paths.outputs_dir>/compute-planner; see planner_settings.py). A missing file is
             created by `add` (an empty list); every other subcommand needs an existing file.

SUBCOMMANDS
    list                         ranked table: rank, id, title, project, state, deadline, needs,
                                 cost hours + basis.
    add --json '<card>'          add one card given as JSON text, or
    add --file card.json         ... read from a file; the card is validated and completed with
                                 defaults (state pending, rank null, can_pause false, ...).
    order id1,id2,...            renumber ranks 1..n in that order; ids not named get rank null.
    rank <id> <n>                insert the task at position n (1 = first), shifting the others.
    set <id> key.path=value      set one field by dotted path; the value is read as JSON
                                 ("true", "12", "null", "[\"ws02\"]"), or as text when it is
                                 not valid JSON ("set x cost.source=log of the last run").
    measured <id> --hours H --source S --on MACHINE [--basis measured|scaled]
                                 record a measured runtime: cost.hours, basis, source,
                                 measured_on and date are set, and history gets the event.
    state <id> <state>           pending | approved | running | done | parked.

    --dry-run (before the subcommand) makes the change in memory, validates it and prints the
    changed file as JSON, but writes nothing. `list` never writes.

USAGE
    python3 task_file.py list                    # the file named by the settings
    python3 task_file.py add --json '{"id": "model_eval", "title": "Model A evaluation", "project": "Project A",
        "needs": {"gpu": true, "gpu_memory_gb": 12, "ram_gb": 30, "threads": 8, "disk_gb": 50,
        "machines": ["ws02", "ws01"]}, "cost": {"hours": 14, "basis": "measured",
        "source": "log of the previous run"}}'
    python3 task_file.py order model_eval,dataset_prep
    python3 task_file.py rank dataset_prep 1
    python3 task_file.py set model_eval deadline.date=2030-05-10
    python3 task_file.py measured model_eval --hours 13.5 --source "run log of May 7" --on ws02
    python3 task_file.py state model_eval done
    python3 task_file.py --file ~/scratch/compute_tasks.json list
    python3 task_file.py --dry-run state model_eval approved     # shows the result, writes nothing

DEPENDENCIES
    Python >= 3.9 (`zoneinfo`), standard library only, plus planner_settings.py and
    workhub_config.py from the same folder (the settings file is optional for this script).

ASSUMPTIONS
    - Times are written in the owner's zone (owner.time_zone of the settings, UTC when the
      settings are missing) with their offset (ISO 8601), the planner's zone.
    - Participant, patient or case identifiers never belong in a card (the plan is published).
      The script cannot detect them; the skill checks before adding a card.

EXIT STATUS
    0 = success;
    1 = the file is missing or malformed, a card fails validation, or a task id is unknown;
    2 = invalid command-line arguments (reported by argparse).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library)
# ----------------------------------------------------------------------------------------------

# `argparse` parses the subcommands and their options; it prints --help and exits with 2 on
# invalid input.
import argparse

# `datetime.datetime.now(zone)` gives the current moment in the user's zone, for `updated`
# and the history events.
import datetime

# `json` reads and writes the tasks file and the card texts.
import json

# `os` gives the environment variable COMPUTE_PLANNER_DIR, path helpers and `os.replace`.
import os

# `re.fullmatch(pattern, text)` checks that a task id is a slug of [a-z0-9_].
import re

# `sys` gives `sys.exit(status)` and `sys.stderr`.
import sys

# `tempfile.NamedTemporaryFile` creates the temporary file used by the atomic write.
import tempfile

# `ZoneInfo("Europe/Lisbon")` is a time-zone object; the owner's zone is used for written times.
from zoneinfo import ZoneInfo

# planner_settings.py and workhub_config.py sit in this script's folder. Python adds the folder
# of the script being RUN to sys.path, but not that of a script loaded by a test through
# importlib, so the folder is added explicitly before the imports.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# load_config(required=False): the settings, or {} when the kit is not set up; time_zone(config):
# owner.time_zone or "UTC"; resolved_settings(config): the planner's options with defaults
# (its "planner_dir" is the folder of compute_tasks.json).
from workhub_config import load_config, time_zone  # noqa: E402
from planner_settings import PlannerSettingsError, resolved_settings  # noqa: E402


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# Schema of the tasks file (references/formats.md §1).
TASKS_SCHEMA = "compute-tasks/1"

def settings_zone():
    """Return the owner's time zone from the settings; UTC when the settings or the zone are unusable."""
    config = load_config(required=False)
    try:
        return ZoneInfo(time_zone(config))
    except Exception:
        return ZoneInfo("UTC")


# Zone of the times written in the file: read once from the settings when the script starts.
TIMEZONE = settings_zone()

# Allowed values of the enumerated fields.
STATES = ("pending", "approved", "running", "done", "parked")
BASES = ("measured", "scaled", "estimate", "none")

# A task id is a slug: lower-case letters, digits and underscores, at least one character.
# Regular expression `[a-z0-9_]+`: `[...]` = one character from the set (a-z = any lower-case
# letter, 0-9 = any digit, _ = underscore); `+` = one or more of them.
ID_PATTERN = r"[a-z0-9_]+"

# Environment variable that names the planner folder (its compute_tasks.json is the default).
PLANNER_DIR_VARIABLE = "COMPUTE_PLANNER_DIR"

# The card keys and their default values when `add` receives a card without them. `None` in
# this table means "no default: the key is required".
CARD_DEFAULTS = {
    "id": None,
    "title": None,
    "project": "",
    "work_hub": None,
    "rank": None,
    "state": "pending",
    "deadline": None,
    "depends_on": [],
    "not_before": None,
    "needs": None,
    "cost": {"hours": None, "basis": "none", "source": "", "measured_on": None, "date": None},
    "measure": {"hours": 1.0, "what": ""},
    "accept_estimate": False,
    "can_pause": False,
    "cluster": None,
    "notes": "",
    "history": [],
}
REQUIRED_KEYS = ("id", "title", "needs")


# ----------------------------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------------------------

class TaskFileError(Exception):
    """A problem with the file, a card or an argument (exit status 1)."""


# ----------------------------------------------------------------------------------------------
# Time and file helpers
# ----------------------------------------------------------------------------------------------

def now_text():
    """Return the current moment as ISO 8601 text with offset, e.g. "2030-05-06T17:30:00+01:00".

    Returns:
        str: the text, without microseconds.
    """
    moment = datetime.datetime.now(TIMEZONE).replace(microsecond=0)
    return moment.isoformat()


def default_file_path():
    """Return the default tasks file.

    Order: $COMPUTE_PLANNER_DIR/compute_tasks.json when the variable is set (handy for a test
    folder), else <planner_dir>/compute_tasks.json from the settings.

    Returns:
        str or None: the path, or None when neither the variable nor the settings file exists.
    """
    planner_dir = os.environ.get(PLANNER_DIR_VARIABLE)
    if planner_dir:
        return os.path.join(planner_dir, "compute_tasks.json")
    config = load_config(required=False)
    if not config:
        return None
    try:
        settings = resolved_settings(config)
    except PlannerSettingsError as error:
        raise TaskFileError(str(error))
    return os.path.join(settings["planner_dir"], "compute_tasks.json")


def read_tasks_file(path, allow_missing=False):
    """Read the tasks file.

    Parameters:
        path (str): the file.
        allow_missing (bool): True returns an empty file structure when the path does not exist
            (used by `add` to create the file).

    Returns:
        dict: {"schema", "updated", "tasks": [...]}.

    Raises:
        TaskFileError: missing (unless allowed), not JSON, or wrong schema.
    """
    if not os.path.isfile(path):
        if allow_missing:
            return {"schema": TASKS_SCHEMA, "updated": None, "tasks": []}
        raise TaskFileError("tasks file not found: %s" % path)

    with open(path, mode="r", encoding="utf-8") as json_file:
        try:
            content = json.load(json_file)
        except json.JSONDecodeError as error:
            raise TaskFileError("tasks file is not valid JSON (%s): %s" % (error, path))

    if not isinstance(content, dict) or content.get("schema") != TASKS_SCHEMA:
        raise TaskFileError("tasks file must be a JSON object with schema %r: %s" % (TASKS_SCHEMA, path))
    if not isinstance(content.get("tasks"), list):
        raise TaskFileError("tasks file: 'tasks' must be a list: %s" % path)
    return content


def write_tasks_file(path, content):
    """Write the tasks file atomically and stamp `updated`.

    The content goes to a temporary file in the same folder, which is then renamed over the
    target with `os.replace`: on POSIX a rename is atomic, so a reader always sees either the
    old or the new complete file, never a partial one.

    Parameters:
        path (str): the file.
        content (dict): the file structure.
    """
    content["updated"] = now_text()

    # The folder must exist for the temporary file.
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)

    # `NamedTemporaryFile(..., delete=False)` creates a uniquely named file that survives the
    # `with` block; `dir=folder` keeps it on the same file system as the target (a rename across
    # file systems would not be atomic).
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, prefix=".compute_tasks.",
                                     suffix=".tmp", delete=False) as temporary_file:
        json.dump(content, temporary_file, indent=1, ensure_ascii=False)
        temporary_file.write("\n")
        temporary_path = temporary_file.name

    # `os.replace` renames the temporary file over the target, replacing it in one step.
    os.replace(temporary_path, path)


def find_task(content, task_id):
    """Return the card with `task_id`.

    Parameters:
        content (dict): the file structure.
        task_id (str): the id.

    Returns:
        dict: the card.

    Raises:
        TaskFileError: no card has that id.
    """
    for task in content["tasks"]:
        if task.get("id") == task_id:
            return task
    raise TaskFileError("unknown task id %r (known: %s)" % (task_id, ", ".join(t.get("id", "?") for t in content["tasks"]) or "none"))


def add_history(task, event):
    """Append a dated event to the card's history.

    Parameters:
        task (dict): the card.
        event (str): what happened, e.g. "rank 3 -> 1".
    """
    if not isinstance(task.get("history"), list):
        task["history"] = []
    task["history"].append({"date": now_text(), "event": event})


# ----------------------------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------------------------

def is_number(value):
    """Tell whether a value is an int or float (and not a bool, which Python counts as int)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_card(task):
    """Check every field of a card; raise on the first problem.

    Parameters:
        task (dict): a complete card (after defaults).

    Raises:
        TaskFileError: with the field and the rule it breaks.
    """
    if not isinstance(task, dict):
        raise TaskFileError("a card must be a JSON object")
    for key in REQUIRED_KEYS:
        if key not in task:
            raise TaskFileError("card is missing the required key %r" % key)

    # id: a slug; title: non-empty text.
    if not isinstance(task["id"], str) or re.fullmatch(ID_PATTERN, task["id"]) is None:
        raise TaskFileError("'id' must match [a-z0-9_]+, got %r" % (task["id"],))
    if not isinstance(task["title"], str) or not task["title"].strip():
        raise TaskFileError("'title' must be a non-empty text")

    # rank: int or null; state: one of STATES.
    if task["rank"] is not None and (not isinstance(task["rank"], int) or isinstance(task["rank"], bool) or task["rank"] < 1):
        raise TaskFileError("'rank' must be a positive integer or null")
    if task["state"] not in STATES:
        raise TaskFileError("'state' must be one of %s" % ", ".join(STATES))

    # deadline: null or {date: "YYYY-MM-DD", hard: bool}.
    deadline = task["deadline"]
    if deadline is not None:
        if not isinstance(deadline, dict) or not isinstance(deadline.get("hard"), bool):
            raise TaskFileError("'deadline' must be null or {\"date\": \"YYYY-MM-DD\", \"hard\": true|false}")
        try:
            datetime.date.fromisoformat(str(deadline.get("date")))
        except ValueError:
            raise TaskFileError("'deadline.date' must be YYYY-MM-DD, got %r" % (deadline.get("date"),))

    # depends_on: list of texts; not_before: null or ISO time.
    if not isinstance(task["depends_on"], list) or not all(isinstance(item, str) for item in task["depends_on"]):
        raise TaskFileError("'depends_on' must be a list of task ids or commitment run names")
    if task["not_before"] is not None:
        try:
            datetime.datetime.fromisoformat(str(task["not_before"]))
        except ValueError:
            raise TaskFileError("'not_before' must be null or an ISO 8601 time")

    # needs: gpu bool; numeric fields numbers or null; machines list of texts.
    needs = task["needs"]
    if not isinstance(needs, dict) or not isinstance(needs.get("gpu"), bool):
        raise TaskFileError("'needs' must be an object with a Boolean 'gpu'")
    for key in ("gpu_memory_gb", "ram_gb", "threads", "disk_gb"):
        value = needs.get(key)
        if value is not None and not is_number(value):
            raise TaskFileError("'needs.%s' must be a number or null" % key)
    machines = needs.get("machines", [])
    if not isinstance(machines, list) or not all(isinstance(item, str) for item in machines):
        raise TaskFileError("'needs.machines' must be a list of machine names")
    needs.setdefault("machines", [])

    # cost: hours number or null; basis one of BASES; per_machine object of numbers.
    cost = task["cost"]
    if not isinstance(cost, dict) or cost.get("basis") not in BASES:
        raise TaskFileError("'cost' must be an object with 'basis' in %s" % ", ".join(BASES))
    if cost.get("hours") is not None and not is_number(cost["hours"]):
        raise TaskFileError("'cost.hours' must be a number or null")
    if cost.get("basis") in ("measured", "scaled") and cost.get("hours") is None:
        raise TaskFileError("'cost.hours' is required when basis is measured or scaled")
    per_machine = cost.get("per_machine")
    if per_machine is not None:
        if not isinstance(per_machine, dict) or not all(is_number(value) for value in per_machine.values()):
            raise TaskFileError("'cost.per_machine' must be an object of machine name -> hours")

    # measure: null or {hours, what}.
    measure = task["measure"]
    if measure is not None:
        if not isinstance(measure, dict) or (measure.get("hours") is not None and not is_number(measure["hours"])):
            raise TaskFileError("'measure' must be null or {\"hours\": number, \"what\": text}")

    # Booleans.
    for key in ("accept_estimate", "can_pause"):
        if not isinstance(task[key], bool):
            raise TaskFileError("'%s' must be true or false" % key)

    # cluster: null or {allowed: [names], gpu: text, gpus: number, walltime_h: number, basis}.
    cluster = task["cluster"]
    if cluster is not None:
        if not isinstance(cluster, dict) or not isinstance(cluster.get("allowed"), list) \
                or not isinstance(cluster.get("gpu"), str):
            raise TaskFileError("'cluster' must be null or {\"allowed\": [...], \"gpu\": \"h100_80gb\", \"gpus\": n, \"walltime_h\": h}")
        for key in ("gpus", "walltime_h"):
            if cluster.get(key) is not None and not is_number(cluster[key]):
                raise TaskFileError("'cluster.%s' must be a number" % key)
        if cluster.get("basis") is not None and cluster["basis"] not in BASES:
            raise TaskFileError("'cluster.basis' must be one of %s" % ", ".join(BASES))

    # history: list of {date, event}.
    if not isinstance(task["history"], list):
        raise TaskFileError("'history' must be a list")


def complete_card(card):
    """Return a copy of a card with the default values filled in (CARD_DEFAULTS).

    Parameters:
        card (dict): the card as given.

    Returns:
        dict: the card with every key present, in the canonical key order.

    Raises:
        TaskFileError: a required key is missing or an unknown key is present.
    """
    if not isinstance(card, dict):
        raise TaskFileError("a card must be a JSON object")
    unknown_keys = [key for key in card if key not in CARD_DEFAULTS]
    if unknown_keys:
        raise TaskFileError("unknown card keys: %s" % ", ".join(unknown_keys))

    completed = {}
    for key, default in CARD_DEFAULTS.items():
        if key in card:
            completed[key] = card[key]
        elif key in REQUIRED_KEYS:
            raise TaskFileError("card is missing the required key %r" % key)
        else:
            # `json.loads(json.dumps(x))` makes a fresh copy of a list or dict default, so that
            # two cards never share the same list object.
            completed[key] = json.loads(json.dumps(default))

    # The cost and measure objects get their own sub-defaults.
    if isinstance(completed["cost"], dict):
        for key, default in CARD_DEFAULTS["cost"].items():
            completed["cost"].setdefault(key, default)
    if isinstance(completed["measure"], dict):
        for key, default in CARD_DEFAULTS["measure"].items():
            completed["measure"].setdefault(key, default)
    return completed


def check_unique_ranks(content):
    """Raise when two ranked tasks share a rank (formats.md §1 rules)."""
    ranks = [task["rank"] for task in content["tasks"] if task.get("rank") is not None]
    if len(ranks) != len(set(ranks)):
        raise TaskFileError("ranks must be unique among ranked tasks")


# ----------------------------------------------------------------------------------------------
# Subcommands
# ----------------------------------------------------------------------------------------------

def ranked_tasks(content):
    """Return the cards sorted by rank (unranked last, then by id)."""
    # The key is a pair: (0, rank) for ranked tasks, (1, 0) for unranked, then the id.
    def sort_key(task):
        if task.get("rank") is None:
            return (1, 0, task.get("id", ""))
        return (0, task["rank"], task.get("id", ""))
    return sorted(content["tasks"], key=sort_key)


def needs_summary(task):
    """One short text for the needs column: "GPU 20 GB · 40 GB RAM · 12 thr · ws02,ws01"."""
    needs = task.get("needs") or {}
    parts = []
    if needs.get("gpu"):
        parts.append("GPU %s GB" % (needs.get("gpu_memory_gb") if needs.get("gpu_memory_gb") is not None else "?"))
    else:
        parts.append("CPU")
    if needs.get("ram_gb") is not None:
        parts.append("%s GB RAM" % needs["ram_gb"])
    if needs.get("threads") is not None:
        parts.append("%s thr" % needs["threads"])
    machines = needs.get("machines") or []
    parts.append(",".join(machines) if machines else "any")
    cluster = task.get("cluster")
    if cluster:
        parts.append("cluster " + ",".join(cluster.get("allowed") or []))
    return " · ".join(parts)


def command_list(content):
    """Print the ranked table."""
    header = "%-4s %-28s %-36s %-10s %-9s %-12s %-44s %s" % ("rank", "id", "title", "project", "state", "deadline", "needs", "cost")
    print(header)
    print("-" * len(header))
    for task in ranked_tasks(content):
        rank = "-" if task.get("rank") is None else str(task["rank"])
        deadline = task.get("deadline")
        deadline_text = "-"
        if deadline:
            deadline_text = deadline.get("date", "?") + (" !" if deadline.get("hard") else "")
        cost = task.get("cost") or {}
        hours = cost.get("hours")
        cost_text = ("%s h" % hours if hours is not None else "? h") + " " + str(cost.get("basis", "none"))
        print("%-4s %-28s %-36s %-10s %-9s %-12s %-44s %s" % (
            rank, task.get("id", "?")[:28], task.get("title", "")[:36], task.get("project", "")[:10],
            task.get("state", "?"), deadline_text, needs_summary(task)[:44], cost_text))
    print("%d tasks" % len(content["tasks"]))


def command_add(content, card_text, card_path):
    """Validate a card, fill its defaults and append it.

    Parameters:
        content (dict): the file structure, changed in place.
        card_text (str or None): the card as JSON text (--json).
        card_path (str or None): a file with the card (--file).

    Returns:
        str: the new task id.
    """
    if card_text is None and card_path is None:
        raise TaskFileError("add needs --json '<card>' or --file card.json")
    if card_path is not None:
        if not os.path.isfile(card_path):
            raise TaskFileError("card file not found: %s" % card_path)
        with open(card_path, mode="r", encoding="utf-8") as card_file:
            card_text = card_file.read()
    try:
        card = json.loads(card_text)
    except json.JSONDecodeError as error:
        raise TaskFileError("card is not valid JSON: %s" % error)

    completed = complete_card(card)
    validate_card(completed)
    for existing in content["tasks"]:
        if existing.get("id") == completed["id"]:
            raise TaskFileError("a task with id %r already exists" % completed["id"])
    if completed["rank"] is not None:
        ranks = [task["rank"] for task in content["tasks"] if task.get("rank") is not None]
        if completed["rank"] in ranks:
            raise TaskFileError("rank %d is already used" % completed["rank"])

    add_history(completed, "added")
    content["tasks"].append(completed)
    return completed["id"]


def command_order(content, id_list_text):
    """Renumber ranks 1..n in the given order; unnamed tasks get rank null.

    Parameters:
        content (dict): the file structure, changed in place.
        id_list_text (str): "id1,id2,...".
    """
    ids = [item.strip() for item in id_list_text.split(",") if item.strip()]
    if not ids:
        raise TaskFileError("order needs at least one task id")
    if len(ids) != len(set(ids)):
        raise TaskFileError("order: an id is repeated")
    for task_id in ids:
        find_task(content, task_id)

    for task in content["tasks"]:
        old_rank = task.get("rank")
        new_rank = ids.index(task["id"]) + 1 if task["id"] in ids else None
        if old_rank != new_rank:
            task["rank"] = new_rank
            add_history(task, "rank %s -> %s (order)" % (old_rank, new_rank))


def command_rank(content, task_id, position):
    """Insert a task at rank `position`, shifting the others down.

    Parameters:
        content (dict): the file structure, changed in place.
        task_id (str): the task.
        position (int): the new rank (1 = first); larger than the count = last.
    """
    task = find_task(content, task_id)
    if position < 1:
        raise TaskFileError("rank position must be >= 1")

    # The current ranked order without this task, then the task inserted at position - 1.
    others = [other for other in ranked_tasks(content) if other.get("rank") is not None and other["id"] != task_id]
    insert_at = min(position - 1, len(others))
    new_order = others[:insert_at] + [task] + others[insert_at:]

    for new_rank, ordered_task in enumerate(new_order, start=1):
        old_rank = ordered_task.get("rank")
        if old_rank != new_rank:
            ordered_task["rank"] = new_rank
            add_history(ordered_task, "rank %s -> %s (rank %s %d)" % (old_rank, new_rank, task_id, position))


def parse_assignment(text):
    """Split "key.path=value" into (["key", "path"], value) with the value read as JSON.

    Parameters:
        text (str): the assignment.

    Returns:
        tuple (list of str, value): the path parts and the typed value.
    """
    if "=" not in text:
        raise TaskFileError("set needs key.path=value, got %r" % text)
    # `split("=", 1)` cuts at the first "=" only, so a value may contain "=".
    path_text, value_text = text.split("=", 1)
    path = [part for part in path_text.strip().split(".") if part]
    if not path:
        raise TaskFileError("set: empty key path in %r" % text)
    # JSON first ("true", "12", "null", "[...]"); any other text stays a text.
    try:
        value = json.loads(value_text)
    except json.JSONDecodeError:
        value = value_text
    return path, value


def command_set(content, task_id, assignment):
    """Set one field of a card by dotted path, then re-validate the card.

    Parameters:
        content (dict): the file structure, changed in place.
        task_id (str): the task.
        assignment (str): "key.path=value".
    """
    task = find_task(content, task_id)
    path, value = parse_assignment(assignment)
    if path[0] == "id":
        raise TaskFileError("the id of a task is never changed")

    # Walk down the path, creating missing intermediate objects (e.g. deadline null -> {}).
    target = task
    for part in path[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    old_value = target.get(path[-1])
    target[path[-1]] = value

    # A deadline set by parts gets the missing part's default so the card stays valid.
    if path[0] == "deadline" and isinstance(task.get("deadline"), dict):
        task["deadline"].setdefault("hard", False)

    validate_card(task)
    check_unique_ranks(content)
    add_history(task, "set %s: %s -> %s" % (".".join(path), json.dumps(old_value), json.dumps(value)))


def command_measured(content, task_id, hours, source, machine, basis):
    """Record a measured runtime in cost and history.

    Parameters:
        content (dict): the file structure, changed in place.
        task_id (str): the task.
        hours (float): wall-clock hours on `machine`.
        source (str): where the number comes from.
        machine (str): the machine it was measured on.
        basis (str): "measured" or "scaled".
    """
    task = find_task(content, task_id)
    if hours <= 0:
        raise TaskFileError("--hours must be positive")
    if basis not in ("measured", "scaled"):
        raise TaskFileError("--basis must be measured or scaled")
    cost = task.get("cost") if isinstance(task.get("cost"), dict) else {}
    cost["hours"] = hours
    cost["basis"] = basis
    cost["source"] = source
    cost["measured_on"] = machine
    cost["date"] = datetime.datetime.now(TIMEZONE).date().isoformat()
    task["cost"] = cost
    validate_card(task)
    add_history(task, "%s %.2f h on %s (%s)" % (basis, hours, machine, source))


def command_state(content, task_id, state):
    """Change a task's state."""
    task = find_task(content, task_id)
    if state not in STATES:
        raise TaskFileError("state must be one of %s" % ", ".join(STATES))
    old_state = task.get("state")
    task["state"] = state
    add_history(task, "state %s -> %s" % (old_state, state))


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def parse_arguments(argument_list=None):
    """Define and parse the command line (one subcommand per call).

    Parameters:
        argument_list (list of str or None): the arguments; None = sys.argv[1:].

    Returns:
        argparse.Namespace: with `command` and the subcommand's own fields.
    """
    parser = argparse.ArgumentParser(description="Maintain compute_tasks.json of the compute-planner skill.",
                                     epilog="Format: references/formats.md §1.")
    parser.add_argument("--file", default=None,
                        help="The tasks file (default: $%s/compute_tasks.json, else <planner_dir>/compute_tasks.json "
                             "from the settings)." % PLANNER_DIR_VARIABLE)
    # `--dry-run` is a global option (it goes before the subcommand, like --file): the change is
    # made and validated in memory and printed, and the file on disk stays as it was.
    parser.add_argument("--dry-run", action="store_true",
                        help="Make and check the change, print the resulting file, write nothing.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="Print the ranked table.")

    add_parser = subparsers.add_parser("add", help="Add one card.")
    add_parser.add_argument("--json", dest="card_text", default=None, help="The card as JSON text.")
    add_parser.add_argument("--file", dest="card_path", default=None, help="A file with the card.")

    order_parser = subparsers.add_parser("order", help="Renumber ranks 1..n in the given order.")
    order_parser.add_argument("ids", help="Comma-separated task ids.")

    rank_parser = subparsers.add_parser("rank", help="Insert a task at a position.")
    rank_parser.add_argument("task_id")
    rank_parser.add_argument("position", type=int)

    set_parser = subparsers.add_parser("set", help="Set a field by dotted path.")
    set_parser.add_argument("task_id")
    set_parser.add_argument("assignment", help="key.path=value (value read as JSON, else text).")

    measured_parser = subparsers.add_parser("measured", help="Record a measured runtime.")
    measured_parser.add_argument("task_id")
    measured_parser.add_argument("--hours", type=float, required=True)
    measured_parser.add_argument("--source", required=True)
    measured_parser.add_argument("--on", dest="machine", required=True)
    measured_parser.add_argument("--basis", default="measured", choices=["measured", "scaled"])

    state_parser = subparsers.add_parser("state", help="Change a task's state.")
    state_parser.add_argument("task_id")
    state_parser.add_argument("state", choices=list(STATES))

    return parser.parse_args(argument_list)


def main(argument_list=None):
    """Entry point: run one subcommand on the tasks file.

    Parameters:
        argument_list (list of str or None): for tests; None = the real command line.

    Returns:
        int: the exit status.
    """
    arguments = parse_arguments(argument_list)
    try:
        path = arguments.file or default_file_path()
        if path is None:
            raise TaskFileError("no tasks file: pass --file, set $%s, or set up workhub-claude "
                                "(say \"set up my work hub\")" % PLANNER_DIR_VARIABLE)

        if arguments.command == "list":
            content = read_tasks_file(path)
            command_list(content)
            return 0

        # Every other subcommand reads, changes and writes the file.
        content = read_tasks_file(path, allow_missing=(arguments.command == "add"))
        if arguments.command == "add":
            new_id = command_add(content, arguments.card_text, arguments.card_path)
            message = "added %s" % new_id
        elif arguments.command == "order":
            command_order(content, arguments.ids)
            message = "ranks renumbered"
        elif arguments.command == "rank":
            command_rank(content, arguments.task_id, arguments.position)
            message = "%s at rank %d" % (arguments.task_id, arguments.position)
        elif arguments.command == "set":
            command_set(content, arguments.task_id, arguments.assignment)
            message = "%s: %s" % (arguments.task_id, arguments.assignment)
        elif arguments.command == "measured":
            command_measured(content, arguments.task_id, arguments.hours, arguments.source, arguments.machine, arguments.basis)
            message = "%s: %.2f h %s on %s" % (arguments.task_id, arguments.hours, arguments.basis, arguments.machine)
        else:
            command_state(content, arguments.task_id, arguments.state)
            message = "%s: state %s" % (arguments.task_id, arguments.state)

        check_unique_ranks(content)
        # With --dry-run the changed content is printed instead of written, so the user can see
        # exactly what a change would do before it is made.
        if arguments.dry_run:
            print(json.dumps(content, indent=1, ensure_ascii=False))
            print("dry run: %s (nothing written to %s)" % (message, path))
            return 0
        write_tasks_file(path, content)
    except TaskFileError as error:
        print("task_file.py: %s" % error, file=sys.stderr)
        return 1

    print("%s -> %s" % (message, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
