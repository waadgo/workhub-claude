#!/usr/bin/env python3
"""
configure.py: the file-writing half of the work-hub-setup skill.

PURPOSE
    The work-hub-setup skill asks the user every setup question at the start, in one round. This script
    then does the local, file-level work with those answers, so that the skill itself only has to talk to
    the user and to Notion:

      detect             print what this machine already tells (time zone, CPUs, RAM, GPUs, whether the
                         home folder is shared over the network, which conversation folders exist), so the
                         questions can offer good defaults.
      write              merge the user's answers with the defaults, check them, and write the settings
                         file ~/.config/workhub-claude/config.json (mode 600).
      set                change one value of the settings file (setup uses it to store the Notion
                         addresses once the hub exists).
      validate           check an existing settings file.
      render-rules       write the work-harness rules (rules/work_harness.md of the kit, filled with the
                         user's settings) into ~/.claude/CLAUDE.md between two marker lines.
      remove-rules       take that block out again.
      init-orchestrator  copy the orchestrator files (NOW.md, QUESTIONS.md, DECISIONS.md, the start
                         prompt) into the orchestrator folder; never overwrites a file.
      kit-root           print where the kit (rules/, hooks/, templates/, tools/) was found.

    Every subcommand that changes a file only PRINTS what it would do, unless --apply is given. A file
    that is replaced is first copied to <file>.bak-YYYYMMDD-HHMMSS.

INPUTS
    --answers FILE   (write) a JSON file in the same shape as config/config.example.json of the kit; keys
                     left out keep their defaults.
    The settings file itself (write/set/validate/render-rules/init-orchestrator), found by
    workhub_config.py ($WORKHUB_CLAUDE_CONFIG or ~/.config/workhub-claude/config.json).
    The kit folder: found next to this skill (plugin or git clone), else ~/.local/share/workhub-claude/kit,
    else --kit-root.

OUTPUTS
    See PURPOSE. Messages go to standard output; problems go to standard error.

EXAMPLES
    python3 configure.py detect
    python3 configure.py write --answers /tmp/answers.json              # show the result, write nothing
    python3 configure.py write --answers /tmp/answers.json --apply
    python3 configure.py set notion.tasks_data_source "collection://..." --apply
    python3 configure.py render-rules --apply
    python3 configure.py init-orchestrator --apply

EXIT STATUS
    0 = done (or nothing to do); 1 = the answers or the settings have problems (listed); 2 = bad usage,
    missing file or missing kit folder.

DEPENDENCIES
    Python >= 3.8, standard library only (zoneinfo, used to check the time zone, needs Python >= 3.9; on
    3.8 that one check is skipped with a warning). workhub_config.py from the same folder.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ---------------------------------------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------------------------------------
# argparse: command line. copy: deep copies of the default settings. datetime: backup suffixes.
# getpass: the login name (only for the suggested socket folder). json: settings format. os, shutil:
# files and folders. re: small format checks (each pattern is explained where it is defined).
# subprocess: nvidia-smi and sysctl for the hardware facts. sys: exit codes.
import argparse
import copy
import datetime
import getpass
import json
import os
import re
import shutil
import subprocess
import sys

# workhub_config.py sits in the same folder (an identical copy of the kit's lib/workhub_config.py). It
# finds and reads the settings file; this script adds the writing side.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workhub_config  # noqa: E402  (the import has to come after the sys.path line above)

# ---------------------------------------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------------------------------------
# Where the manual installer (tools/install.sh) puts a copy of the whole kit, and where setup installs the
# hooks. Both stay the same across kit versions, so settings.json and CLAUDE.md can name them.
INSTALL_HOME = "~/.local/share/workhub-claude"
INSTALLED_KIT = INSTALL_HOME + "/kit"
HOOKS_DIR = INSTALL_HOME + "/hooks"

# The two lines that frame the rules block inside CLAUDE.md. render-rules replaces whatever is between
# them; remove-rules deletes them together with the block.
RULES_BEGIN = "<!-- workhub-claude rules: begin (written by the work-hub-setup skill; re-run setup to change) -->"
RULES_END = "<!-- workhub-claude rules: end -->"
RULES_BEGIN_PREFIX = "<!-- workhub-claude rules: begin"

# File systems that mean "this folder is shared over the network" (a home folder that several machines
# mount). SSH sockets must not live on them, and the hooks must keep machine-local state apart.
NETWORK_FILE_SYSTEMS = {"nfs", "nfs4", "cifs", "smbfs", "smb3", "lustre", "gpfs", "beegfs", "fuse.sshfs",
                        "afs", "ceph", "glusterfs", "panfs", "wekafs", "vast"}

# Format checks.
#   PREFIX_PATTERN:  a capital letter, then 0-5 capital letters or digits ("TK", "WH", "LAB2").
#   MACHINE_PATTERN: lower-case letters, digits and dashes, starting with a letter or digit ("ws01").
#   CLOCK_PATTERN:   two digits, a colon, two digits ("07:40").
#   NOTION_ID:       32 hexadecimal characters, the id at the end of every Notion page address.
PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,5}$")
MACHINE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CLOCK_PATTERN = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
NOTION_ID = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------------------------------------
# Defaults: the full shape of the settings file (config/config.example.json shows the same keys filled in)
# ---------------------------------------------------------------------------------------------------------
def default_config():
    """
    Return the settings every new user starts from, before their answers are merged in.

    The shape (keys and nesting) must stay identical to config/config.example.json; the kit test
    tests/test_kit.py checks that. Empty strings mean "not set yet": setup fills the Notion addresses
    after it has built or found the hub.
    """
    return {
        "schema_version": 1,
        "owner": {"name": "", "time_zone": "UTC"},
        "system_name": {"override_env": "CC_CLUSTER", "aliases": {}},
        "notion": {
            "hub_parent_page_url": "",
            "hub_page_url": "",
            "tasks_database_url": "",
            "tasks_data_source": "",
            "views": {"now": "", "focus": ""},
            "task_id_prefix": "TK",
            "projects": ["Writing and meetings", "Miscellaneous"],
            "research_log": {"enabled": False, "parent_page_url": "", "data_source": ""},
            "conversation_reports": {"enabled": False, "parent_page_url": "", "data_source": ""},
        },
        "workflow": {
            "conversation_names": True,
            "orchestrator": {"enabled": False, "folder": "~/workhub/orchestrator"},
            "morning_brief": {"enabled": False, "days": "Mon-Fri", "time": "08:00", "scheduler": "manual"},
            "now_limits": {"max_rows": 7, "max_in_progress": 3},
            "max_active_conversations": 4,
            "session_records_subdir": "docs/sessions",
        },
        "machines": {"workstations": [], "clusters": [], "cluster_hops": []},
        "cluster_login": {
            "enabled": False,
            "install_dir": "~/.local/bin",
            "socket_dir": "~/.ssh/sockets",
            "duo_hint": "",
        },
        "conversations": {
            "claude_projects_dir": "~/.claude/projects",
            "include_codex": False,
            "codex_sessions_dir": "~/.codex/sessions",
        },
        "paths": {"state_dir": "~/.local/state/workhub-claude", "outputs_dir": "~/workhub/outputs"},
        "runtime": {"python": "python3"},
        "skill_settings": {},
    }


def merge_settings(base, answers):
    """
    Return a copy of `base` with every value of `answers` written over it, level by level.

    Dicts are merged key by key (so an answer {"owner": {"name": "Alex"}} keeps owner.time_zone); any other
    value (text, number, list) replaces the default as a whole. Keys the defaults do not know are kept too,
    so a newer settings file is not damaged by an older script.
    """
    merged = copy.deepcopy(base)
    for key, value in answers.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_settings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def normalize_settings(config):
    """
    Tidy answers that have one obvious correct form, in place, and return the config.

    - The Task ID prefix is written in capitals ("tk" -> "TK").
    - The project list keeps its order, drops empty or repeated names, and always ends with "Miscellaneous"
      (the catch-all the skills file unclear rows under).
    - Machine names are written in lower case.
    """
    notion = config.setdefault("notion", {})
    notion["task_id_prefix"] = str(notion.get("task_id_prefix") or "TK").strip().upper()

    cleaned_projects = []
    for name in notion.get("projects") or []:
        text = str(name).strip()
        if text and text not in cleaned_projects and text != "Miscellaneous":
            cleaned_projects.append(text)
    cleaned_projects.append("Miscellaneous")
    notion["projects"] = cleaned_projects

    machines = config.setdefault("machines", {})
    for group in ("workstations", "clusters"):
        for machine in machines.get(group) or []:
            if isinstance(machine, dict) and machine.get("name"):
                machine["name"] = str(machine["name"]).strip().lower()
    return config


# ---------------------------------------------------------------------------------------------------------
# Checking the settings
# ---------------------------------------------------------------------------------------------------------
def check_time_zone(name, problems, warnings):
    """Add a problem when `name` is not a known IANA time zone (needs zoneinfo, Python >= 3.9)."""
    try:
        import zoneinfo
    except ImportError:
        warnings.append("owner.time_zone: not checked (Python 3.9 or newer is needed for that check)")
        return
    try:
        zoneinfo.ZoneInfo(name)
    except Exception:
        problems.append("owner.time_zone: '" + str(name) + "' is not a time zone name such as Europe/Paris")


def check_days(text):
    """
    Return True when `text` lists week days as "Mon-Fri" (a range) or "Mon,Wed,Fri" (a list).

    Example: "Mon-Fri" -> True; "weekdays" -> False.
    """
    parts = [part.strip() for part in str(text).split(",") if part.strip()]
    if not parts:
        return False
    for part in parts:
        ends = part.split("-")
        if len(ends) > 2:
            return False
        for day in ends:
            if day not in DAY_NAMES:
                return False
    return True


def check_machines(config, problems):
    """Add a problem for every machine entry that is incomplete or badly formed."""
    seen_names = set()
    for workstation in workhub_config.workstations(config):
        name = str(workstation.get("name", ""))
        if not MACHINE_PATTERN.match(name):
            problems.append("machines.workstations: name '" + name + "' should look like ws01 (a-z, 0-9, -)")
        if name in seen_names:
            problems.append("machines: the name '" + name + "' is used twice")
        seen_names.add(name)
        for window in workstation.get("usage_windows") or []:
            if not check_days(window.get("days", "")):
                problems.append(name + ".usage_windows: days '" + str(window.get("days")) + "' should look like Mon-Fri")
            for edge in ("from", "to"):
                if not CLOCK_PATTERN.match(str(window.get(edge, ""))):
                    problems.append(name + ".usage_windows: '" + edge + "' should be a time such as 09:00")
            # "blocked" is the only rule the skills know: time outside blocked windows is free.
            if window.get("rule", "blocked") != "blocked":
                problems.append(name + ".usage_windows: rule should be blocked (time outside blocked windows is free)")
    for cluster in workhub_config.clusters(config):
        name = str(cluster.get("name", ""))
        if not MACHINE_PATTERN.match(name):
            problems.append("machines.clusters: name '" + name + "' should look like mycluster (a-z, 0-9, -)")
        if name in seen_names:
            problems.append("machines: the name '" + name + "' is used twice")
        seen_names.add(name)
        for field in ("hostname", "user"):
            if not str(cluster.get(field, "")).strip():
                problems.append("machines.clusters." + name + ": '" + field + "' is empty")
        if not cluster.get("ssh_alias"):
            problems.append("machines.clusters." + name + ": 'ssh_alias' is empty (the name used in ssh <alias>)")
    # A hop is a Slurm cluster reached from another cluster's login node (for example a separate GPU side).
    cluster_names = set(str(cluster.get("name", "")) for cluster in workhub_config.clusters(config))
    for hop in workhub_config.get_setting(config, "machines.cluster_hops", default=[]) or []:
        name = str(hop.get("name", "")) if isinstance(hop, dict) else ""
        if not MACHINE_PATTERN.match(name):
            problems.append("machines.cluster_hops: name '" + name + "' should look like mycluster-gpu (a-z, 0-9, -)")
            continue
        if str(hop.get("through", "")) not in cluster_names:
            problems.append("machines.cluster_hops." + name + ": 'through' must be the name of one of the clusters")
        if not str(hop.get("login_node", "")).strip():
            problems.append("machines.cluster_hops." + name + ": 'login_node' is empty")


def validate_settings(config):
    """
    Check a complete settings dict and return (problems, warnings), two lists of plain-text lines.

    Problems stop `write --apply`; warnings are only shown. The checks cover what the skills rely on: a
    name and a valid time zone, where the hub lives (or is going to live), a usable Task ID prefix, a
    project list, the clock formats of the morning brief and usage windows, and complete machine entries.
    """
    problems = []
    warnings = []

    owner_name = str(workhub_config.get_setting(config, "owner.name", default="")).strip()
    if not owner_name:
        problems.append("owner.name: empty (the first name used in notes such as '(Alex, Nov 4)')")
    elif len(owner_name) > 40:
        problems.append("owner.name: longer than 40 characters")
    check_time_zone(workhub_config.time_zone(config), problems, warnings)

    parent_url = workhub_config.get_setting(config, "notion.hub_parent_page_url", default="")
    hub_url = workhub_config.get_setting(config, "notion.hub_page_url", default="")
    if not parent_url and not hub_url:
        problems.append("notion: give hub_parent_page_url (where a new hub is built) or hub_page_url (an existing hub)")
    for key, value in (("notion.hub_parent_page_url", parent_url), ("notion.hub_page_url", hub_url)):
        if value and not NOTION_ID.search(str(value)):
            problems.append(key + ": '" + str(value) + "' does not contain a Notion page id (copy the page link)")

    prefix = workhub_config.get_setting(config, "notion.task_id_prefix", default="")
    if not PREFIX_PATTERN.match(str(prefix)):
        problems.append("notion.task_id_prefix: '" + str(prefix) + "' should be 1-6 capital letters or digits, such as TK")
    projects = workhub_config.get_setting(config, "notion.projects", default=[])
    if len(projects) > 16:
        problems.append("notion.projects: more than 15 projects; keep the main ones and use Miscellaneous")
    for project in projects:
        if "," in project:
            problems.append("notion.projects: '" + project + "' contains a comma (Notion options cannot)")

    brief_enabled = workhub_config.get_setting(config, "workflow.morning_brief.enabled", default=False)
    if brief_enabled:
        if not CLOCK_PATTERN.match(str(workhub_config.get_setting(config, "workflow.morning_brief.time", default=""))):
            problems.append("workflow.morning_brief.time: should be a time such as 07:40")
        if not check_days(workhub_config.get_setting(config, "workflow.morning_brief.days", default="")):
            problems.append("workflow.morning_brief.days: should look like Mon-Fri")
        scheduler = workhub_config.get_setting(config, "workflow.morning_brief.scheduler", default="manual")
        if scheduler not in ("claude-app", "cron", "manual"):
            problems.append("workflow.morning_brief.scheduler: should be claude-app, cron or manual")
    for key in ("workflow.now_limits.max_rows", "workflow.now_limits.max_in_progress", "workflow.max_active_conversations"):
        value = workhub_config.get_setting(config, key, default=0)
        if not isinstance(value, int) or value < 1:
            problems.append(key + ": should be a whole number of at least 1")

    check_machines(config, problems)

    if workhub_config.get_setting(config, "cluster_login.enabled", default=False):
        if not workhub_config.clusters(config):
            warnings.append("cluster_login.enabled is true but no cluster is listed under machines.clusters")
        socket_dir = workhub_config.expand_path(workhub_config.get_setting(config, "cluster_login.socket_dir", default=""))
        if socket_dir and is_on_network_file_system(socket_dir):
            problems.append("cluster_login.socket_dir: " + socket_dir + " is on a network file system; SSH sockets "
                            "need a machine-local folder such as /tmp/ssh-sockets-<you>")

    python_command = workhub_config.get_setting(config, "runtime.python", default="python3")
    if not shutil.which(str(python_command)):
        warnings.append("runtime.python: '" + str(python_command) + "' is not found on this machine's PATH")
    return problems, warnings


# ---------------------------------------------------------------------------------------------------------
# Detecting facts about this machine
# ---------------------------------------------------------------------------------------------------------
def detect_time_zone():
    """
    Return this machine's IANA time zone name, or "UTC" when nothing tells.

    Order: $TZ (when it names a zone such as Europe/Paris), /etc/timezone (Debian, Ubuntu), then the
    target of the /etc/localtime link (".../zoneinfo/Europe/Paris" on most Linux systems and on macOS).
    """
    from_environment = os.environ.get("TZ", "").lstrip(":")
    if "/" in from_environment:
        return from_environment
    try:
        with open("/etc/timezone", mode="r", encoding="utf-8") as handle:
            text = handle.read().strip()
        if text:
            return text
    except OSError:
        pass
    link_target = os.path.realpath("/etc/localtime")
    marker = "zoneinfo/"
    if marker in link_target:
        return link_target.split(marker, 1)[1]
    return "UTC"


def detect_ram_gb():
    """Return the installed RAM in whole GB (Linux /proc/meminfo, macOS sysctl), or 0 when unknown."""
    try:
        with open("/proc/meminfo", mode="r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kilobytes = int(line.split()[1])
                    return round(kilobytes / 1024 / 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        output = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
        return round(int(output.stdout.strip()) / 1024 ** 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def detect_gpus():
    """
    Return the NVIDIA GPUs of this machine as [{"model", "memory_gb", "count"}], [] when there are none.

    `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` prints one line per GPU, such
    as "NVIDIA GeForce RTX 4090, 23028" (memory in MiB). Identical models are counted together.
    """
    if not shutil.which("nvidia-smi"):
        return []
    try:
        output = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in output.stdout.splitlines():
        if "," not in line:
            continue
        model, memory_mib = [part.strip() for part in line.rsplit(",", 1)]
        model = model.replace("NVIDIA GeForce ", "").replace("NVIDIA ", "")
        try:
            memory_gb = round(int(memory_mib) / 1024)
        except ValueError:
            memory_gb = 0
        same = [gpu for gpu in gpus if gpu["model"] == model and gpu["memory_gb"] == memory_gb]
        if same:
            same[0]["count"] += 1
        else:
            gpus.append({"model": model, "memory_gb": memory_gb, "count": 1})
    return gpus


def file_system_type(path):
    """
    Return the file-system type of the mount that holds `path` ("nfs4", "ext4", ...), or "" when unknown.

    Reads /proc/mounts (Linux only): the mount point that is the longest leading part of the real path
    wins. "autofs" entries are skipped, because they only stand for the real mount below them.
    """
    real_path = os.path.realpath(workhub_config.expand_path(path))
    best_mount = ""
    best_type = ""
    try:
        with open("/proc/mounts", mode="r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return ""
    for line in lines:
        fields = line.split()
        if len(fields) < 3 or fields[2] == "autofs":
            continue
        mount_point = fields[1]
        inside = real_path == mount_point or real_path.startswith(mount_point.rstrip("/") + "/")
        if inside and len(mount_point) > len(best_mount):
            best_mount = mount_point
            best_type = fields[2]
    return best_type


def is_on_network_file_system(path):
    """Return True when `path` (or the nearest folder above it that exists) is on a network file system."""
    probe = workhub_config.expand_path(path)
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return file_system_type(probe) in NETWORK_FILE_SYSTEMS


def detect_facts():
    """
    Return a dict of facts that let the setup questions offer good defaults. Nothing is written.

    Keys: host_name, time_zone, cpus, ram_gb, gpus, home_is_shared (home folder on a network file system:
    several machines see the same files), suggested_socket_dir (machine-local when the home is shared),
    python_version, the paths that exist (Claude conversations, Codex conversations, CLAUDE.md, SSH config,
    an earlier settings file), and kit_root.
    """
    home_is_shared = is_on_network_file_system("~")
    if home_is_shared:
        suggested_socket_dir = "/tmp/ssh-sockets-" + getpass.getuser()
    else:
        suggested_socket_dir = "~/.ssh/sockets"
    kit_root = find_kit_root(None, required=False)
    facts = {
        "host_name": workhub_config.system_name({}),
        "time_zone": detect_time_zone(),
        "cpus": os.cpu_count() or 0,
        "ram_gb": detect_ram_gb(),
        "gpus": detect_gpus(),
        "home_is_shared": home_is_shared,
        "home_file_system": file_system_type("~"),
        "suggested_socket_dir": suggested_socket_dir,
        "python_version": sys.version.split()[0],
        "claude_projects_dir_exists": os.path.isdir(workhub_config.expand_path("~/.claude/projects")),
        "codex_sessions_dir_exists": os.path.isdir(workhub_config.expand_path("~/.codex/sessions")),
        "claude_md_exists": os.path.isfile(workhub_config.expand_path("~/.claude/CLAUDE.md")),
        "ssh_config_exists": os.path.isfile(workhub_config.expand_path("~/.ssh/config")),
        "settings_file": workhub_config.config_path(),
        "settings_file_exists": os.path.isfile(workhub_config.config_path()),
        "kit_root": kit_root,
    }
    return facts


# ---------------------------------------------------------------------------------------------------------
# Files: the kit folder, backups, writing the settings
# ---------------------------------------------------------------------------------------------------------
def looks_like_kit(folder):
    """Return True when `folder` holds the kit parts this script needs (rules/ and templates/)."""
    return os.path.isfile(os.path.join(folder, "rules", "work_harness.md")) and os.path.isdir(
        os.path.join(folder, "templates", "orchestrator"))


def find_kit_root(explicit, required=True):
    """
    Return the kit folder (the one with rules/, hooks/, templates/, tools/), or "" when not found.

    Order: --kit-root; two levels above this skill's folder (plugin install or git clone:
    <kit>/skills/work-hub-setup/scripts/configure.py); the copy that tools/install.sh makes in
    ~/.local/share/workhub-claude/kit. With required=True a missing kit ends the script (exit 2).
    """
    candidates = []
    if explicit:
        candidates.append(workhub_config.expand_path(explicit))
    script_folder = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.abspath(os.path.join(script_folder, "..", "..", "..")))
    candidates.append(workhub_config.expand_path(INSTALLED_KIT))
    for candidate in candidates:
        if looks_like_kit(candidate):
            return candidate
    if required:
        print("The kit folder (with rules/ and templates/) was not found. Looked in: " + ", ".join(candidates)
              + ". Give it with --kit-root, or run tools/install.sh from the repository.", file=sys.stderr)
        sys.exit(2)
    return ""


def backup_file(path):
    """Copy `path` to `path`.bak-YYYYMMDD-HHMMSS (keeping its permissions) and return the backup path."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = path + ".bak-" + stamp
    shutil.copy2(path, backup_path)
    return backup_path


