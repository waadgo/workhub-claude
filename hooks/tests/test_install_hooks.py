"""
test_install_hooks.py: tests for install_hooks.py, always against a temporary HOME (never the real ~/.claude).

PURPOSE
    Check that the installer:
      1. prints a plan and writes nothing without --apply (the default dry run);
      2. with --apply copies the four hook files, adds exactly the two entries (UserPromptSubmit, SessionStart) to
         settings.json, keeps every other setting as it was, and keeps a backup of the old settings.json;
      3. is idempotent: a second --apply changes nothing and makes no new backup;
      4. recognises its entry written as "$HOME/..." and does not add it twice;
      5. refuses to replace an installed file that differs from the kit's copy unless --overwrite (backup first);
      6. refuses a settings.json that is not valid JSON, changing nothing;
      7. --uninstall --apply restores the old settings.json content and removes the installed files;
      8. the installed copy works on its own (the hook names a fake conversation from the install folder).

RUN
    python3 -m pytest -q tests/          (from the kit's hooks/ folder; needs pytest)

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# glob: find backup files. json: settings files and hook events. os, shutil, sys: paths and the python3 path.
# subprocess: run the installer and the installed hook as separate processes.
import glob
import json
import os
import shutil
import subprocess
import sys

HOOKS_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(HOOKS_FOLDER, "install_hooks.py")
PYTHON = shutil.which("python3") or sys.executable
# The commands the installer registers, as written in settings.json (home folder written as ~).
TITLE_COMMAND = "python3 ~/.local/share/workhub-claude/hooks/conversation_title.py"
START_COMMAND = "python3 ~/.local/share/workhub-claude/hooks/session_start_system_name.py"
INSTALLED_FILES = ["conversation_title.py", "set_conversation_task.py", "session_start_system_name.py",
                   "workhub_config.py"]
# A settings.json with other settings and another hook, which must all survive untouched.
USER_SETTINGS = {
    "model": "example-model",
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ~/my_other_hook.py", "timeout": 5}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash ~/gate.sh"}]}],
    },
}


def environment_for(home):
    """Return an environment whose HOME is the temporary folder and without the real Claude folder variable."""
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment.pop("CLAUDE_CONFIG_DIR", None)
    environment["WORKHUB_CLAUDE_CONFIG"] = os.path.join(str(home), ".config", "workhub-claude", "config.json")
    return environment


def settings_path(home):
    """Return the fake ~/.claude/settings.json path."""
    return os.path.join(str(home), ".claude", "settings.json")


def target_folder(home):
    """Return the fake ~/.local/share/workhub-claude/hooks path."""
    return os.path.join(str(home), ".local", "share", "workhub-claude", "hooks")


def write_user_settings(home, settings=None, text=None):
    """Write the fake settings.json (a dict, or raw text) and return its text."""
    os.makedirs(os.path.join(str(home), ".claude"), exist_ok=True)
    content = text if text is not None else json.dumps(settings if settings is not None else USER_SETTINGS,
                                                       indent=2) + "\n"
    with open(settings_path(home), "w", encoding="utf-8") as handle:
        handle.write(content)
    return content


def read_user_settings(home):
    """Return the fake settings.json as a dict."""
    with open(settings_path(home), encoding="utf-8") as handle:
        return json.load(handle)


def run_installer(home, *arguments):
    """Run install_hooks.py; return (exit code, standard output)."""
    result = subprocess.run([PYTHON, INSTALLER, *arguments], capture_output=True, text=True,
                            env=environment_for(home))
    return result.returncode, result.stdout + result.stderr


def commands_of(settings, event_name):
    """Return the list of command texts registered for one hook event."""
    commands = []
    for group in settings.get("hooks", {}).get(event_name, []):
        for entry in group.get("hooks", []):
            commands.append(entry.get("command"))
    return commands


def backups(home):
    """Return the backup copies of the fake settings.json."""
    return sorted(glob.glob(settings_path(home) + ".bak-workhub-claude-*"))


def test_dry_run_is_the_default_and_writes_nothing(tmp_path):
    original_text = write_user_settings(tmp_path)
    code, output = run_installer(tmp_path)
    assert code == 0 and "DRY RUN" in output and ": add" in output
    # The diff shows the new entry; the file and the target folder are unchanged / absent.
    assert TITLE_COMMAND in output
    with open(settings_path(tmp_path), encoding="utf-8") as handle:
        assert handle.read() == original_text
    assert not os.path.exists(target_folder(tmp_path))


def test_apply_installs_once_and_keeps_everything_else(tmp_path):
    write_user_settings(tmp_path)
    code, output = run_installer(tmp_path, "--apply")
    assert code == 0, output
    settings = read_user_settings(tmp_path)
    # Exactly one entry of each, plus the user's own hook, in its original place (first).
    assert commands_of(settings, "UserPromptSubmit") == ["python3 ~/my_other_hook.py", TITLE_COMMAND]
    assert commands_of(settings, "SessionStart") == [START_COMMAND]
    # Every other setting is as it was.
    assert settings["model"] == USER_SETTINGS["model"]
    assert settings["permissions"] == USER_SETTINGS["permissions"]
    assert settings["hooks"]["PreToolUse"] == USER_SETTINGS["hooks"]["PreToolUse"]
    # The files are copied, byte for byte.
    for file_name in INSTALLED_FILES:
        with open(os.path.join(HOOKS_FOLDER, file_name), "rb") as source, \
                open(os.path.join(target_folder(tmp_path), file_name), "rb") as target:
            assert source.read() == target.read()
    # One backup, holding the old content.
    assert len(backups(tmp_path)) == 1
    with open(backups(tmp_path)[0], encoding="utf-8") as handle:
        assert json.load(handle) == USER_SETTINGS
    # Second run: nothing to do, no new backup, same settings.
    code, output = run_installer(tmp_path, "--apply")
    assert code == 0 and "no change needed" in output
    assert len(backups(tmp_path)) == 1 and read_user_settings(tmp_path) == settings


def test_entry_written_with_home_variable_is_not_added_twice(tmp_path):
    settings = json.loads(json.dumps(USER_SETTINGS))
    settings["hooks"]["UserPromptSubmit"].append(
        {"hooks": [{"type": "command",
                    "command": 'python3 "$HOME/.local/share/workhub-claude/hooks/conversation_title.py"'}]})
    write_user_settings(tmp_path, settings)
    code, output = run_installer(tmp_path, "--apply")
    assert code == 0, output
    after = read_user_settings(tmp_path)
    assert len(commands_of(after, "UserPromptSubmit")) == 2
    assert commands_of(after, "SessionStart") == [START_COMMAND]


def test_differing_installed_file_needs_overwrite(tmp_path):
    write_user_settings(tmp_path)
    run_installer(tmp_path, "--apply")
    edited = os.path.join(target_folder(tmp_path), "conversation_title.py")
    with open(edited, "a", encoding="utf-8") as handle:
        handle.write("# edited by hand\n")
    code, output = run_installer(tmp_path, "--apply")
    assert code == 1 and "REFUSED" in output
    code, output = run_installer(tmp_path, "--apply", "--overwrite")
    assert code == 0, output
    # The edited copy was saved before it was replaced.
    saved = glob.glob(edited + ".bak-workhub-claude-*")
    assert len(saved) == 1
    with open(saved[0], encoding="utf-8") as handle:
        assert handle.read().endswith("# edited by hand\n")
    with open(edited, "rb") as installed, open(os.path.join(HOOKS_FOLDER, "conversation_title.py"), "rb") as kit:
        assert installed.read() == kit.read()


def test_invalid_settings_json_is_never_rewritten(tmp_path):
    original_text = write_user_settings(tmp_path, text='{"model": "x",, }')
    code, output = run_installer(tmp_path, "--apply")
    assert code == 2 and "not valid JSON" in output
    with open(settings_path(tmp_path), encoding="utf-8") as handle:
        assert handle.read() == original_text
    assert not os.path.exists(target_folder(tmp_path))


def test_missing_settings_json_is_created(tmp_path):
    code, output = run_installer(tmp_path, "--apply")
    assert code == 0, output
    settings = read_user_settings(tmp_path)
    assert commands_of(settings, "UserPromptSubmit") == [TITLE_COMMAND]
    assert backups(tmp_path) == []


def test_uninstall_restores_the_old_settings_and_removes_the_files(tmp_path):
    write_user_settings(tmp_path)
    run_installer(tmp_path, "--apply")
    # Dry run of the removal first: nothing changes.
    code, output = run_installer(tmp_path, "--uninstall")
    assert code == 0 and "DRY RUN" in output and "remove" in output
    assert os.path.isdir(target_folder(tmp_path))
    code, output = run_installer(tmp_path, "--uninstall", "--apply")
    assert code == 0, output
    assert read_user_settings(tmp_path) == USER_SETTINGS
    assert not os.path.exists(target_folder(tmp_path))
    # Installed into an empty settings.json and removed again: back to the same content.
    other_home = tmp_path / "second"
    other_home.mkdir()
    write_user_settings(other_home, {"theme": "dark"})
    run_installer(other_home, "--apply")
    run_installer(other_home, "--uninstall", "--apply")
    assert read_user_settings(other_home) == {"theme": "dark"}


def test_check_and_remove_spelling(tmp_path):
    write_user_settings(tmp_path)
    # Not installed yet: --check says so with exit code 1 and writes nothing.
    code, output = run_installer(tmp_path, "--check")
    assert code == 1 and "NOT CURRENT" in output and not os.path.exists(target_folder(tmp_path))
    run_installer(tmp_path, "--apply")
    code, output = run_installer(tmp_path, "--check")
    assert code == 0 and "installed and current" in output
    # An edited installed file is "not current".
    with open(os.path.join(target_folder(tmp_path), "workhub_config.py"), "a", encoding="utf-8") as handle:
        handle.write("# edited\n")
    code, output = run_installer(tmp_path, "--check")
    assert code == 1 and "differs" in output
    # --remove is the same option as --uninstall; the edited file is kept and listed.
    code, output = run_installer(tmp_path, "--remove", "--apply")
    assert code == 0 and "kept: differs" in output
    assert read_user_settings(tmp_path) == USER_SETTINGS
    assert os.listdir(target_folder(tmp_path)) == ["workhub_config.py"]
    # --check cannot be combined with --apply.
    code, _ = run_installer(tmp_path, "--check", "--apply")
    assert code == 2


def test_installed_hook_works_from_the_install_folder(tmp_path):
    write_user_settings(tmp_path)
    run_installer(tmp_path, "--apply")
    # A settings file for the hook, with a fixed machine name.
    config_folder = os.path.join(str(tmp_path), ".config", "workhub-claude")
    os.makedirs(config_folder)
    with open(os.path.join(config_folder, "config.json"), "w", encoding="utf-8") as handle:
        json.dump({"owner": {"time_zone": "UTC"}, "system_name": {"aliases": {}}}, handle)
    environment = environment_for(tmp_path)
    environment["CC_CLUSTER"] = "ws01"
    hook = os.path.join(target_folder(tmp_path), "conversation_title.py")
    event = json.dumps({"session_id": "i1", "transcript_path": "/nonexistent.jsonl", "prompt": "Install check run"})
    first = subprocess.run([PYTHON, hook], input=event, capture_output=True, text=True, env=environment)
    second = subprocess.run([PYTHON, hook], input=event, capture_output=True, text=True, env=environment)
    assert first.returncode == 0 and first.stdout == ""
    title = json.loads(second.stdout)["hookSpecificOutput"]["sessionTitle"]
    assert title.endswith("][ws01][UC] Install check run")
    # The default state folder inside the temporary HOME was used, and no __pycache__ was written.
    assert os.path.isfile(os.path.join(str(tmp_path), ".local", "state", "workhub-claude", "conversation_titles",
                                       "i1.json"))
    assert not os.path.exists(os.path.join(target_folder(tmp_path), "__pycache__"))
