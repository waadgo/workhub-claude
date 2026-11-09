#!/usr/bin/env python3
"""
install_cluster_login.py: install the `cluster-login` command and (with --ssh-config) the ssh settings it needs.

PURPOSE
    `cluster-login` (same folder) logs in once to each cluster (SSH key + one second-factor approval, for example
    Duo) and keeps that connection open, so later ssh/scp/rsync calls from the same machine, the user's or Claude's,
    need no new approval. For that, ssh needs a few settings in ~/.ssh/config. This installer asks nothing (the
    work-hub-setup skill asks the user first); it takes everything from options or from the workhub-claude settings
    file, and:
      1. copies cluster-login to the install folder (option --install-dir, setting cluster_login.install_dir, default
         ~/.local/bin), mode 755;
      2. with --ssh-config, writes ONE managed block into ~/.ssh/config, between the marker lines
             # >>> workhub-claude cluster-login >>>
             # <<< workhub-claude cluster-login <<<
         holding: one "Host <alias> <hostname>" entry per cluster (HostName, User); keep-alive settings; the shared
         connection (ControlMaster auto, ControlPath in the socket folder, ControlPersist yes), guarded by
         `Match ... exec "test -O <socket folder>"`; and the Claude safety block: when the variable CLAUDECODE is set
         (Claude Code sets it for every command it runs), BatchMode yes + ProxyCommand false, so Claude never
         triggers a second-factor prompt and never opens new logins that the cluster would refuse;
      3. --remove takes both away again (the block, and the installed copy when it is unchanged).
    The dry run (the default) prints the plan and the change of ~/.ssh/config as a diff, also when --ssh-config is
    not given, so the user can see the block first.

WHY A MACHINE-LOCAL SOCKET FOLDER
    The shared connection is a Unix socket file (ControlPath). It must be in a private folder on the local disk of
    THIS machine, for three reasons:
      - when the home folder is shared between machines (NFS and similar network file systems, common on lab
        workstations and clusters), a socket file created there by machine A is visible on machine B but cannot carry
        B's connections, and many network file systems cannot hold socket files at all;
      - anyone who can open the socket uses the logged-in connection without any approval, so the folder must be
        owned by the user and closed to others (mode 700);
      - ~/.ssh/config is shared too when the home folder is shared, so the same settings run on every machine. The
        guard `Match ... exec "test -O <folder>"` applies the shared-connection settings only where that folder exists
        and belongs to the user; other machines simply connect the normal way.
    Good choices: /run/user/%i on most Linux systems (systemd's private runtime folder; %i = your numeric user ID,
    expanded by ssh), or a private folder such as /tmp/user/%i where the system creates one. The socket file name
    adds about 62 characters to the folder (ssh-<40-character hash> plus a temporary suffix), and socket paths are
    limited to 104-108 characters, so the folder path must be short.

WHY THE BLOCK GOES AT THE TOP OF ~/.ssh/config
    ssh uses the FIRST value it finds for each option. At the top, an earlier "Host *" line elsewhere in the file
    cannot switch off the shared connection for the clusters. The block's lines match only the listed cluster hosts,
    and its last line is "Match all", so everything after the block applies to all hosts exactly as before. When a
    block already exists, it is replaced where it is.

INPUT
    Options (see --help):
      --apply / --dry-run   do it, or only print the plan and the diff (the default).
      --ssh-config          also write the managed block into ~/.ssh/config (backup first).
      --remove              remove the block and the installed copy (also --uninstall); a dry run unless --apply.
      --check               exit 0 when cluster-login is installed and current (and the block, when one exists or
                            --ssh-config is given); prints, for information, whether ssh would share connections.
      --cluster SPEC        a cluster, repeatable: a preset alias (rorqual, fir, nibi, narval, trillium, tamia: the
                            public clusters of the Digital Research Alliance of Canada) or alias=hostname for any other
                            host. Given clusters replace the settings' list.
      --user NAME           user name on the clusters (all of them); default: machines.clusters[].user per cluster.
      --socket-dir DIR      the machine-local socket folder (setting cluster_login.socket_dir).
      --install-dir DIR     where cluster-login goes (setting cluster_login.install_dir, default ~/.local/bin).
      --ssh-config-file F   the ssh configuration file (default ~/.ssh/config).
      --overwrite           replace an installed cluster-login that differs from this kit's copy (backup first).
      --no-settings         ignore the settings file; use only the options.
      --list-presets        print the built-in presets and stop.
    Settings file (read only, never written): ~/.config/workhub-claude/config.json or $WORKHUB_CLAUDE_CONFIG.
    Keys: machines.clusters[] (name, ssh_alias, hostname, user), cluster_login.enabled, cluster_login.socket_dir,
    cluster_login.install_dir. Clusters come from the settings only when cluster_login.enabled is true.

OUTPUT
    The plan, the diff and what was done, on standard output. Files: <install folder>/cluster-login, ~/.ssh/config,
    backups <file>.bak-workhub-claude-YYYYmmdd-HHMMSS. Exit codes: 0 = done, nothing to do, or dry run;
    1 = refused (installed copy differs, no --overwrite) or --check found something not current; 2 = bad input.

EXAMPLES
    python3 install_cluster_login.py --cluster rorqual --cluster fir --user alex --socket-dir '/run/user/%i'
    python3 install_cluster_login.py --apply --ssh-config               # everything from the settings file
    python3 install_cluster_login.py --cluster mycluster=login.mycluster.org --user alex \\
            --socket-dir '/run/user/%i' --apply --ssh-config
    python3 install_cluster_login.py --check
    python3 install_cluster_login.py --remove --apply
    (install_cluster_login.sh in this folder runs this file with the same options.)

DEPENDENCIES
    Python >= 3.8, standard library; workhub_config.py (identical copy of the kit's lib/workhub_config.py) in this
    folder; OpenSSH for --check's information lines (optional).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).

DOCUMENTATION LEVEL
    ULTRA-style for a small tool: every block has an explanation above it.
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------------------------------------------------

# argparse: options. difflib: the diff of ~/.ssh/config. filecmp: compare the installed copy with the kit's.
# getpass: the local user name (for %u). os, shutil: paths, copies, PATH lookups. re: check names. subprocess: ssh -G
# for --check. sys: exit codes and the import path. time: time stamps of backups.
import argparse
import difflib
import filecmp
import getpass
import os
import re
import shutil
import subprocess
import sys
import time

# No __pycache__ folder in the kit when workhub_config is imported.
sys.dont_write_bytecode = True
# The folder of this file (the kit's tools/ folder): cluster-login and workhub_config.py are next to it.
TOOLS_FOLDER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_FOLDER)
# workhub_config.py: load_config(required=False) -> settings dict ({} without a settings file), get_setting(config,
# "a.b", default), clusters(config) -> list of cluster dicts, expand_path(text) -> ~ and $VARS expanded.
import workhub_config  # noqa: E402 - the import path must be set first

# ---------------------------------------------------------------------------------------------------------------------
# Fixed settings
# ---------------------------------------------------------------------------------------------------------------------

# The marker lines of the managed block (cluster-login reads the "# aliases:" line between them).
BLOCK_START = "# >>> workhub-claude cluster-login >>>"
BLOCK_END = "# <<< workhub-claude cluster-login <<<"
# Suffix of every backup copy, followed by a time stamp.
BACKUP_TAG = ".bak-workhub-claude-"
# The command copied by this installer.
TOOL_NAME = "cluster-login"
DEFAULT_INSTALL_DIR = "~/.local/bin"
DEFAULT_SSH_CONFIG = "~/.ssh/config"
# Built-in presets: the public login hosts of the Digital Research Alliance of Canada clusters. "also" = other names
# of the same login host (matched too); "options" = extra ssh options for that cluster.
# narval: second factor through keyboard-interactive, so it is listed explicitly after the SSH key.
PRESETS = {
    "rorqual": {"hostname": "rorqual.alliancecan.ca", "also": [], "options": []},
    "fir": {"hostname": "fir.alliancecan.ca", "also": [], "options": []},
    "nibi": {"hostname": "nibi.alliancecan.ca", "also": [], "options": []},
    "narval": {"hostname": "narval.alliancecan.ca", "also": ["narval.computecanada.ca"],
               "options": ["PreferredAuthentications publickey,keyboard-interactive"]},
    "trillium": {"hostname": "trillium.alliancecan.ca", "also": [], "options": []},
    "tamia": {"hostname": "tamia.alliancecan.ca", "also": [], "options": []},
}
# Allowed characters. Regular expressions: ^ start, [...] one allowed character, + one or more, $ end.
#   alias and user: letters, digits, ".", "_", "-";   host name: letters, digits, ".", "-".
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
# The socket folder: "/" or "~/" first, then path characters and the ssh tokens %i (user ID) or %u (user name).
# Spaces and quotes are refused because the folder is written inside exec "test -O <folder>".
SOCKET_DIR_PATTERN = re.compile(r"^(/|~/)[A-Za-z0-9._/%-]*$")
# Characters ssh adds to the folder for a socket: "/ssh-" + a 40-character hash + a 17-character temporary suffix.
SOCKET_NAME_LENGTH = 1 + 4 + 40 + 17
# The smallest limit of a socket path (macOS 104 bytes; Linux 108).
SOCKET_PATH_LIMIT = 104


class InputError(Exception):
    """Raised for a bad option or setting; main() prints it and exits with code 2."""


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------------

def backup_path_for(path):
    """Return a free backup name next to `path`: <path>.bak-workhub-claude-YYYYmmdd-HHMMSS (plus -2, -3 if taken)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path + BACKUP_TAG + stamp
    counter = 2
    while os.path.exists(candidate):
        candidate = path + BACKUP_TAG + stamp + "-" + str(counter)
        counter += 1
    return candidate


