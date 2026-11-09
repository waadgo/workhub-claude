#!/usr/bin/env python3
"""
install_hooks.py: install (or remove) the workhub-claude conversation hooks for Claude Code.

PURPOSE
    workhub-claude uses two small Claude Code hooks (scripts that Claude Code runs at fixed moments):
      - conversation_title.py, run at every prompt ("UserPromptSubmit"): names each conversation
        "[Nov-04 09:10][ws01][UC] TK-42 Data cleaning" (its header explains how);
      - session_start_system_name.py, run when a conversation starts ("SessionStart"): prints
        "System name for file paths: ws01" into Claude's context.
    They are NOT registered through the plugin's hooks.json: a plugin's files live in a cache folder that changes with
    every plugin version, and the conversations, the work-hub-setup skill and the rules call
    set_conversation_task.py by a path that must stay the same. So this installer:
      1. copies the hook files (conversation_title.py, set_conversation_task.py, session_start_system_name.py and
         workhub_config.py) from this folder to a stable folder, by default ~/.local/share/workhub-claude/hooks/;
      2. adds two entries to Claude Code's user settings file ~/.claude/settings.json:
            "UserPromptSubmit": python3 ~/.local/share/workhub-claude/hooks/conversation_title.py          (timeout 5)
            "SessionStart":     python3 ~/.local/share/workhub-claude/hooks/session_start_system_name.py   (timeout 5)
    and nothing else. Rules it follows:
      - DRY RUN BY DEFAULT: without --apply it only prints the plan and the change of settings.json as a diff.
      - BACKUP: before settings.json is changed, a copy is saved next to it as
        settings.json.bak-workhub-claude-YYYYmmdd-HHMMSS.
      - IDEMPOTENT: an entry already present (same script, written as ~/..., $HOME/... or the absolute path) is not
        added again; files already identical are not copied again. Running it twice changes nothing the second time.
      - NEVER EDITS ANYTHING ELSE in settings.json: before writing, it checks that the new content minus this
        installer's entries equals the old content minus this installer's entries (else it stops). Other hooks
        (including another naming hook) are only reported, never changed.
      - NO SILENT OVERWRITE: an installed hook file that differs from this kit's copy (another version, or edited by
        hand) is replaced only with --overwrite, after a backup copy <file>.bak-workhub-claude-<stamp>.
      - --uninstall removes the two entries (backup first) and the installed files that are identical to this kit's
        copies; files that differ are left in place and listed. The state files of the hook (conversation names,
        default ~/.local/state/workhub-claude/conversation_titles) are never touched.
    Settings file of workhub-claude: the hooks do nothing until ~/.config/workhub-claude/config.json exists (written
    by the work-hub-setup skill). The installer only reports whether it exists; it never reads or writes it otherwise.

INPUT (command line)
    --apply              do it (default: dry run, print the plan only).
    --dry-run            print the plan only (the default; given for clarity in scripts).
    --uninstall          remove instead of install (also a dry run unless --apply is given). --remove is the same.
    --check              exit 0 when all four files are installed unchanged and both entries are registered,
                         else exit 1 with the list of what is missing; writes nothing.
    --overwrite         replace installed hook files that differ from this kit's copies (old copies backed up).
    --target-dir DIR     where the hook files go (default ~/.local/share/workhub-claude/hooks).
    --settings-file F    Claude Code's settings file (default $CLAUDE_CONFIG_DIR/settings.json, else
                         ~/.claude/settings.json).

OUTPUT
    The plan (and, with --apply, what was done) on standard output. Files: the copied hook files in --target-dir,
    the changed settings.json and its backup. Exit codes: 0 = done or nothing to do (or dry run), 1 = refused (a file
    differs and --overwrite was not given), 2 = bad input (settings.json is not valid JSON, python3 on PATH is older
    than 3.9, a kit file is missing).

EXAMPLES
    python3 install_hooks.py                         # dry run: what would be copied and added
    python3 install_hooks.py --apply                 # install
    python3 install_hooks.py --apply --overwrite     # update to this kit version
    python3 install_hooks.py --check                 # exit 0 when installed and current
    python3 install_hooks.py --remove --apply        # remove the entries and the installed files

DEPENDENCIES
    Python >= 3.8 for this installer (standard library); the hooks need python3 >= 3.9 on PATH (checked).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).

DOCUMENTATION LEVEL
    ULTRA-style for a small tool: every block has an explanation above it.
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports (standard library only)
# ---------------------------------------------------------------------------------------------------------------------

# argparse: command-line options. copy: deep copies of the settings dict. difflib: the diff shown in a dry run.
# filecmp: compare an installed file with the kit's copy. json: settings.json. os, shutil: paths and copies.
# shlex: quote a path for the shell when it has unusual characters. subprocess: ask python3 on PATH for its version.
# sys: exit codes. time: the time stamp of backup names.
import argparse
import copy
import difflib
import filecmp
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

# No __pycache__ folder in the kit when the settings reader is imported below.
sys.dont_write_bytecode = True

# ---------------------------------------------------------------------------------------------------------------------
# Fixed settings
# ---------------------------------------------------------------------------------------------------------------------

# The folder of this installer = the kit's hooks/ folder, where the files to copy are.
KIT_HOOKS_FOLDER = os.path.dirname(os.path.abspath(__file__))
# The files copied to the target folder (the tests and this installer are not copied).
HOOK_FILES = ["conversation_title.py", "set_conversation_task.py", "session_start_system_name.py", "workhub_config.py"]
# The entries added to settings.json: (Claude Code hook event, script started by it, timeout in seconds).
REGISTRATIONS = [
    ("UserPromptSubmit", "conversation_title.py", 5),
    ("SessionStart", "session_start_system_name.py", 5),
]
# Default target folder: stable (it does not change with the kit version), per user, outside ~/.claude.
DEFAULT_TARGET_DIR = "~/.local/share/workhub-claude/hooks"
# Suffix of every backup copy this installer makes, followed by a time stamp.
BACKUP_TAG = ".bak-workhub-claude-"
# Text that identifies other naming hooks or system-name hooks in settings.json (only reported, never changed).
FOREIGN_HINTS = {"UserPromptSubmit": "conversation_title", "SessionStart": "System name for file paths"}
# Oldest Python the hooks run with (zoneinfo arrived in 3.9).
MINIMUM_HOOK_PYTHON = (3, 9)


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers: paths, commands, backups
# ---------------------------------------------------------------------------------------------------------------------

def home_folder():
    """Return the user's home folder ($HOME), e.g. "/home/alex"."""
    return os.path.expanduser("~")


