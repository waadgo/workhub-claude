#!/usr/bin/env python3
"""
recap_common.py - shared helpers of the `weekly-recap` skill: the settings it reads, the week
window, Task IDs, running programs, reaching clusters through already-open SSH connections, and
writing private output files.

PURPOSE
    The weekly recap covers one Monday-to-Sunday week in the owner's time zone. Every collector
    of the skill (`slurm_week.py`, `week_sources.py`, `claude_tokens_week.py`, `week_timeline.py`)
    must agree on exactly the same window, must find the same clusters, folders and Task IDs in
    the settings file, and must reach the clusters the same safe way. Those shared pieces live
    here, so a change is made once.

    Everything that differs between users (time zone, clusters, SSH aliases, Slurm accounts,
    workstations, transcript folders, orchestrator folder, output folder, Task ID prefix) comes
    from the workhub-claude settings file, read with `workhub_config.py` (same folder):
        ~/.config/workhub-claude/config.json   (or the file named by $WORKHUB_CLAUDE_CONFIG)

WHAT IT PROVIDES
    load_settings()                     the settings dict, or exit 2 with the set-up hint
    owner_timezone(config)              ZoneInfo of owner.time_zone (UTC when unset)
    week_window(text, timezone, now)    (start, end, label) of the week named by `text`
    readable_day(day), week_id(start)   "Mon Nov 2", "2026-W45"
    task_id_prefix(config)              "TK" (notion.task_id_prefix)
    task_id_pattern(prefix)             compiled regular expression for "TK-42", "tk42", "TK 42"
    task_ids_in(text, pattern, prefix)  the Task IDs of a text, in order, without repeats
    slurm_targets(config)               the Slurm clusters to read (machines.clusters + hops)
    transcript_hosts(config, system)    the clusters whose transcripts are NOT on this disk
    this_machine_label(config, system)  "ws01", or "ws01+ws02" when workstations share the home
    orchestrator_dir(config)            workflow.orchestrator.folder, "" when disabled
    output_dir_for(config, week, sub)   <paths.outputs_dir>/weekly-recap/<week id>/<sub>
    run_command(command, timeout)       run a program, never raise, return (status, out, err)
    connection_is_open(alias)           is the shared SSH connection (ControlMaster) alive?
    remote_command(target, shell, sys)  the argument list that runs `shell` on a cluster, or None
    shell_path(path)                    a path written so that a remote shell expands "~" itself
    prepare_output_dir(path)            create a private (mode 700) output folder
    write_private_text(path, text, overwrite)   write a file with mode 600

COMMAND LINE (used by SKILL.md)
    python3 recap_common.py --week last            the week, "now", the folders and the hosts
    python3 recap_common.py --week 2026-W45 --shell
                                                   the same as shell assignments, for
                                                   eval "$(python3 recap_common.py --week last --shell)"
    python3 recap_common.py --connections          which clusters have an open shared connection

DEPENDENCIES
    Python >= 3.9 (for `zoneinfo`), standard library only; `ssh` for the cluster helpers.

ASSUMPTIONS
    - A week starts on Monday 00:00 and ends at the next Monday 00:00 (end excluded), in the
      owner's time zone. Daylight-saving changes inside the week are handled by `zoneinfo` (that
      week is then 167 or 169 hours long).
    - Clusters are only reached through shared SSH connections that are ALREADY open
      (`ssh -O check <alias>` succeeds; opened by `cluster-login` or by hand). A new login (which
      could ask for a password or a second factor) is never attempted: every ssh call uses
      `-o BatchMode=yes`.

EXIT STATUS (command line)
    0 = success; 1 = --week cannot be read; 2 = no settings file (run the work-hub-setup skill).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (all from Python's standard library)
# ----------------------------------------------------------------------------------------------

# `argparse` reads the command line of the small CLI at the end and writes its --help text.
import argparse

# `datetime.datetime` is one moment (date + clock time + UTC offset); `datetime.date` is a day;
# `datetime.timedelta` is a duration such as "7 days".
import datetime

# `os` gives `os.makedirs`, `os.chmod`, `os.open` and path helpers.
import os

# `re` is Python's regular-expression module (week names, Task IDs).
import re

# `shlex.quote(text)` wraps a text in quotes that a remote shell reads back unchanged.
import shlex

# `subprocess.run` starts another program (ssh, bash) and waits for it to finish.
import subprocess

# `sys` gives `sys.exit`, `sys.stderr` and `sys.path`.
import sys

# `ZoneInfo("Europe/Lisbon")` is a time-zone object with that zone's daylight-saving rules.
from zoneinfo import ZoneInfo

# The settings reader of workhub-claude (an identical copy of the kit's lib/workhub_config.py,
# kept in this same folder). `sys.path.insert(0, folder)` makes Python look in this script's own
# folder first, so the import works from any working directory. It provides load_config(),
# get_setting(config, "a.b.c", default), skill_setting(config, skill, key, default),
# time_zone(config), system_name(config), workstations(config), clusters(config), expand_path().
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workhub_config  # noqa: E402  (the import must come after the sys.path change above)


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# This skill's name: the key of its options under skill_settings, and the folder of its outputs.
SKILL_NAME = "weekly-recap"

# Seconds before one remote command is given up (sacct over a week can take ~20 s).
COMMAND_TIMEOUT_SECONDS = 120

# Where outputs go when paths.outputs_dir is not set (the documented default of the kit).
DEFAULT_OUTPUTS_DIR = "~/workhub/outputs"

# Task ID prefix when notion.task_id_prefix is not set.
DEFAULT_TASK_ID_PREFIX = "TK"


# ----------------------------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------------------------

def load_settings():
    """Read the workhub-claude settings file, or stop with the set-up hint.

    Returns:
        dict: the settings (see config/config.example.json in the repository).

    Exits:
        with status 2 and the hint "say 'set up my work hub'" when the file is missing or broken.
    """
    try:
        return workhub_config.load_config(required=True)
    except workhub_config.ConfigError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)


def owner_timezone(config):
    """Return the owner's time zone as a ZoneInfo object (owner.time_zone; UTC when unset).

    Parameters:
        config (dict): the settings.

    Returns:
        ZoneInfo: e.g. ZoneInfo("Europe/Lisbon").

    Raises:
        zoneinfo.ZoneInfoNotFoundError: when the name in the settings is not an IANA zone.
    """
    return ZoneInfo(workhub_config.time_zone(config))


def recap_setting(config, key, default=None):
    """Return one option of this skill: skill_settings.weekly-recap.<key>, else `default`."""
    return workhub_config.skill_setting(config, SKILL_NAME, key, default)


# ----------------------------------------------------------------------------------------------
# The week window
# ----------------------------------------------------------------------------------------------

def monday_of(day):
    """Return the Monday of the week that contains `day`.

    Parameters:
        day (datetime.date): any day, e.g. date(2026, 11, 5) (a Thursday).

    Returns:
        datetime.date: that week's Monday, e.g. date(2026, 11, 2).
    """
    # `weekday()` numbers the days Monday = 0 ... Sunday = 6, so subtracting that many days
    # always lands on the Monday. Example: Thursday Nov 5 has weekday 3 -> Nov 5 - 3 days = Nov 2.
    days_since_monday = day.weekday()
    return day - datetime.timedelta(days=days_since_monday)


def week_window(text, timezone, now=None):
    """Turn a week description into its start and end moments.

    Parameters:
        text (str): which week:
            "last"        the full week before the current one (the default of every script);
            "this"        the current week (its end is in the future: a partial recap);
            "2026-W45"    an ISO week number (ISO weeks start on Monday);
            "2026-11-04"  the week that contains this day.
        timezone (ZoneInfo): the zone in which "Monday 00:00" is meant (the owner's zone).
        now (datetime.datetime or None): "now", for tests; None = the real current time.

    Returns:
        tuple (start, end, label):
            start (datetime.datetime): Monday 00:00 of that week, aware of its UTC offset;
            end (datetime.datetime): the next Monday 00:00 (excluded from the week);
            label (str): "2026-W45 (Mon Nov 2 - Sun Nov 8)".

    Raises:
        ValueError: when `text` is none of the forms above.

    Example:
        >>> start, end, label = week_window("2026-W45", ZoneInfo("America/New_York"))
        >>> start.isoformat(), end.isoformat()
        ('2026-11-02T00:00:00-05:00', '2026-11-09T00:00:00-05:00')
    """
    # "now" in the owner's zone; tests pass a fixed moment so the result does not depend on the day.
    if now is None:
        now = datetime.datetime.now(timezone)
    today = now.astimezone(timezone).date()

    # Decide the Monday of the requested week, one form at a time.
    cleaned = text.strip().lower()
    if cleaned == "last":
        # This week's Monday minus 7 days = last week's Monday.
        monday = monday_of(today) - datetime.timedelta(days=7)
    elif cleaned == "this":
        monday = monday_of(today)
    elif re.fullmatch(r"\d{4}-w\d{1,2}", cleaned):
        # Regular expression, character by character: `\d{4}` four digits (the year), `-w` the
        # literal text "-w" (already lower-cased), `\d{1,2}` one or two digits (the week number).
        # `fullmatch` requires the whole text to match, not only a part of it.
        year_text, week_text = cleaned.split("-w")
        # `date.fromisocalendar(year, week, 1)` is the Monday (ISO day 1) of that ISO week; it
        # raises ValueError for a week number that does not exist (e.g. week 54).
        monday = datetime.date.fromisocalendar(int(year_text), int(week_text), 1)
    else:
        # Last possible form: a day "YYYY-MM-DD"; `fromisoformat` raises ValueError otherwise.
        try:
            day = datetime.date.fromisoformat(cleaned)
        except ValueError:
            raise ValueError("week must be 'last', 'this', 'YYYY-Www' or 'YYYY-MM-DD', not %r" % text)
        monday = monday_of(day)

    # Monday 00:00 and the next Monday 00:00 as aware moments in the owner's zone. Building each
    # moment from its calendar day (instead of adding 168 hours) keeps both at local midnight
    # even in the week when daylight-saving time starts or ends.
    next_monday = monday + datetime.timedelta(days=7)
    start = datetime.datetime(monday.year, monday.month, monday.day, tzinfo=timezone)
    end = datetime.datetime(next_monday.year, next_monday.month, next_monday.day, tzinfo=timezone)

    # Label used in titles and file names: ISO year and week number, then the readable days.
    # `isocalendar()` returns (ISO year, ISO week, ISO weekday).
    iso_year, iso_week, _ = monday.isocalendar()
    sunday = monday + datetime.timedelta(days=6)
    label = "%d-W%02d (%s - %s)" % (iso_year, iso_week, readable_day(monday), readable_day(sunday))

    # DEBUG CHECK: start is a Monday at 00:00 and (end - start) is 7 days (167-169 h near DST).
    assert start.weekday() == 0 and start.hour == 0 and start.minute == 0
    return start, end, label


def readable_day(day):
    """Return "Mon Nov 2" for a day (no leading zero, English names, independent of the locale).

    Parameters:
        day (datetime.date or datetime.datetime): the day.

    Returns:
        str: e.g. "Sun Nov 8".
    """
    # Fixed English names, so the text does not change with the machine's language settings
    # (`strftime("%a")` would follow the locale).
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return "%s %s %d" % (day_names[day.weekday()], month_names[day.month - 1], day.day)


def week_id(start):
    """Return the short week identifier "2026-W45" used in folder names.

    Parameters:
        start (datetime.datetime): the week's Monday 00:00.

    Returns:
        str: ISO year, "-W", two-digit ISO week.
    """
    iso_year, iso_week, _ = start.date().isocalendar()
    return "%d-W%02d" % (iso_year, iso_week)


# ----------------------------------------------------------------------------------------------
# Task IDs (built from the prefix in the settings, never hard-coded)
# ----------------------------------------------------------------------------------------------

def task_id_prefix(config):
    """Return the Task ID prefix of the Work Hub (notion.task_id_prefix, default "TK")."""
    return str(workhub_config.get_setting(config, "notion.task_id_prefix", default=DEFAULT_TASK_ID_PREFIX))


def task_id_pattern(prefix):
    """Build the regular expression that finds Task IDs written by people, jobs or conversations.

    Pattern, part by part (for the prefix "TK"):
        `(?<![A-Za-z0-9])`  no letter or digit just before (so "atk-12" does not match);
        `TK`                the prefix, `re.escape`d so that any character in it is literal, and
                            matched in any case (re.IGNORECASE: "tk", "Tk", "TK");
        `[-_ ]?`            an optional dash, underscore or space ("TK-42", "tk_42", "TK 42", "tk42");
        `(\\d{1,4})`         one to four digits, captured (the task number);
        `(?!\\d)`            no fifth digit after.
    Lookarounds are used instead of `\\b` because `\\b` counts "_" as a letter, so a job name
    such as "tk118_stage_b" would not match.

    Parameters:
        prefix (str): e.g. "TK".

    Returns:
        re.Pattern: group 1 is the task number.
    """
    escaped_prefix = re.escape(prefix)
    pattern_text = r"(?<![A-Za-z0-9])" + escaped_prefix + r"[-_ ]?(\d{1,4})(?!\d)"
    return re.compile(pattern_text, re.IGNORECASE)


def task_ids_in(text, pattern, prefix):
    """Return the Task IDs in a text, in order of appearance, without repeats ("TK-126").

    Parameters:
        text (str): any text (a job name, a log entry, a conversation).
        pattern (re.Pattern): from task_id_pattern(prefix).
        prefix (str): the prefix written in the result, e.g. "TK".

    Returns:
        list of str: e.g. ["TK-42", "TK-7"]; leading zeros are dropped ("tk042" -> "TK-42").
    """
    found = []
    for match in pattern.finditer(text or ""):
        task_id = "%s-%d" % (prefix, int(match.group(1)))
        if task_id not in found:
            found.append(task_id)
    return found


def task_number(task_id):
    """Return the number of a Task ID ("TK-110" -> 110), for sorting rows in numeric order."""
    # `rsplit("-", 1)` splits at the LAST dash, so a prefix with a dash in it still works.
    number_text = task_id.rsplit("-", 1)[-1]
    return int(number_text) if number_text.isdigit() else 0


# ----------------------------------------------------------------------------------------------
# Machines and folders from the settings
# ----------------------------------------------------------------------------------------------

def slurm_targets(config):
    """List the Slurm clusters the recap reads, from the settings.

    Sources:
        machines.clusters[]   every cluster whose `scheduler` is "slurm" (or not set). Its
                              `ssh_alias` (default: its name) is the Host of ~/.ssh/config used
                              for the shared connection; `slurm_accounts` lists its accounts.
        machines.cluster_hops[]   optional extra Slurm clusters that are only reached through
                              another cluster's login node, for example a GPU side with its own
                              Slurm database (shared with work-hub-cleanup):
                              {"name": "mycluster-gpu", "through": "mycluster",
                               "login_node": "gpu-login1", "slurm_accounts": [...]}.
                              `through` is the name of a cluster of machines.clusters; `login_node`
                              is the login node reached from there with a second ssh. Inside this
                              module they are kept as "via" and "hop_host".

    Parameters:
        config (dict): the settings.

    Returns:
        list of dict: {"name", "ssh_alias", "accounts", "via", "via_alias", "hop_host"};
            "via" and "hop_host" are "" for a directly reached cluster.
    """
    targets = []
    alias_by_name = {}
    for cluster in workhub_config.clusters(config):
        name = str(cluster.get("name") or "").strip()
        if not name:
            continue
        alias = str(cluster.get("ssh_alias") or name).strip()
        alias_by_name[name] = alias
        # A cluster with another scheduler (or "none") has no sacct; it is left out here.
        scheduler = str(cluster.get("scheduler") or "slurm").strip().lower()
        if scheduler != "slurm":
            continue
        accounts = [str(account) for account in (cluster.get("slurm_accounts") or [])]
        targets.append({"name": name, "ssh_alias": alias, "accounts": accounts,
                        "via": "", "via_alias": "", "hop_host": ""})

    # Optional two-hop clusters. An entry whose `via` is not a listed cluster is skipped, because
    # there would be no shared connection to start from.
    hops = workhub_config.get_setting(config, "machines.cluster_hops", default=[])
    if isinstance(hops, list):
        for hop in hops:
            if not isinstance(hop, dict):
                continue
            name = str(hop.get("name") or "").strip()
            via = str(hop.get("through") or "").strip()
            hop_host = str(hop.get("login_node") or "").strip()
            if not name or not hop_host or via not in alias_by_name:
                continue
            accounts = [str(account) for account in (hop.get("slurm_accounts") or [])]
            targets.append({"name": name, "ssh_alias": "", "accounts": accounts,
                            "via": via, "via_alias": alias_by_name[via], "hop_host": hop_host})
    return targets


def transcript_hosts(config, this_system):
    """List the clusters whose Claude Code / Codex transcripts must be read through SSH.

    A cluster that shares its home folder with this machine (`shares_home_with_this_machine`)
    keeps its transcripts on this very disk, so reading it again would count everything twice;
    this machine itself is left out too.

    Parameters:
        config (dict): the settings.
        this_system (str): this machine's system name.

    Returns:
        list of dict: {"name", "ssh_alias"} in the order of the settings.
    """
    hosts = []
    for cluster in workhub_config.clusters(config):
        name = str(cluster.get("name") or "").strip()
        if not name or name == this_system:
            continue
        if cluster.get("shares_home_with_this_machine"):
            continue
        hosts.append({"name": name, "ssh_alias": str(cluster.get("ssh_alias") or name).strip()})
    return hosts


def hosts_from_option(option_text, config, this_system):
    """Turn a --hosts option into the list of hosts to read.

    Parameters:
        option_text (str): "" or "auto" = transcript_hosts() of the settings; "none" = no host;
            otherwise comma-separated cluster names or SSH aliases ("mycluster,other").
        config (dict): the settings (to look up the SSH alias of a cluster name).
        this_system (str): this machine's system name.

    Returns:
        list of dict: {"name", "ssh_alias"}.
    """
    cleaned = (option_text or "").strip()
    if cleaned in ("", "auto"):
        return transcript_hosts(config, this_system)
    if cleaned == "none":
        return []
    alias_by_name = {}
    for cluster in workhub_config.clusters(config):
        name = str(cluster.get("name") or "").strip()
        if name:
            alias_by_name[name] = str(cluster.get("ssh_alias") or name).strip()
    hosts = []
    for piece in cleaned.split(","):
        name = piece.strip()
        if name and name != this_system:
            # A name that is not in the settings is used as an SSH alias as it is.
            hosts.append({"name": name, "ssh_alias": alias_by_name.get(name, name)})
    return hosts


def this_machine_label(config, this_system):
    """Return the label of the transcripts on this disk: "ws01", or "ws01+ws02" for a shared home.

    Workstations listed with `shares_home_with_this_machine: true` see the same home folder, so
    one scan of ~/.claude covers all of them together; the label names them all.

    Parameters:
        config (dict): the settings.
        this_system (str): this machine's system name.

    Returns:
        str: e.g. "ws01+ws02".
    """
    names = [this_system]
    for machine in workhub_config.workstations(config):
        name = str(machine.get("name") or "").strip().lower()
        if name and machine.get("shares_home_with_this_machine") and name not in names:
            names.append(name)
    return "+".join(names)


def orchestrator_dir(config):
    """Return the orchestrator's folder (workflow.orchestrator.folder), or "" when it is disabled.

    The folder holds log/YYYY-MM.md (append-only event log), DECISIONS.md, QUESTIONS.md and NOW.md.

    Parameters:
        config (dict): the settings.

    Returns:
        str: the expanded absolute folder, or "" when workflow.orchestrator.enabled is false or
            the folder is not set.
    """
    enabled = workhub_config.get_setting(config, "workflow.orchestrator.enabled", default=True)
    folder = workhub_config.get_setting(config, "workflow.orchestrator.folder", default="")
    if not enabled or not folder:
        return ""
    return os.path.abspath(workhub_config.expand_path(folder))


def orchestrator_log_files(folder, start):
    """Return the orchestrator's monthly log files that can hold days of the week (1 or 2 months).

    Parameters:
        folder (str): the orchestrator folder ("" = none).
        start (datetime.datetime): Monday 00:00 of the week.

    Returns:
        list of tuple (path, year): only files that exist, in month order.
    """
    if not folder:
        return []
    # The seven days of the week, then the distinct (year, month) pairs they fall in.
    days = [start.date() + datetime.timedelta(days=offset) for offset in range(7)]
    months = sorted({(day.year, day.month) for day in days})
    paths = []
    for year, month in months:
        path = os.path.join(folder, "log", "%04d-%02d.md" % (year, month))
        if os.path.isfile(path):
            paths.append((path, year))
    return paths


def outputs_root(config):
    """Return <paths.outputs_dir>/weekly-recap (expanded), the parent of every week's folder."""
    base = workhub_config.get_setting(config, "paths.outputs_dir", default=DEFAULT_OUTPUTS_DIR)
    return os.path.join(os.path.abspath(workhub_config.expand_path(base)), SKILL_NAME)


def output_dir_for(config, week, subfolder=""):
    """Return the folder of one week's outputs, or of one of its parts.

    Parameters:
        config (dict): the settings.
        week (str): the week id, e.g. "2026-W45".
        subfolder (str): "slurm", "tokens", "sources", "figure" ... or "" for the week folder.

    Returns:
        str: e.g. "/home/alex/workhub/outputs/weekly-recap/2026-W45/slurm".
    """
    folder = os.path.join(outputs_root(config), week)
    if subfolder:
        folder = os.path.join(folder, subfolder)
    return folder


def transcript_dirs(config):
    """Return the transcript folders to search, as written in the settings (not expanded).

    Returns:
        list of str: conversations.claude_projects_dir (default "~/.claude/projects"), plus
            conversations.codex_sessions_dir when conversations.include_codex is true.
            They stay unexpanded ("~/...") so that a remote shell expands "~" to ITS home.
    """
    folders = [str(workhub_config.get_setting(config, "conversations.claude_projects_dir",
                                              default="~/.claude/projects"))]
    if workhub_config.get_setting(config, "conversations.include_codex", default=False):
        folders.append(str(workhub_config.get_setting(config, "conversations.codex_sessions_dir",
                                                      default="~/.codex/sessions")))
    return folders


def shell_path(path):
    """Write a path for a shell command line so that "~" is expanded by the shell that runs it.

    `shlex.quote("~/.claude/projects")` would give '~/.claude/projects', in which the remote
    shell no longer expands "~". So a leading "~/" becomes "$HOME"/ (expanded by that shell),
    and the rest is quoted.

    Parameters:
        path (str): e.g. "~/.claude/projects" or "/srv/claude/projects".

    Returns:
        str: e.g. "$HOME"/'.claude/projects' (shell text) or '/srv/claude/projects'.
    """
    if path == "~":
        return "\"$HOME\""
    if path.startswith("~/"):
        return "\"$HOME\"/" + shlex.quote(path[2:])
    return shlex.quote(path)


# ----------------------------------------------------------------------------------------------
# Running programs and reaching clusters
# ----------------------------------------------------------------------------------------------

def run_command(command, timeout=COMMAND_TIMEOUT_SECONDS):
    """Run a program, capture its output, and never raise.

    Parameters:
        command (list of str): the program and its arguments, e.g. ["ssh", "-O", "check", "mycluster"].
        timeout (int): seconds before giving up.

    Returns:
        tuple (status, stdout, stderr): status is the program's exit status (0 = success), or -1
            when it could not be started or timed out (the reason is then in stderr).
    """
    # `capture_output=True` collects what the program prints; `text=True` decodes bytes to text;
    # `timeout` raises TimeoutExpired after that many seconds.
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timed out after %d s" % timeout
    except OSError as error:
        # The program does not exist or cannot be started (e.g. no `ssh` installed).
        return -1, "", str(error)
    return completed.returncode, completed.stdout, completed.stderr


def connection_is_open(ssh_alias):
    """Tell whether the shared SSH connection to `ssh_alias` is alive.

    `ssh -O check <alias>` only asks the local ControlMaster process (the shared connection that
    `cluster-login` or `ssh -fN` opened); it never starts a login, so it can never cause a
    password or second-factor prompt.

    Parameters:
        ssh_alias (str): host alias from ~/.ssh/config, e.g. "mycluster".

    Returns:
        bool: True when the connection is open.
    """
    if not ssh_alias:
        return False
    status, _, _ = run_command(["ssh", "-O", "check", ssh_alias], timeout=15)
    return status == 0


def how_to_open(ssh_alias):
    """Return the one-line fix for a closed connection, shown in notes and Coverage."""
    return "no open shared SSH connection to %s; open it (for example `cluster-login %s`) and run again" % (
        ssh_alias, ssh_alias)


def remote_command(target, shell_command, this_system):
    """Build the argument list that runs `shell_command` on a cluster, or explain why it cannot.

    Parameters:
        target (dict): one entry of slurm_targets() (or {"name", "ssh_alias"} of transcript_hosts()).
        shell_command (str): one bash command line to run there.
        this_system (str): this machine's system name. When the script runs on that very
            cluster, the command runs directly, without SSH.

    Returns:
        tuple (command, note):
            command (list of str or None): the arguments for run_command, None when unreachable;
            note (str): why it is unreachable, with the fix ("" when reachable).
    """
    name = target.get("name", "")
    hop_host = target.get("hop_host", "")
    via = target.get("via", "")
    # `BatchMode=yes` forbids any password or second-factor prompt; `ConnectTimeout=10` gives up
    # after 10 s. Both are used on every ssh call.
    ssh_options = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

    # Route 1: this machine itself. `bash -c "<line>"` runs one command line in a new shell.
    if not hop_host and name == this_system:
        return ["bash", "-c", shell_command], ""

    # Route 2: a cluster reached through another cluster's login node (machines.cluster_hops).
    if hop_host:
        # `shlex.quote` keeps the inner command intact through the first shell, so it is
        # expanded only on the hop host (where "$USER" must be read).
        hop_line = "ssh -o BatchMode=yes -o ConnectTimeout=10 %s %s" % (hop_host, shlex.quote(shell_command))
        if via == this_system:
            # Already on the first cluster: one ssh to its other login node is enough.
            return ["bash", "-c", hop_line], ""
        if not connection_is_open(target.get("via_alias", "")):
            return None, how_to_open(target.get("via_alias", "") or via)
        return ["ssh"] + ssh_options + [target["via_alias"], hop_line], ""

    # Route 3: any other cluster, only through an already-open shared connection.
    alias = target.get("ssh_alias") or name
    if not connection_is_open(alias):
        return None, how_to_open(alias)
    return ["ssh"] + ssh_options + [alias, shell_command], ""


# ----------------------------------------------------------------------------------------------
# Private output files
# ----------------------------------------------------------------------------------------------

def prepare_output_dir(path):
    """Create the output folder (and its parents) and make it private (mode 700).

    The collected files hold user names, folder paths and job names, so nobody else on a shared
    machine should read them. Every folder this function CREATES gets mode 700 (for example the
    week folder <outputs>/weekly-recap/2026-W45 when its "slurm" subfolder is made first);
    folders that already existed above it keep their mode.

    Parameters:
        path (str): the folder.
    """
    # Walk up from the requested folder and remember the ones that do not exist yet.
    # (`os.makedirs(..., mode=0o700)` would apply the mode to the last folder only.)
    missing_folders = []
    current = os.path.abspath(path)
    while not os.path.isdir(current):
        missing_folders.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    # Create them from the outermost to the innermost; `reversed` walks the list backwards.
    for folder in reversed(missing_folders):
        os.mkdir(folder)
        # 0o700 = read, write and enter for the owner only (the `0o` prefix marks an octal
        # number); set after mkdir because the umask may have removed bits.
        os.chmod(folder, 0o700)
    # The requested folder itself is private even when it existed already.
    os.chmod(path, 0o700)


def write_private_text(path, text, overwrite):
    """Write `text` to `path` with mode 600 (owner read/write only).

    Parameters:
        path (str): the file to write.
        text (str): its content.
        overwrite (bool): allow replacing an existing file.

    Raises:
        FileExistsError: when the file exists and overwrite is False.
    """
    if os.path.exists(path) and not overwrite:
        raise FileExistsError("%s exists; pass --overwrite to replace it" % path)

    # `os.open` with mode 0o600 creates the file private from the first byte (a plain `open`
    # would create it with the default permissions and need a chmod afterwards).
    # O_WRONLY = write only, O_CREAT = create if missing, O_TRUNC = empty an existing file.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # `os.fdopen` wraps the low-level descriptor in a normal text file object; `with` closes it.
    with os.fdopen(descriptor, "w", encoding="utf-8") as output_file:
        output_file.write(text)
    # An existing file keeps its old mode through O_TRUNC, so set it explicitly.
    os.chmod(path, 0o600)


# ----------------------------------------------------------------------------------------------
# Command line: the week, the folders and the connections
# ----------------------------------------------------------------------------------------------

def week_facts(config, week_text, now=None):
    """Collect what SKILL.md needs before the collectors run.

    Parameters:
        config (dict): the settings.
        week_text (str): the --week value.
        now (datetime.datetime or None): for tests.

    Returns:
        dict (ordered): WEEK_ID, WEEK_LABEL, WEEK_START, WEEK_END, PREVIOUS_WEEK_ID, NOW, TIME_ZONE,
            SYSTEM, OUT, ORCH, HOSTS (comma-separated cluster names, or "none"), TASK_PREFIX.
    """
    timezone = owner_timezone(config)
    start, end, label = week_window(week_text, timezone, now=now)
    previous_start, _, _ = week_window((start - datetime.timedelta(days=7)).date().isoformat(), timezone)
    system = workhub_config.system_name(config)
    hosts = transcript_hosts(config, system)
    moment = now if now is not None else datetime.datetime.now(timezone)
    facts = {
        "WEEK_ID": week_id(start),
        "WEEK_LABEL": label,
        "WEEK_START": start.isoformat(),
        "WEEK_END": end.isoformat(),
        "PREVIOUS_WEEK_ID": week_id(previous_start),
        "NOW": moment.astimezone(timezone).strftime("%a %b %d %Y %H:%M %Z"),
        "TIME_ZONE": workhub_config.time_zone(config),
        "SYSTEM": system,
        "OUT": output_dir_for(config, week_id(start)),
        "ORCH": orchestrator_dir(config),
        "HOSTS": ",".join(host["name"] for host in hosts) or "none",
        "TASK_PREFIX": task_id_prefix(config),
    }
    return facts


def connection_report(config, this_system):
    """Return the lines of --connections: one per Slurm target and transcript host.

    Returns:
        tuple (lines, closed): readable lines, and the names whose connection is not open.
    """
    lines = []
    closed = []
    seen = set()
    entries = []
    for target in slurm_targets(config):
        entries.append(target)
    for host in transcript_hosts(config, this_system):
        entries.append({"name": host["name"], "ssh_alias": host["ssh_alias"], "via": "",
                        "via_alias": "", "hop_host": ""})
    for entry in entries:
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        if not entry.get("hop_host") and entry["name"] == this_system:
            lines.append("%-16s this machine (no SSH needed)" % entry["name"])
            continue
        alias = entry.get("via_alias") if entry.get("hop_host") else entry.get("ssh_alias")
        if entry.get("hop_host") and entry.get("via") == this_system:
            lines.append("%-16s through %s from this machine" % (entry["name"], entry["hop_host"]))
            continue
        if connection_is_open(alias):
            lines.append("%-16s open (%s)" % (entry["name"], alias))
        else:
            lines.append("%-16s NOT open: %s" % (entry["name"], how_to_open(alias)))
            closed.append(entry["name"])
    if not entries:
        lines.append("No clusters in the settings (machines.clusters): nothing to connect.")
    return lines, closed


def main():
    """Print the week's facts (or the connection states) for SKILL.md."""
    parser = argparse.ArgumentParser(
        description="Print the Monday-Sunday window of one week (owner's time zone), its output "
                    "folder and the hosts the weekly recap reads, or the state of the shared SSH connections.")
    parser.add_argument("--week", default="last", help="last (default), this, YYYY-Www or YYYY-MM-DD")
    parser.add_argument("--shell", action="store_true",
                        help="print shell assignments (KEY='value') for eval \"$(...)\"")
    parser.add_argument("--connections", action="store_true",
                        help="print which clusters have an open shared SSH connection")
    arguments = parser.parse_args()

    config = load_settings()
    if arguments.connections:
        lines, closed = connection_report(config, workhub_config.system_name(config))
        print("\n".join(lines))
        print("Not connected: " + (", ".join(closed) if closed else "none"))
        return 0

    try:
        facts = week_facts(config, arguments.week)
    except ValueError as error:
        print("recap_common.py: %s" % error, file=sys.stderr)
        return 1

    for key, value in facts.items():
        if arguments.shell:
            # `shlex.quote` makes each value one safe shell word, whatever characters it holds.
            print("%s=%s" % (key, shlex.quote(value)))
        else:
            print("%-17s %s" % (key, value))
    # DEBUG CHECK: WEEK_START is a Monday 00:00 and WEEK_END the next Monday 00:00 (168 h, or
    # 167 / 169 h in a week with a daylight-saving change).
    return 0


if __name__ == "__main__":
    sys.exit(main())
