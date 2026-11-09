#!/usr/bin/env python3
"""
live_state.py - what is running right now: Slurm jobs on the clusters, tmux sessions and busy
processes on the workstations. Read-only.

PURPOSE
    A Work Hub cleanup (skill `work-hub-cleanup`) must not mark a task Done while its jobs are
    still running, nor leave it "In progress" when its jobs finished or failed. Conversations
    say what was started; this script says what is still alive. It only reads: it never
    starts, stops or changes a job, a session or a file outside its output folder.

WHAT IS CHECKED (the machines come from the workhub-claude settings)
    Clusters (machines.clusters, scheduler "slurm"), through SSH connections that are ALREADY
    open (for example opened with `cluster-login`); a new login is never attempted, so there is
    never a password or two-factor prompt:
        squeue  the user's queued and running jobs (state, elapsed / limit, expected start);
        sacct   the user's jobs that ended since the start of the period (COMPLETED, FAILED,
                TIMEOUT, CANCELLED, OUT_OF_MEMORY ...), with their exit codes.
        Optional "hops" (machines.cluster_hops, shared with weekly-recap) are Slurm clusters that
        have their own login node and are reached through the open connection of another
        cluster, for example a separate GPU cluster: {"name": "mycluster-gpu",
        "through": "mycluster", "login_node": "gpu-login1"}.
    Workstations (machines.workstations): this machine directly, the others with
    `ssh -o BatchMode=yes <ssh_alias>` (SSH keys only; BatchMode forbids any prompt):
        tmux    the user's tmux sessions (name, windows, creation time, attached or not);
        GPU     how many processes of the user, and of other users, run on the GPU;
        ps      the user's long-running busy processes (running >= 10 minutes, and either on
                the GPU or using >= 20% CPU averaged over their lifetime).

OUTPUTS (in --output-dir, created if missing; existing files are replaced only with --overwrite)
    live_state.txt   the human-readable snapshot;
    live_state.json  the same facts as structured data.
    With --connections-only nothing is written: one line per cluster says whether its shared
    SSH connection is open.

USAGE
    python3 live_state.py --connections-only
    python3 live_state.py --since 2026-03-02T14:41+00:00 -o /tmp/hub/live
    python3 live_state.py --since 24h --clusters mycluster --workstations none -o /tmp/hub/live --print

SETTINGS USED
    owner.time_zone, system_name.*, machines.clusters[] (name, ssh_alias, scheduler),
    machines.workstations[] (name, ssh_alias), cluster_login.enabled (for the hints),
    machines.cluster_hops (optional, default []; shared with weekly-recap).

DEPENDENCIES
    Python >= 3.9 (for `zoneinfo`), standard library only, plus workhub_config.py (an identical
    copy of the kit's settings reader) in the same folder. On the remote side: bash, squeue and
    sacct (clusters), tmux, nvidia-smi and procps `ps` (workstations); a missing program only
    leaves its part empty.

ASSUMPTIONS
    - Slurm reads and prints times in the time zone of the process, which the variable TZ sets.
      The commands are therefore run with TZ=UTC: `sacct -S` receives the start in UTC, and
      every printed time is converted back to the user's zone, whatever the cluster's own zone
      is. The start is also moved SACCT_MARGIN_HOURS earlier as a safety margin; a few extra
      ended jobs may then be listed, but none is missed.
    - The CPU percentage of `ps` is averaged over the process's whole life, not instantaneous.

EXIT STATUS
    0 = success (unreachable machines are reported in the snapshot, not as errors);
    1 = an output file exists without --overwrite, or --since/--timezone cannot be read;
    2 = invalid command-line arguments (reported by argparse), or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (Python's standard library, then the kit's settings reader)
# ----------------------------------------------------------------------------------------------

# `argparse` turns the words typed after the script name into a Namespace object; it also
# prints --help and exits with status 2 on invalid input.
import argparse

# `datetime.datetime` is one moment (date + clock time + UTC offset); `datetime.timedelta` is a
# duration ("3 hours earlier", "36 hours before now").
import datetime

# `json` writes the structured snapshot.
import json

# `os` gives path helpers.
import os

# `shlex.quote(text)` wraps a text in quotes that a remote shell reads back unchanged; used to
# send a command through two SSH hops without the first shell altering it.
import shlex

# `subprocess.run` starts another program (bash, ssh) and waits for it to finish.
import subprocess

# `sys` gives `sys.exit(status)` and `sys.stderr`.
import sys

# `ZoneInfo("Europe/Lisbon")` is a time-zone object with that zone's daylight-saving rules.
from zoneinfo import ZoneInfo

# `workhub_config` is the kit's settings reader (workhub_config.py in this same folder; Python
# finds it because the folder of the script being run is searched first). Functions used here:
#   load_config()                  -> the settings as a dict (ConfigError when the file is missing);
#   get_setting(c, "a.b", default) -> one value by its dotted key;
#   skill_setting(c, skill, key, d)-> c["skill_settings"][skill][key], else d;
#   time_zone(c), system_name(c)   -> the user's zone name; this machine's short name;
#   clusters(c), workstations(c)   -> the lists machines.clusters and machines.workstations.
import workhub_config


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# This skill's name, under which its own options live in skill_settings.
SKILL_NAME = "work-hub-cleanup"

# Hours by which the sacct start time is moved earlier, as a safety margin (see ASSUMPTIONS).
SACCT_MARGIN_HOURS = 3

# A process counts as long-running after this many seconds (10 minutes).
MIN_PROCESS_SECONDS = 600

# A process counts as busy at this lifetime-average CPU percentage (100% = one full core).
MIN_PROCESS_CPU_PERCENT = 20.0

# Processes that are part of the desktop or of the AI tools themselves, not research work.
# A process is ignored when its command line contains one of these texts.
IGNORED_PROCESS_TEXTS = [".vscode-server", ".vscode/extensions", "/usr/share/code/", "code-server",
                         "/claude", "codex", "sshd", "tmux", "chrome", "firefox", "cinnamon", "gnome",
                         "Xorg", "dbus", "pulseaudio", "pipewire"]

# Section markers printed between the parts of one remote command, to split its output.
MARKER_SQUEUE = "@@SQUEUE@@"
MARKER_SACCT = "@@SACCT@@"
MARKER_TMUX = "@@TMUX@@"
MARKER_GPU = "@@GPU@@"
MARKER_PS = "@@PS@@"

# Maximum seconds for one machine's commands (sacct can take ~10 s on a busy cluster).
COMMAND_TIMEOUT_SECONDS = 90

# Texts Slurm prints instead of a time ("not known yet", "not applicable").
SLURM_NON_TIMES = ("Unknown", "N/A", "None", "")


# ----------------------------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------------------------

def parse_since(text, timezone):
    """Convert --since into an aware datetime (a datetime that carries its UTC offset).

    Accepted: ISO 8601 ("2026-03-02T14:41+00:00", "2026-03-02 14:41", "2026-03-02") or a
    duration before now ("36h", "2d"). These are the same forms as in session_digest.py; that
    script must stay self-contained (it is sent to the clusters through `python3 -`), hence
    this small copy instead of a shared module.

    Parameters:
        text (str): the --since value.
        timezone (ZoneInfo): zone used for texts without an offset.

    Returns:
        datetime.datetime: the aware moment.

    Raises:
        ValueError: for any other text (raised by `float` or `fromisoformat`).
    """
    # `.strip()` removes spaces and line breaks around the text.
    cleaned_text = text.strip()

    # Durations before now. `cleaned_text[:-1]` is the text without its last character, so
    # "36h" -> "36"; `float` turns it into the number 36.0.
    if cleaned_text.endswith("h") or cleaned_text.endswith("d"):
        amount = float(cleaned_text[:-1])

        # `timedelta(hours=36)` is a duration of 36 hours; `timedelta(days=2)` one of 2 days.
        if cleaned_text.endswith("h"):
            duration = datetime.timedelta(hours=amount)
        else:
            duration = datetime.timedelta(days=amount)

        # The current moment in the user's zone, minus the duration.
        now_moment = datetime.datetime.now(timezone)
        return now_moment - duration

    # ISO 8601. Python 3.9-3.10 refuse a final "Z" (= UTC), so it becomes "+00:00".
    if cleaned_text.endswith("Z"):
        cleaned_text = cleaned_text[:-1] + "+00:00"

    # `fromisoformat` parses the text; a date alone gives midnight at the start of that day.
    moment = datetime.datetime.fromisoformat(cleaned_text)

    # Without an offset ("naive" datetime) the clock time is read in the user's zone:
    # `.replace(tzinfo=...)` attaches the zone without changing the clock time.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone)

    # DEBUG CHECK: `moment.utcoffset()` is not None.
    return moment


def run_command(command, timeout=COMMAND_TIMEOUT_SECONDS):
    """Run a program, capture its output, and never raise.

    Parameters:
        command (list of str): the program and its arguments, e.g. ["ssh", "-O", "check", "mycluster"].
        timeout (int): seconds before giving up.

    Returns:
        tuple (status, stdout, stderr): status is the program's exit status, or -1 when it could
            not be started or timed out (the reason is then in stderr).
    """
    # `subprocess.run` starts the program and waits for it. `capture_output=True` collects what
    # it prints (stdout) and its error messages (stderr); `text=True` decodes them into text;
    # `timeout` stops waiting after that many seconds (raising TimeoutExpired).
    # `stdin=subprocess.DEVNULL` gives the program an empty input, so an unexpected question
    # can never wait for an answer.
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        # The program hung (for example an unreachable host); report it as a failure.
        return -1, "", "timed out after %d s" % timeout
    except OSError as error:
        # The program does not exist or cannot be started (e.g. no `ssh` installed).
        return -1, "", str(error)

    # `returncode` is the exit status: 0 = success.
    return completed.returncode, completed.stdout, completed.stderr


def connection_is_open(ssh_alias):
    """Tell whether a shared SSH connection to `ssh_alias` is already open.

    `ssh -O check <alias>` asks the local "ControlMaster" process (the shared connection opened
    for example by `cluster-login`) whether it is alive. It never starts a new login, so it can
    never trigger a password or two-factor prompt.

    Parameters:
        ssh_alias (str): host alias from ~/.ssh/config, e.g. "mycluster".

    Returns:
        bool: True when the connection is open.
    """
    # Only the exit status matters; `_` is the usual name for values that are not used.
    status, _, _ = run_command(["ssh", "-O", "check", ssh_alias], timeout=15)
    return status == 0


def connection_hint(ssh_alias, config):
    """Return what the user should do to open the connection to `ssh_alias`.

    Parameters:
        ssh_alias (str): the cluster's SSH alias.
        config (dict): the settings (cluster_login.enabled).

    Returns:
        str: e.g. "run `cluster-login mycluster` on this machine".
    """
    # The kit's `cluster-login` tool opens shared connections when the user installed it.
    if workhub_config.get_setting(config, "cluster_login.enabled", default=False):
        return "run `cluster-login %s` on this machine" % ssh_alias
    return "open a shared SSH connection to %s on this machine first (ssh ControlMaster)" % ssh_alias


def split_sections(text, markers):
    """Split a command's output at marker lines.

    Example: "@@A@@\\nx\\n@@B@@\\ny\\nz" with markers ["@@A@@", "@@B@@"]
             -> {"@@A@@": ["x"], "@@B@@": ["y", "z"]}

    Parameters:
        text (str): the output.
        markers (list of str): the marker lines, in any order.

    Returns:
        dict: marker -> list of the non-empty lines that follow it, up to the next marker.
    """
    # Every marker starts with an empty list, so a part that printed nothing reads as [].
    sections = {}
    for marker in markers:
        sections[marker] = []

    # `current_marker` remembers which part the following lines belong to. Lines before the
    # first marker (for example a login message) belong to no part and are ignored.
    current_marker = None

    # `.splitlines()` cuts the text into its lines (without the line-break characters).
    for line in text.splitlines():
        stripped_line = line.strip()

        # A marker line switches the current part; any other non-empty line is stored in it.
        if stripped_line in sections:
            current_marker = stripped_line
        elif current_marker is not None and stripped_line:
            sections[current_marker].append(stripped_line)

    # DEBUG CHECK: `sections` has exactly the keys in `markers`.
    return sections


def seconds_to_text(seconds):
    """Format a duration for people.

    Examples: 300 -> "5 min", 11400 -> "3 h 10 min", 200000 -> "2 d 7 h".

    Parameters:
        seconds (int or float): the duration.

    Returns:
        str: the formatted duration.
    """
    # `//` is whole-number division: 11400 // 60 = 190 minutes.
    minutes_total = int(seconds) // 60

    # `divmod(a, b)` returns the pair (a // b, a % b): whole units and the remainder.
    # 190 minutes -> (3 hours, 10 minutes); 3333 hours -> (138 days, 21 hours).
    hours_total, minutes = divmod(minutes_total, 60)
    days, hours = divmod(hours_total, 24)

    # Show the two largest units only.
    if days > 0:
        return "%d d %d h" % (days, hours)
    if hours > 0:
        return "%d h %d min" % (hours, minutes)
    return "%d min" % minutes


def utc_slurm_time_to_local(text, timezone):
    """Convert a time printed by Slurm under TZ=UTC into the user's clock time.

    Examples (timezone Europe/Lisbon, UTC+1 in summer):
        "2026-07-13T12:50:02" -> "2026-07-13 13:50"
        "Unknown"             -> "Unknown"   (also "N/A", "None" and anything unreadable)

    Parameters:
        text (str): a Slurm time "YYYY-MM-DDTHH:MM:SS" in UTC, or a non-time word.
        timezone (ZoneInfo): the user's zone.

    Returns:
        str: "YYYY-MM-DD HH:MM" in the user's zone, or `text` unchanged.
    """
    # Words such as "Unknown" are kept as they are.
    if text in SLURM_NON_TIMES:
        return text

    # `fromisoformat` reads "2026-07-13T12:50:02"; anything else raises ValueError and the
    # text is kept unchanged (it is still shown, just not converted).
    try:
        naive_moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return text

    # The text carried no offset; it is UTC because the command ran with TZ=UTC.
    # `.replace(tzinfo=utc)` attaches UTC; `.astimezone(zone)` moves it to the user's clock.
    utc_moment = naive_moment.replace(tzinfo=datetime.timezone.utc)
    local_moment = utc_moment.astimezone(timezone)

    # DEBUG CHECK: the result has 16 characters, e.g. "2026-07-13 13:50".
    return local_moment.strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------------------------
# Which machines (from the settings)
# ----------------------------------------------------------------------------------------------

def slurm_targets(config):
    """Return every Slurm cluster that can be checked: the configured clusters, then the hops.

    Parameters:
        config (dict): the settings.

    Returns:
        list of dict: {"name", "ssh_alias", "scheduler", "through", "login_node"}. For a
            cluster, "through" and "login_node" are "". For a hop, "ssh_alias" is the alias of
            the cluster it goes through.
    """
    targets = []

    # Clusters of machines.clusters. A missing ssh_alias falls back to the name; a missing
    # scheduler is taken as Slurm (the only scheduler this script reads).
    alias_by_name = {}
    for cluster in workhub_config.clusters(config):
        name = str(cluster.get("name") or "").strip()
        if not name:
            continue
        ssh_alias = str(cluster.get("ssh_alias") or name).strip()
        scheduler = str(cluster.get("scheduler") or "slurm").strip().lower()
        alias_by_name[name] = ssh_alias
        targets.append({"name": name, "ssh_alias": ssh_alias, "scheduler": scheduler,
                        "through": "", "login_node": ""})

    # Hops: Slurm clusters with their own login node, reached through another cluster's open
    # connection. Entries without the three texts are skipped.
    hops = workhub_config.get_setting(config, "machines.cluster_hops", default=[])
    if not isinstance(hops, list):
        hops = []
    for hop in hops:
        if not isinstance(hop, dict):
            continue
        name = str(hop.get("name") or "").strip()
        through = str(hop.get("through") or "").strip()
        login_node = str(hop.get("login_node") or "").strip()
        if not (name and through and login_node):
            continue
        # `dict.get(key, default)`: the alias of the cluster named `through`, else `through`
        # itself (it may already be an SSH alias).
        targets.append({"name": name, "ssh_alias": alias_by_name.get(through, through),
                        "scheduler": "slurm", "through": through, "login_node": login_node})

    # DEBUG CHECK: one dict per configured cluster, plus one per valid hop.
    return targets


def workstation_targets(config):
    """Return every configured workstation as {"name", "ssh_alias"}.

    Parameters:
        config (dict): the settings.

    Returns:
        list of dict: in the order of machines.workstations.
    """
    targets = []
    for workstation in workhub_config.workstations(config):
        name = str(workstation.get("name") or "").strip()
        if not name:
            continue
        targets.append({"name": name, "ssh_alias": str(workstation.get("ssh_alias") or "").strip()})
    return targets


def select_targets(argument, known_targets, auto_names, kind):
    """Turn --clusters or --workstations into the list of machines to check.

    Parameters:
        argument (str): "auto", "none" or "a,b,c" (names or SSH aliases).
        known_targets (list of dict): from slurm_targets() or workstation_targets().
        auto_names (list of str): the names used for "auto".
        kind (str): "cluster" or "workstation", for machines not in the settings.

    Returns:
        list of dict: the selected machines. A name not in the settings is taken as an SSH
            alias of its own (a cluster with Slurm, or a workstation).
    """
    # "none": nothing.
    if argument == "none":
        return []

    # "auto" uses the names given by the caller; otherwise "a, b" -> ["a", "b"] (cut at
    # commas, strip spaces, drop empty parts).
    if argument == "auto":
        wanted_names = list(auto_names)
    else:
        wanted_names = []
        for part in argument.split(","):
            if part.strip():
                wanted_names.append(part.strip())

    # Look each wanted name up among the known machines, by name first, then by SSH alias.
    selected = []
    for wanted_name in wanted_names:
        found = None
        for target in known_targets:
            if target["name"] == wanted_name or target["ssh_alias"] == wanted_name:
                found = target
                break
        if found is None:
            if kind == "cluster":
                found = {"name": wanted_name, "ssh_alias": wanted_name, "scheduler": "slurm",
                         "through": "", "login_node": ""}
            else:
                found = {"name": wanted_name, "ssh_alias": wanted_name}
        selected.append(found)
    return selected


# ----------------------------------------------------------------------------------------------
# Clusters (Slurm)
# ----------------------------------------------------------------------------------------------

def slurm_shell_command(sacct_start_utc_text):
    """Build the shell command run on a cluster: a marker, squeue, a marker, sacct.

    Parameters:
        sacct_start_utc_text (str): start for `sacct -S`, in UTC, e.g. "2026-03-02T11:41".

    Returns:
        str: one shell command line (the remote shell expands "$USER").
    """
    # `TZ=UTC command` sets the time zone of that one command to UTC, so Slurm reads -S and
    #   prints every time in UTC (checked: the printed End times move by the cluster's offset).
    # squeue -u "$USER": the user's jobs; -h: no header line; -o: the output format, fields
    #   separated by "|": %i job id, %j job name, %T state, %M time used, %l time limit,
    #   %S start time (expected start for a pending job), %R the reason a job waits
    #   ("(Priority)") or the nodes it runs on.
    # sacct -u "$USER" -X: the user's jobs, one line per job (no job steps); -n: no header;
    #   -P: fields separated by "|"; -S: jobs active since that time; -o: the fields.
    # `2>&1` sends error messages into the normal output, so "command not found" is visible.
    # In a Python format string `%%` stands for a literal "%", so '%%i' becomes '%i' here.
    return ("echo %s; TZ=UTC squeue -u \"$USER\" -h -o '%%i|%%j|%%T|%%M|%%l|%%S|%%R' 2>&1; "
            "echo %s; TZ=UTC sacct -u \"$USER\" -X -n -P -S %s -o JobID,JobName,State,Elapsed,End,ExitCode 2>&1"
            % (MARKER_SQUEUE, MARKER_SACCT, sacct_start_utc_text))


def parse_squeue_lines(lines):
    """Parse squeue lines "id|name|state|used|limit|start|reason".

    Example line: "1000147|demo_f2|PENDING|0:00|23:50:00|2026-03-03T22:38:59|(Priority)".

    Parameters:
        lines (list of str): the lines after the squeue marker.

    Returns:
        tuple (jobs, problems): jobs = list of dicts with keys job_id, name, state, elapsed,
            limit, start, reason; problems = lines that are not job lines (error messages).
    """
    jobs = []
    problems = []
    for line in lines:
        # `.split("|")` cuts the line at every "|" into a list of fields.
        fields = line.split("|")

        # A job line has exactly 7 fields; anything else is a message, kept for the snapshot.
        if len(fields) != 7:
            problems.append(line)
            continue

        # One dictionary per job, with named fields.
        job = {"job_id": fields[0], "name": fields[1], "state": fields[2], "elapsed": fields[3],
               "limit": fields[4], "start": fields[5], "reason": fields[6]}
        jobs.append(job)
    return jobs, problems


def parse_sacct_lines(lines):
    """Parse sacct lines "id|name|state|elapsed|end|exitcode"; keep the jobs that ended.

    Running and pending jobs are skipped here because squeue already lists them.
    Example line: "1000149|demo_scaling|COMPLETED|00:04:20|2026-03-02T12:50:02|0:0".

    Parameters:
        lines (list of str): the lines after the sacct marker.

    Returns:
        tuple (jobs, problems): ended jobs (dicts with job_id, name, state, elapsed, end,
            exit_code) and lines that are not job lines.
    """
    jobs = []
    problems = []
    for line in lines:
        fields = line.split("|")

        # A job line has exactly 6 fields.
        if len(fields) != 6:
            problems.append(line)
            continue

        # Jobs that have not ended (their End field reads "Unknown") are skipped.
        state = fields[2]
        if state in ("RUNNING", "PENDING", "REQUEUED", "SUSPENDED"):
            continue

        # The ended job. Its state can be COMPLETED, FAILED, TIMEOUT, OUT_OF_MEMORY, or
        # "CANCELLED by <uid>"; the exit code reads "<status>:<signal>", e.g. "0:0".
        job = {"job_id": fields[0], "name": fields[1], "state": state, "elapsed": fields[3],
               "end": fields[4], "exit_code": fields[5]}
        jobs.append(job)
    return jobs, problems


def query_cluster(target, sacct_start_utc_text, this_system, timezone, config):
    """Read the user's Slurm jobs on one cluster.

    Parameters:
        target (dict): from slurm_targets() / select_targets().
        sacct_start_utc_text (str): start for `sacct -S`, in UTC.
        this_system (str): this machine's system name; when the script runs on that very
            cluster, it is read directly, without SSH.
        timezone (ZoneInfo): the user's zone, for the printed times.
        config (dict): the settings (for the connection hint).

    Returns:
        dict: {"system", "reached", "note", "running", "pending", "ended", "problems"}; the
            jobs' "start" and "end" are in the user's zone ("YYYY-MM-DD HH:MM").
    """
    # The result starts as "not reached"; it is filled step by step below.
    result = {"system": target["name"], "reached": False, "note": "", "running": [], "pending": [],
              "ended": [], "problems": []}

    # Only Slurm is read. Another scheduler gets a note instead of a wrong answer.
    if target["scheduler"] != "slurm":
        result["note"] = "scheduler %r is not read by this script (only Slurm)" % target["scheduler"]
        return result

    # The same shell command runs whatever the route.
    shell_command = slurm_shell_command(sacct_start_utc_text)

    # Choose the route: directly (this machine), a hop through another cluster, or one SSH hop.
    # `shlex.quote` wraps a text in single quotes, so the first remote shell passes the login
    # node and the inner command unchanged to the second `ssh`, whose shell expands "$USER".
    if target["name"] == this_system and not target["login_node"]:
        # `bash -c "<command>"` runs one command line in a new bash shell.
        command = ["bash", "-c", shell_command]
    elif target["login_node"] and target["through"] == this_system:
        # A hop while running on the cluster it goes through: one SSH step to its login node.
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target["login_node"], shell_command]
    elif target["login_node"]:
        # A hop: through the open connection of the cluster named in "through".
        if not connection_is_open(target["ssh_alias"]):
            result["note"] = ("no open shared SSH connection to %s; %s"
                              % (target["ssh_alias"], connection_hint(target["ssh_alias"], config)))
            return result
        hop_command = ("ssh -o BatchMode=yes -o ConnectTimeout=10 " + shlex.quote(target["login_node"])
                       + " " + shlex.quote(shell_command))
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target["ssh_alias"], hop_command]
    else:
        # Any other cluster: only through an already-open shared connection (no prompt).
        if not connection_is_open(target["ssh_alias"]):
            result["note"] = ("no open shared SSH connection; %s"
                              % connection_hint(target["ssh_alias"], config))
            return result

        # `BatchMode=yes` forbids any password or two-factor prompt; `ConnectTimeout=10` gives
        # up after 10 seconds.
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target["ssh_alias"], shell_command]

    # Run it, then cut the output into its squeue part and its sacct part.
    status, stdout, stderr = run_command(command)
    sections = split_sections(stdout, [MARKER_SQUEUE, MARKER_SACCT])

    # A failure with no output at all means the command never ran on the cluster.
    if status != 0 and not sections[MARKER_SQUEUE] and not sections[MARKER_SACCT]:
        # `stderr.strip()[:200]` keeps the first 200 characters of the error message; when it
        # is empty, `or` falls back to the exit status.
        result["note"] = "command failed: %s" % (stderr.strip()[:200] or "exit status %d" % status)
        return result

    # Parse both parts.
    queued_jobs, squeue_problems = parse_squeue_lines(sections[MARKER_SQUEUE])
    ended_jobs, sacct_problems = parse_sacct_lines(sections[MARKER_SACCT])

    # Times were printed in UTC (TZ=UTC); they are shown in the user's zone.
    for job in queued_jobs:
        job["start"] = utc_slurm_time_to_local(job["start"], timezone)
    for job in ended_jobs:
        job["end"] = utc_slurm_time_to_local(job["end"], timezone)

    # Running jobs and waiting jobs (PENDING, CONFIGURING, ...) are listed separately.
    for job in queued_jobs:
        if job["state"] == "RUNNING":
            result["running"].append(job)
        else:
            result["pending"].append(job)

    # The cluster answered: store the rest.
    result["reached"] = True
    result["ended"] = ended_jobs
    result["problems"] = squeue_problems + sacct_problems

    # DEBUG CHECK: len(running) + len(pending) == number of squeue job lines.
    return result


# ----------------------------------------------------------------------------------------------
# Workstations (tmux, GPU, processes)
# ----------------------------------------------------------------------------------------------

def workstation_shell_command():
    """Build the shell command run on a workstation: tmux sessions, GPU processes, ps.

    Returns:
        str: one shell command line.
    """
    # tmux ls -F '<format>': one line per tmux session, fields separated by "|": name, number of
    #   windows, creation time (Unix seconds), number of attached clients. `2>/dev/null` hides
    #   the "no server running" message of a machine without tmux sessions.
    # nvidia-smi --query-compute-apps=pid,used_memory: every process on the GPU and its memory
    #   (MiB), printed as bare CSV numbers (noheader, nounits).
    # ps -u "$USER" -o pid=,etimes=,pcpu=,args=: the user's processes with process id, seconds
    #   since start, lifetime-average CPU %, and command line; "=" after each field name
    #   removes the header; --sort=-pcpu lists the busiest first.
    return ("echo %s; tmux ls -F '#{session_name}|#{session_windows}|#{session_created}|#{session_attached}' 2>/dev/null; "
            "echo %s; nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null; "
            "echo %s; ps -u \"$USER\" -o pid=,etimes=,pcpu=,args= --sort=-pcpu 2>/dev/null"
            % (MARKER_TMUX, MARKER_GPU, MARKER_PS))


def parse_tmux_lines(lines):
    """Parse tmux lines "name|windows|created|attached".

    Example: "demo_run|1|1772000000|1" -> {"name": "demo_run", "windows": 1,
             "created": 1772000000, "attached": True}.

    Parameters:
        lines (list of str): the lines after the tmux marker.

    Returns:
        list of dict: one per session.
    """
    sessions = []
    for line in lines:
        fields = line.split("|")

        # A session line has exactly 4 fields; anything else is skipped.
        if len(fields) != 4:
            continue

        # `int(...)` turns the digits into numbers; it raises ValueError on anything else,
        # in which case the line is skipped.
        try:
            session = {"name": fields[0], "windows": int(fields[1]), "created": int(fields[2]),
                       "attached": int(fields[3]) > 0}
        except ValueError:
            continue
        sessions.append(session)
    return sessions


def parse_gpu_lines(lines):
    """Parse nvidia-smi lines "pid, memory".

    Example: "123456, 2450" -> {123456: 2450} (memory in MiB).

    Parameters:
        lines (list of str): the lines after the GPU marker.

    Returns:
        dict: process id (int) -> GPU memory in MiB (int).
    """
    gpu_memory_by_pid = {}
    for line in lines:
        # "123456, 2450".split(",") -> ["123456", " 2450"]; each part is then stripped.
        raw_fields = line.split(",")
        fields = []
        for raw_field in raw_fields:
            fields.append(raw_field.strip())

        # Exactly two fields, both numbers; anything else is skipped.
        if len(fields) != 2:
            continue
        try:
            process_id = int(fields[0])
            memory_mib = int(fields[1])
        except ValueError:
            continue
        gpu_memory_by_pid[process_id] = memory_mib
    return gpu_memory_by_pid


def parse_ps_lines(lines):
    """Parse ps lines "pid etimes pcpu args...".

    Example: "4242 7200 95.0 python3 train.py --fold 2" -> {"pid": 4242, "seconds": 7200,
             "cpu": 95.0, "args": "python3 train.py --fold 2"}.

    Parameters:
        lines (list of str): the lines after the ps marker.

    Returns:
        list of dict: one per process, in the order given (busiest first).
    """
    processes = []
    for line in lines:
        # `split(None, 3)` splits on runs of white space at most 3 times, so the command line,
        # which contains spaces, stays whole in the fourth part.
        fields = line.split(None, 3)
        if len(fields) < 4:
            continue

        # Numbers are converted; a line that does not convert is skipped.
        try:
            process = {"pid": int(fields[0]), "seconds": int(fields[1]), "cpu": float(fields[2]),
                       "args": fields[3]}
        except ValueError:
            continue
        processes.append(process)
    return processes


def busy_processes(processes, gpu_memory_by_pid):
    """Select the user's long-running research processes.

    Kept: processes running for at least MIN_PROCESS_SECONDS that are on the GPU or use at
    least MIN_PROCESS_CPU_PERCENT CPU, except desktop and AI-tool processes. At most 8.

    Parameters:
        processes (list of dict): from parse_ps_lines(), busiest first.
        gpu_memory_by_pid (dict): from parse_gpu_lines().

    Returns:
        list of dict: the kept processes, each with an extra key "gpu_mib" (0 when not on GPU).
    """
    selected = []
    for process in processes:
        # Too young: a short command, not a long run.
        if process["seconds"] < MIN_PROCESS_SECONDS:
            continue

        # Neither on the GPU nor busy on the CPU: an idle helper.
        on_gpu = process["pid"] in gpu_memory_by_pid
        if not on_gpu and process["cpu"] < MIN_PROCESS_CPU_PERCENT:
            continue

        # Desktop and AI-tool processes: ignored when any ignored text is in the command line.
        is_ignored = False
        for ignored_text in IGNORED_PROCESS_TEXTS:
            if ignored_text in process["args"]:
                is_ignored = True
                break
        if is_ignored:
            continue

        # A copy of the process with its GPU memory added. `dict(process)` copies the
        # dictionary, so the original list is not modified.
        kept_process = dict(process)
        kept_process["gpu_mib"] = gpu_memory_by_pid.get(process["pid"], 0)
        selected.append(kept_process)

    # `[:8]` keeps the first 8 (the busiest, since ps sorted them).
    return selected[:8]


def query_workstation(target, this_system):
    """Read the user's tmux sessions, GPU use and busy processes on one workstation.

    Parameters:
        target (dict): {"name", "ssh_alias"}, from workstation_targets() / select_targets().
        this_system (str): this machine's name; this machine is read directly, without SSH.

    Returns:
        dict: {"system", "reached", "note", "tmux", "gpu_mine_count", "gpu_mine_mib",
            "gpu_others_count", "busy"}.
    """
    # The result starts as "not reached" with empty parts.
    result = {"system": target["name"], "reached": False, "note": "", "tmux": [], "gpu_mine_count": 0,
              "gpu_mine_mib": 0, "gpu_others_count": 0, "busy": []}
    shell_command = workstation_shell_command()

    # This machine directly; the others through SSH with keys only (BatchMode: no prompt).
    # A workstation without an SSH alias in the settings cannot be reached from here.
    if target["name"] == this_system:
        command = ["bash", "-c", shell_command]
    elif not target["ssh_alias"]:
        result["note"] = "no ssh_alias for it in the settings (machines.workstations)"
        return result
    else:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target["ssh_alias"], shell_command]
    status, stdout, stderr = run_command(command)

    # Without the first marker in the output, the command never ran (SSH refused, host down).
    if MARKER_TMUX not in stdout:
        result["note"] = "not reachable: %s" % (stderr.strip()[:200] or "exit status %d" % status)
        return result

    # Cut the output into its three parts and parse each.
    sections = split_sections(stdout, [MARKER_TMUX, MARKER_GPU, MARKER_PS])
    processes = parse_ps_lines(sections[MARKER_PS])
    gpu_memory_by_pid = parse_gpu_lines(sections[MARKER_GPU])

    # The user's process ids, to tell the user's GPU processes from other users'.
    # A `set` answers "is this id in it?" quickly.
    my_process_ids = set()
    for process in processes:
        my_process_ids.add(process["pid"])

    # Count GPU processes and memory: the user's, and other users'.
    # `.items()` yields (key, value) pairs: (process id, memory in MiB).
    for process_id, memory_mib in gpu_memory_by_pid.items():
        if process_id in my_process_ids:
            result["gpu_mine_count"] += 1
            result["gpu_mine_mib"] += memory_mib
        else:
            result["gpu_others_count"] += 1

    # Store the parts.
    result["reached"] = True
    result["tmux"] = parse_tmux_lines(sections[MARKER_TMUX])
    result["busy"] = busy_processes(processes, gpu_memory_by_pid)
    return result


# ----------------------------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------------------------

def format_snapshot(report, timezone):
    """Build the text of live_state.txt.

    Parameters:
        report (dict): {"collected", "system", "sacct_start_utc", "clusters", "workstations"}.
        timezone (ZoneInfo): zone for printed times.

    Returns:
        str: the snapshot text.
    """
    # The snapshot is built as a list of lines, joined at the end.
    lines = []

    # Header: when and where, and from when ended jobs are listed.
    # `fromtimestamp(seconds, zone)` turns Unix seconds into an aware moment in that zone;
    # `strftime` formats it: %a weekday, %m month, %d day, %H:%M clock.
    collected = datetime.datetime.fromtimestamp(report["collected"], timezone)
    lines.append("Live state at %s (%s), collected on %s"
                 % (collected.strftime("%a %m-%d %H:%M"), timezone.key, report["system"]))
    lines.append("Ended Slurm jobs listed since %s UTC (%d h before the period, as a margin); "
                 "job times below are in %s"
                 % (report["sacct_start_utc"], SACCT_MARGIN_HOURS, timezone.key))
    lines.append("")

    # ---- Slurm: one block per cluster ----
    lines.append("== Slurm")
    if not report["clusters"]:
        lines.append("(no cluster checked)")
    for cluster in report["clusters"]:
        # An unreached cluster gets one line with the reason and the fix.
        if not cluster["reached"]:
            lines.append("%s: NOT REACHED - %s" % (cluster["system"], cluster["note"]))
            continue

        # Count the ended jobs by state, e.g. {"COMPLETED": 3, "FAILED": 1}.
        # `dict.get(key, 0)` returns 0 for a state not counted yet.
        ended_counts = {}
        for job in cluster["ended"]:
            ended_counts[job["state"]] = ended_counts.get(job["state"], 0) + 1

        # "COMPLETED 3, FAILED 1", in alphabetical order of the state.
        ended_parts = []
        for state in sorted(ended_counts):
            ended_parts.append("%s %d" % (state, ended_counts[state]))
        ended_text = ", ".join(ended_parts)

        # The cluster's summary line.
        has_jobs = bool(cluster["running"] or cluster["pending"] or cluster["ended"])
        if not has_jobs:
            lines.append("%s: no jobs" % cluster["system"])
        else:
            counts_text = "%d running, %d pending, %d ended" % (
                len(cluster["running"]), len(cluster["pending"]), len(cluster["ended"]))
            if ended_text:
                counts_text += " (" + ended_text + ")"
            lines.append("%s: %s" % (cluster["system"], counts_text))

        # One line per job. For running jobs, squeue's last field is the node list.
        for job in cluster["running"]:
            lines.append("   RUNNING %s %s  elapsed %s of %s  on %s"
                         % (job["job_id"], job["name"], job["elapsed"], job["limit"], job["reason"]))
        for job in cluster["pending"]:
            # squeue already writes the reason in parentheses, e.g. "(Priority)".
            lines.append("   %s %s %s  expected start %s  reason %s"
                         % (job["state"], job["job_id"], job["name"], job["start"], job["reason"]))
        for job in cluster["ended"]:
            lines.append("   ended %s %s %s  end %s  elapsed %s  exit %s"
                         % (job["state"], job["job_id"], job["name"], job["end"], job["elapsed"], job["exit_code"]))

        # Up to 3 error messages, cut to 200 characters (`[:3]` = the first 3 items).
        for problem in cluster["problems"][:3]:
            lines.append("   (message: %s)" % problem[:200])

    # ---- Workstations: tmux sessions, GPU and busy processes ----
    lines.append("")
    lines.append("== Workstations")
    if not report["workstations"]:
        lines.append("(no workstation checked)")
    for workstation in report["workstations"]:
        if not workstation["reached"]:
            lines.append("%s: NOT REACHED - %s" % (workstation["system"], workstation["note"]))
            continue

        # "name (1 window(s), created Mon 03-02 09:00, attached)" for each tmux session.
        tmux_parts = []
        for session in workstation["tmux"]:
            created = datetime.datetime.fromtimestamp(session["created"], timezone)
            attached_text = ", attached" if session["attached"] else ""
            tmux_parts.append("%s (%d window(s), created %s%s)"
                              % (session["name"], session["windows"], created.strftime("%a %m-%d %H:%M"),
                                 attached_text))
        if tmux_parts:
            tmux_text = "; ".join(tmux_parts)
        else:
            tmux_text = "none"
        lines.append("%s: tmux: %s" % (workstation["system"], tmux_text))

        # GPU use: memory in GB (1 GB = 1024 MiB here).
        lines.append("   GPU: %d process(es) of yours using %.1f GB; %d process(es) of other users"
                     % (workstation["gpu_mine_count"], workstation["gpu_mine_mib"] / 1024.0,
                        workstation["gpu_others_count"]))

        # Busy processes; `%.0f%%` prints the CPU percentage without decimals, then "%".
        # An explicit "none" shows that the check ran and found nothing.
        if not workstation["busy"]:
            lines.append("   busy processes: none")
        for process in workstation["busy"]:
            gpu_text = ""
            if process["gpu_mib"]:
                gpu_text = ", GPU %.1f GB" % (process["gpu_mib"] / 1024.0)
            lines.append("   busy: pid %d, running %s, %.0f%% CPU%s: %s"
                         % (process["pid"], seconds_to_text(process["seconds"]), process["cpu"], gpu_text,
                            process["args"][:160]))

    # One text with a line break between lines and at the end.
    return "\n".join(lines) + "\n"


def print_connections(targets, this_system, config):
    """Print one line per cluster: is its shared SSH connection open? (--connections-only).

    Parameters:
        targets (list of dict): from slurm_targets().
        this_system (str): this machine's name (a cluster is "this machine" when it matches).
        config (dict): the settings (for the hint).

    Returns:
        int: how many clusters are not connected.
    """
    missing_count = 0
    if not targets:
        print("no cluster in the settings (machines.clusters)")
    for target in targets:
        # A hop is reached through another cluster's connection, which has its own line.
        if target["login_node"]:
            print("%s: through %s (login node %s)" % (target["name"], target["through"], target["login_node"]))
            continue
        if target["name"] == this_system:
            print("%s: this machine" % target["name"])
            continue
        if connection_is_open(target["ssh_alias"]):
            print("%s: connected (ssh alias %s)" % (target["name"], target["ssh_alias"]))
        else:
            missing_count += 1
            print("%s: NOT CONNECTED - %s" % (target["name"], connection_hint(target["ssh_alias"], config)))
    return missing_count


# ----------------------------------------------------------------------------------------------
# Command line and main workflow
# ----------------------------------------------------------------------------------------------

def build_parser():
    """Build the command-line parser.

    Defaults that come from the settings are left as None or "auto" here and resolved in
    main(), so that `--help` works on a machine where the kit is not set up yet.

    Returns:
        argparse.ArgumentParser: the parser.
    """
    parser = argparse.ArgumentParser(
        description="Read-only snapshot of Slurm jobs, tmux sessions and busy processes "
                    "on the machines listed in the workhub-claude settings.")

    # The period: only its start matters, for the jobs that ended.
    parser.add_argument("--since",
                        help="start of the period, for ended Slurm jobs: ISO 8601 or 36h / 2d "
                             "(required unless --connections-only)")

    # Where to write, and whether existing outputs may be replaced.
    parser.add_argument("-o", "--output-dir",
                        help="folder for live_state.txt and live_state.json (required unless --connections-only)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files")

    # `dest="print_snapshot"` stores the flag under that name (`print` is a Python function).
    parser.add_argument("--print", dest="print_snapshot", action="store_true",
                        help="also print the snapshot on the screen")

    # Only the connection check (step 1 of the cleanup).
    parser.add_argument("--connections-only", action="store_true",
                        help="only say which clusters have an open shared SSH connection; write nothing")

    # Which machines.
    parser.add_argument("--clusters", default="auto",
                        help="comma-separated cluster names or SSH aliases, 'none', or 'auto' (default): "
                             "every cluster of the settings (plus machines.cluster_hops) from a workstation, "
                             "only this cluster when run on a cluster")
    parser.add_argument("--workstations", default="auto",
                        help="comma-separated workstation names, 'none', or 'auto' (default): every "
                             "workstation of the settings, none when run on a cluster")

    # `None` = take owner.time_zone from the settings.
    parser.add_argument("--timezone", default=None,
                        help="time zone for reading and printing times (default: owner.time_zone)")
    return parser


def main():
    """Collect the snapshot, write it, and print a short summary."""
    # ---- Stage 1: command line and settings -------------------------------------------------
    # argparse exits with status 2 on invalid arguments; `parser.error` does the same with a
    # message, for the checks argparse cannot express by itself.
    parser = build_parser()
    args = parser.parse_args()
    if not args.connections_only and (not args.since or not args.output_dir):
        parser.error("--since and --output-dir are required (unless --connections-only)")

    # The settings: without them the kit is not set up (status 2, with what to say).
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        print("error: %s" % error, file=sys.stderr)
        sys.exit(2)

    # This machine's name, and the machines known in the settings.
    this_system = workhub_config.system_name(config)
    known_clusters = slurm_targets(config)
    known_workstations = workstation_targets(config)

    # ---- Stage 2: the connection check only --------------------------------------------------
    if args.connections_only:
        missing_count = print_connections(known_clusters, this_system, config)
        print("%d cluster(s) not connected" % missing_count)
        return

    # ---- Stage 3: time zone and period -------------------------------------------------------
    # An unknown zone or an unreadable --since stops with status 1 and a clear message.
    if args.timezone is None:
        args.timezone = workhub_config.time_zone(config)
    try:
        timezone = ZoneInfo(args.timezone)
        since_moment = parse_since(args.since, timezone)
    except Exception as error:  # ZoneInfoNotFoundError or ValueError
        print("error: %s" % error, file=sys.stderr)
        sys.exit(1)

    # sacct start: SACCT_MARGIN_HOURS earlier than the period, written as "YYYY-MM-DDTHH:MM"
    # in UTC (the commands run with TZ=UTC).
    # DEBUG CHECK: "2026-03-02T11:41" for --since 2026-03-02T14:41+00:00.
    sacct_start_utc = since_moment.astimezone(datetime.timezone.utc) - datetime.timedelta(hours=SACCT_MARGIN_HOURS)
    sacct_start_utc_text = sacct_start_utc.strftime("%Y-%m-%dT%H:%M")

    # Which machines: from a workstation, every cluster (and hop) and every workstation of the
    # settings; on a cluster (its name is this machine's name), only that cluster.
    cluster_names = []
    for target in known_clusters:
        cluster_names.append(target["name"])
    workstation_names = []
    for target in known_workstations:
        workstation_names.append(target["name"])
    on_cluster = this_system in cluster_names
    if on_cluster:
        auto_cluster_names = [this_system]
        auto_workstation_names = []
    else:
        auto_cluster_names = cluster_names
        auto_workstation_names = workstation_names
    clusters = select_targets(args.clusters, known_clusters, auto_cluster_names, "cluster")
    workstations = select_targets(args.workstations, known_workstations, auto_workstation_names, "workstation")

    # Existing outputs are never replaced silently; checked before contacting any machine.
    text_path = os.path.join(args.output_dir, "live_state.txt")
    json_path = os.path.join(args.output_dir, "live_state.json")
    for path in (text_path, json_path):
        if os.path.exists(path) and not args.overwrite:
            print("error: %s exists; use --overwrite or another --output-dir" % path, file=sys.stderr)
            sys.exit(1)

    # ---- Stage 4: query the machines, one after the other ------------------------------------
    cluster_results = []
    for target in clusters:
        cluster_results.append(query_cluster(target, sacct_start_utc_text, this_system, timezone, config))
    workstation_results = []
    for target in workstations:
        workstation_results.append(query_workstation(target, this_system))

    # ---- Stage 5: write the snapshot ---------------------------------------------------------
    report = {"collected": datetime.datetime.now(timezone).timestamp(), "system": this_system,
              "timezone": args.timezone, "sacct_start_utc": sacct_start_utc_text,
              "clusters": cluster_results, "workstations": workstation_results}
    snapshot_text = format_snapshot(report, timezone)

    # `os.makedirs(..., exist_ok=True)` creates the folder if needed; mode "w" creates or
    # replaces each file (replacement was allowed above).
    os.makedirs(args.output_dir, exist_ok=True)
    with open(text_path, "w", encoding="utf-8") as text_file:
        text_file.write(snapshot_text)
    with open(json_path, "w", encoding="utf-8") as json_file:
        json.dump(report, json_file, ensure_ascii=False, indent=1)

    # ---- Stage 6: short summary on the screen (the whole snapshot with --print) --------------
    if args.print_snapshot:
        print(snapshot_text)
    reached = []
    missed = []
    for item in cluster_results + workstation_results:
        if item["reached"]:
            reached.append(item["system"])
        else:
            missed.append(item["system"])
    # `", ".join(list) or "none"` prints "none" for an empty list (an empty text is false).
    print("reached: %s" % (", ".join(reached) or "none"))
    print("not reached: %s" % (", ".join(missed) or "none"))
    print("snapshot: %s" % text_path)


# `__name__ == "__main__"` is true only when the file runs as a program, so importing it in the
# tests does not run main().
if __name__ == "__main__":
    main()
