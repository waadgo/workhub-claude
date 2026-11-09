"""
test_cluster_login.py: tests for cluster-login and install_cluster_login.py / install_cluster_login.sh.

PURPOSE
    Everything runs against a temporary HOME and temporary ssh configuration files (never the real ~/.ssh/config),
    and nothing connects to any network:
      1. the installer's dry run (the default) writes nothing and shows the block it would write;
      2. --apply copies cluster-login; the ssh configuration changes only with --ssh-config, with a backup first;
         the block goes at the top, the user's own lines stay unchanged after it, and a second run changes nothing;
      3. `ssh -G` (which prints the settings ssh would use, without connecting) confirms the block: shared connection
         for the clusters, BatchMode yes + ProxyCommand false when CLAUDECODE is set, other hosts untouched, no
         sharing when the socket folder does not exist;
      4. clusters, user names, socket folder and install folder come from the settings file when no options are
         given (only when cluster_login.enabled is true); presets fill missing host names;
      5. bad input (unknown alias without host name, spaces in the socket folder, odd user names, a broken block)
         stops with exit code 2 and changes nothing;
      6. --remove --apply restores the original ssh configuration exactly; an installed cluster-login that differs
         from the kit's copy is never replaced without --overwrite and never removed;
      7. --check reports "not current" (exit 1) before and "current" (exit 0) after an install;
      8. cluster-login finds its cluster list in the arguments, the settings, or the block's "# aliases:" line,
         refuses to log in inside Claude Code, rejects aliases that look like ssh options, and reports connected /
         stuck / not connected (a stub `ssh` on PATH plays the part of a running master).

RUN
    python3 -m pytest -q tests/          (from the kit's tools/ folder; needs pytest; the ssh -G test needs OpenSSH)

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# glob: find backups. json: settings files. os, shutil, stat, sys: paths, modes, the python3 path. subprocess: run
# the tools as separate processes. pytest: skip the ssh -G test where OpenSSH is missing.
import glob
import json
import os
import shutil
import subprocess
import sys

import pytest

TOOLS_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER_SH = os.path.join(TOOLS_FOLDER, "install_cluster_login.sh")
INSTALLER_PY = os.path.join(TOOLS_FOLDER, "install_cluster_login.py")
CLUSTER_LOGIN = os.path.join(TOOLS_FOLDER, "cluster-login")
PYTHON = shutil.which("python3") or sys.executable
BLOCK_START = "# >>> workhub-claude cluster-login >>>"
BLOCK_END = "# <<< workhub-claude cluster-login <<<"
# The user's own ssh configuration in the tests: a global option first, then another host.
USER_SSH_CONFIG = "ServerAliveInterval 60\n\nHost ws02\n  User alex\n"


# ---------------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------------

def environment_for(home, settings=None):
    """Return an environment with HOME = the temporary folder and the settings file inside it (or none)."""
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    settings_path = os.path.join(str(home), "config.json")
    if settings is not None:
        with open(settings_path, "w", encoding="utf-8") as handle:
            json.dump(settings, handle)
    environment["WORKHUB_CLAUDE_CONFIG"] = settings_path
    for name in ("CLAUDECODE", "CLUSTER_LOGIN_SSH_CONFIG"):
        environment.pop(name, None)
    return environment


def ssh_config_path(home):
    """Return the temporary ~/.ssh/config path."""
    return os.path.join(str(home), ".ssh", "config")


def write_ssh_config(home, text=USER_SSH_CONFIG):
    """Write the temporary ~/.ssh/config (folder mode 700, file mode 600, as ssh expects)."""
    os.makedirs(os.path.join(str(home), ".ssh"), mode=0o700, exist_ok=True)
    with open(ssh_config_path(home), "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(ssh_config_path(home), 0o600)


def read_ssh_config(home):
    """Return the text of the temporary ~/.ssh/config."""
    with open(ssh_config_path(home), encoding="utf-8") as handle:
        return handle.read()


def socket_folder(home):
    """Create and return a private socket folder inside the temporary HOME's parent (mode 700)."""
    path = os.path.join(os.path.dirname(str(home)), "sock")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def run_installer(home, *arguments, settings=None, use_shell_entry=True):
    """Run the installer (through install_cluster_login.sh by default); return (exit code, all output)."""
    command = ["bash", INSTALLER_SH] if use_shell_entry else [PYTHON, INSTALLER_PY]
    result = subprocess.run(command + list(arguments), capture_output=True, text=True,
                            env=environment_for(home, settings))
    return result.returncode, result.stdout + result.stderr