def write_private_json(path, data):
    """
    Write `data` as indented JSON to `path`, readable only by the user (folder 700, file 600).

    The file is written next to its final place first and then renamed over it, so a crash never leaves
    half a settings file behind.
    """
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    temporary_path = path + ".tmp"
    with open(temporary_path, mode="w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)


def print_problems(problems, warnings):
    """Print problems and warnings, one per line, to standard error."""
    for line in warnings:
        print("warning: " + line, file=sys.stderr)
    for line in problems:
        print("problem: " + line, file=sys.stderr)


# ---------------------------------------------------------------------------------------------------------
# Rules and orchestrator templates
# ---------------------------------------------------------------------------------------------------------
def template_values(config):
    """
    Return the {{name}} -> text table used to fill rules/work_harness.md and the orchestrator templates.

    Every placeholder in those templates must be listed here; render_template() refuses unknown ones.
    """
    values = {
        "owner_name": workhub_config.owner_name(config),
        "time_zone": workhub_config.time_zone(config),
        "task_id_prefix": workhub_config.get_setting(config, "notion.task_id_prefix", default="TK"),
        "hub_page_url": workhub_config.get_setting(config, "notion.hub_page_url", default="(not set yet)"),
        "hooks_dir": HOOKS_DIR,
        "config_path": workhub_config.config_path(),
        "orchestrator_folder": workhub_config.get_setting(config, "workflow.orchestrator.folder", default="~/workhub/orchestrator"),
        "now_max_rows": str(workhub_config.get_setting(config, "workflow.now_limits.max_rows", default=7)),
        "now_max_in_progress": str(workhub_config.get_setting(config, "workflow.now_limits.max_in_progress", default=3)),
        "max_active_conversations": str(workhub_config.get_setting(config, "workflow.max_active_conversations", default=4)),
        "session_records_subdir": workhub_config.get_setting(config, "workflow.session_records_subdir", default="docs/sessions"),
    }
    return values


# CONDITIONAL_BLOCK finds a part of a template that is kept only when a setting is true:
#   <!-- if workflow.orchestrator.enabled -->      the opening line; group 1 = the dotted setting name
#   (.*?)                                          group 2 = everything up to the closing line; "?" makes the
#                                                  match stop at the FIRST closing line, and re.S lets "."
#                                                  cross line breaks
#   <!-- endif workflow.orchestrator.enabled -->   the closing line; "\1" demands the same name as opened
#   \n?                                            the line break after each marker, when there is one
CONDITIONAL_BLOCK = re.compile(r"<!-- if ([\w.]+) -->\n?(.*?)<!-- endif \1 -->\n?", re.S)

# PLACEHOLDER finds {{name}}: two braces, a name of letters, digits or underscores (group 1), two braces.
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render_template(text, config):
    """
    Return `text` with its conditional blocks resolved and its {{placeholders}} filled from `config`.

    Raises ValueError naming any placeholder that template_values() does not know, so a typo in a
    template can never reach the user's CLAUDE.md as a literal "{{...}}".
    """
    def keep_or_drop(match):
        is_enabled = bool(workhub_config.get_setting(config, match.group(1), default=False))
        return match.group(2) if is_enabled else ""

    resolved = CONDITIONAL_BLOCK.sub(keep_or_drop, text)
    values = template_values(config)
    unknown = sorted(set(name for name in PLACEHOLDER.findall(resolved) if name not in values))
    if unknown:
        raise ValueError("unknown placeholders in template: " + ", ".join(unknown))
    filled = PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), resolved)
    return filled