def write_text_atomically(path, text, mode):
    """Write `text` to `path` through a temporary file and a rename, with permission bits `mode`."""
    temporary_path = path + ".partial-workhub-claude"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary_path, mode)
    # One-step swap: ssh never reads a half-written configuration.
    os.replace(temporary_path, path)


def expand_socket_dir(socket_dir):
    """Return the socket folder as this machine sees it: ~ -> home, %i -> numeric user ID, %u -> user name.

    Example (user ID 1000): "/run/user/%i" -> "/run/user/1000".
    """
    expanded = socket_dir.replace("%i", str(os.getuid())).replace("%u", getpass.getuser())
    return os.path.expanduser(expanded)


# ---------------------------------------------------------------------------------------------------------------------
# Which clusters, user, folders
# ---------------------------------------------------------------------------------------------------------------------

def cluster_from_preset_or_spec(spec):
    """Turn one --cluster value into (alias, hostname or "", also, options).

    "rorqual" -> the preset; "mycluster=login.mycluster.org" -> that host; an unknown alias without "=" -> hostname ""
    (the caller then looks in the settings, or stops).
    """
    if "=" in spec:
        alias, hostname = spec.split("=", 1)
        return alias.strip(), hostname.strip(), [], []
    alias = spec.strip()
    preset = PRESETS.get(alias)
    if preset:
        return alias, preset["hostname"], list(preset["also"]), list(preset["options"])
    return alias, "", [], []