def basic_options(home):
    """Return the options of a typical install without a settings file."""
    return ["--cluster", "rorqual", "--cluster", "narval", "--cluster", "mycl=login.mycl.org", "--user", "alex",
            "--socket-dir", socket_folder(home)]


def run_cluster_login(home, *arguments, settings=None, ssh_config=None, extra_environment=None):
    """Run cluster-login; return (exit code, all output)."""
    environment = environment_for(home, settings)
    if ssh_config:
        environment["CLUSTER_LOGIN_SSH_CONFIG"] = ssh_config
    environment.update(extra_environment or {})
    result = subprocess.run(["bash", CLUSTER_LOGIN] + list(arguments), capture_output=True, text=True,
                            env=environment, stdin=subprocess.DEVNULL)
    return result.returncode, result.stdout + result.stderr


def ssh_settings(config_path, host, claude=False):
    """Return the dict of settings `ssh -G` reports for `host` with the given configuration file (no connection)."""
    environment = dict(os.environ)
    environment.pop("CLAUDECODE", None)
    if claude:
        environment["CLAUDECODE"] = "1"
    result = subprocess.run(["ssh", "-F", config_path, "-G", host], capture_output=True, text=True,
                            env=environment, stdin=subprocess.DEVNULL, check=True)
    settings = {}
    for line in result.stdout.splitlines():
        name, _, value = line.partition(" ")
        settings[name] = value
    return settings


# ---------------------------------------------------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_shows_the_block(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    code, output = run_installer(home, *basic_options(home))
    assert code == 0 and "DRY RUN" in output and "+" + BLOCK_START in output
    assert "NOT written without --ssh-config" in output
    assert read_ssh_config(home) == USER_SSH_CONFIG
    assert not os.path.exists(home / ".local")