def replace_rules_block(existing_text, block_text):
    """
    Return `existing_text` with the workhub-claude rules block replaced by `block_text` (or appended).

    The block is everything from the line starting with RULES_BEGIN_PREFIX to the RULES_END line. When
    `block_text` is "" the block and its markers are removed. Text outside the block is never changed.
    """
    lines = existing_text.splitlines(keepends=True)
    begin_index = None
    end_index = None
    for index, line in enumerate(lines):
        if line.startswith(RULES_BEGIN_PREFIX) and begin_index is None:
            begin_index = index
        if line.strip() == RULES_END and begin_index is not None:
            end_index = index
            break
    if begin_index is not None and end_index is None:
        raise ValueError("the rules block has a begin marker but no end marker; fix the file by hand first")
    new_block = ""
    if block_text:
        new_block = RULES_BEGIN + "\n" + block_text.rstrip("\n") + "\n" + RULES_END + "\n"
    if begin_index is None:
        if not new_block:
            return existing_text
        separator = "" if existing_text.endswith("\n\n") or not existing_text else ("\n" if existing_text.endswith("\n") else "\n\n")
        return existing_text + separator + new_block
    before = "".join(lines[:begin_index])
    after = "".join(lines[end_index + 1:])
    return before + new_block + after


# ---------------------------------------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------------------------------------
def command_detect(arguments):
    """Print the detected facts as JSON."""
    print(json.dumps(detect_facts(), indent=2, ensure_ascii=False))
    return 0