def resolve_clusters(options, config):
    """Return the list of clusters to configure: dicts with alias, hostname, also, options, user.

    Order: --cluster options (users from --user, else from the settings entry with the same alias); else the
    settings' machines.clusters when cluster_login.enabled is true. Raises InputError for bad or missing values.
    """
    # Settings entries by alias (ssh_alias, else name), so --cluster can find the user name there.
    settings_by_alias = {}
    for entry in workhub_config.clusters(config):
        alias = str(entry.get("ssh_alias") or entry.get("name") or "").strip()
        if alias:
            settings_by_alias[alias] = entry
    raw = []
    if options.cluster:
        for spec in options.cluster:
            alias, hostname, also, extra = cluster_from_preset_or_spec(spec)
            entry = settings_by_alias.get(alias, {})
            if not hostname:
                hostname = str(entry.get("hostname") or "").strip()
            raw.append({"alias": alias, "hostname": hostname, "also": also, "options": extra,
                        "user": options.user or str(entry.get("user") or "").strip()})
    elif workhub_config.get_setting(config, "cluster_login.enabled", default=False) is True:
        for alias, entry in settings_by_alias.items():
            # A preset by alias or by name fills a missing host name and adds the preset's extra names and options.
            preset = PRESETS.get(alias) or PRESETS.get(str(entry.get("name") or ""), {})
            hostname = str(entry.get("hostname") or preset.get("hostname") or "").strip()
            raw.append({"alias": alias, "hostname": hostname,
                        "also": [name for name in preset.get("also", []) if name != hostname],
                        "options": list(preset.get("options", [])),
                        "user": options.user or str(entry.get("user") or "").strip()})
    # Check every value before it is written into the ssh configuration.
    seen = set()
    for cluster in raw:
        alias = cluster["alias"]
        if not NAME_PATTERN.match(alias) or alias.startswith("-"):
            raise InputError(f"not a valid ssh alias: '{alias}' (letters, digits, '.', '_', '-')")
        if alias in seen:
            raise InputError(f"cluster '{alias}' is listed twice")
        seen.add(alias)
        if not cluster["hostname"]:
            raise InputError(f"no host name for '{alias}': use --cluster {alias}=<hostname> "
                             f"(presets: {', '.join(sorted(PRESETS))})")
        if not HOST_PATTERN.match(cluster["hostname"]):
            raise InputError(f"not a valid host name: '{cluster['hostname']}'")
        if cluster["user"] and not NAME_PATTERN.match(cluster["user"]):
            raise InputError(f"not a valid user name: '{cluster['user']}'")
    return raw


