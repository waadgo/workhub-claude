"""Tests for scripts/collect_conversations.py (sites from the settings, open and recent conversations).

PURPOSE
    Check, on synthetic data only, that the collector:
      - builds the sites from a temporary settings file (home site, own-home workstations, clusters);
      - finds an open conversation (a fake registry entry for THIS test process, which is alive) and
        ignores a dead one;
      - lists recent conversations, open or closed, and filters them with --match;
      - leaves other machines of a site alone when no shared SSH connection to them is open;
      - learns the machine-id of the home site's machines and keeps it in the state folder;
      - reports a site without an open shared connection as not reached, with a useful hint.
    No real transcript, registry, settings file or remote machine is used. The only commands run are
    `ssh -O check` and `ssh -G` on made-up host names, which never open a connection.

RUN
    python3 -m pytest -q <skill folder>/tests/test_collect_conversations.py

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# importlib.util: load the scripts by path (scripts/ is not a package). json: synthetic inputs.
# os: paths and environment. stat: file modes. subprocess/sys: run the script as a command. time: time stamps.
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time

# ---------------------------------------------------------------------------------------------------------------------
# Modules under test
# ---------------------------------------------------------------------------------------------------------------------
SCRIPTS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
COLLECTOR_PATH = os.path.join(SCRIPTS_FOLDER, "collect_conversations.py")


def load_module(name):
    """Load scripts/<name>.py as a module (its main() does not run)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_FOLDER, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load_module("collect_conversations")
wc = load_module("workhub_config")

# Session ids of the synthetic conversations (letters after "f", so they never look like real ids).
LIVE_ID = "testconv-0000-0000-0000-000000000001"
DEAD_ID = "testconv-0000-0000-0000-000000000002"
OLD_ID = "testconv-0000-0000-0000-000000000003"


# ---------------------------------------------------------------------------------------------------------------------
# Synthetic settings and Claude Code folder
# ---------------------------------------------------------------------------------------------------------------------

def settings_dict(tmp_path, cluster_alias="mycluster-test-noconn"):
    """A settings file with two home workstations, two own-home workstations and two clusters."""
    return {
        "owner": {"name": "Alex", "time_zone": "UTC"},
        "system_name": {"override_env": "CC_CLUSTER", "aliases": {}},
        "notion": {"task_id_prefix": "TK", "conversation_reports": {"enabled": False}},
        "workflow": {"conversation_names": True, "orchestrator": {"enabled": False}},
        "machines": {
            "workstations": [
                {"name": "ws01", "ssh_alias": "", "shares_home_with_this_machine": True},
                {"name": "ws02", "ssh_alias": "ws02-test-alias", "shares_home_with_this_machine": True},
                {"name": "ws03", "ssh_alias": "ws03-test-alias", "shares_home_with_this_machine": False},
                {"name": "ws04", "ssh_alias": "", "shares_home_with_this_machine": False},
            ],
            "clusters": [
                {"name": "mycluster", "ssh_alias": cluster_alias, "shares_home_with_this_machine": False},
                {"name": "othercluster", "ssh_alias": "", "shares_home_with_this_machine": False},
            ],
        },
        "cluster_login": {"enabled": True},
        "conversations": {"claude_projects_dir": os.path.join(str(tmp_path), "claude", "projects"),
                          "include_codex": False},
        "paths": {"state_dir": os.path.join(str(tmp_path), "state")},
        "skill_settings": {},
    }


def write_settings(tmp_path, settings):
    """Write the settings JSON and return its path."""
    path = os.path.join(str(tmp_path), "config.json")
    with open(path, "w", encoding="utf-8") as settings_file:
        json.dump(settings, settings_file)
    return path


def own_start_tick():
    """Field 22 of /proc/self/stat (the start tick of this process), as Claude Code saves it in procStart."""
    with open("/proc/%d/stat" % os.getpid()) as stat_file:
        fields_after_name = stat_file.read().rsplit(")", 1)[1].split()
    # Field 22 overall is index 19 after the name (fields 1 and 2 are the pid and the name).
    return fields_after_name[19]


def this_machine_id():
    """/etc/machine-id of this machine ("" when missing)."""
    try:
        with open("/etc/machine-id") as machine_id_file:
            return machine_id_file.read().strip()
    except OSError:
        return ""


