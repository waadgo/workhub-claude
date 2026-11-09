#!/usr/bin/env python3
"""
session_start_system_name.py: tell Claude, at the start of every conversation, which machine it runs on.

PURPOSE
    The workhub-claude rules ask Claude to write every file path shown to people as "<system>:/absolute/path"
    (for example "ws01:/home/alex/projects/demo/report.md"), because the same files, conversations and settings may be
    used on several machines (a shared home folder, clusters). Claude must never guess the machine name. Claude Code
    runs a "SessionStart" hook when a conversation starts (and when it resumes, is cleared or is compacted) and adds
    what the hook prints to Claude's context. This script prints one line:
        System name for file paths: ws01
    The name comes from workhub_config.system_name(config): the environment variable named by
    system_name.override_env (default CC_CLUSTER), else the short host name, in lower case, then the renames listed in
    system_name.aliases (for example {"login1": "mycluster"}).

INPUT
    Standard input: Claude Code's SessionStart event (JSON); it is not needed and not read.
    The settings file ~/.config/workhub-claude/config.json (or $WORKHUB_CLAUDE_CONFIG).

OUTPUT
    One line on standard output (added to Claude's context), or nothing when the settings file is missing (a machine
    where workhub-claude is not set up is left alone). Writes no file.

SAFETY
    Always exits 0: any error is swallowed, so starting a conversation never fails because of this hook.

INSTALLATION
    install_hooks.py (same folder) copies it to ~/.local/share/workhub-claude/hooks/ and adds to
    ~/.claude/settings.json: "SessionStart": python3 ~/.local/share/workhub-claude/hooks/session_start_system_name.py

EXAMPLE
    python3 session_start_system_name.py           -> System name for file paths: ws01

DEPENDENCIES
    Python >= 3.8, standard library, plus workhub_config.py in the same folder.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).

DOCUMENTATION LEVEL
    ULTRA-style for a small tool: every block has an explanation above it.
"""

# os: the folder of this file. sys: the import path and the exit code.
import os
import sys

# No __pycache__ folder next to workhub_config.py (the install folder keeps only the installed files).
sys.dont_write_bytecode = True
# The settings reader is next to this file; putting this folder first on the import path makes `import
# workhub_config` load that copy, whatever the working directory is.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    """Print "System name for file paths: <name>" when workhub-claude is set up on this machine."""
    # Imported here, inside the try of the caller below, so that even a missing workhub_config.py cannot fail.
    import workhub_config
    # required=False: {} when there is no settings file; then nothing is printed.
    config = workhub_config.load_config(required=False)
    if not config:
        return
    # DEBUG CHECK: the printed name should be the one used in "<system>:/path" strings, e.g. "ws01".
    print("System name for file paths: " + workhub_config.system_name(config))


# Run only when executed as a script; any error is swallowed and the exit code is always 0.
if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - never fail a conversation start
        pass
    sys.exit(0)