def socket_dir_problems(socket_dir):
    """Return (errors, warnings) about the socket folder: errors stop the installer, warnings are printed."""
    errors = []
    warnings = []
    if not SOCKET_DIR_PATTERN.match(socket_dir):
        errors.append(f"socket folder '{socket_dir}': must start with / (or ~/) and hold only letters, digits, "
                      "'.', '_', '-', '/', and the ssh tokens %i or %u (no spaces or quotes)")
        return errors, warnings
    local_path = expand_socket_dir(socket_dir)
    home = os.path.expanduser("~").rstrip("/") + "/"
    if (local_path.rstrip("/") + "/").startswith(home):
        warnings.append(f"the socket folder {socket_dir} is inside your home folder. That is fine on a single "
                        "machine, but when the home folder is shared between machines (network file system), "
                        "use a folder on this machine's local disk, e.g. /run/user/%i (see the header).")
    if len(local_path) + SOCKET_NAME_LENGTH > SOCKET_PATH_LIMIT:
        warnings.append(f"the socket folder {local_path} is long ({len(local_path)} characters): with the "
                        f"{SOCKET_NAME_LENGTH} characters ssh adds, the path may exceed the {SOCKET_PATH_LIMIT}-"
                        "character limit of socket paths and ssh would fail; choose a shorter folder.")
    if not os.path.isdir(local_path):
        warnings.append(f"the socket folder {local_path} does not exist on this machine, so ssh will connect the "
                        "normal way here (no shared connection) until it exists. Create it with "
                        f"`mkdir -m 700 -p {local_path}` (a folder under /tmp may vanish at reboot) or choose "
                        "/run/user/%i.")
    else:
        status = os.stat(local_path)
        if status.st_uid != os.getuid():
            warnings.append(f"the socket folder {local_path} does not belong to you, so the guard "
                            "`test -O` fails and ssh will not share connections on this machine.")
        # 0o077 = the permission bits of group and others; any of them set means someone else may open the folder.
        elif status.st_mode & 0o077:
            warnings.append(f"the socket folder {local_path} is open to other users (mode "
                            f"{oct(status.st_mode & 0o777)}); anyone who can open the socket uses your logged-in "
                            "connection. Run `chmod 700 " + local_path + "`.")
    return errors, warnings