def command_write(arguments):
    """Merge the answers with the defaults, check them, show them, and write them with --apply."""
    answers_path = workhub_config.expand_path(arguments.answers)
    if not os.path.isfile(answers_path):
        print("answers file not found: " + answers_path, file=sys.stderr)
        return 2
    with open(answers_path, mode="r", encoding="utf-8") as handle:
        answers = json.load(handle)
    if not isinstance(answers, dict):
        print("the answers file must hold a JSON object ({...})", file=sys.stderr)
        return 2

    # An earlier settings file is the starting point when it exists, so re-running setup keeps what the
    # user does not change (for example the Notion addresses that setup stored the first time).
    target = workhub_config.config_path()
    base = default_config()
    if os.path.isfile(target):
        base = merge_settings(base, workhub_config.load_config(target))
    config = normalize_settings(merge_settings(base, answers))
    problems, warnings = validate_settings(config)

    print(json.dumps(config, indent=2, ensure_ascii=False))
    print_problems(problems, warnings)
    if problems:
        print("Not written: fix the problems above in the answers and run again.", file=sys.stderr)
        return 1
    if not arguments.apply:
        print("(dry run) would write " + target + "; add --apply to write it.")
        return 0
    if os.path.isfile(target):
        print("backup: " + backup_file(target))
    write_private_json(target, config)
    print("wrote " + target + " (mode 600)")
    return 0


