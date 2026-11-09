#!/usr/bin/env python3
"""
slurm_week.py - every Slurm job of one Monday-to-Sunday week on the user's clusters, with the
compute it used, how it ended, and the Work Hub row (Task ID, e.g. TK-42) it belongs to. Read-only.

PURPOSE
    The weekly recap (skill `weekly-recap`) must say what ran on the clusters that week, not only
    what the conversations remember. Slurm's accounting database (`sacct`) records every job:
    when it was submitted, started and ended, its state (COMPLETED, FAILED, TIMEOUT ...), the CPUs,
    GPUs and memory it held, the CPU time it really used and its peak memory. This script asks
    each reachable cluster for the user's jobs of the week, clips each job to the week, adds up
    core-hours and GPU-hours (also per Slurm account), and links each job to a Work Hub row when
    it can.

    Nothing is submitted, cancelled or modified: the only remote command is `sacct` (plus `date`).

WHICH CLUSTERS (from the workhub-claude settings file)
    machines.clusters[] with scheduler "slurm" (name, ssh_alias, slurm_accounts), plus the optional
    two-hop clusters of machines.cluster_hops (see recap_common.slurm_targets).
    A cluster is read only through a shared SSH connection that is ALREADY open (or directly when
    this script runs on it); the others are reported as "not reached", never logged in to.

HOW A JOB IS LINKED TO A WORK HUB ROW (first rule that applies)
    1. The job name holds a Task ID, e.g. "tk118_check" -> TK-118 (prefix: notion.task_id_prefix).
    2. The job ID appears in a conversation that recorded its row (`--sessions-json`, the output
       of work-hub-cleanup's session_digest.py): the conversation that launched or followed it.
    3. The job ID appears in an orchestrator log entry (`--orchestrator-log`); the Task ID written
       closest before it in the same sentence gives the row ("TK-121 [a2f4d1] submitted array
       1000003_[2-63]" -> TK-121); several entries vote.
    4. Another job of the same working folder has a row (a chain of jobs shares a folder, but
       only some of its job IDs are written down); shallow folders such as a bare home do not count.
    Jobs with no row get only a project label (below), so the recap can still group them.

HOW A JOB GETS A PROJECT LABEL
    The optional rules file (skill_settings.weekly-recap.project_rules_file, or --project-rules;
    format: references/project_rules.example.json) lists labels with plain-text patterns; the job
    name and working folder are joined, lower-cased and searched; the first rule with a matching
    pattern wins. Without a rules file, or without a match, the label is "other: <job-name prefix>",
    and the recap groups rows by their Work Hub Project only.

INPUTS
    --week                 last (default), this, 2026-W45 or a day 2026-11-04 (see recap_common.py)
    --clusters             comma-separated cluster names (default: every Slurm cluster in the settings)
    --orchestrator-log     "auto" (default: the log files of the week's months in
                           workflow.orchestrator.folder/log/), "none", or file(s), comma-separated
    --sessions-json        optional, sessions.json of session_digest.py for the same week
    --project-rules        "auto" (default: the settings), "none", or a JSON rules file
    --accounts-only        ask sacct only for the accounts listed in the settings (default: all
                           of the user's jobs, whatever the account)
    --compare-previous     also collect the week before, for totals only
    -o / --output-dir      folder for the outputs (default <paths.outputs_dir>/weekly-recap/<week>/slurm);
                           --overwrite to replace them

OUTPUTS (in --output-dir, folder mode 700, files mode 600: they hold user names, job names, paths)
    slurm_week.json   the week, the clusters reached or not, every job with its numbers and row,
                      the totals per cluster (and per account), and (with --compare-previous)
                      last week's totals;
    slurm_week.txt    the same as a readable summary: totals, work groups, problem jobs.

USAGE
    python3 slurm_week.py --week 2026-W45 --print
    python3 slurm_week.py --week 2026-W45 --compare-previous \
        --sessions-json ~/workhub/outputs/weekly-recap/2026-W45/sessions/sessions.json --overwrite

DEPENDENCIES
    Python >= 3.9, standard library only; `ssh` with already-open shared connections; on the
    clusters `sacct` (Slurm 23 or newer was checked).

ASSUMPTIONS AND LIMITS
    - `TZ=<owner.time_zone>` is set for the remote sacct, so its -S/-E window and every time it
      prints are in the owner's zone on every cluster, whatever the cluster's own zone.
    - `sacct -S -E` returns the jobs that were eligible or running at some point in the window,
      so a job pending since an earlier week is listed; it counts as "waiting", with 0 hours.
    - Hours are clipped to the week: a job running Sunday 22:00 to Monday 02:00 counts 2 hours.
    - GPU-hours = GPUs held x hours held (allocation, not utilisation; sacct has no GPU usage).
    - CPU efficiency = CPU time used / (cores held x elapsed), for jobs that ended and ran >= 5 min.
    - Some clusters keep only a few weeks of sacct history: an older week can be incomplete.

EXIT STATUS
    0 = success (unreachable clusters are reported in the outputs, not as errors);
    1 = an output file exists without --overwrite, or --week cannot be read;
    2 = invalid command-line arguments (argparse) or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------------------------------

# `argparse` reads the command-line options and prints --help.
import argparse

# `datetime` gives moments and durations (see recap_common.py).
import datetime

# `json` reads the rules and the sessions file, and writes slurm_week.json.
import json

# `os` gives path helpers.
import os

# `re` finds job IDs (runs of digits) and sentence breaks in texts.
import re

# `shlex.quote` makes the time-zone name and the account list safe shell words.
import shlex

# `statistics.median` gives the middle value of a list (for queue waits and efficiencies).
import statistics

# `sys` gives `sys.exit` and `sys.path`.
import sys

# The shared helpers of this skill (same folder): settings, the week window, Task IDs, running
# remote commands through open SSH connections, and private output files. `sys.path.insert(0,
# folder)` makes Python look in this script's own folder first, so the import works from any
# working directory. recap_common itself imports workhub_config.py (the settings reader).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recap_common  # noqa: E402  (the import must come after the sys.path change above)
import workhub_config  # noqa: E402


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# The sacct columns asked for, in this order. WorkDir is last because it is the only column that
# could ever contain the "|" separator; the parser joins everything after column 16 back into it.
SACCT_FIELDS = ["JobID", "JobName", "Account", "Partition", "State", "ExitCode", "Submit", "Start",
                "End", "ElapsedRaw", "Timelimit", "AllocTRES", "TotalCPU", "MaxRSS", "ReqMem",
                "NNodes", "WorkDir"]

# States in which a job ended badly; the GPU-hours they held count as "wasted".
BAD_STATES = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED"}

# Jobs shorter than this (seconds) get no CPU efficiency (start-up time dominates them).
MIN_SECONDS_FOR_EFFICIENCY = 300

# UTC, used to subtract two local times as real elapsed time (see add_week_numbers).
UTC = datetime.timezone.utc


# ----------------------------------------------------------------------------------------------
# Parsing sacct values
# ----------------------------------------------------------------------------------------------

def duration_to_seconds(text):
    """Convert a Slurm duration into seconds.

    Slurm writes durations as "[DD-][HH:]MM:SS[.mmm]": "1-02:03:04" (1 day 2 h 3 min 4 s),
    "02:03:04", "03:04.123" (TotalCPU of short jobs), or words such as "UNLIMITED" / "Partition_Limit".

    Parameters:
        text (str): the duration text.

    Returns:
        float or None: seconds; None for an empty or non-numeric value.

    Example:
        >>> duration_to_seconds("1-02:03:04")
        93784.0
    """
    text = text.strip()
    if not text or not text[0].isdigit():
        return None

    # Split off the days: "1-02:03:04" -> days 1, rest "02:03:04".
    days = 0
    if "-" in text:
        days_text, text = text.split("-", 1)
        days = int(days_text)

    # The rest has 1 to 3 parts separated by ":"; the last one may carry decimals.
    parts = [float(part) for part in text.split(":")]
    # Pad on the left so there are always hours, minutes, seconds: [3, 4.1] -> [0, 3, 4.1].
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hours, minutes, seconds = parts
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def memory_to_gb(text):
    """Convert a Slurm memory value ("1234K", "4000M", "64G", "1.5T", "0") into gigabytes (GiB).

    Parameters:
        text (str): the value; older Slurm versions add "n" (per node) or "c" (per core), which
            are dropped here (only the size matters for a weekly summary).

    Returns:
        float or None: GiB, None when empty.
    """
    text = text.strip().rstrip("nc")
    if not text:
        return None

    # The last character is the unit when it is a letter; a bare number is in megabytes for
    # ReqMem but in bytes nowhere in practice here, so it is read as megabytes.
    factors_to_gb = {"K": 1.0 / 1024 ** 2, "M": 1.0 / 1024, "G": 1.0, "T": 1024.0, "P": 1024.0 ** 2}
    unit = text[-1].upper()
    if unit in factors_to_gb:
        number_text = text[:-1]
    else:
        unit = "M"
        number_text = text
    try:
        return float(number_text) * factors_to_gb[unit]
    except ValueError:
        return None


def tres_counts(alloc_tres):
    """Read the cores, GPUs and GPU model from an AllocTRES value.

    Parameters:
        alloc_tres (str): e.g. "billing=16000,cpu=16,gres/gpu:h100=4,gres/gpu=4,mem=64G,node=1".

    Returns:
        tuple (cpus, gpus, gpu_type): cpus (int), gpus (int), gpu_type (str, "" when unknown).
    """
    cpus = 0
    gpus = 0
    gpu_type = ""
    # The value is a comma-separated list of "name=count" pairs.
    for pair in alloc_tres.split(","):
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        if name == "cpu":
            cpus = int(float(value))
        elif name == "gres/gpu":
            # The untyped total, present on every Slurm version.
            gpus = int(float(value))
        elif name.startswith("gres/gpu:"):
            # The typed entry gives the model: "gres/gpu:h100" -> "h100". Some clusters only list
            # the typed entry, so its count is used when the untyped one is missing.
            gpu_type = name.split(":", 1)[1]
            if gpus == 0:
                gpus = int(float(value))
    return cpus, gpus, gpu_type


def parse_local_time(text, timezone):
    """Read a sacct time ("2026-11-02T14:27:09") as a moment in `timezone`; None for Unknown/None.

    Parameters:
        text (str): the sacct value.
        timezone (ZoneInfo): the zone sacct printed in (TZ was set to it).

    Returns:
        datetime.datetime or None.
    """
    text = text.strip()
    if not text or not text[0].isdigit():
        return None
    # `fromisoformat` reads the date and time; `replace(tzinfo=...)` attaches the zone they are in.
    return datetime.datetime.fromisoformat(text).replace(tzinfo=timezone)


# ----------------------------------------------------------------------------------------------
# Reading one cluster
# ----------------------------------------------------------------------------------------------

def sacct_shell_command(start, end, zone_name, accounts=None):
    """Build the remote shell line: the cluster's clock, then sacct for the window.

    Parameters:
        start, end (datetime.datetime): the window (owner's time zone).
        zone_name (str): the owner's IANA zone, e.g. "Europe/Lisbon".
        accounts (list of str or None): with --accounts-only, the accounts to ask for.

    Returns:
        str: the command line, e.g.
            date -Is; TZ=Europe/Lisbon sacct -u "$USER" -n -P -S 2026-11-02T00:00:00 -E ... --format=...
    """
    # `TZ=<zone>` before the command sets the time zone for that one program, so sacct reads
    # -S/-E and prints every time in the owner's zone, whatever the cluster's own zone.
    # -u "$USER": the user's own jobs (expanded on the cluster); -n: no header; -P: fields
    # separated by "|" with no padding; no -X, so job steps come too (they carry TotalCPU and
    # MaxRSS); --format: the columns above. `-A a,b` (only with --accounts-only) keeps the jobs
    # of those accounts.
    account_option = ""
    if accounts:
        account_option = " -A " + shlex.quote(",".join(accounts))
    return ("date -Is; TZ=%s sacct -u \"$USER\"%s -n -P -S %s -E %s --format=%s"
            % (shlex.quote(zone_name), account_option, start.strftime("%Y-%m-%dT%H:%M:%S"),
               end.strftime("%Y-%m-%dT%H:%M:%S"), ",".join(SACCT_FIELDS)))


def parse_sacct_output(lines, cluster, timezone):
    """Turn sacct lines (jobs and their steps) into one record per job.

    Parameters:
        lines (list of str): sacct output lines, "|"-separated in SACCT_FIELDS order.
        cluster (str): cluster name, stored in each record.
        timezone (ZoneInfo): zone of the printed times.

    Returns:
        tuple (jobs, problems):
            jobs (list of dict): one dict per job (keys listed in the dict below);
            problems (list of str): lines that could not be read.
    """
    jobs_by_id = {}
    step_cpu_seconds = {}
    step_max_rss_gb = {}
    problems = []

    for line in lines:
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) < len(SACCT_FIELDS):
            problems.append("unreadable sacct line: " + line[:120])
            continue
        # Join any extra pieces back into WorkDir (the last column).
        fields = fields[:len(SACCT_FIELDS) - 1] + ["|".join(fields[len(SACCT_FIELDS) - 1:])]
        # `dict(zip(names, values))` pairs each column name with its value:
        # {"JobID": "1000001", "JobName": "demo_smoke", ...}.
        row = dict(zip(SACCT_FIELDS, fields))

        job_id = row["JobID"]
        if "." in job_id:
            # A job step, e.g. "1000001.batch" or "1000001.0": add its CPU time and peak memory
            # to its job ("1000001", the part before the dot).
            parent_id = job_id.split(".", 1)[0]
            cpu_seconds = duration_to_seconds(row["TotalCPU"]) or 0.0
            step_cpu_seconds[parent_id] = step_cpu_seconds.get(parent_id, 0.0) + cpu_seconds
            rss_gb = memory_to_gb(row["MaxRSS"])
            if rss_gb is not None:
                step_max_rss_gb[parent_id] = max(step_max_rss_gb.get(parent_id, 0.0), rss_gb)
            continue

        # A job (or one task of an array, "123_4"): keep its own line.
        cpus, gpus, gpu_type = tres_counts(row["AllocTRES"])
        jobs_by_id[job_id] = {
            "cluster": cluster,
            "job_id": job_id,
            "name": row["JobName"],
            "account": row["Account"],
            "partition": row["Partition"],
            # "CANCELLED by 1000" -> "CANCELLED"; the full text is kept in state_text.
            "state": row["State"].split()[0] if row["State"].strip() else "UNKNOWN",
            "state_text": row["State"],
            "exit_code": row["ExitCode"],
            "submit": parse_local_time(row["Submit"], timezone),
            "start": parse_local_time(row["Start"], timezone),
            "end": parse_local_time(row["End"], timezone),
            "elapsed_seconds": float(row["ElapsedRaw"]) if row["ElapsedRaw"].strip().isdigit() else 0.0,
            "time_limit_seconds": duration_to_seconds(row["Timelimit"]),
            "cpus": cpus,
            "gpus": gpus,
            "gpu_type": gpu_type,
            "job_cpu_seconds": duration_to_seconds(row["TotalCPU"]) or 0.0,
            "requested_memory_gb": memory_to_gb(row["ReqMem"]),
            "nodes": int(row["NNodes"]) if row["NNodes"].strip().isdigit() else 0,
            "workdir": row["WorkDir"],
        }

    # Attach the step totals. The job line's own TotalCPU already sums its steps on recent Slurm
    # versions; when it is 0 (seen with some versions) the sum of the steps is used instead.
    jobs = []
    for job_id, job in jobs_by_id.items():
        cpu_seconds = job.pop("job_cpu_seconds")
        if cpu_seconds <= 0:
            cpu_seconds = step_cpu_seconds.get(job_id, 0.0)
        job["cpu_seconds_used"] = cpu_seconds
        job["max_rss_gb"] = step_max_rss_gb.get(job_id)
        jobs.append(job)
    return jobs, problems


def collect_cluster(target, start, end, timezone, zone_name, this_system, accounts_only):
    """Read one cluster's jobs of the window.

    Parameters:
        target (dict): one entry of recap_common.slurm_targets().
        start, end (datetime.datetime): the window.
        timezone (ZoneInfo): the owner's zone.
        zone_name (str): its IANA name (for TZ= on the cluster).
        this_system (str): this machine's system name.
        accounts_only (bool): restrict sacct to the target's configured accounts.

    Returns:
        dict: {"cluster", "reached", "note", "accounts_configured", "jobs", "problems"}.
    """
    result = {"cluster": target["name"], "reached": False, "note": "",
              "accounts_configured": target.get("accounts", []), "jobs": [], "problems": []}
    accounts = target.get("accounts") if accounts_only else None
    if accounts_only and not accounts:
        result["note"] = "--accounts-only, but no slurm_accounts are listed for %s in the settings" % target["name"]
        return result
    command, note = recap_common.remote_command(target, sacct_shell_command(start, end, zone_name, accounts),
                                                this_system)
    if command is None:
        result["note"] = note
        return result

    status, stdout, stderr = recap_common.run_command(command)
    if status != 0:
        # Keep the first line of the error: enough to tell "sacct not found" from a timeout.
        first_error_line = (stderr.strip().splitlines() or ["exit status %d" % status])[0]
        result["note"] = "sacct failed: " + first_error_line[:200]
        return result

    # The first line is the cluster's `date -Is` (a liveness check); the rest is sacct.
    lines = stdout.splitlines()
    result["reached"] = True
    result["jobs"], result["problems"] = parse_sacct_output(lines[1:], target["name"], timezone)
    return result


# ----------------------------------------------------------------------------------------------
# Hours inside the week
# ----------------------------------------------------------------------------------------------

def add_week_numbers(job, start, end, now):
    """Add the hours a job held inside the window, its CPU efficiency and its queue wait.

    Parameters:
        job (dict): one record of parse_sacct_output (changed in place).
        start, end (datetime.datetime): the window.
        now (datetime.datetime): the collection time (the end of a still-running job).
    """
    # Moments the job held its resources: from its start to its end (or "now" while running).
    held_from = job["start"]
    held_until = job["end"] or now
    if held_from is None:
        overlap_seconds = 0.0
    else:
        # Clip to the window: the later of the two starts, the earlier of the two ends.
        overlap_start = max(held_from, start)
        overlap_end = min(held_until, end)
        # Subtract in UTC: Python subtracts two times of the SAME zone object by their wall
        # clocks, so a job running through the Sunday of the daylight-saving change would be off
        # by one hour. `.astimezone(UTC)` turns both into real elapsed time first.
        overlap_seconds = max(0.0, (overlap_end.astimezone(UTC) - overlap_start.astimezone(UTC)).total_seconds())

    job["hours_in_week"] = overlap_seconds / 3600.0
    job["core_hours"] = job["cpus"] * job["hours_in_week"]
    job["gpu_hours"] = job["gpus"] * job["hours_in_week"]

    # CPU efficiency, only for jobs that ended and ran long enough.
    elapsed = job["elapsed_seconds"]
    if job["end"] is not None and elapsed >= MIN_SECONDS_FOR_EFFICIENCY and job["cpus"] > 0:
        job["cpu_efficiency"] = job["cpu_seconds_used"] / (job["cpus"] * elapsed)
    else:
        job["cpu_efficiency"] = None

    # Memory efficiency: peak memory of its steps over the memory it asked for.
    if job["max_rss_gb"] and job["requested_memory_gb"]:
        job["memory_efficiency"] = job["max_rss_gb"] / job["requested_memory_gb"]
    else:
        job["memory_efficiency"] = None

    # Queue wait, only for jobs that STARTED inside the window.
    if job["start"] is not None and job["submit"] is not None and start <= job["start"] < end:
        job["wait_hours"] = (job["start"].astimezone(UTC) - job["submit"].astimezone(UTC)).total_seconds() / 3600.0
    else:
        job["wait_hours"] = None

    # Where the job stood at the end of the window, in plain words used by the summary.
    if job["start"] is None or job["start"] >= end:
        job["week_status"] = "waiting"
    elif job["end"] is None or job["end"] >= end:
        job["week_status"] = "running at week end"
    else:
        job["week_status"] = job["state"].lower()


# ----------------------------------------------------------------------------------------------
# Links to Work Hub rows and project labels
# ----------------------------------------------------------------------------------------------

# Regular expression for a Slurm job ID, character by character: `(?<!\d)` "not preceded by a
# digit", `(\d{6,9})` six to nine digits (captured), `(?!\d)` "not followed by a digit". A plain
# `\b` boundary would miss array IDs such as "1000003_[2-63]", because "_" counts as a word
# character. Only numbers that are real job IDs of the week are used, so other numbers do no harm.
JOB_ID_PATTERN = re.compile(r"(?<!\d)(\d{6,9})(?!\d)")

# Where one sentence of a log entry ends: after ".", ";", "!" or "?" followed by spaces, or at a
# spaced dash " — ". `(?<=[.;!?])` = "preceded by one of these characters" (kept in the sentence).
SENTENCE_BREAK_PATTERN = re.compile(r"(?<=[.;!?])\s+|\s+—\s+")


def read_orchestrator_entries(paths):
    """Split the orchestrator log files into entries (a "- " line and its indented continuation).

    Parameters:
        paths (list of str): log files, e.g. ["~/workhub/orchestrator/log/2026-11.md" expanded].

    Returns:
        list of str: one text per entry.
    """
    entries = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as log_file:
            current = None
            for line in log_file:
                if line.startswith("- "):
                    # A new entry begins: keep the previous one.
                    if current is not None:
                        entries.append(current)
                    current = line
                elif current is not None and line.startswith(" "):
                    # An indented line continues the current entry.
                    current += line
                else:
                    # A blank line or a heading closes the current entry.
                    if current is not None:
                        entries.append(current)
                    current = None
            if current is not None:
                entries.append(current)
    return entries


def read_session_texts(sessions_json_path):
    """Read the conversations of session_digest.py as (task_id, text) pairs.

    Parameters:
        sessions_json_path (str or None): its sessions.json; None or missing = no pairs.

    Returns:
        list of tuple (str, str): the row a conversation recorded ("" when none) and all its
            requests and replies joined.
    """
    if not sessions_json_path or not os.path.isfile(sessions_json_path):
        return []
    with open(sessions_json_path, encoding="utf-8") as sessions_file:
        report = json.load(sessions_file)
    pairs = []
    for session in report.get("sessions", []):
        pieces = []
        for turn in session.get("turns", []):
            # `or ""` turns a missing (None) value into an empty text.
            pieces.append(turn.get("prompt") or "")
            pieces.append(turn.get("reply") or "")
        pairs.append((session.get("task") or "", "\n".join(pieces)))
    return pairs


def tasks_next_to_job_ids(entry, known_ids, task_pattern, prefix):
    """Find, in one orchestrator log entry, the row each job ID is written next to.

    An entry often names several rows ("Live check: TK-121 and TK-95 not started; 1000009
    PENDING"), so the entry's first Task ID is not reliable. A job ID is linked only to a Task ID
    written BEFORE it in the SAME sentence ("TK-121 [a2f4d1] submitted array 1000003_[2-63]");
    the closest one wins.

    Parameters:
        entry (str): the entry text.
        known_ids (set of str): job IDs of the week.
        task_pattern (re.Pattern): from recap_common.task_id_pattern(prefix).
        prefix (str): the Task ID prefix, e.g. "TK".

    Returns:
        list of tuple (job_id, task_id).
    """
    pairs = []
    for sentence in SENTENCE_BREAK_PATTERN.split(entry):
        for job_match in JOB_ID_PATTERN.finditer(sentence):
            if job_match.group(1) not in known_ids:
                continue
            nearest = ""
            for task_match in task_pattern.finditer(sentence):
                if task_match.start() > job_match.start():
                    break
                nearest = "%s-%d" % (prefix, int(task_match.group(1)))
            if nearest:
                pairs.append((job_match.group(1), nearest))
    return pairs


def link_jobs_to_rows(jobs, orchestrator_entries, session_pairs, task_pattern, prefix):
    """Set job["task_id"] and job["task_source"] for every job.

    Order of trust (first one that gives a row wins):
        1. a Task ID in the job name ("tk118_stage_b" -> TK-118);
        2. a conversation that RECORDED its row and mentions the job ID (the conversation that
           launched or followed the job; the orchestrator records no row, so it never counts here);
        3. the orchestrator log: the Task ID written closest before the job ID in the same sentence
           (tasks_next_to_job_ids); when several entries disagree, the most frequent row wins;
        4. a job still without a row: the row of the linked jobs in the same (specific) folder.

    Parameters:
        jobs (list of dict): the week's jobs (changed in place).
        orchestrator_entries (list of str): from read_orchestrator_entries.
        session_pairs (list of tuple): from read_session_texts.
        task_pattern (re.Pattern): from recap_common.task_id_pattern(prefix).
        prefix (str): the Task ID prefix, e.g. "TK".
    """
    # Job IDs of the week: "123_4" (array task) is also found by its array ID "123".
    known_ids = set()
    for job in jobs:
        known_ids.add(job["job_id"].split("_")[0])

    # Rule 2: job ID -> the recorded row of the first conversation that mentions it.
    row_from_conversation = {}
    for recorded_task, text in session_pairs:
        if not recorded_task:
            continue
        for match in JOB_ID_PATTERN.finditer(text):
            job_number = match.group(1)
            if job_number in known_ids and job_number not in row_from_conversation:
                row_from_conversation[job_number] = recorded_task

    # Rule 3: job ID -> votes for the rows written next to it in the log; most votes wins
    # (`max` with `key=votes.get` returns the key with the largest count; ties keep the first seen).
    votes_by_job = {}
    for entry in orchestrator_entries:
        for job_number, task_id in tasks_next_to_job_ids(entry, known_ids, task_pattern, prefix):
            votes = votes_by_job.setdefault(job_number, {})
            votes[task_id] = votes.get(task_id, 0) + 1
    row_from_log = {}
    for job_number, votes in votes_by_job.items():
        row_from_log[job_number] = max(votes, key=votes.get)

    for job in jobs:
        from_name = recap_common.task_ids_in(job["name"], task_pattern, prefix)
        array_id = job["job_id"].split("_")[0]
        if from_name:
            job["task_id"], job["task_source"] = from_name[0], "job name"
        elif array_id in row_from_conversation:
            job["task_id"], job["task_source"] = row_from_conversation[array_id], "conversation"
        elif array_id in row_from_log:
            job["task_id"], job["task_source"] = row_from_log[array_id], "orchestrator log"
        else:
            job["task_id"], job["task_source"] = "", ""

    # Rule 4: a job still without a row inherits the row of the linked jobs that ran in the same
    # working folder (a chain of jobs usually shares one folder, but only a few of its job IDs
    # are ever written down). Shallow folders such as /home/alex hold unrelated jobs, so a
    # folder counts only with at least 4 parts ("/scratch/alex/demo" has 3: too generic).
    votes_by_folder = {}
    for job in jobs:
        if job["task_id"] and folder_is_specific(job["workdir"]):
            votes = votes_by_folder.setdefault(job["workdir"], {})
            votes[job["task_id"]] = votes.get(job["task_id"], 0) + 1
    for job in jobs:
        if not job["task_id"] and job["workdir"] in votes_by_folder:
            votes = votes_by_folder[job["workdir"]]
            job["task_id"], job["task_source"] = max(votes, key=votes.get), "same folder"


def folder_is_specific(path):
    """Tell whether a working folder is deep enough to identify one piece of work.

    Parameters:
        path (str): e.g. "/project/def-pi/alex/demo/networks".

    Returns:
        bool: True with 4 or more path parts ("/home/alex" has 2: False).
    """
    # `split("/")` on "/a/b/c" gives ["", "a", "b", "c"]; empty pieces are dropped.
    parts = [piece for piece in path.split("/") if piece]
    return len(parts) >= 4


def load_rules(path):
    """Read the project rules file; no path (or an absent file) gives no rules.

    Parameters:
        path (str or None): the JSON file ({"rules": [{"label": ..., "patterns": [...]}]}).

    Returns:
        list of dict.
    """
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as rules_file:
        return json.load(rules_file).get("rules", [])


def rules_path_from_option(option_text, config):
    """Turn --project-rules into a file path ("" = no rules).

    Parameters:
        option_text (str): "auto" = skill_settings.weekly-recap.project_rules_file (default none);
            "none" = no rules; anything else = a path.
        config (dict): the settings.

    Returns:
        str: the expanded path, or "".
    """
    cleaned = (option_text or "").strip()
    if cleaned == "none":
        return ""
    if cleaned in ("", "auto"):
        cleaned = recap_common.recap_setting(config, "project_rules_file", default="") or ""
    return workhub_config.expand_path(cleaned)


def project_label(job, rules):
    """Return the job's project label: the first matching rule, else "other: <name prefix>".

    Parameters:
        job (dict): with "name" and "workdir".
        rules (list of dict): from load_rules.

    Returns:
        str: e.g. "Project A" or "other: demo".
    """
    haystack = (job["name"] + " " + job["workdir"]).lower()
    for rule in rules:
        for pattern in rule.get("patterns", []):
            if pattern.lower() in haystack:
                return rule["label"]
    # "demo_cpu_smoke" -> "demo": the part before the first underscore, dash or dot
    # (`[_\-.]` is a character class: "_", "-" (escaped) or ".").
    prefix = re.split(r"[_\-.]", job["name"].lower(), maxsplit=1)[0] or "unnamed"
    return "other: " + prefix


# ----------------------------------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------------------------------

def cluster_totals(jobs):
    """Add up one cluster's week.

    Parameters:
        jobs (list of dict): that cluster's jobs, with the week numbers added.

    Returns:
        dict: counts per week status, core-hours, GPU-hours (total, per GPU model and per Slurm
            account), GPU-hours of badly ended jobs, median queue wait in hours.
    """
    totals = {"jobs": len(jobs), "by_status": {}, "core_hours": 0.0, "gpu_hours": 0.0,
              "gpu_hours_by_type": {}, "by_account": {}, "bad_gpu_hours": 0.0, "bad_core_hours": 0.0,
              "median_wait_hours": None}
    waits = []
    for job in jobs:
        status = job["week_status"]
        totals["by_status"][status] = totals["by_status"].get(status, 0) + 1
        totals["core_hours"] += job["core_hours"]
        totals["gpu_hours"] += job["gpu_hours"]
        if job["gpus"]:
            gpu_type = job["gpu_type"] or "gpu"
            totals["gpu_hours_by_type"][gpu_type] = totals["gpu_hours_by_type"].get(gpu_type, 0.0) + job["gpu_hours"]
        # Per account: the Slurm account the job was charged to ("" when sacct printed none).
        account = job.get("account") or "(none)"
        account_totals = totals["by_account"].setdefault(account, {"jobs": 0, "core_hours": 0.0, "gpu_hours": 0.0})
        account_totals["jobs"] += 1
        account_totals["core_hours"] += job["core_hours"]
        account_totals["gpu_hours"] += job["gpu_hours"]
        if job["state"] in BAD_STATES:
            totals["bad_gpu_hours"] += job["gpu_hours"]
            totals["bad_core_hours"] += job["core_hours"]
        if job["wait_hours"] is not None:
            waits.append(job["wait_hours"])
    if waits:
        totals["median_wait_hours"] = statistics.median(waits)
    return totals


def work_groups(jobs):
    """Group the jobs by cluster and Work Hub row (or project label when there is no row).

    Parameters:
        jobs (list of dict): all jobs with week numbers, task_id and label.

    Returns:
        list of dict: one per group, the biggest (core-hours + 50 x GPU-hours) first. Each has the
            cluster, row, label, job count, statuses, hours, first start, last end, median CPU
            efficiency, up to 3 job names and up to 2 working folders.
    """
    groups = {}
    for job in jobs:
        key = (job["cluster"], job["task_id"] or "", job["label"])
        group = groups.setdefault(key, {"cluster": job["cluster"], "task_id": job["task_id"],
                                        "label": job["label"], "jobs": 0, "by_status": {},
                                        "core_hours": 0.0, "gpu_hours": 0.0, "first_start": None,
                                        "last_end": None, "efficiencies": [], "names": [],
                                        "workdirs": [], "job_ids": []})
        group["jobs"] += 1
        group["by_status"][job["week_status"]] = group["by_status"].get(job["week_status"], 0) + 1
        group["core_hours"] += job["core_hours"]
        group["gpu_hours"] += job["gpu_hours"]
        if job["start"] is not None and (group["first_start"] is None or job["start"] < group["first_start"]):
            group["first_start"] = job["start"]
        if job["end"] is not None and (group["last_end"] is None or job["end"] > group["last_end"]):
            group["last_end"] = job["end"]
        if job["cpu_efficiency"] is not None:
            group["efficiencies"].append(job["cpu_efficiency"])
        if job["name"] not in group["names"] and len(group["names"]) < 3:
            group["names"].append(job["name"])
        if job["workdir"] not in group["workdirs"] and len(group["workdirs"]) < 2:
            group["workdirs"].append(job["workdir"])
        group["job_ids"].append(job["job_id"])

    result = []
    for group in groups.values():
        efficiencies = group.pop("efficiencies")
        group["median_cpu_efficiency"] = statistics.median(efficiencies) if efficiencies else None
        result.append(group)

    # Sort key: GPU time weighs more than CPU time (one GPU-hour ~ 50 core-hours, roughly the
    # GPU/CPU billing ratio of large academic clusters); `reverse=True` puts the biggest first.
    result.sort(key=lambda group: group["core_hours"] + 50 * group["gpu_hours"], reverse=True)
    return result


def format_moment(moment):
    """Return "Thu Nov 5 18:51" (owner's time zone), or "-" for None."""
    if moment is None:
        return "-"
    return "%s %s" % (recap_common.readable_day(moment), moment.strftime("%H:%M"))


def format_summary(report):
    """Build the readable summary slurm_week.txt.

    Parameters:
        report (dict): the full report (see main()).

    Returns:
        str.
    """
    lines = []
    lines.append("Slurm jobs, week %s, %s time (collected %s on %s)"
                 % (report["week"]["label"], report["time_zone"], report["collected"], report["system"]))
    reached = [c["cluster"] for c in report["clusters"] if c["reached"]]
    missing = [c for c in report["clusters"] if not c["reached"]]
    lines.append("Clusters reached: " + (", ".join(reached) or "none"))
    for cluster in missing:
        lines.append("NOT reached: %s - %s" % (cluster["cluster"], cluster["note"]))
    if not report["clusters"]:
        lines.append("No Slurm clusters in the settings (machines.clusters).")
    lines.append("")

    lines.append("TOTALS PER CLUSTER (hours clipped to the week; GPU-h = GPUs held x hours)")
    for cluster in report["clusters"]:
        if not cluster["reached"]:
            continue
        totals = cluster["totals"]
        statuses = ", ".join("%s %d" % (name, count) for name, count in sorted(totals["by_status"].items()))
        by_type = ", ".join("%s %.1f" % (name, hours) for name, hours in sorted(totals["gpu_hours_by_type"].items()))
        wait = totals["median_wait_hours"]
        lines.append("  %-13s %4d jobs (%s)" % (cluster["cluster"], totals["jobs"], statuses or "none"))
        lines.append("                core-h %.1f | GPU-h %.1f%s | badly ended: core-h %.1f, GPU-h %.1f | median wait %s"
                     % (totals["core_hours"], totals["gpu_hours"], (" (" + by_type + ")") if by_type else "",
                        totals["bad_core_hours"], totals["bad_gpu_hours"],
                        ("%.1f h" % wait) if wait is not None else "-"))
        # Per account, with a mark on accounts that are not listed in the settings for this cluster.
        configured = set(cluster.get("accounts_configured") or [])
        account_parts = []
        for account, account_totals in sorted(totals["by_account"].items()):
            mark = "" if (not configured or account in configured) else " (not in the settings)"
            account_parts.append("%s%s: %d jobs, core-h %.1f, GPU-h %.1f"
                                 % (account, mark, account_totals["jobs"], account_totals["core_hours"],
                                    account_totals["gpu_hours"]))
        if account_parts:
            lines.append("                accounts: " + " | ".join(account_parts))
    if report.get("previous_week"):
        previous = report["previous_week"]
        lines.append("")
        lines.append("PREVIOUS WEEK %s (totals only)" % previous["label"])
        for cluster in previous["clusters"]:
            if cluster["reached"]:
                totals = cluster["totals"]
                lines.append("  %-13s %4d jobs | core-h %.1f | GPU-h %.1f | badly ended GPU-h %.1f"
                             % (cluster["cluster"], totals["jobs"], totals["core_hours"],
                                totals["gpu_hours"], totals["bad_gpu_hours"]))
            else:
                lines.append("  %-13s not reached" % cluster["cluster"])
    lines.append("")

    lines.append("WORK GROUPS (cluster | row or label | jobs | hours | when | CPU efficiency)")
    for group in report["groups"]:
        statuses = ", ".join("%s %d" % (name, count) for name, count in sorted(group["by_status"].items()))
        efficiency = group["median_cpu_efficiency"]
        lines.append("  %s | %s | %s | %d jobs (%s)"
                     % (group["cluster"], group["task_id"] or "no row", group["label"], group["jobs"], statuses))
        lines.append("      core-h %.1f, GPU-h %.1f | %s -> %s | median CPU eff %s"
                     % (group["core_hours"], group["gpu_hours"], format_moment(group["first_start"]),
                        format_moment(group["last_end"]),
                        ("%.0f %%" % (100 * efficiency)) if efficiency is not None else "-"))
        lines.append("      names: %s" % ", ".join(group["names"]))
        lines.append("      folders: %s" % ", ".join("%s:%s" % (group["cluster"], path) for path in group["workdirs"]))
    lines.append("")

    lines.append("PROBLEM JOBS (ended FAILED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL ... inside the week)")
    problem_count = 0
    for job in report["jobs"]:
        if job["state"] in BAD_STATES and job["end"] is not None and job["end"] < report["_end"]:
            problem_count += 1
            limit = job["time_limit_seconds"]
            lines.append("  %s %s %s %s exit %s, ran %.1f h of %s, ended %s, %s"
                         % (job["cluster"], job["job_id"], job["name"], job["state"], job["exit_code"],
                            job["elapsed_seconds"] / 3600.0, ("%.1f h" % (limit / 3600.0)) if limit else "?",
                            format_moment(job["end"]), job["task_id"] or job["label"]))
    if problem_count == 0:
        lines.append("  none")
    return "\n".join(lines) + "\n"


def to_json_ready(value):
    """Turn moments into ISO texts so json.dump can write the report (recursively).

    Keys that start with "_" (internal values such as "_end") are left out.
    """
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: to_json_ready(item) for key, item in value.items() if not key.startswith("_")}
    if isinstance(value, list):
        return [to_json_ready(item) for item in value]
    return value


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def build_parser():
    """Define the command-line options (see the file header)."""
    parser = argparse.ArgumentParser(
        description="Every Slurm job of one Monday-to-Sunday week on the reachable clusters of the "
                    "workhub-claude settings, with core-hours, GPU-hours, how it ended, and its Work Hub row. Read-only.")
    parser.add_argument("--week", default="last",
                        help="last (default), this, YYYY-Www (e.g. 2026-W45) or a day YYYY-MM-DD")
    parser.add_argument("--clusters", default="",
                        help="comma-separated cluster names (default: every Slurm cluster in the settings)")
    parser.add_argument("--orchestrator-log", default="auto",
                        help="'auto' (default: the week's monthly logs in the orchestrator folder), "
                             "'none', or log file(s), comma-separated")
    parser.add_argument("--sessions-json", default="",
                        help="sessions.json of session_digest.py for the week, to link job IDs to rows")
    parser.add_argument("--project-rules", default="auto",
                        help="'auto' (default: skill_settings.weekly-recap.project_rules_file), 'none', or a JSON file")
    parser.add_argument("--accounts-only", action="store_true",
                        help="ask sacct only for the slurm_accounts listed in the settings")
    parser.add_argument("--compare-previous", action="store_true",
                        help="also collect the week before (totals only)")
    parser.add_argument("-o", "--output-dir", default="",
                        help="folder for slurm_week.json and .txt (default <paths.outputs_dir>/weekly-recap/<week>/slurm)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files")
    parser.add_argument("--print", action="store_true", help="also print the summary")
    return parser


def collect_week(targets, start, end, timezone, zone_name, this_system, now, accounts_only):
    """Collect every cluster for one window and add the week numbers to each job.

    Returns:
        tuple (cluster_results, all_jobs).
    """
    cluster_results = []
    all_jobs = []
    for target in targets:
        result = collect_cluster(target, start, end, timezone, zone_name, this_system, accounts_only)
        for job in result["jobs"]:
            add_week_numbers(job, start, end, now)
        result["totals"] = cluster_totals(result["jobs"])
        cluster_results.append(result)
        all_jobs.extend(result["jobs"])
    return cluster_results, all_jobs


def choose_targets(config, clusters_option):
    """Return the Slurm targets to read: all of the settings, or the names given with --clusters."""
    targets = recap_common.slurm_targets(config)
    wanted = [name.strip() for name in (clusters_option or "").split(",") if name.strip()]
    if not wanted:
        return targets
    return [target for target in targets if target["name"] in wanted]


def orchestrator_log_paths(option_text, config, start):
    """Turn --orchestrator-log into a list of log files ("auto" = the week's months, "none" = [])."""
    cleaned = (option_text or "").strip()
    if cleaned == "none":
        return []
    if cleaned in ("", "auto"):
        folder = recap_common.orchestrator_dir(config)
        return [path for path, _ in recap_common.orchestrator_log_files(folder, start)]
    return [workhub_config.expand_path(path.strip()) for path in cleaned.split(",") if path.strip()]


def main():
    """Collect, link, summarise and write the outputs."""
    args = build_parser().parse_args()
    config = recap_common.load_settings()
    timezone = recap_common.owner_timezone(config)
    zone_name = workhub_config.time_zone(config)
    try:
        start, end, label = recap_common.week_window(args.week, timezone)
    except ValueError as error:
        print("slurm_week.py: %s" % error, file=sys.stderr)
        sys.exit(1)

    # Refuse early, before the slow collection, when outputs exist without --overwrite.
    output_dir = args.output_dir or recap_common.output_dir_for(config, recap_common.week_id(start), "slurm")
    json_path = os.path.join(output_dir, "slurm_week.json")
    text_path = os.path.join(output_dir, "slurm_week.txt")
    for path in (json_path, text_path):
        if os.path.exists(path) and not args.overwrite:
            print("slurm_week.py: %s exists; pass --overwrite to replace it" % path, file=sys.stderr)
            sys.exit(1)

    now = datetime.datetime.now(timezone)
    this_system = workhub_config.system_name(config)
    targets = choose_targets(config, args.clusters)
    prefix = recap_common.task_id_prefix(config)
    task_pattern = recap_common.task_id_pattern(prefix)

    # 1. The week itself.
    cluster_results, jobs = collect_week(targets, start, end, timezone, zone_name, this_system, now,
                                         args.accounts_only)

    # 2. Rows and labels.
    log_paths = orchestrator_log_paths(args.orchestrator_log, config, start)
    link_jobs_to_rows(jobs, read_orchestrator_entries(log_paths), read_session_texts(args.sessions_json),
                      task_pattern, prefix)
    rules_path = rules_path_from_option(args.project_rules, config)
    rules = load_rules(rules_path)
    for job in jobs:
        job["label"] = project_label(job, rules)

    # 3. Optional previous week, totals only (its jobs are not kept).
    previous = None
    if args.compare_previous:
        previous_start = start - datetime.timedelta(days=7)
        previous_start, previous_end, previous_label = recap_common.week_window(
            previous_start.date().isoformat(), timezone)
        previous_results, _ = collect_week(targets, previous_start, previous_end, timezone, zone_name,
                                           this_system, now, args.accounts_only)
        previous = {"label": previous_label,
                    "clusters": [{"cluster": r["cluster"], "reached": r["reached"], "note": r["note"],
                                  "totals": r["totals"]} for r in previous_results]}

    # 4. The report: cluster jobs are kept once, in the top-level "jobs" list.
    for result in cluster_results:
        result.pop("jobs")
    jobs.sort(key=lambda job: (job["cluster"], job["start"] or end))
    report = {"schema": "weekly-recap-slurm/1",
              "week": {"id": recap_common.week_id(start), "label": label,
                       "start": start, "end": end},
              "time_zone": zone_name,
              "collected": now.strftime("%Y-%m-%d %H:%M"), "system": this_system,
              "orchestrator_logs": log_paths, "project_rules": rules_path,
              "clusters": cluster_results, "groups": work_groups(jobs), "jobs": jobs,
              "previous_week": previous, "_end": end}

    summary = format_summary(report)
    recap_common.prepare_output_dir(output_dir)
    recap_common.write_private_text(json_path, json.dumps(to_json_ready(report), indent=1) + "\n", args.overwrite)
    recap_common.write_private_text(text_path, summary, args.overwrite)

    if args.print:
        print(summary)
    print("%d job(s) on %d reached cluster(s); summary %s" %
          (len(jobs), sum(1 for c in cluster_results if c["reached"]), text_path))


if __name__ == "__main__":
    main()