def write_transcript(projects_folder, session_id, prompt, reply, title, minutes_ago):
    """One transcript: a custom title, one typed request and one reply, `minutes_ago` minutes old."""
    folder = os.path.join(projects_folder, "-home-alex-projects-demo")
    os.makedirs(folder, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - minutes_ago * 60))
    records = [
        {"type": "custom-title", "customTitle": title, "sessionId": session_id},
        {"type": "user", "timestamp": stamp, "cwd": "/home/alex/projects/demo",
         "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "timestamp": stamp, "cwd": "/home/alex/projects/demo",
         "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}},
    ]
    path = os.path.join(folder, session_id + ".jsonl")
    with open(path, "w", encoding="utf-8") as transcript_file:
        for record in records:
            # Compact separators, as Claude Code writes them: the collector tests for '"type":"user"'.
            transcript_file.write(json.dumps(record, separators=(",", ":")) + "\n")
    modified = time.time() - minutes_ago * 60
    os.utime(path, (modified, modified))
    return path


def build_claude_folder(tmp_path, dead_machine_id=None):
    """A fake ~/.claude: a live registry entry (this test process), a dead one, three transcripts."""
    claude = os.path.join(str(tmp_path), "claude")
    sessions = os.path.join(claude, "sessions")
    os.makedirs(sessions)
    machine_id = this_machine_id()
    live = {"pid": os.getpid(), "sessionId": LIVE_ID, "procStart": own_start_tick(),
            "pidDomain": "linux:%s:boot" % machine_id, "status": "idle",
            "cwd": "/home/alex/projects/demo", "name": "", "entrypoint": "cli"}
    # A process id that cannot exist (above the usual pid_max of 4,194,304), so the entry is dead.
    dead = {"pid": 4999999, "sessionId": DEAD_ID, "procStart": "1",
            "pidDomain": "linux:%s:boot" % (dead_machine_id or machine_id), "status": "idle",
            "cwd": "/home/alex/projects/demo"}
    for entry in (live, dead):
        with open(os.path.join(sessions, "%d.json" % entry["pid"]), "w") as entry_file:
            json.dump(entry, entry_file)
    projects = os.path.join(claude, "projects")
    write_transcript(projects, LIVE_ID, "Clean the survey table for Project A", "Step 2 of 3 is done.",
                     "Survey cleaning", 5)
    write_transcript(projects, DEAD_ID, "Draft the methods paragraph", "The draft is in methods.md.",
                     "Methods draft", 30)
    write_transcript(projects, OLD_ID, "Plot the pilot results", "The figure is saved.", "Pilot figure", 600)
    return claude


def home_site(claude, nodes="self"):
    """A home site dict as build_sites() makes it, pointing at the fake Claude folder."""
    return {"key": "home", "label": "Workstations", "kind": "home", "local": True, "ssh": "", "nodes": nodes,
            "node_ssh": {}, "system": "", "fallback_system": "ws01", "claude_dir": claude}


# ---------------------------------------------------------------------------------------------------------------------
# Sites from the settings
# ---------------------------------------------------------------------------------------------------------------------

def test_sites_from_a_workstation(tmp_path, monkeypatch):
    """Asked from ws01: home = this host + ws02 (shares home); then ws03 (own home, alias) and both clusters.
    ws04 (own home, no alias) cannot be reached and is no site."""
    monkeypatch.setenv("CC_CLUSTER", "ws01")
    config = settings_dict(tmp_path)
    sites = cc.build_sites(wc, config)
    assert [s["key"] for s in sites] == ["home", "ws03", "mycluster", "othercluster"]
    home = sites[0]
    assert home["label"] == "Workstations" and home["local"] is True
    assert home["nodes"] == [cc.short_host(), "ws02"]
    assert home["node_ssh"] == {"ws02": "ws02-test-alias"}
    assert home["system"] == "" and home["fallback_system"] == "ws01"
    assert home["claude_dir"] == os.path.join(str(tmp_path), "claude")
    assert sites[1]["ssh"] == "ws03-test-alias" and sites[1]["nodes"] == "self"
    assert sites[2]["ssh"] == "mycluster-test-noconn" and sites[2]["nodes"] == "auto"
    # A cluster without an alias is reached by its name.
    assert sites[3]["ssh"] == "othercluster"
    # An absolute Claude folder of this machine is not sent elsewhere: the remote side uses ~/.claude.
    assert sites[2]["claude_dir"] == "~/.claude"


def test_sites_from_a_cluster(tmp_path, monkeypatch):
    """Asked from mycluster: the home site is the cluster itself, its login nodes found automatically."""
    monkeypatch.setenv("CC_CLUSTER", "mycluster")
    config = settings_dict(tmp_path)
    config["skill_settings"] = {"active-conversations": {"home_site_label": "Lab machines"}}
    sites = cc.build_sites(wc, config)
    assert sites[0]["key"] == "mycluster" and sites[0]["nodes"] == "auto" and sites[0]["system"] == "mycluster"
    assert [s["key"] for s in sites[1:]] == ["ws03", "othercluster"]


def test_home_folder_setting_is_portable():
    """A Claude folder given from the home folder (~ or $HOME) is sent as is; an absolute one is not."""
    assert cc.portable_home_path("~/.claude", "~/.claude") == "~/.claude"
    assert cc.portable_home_path("$HOME/alt/.claude", "~/.claude") == "$HOME/alt/.claude"
    assert cc.portable_home_path("/fast/disk/claude", "~/.claude") == "~/.claude"


# ---------------------------------------------------------------------------------------------------------------------
# One site, here
# ---------------------------------------------------------------------------------------------------------------------

def test_open_conversations(tmp_path):
    """The live entry (this process) is open; the dead one on this machine counts as closed."""
    claude = build_claude_folder(tmp_path)
    result = cc.collect_site(home_site(claude), {"mode": "open"})
    assert result["reached"] is True
    assert [c["session_id"] for c in result["conversations"]] == [LIVE_ID]
    conversation = result["conversations"][0]
    assert conversation["title"] == "Survey cleaning"
    assert conversation["status"] == "idle"
    assert conversation["cwd"] == "/home/alex/projects/demo"
    assert conversation["processes"][0]["host"] == cc.short_host()
    assert conversation["system"] == cc.short_host()
    assert result["closed_entries"] == 1 and result["unverified"] == []
    # The machine that answered is remembered by its machine-id (for the cache).
    if this_machine_id():
        assert result["node_ids"] == {this_machine_id(): cc.short_host()}


def test_recent_conversations_and_match(tmp_path):
    """--last lists open and closed conversations, newest first; --match filters on the requests."""
    claude = build_claude_folder(tmp_path)
    result = cc.collect_site(home_site(claude), {"mode": "last", "last": 5})
    assert [c["session_id"] for c in result["conversations"]] == [LIVE_ID, DEAD_ID, OLD_ID]
    statuses = [c["status"] for c in result["conversations"]]
    assert statuses == ["idle", "closed", "closed"]
    # A closed conversation's machine is not known: it gets the site's fallback system name.
    assert result["conversations"][1]["system"] == "ws01"
    matched = cc.collect_site(home_site(claude), {"mode": "last", "last": 5, "match": "methods"})
    assert [c["session_id"] for c in matched["conversations"]] == [DEAD_ID]
    assert matched["matched"] == 1


def test_other_machines_need_an_open_shared_connection(tmp_path):
    """Without key_login, another machine of the site is not contacted when no shared connection is open:
    it is listed as skipped, and a recent dead entry becomes unverified instead of closed."""
    claude = build_claude_folder(tmp_path, dead_machine_id="otherotherotherotherotherotherot")
    site = home_site(claude, nodes=[cc.short_host(), "ws02"])
    site["node_ssh"] = {"ws02": "ws02-test-noconn.invalid"}
    result = cc.collect_site(site, {"mode": "open", "key_login": False})
    skipped = [n for n in result["nodes"] if n["skipped"]]
    assert [n["name"] for n in skipped] == ["ws02"]
    assert "no open shared SSH connection" in skipped[0]["error"]
    assert [u["session_id"] for u in result["unverified"]] == [DEAD_ID]
    # The live conversation on this machine is still found.
    assert [c["session_id"] for c in result["conversations"]] == [LIVE_ID]


def test_machine_id_cache_round_trip(tmp_path):
    """The cache is written with mode 600 and read back; a damaged file reads as empty."""
    path = os.path.join(str(tmp_path), "machine_ids.json")
    cc.write_machine_id_cache(path, {"home": {"abc": "ws02"}})
    assert cc.read_machine_id_cache(path) == {"home": {"abc": "ws02"}}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with open(path, "w") as damaged:
        damaged.write("{not json")
    assert cc.read_machine_id_cache(path) == {}


def test_reach_hints():
    """The hint names cluster-login when the kit's tool is installed, else a plain shared ssh connection."""
    site = {"site": "mycluster", "label": "mycluster", "ssh": "mycluster", "reason_code": "no_master",
            "shareable": True}
    assert "cluster-login mycluster" in cc.reach_hint(site, "ws01", True)
    assert "ssh -fN mycluster" in cc.reach_hint(site, "ws01", False)
    site["shareable"] = False
    assert "No shared SSH connection (ControlMaster)" in cc.reach_hint(site, "ws01", True)
    assert "Cannot be reached" in cc.reach_hint({"site": "x", "reason_code": "no_route"}, "ws01", True)


# ---------------------------------------------------------------------------------------------------------------------
# The command line, end to end
# ---------------------------------------------------------------------------------------------------------------------

def run_collector(tmp_path, arguments, settings):
    """Run collect_conversations.py with the settings in a temporary file; return the finished process."""
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=write_settings(tmp_path, settings), CC_CLUSTER="ws01")
    return subprocess.run([sys.executable, COLLECTOR_PATH] + arguments, env=environment,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120)


