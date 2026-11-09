#!/usr/bin/env python3
"""
workhub_config.py: read the one settings file that every workhub-claude skill, hook and tool uses.

PURPOSE
    workhub-claude is a set of Claude Code skills that run a Notion "Work Hub" (an Inbox plus a Tasks
    database) together with the user's conversations, machines and clusters. Nothing personal is written
    inside the skills. Everything that differs between users (name, time zone, Notion page and database
    addresses, projects, machines, cluster accounts, folders) lives in ONE JSON file that the
    `work-hub-setup` skill writes after asking the user:

        ~/.config/workhub-claude/config.json        (or the file named by $WORKHUB_CLAUDE_CONFIG)

    This module finds that file, reads it, and answers small questions about it ("which time zone?",
    "what is this machine called?", "where does state go?"). Every skill keeps an identical copy of this
    file in its own scripts/ folder, so a skill still works when it is copied alone into ~/.claude/skills/
    or ~/.codex/skills/. The master copy is lib/workhub_config.py in the repository; the test
    tests/test_kit.py checks that all copies are identical.

INPUTS
    The JSON settings file (format: config/config.example.json and config/CONFIG_REFERENCE.md in the
    repository). Environment variables:
        WORKHUB_CLAUDE_CONFIG   optional path of another settings file (used by tests).
        CC_CLUSTER (or the name given in system_name.override_env)
                                optional machine name that wins over the host name.

OUTPUTS
    Python values for the caller; the command line prints values (see USAGE). Nothing is written, except
    that state_dir() creates the state folder (mode 700) when it is missing.

USAGE (command line, for SKILL.md instructions)
    python3 workhub_config.py path                       # where the settings file is
    python3 workhub_config.py check                      # exit 0 if it exists and parses, else 2
    python3 workhub_config.py get notion.tasks_data_source
    python3 workhub_config.py get owner.time_zone --default UTC
    python3 workhub_config.py system-name                # this machine's name, as used in file paths
    python3 workhub_config.py show                       # the whole file, pretty-printed

USAGE (Python)
    from workhub_config import load_config, get_setting, system_name
    config = load_config()
    data_source = get_setting(config, "notion.tasks_data_source", required=True)

DEPENDENCIES
    Python >= 3.8, standard library only.

EXIT STATUS (command line)
    0 = success; 2 = no settings file, unreadable file, or a required value is missing.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ---------------------------------------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------------------------------------
# argparse: the small command line at the end. json: the settings file format. os: paths and environment
# variables. socket: the host name when no override is set. sys: exit codes and error output.
import argparse
import json
import os
import socket
import sys

# ---------------------------------------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------------------------------------
# The default place of the settings file. ~ is expanded later, so the file follows the user's home folder.
DEFAULT_CONFIG_PATH = "~/.config/workhub-claude/config.json"

# The environment variable that may point to another settings file (tests use it; a user may too).
CONFIG_PATH_ENV = "WORKHUB_CLAUDE_CONFIG"

# The message every caller shows when the settings file is missing: it tells the user what to do.
SETUP_HINT = (
    "workhub-claude is not set up on this machine yet. "
    "Say \"set up my work hub\" to Claude (skill work-hub-setup), which asks a few questions and writes "
    + DEFAULT_CONFIG_PATH + "."
)


class ConfigError(Exception):
    """Raised when the settings file is missing or unreadable, or a required value is empty."""


# ---------------------------------------------------------------------------------------------------------
# Finding and reading the file
# ---------------------------------------------------------------------------------------------------------
def expand_path(value):
    """
    Return `value` with ~ and $VARIABLES expanded, or "" when `value` is empty or None.

    Example: "~/workhub/outputs" -> "/home/alex/workhub/outputs".
    """
    if not value:
        return ""
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    return expanded


def config_path():
    """Return the absolute path of the settings file ($WORKHUB_CLAUDE_CONFIG wins over the default)."""
    chosen = os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
    return os.path.abspath(expand_path(chosen))


def load_config(path=None, required=True):
    """
    Read the settings file and return it as a dict.

    Parameters
        path      optional path; default config_path().
        required  True: raise ConfigError when the file is missing. False: return {} instead (hooks use
                  this, so that a machine without the kit set up is left alone).

    Raises
        ConfigError when the file is missing (and required), is not valid JSON, or is not a JSON object.
    """
    settings_file = path or config_path()
    if not os.path.isfile(settings_file):
        if required:
            raise ConfigError(SETUP_HINT + " (looked for " + settings_file + ")")
        return {}
    try:
        with open(settings_file, mode="r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError) as error:
        raise ConfigError("Cannot read " + settings_file + ": " + str(error))
    if not isinstance(loaded, dict):
        raise ConfigError(settings_file + " must hold a JSON object ({...}), not " + type(loaded).__name__)
    return loaded


# ---------------------------------------------------------------------------------------------------------
# Reading values
# ---------------------------------------------------------------------------------------------------------
def get_setting(config, dotted_key, default=None, required=False):
    """
    Return one value of the settings, addressed by a dotted key such as "notion.tasks_data_source".

    Each part of the key selects one level of the nested dicts: "notion.views.now" is
    config["notion"]["views"]["now"]. A missing level, None, "" or an empty list counts as not set.

    Parameters
        config      the dict from load_config().
        dotted_key  levels separated by dots.
        default     what to return when the value is not set (and not required).
        required    True: raise ConfigError naming the key when it is not set.
    """
    current = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            current = None
            break
        current = current[part]
    is_unset = current is None or current == "" or current == [] or current == {}
    if is_unset:
        if required:
            raise ConfigError(
                "The setting '" + dotted_key + "' is empty in " + config_path()
                + ". Run the work-hub-setup skill again (\"set up my work hub\") or fill it in by hand."
            )
        return default
    return current


def skill_setting(config, skill_name, key, default=None):
    """
    Return a setting that belongs to one skill: config["skill_settings"][skill_name][key], else `default`.

    Skills keep their own options here so the top level of the file stays the same for everyone.
    """
    per_skill = get_setting(config, "skill_settings." + skill_name, default={})
    if not isinstance(per_skill, dict):
        return default
    value = per_skill.get(key)
    if value is None or value == "":
        return default
    return value


def data_source_id(collection_url):
    """
    Return the bare id of a Notion data source: "collection://1234-..." -> "1234-...".

    Notion's create-pages tool wants the bare id as `data_source_id`; queries want the collection:// URL.
    """
    text = str(collection_url or "").strip()
    prefix = "collection://"
    if text.startswith(prefix):
        return text[len(prefix):]
    return text


def time_zone(config):
    """Return the owner's IANA time zone name (for example "Europe/Paris"); "UTC" when not set."""
    return get_setting(config, "owner.time_zone", default="UTC")