def default_settings_file():
    """Return Claude Code's user settings file: $CLAUDE_CONFIG_DIR/settings.json, else ~/.claude/settings.json."""
    claude_folder = os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or os.path.join(home_folder(), ".claude")
    return os.path.join(os.path.expanduser(claude_folder), "settings.json")


def command_for(script_path):
    """Return the hook command for a script, written as the user would type it.

    Inside the home folder and with only ordinary characters: "python3 ~/.local/share/.../x.py" (Claude Code runs
    hook commands through a shell, which expands ~). Otherwise the absolute path, quoted for the shell by
    `shlex.quote` when needed (for example a folder name with a space).
    """
    home_prefix = home_folder().rstrip("/") + "/"
    if script_path.startswith(home_prefix):
        relative_part = script_path[len(home_prefix):]
        # `shlex.quote` returns the text unchanged when it needs no quoting; then the ~ form is safe.
        if shlex.quote(relative_part) == relative_part:
            return "python3 ~/" + relative_part
    return "python3 " + shlex.quote(script_path)


def normalized_command(command_text):
    """Return a hook command in one comparable form: quotes removed, ~ / $HOME / ${HOME} replaced by the home folder.

    Examples (home /home/alex): 'python3 "$HOME/.local/x.py"' -> "python3 /home/alex/.local/x.py";
    "python3 ~/.local/x.py" -> "python3 /home/alex/.local/x.py". Used to recognise this installer's entries however
    they were written.
    """
    text = str(command_text or "").strip()
    text = text.replace('"', "").replace("'", "")
    text = text.replace("${HOME}", home_folder()).replace("$HOME", home_folder())
    text = text.replace(" ~/", " " + home_folder().rstrip("/") + "/")
    # `" ".join(text.split())` turns every run of spaces into one space.
    return " ".join(text.split())


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
    """Write `text` to `path` through a temporary file and a rename, with permission bits `mode` (e.g. 0o600)."""
    temporary_path = path + ".partial-workhub-claude"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary_path, mode)
    # `os.replace` swaps the file in one step: settings.json is never seen half-written.
    os.replace(temporary_path, path)