def test_command_line_end_to_end(tmp_path):
    """Open mode over the home site and one cluster without a connection: JSON written to the state folder,
    digest printed, machine-ids learned, the cluster reported as not reached with the cluster-login hint."""
    build_claude_folder(tmp_path)
    settings = settings_dict(tmp_path)
    # Only one remote site, and the home site without the second workstation, to keep the test short.
    settings["machines"]["workstations"] = settings["machines"]["workstations"][:1]
    settings["machines"]["clusters"] = settings["machines"]["clusters"][:1]
    completed = run_collector(tmp_path, [], settings)
    assert completed.returncode == 0, completed.stderr
    output = os.path.join(str(tmp_path), "state", "active-conversations", "conversations.json")
    with open(output, encoding="utf-8") as output_file:
        data = json.load(output_file)
    assert data["schema"] == 2 and data["timezone"] == "UTC"
    assert data["requested_from"]["system"] == "ws01" and data["requested_from"]["site"] == "home"
    assert data["site_labels"] == ["Workstations", "mycluster"]
    home, cluster = data["sites"]
    assert [c["session_id"] for c in home["conversations"]] == [LIVE_ID]
    assert cluster["reached"] is False and cluster["reason_code"] == "no_master"
    assert '"Survey cleaning"' in completed.stdout
    assert "mycluster: not reached" in completed.stdout
    if cluster["shareable"]:
        assert "cluster-login mycluster-test-noconn" in completed.stdout
    if this_machine_id():
        cache_path = os.path.join(str(tmp_path), "state", "active-conversations", "machine_ids.json")
        assert cc.read_machine_id_cache(cache_path) == {"home": {this_machine_id(): cc.short_host()}}