# ---------------------------------------------------------------------------------------------------------------------
# The managed block in ~/.ssh/config
# ---------------------------------------------------------------------------------------------------------------------

def build_block(clusters, socket_dir):
    """Return the managed block as a list of lines (each ending with "\\n"), markers included."""
    # Every host name the shared settings apply to, joined with commas (the form `Match host` wants).
    all_hosts = []
    for cluster in clusters:
        for hostname in [cluster["hostname"]] + cluster["also"]:
            if hostname not in all_hosts:
                all_hosts.append(hostname)
    host_list = ",".join(all_hosts)
    aliases = " ".join(cluster["alias"] for cluster in clusters)
    lines = [
        BLOCK_START,
        "# Written by install_cluster_login.sh (workhub-claude). Do not edit between these markers: the installer",
        "# rewrites this block, and `install_cluster_login.sh --remove --apply` removes it.",
        "# Run `cluster-login` in a terminal on each machine: SSH key + one second-factor approval per cluster.",
        "# Every later ssh/scp/rsync to these clusters from the same machine (yours or Claude's) reuses that",
        "# connection, until it breaks or you run `cluster-login logout`.",
        "# aliases: " + aliases,
    ]
    # One entry per cluster: its alias and its host names, the real host name, the user name.
    for cluster in clusters:
        lines.append("Host " + " ".join([cluster["alias"], cluster["hostname"]] + cluster["also"]))
        lines.append("  HostName " + cluster["hostname"])
        if cluster["user"]:
            lines.append("  User " + cluster["user"])
        for option in cluster["options"]:
            lines.append("  " + option)
    lines += [
        "",
        "# Keep idle connections alive; give up after about 10 minutes without an answer.",
        "Match host " + host_list,
        "  ConnectTimeout 20",
        "  ServerAliveInterval 30",
        "  ServerAliveCountMax 20",
        "",
        "# Shared connection. The socket lives in a private folder on THIS machine's local disk, never in a home",
        "# folder that other machines share. Machines where that folder does not exist (or is not yours) connect",
        "# the normal way.",
        f'Match host {host_list} exec "test -O {socket_dir}"',
        "  ControlMaster auto",
        f"  ControlPath {socket_dir}/ssh-%C",
        "  ControlPersist yes",
        "",
        "# Claude cannot answer a second-factor prompt: when it runs ssh (variable CLAUDECODE set), fail at once,",
        "# never prompt. ProxyCommand false: when no shared connection is open (or it is full), fail here instead",
        "# of opening a new login that the cluster would refuse (repeated refusals can get this machine blocked).",
        f'Match host {host_list} exec "printenv CLAUDECODE >/dev/null"',
        "  BatchMode yes",
        "  ProxyCommand false",
        "",
        "# End of the block: the lines after it apply to every host again, as if this block were not here.",
        "Match all",
        BLOCK_END,
    ]
    return [line + "\n" for line in lines]


def find_block(lines):
    """Return (start index, end index) of the managed block in `lines`, or None; raises InputError when broken."""
    starts = [index for index, line in enumerate(lines) if line.rstrip("\n") == BLOCK_START]
    ends = [index for index, line in enumerate(lines) if line.rstrip("\n") == BLOCK_END]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise InputError("the ssh configuration holds a broken or repeated workhub-claude block "
                         f"({len(starts)} start and {len(ends)} end markers); fix it by hand first")
    return starts[0], ends[0]


def config_with_block(old_lines, block_lines):
    """Return the ssh configuration lines with the managed block put in (replaced in place, or added at the top)."""
    position = find_block(old_lines)
    if position is not None:
        start, end = position
        return old_lines[:start] + block_lines + old_lines[end + 1:]
    if not old_lines:
        return list(block_lines)
    # A blank line separates the block from the user's own lines (removed again by config_without_block).
    return list(block_lines) + ["\n"] + old_lines


def config_without_block(old_lines):
    """Return the ssh configuration lines without the managed block (and the one blank line after it)."""
    position = find_block(old_lines)
    if position is None:
        return list(old_lines)
    start, end = position
    rest = old_lines[end + 1:]
    if rest and rest[0].strip() == "":
        rest = rest[1:]
    return old_lines[:start] + rest