def check_hook_python():
    """Return (ok, message) about the python3 that Claude Code will find on PATH to run the hooks."""
    python_path = shutil.which("python3")
    if not python_path:
        return False, "python3 is not on PATH; the hooks are started as `python3 ...`."
    # Ask that interpreter for its version and whether zoneinfo imports (a list of arguments, no shell involved).
    probe = "import sys, zoneinfo; print('%d.%d' % sys.version_info[:2])"
    try:
        result = subprocess.run([python_path, "-c", probe], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, python_path + " did not answer within 30 s."
    if result.returncode != 0:
        return False, python_path + " cannot import zoneinfo (Python >= 3.9 needed)."
    version_text = result.stdout.strip()
    # "3.10" -> (3, 10): split at the dot and turn both parts into whole numbers for a correct comparison.
    major_text, minor_text = version_text.split(".")
    version = (int(major_text), int(minor_text))
    if version < MINIMUM_HOOK_PYTHON:
        return False, python_path + " is Python " + version_text + "; the hooks need >= 3.9."
    return True, python_path + " (Python " + version_text + ")"


# ---------------------------------------------------------------------------------------------------------------------
# settings.json: read, change, compare
# ---------------------------------------------------------------------------------------------------------------------

def read_settings(settings_path):
    """Return (settings dict, original text, indent) of settings.json; ({}, "", 2) when the file does not exist.

    Raises ValueError when the file is not valid JSON or not a JSON object: the installer then stops without
    changing anything (a file it cannot read is never rewritten).
    """
    if not os.path.exists(settings_path):
        return {}, "", 2
    with open(settings_path, encoding="utf-8") as handle:
        original_text = handle.read()
    if not original_text.strip():
        return {}, original_text, 2
    loaded = json.loads(original_text)
    if not isinstance(loaded, dict):
        raise ValueError("it holds " + type(loaded).__name__ + ", not a JSON object {...}")
    # The indentation of the first indented line ("  \"model\": ..." -> 2), so the rewritten file keeps the style.
    indent = 2
    for line in original_text.splitlines()[1:]:
        stripped = line.lstrip(" ")
        if stripped and len(stripped) < len(line):
            indent = len(line) - len(stripped)
            break
    return loaded, original_text, indent


def settings_text(settings, indent):
    """Return settings as JSON text with the given indentation and a final line break."""
    return json.dumps(settings, indent=indent, ensure_ascii=False) + "\n"


def is_our_entry(entry, our_commands):
    """True when a hook entry ({"type": "command", "command": ...}) runs one of this installer's scripts."""
    if not isinstance(entry, dict):
        return False
    return normalized_command(entry.get("command")) in our_commands


def remove_our_entries(settings, our_commands):
    """Return a copy of settings without this installer's hook entries.

    A group ({"matcher": ..., "hooks": [...]}) emptied by the removal is dropped, an event list emptied that way is
    dropped, and a "hooks" object emptied that way is dropped; anything that was already empty stays as it was.
    """
    result = copy.deepcopy(settings)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event_name in list(hooks.keys()):
        groups = hooks[event_name]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        removed_any = False
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept_entries = [entry for entry in entries if not is_our_entry(entry, our_commands)]
            if len(kept_entries) == len(entries):
                kept_groups.append(group)
                continue
            removed_any = True
            if kept_entries:
                group["hooks"] = kept_entries
                kept_groups.append(group)
        if removed_any and not kept_groups:
            del hooks[event_name]
        else:
            hooks[event_name] = kept_groups
    if not hooks and "hooks" in settings and settings["hooks"]:
        del result["hooks"]
    return result


def comparable_settings(settings, our_commands):
    """Return settings without this installer's entries and without ANY empty hook group, event list or "hooks" object.

    Used only for the safety check in main(): two settings that differ only in this installer's entries (or in empty
    containers, which mean nothing to Claude Code) give the same result. Example: {"hooks": {"SessionStart": []}}
    and {} both give {}.
    """
    result = copy.deepcopy(settings)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event_name in list(hooks.keys()):
        groups = hooks[event_name]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(entries, list):
                group["hooks"] = [entry for entry in entries if not is_our_entry(entry, our_commands)]
                if not group["hooks"]:
                    continue
            kept_groups.append(group)
        if kept_groups:
            hooks[event_name] = kept_groups
        else:
            del hooks[event_name]
    if not hooks:
        del result["hooks"]
    return result


def registered_commands(settings, event_name):
    """Return the list of command texts registered for one hook event (all groups)."""
    commands = []
    groups = settings.get("hooks", {}).get(event_name, []) if isinstance(settings.get("hooks"), dict) else []
    for group in groups if isinstance(groups, list) else []:
        entries = group.get("hooks") if isinstance(group, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("command"):
                commands.append(str(entry["command"]))
    return commands


def add_our_entries(settings, wanted):
    """Return (new settings, list of what was added) with each wanted entry added once.

    `wanted` = list of (event name, command text, timeout). An entry is added as its own group
    {"hooks": [{"type": "command", "command": ..., "timeout": ...}]} at the end of the event's list, the form
    Claude Code itself writes. An entry already present (in any spelling, see normalized_command) is not added.
    """
    result = copy.deepcopy(settings)
    added = []
    for event_name, command_text, timeout_seconds in wanted:
        present = [normalized_command(text) for text in registered_commands(result, event_name)]
        if normalized_command(command_text) in present:
            continue
        # `setdefault(key, value)` returns the existing value, or stores `value` first when the key is missing.
        hooks = result.setdefault("hooks", {})
        groups = hooks.setdefault(event_name, [])
        groups.append({"hooks": [{"type": "command", "command": command_text, "timeout": timeout_seconds}]})
        added.append((event_name, command_text, timeout_seconds))
    return result, added


# ---------------------------------------------------------------------------------------------------------------------
# Plans for the hook files
# ---------------------------------------------------------------------------------------------------------------------

def plan_file_copies(target_dir):
    """Return a list of (file name, action) for installing: action is "copy", "same" or "differs"."""
    plan = []
    for file_name in HOOK_FILES:
        source = os.path.join(KIT_HOOKS_FOLDER, file_name)
        target = os.path.join(target_dir, file_name)
        if not os.path.exists(target):
            plan.append((file_name, "copy"))
        elif filecmp.cmp(source, target, shallow=False):
            # shallow=False compares the bytes, not only size and modification time.
            plan.append((file_name, "same"))
        else:
            plan.append((file_name, "differs"))
    return plan


def plan_file_removals(target_dir):
    """Return a list of (file name, action) for uninstalling: "remove", "keep-differs" or "absent"."""
    plan = []
    for file_name in HOOK_FILES:
        source = os.path.join(KIT_HOOKS_FOLDER, file_name)
        target = os.path.join(target_dir, file_name)
        if not os.path.exists(target):
            plan.append((file_name, "absent"))
        elif filecmp.cmp(source, target, shallow=False):
            plan.append((file_name, "remove"))
        else:
            plan.append((file_name, "keep-differs"))
    return plan


# ---------------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------------

def parse_options():
    """Read the command-line options (see the header)."""
    parser = argparse.ArgumentParser(
        description="Install or remove the workhub-claude conversation hooks (dry run unless --apply).")
    parser.add_argument("--apply", action="store_true", help="do it (default: print the plan only)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only (the default)")
    # Two spellings of one option: `dest="uninstall"` stores both in options.uninstall.
    parser.add_argument("--uninstall", "--remove", dest="uninstall", action="store_true",
                        help="remove the entries and the installed files (--remove is the same option)")
    parser.add_argument("--check", action="store_true",
                        help="exit 0 when the hooks are installed and current, else 1; writes nothing")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace installed hook files that differ from this kit's copies (backup first)")
    parser.add_argument("--target-dir", default=DEFAULT_TARGET_DIR, help="default " + DEFAULT_TARGET_DIR)
    parser.add_argument("--settings-file", default="", help="default ~/.claude/settings.json")
    options = parser.parse_args()
    if options.apply and options.dry_run:
        parser.error("--apply and --dry-run exclude each other")
    if options.check and (options.apply or options.uninstall):
        parser.error("--check only reports; it cannot be combined with --apply or --uninstall/--remove")
    return options


def report_check(target_dir, settings_path, old_settings, wanted):
    """--check: print whether every hook file is installed unchanged and both entries are registered.

    Returns the exit code: 0 = installed and current, 1 = something is missing or out of date (listed).
    """
    problems = []
    for file_name, action in plan_file_copies(target_dir):
        if action == "copy":
            problems.append(file_name + " is not installed in " + target_dir)
        elif action == "differs":
            problems.append(file_name + " in " + target_dir + " differs from this kit's copy")
    for event_name, command_text, _ in wanted:
        present = [normalized_command(text) for text in registered_commands(old_settings, event_name)]
        if normalized_command(command_text) not in present:
            problems.append(event_name + " entry missing in " + settings_path + ": " + command_text)
    if problems:
        for problem in problems:
            print("  NOT CURRENT: " + problem)
        print("check: the hooks are not installed or not current (run with --apply, or --apply --overwrite).")
        return 1
    print("check: the hooks are installed and current.")
    return 0


def main():
    """Make the plan, print it, and carry it out with --apply."""
    options = parse_options()
    target_dir = os.path.abspath(os.path.expanduser(options.target_dir))
    # realpath: when settings.json is a symbolic link (dotfiles kept elsewhere), the real file is changed, and the
    # link stays a link.
    settings_path = os.path.realpath(os.path.expanduser(options.settings_file or default_settings_file()))
    mode_word = "APPLY" if options.apply else "DRY RUN (nothing is written; add --apply to do it)"
    if options.check:
        mode_word = "CHECK (nothing is written)"
    action_word = "uninstall" if options.uninstall else "install"
    print(f"install_hooks.py {action_word}: {mode_word}")

    # ---- 1. The kit's files must all be here -------------------------------------------------------------------------
    for file_name in HOOK_FILES:
        if not os.path.isfile(os.path.join(KIT_HOOKS_FOLDER, file_name)):
            print("ERROR: kit file missing: " + os.path.join(KIT_HOOKS_FOLDER, file_name))
            return 2

    # ---- 2. Read settings.json (stop when it cannot be parsed) ------------------------------------------------------
    try:
        old_settings, old_text, indent = read_settings(settings_path)
    except ValueError as error:
        print(f"ERROR: {settings_path} is not valid JSON ({error}). Nothing was changed; fix the file first.")
        return 2

    # ---- 3. Our entries, in every spelling that counts as ours ---------------------------------------------------
    # `wanted` = the entries to have; `our_commands` = their normalized forms, used to recognise them.
    wanted = []
    for event_name, script_name, timeout_seconds in REGISTRATIONS:
        script_path = os.path.join(target_dir, script_name)
        wanted.append((event_name, command_for(script_path), timeout_seconds))
    our_commands = {normalized_command(command_text) for _, command_text, _ in wanted}
    # --check stops here: it only compares and reports.
    if options.check:
        return report_check(target_dir, settings_path, old_settings, wanted)

    # ---- 4. The new settings and the file plan ------------------------------------------------------------------
    if options.uninstall:
        new_settings = remove_our_entries(old_settings, our_commands)
        file_plan = plan_file_removals(target_dir)
    else:
        python_ok, python_message = check_hook_python()
        print("python3 for the hooks: " + python_message)
        if not python_ok:
            return 2
        new_settings, _ = add_our_entries(old_settings, wanted)
        file_plan = plan_file_copies(target_dir)
    # SAFETY CHECK: apart from this installer's entries, the settings must be exactly what they were.
    if comparable_settings(new_settings, our_commands) != comparable_settings(old_settings, our_commands):
        print("ERROR: internal check failed (the change would touch other settings). Nothing was changed.")
        return 2

    # ---- 5. Print the plan --------------------------------------------------------------------------------------------
    print(f"\nHook files in {target_dir}:")
    differs_label = ("differs from this kit's copy: replace (backup first)" if options.overwrite
                     else "DIFFERS from this kit's copy: replaced only with --overwrite (backup first)")
    labels = {"copy": "copy (new)", "same": "already installed (identical)",
              "differs": differs_label,
              "remove": "remove (identical to this kit's copy)",
              "keep-differs": "kept: differs from this kit's copy (edited, or another version); delete by hand",
              "absent": "not installed"}
    for file_name, action in file_plan:
        print(f"  {file_name:<32} {labels[action]}")
    print(f"\nClaude Code settings {settings_path}:")
    for event_name, command_text, timeout_seconds in wanted:
        present = normalized_command(command_text) in [normalized_command(text) for text in
                                                         registered_commands(old_settings, event_name)]
        if options.uninstall:
            state_word = "remove" if present else "not registered"
        else:
            state_word = "already registered" if present else "add"
        print(f"  {event_name:<17} {command_text}  (timeout {timeout_seconds}): {state_word}")
    # Other hooks that look like naming or system-name hooks: report only (two naming hooks would fight).
    for event_name, hint in FOREIGN_HINTS.items():
        for text in registered_commands(old_settings, event_name):
            if hint in text and normalized_command(text) not in our_commands:
                print(f"  NOTE: another {event_name} hook looks similar and stays as it is: {text}")
    settings_change = new_settings != old_settings
    new_text = settings_text(new_settings, indent) if settings_change else old_text
    if settings_change:
        if old_text and settings_text(old_settings, indent) != old_text:
            print("  NOTE: the file will be re-written in standard JSON layout (same content apart from the diff).")
        diff_lines = difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                          fromfile=settings_path + " (now)", tofile=settings_path + " (after)")
        print("".join(diff_lines))
    else:
        print("  settings.json: no change needed.")
    if not options.uninstall:
        # Import here (not at the top): only this message needs the settings reader.
        sys.path.insert(0, KIT_HOOKS_FOLDER)
        import workhub_config
        if not os.path.isfile(workhub_config.config_path()):
            print("NOTE: " + workhub_config.config_path() + " does not exist yet: the hooks stay silent until the "
                  "work-hub-setup skill writes it.")

    # ---- 6. Refuse a silent overwrite --------------------------------------------------------------------------------
    differing = [file_name for file_name, action in file_plan if action == "differs"]
    if differing and not options.overwrite:
        print("REFUSED: " + ", ".join(differing) + " differ(s) from this kit's copy. Re-run with --overwrite to "
              "replace them (a backup copy is kept). Nothing was changed.")
        return 1
    if not options.apply:
        return 0

    # ---- 7. Apply: files first, then settings.json --------------------------------------------------------------
    if options.uninstall:
        for file_name, action in file_plan:
            if action == "remove":
                os.remove(os.path.join(target_dir, file_name))
                print("removed " + os.path.join(target_dir, file_name))
        # Remove the folder only when it is empty now (os.rmdir refuses a folder that still holds anything).
        if os.path.isdir(target_dir) and not os.listdir(target_dir):
            os.rmdir(target_dir)
            print("removed empty folder " + target_dir)
    else:
        os.makedirs(target_dir, exist_ok=True)
        for file_name, action in file_plan:
            if action == "same":
                continue
            source = os.path.join(KIT_HOOKS_FOLDER, file_name)
            target = os.path.join(target_dir, file_name)
            if action == "differs":
                backup = backup_path_for(target)
                # copy2 keeps the modification time, so the backup shows when the old version was written.
                shutil.copy2(target, backup)
                print("backup " + backup)
            with open(source, encoding="utf-8") as handle:
                source_text = handle.read()
            # 0o755: readable and runnable by everyone, writable by the owner (ordinary for scripts).
            write_text_atomically(target, source_text, 0o755)
            print("copied " + target)
    if settings_change:
        if os.path.exists(settings_path):
            backup = backup_path_for(settings_path)
            shutil.copy2(settings_path, backup)
            print("backup " + backup)
            file_mode = os.stat(settings_path).st_mode & 0o777
        else:
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            file_mode = 0o600
        write_text_atomically(settings_path, new_text, file_mode)
        print("updated " + settings_path)
    if options.uninstall:
        print("done. The hooks no longer run in conversations started (or resumed) from now on.")
    else:
        print("done. Hooks take effect in conversations started (or resumed) from now on.")
    return 0


# Run main() only when executed as a script; its return value becomes the exit code.
if __name__ == "__main__":
    sys.exit(main())