def owner_name(config):
    """Return the owner's first name, used in note attributions such as "(Alex, Nov 4)"; "the user" if unset."""
    return get_setting(config, "owner.name", default="the user")


def system_name(config=None):
    """
    Return this machine's short name, as written in file paths shown to people ("ws01:~/projects/x").

    Order: the environment variable named by system_name.override_env (default CC_CLUSTER), else the
    host name up to its first dot, in lower case. A name listed in system_name.aliases is then replaced
    (for example {"login1": "mycluster"}, so every login node of a cluster gets the cluster's name).
    """
    settings = config if config is not None else load_config(required=False)
    override_variable = get_setting(settings, "system_name.override_env", default="CC_CLUSTER")
    name = os.environ.get(override_variable, "").strip()
    if not name:
        name = socket.gethostname().split(".")[0]
    name = name.lower()
    aliases = get_setting(settings, "system_name.aliases", default={})
    if isinstance(aliases, dict) and name in aliases:
        name = str(aliases[name]).lower()
    return name


def state_dir(config, subfolder=""):
    """
    Return (and create, mode 700) the folder where skills and hooks keep their small state files.

    Default ~/.local/state/workhub-claude; `subfolder` adds one level, for example "conversation_titles".
    Mode 700 = only the owner can list or open it, because state files name the user's conversations.
    """
    base = expand_path(get_setting(config, "paths.state_dir", default="~/.local/state/workhub-claude"))
    folder = os.path.join(base, subfolder) if subfolder else base
    # os.makedirs applies `mode` only to the last folder it creates, so the base folder is made first (700 too).
    os.makedirs(base, mode=0o700, exist_ok=True)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    return folder


def workstations(config):
    """Return the list of workstation dicts (machines.workstations), [] when none are listed."""
    listed = get_setting(config, "machines.workstations", default=[])
    return [machine for machine in listed if isinstance(machine, dict)]


def clusters(config):
    """Return the list of cluster dicts (machines.clusters), [] when none are listed."""
    listed = get_setting(config, "machines.clusters", default=[])
    return [cluster for cluster in listed if isinstance(cluster, dict)]


# ---------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------
def main():
    """Small command line for SKILL.md instructions: path, check, get, system-name, show."""
    parser = argparse.ArgumentParser(
        description="Read the workhub-claude settings file (" + DEFAULT_CONFIG_PATH + ")."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("path", help="print where the settings file is")
    subcommands.add_parser("check", help="exit 0 if the settings file exists and parses")
    get_parser = subcommands.add_parser("get", help="print one value, e.g. notion.tasks_data_source")
    get_parser.add_argument("key", help="dotted key, e.g. owner.time_zone")
    get_parser.add_argument("--default", default=None, help="printed when the value is not set")
    subcommands.add_parser("system-name", help="print this machine's name as used in file paths")
    subcommands.add_parser("show", help="print the whole settings file")
    arguments = parser.parse_args()

    if arguments.command == "path":
        print(config_path())
        return 0
    try:
        config = load_config()
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    if arguments.command == "check":
        print("ok: " + config_path())
        return 0
    if arguments.command == "system-name":
        print(system_name(config))
        return 0
    if arguments.command == "show":
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return 0
    value = get_setting(config, arguments.key, default=arguments.default)
    if value is None:
        print("The setting '" + arguments.key + "' is empty in " + config_path(), file=sys.stderr)
        return 2
    if isinstance(value, (dict, list)):
        print(json.dumps(value, indent=2, ensure_ascii=False))
    else:
        print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