def parse_cli_value(text, as_json):
    """Return `text` as a Python value: parsed as JSON with --json (lists, numbers, true/false), else as text."""
    if as_json:
        return json.loads(text)
    return text


def command_set(arguments):
    """Set one dotted key of the settings file (creating missing levels), shown first, written with --apply."""
    target = workhub_config.config_path()
    try:
        config = workhub_config.load_config(target)
    except workhub_config.ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    value = parse_cli_value(arguments.value, arguments.json)
    parts = arguments.key.split(".")
    level = config
    for part in parts[:-1]:
        if not isinstance(level.get(part), dict):
            level[part] = {}
        level = level[part]
    old_value = level.get(parts[-1])
    level[parts[-1]] = value
    print(arguments.key + ": " + json.dumps(old_value, ensure_ascii=False) + " -> " + json.dumps(value, ensure_ascii=False))
    if not arguments.apply:
        print("(dry run) add --apply to write " + target)
        return 0
    write_private_json(target, config)
    print("wrote " + target)
    return 0


def command_validate(arguments):
    """Check the settings file and list problems and warnings."""
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    problems, warnings = validate_settings(merge_settings(default_config(), config))
    print_problems(problems, warnings)
    if problems:
        return 1
    print("ok: " + workhub_config.config_path())
    return 0