def test_apply_without_ssh_config_copies_only_the_command(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    code, output = run_installer(home, *basic_options(home), "--apply")
    assert code == 0, output
    installed = home / ".local" / "bin" / "cluster-login"
    assert installed.read_bytes() == open(CLUSTER_LOGIN, "rb").read()
    assert os.stat(installed).st_mode & 0o777 == 0o755
    assert read_ssh_config(home) == USER_SSH_CONFIG


def test_apply_with_ssh_config_puts_the_block_first_once(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    code, output = run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    assert code == 0, output
    text = read_ssh_config(home)
    # The block is first; the user's lines follow unchanged after one blank line.
    assert text.startswith(BLOCK_START + "\n")
    assert text.endswith(BLOCK_END + "\n\n" + USER_SSH_CONFIG)
    assert "# aliases: rorqual narval mycl\n" in text and "Match all\n" in text
    # The backup holds the old content; the file stays private.
    backups = glob.glob(ssh_config_path(home) + ".bak-workhub-claude-*")
    assert len(backups) == 1 and open(backups[0], encoding="utf-8").read() == USER_SSH_CONFIG
    assert os.stat(ssh_config_path(home)).st_mode & 0o022 == 0
    # Second run: nothing changes, no new backup.
    code, output = run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    assert code == 0 and "no change needed" in output
    assert read_ssh_config(home) == text
    assert len(glob.glob(ssh_config_path(home) + ".bak-workhub-claude-*")) == 1


@pytest.mark.skipif(shutil.which("ssh") is None, reason="OpenSSH is not installed")
def test_ssh_reads_the_block_as_intended(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    config = ssh_config_path(home)
    # The user's terminal: shared connection, keep-alive, user name.
    rorqual = ssh_settings(config, "rorqual")
    assert rorqual["hostname"] == "rorqual.alliancecan.ca" and rorqual["user"] == "alex"
    assert rorqual["controlmaster"] == "auto" and rorqual["controlpersist"] == "yes"
    assert rorqual["controlpath"].startswith(socket_folder(home) + "/ssh-")
    assert rorqual["serveraliveinterval"] == "30" and rorqual["batchmode"] == "no"
    # Inside Claude Code, also for the other name of a cluster: never prompt, never open a new login.
    narval = ssh_settings(config, "narval.computecanada.ca", claude=True)
    assert narval["hostname"] == "narval.alliancecan.ca"
    assert narval["batchmode"] == "yes" and narval["proxycommand"] == "false"
    # Another host is untouched and keeps the user's global option.
    other = ssh_settings(config, "ws02", claude=True)
    assert other["batchmode"] == "no" and other["controlmaster"] == "false"
    assert other["serveraliveinterval"] == "60" and "proxycommand" not in other
    # Without the socket folder on this machine: no sharing (the `test -O` guard).
    os.rename(socket_folder(home), socket_folder(home) + "_gone")
    assert ssh_settings(config, "rorqual")["controlmaster"] == "false"


def test_everything_from_the_settings_file(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    settings = {
        "machines": {"clusters": [
            {"name": "fir", "ssh_alias": "fir", "hostname": "", "user": "alex"},
            {"name": "lab", "ssh_alias": "labhpc", "hostname": "login.lab.example.org", "user": "a.user"},
        ]},
        "cluster_login": {"enabled": True, "socket_dir": socket_folder(home),
                          "install_dir": os.path.join(str(home), "bin")},
    }
    code, output = run_installer(home, "--apply", "--ssh-config", settings=settings, use_shell_entry=False)
    assert code == 0, output
    text = read_ssh_config(home)
    # The preset fills fir's empty host name; each cluster keeps its own user name.
    assert "Host fir fir.alliancecan.ca\n  HostName fir.alliancecan.ca\n  User alex\n" in text
    assert "Host labhpc login.lab.example.org\n  HostName login.lab.example.org\n  User a.user\n" in text
    assert (home / "bin" / "cluster-login").exists()
    # The same settings with cluster_login.enabled false: no clusters, so --ssh-config stops.
    settings["cluster_login"]["enabled"] = False
    code, output = run_installer(home, "--ssh-config", settings=settings)
    assert code == 2 and "no clusters" in output


def test_bad_input_stops_and_changes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    bad_runs = [
        ["--cluster", "unknowncluster", "--socket-dir", socket_folder(home)],
        ["--cluster", "rorqual", "--socket-dir", "/tmp/with space"],
        ["--cluster", "rorqual", "--user", "bad user", "--socket-dir", socket_folder(home)],
        ["--cluster", "-oProxyCommand=x=host.example.org", "--socket-dir", socket_folder(home)],
        ["--cluster", "rorqual", "--cluster", "rorqual", "--socket-dir", socket_folder(home)],
        ["--cluster", "rorqual", "--ssh-config"],
    ]
    for arguments in bad_runs:
        code, output = run_installer(home, *arguments, "--apply")
        assert code == 2, (arguments, output)
    assert read_ssh_config(home) == USER_SSH_CONFIG
    assert not os.path.exists(home / ".local")
    # A broken block (start marker without end marker) is never rewritten.
    write_ssh_config(home, BLOCK_START + "\nHost x\n")
    code, output = run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    assert code == 2 and "broken" in output
    assert read_ssh_config(home) == BLOCK_START + "\nHost x\n"


def test_remove_restores_the_original_exactly(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    code, output = run_installer(home, "--remove")
    assert code == 0 and "DRY RUN" in output and "cluster-login logout" in output
    code, output = run_installer(home, "--remove", "--apply")
    assert code == 0, output
    assert read_ssh_config(home) == USER_SSH_CONFIG
    assert not (home / ".local" / "bin" / "cluster-login").exists()


def test_a_different_installed_command_is_protected(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    own_tool = home / ".local" / "bin" / "cluster-login"
    own_tool.write_text("#!/bin/sh\necho my own tool\n", encoding="utf-8")
    code, output = run_installer(home, "--apply")
    assert code == 1 and "REFUSED" in output
    assert own_tool.read_text(encoding="utf-8") == "#!/bin/sh\necho my own tool\n"
    # --remove keeps it too.
    code, output = run_installer(home, "--remove", "--apply")
    assert code == 0 and own_tool.exists()
    # --overwrite replaces it after a backup.
    code, output = run_installer(home, "--apply", "--overwrite")
    assert code == 0, output
    saved = glob.glob(str(own_tool) + ".bak-workhub-claude-*")
    assert len(saved) == 1 and "my own tool" in open(saved[0], encoding="utf-8").read()
    assert own_tool.read_bytes() == open(CLUSTER_LOGIN, "rb").read()


def test_check_before_and_after(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    code, output = run_installer(home, *basic_options(home), "--check", "--ssh-config")
    assert code == 1 and "NOT CURRENT" in output
    run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    code, output = run_installer(home, *basic_options(home), "--check")
    assert code == 0 and "installed and current" in output


def test_symlinked_ssh_config_stays_a_link(tmp_path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True, mode=0o700)
    real_file = tmp_path / "dotfiles_ssh_config"
    real_file.write_text(USER_SSH_CONFIG, encoding="utf-8")
    os.chmod(real_file, 0o600)
    os.symlink(real_file, ssh_config_path(home))
    code, output = run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    assert code == 0, output
    assert os.path.islink(ssh_config_path(home))
    assert real_file.read_text(encoding="utf-8").startswith(BLOCK_START)


# ---------------------------------------------------------------------------------------------------------------------
# cluster-login
# ---------------------------------------------------------------------------------------------------------------------

def test_cluster_list_sources(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    write_ssh_config(home)
    run_installer(home, *basic_options(home), "--apply", "--ssh-config")
    config = ssh_config_path(home)
    # 3. No settings file: the block's "# aliases:" line.
    code, output = run_cluster_login(home, "status", ssh_config=config)
    assert code == 1
    assert [line.split()[0] for line in output.splitlines()] == ["rorqual", "narval", "mycl"]
    assert "not connected" in output
    # 2. The settings, when cluster_login.enabled is true.
    settings = {"cluster_login": {"enabled": True},
                "machines": {"clusters": [{"ssh_alias": "mycl"}, {"ssh_alias": "narval"}]}}
    code, output = run_cluster_login(home, "status", settings=settings, ssh_config=config)
    assert [line.split()[0] for line in output.splitlines()] == ["mycl", "narval"]
    # Settings with cluster_login.enabled false and no arguments: no list.
    settings["cluster_login"]["enabled"] = False
    code, output = run_cluster_login(home, "status", settings=settings, ssh_config=config)
    assert code == 2 and "cluster_login.enabled is not true" in output
    # 1. Arguments win, also without any settings file or block.
    other_home = tmp_path / "bare"
    other_home.mkdir()
    code, output = run_cluster_login(other_home, "logout", "foo", ssh_config=str(other_home / "none"))
    assert code == 0 and output.split() == ["foo", "was", "not", "connected"]
    code, output = run_cluster_login(other_home, "status", ssh_config=str(other_home / "none"))
    assert code == 2 and "no clusters" in output


def test_login_is_refused_inside_claude_and_bad_aliases_are_rejected(tmp_path):
    code, output = run_cluster_login(tmp_path, "mycl", extra_environment={"CLAUDECODE": "1"})
    assert code == 2 and "not from inside Claude Code" in output
    code, output = run_cluster_login(tmp_path, "status", "-oProxyCommand=x")
    assert code == 2 and "not a valid ssh alias" in output
    code, output = run_cluster_login(tmp_path, "--help")
    assert code == 0 and "cluster-login status" in output


def test_status_reports_connected_and_stuck_with_a_stub_ssh(tmp_path):
    # A stub `ssh` first on PATH: "-O check" answers like a running master (the pid of this test process, so `ps`
    # finds it); any other call (the "answers" check) succeeds, or fails when FAKE_STUCK is set.
    stub_folder = tmp_path / "stub"
    stub_folder.mkdir()
    stub = stub_folder / "ssh"
    stub.write_text("#!/usr/bin/env bash\n"
                    "for argument in \"$@\"; do\n"
                    "  if [ \"$argument\" = check ]; then echo \"Master running (pid=$FAKE_PID)\" >&2; exit 0; fi\n"
                    "done\n"
                    "if [ -n \"${FAKE_STUCK:-}\" ]; then exit 255; fi\n"
                    "exit 0\n", encoding="utf-8")
    os.chmod(stub, 0o755)
    path_value = str(stub_folder) + os.pathsep + os.environ.get("PATH", "")
    common = {"PATH": path_value, "FAKE_PID": str(os.getpid())}
    code, output = run_cluster_login(tmp_path, "status", "mycl", extra_environment=common)
    assert code == 0 and output.startswith("mycl") and "connected" in output and "not connected" not in output
    stuck = dict(common, FAKE_STUCK="1")
    code, output = run_cluster_login(tmp_path, "status", "mycl", extra_environment=stuck)
    assert code == 1 and "stuck (open but not answering)" in output
