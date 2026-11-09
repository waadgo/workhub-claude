#!/usr/bin/env python3
"""research_log.py: the deterministic parts of the research-log skill (settings, labels, keys, pending queue, hash).

PURPOSE
    The research-log skill publishes local Markdown records (session records, plans, reports, notes) to a Notion
    "Research log" database. Claude does the Notion calls and the judgement (what to publish, what to redact).
    This script does the small parts that must come out the same every time:
      settings      every setting the skill uses, resolved from the workhub-claude settings file, with defaults;
      label         the machine label for the "Clusters" property (one rule for every kit skill);
      project-key   the short key of a project, used at the start of every Source key;
      pending       the queue of documents waiting for a session with Notion (list, add, remove);
      hash          the first 12 hex characters of a file's SHA-256 (the "Published ... from SHA-256" line).

USAGE
    python3 research_log.py settings                      # JSON: data source, projects, labels, queue, ...
    python3 research_log.py label                         # this machine's label, e.g. "ws01" or "Mycluster"
    python3 research_log.py label ws02
    python3 research_log.py project-key "Project A"       # -> project-a
    python3 research_log.py pending list
    python3 research_log.py pending add ~/projects/demo/docs/sessions/2026-01-15_1400_qc-run.md [--dry-run]
    python3 research_log.py pending remove <path> [--dry-run]
    python3 research_log.py hash /tmp/upload/2026-01-15_1400_qc-run.md

INPUTS
    The settings file ~/.config/workhub-claude/config.json (or $WORKHUB_CLAUDE_CONFIG), read with the bundled
    workhub_config.py. Keys: notion.research_log.{enabled,data_source,parent_page_url}, notion.projects,
    owner.{name,time_zone}, machines.{workstations,clusters}, workflow.session_records_subdir, and the skill's own
    options under skill_settings.research-log: project_keys, publish_mode, machine_labels, archive_dir,
    publish_hosts (meanings and defaults: SKILL_DEFAULTS below and SKILL.md).

OUTPUTS
    Printed values. Only `pending add` and `pending remove` write a file: <state_dir>/research-log/pending.txt
    (default ~/.local/state/workhub-claude/research-log/pending.txt, folder mode 700, file mode 600), one absolute
    path per line. --dry-run prints the change without writing it.

EXIT STATUS
    0 = success; 2 = no settings file, a bad argument, or a file that does not exist.

DEPENDENCIES
    Python >= 3.8, standard library only.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import argparse
import hashlib
import json
import os
import sys

# workhub_config.py: the bundled copy of the kit's settings reader (same folder as this script). It finds and
# parses the settings file and answers small questions about it (time zone, machine name, state folder).
from workhub_config import (ConfigError, clusters, config_path, expand_path, get_setting, load_config, owner_name,
                            skill_setting, state_dir, system_name, time_zone, workstations)

SKILL_NAME = "research-log"

# The skill's own options (skill_settings.research-log.<key>) and their defaults.
#   project_keys    {project display name: key}; a project not listed gets a key made from its name.
#   publish_mode    "auto" = publish after substantial work without asking; "ask" = ask once at the end.
#   machine_labels  {machine-name prefix: label} for the "Clusters" property, e.g. {"ws": "Lab"}.
#   archive_dir     a folder that keeps a sanitized copy of every published record; "" = none.
#   publish_hosts   SSH aliases whose pending queues a session with Notion also publishes; "auto" = every listed
#                   workstation or cluster that does not share this machine's home folder.
SKILL_DEFAULTS = {
    "project_keys": {},
    "publish_mode": "auto",
    "machine_labels": {},
    "archive_dir": "",
    "publish_hosts": "auto",
}

# Where another machine keeps its queue, relative to its home folder (the kit's default state folder there).
REMOTE_PENDING_PATH = ".local/state/workhub-claude/research-log/pending.txt"


# ---------------------------------------------------------------------------------------------------------
# Keys and labels
# ---------------------------------------------------------------------------------------------------------
def project_key(name, explicit_keys=None):
    """
    Return the short key of a project: the explicit key when one is set, else the name in lower case with every
    run of other characters turned into one dash.

    Example: "Thesis chapter 1" -> "thesis-chapter-1"; "Data (cleaning)" -> "data-cleaning".
    """
    keys = explicit_keys if isinstance(explicit_keys, dict) else {}
    if keys.get(name):
        return str(keys[name])
    characters = []
    for character in str(name).lower():
        if character.isascii() and character.isalnum():
            characters.append(character)
        elif characters and characters[-1] != "-":
            characters.append("-")
    return "".join(characters).strip("-")


def machine_label(config, system):
    """
    Return the label of a machine for the Research log's "Clusters" property.

    The rule (the same in run-status-figure's collect_env.py):
      1. machine_labels: the entry whose key is the longest prefix of the machine name wins;
      2. a machine listed in machines.clusters: its name with a capital first letter ("mycluster" -> "Mycluster");
      3. otherwise the machine name itself ("ws01").
    """
    name = str(system or "").lower()
    labels = skill_setting(config, SKILL_NAME, "machine_labels", default={})
    if isinstance(labels, dict):
        matches = [prefix for prefix in labels if prefix and name.startswith(str(prefix).lower())]
        if matches:
            longest = max(matches, key=len)
            return str(labels[longest])
    for cluster in clusters(config):
        cluster_name = str(cluster.get("name", ""))
        if cluster_name and cluster_name.lower() == name:
            return cluster_name[:1].upper() + cluster_name[1:]
    return name


def all_labels(config):
    """The labels of every listed machine plus this one, without repeats, in settings order (the initial
    options of "Clusters" when the database is built)."""
    names = [machine.get("name", "") for machine in workstations(config) + clusters(config)]
    names.append(system_name(config))
    labels = []
    for name in names:
        label = machine_label(config, name)
        if label and label not in labels:
            labels.append(label)
    return labels


def publish_hosts(config):
    """The SSH aliases whose pending queues are also published (see SKILL_DEFAULTS); never this machine."""
    chosen = skill_setting(config, SKILL_NAME, "publish_hosts", default=SKILL_DEFAULTS["publish_hosts"])
    if isinstance(chosen, list):
        return [str(alias) for alias in chosen if alias]
    if chosen != "auto":
        return []
    this_machine = system_name(config)
    aliases = []
    for machine in workstations(config) + clusters(config):
        alias = machine.get("ssh_alias") or ""
        is_this_machine = str(machine.get("name", "")).lower() == this_machine
        if alias and not is_this_machine and machine.get("shares_home_with_this_machine") is not True:
            if alias not in aliases:
                aliases.append(alias)
    return aliases


# ---------------------------------------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------------------------------------
def pending_file(config):
    """The absolute path of this machine's pending queue (its folder is created, mode 700)."""
    return os.path.join(state_dir(config, SKILL_NAME), "pending.txt")


def resolved_settings(config):
    """Every setting the skill uses, with defaults filled in, as one dict (printed by `settings`)."""
    enabled = get_setting(config, "notion.research_log.enabled", default=True) is not False
    data_source = get_setting(config, "notion.research_log.data_source", default="")
    if not enabled:
        state = "disabled"
    elif not data_source:
        state = "not built"
    else:
        state = "ready"
    explicit_keys = skill_setting(config, SKILL_NAME, "project_keys", default={})
    projects = [{"name": name, "key": project_key(name, explicit_keys)}
                for name in get_setting(config, "notion.projects", default=[])]
    publish_mode = skill_setting(config, SKILL_NAME, "publish_mode", default=SKILL_DEFAULTS["publish_mode"])
    if publish_mode not in ("auto", "ask"):
        publish_mode = "ask"                     # an unknown value: the more conservative choice
    this_machine = system_name(config)
    return {
        "config_file": config_path(),
        "state": state,
        "enabled": enabled,
        "data_source": data_source,
        "parent_page_url": get_setting(config, "notion.research_log.parent_page_url", default=""),
        "owner_name": owner_name(config),
        "time_zone": time_zone(config),
        "projects": projects,
        "publish_mode": publish_mode,
        "machine_labels": skill_setting(config, SKILL_NAME, "machine_labels", default={}),
        "this_machine": this_machine,
        "this_label": machine_label(config, this_machine),
        "all_labels": all_labels(config),
        "archive_dir": expand_path(skill_setting(config, SKILL_NAME, "archive_dir", default="")),
        "publish_hosts": publish_hosts(config),
        "pending_file": pending_file(config),
        "remote_pending_path": REMOTE_PENDING_PATH,
        "session_records_subdir": get_setting(config, "workflow.session_records_subdir", default="docs/sessions"),
        "claude_projects_dir": expand_path(get_setting(config, "conversations.claude_projects_dir",
                                                       default="~/.claude/projects")),
    }


# ---------------------------------------------------------------------------------------------------------
# Pending queue
# ---------------------------------------------------------------------------------------------------------
def read_pending(path):
    """The queued paths, in order, without empty lines; [] when the file does not exist."""
    if not os.path.isfile(path):
        return []
    with open(path, mode="r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def write_pending(path, entries):
    """Rewrite the queue with `entries` (one per line); the file is readable by its owner only (mode 600)."""
    with open(path, mode="w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(entry + "\n")
    os.chmod(path, 0o600)


def change_pending(path, action, documents, dry_run):
    """Add or remove documents (absolute paths) in the queue; return the messages to print."""
    entries = read_pending(path)
    messages = []
    for document in documents:
        absolute = os.path.abspath(expand_path(document))
        if action == "add":
            if absolute in entries:
                messages.append("already queued: " + absolute)
                continue
            if not os.path.exists(absolute):
                raise FileNotFoundError(absolute)
            entries.append(absolute)
            messages.append(("would queue: " if dry_run else "queued: ") + absolute)
        else:
            if absolute not in entries:
                messages.append("not in the queue: " + absolute)
                continue
            entries = [entry for entry in entries if entry != absolute]
            messages.append(("would remove: " if dry_run else "removed: ") + absolute)
    if not dry_run:
        write_pending(path, entries)
    return messages


# ---------------------------------------------------------------------------------------------------------
# Hash
# ---------------------------------------------------------------------------------------------------------
def short_sha256(path):
    """The first 12 hexadecimal characters of the file's SHA-256 (read in 1 MiB blocks)."""
    digest = hashlib.sha256()
    with open(path, mode="rb") as handle:
        block = handle.read(1024 * 1024)
        while block:
            digest.update(block)
            block = handle.read(1024 * 1024)
    return digest.hexdigest()[:12]


# ---------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Deterministic helpers of the research-log skill.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("settings", help="print every setting the skill uses (JSON)")
    label_parser = commands.add_parser("label", help="print a machine's label for the Clusters property")
    label_parser.add_argument("machine", nargs="?", help="machine name (default: this machine)")
    key_parser = commands.add_parser("project-key", help="print the key of a project")
    key_parser.add_argument("name")
    pending_parser = commands.add_parser("pending", help="list, add to or remove from the pending queue")
    pending_parser.add_argument("action", choices=["list", "add", "remove"])
    pending_parser.add_argument("documents", nargs="*", help="paths of the documents (add, remove)")
    pending_parser.add_argument("--dry-run", action="store_true", help="print the change; write nothing")
    hash_parser = commands.add_parser("hash", help="print the first 12 hex characters of a file's SHA-256")
    hash_parser.add_argument("file")
    arguments = parser.parse_args()

    if arguments.command == "hash":
        if not os.path.isfile(arguments.file):
            print("no such file: " + arguments.file, file=sys.stderr)
            return 2
        print(short_sha256(arguments.file))
        return 0

    try:
        config = load_config(required=True)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2

    if arguments.command == "settings":
        print(json.dumps(resolved_settings(config), indent=2, ensure_ascii=False))
        return 0
    if arguments.command == "label":
        print(machine_label(config, arguments.machine or system_name(config)))
        return 0
    if arguments.command == "project-key":
        explicit_keys = skill_setting(config, SKILL_NAME, "project_keys", default={})
        print(project_key(arguments.name, explicit_keys))
        return 0

    path = pending_file(config)
    if arguments.action == "list":
        for entry in read_pending(path):
            print(entry)
        return 0
    if not arguments.documents:
        print("give at least one document path", file=sys.stderr)
        return 2
    try:
        messages = change_pending(path, arguments.action, arguments.documents, arguments.dry_run)
    except FileNotFoundError as error:
        print("no such file: " + str(error), file=sys.stderr)
        return 2
    for message in messages:
        print(message)
    print("queue: " + path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