def command_render_rules(arguments, remove=False):
    """Write (or with remove=True, take out) the rules block in the target CLAUDE.md file."""
    target = workhub_config.expand_path(arguments.target)
    existing_text = ""
    if os.path.isfile(target):
        with open(target, mode="r", encoding="utf-8") as handle:
            existing_text = handle.read()
    block_text = ""
    if not remove:
        try:
            config = workhub_config.load_config()
        except workhub_config.ConfigError as error:
            print(str(error), file=sys.stderr)
            return 2
        kit_root = find_kit_root(arguments.kit_root)
        with open(os.path.join(kit_root, "rules", "work_harness.md"), mode="r", encoding="utf-8") as handle:
            template_text = handle.read()
        block_text = render_template(template_text, merge_settings(default_config(), config))
    new_text = replace_rules_block(existing_text, block_text)
    if new_text == existing_text:
        print("nothing to change in " + target)
        return 0
    if not arguments.apply:
        if remove:
            print("(dry run) would remove the workhub-claude rules block from " + target)
        else:
            print(block_text)
            print("(dry run) would write the block above into " + target + " (other text unchanged); add --apply")
        return 0
    if os.path.isfile(target):
        print("backup: " + backup_file(target))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, mode="w", encoding="utf-8") as handle:
        handle.write(new_text)
    print(("removed the rules block from " if remove else "wrote the rules block into ") + target)
    return 0