def other_entries_for(lines, clusters):
    """Return notes about the user's own lines (outside the block) that mention the same aliases or host names."""
    names = set()
    for cluster in clusters:
        names.update([cluster["alias"], cluster["hostname"]] + cluster["also"])
    position = find_block(lines)
    notes = []
    for index, line in enumerate(lines):
        if position is not None and position[0] <= index <= position[1]:
            continue
        words = line.replace(",", " ").split()
        if not words or words[0].lower() not in ("host", "match"):
            continue
        if names.intersection(words[1:]):
            notes.append(f"line {index + 1}: {line.strip()}")
    return notes


# ---------------------------------------------------------------------------------------------------------------------
# --check helpers
# ---------------------------------------------------------------------------------------------------------------------

def ssh_shares_connection(ssh_config_path, alias):
    """Return "yes", "no" or "unknown": would ssh share connections for `alias` here (ssh -G shows controlmaster auto)?

    `ssh -G` prints the settings ssh would use, without connecting. CLAUDECODE is removed from its environment, so the
    answer is the one for the user's own terminal.
    """
    if not shutil.which("ssh"):
        return "unknown"
    environment = dict(os.environ)
    environment.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(["ssh", "-F", ssh_config_path, "-G", alias], capture_output=True, text=True,
                                timeout=20, env=environment)
    except subprocess.TimeoutExpired:
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return "yes" if "controlmaster auto" in result.stdout.splitlines() else "no"


# ---------------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------------

def parse_options():
    """Read the command-line options (see the header)."""
    parser = argparse.ArgumentParser(
        description="Install cluster-login and (with --ssh-config) its ssh settings. Dry run unless --apply.")
    parser.add_argument("--apply", action="store_true", help="do it (default: print the plan and the diff only)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and the diff only (the default)")
    parser.add_argument("--ssh-config", action="store_true",
                        help="also write the managed block into the ssh configuration (backup first)")
    parser.add_argument("--remove", "--uninstall", dest="remove", action="store_true",
                        help="remove the managed block and the installed cluster-login")
    parser.add_argument("--check", action="store_true", help="exit 0 when installed and current; writes nothing")
    parser.add_argument("--cluster", action="append", default=[],
                        help="preset alias (rorqual, fir, nibi, narval, trillium, tamia) or alias=hostname; repeatable")
    parser.add_argument("--user", default="", help="user name on the clusters (default: per cluster from settings)")
    parser.add_argument("--socket-dir", default="", help="machine-local socket folder, e.g. '/run/user/%%i'")
    parser.add_argument("--install-dir", default="", help="folder for cluster-login (default ~/.local/bin)")
    parser.add_argument("--ssh-config-file", default=DEFAULT_SSH_CONFIG, help="default ~/.ssh/config")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an installed cluster-login that differs from this kit's copy (backup first)")
    parser.add_argument("--no-settings", action="store_true", help="ignore the workhub-claude settings file")
    parser.add_argument("--list-presets", action="store_true", help="print the built-in presets and stop")
    options = parser.parse_args()
    if options.apply and options.dry_run:
        parser.error("--apply and --dry-run exclude each other")
    if options.check and (options.apply or options.remove):
        parser.error("--check only reports; it cannot be combined with --apply or --remove")
    return options