def test_command_line_last_and_list_sites(tmp_path):
    """--last 2 keeps the two newest across sites; --list-sites prints the sites; --sites limits them."""
    build_claude_folder(tmp_path)
    settings = settings_dict(tmp_path)
    out = os.path.join(str(tmp_path), "c.json")
    completed = run_collector(tmp_path, ["--last", "2", "--sites", "home", "-o", out, "--quiet"], settings)
    assert completed.returncode == 0, completed.stderr
    with open(out, encoding="utf-8") as output_file:
        data = json.load(output_file)
    assert [s["site"] for s in data["sites"]] == ["home"]
    assert [c["session_id"] for c in data["sites"][0]["conversations"]] == [LIVE_ID, DEAD_ID]
    listed = run_collector(tmp_path, ["--list-sites"], settings)
    assert [s["key"] for s in json.loads(listed.stdout)] == ["home", "ws03", "mycluster", "othercluster"]
    unknown = run_collector(tmp_path, ["--sites", "nowhere"], settings)
    assert unknown.returncode == 2 and "unknown site key" in unknown.stderr


def test_command_line_without_settings(tmp_path):
    """No settings file: exit status 2 and the hint to run the setup skill."""
    environment = dict(os.environ, WORKHUB_CLAUDE_CONFIG=os.path.join(str(tmp_path), "absent.json"))
    completed = subprocess.run([sys.executable, COLLECTOR_PATH], env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    assert completed.returncode == 2
    assert "set up my work hub" in completed.stderr