def command_init_orchestrator(arguments):
    """Copy the orchestrator templates into the orchestrator folder, filled in; never overwrite a file."""
    try:
        config = merge_settings(default_config(), workhub_config.load_config())
    except workhub_config.ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not workhub_config.get_setting(config, "workflow.orchestrator.enabled", default=False):
        print("workflow.orchestrator.enabled is false: nothing to do")
        return 0
    kit_root = find_kit_root(arguments.kit_root)
    source_folder = os.path.join(kit_root, "templates", "orchestrator")
    target_folder = workhub_config.expand_path(workhub_config.get_setting(config, "workflow.orchestrator.folder"))
    for name in sorted(os.listdir(source_folder)):
        source_path = os.path.join(source_folder, name)
        target_path = os.path.join(target_folder, name)
        if not os.path.isfile(source_path):
            continue
        if os.path.exists(target_path):
            print("kept (exists): " + target_path)
            continue
        if not arguments.apply:
            print("(dry run) would write " + target_path)
            continue
        with open(source_path, mode="r", encoding="utf-8") as handle:
            filled = render_template(handle.read(), config)
        os.makedirs(target_folder, mode=0o700, exist_ok=True)
        with open(target_path, mode="w", encoding="utf-8") as handle:
            handle.write(filled)
        print("wrote " + target_path)
    log_folder = os.path.join(target_folder, "log")
    if arguments.apply:
        os.makedirs(log_folder, mode=0o700, exist_ok=True)
    print(("" if arguments.apply else "(dry run) ") + "log folder: " + log_folder)
    return 0