def main():
    """Make the plan, print it, and carry it out with --apply."""
    options = parse_options()
    if options.list_presets:
        for alias in sorted(PRESETS):
            preset = PRESETS[alias]
            extra = ("  also " + ", ".join(preset["also"])) if preset["also"] else ""
            print(f"{alias:<10} {preset['hostname']}{extra}")
        return 0

    # ---- 1. Settings and paths ----------------------------------------------------------------------------------
    try:
        config = {} if options.no_settings else workhub_config.load_config(required=False)
    except workhub_config.ConfigError as error:
        print("ERROR: " + str(error))
        return 2
    install_dir = workhub_config.expand_path(
        options.install_dir or workhub_config.get_setting(config, "cluster_login.install_dir",
                                                          default=DEFAULT_INSTALL_DIR))
    install_dir = os.path.abspath(install_dir)
    # realpath: when ~/.ssh/config is a symbolic link (dotfiles kept elsewhere), the real file is changed.
    ssh_config_path = os.path.realpath(workhub_config.expand_path(options.ssh_config_file))
    socket_dir = str(options.socket_dir or workhub_config.get_setting(config, "cluster_login.socket_dir",
                                                                      default="")).strip().rstrip("/")
    source_tool = os.path.join(TOOLS_FOLDER, TOOL_NAME)
    target_tool = os.path.join(install_dir, TOOL_NAME)
    if not os.path.isfile(source_tool):
        print("ERROR: kit file missing: " + source_tool)
        return 2
    mode_word = "APPLY" if options.apply else "DRY RUN (nothing is written; add --apply to do it)"
    if options.check:
        mode_word = "CHECK (nothing is written)"
    print(f"install_cluster_login {'remove' if options.remove else 'install'}: {mode_word}")

    # ---- 2. The ssh configuration now ----------------------------------------------------------------------------
    old_lines = []
    if os.path.exists(ssh_config_path):
        with open(ssh_config_path, encoding="utf-8") as handle:
            old_lines = handle.readlines()
        # A last line without a line break would be glued to the next line; give it one in the comparison copy.
        if old_lines and not old_lines[-1].endswith("\n"):
            old_lines[-1] = old_lines[-1] + "\n"
    try:
        has_block = find_block(old_lines) is not None
    except InputError as error:
        print("ERROR: " + str(error) + " (" + ssh_config_path + ")")
        return 2

    # ---- 3. The new ssh configuration -----------------------------------------------------------------------------
    clusters = []
    block_lines = []
    if options.remove:
        new_lines = config_without_block(old_lines)
    else:
        try:
            clusters = resolve_clusters(options, config)
        except InputError as error:
            print("ERROR: " + str(error))
            return 2
        if clusters and socket_dir:
            errors, warnings = socket_dir_problems(socket_dir)
            if errors:
                for message in errors:
                    print("ERROR: " + message)
                return 2
            for message in warnings:
                print("WARNING: " + message)
            block_lines = build_block(clusters, socket_dir)
            new_lines = config_with_block(old_lines, block_lines)
        else:
            new_lines = list(old_lines)
        missing = []
        if not clusters:
            missing.append("no clusters (give --cluster, or set cluster_login.enabled and machines.clusters)")
        if not socket_dir:
            missing.append("no socket folder (give --socket-dir, or set cluster_login.socket_dir)")
        # The block is needed when it is to be written (--ssh-config) or checked (it exists already). Without it,
        # only the copy of cluster-login is installed or checked (the user keeps their own ssh settings).
        if missing and (options.ssh_config or (options.check and has_block)):
            print("ERROR: the ssh block needs: " + "; ".join(missing))
            return 2
        if missing:
            print("NOTE: no ssh block preview: " + "; ".join(missing))
        for cluster in clusters:
            if not cluster["user"]:
                print(f"NOTE: no user name for {cluster['alias']}: ssh will use your local user name there "
                      "(give --user, or machines.clusters[].user).")

    # ---- 4. --check: compare and report only ---------------------------------------------------------------------
    if options.check:
        problems = []
        if not os.path.exists(target_tool):
            problems.append(target_tool + " is not installed")
        elif not filecmp.cmp(source_tool, target_tool, shallow=False):
            problems.append(target_tool + " differs from this kit's copy")
        if (options.ssh_config or has_block) and new_lines != old_lines:
            problems.append("the ssh block in " + ssh_config_path + " is missing or not current")
        for cluster in clusters:
            print(f"  ssh shares connections for {cluster['alias']} on this machine: "
                  f"{ssh_shares_connection(ssh_config_path, cluster['alias'])}")
        for problem in problems:
            print("  NOT CURRENT: " + problem)
        if problems:
            print("check: not installed or not current.")
            return 1
        print("check: cluster-login is installed and current.")
        return 0

    # ---- 5. The plan ------------------------------------------------------------------------------------------------
    if options.remove:
        if not os.path.exists(target_tool):
            tool_action = "absent"
        elif filecmp.cmp(source_tool, target_tool, shallow=False):
            tool_action = "remove"
        else:
            tool_action = "keep-differs"
    elif not os.path.exists(target_tool):
        tool_action = "copy"
    elif filecmp.cmp(source_tool, target_tool, shallow=False):
        tool_action = "same"
    else:
        tool_action = "differs"
    labels = {"copy": "copy (new)", "same": "already installed (identical)",
              "differs": ("differs from this kit's copy: replace (backup first)" if options.overwrite
                          else "DIFFERS from this kit's copy: replaced only with --overwrite (backup first)"),
              "remove": "remove (identical to this kit's copy)",
              "keep-differs": "kept: differs from this kit's copy (your own version?); delete by hand",
              "absent": "not installed"}
    print(f"\n{target_tool}: {labels[tool_action]}")
    if not options.remove:
        # `os.pathsep` is ":" on Linux and macOS; PATH lists the folders searched for commands.
        path_folders = [os.path.abspath(os.path.expanduser(folder))
                        for folder in os.environ.get("PATH", "").split(os.pathsep) if folder]
        if install_dir not in path_folders:
            print(f"NOTE: {install_dir} is not on PATH: run it as {target_tool}, or add the folder to PATH.")
    writes_ssh = (options.remove or options.ssh_config) and new_lines != old_lines
    print(f"\n{ssh_config_path}:")
    if new_lines == old_lines:
        print("  no change needed.")
    else:
        if not options.remove and not options.ssh_config:
            print("  NOT written without --ssh-config. This is the block --ssh-config would write:")
        diff_lines = difflib.unified_diff(old_lines, new_lines, fromfile=ssh_config_path + " (now)",
                                          tofile=ssh_config_path + " (after)")
        print("".join(diff_lines))
        for note in other_entries_for(old_lines, clusters):
            print("  NOTE: your own entry for the same host stays; the block comes first, so its values win: " + note)
    if options.remove and has_block:
        print("NOTE: close open connections first with `cluster-login logout`: without the block, ssh no longer "
              "knows where their sockets are.")

    # ---- 6. Refuse a silent overwrite --------------------------------------------------------------------------------
    if tool_action == "differs" and not options.overwrite:
        print(f"REFUSED: {target_tool} differs from this kit's copy. Re-run with --overwrite to replace it "
              "(a backup copy is kept). Nothing was changed.")
        return 1
    if not options.apply:
        return 0

    # ---- 7. Apply ---------------------------------------------------------------------------------------------------
    if tool_action == "remove":
        os.remove(target_tool)
        print("removed " + target_tool)
    elif tool_action in ("copy", "differs"):
        os.makedirs(install_dir, exist_ok=True)
        if tool_action == "differs":
            backup = backup_path_for(target_tool)
            shutil.copy2(target_tool, backup)
            print("backup " + backup)
        with open(source_tool, encoding="utf-8") as handle:
            tool_text = handle.read()
        # 0o755: everyone may run it; only the owner may change it.
        write_text_atomically(target_tool, tool_text, 0o755)
        print("copied " + target_tool)
    if writes_ssh:
        if os.path.exists(ssh_config_path):
            backup = backup_path_for(ssh_config_path)
            shutil.copy2(ssh_config_path, backup)
            print("backup " + backup)
            # Keep the file's mode, but never writable by group or others (ssh refuses such a file).
            file_mode = (os.stat(ssh_config_path).st_mode & 0o777) & ~0o022
        else:
            # ~/.ssh must be private (mode 700) for ssh to trust it.
            os.makedirs(os.path.dirname(ssh_config_path), mode=0o700, exist_ok=True)
            file_mode = 0o600
        write_text_atomically(ssh_config_path, "".join(new_lines), file_mode)
        print("updated " + ssh_config_path)
    print("done.")
    return 0


# Run main() only when executed as a script; its return value becomes the exit code.
if __name__ == "__main__":
    sys.exit(main())