def command_kit_root(arguments):
    """Print the kit folder."""
    print(find_kit_root(arguments.kit_root))
    return 0


# ---------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------
def build_parser():
    """Return the argument parser with one sub-parser per subcommand (see the module docstring)."""
    parser = argparse.ArgumentParser(description="Local file work of the work-hub-setup skill (workhub-claude).")
    parser.add_argument("--kit-root", default=None, help="the kit folder, when it is not found by itself")
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("detect", help="print facts about this machine as JSON")

    write_parser = subcommands.add_parser("write", help="write the settings file from an answers file")
    write_parser.add_argument("--answers", required=True, help="JSON file shaped like config/config.example.json")
    write_parser.add_argument("--apply", action="store_true", help="really write (default: only show)")

    set_parser = subcommands.add_parser("set", help="change one value of the settings file")
    set_parser.add_argument("key", help="dotted key, e.g. notion.tasks_data_source")
    set_parser.add_argument("value", help="the new value (text; with --json: JSON such as true, 3, [\"a\"])")
    set_parser.add_argument("--json", action="store_true", help="read the value as JSON")
    set_parser.add_argument("--apply", action="store_true", help="really write (default: only show)")

    subcommands.add_parser("validate", help="check the settings file")

    for name, helptext in (("render-rules", "write the rules block into CLAUDE.md"),
                           ("remove-rules", "remove the rules block from CLAUDE.md")):
        rules_parser = subcommands.add_parser(name, help=helptext)
        rules_parser.add_argument("--target", default="~/.claude/CLAUDE.md", help="file to change (default ~/.claude/CLAUDE.md)")
        rules_parser.add_argument("--apply", action="store_true", help="really write (default: only show)")

    orchestrator_parser = subcommands.add_parser("init-orchestrator", help="copy the orchestrator files")
    orchestrator_parser.add_argument("--apply", action="store_true", help="really write (default: only show)")

    subcommands.add_parser("kit-root", help="print where the kit folder is")
    return parser


def main():
    """Run the chosen subcommand and return its exit status."""
    arguments = build_parser().parse_args()
    if arguments.command == "detect":
        return command_detect(arguments)
    if arguments.command == "write":
        return command_write(arguments)
    if arguments.command == "set":
        return command_set(arguments)
    if arguments.command == "validate":
        return command_validate(arguments)
    if arguments.command == "render-rules":
        return command_render_rules(arguments)
    if arguments.command == "remove-rules":
        return command_render_rules(arguments, remove=True)
    if arguments.command == "init-orchestrator":
        return command_init_orchestrator(arguments)
    return command_kit_root(arguments)


if __name__ == "__main__":
    sys.exit(main())
