#!/usr/bin/env python3
"""
collect_conversations.py: find the Claude Code conversations of this machine's site and of every other site that can
be reached through an SSH connection that is already open: the ones open right now (default), or the most recent ones,
open or closed (--last N, --match TEXT, --since HOURS).

PURPOSE
    The active-conversations skill answers "which conversations are open, where, and what are they doing?". This
    script gathers the facts: for every conversation its name, state (working, background job, waiting, closed), the
    machine and app that has it open, its folder, start, last message, and a short digest of its recent turns. It
    prints the digest (the text Claude reads to write one-line descriptions) and writes the full facts as JSON;
    notion_report.py turns that JSON into a Notion report and triage_conversations.py into a warm / finished triage.

SITES (built from the workhub-claude settings by build_sites())
    A site is one transcript store: the machines that share one home folder, so one ~/.claude folder.
    1. The home site: this machine plus every entry of machines.workstations[] and machines.clusters[] whose
       shares_home_with_this_machine is true. Key "home"; label skill_settings.active-conversations.home_site_label
       (default "Workstations"). When this machine IS one of machines.clusters[] (its system name equals the
       cluster's name), the home site is that cluster instead: key and label = the cluster's name, and its other
       login nodes are found from the host name (see discover_nodes()).
    2. One site per workstation that has its own home folder (shares_home_with_this_machine false) AND an ssh_alias.
    3. One site per entry of machines.clusters[], reached through its ssh_alias (else its name).

HOW A CONVERSATION COUNTS AS OPEN
    Claude Code writes ~/.claude/sessions/<pid>.json for each running process: process id, session id, folder, start
    time, status (busy or idle). The file can stay behind when a process dies, so each entry is checked on the
    machines of its site and counts only if that process id is alive there with the same start tick (field 22 of
    /proc/<pid>/stat, saved as "procStart"). All the machines of a site share one home folder, so that one folder
    holds the entries of all of them.

RECENT CONVERSATIONS
    Every conversation keeps a transcript, ~/.claude/projects/<folder>/<session id>.jsonl, after it is closed. --last
    reads the newest transcripts of each site (their folder is in them), marks the ones that are open now, and keeps
    the N with the latest messages across all sites.

WHICH MACHINES ARE CONTACTED (never a password or two-factor prompt)
    - Another site: only through a shared SSH connection that is ALREADY open (`ssh -O check <alias>` succeeds, as
      opened by cluster-login or `ssh -fN <alias>` with ControlMaster), always with -o BatchMode=yes. A new login is
      never attempted. The script sends ITSELF over that connection (`ssh <alias> python3 -`), so nothing has to be
      installed on the other site.
    - Inside a site, another machine (a workstation that shares this home, another login node of a cluster) is needed
      only to tell whether a registry entry's process is still alive. By default it is contacted only when a shared
      connection to it is open too. With skill_settings.active-conversations.key_login_to_other_machines = true it is
      also contacted with `ssh -o BatchMode=yes` (SSH keys only, so still never a prompt). A machine that is not
      contacted makes the dead registry entries of the last --recent-hours "unverified" instead of "closed".

INPUTS
    - The settings file (~/.config/workhub-claude/config.json or $WORKHUB_CLAUDE_CONFIG), read by workhub_config.py
      in this folder. Keys used: owner.time_zone, system_name.*, machines.workstations[], machines.clusters[],
      conversations.claude_projects_dir, cluster_login.enabled, paths.state_dir,
      skill_settings.active-conversations.home_site_label / key_login_to_other_machines.
    - ~/.claude/sessions/*.json (running processes) and ~/.claude/projects/*/*.jsonl (transcripts) of each site.
    - <state_dir>/active-conversations/machine_ids.json: which machine-id belongs to which machine of the home site,
      learned on earlier runs (written by this script; delete it any time, it is rebuilt).

OUTPUTS
    - JSON with every fact (-o PATH; default <state_dir>/active-conversations/conversations.json), schema 2.
    - The digest on standard output (unless --quiet).
    - The machine-id cache above. Nothing else is written; no conversation or transcript is changed.

EXAMPLES
    python3 collect_conversations.py -o /tmp/me/conversations.json           # open now, every site
    python3 collect_conversations.py --last 10 -o c.json                     # the 10 newest, open or not
    python3 collect_conversations.py --match "dataset cleaning" --last 5 -o c.json
    python3 collect_conversations.py --sites home,mycluster -o c.json        # only these sites (keys)
    python3 collect_conversations.py --list-sites                            # the sites built from the settings
    python3 collect_conversations.py --digest c.json                         # print the digest of an earlier run

DEPENDENCIES
    Python standard library only. The part that runs on other machines (collect_site and CHECKER_SRC) works with
    Python >= 3.6; local times use zoneinfo (Python >= 3.9) and fall back to UTC, saying so.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports (standard library only, so that the script also runs on another machine as `ssh host python3 -`)
# ---------------------------------------------------------------------------------------------------------------------
# argparse: command line. base64: pack the site and options into one shell-safe word for the remote run. calendar:
# UTC time stamps -> seconds. collections: deque/OrderedDict for the transcript tail. concurrent.futures: check sites
# and machines at the same time. datetime: local clock times. glob: list registry and transcript files. json: every
# file format here. os, re, shlex, socket, subprocess, sys, time: paths, patterns, quoting, host name, ssh, exit codes.
import argparse
import base64
import calendar
import collections
import concurrent.futures
import datetime
import glob
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time

# ---------------------------------------------------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------------------------------------------------
# The skill's name: its key under skill_settings and its folder under the state folder.
SKILL_NAME = "active-conversations"

# ssh options for every remote call: BatchMode=yes forbids any password or two-factor question (a call that would
# need one fails at once instead); ConnectTimeout bounds a dead network; LogLevel=ERROR hides banners.
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]
# For machines inside a site: also accept a new host key once (the machines of one site are trusted together).
NODE_SSH = SSH + ["-o", "StrictHostKeyChecking=accept-new"]

# Default Claude Code folder (holds projects/ and sessions/) when the settings name none.
DEFAULT_CLAUDE_DIR = "~/.claude"

# Runs on each machine that is checked: here through exec(), elsewhere as `ssh host python3 - '<candidates>'`.
# It receives [[pid, procStart], ...] and answers which of these processes are alive on that machine, owned by this
# user, with the same start tick, plus details: the program file (to tell VS Code from a terminal), the tmux pane,
# the background shells Claude started, the folder and the start time.
CHECKER_SRC = r'''
import json, os, re, socket, subprocess, sys


def _read(path):
    # Whole file as text, "" when it cannot be read (a process that ended, a file of another user).
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def _link(path):
    # Target of a symbolic link such as /proc/<pid>/exe, "" when unreadable.
    try:
        return os.readlink(path)
    except Exception:
        return ""


def _cmdline(pid):
    # The command line of a process; its arguments are separated by NUL characters in /proc.
    return _read("/proc/%d/cmdline" % pid).replace("\0", " ").strip()


def check(cands):
    """cands: [[pid, procStart], ...]. Returns this machine's name and machine-id, and details of
    each candidate that is alive here, owned by this user, with the same start tick."""
    me = os.getuid()
    # pid -> (parent pid, start tick). /proc/<pid>/stat is "pid (name) state ppid ...": the name may hold spaces,
    # so the text is cut at the LAST ")"; after it, field 1 is the parent pid and field 19 the start tick.
    procs = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            rest = _read("/proc/%s/stat" % d).rsplit(")", 1)[-1].split()
            if len(rest) > 19:
                procs[int(d)] = (int(rest[1]), rest[19])
    # parent pid -> its child pids, to find the background shells of a Claude process.
    children = {}
    for pid, (ppid, _) in procs.items():
        children.setdefault(ppid, []).append(pid)
    # pane process -> "session:window.pane" for every tmux pane of this user (none when tmux is missing).
    panes = {}
    try:
        out = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}:#{window_index}.#{pane_index}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5).stdout
        for line in out.splitlines():
            pid, _, name = line.partition(" ")
            if pid.isdigit():
                panes[int(pid)] = name
    except Exception:
        pass
    # Boot time (seconds since 1970) and clock ticks per second turn a start tick into a time.
    btime = 0
    for line in _read("/proc/stat").splitlines():
        if line.startswith("btime "):
            btime = int(line.split()[1])
    hz = os.sysconf("SC_CLK_TCK")
    alive = {}
    for pid, start in cands:
        pid = int(pid)
        # Same pid but another start tick = the pid was reused by another program: not our process.
        if procs.get(pid, (0, None))[1] != str(start):
            continue
        try:
            if os.stat("/proc/%d" % pid).st_uid != me:
                continue
        except OSError:
            continue
        # Walk up the parents (at most 40 steps) to the tmux pane that holds the process, if any.
        tmux, p = "", pid
        for _ in range(40):
            if p in panes:
                tmux = panes[p]
                break
            p = procs.get(p, (0, None))[0]
            if p <= 1:
                break
        # Claude Code runs each shell command as a child whose command line holds its shell snapshot; the part
        # between "&& eval" and "&& pwd -P" is the command itself.
        shells = []
        for c in children.get(pid, []):
            cmd = _cmdline(c)
            if "shell-snapshots/snapshot-" in cmd:
                m = re.search(r"&& eval (.*?)(?: < /dev/null)? && pwd -P", cmd, re.S)
                shells.append((m.group(1) if m else cmd)[:240])
        env = dict(kv.split("=", 1) for kv in _read("/proc/%d/environ" % pid).split("\0") if "=" in kv)
        alive[str(pid)] = {
            "exe": _link("/proc/%d/exe" % pid),
            "term_program": env.get("TERM_PROGRAM", ""),
            "in_tmux": bool(env.get("TMUX")),
            "cwd": _link("/proc/%d/cwd" % pid),
            "tty": _link("/proc/%d/fd/0" % pid),
            "tmux": tmux,
            "shells": shells,
            "proc_started": btime + int(start) / float(hz) if btime else None,
        }
    return {"host": socket.gethostname().split(".")[0],
            "machine_id": _read("/etc/machine-id").strip(),
            "alive": alive}


if __name__ == "__main__":
    print(json.dumps(check(json.loads(sys.argv[1]) if len(sys.argv) > 1 else [])))
'''


# ---------------------------------------------------------------------------------------------------------------------
# Small helpers (also used on other machines: standard library only)
# ---------------------------------------------------------------------------------------------------------------------

def run(cmd, stdin_text=None, timeout=60):
    """subprocess.run with text I/O; a timeout or a missing program becomes returncode 124 / 127 instead of an error."""
    try:
        return subprocess.run(cmd, input=stdin_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timed out after %d s" % timeout)
    except OSError as error:
        return subprocess.CompletedProcess(cmd, 127, "", str(error))


def master_is_open(ssh_target):
    """True when a shared SSH connection (ControlMaster) to `ssh_target` is open on this machine.

    `ssh -O check <target>` only asks the local master process; it never opens a login, so it cannot prompt.
    """
    return run(["ssh", "-O", "check", ssh_target], timeout=15).returncode == 0


def last_json_line(text):
    """The last line of `text` that is a JSON object (remote output may start with login banners), else None."""
    for line in reversed(text.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def short_host():
    """This machine's host name up to the first dot ("ws01.example.org" -> "ws01")."""
    return socket.gethostname().split(".")[0]


def expand_user_path(path):
    """~ and $VARIABLES expanded, on the machine that runs this (so "~/.claude" means that machine's home)."""
    return os.path.expandvars(os.path.expanduser(str(path)))


def to_epoch(ts):
    """'2026-01-08T15:32:16.021Z' (UTC) or milliseconds -> seconds since 1970; None if unreadable."""
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e11 else float(ts)
    # Groups: year, month, day, hour, minute, second, optional fraction. calendar.timegm reads the tuple as UTC.
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?", ts or "")
    if not m:
        return None
    sec = calendar.timegm(tuple(int(x) for x in m.groups()[:6]) + (0, 0, 0))
    return sec + (float(m.group(7)) if m.group(7) else 0.0)


def one_line(text, limit):
    """Collapse white space to single spaces and cut to `limit` characters (with "…" when cut)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def loads(raw):
    """One transcript line -> dict, or None when it is not a JSON object."""
    try:
        rec = json.loads(raw)
        return rec if isinstance(rec, dict) else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------------------------------------------------
# Transcripts
# ---------------------------------------------------------------------------------------------------------------------

def prompt_text(rec):
    """The typed text of a "user" record, or None for tool results and records without text.

    Editor context ("<ide_selection>...") and system reminders are left out: they are not what the user typed.
    """
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    texts = [t for t in texts if t.strip() and not t.lstrip().startswith(("<ide_", "<system-reminder>"))]
    return " ".join(texts) if texts else None


def is_human(rec):
    """True for a request the user typed (not a task notice, a skill's text, or a continuation summary)."""
    if rec.get("isMeta") or rec.get("isSidechain"):
        return False
    # Newer Claude Code versions say who wrote the message in "origin.kind".
    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind"):
        return origin["kind"] == "human"
    text = (prompt_text(rec) or "").lstrip()
    return bool(text) and not text.startswith(("<", "This session is being continued", "Caveat:"))


def notice_text(rec):
    """The summary of a background-task notice (its <summary> or <status> tag), on one line."""
    text = prompt_text(rec) or ""
    m = re.search(r"<summary>(.*?)</summary>", text, re.S) or re.search(r"<status>(.*?)</status>", text, re.S)
    return one_line(m.group(1) if m else re.sub(r"<[^>]+>", " ", text), 200)


def tool_brief(name, inp):
    """'Bash: <description or command>' for a tool call: the first input field that says what the call did."""
    if isinstance(inp, dict):
        for key in ("description", "command", "file_path", "path", "pattern", "url", "query",
                    "prompt", "skill", "subject", "question"):
            val = inp.get(key)
            if isinstance(val, str) and val.strip():
                return "%s: %s" % (name, one_line(val, 110))
        if isinstance(inp.get("questions"), list) and inp["questions"]:
            q = inp["questions"][0]
            if isinstance(q, dict):
                return "%s: %s" % (name, one_line(q.get("question", ""), 110))
    return name


def parse_transcript(path, keep_prompts=False):
    """Start, last message, folder, titles and a digest of the recent turns of one transcript (.jsonl).

    keep_prompts: also keep the text of every request (for --match).
    Speed: only lines that can matter are parsed as JSON (cheap byte tests first); the digest is built from the last
    150 user/assistant lines, kept in a deque (a list with a maximum length that drops its oldest items).
    """
    info = {"path": path, "size": 0, "started": None, "last_message": None, "cwd": "",
            "custom_title": None, "ai_title": None, "agent_name": None, "prompts": 0,
            "first_prompt": None, "recent_prompts": [], "last_reply": None, "recent_tools": [],
            "pending_tool": None, "last_notice": None, "compactions": 0}
    if keep_prompts:
        info["all_prompts"] = []
    try:
        info["size"] = os.path.getsize(path)
        f = open(path, "rb")
    except OSError:
        return info
    tail = collections.deque(maxlen=150)
    recent = collections.deque(maxlen=3)
    # Name records: the user's /rename (custom-title), Claude Code's automatic title (ai-title), an agent name.
    titles = ((b'"type":"custom-title"', "custom_title", "customTitle"),
              (b'"type":"ai-title"', "ai_title", "aiTitle"),
              (b'"type":"agent-name"', "agent_name", "agentName"))
    with f:
        for raw in f:
            is_user = b'"type":"user"' in raw
            if is_user or b'"type":"assistant"' in raw:
                # Sub-agent turns (sidechains) are not the conversation itself.
                if b'"isSidechain":true' in raw:
                    continue
                tail.append(raw)
                if info["started"] is None:
                    info["started"] = to_epoch((loads(raw) or {}).get("timestamp"))
                if is_user and b'"tool_use_id"' not in raw:
                    rec = loads(raw)
                    if rec is None:
                        continue
                    if is_human(rec):
                        info["prompts"] += 1
                        text = prompt_text(rec)
                        item = [to_epoch(rec.get("timestamp")), one_line(text, 300)]
                        if info["first_prompt"] is None:
                            info["first_prompt"] = item
                        recent.append(item)
                        if keep_prompts:
                            info["all_prompts"].append(one_line(text, 2000))
                    elif (rec.get("origin") or {}).get("kind") == "task-notification":
                        info["last_notice"] = [to_epoch(rec.get("timestamp")), notice_text(rec)]
                continue
            # `for ... else`: the else block runs only when the loop ended without `break` (no title record).
            for needle, field, attr in titles:
                if needle in raw:
                    val = (loads(raw) or {}).get(attr)
                    if val:
                        info[field] = val
                    break
            else:
                if info["started"] is None and b'"timestamp"' in raw:
                    info["started"] = to_epoch((loads(raw) or {}).get("timestamp"))
                if b'"compact_boundary"' in raw:
                    info["compactions"] += 1
    # The tail gives the last message, the folder, the last reply, the recent tool calls, and a tool call that has
    # no result yet (Claude is waiting for it).
    tools, pending = [], collections.OrderedDict()
    for raw in tail:
        rec = loads(raw)
        if rec is None:
            continue
        ts = to_epoch(rec.get("timestamp"))
        if ts:
            info["last_message"] = ts
        info["cwd"] = rec.get("cwd") or info["cwd"]
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if rec.get("type") == "assistant" and b.get("type") == "text" and b.get("text", "").strip():
                info["last_reply"] = [ts, one_line(b["text"], 500)]
            elif rec.get("type") == "assistant" and b.get("type") == "tool_use":
                tools.append([ts, tool_brief(b.get("name", "?"), b.get("input"))])
                pending[b.get("id")] = b.get("name")
            elif b.get("type") == "tool_result":
                pending.pop(b.get("tool_use_id"), None)
    info["recent_tools"] = tools[-6:]
    info["recent_prompts"] = list(recent)
    if pending:
        info["pending_tool"] = list(pending.values())[-1]
    return info


# ---------------------------------------------------------------------------------------------------------------------
# One site (runs on a machine of that site: here, or on another machine through `ssh host python3 -`)
# ---------------------------------------------------------------------------------------------------------------------

def read_registry(cdir):
    """The entries of <claude dir>/sessions/*.json, each with its "machine_id" taken from "pidDomain".

    "pidDomain" looks like "linux:<machine-id>:<boot id>"; entries without it get machine_id "".
    """
    entries = []
    for path in glob.glob(os.path.join(cdir, "sessions", "*.json")):
        try:
            with open(path) as f:
                e = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(e, dict) or "pid" not in e or "sessionId" not in e:
            continue
        parts = (e.get("pidDomain") or "").split(":")
        e["machine_id"] = parts[1] if len(parts) > 2 and parts[0] == "linux" else ""
        entries.append(e)
    return entries


def transcript_index(cdir):
    """session id -> its transcript path (the newest file when a session id appears in two project folders)."""
    index = {}
    for path in glob.glob(os.path.join(cdir, "projects", "*", "*.jsonl")):
        sid = os.path.basename(path)[:-6]
        if sid not in index or os.path.getmtime(path) > os.path.getmtime(index[sid]):
            index[sid] = path
    return index


def discover_nodes(this_host, limit=9):
    """Login nodes of a cluster: this host's name with its number replaced by 1..limit, kept when the name resolves.

    Examples: "mycluster3" -> mycluster1 .. mycluster5 (those that resolve); "login1" -> login1 .. login3. A host
    name without a number at the end gives only itself.
    """
    # Groups: (everything before the final digits)(the final digits).
    m = re.match(r"^(.*?)(\d+)$", this_host)
    if not m:
        return [this_host]
    prefix, digits = m.groups()
    names = []
    for i in range(1, limit + 1):
        # zfill keeps the width: "login01" -> "login02".
        name = prefix + str(i).zfill(len(digits))
        if name != this_host:
            try:
                socket.gethostbyname(name)
            except (socket.error, UnicodeError):
                continue
        names.append(name)
    return names


def check_node(name, ssh_target, cands, local, key_login):
    """Ask one machine which candidate processes are alive there (see CHECKER_SRC).

    Parameters
        name        the machine's name as listed for the site (kept as "listed_name" in the answer).
        ssh_target  what to give to ssh (the ssh_alias, else the name).
        cands       [[pid, procStart], ...].
        local       True for the machine running this code (no ssh).
        key_login   False: contact another machine only through an open shared connection to it.
    Returns
        the checker's answer {"host", "machine_id", "alive"}, or {"host", "error"} (+ "skipped": True when the
        machine was left out on purpose because no shared connection to it is open).
    """
    if local:
        namespace = {"__name__": "checker"}
        # exec() runs the checker's source in `namespace`, so the same code serves here and over ssh.
        exec(CHECKER_SRC, namespace)
        answer = namespace["check"](cands)
        answer["listed_name"] = name
        return answer
    if not key_login and not master_is_open(ssh_target):
        return {"host": name, "listed_name": name, "skipped": True,
                "error": "not checked (no open shared SSH connection to %s)" % ssh_target}
    # `python3 -` reads the program from standard input; shlex.quote makes the JSON one safe shell word.
    r = run(NODE_SSH + [ssh_target, "python3 - " + shlex.quote(json.dumps(cands))], CHECKER_SRC, timeout=45)
    answer = last_json_line(r.stdout) if r.returncode == 0 else None
    if answer is None:
        err = (r.stderr.strip().splitlines() or ["no answer (exit %d)" % r.returncode])[-1]
        return {"host": name, "listed_name": name, "error": one_line(err, 160)}
    answer["listed_name"] = name
    return answer


def app_of(details, entry):
    """The app a Claude process runs in, from its program path and environment (VS Code, tmux, terminal, ...)."""
    exe = details.get("exe", "")
    if "/.vscode-server/" in exe:
        app = "VS Code Remote-SSH"
    elif "/.cursor-server/" in exe:
        app = "Cursor Remote-SSH"
    elif "/.cursor/" in exe:
        app = "Cursor"
    elif "/.vscode/" in exe or entry.get("entrypoint") == "claude-vscode":
        app = "VS Code"
    elif details.get("tmux") or details.get("in_tmux"):
        app = "tmux"
    elif details.get("term_program") == "vscode":
        app = "VS Code terminal"
    else:
        app = "terminal"
    return app


def build_conv(sid, info, procs, system, path, fallback_system):
    """One conversation: the facts of its transcript, and the processes that have it open (none when closed).

    system           the site's fixed system name ("" = use the host where it is open).
    fallback_system  the name shown for a closed conversation when `system` is "" (its machine is not known).
    """
    plist = []
    for e, host, d in procs:
        plist.append({"host": host, "pid": e["pid"], "app": app_of(d, e), "entrypoint": e.get("entrypoint", ""),
                      "tmux": d.get("tmux", ""), "tty": d.get("tty", ""), "status": e.get("status", ""),
                      "status_since": to_epoch(e.get("statusUpdatedAt")), "since": d.get("proc_started"),
                      "shells": d.get("shells", []), "registry_name": e.get("name", ""),
                      "name_source": e.get("nameSource", "")})
    # The name: the user's rename first (transcript or registry), then the automatic title, then the short id.
    user_name = next((p["registry_name"] for p in plist if p["name_source"] == "user"), "")
    title, source = info["custom_title"] or user_name, "custom"
    if not title:
        title, source = info["ai_title"] or info["agent_name"], "ai"
    if not title:
        title, source = (procs[0][0].get("name") if procs else "") or sid[:8], "registry"
    if plist:
        statuses = [p["status"] for p in plist]
        shells = [s for p in plist for s in p["shells"]]
        status = "busy" if "busy" in statuses else (statuses[0] or "unknown")
        # Idle, but a command it started in the background still runs: "background".
        if status == "idle" and shells:
            status = "background"
        cwd, where = procs[0][0].get("cwd") or info["cwd"], system or plist[0]["host"]
    else:  # closed: the folder comes from the transcript, the machine is not known
        status, shells, cwd, where = "closed", [], info["cwd"], system or fallback_system
    return {"session_id": sid, "title": title, "title_source": source, "cwd": cwd, "system": where,
            "status": status, "processes": plist, "background": shells, "started": info["started"],
            "last_message": info["last_message"], "prompts": info["prompts"], "transcript": path,
            "transcript_mb": round(info["size"] / 1e6, 1),
            "digest": {k: info[k] for k in ("first_prompt", "recent_prompts", "last_reply", "recent_tools",
                                            "pending_tool", "last_notice", "compactions")}}


def matches(info, text):
    """--match: is TEXT (lower case) in the name, folder, any request or the last reply?"""
    hay = [info.get("custom_title") or "", info.get("ai_title") or "", info.get("cwd") or ""]
    hay += info.get("all_prompts") or []
    if info.get("last_reply"):
        hay.append(info["last_reply"][1])
    return any(text in h.lower() for h in hay)


def find_recent(index, groups, system, fallback_system, opts):
    """--last / --match / --since: the conversations of this site with the most recent messages, open or not.

    Returns (the newest ones, how many matched) - the count is None when nothing filters (plain --last).
    """
    n = int(opts.get("last") or 20)
    match = (opts.get("match") or "").lower()
    since = opts.get("since_epoch")
    items = []
    for sid, path in index.items():
        try:
            items.append((os.path.getmtime(path), sid, path))
        except OSError:
            pass
    items.sort(reverse=True)
    if since:  # a transcript is written at every message, so its file time is >= its last message
        items = [x for x in items if x[0] >= since]
    if not match:  # the file time is close to the last message: read only the newest files
        items = items[:n + 10]
    found = []
    for _, sid, path in items:
        info = parse_transcript(path, keep_prompts=bool(match))
        if not (info["prompts"] or info["last_reply"]):
            continue
        if since and (info["last_message"] or 0) < since:
            continue
        if match and not matches(info, match):
            continue
        info.pop("all_prompts", None)
        found.append(build_conv(sid, info, groups.get(sid, []), system, path, fallback_system))
    found.sort(key=lambda c: -(c["last_message"] or 0))
    return found[:n], (len(found) if (match or since) else None)


def collect_site(site, opts=None):
    """Conversations of the site whose home folder this machine sees.

    Parameters
        site  one dict of build_sites(): key, label, claude_dir, nodes ("auto", "self" or a list of names),
              node_ssh {name: ssh target}, system, fallback_system, machine_ids {machine-id: name} (learned).
        opts  mode ("open" or "last"), last, match, since_epoch, recent_hours, key_login.
    Returns
        a JSON-ready dict: the conversations, the machines checked, empty tabs, unverified entries, counts, and
        node_ids {machine-id: listed name} of the machines that answered (for the cache).
    """
    opts = opts or {}
    mode = opts.get("mode", "open")
    recent_hours = float(opts.get("recent_hours", 24.0))
    key_login = bool(opts.get("key_login"))
    now = time.time()
    this_host = short_host()
    try:
        with open("/etc/machine-id") as f:
            this_mid = f.read().strip()
    except OSError:
        this_mid = ""
    cdir = expand_user_path(site.get("claude_dir") or DEFAULT_CLAUDE_DIR)
    system = site.get("system") or ""
    fallback_system = site.get("fallback_system") or short_host()
    node_ssh = site.get("node_ssh") or {}
    result = {"site": site["key"], "label": site.get("label", site["key"]), "reached": True,
              "checked_at": now, "checked_from": this_host, "cluster_system": system,
              "home": os.path.expanduser("~"), "nodes": [], "conversations": [], "empty": [],
              "unverified": [], "closed_entries": 0, "registry_entries": 0, "matched": None, "node_ids": {}}
    entries = read_registry(cdir)
    result["registry_entries"] = len(entries)
    index = transcript_index(cdir)
    groups = collections.OrderedDict()  # session id -> [(registry entry, host, process details)]
    nodes, answers, auto = [], [], False
    if entries:
        node_spec = site.get("nodes") or "self"
        auto = node_spec == "auto"
        if auto:
            names = discover_nodes(this_host)
        elif node_spec == "self":
            names = [this_host]
        else:
            names = list(node_spec)
        others = [n for n in names if n != this_host]
        mids = site.get("machine_ids") or {}
        if mids:
            # Machines whose machine-id is known: visit only those that entries point to. An entry with an
            # unknown machine-id (a machine not learned yet, or one whose id changed) makes every machine visited.
            wanted = set(e["machine_id"] for e in entries) - {this_mid}
            unknown = any(m not in mids for m in wanted)
            if not unknown:
                others = [n for n in others if any(mids.get(m) == n for m in wanted)]
        cands = [[e["pid"], e.get("procStart")] for e in entries if e.get("procStart")]
        nodes = [(this_host, True)] + [(n, False) for n in others]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(
                lambda node: check_node(node[0], node_ssh.get(node[0], node[0]), cands, node[1], key_login), nodes))

        # A dead entry is "closed" once the machine it names was checked. Login nodes of a cluster can share one
        # machine-id, so there a machine left unchecked leaves dead entries unverified. Names found by
        # discover_nodes() that refuse a key login are often not login nodes (test or service machines): they are
        # listed as "no answer" but do not make the other checks uncertain. A machine skipped on purpose (no open
        # shared connection) always makes them uncertain, because it may hold live processes.
        skipped = any(a.get("skipped") for a in answers)
        errors = skipped or (not auto and any("error" in a for a in answers))
        reached_mids = set(a.get("machine_id") for a in answers if "error" not in a)
        for e in entries:
            hit = None
            for a in answers:
                d = (a.get("alive") or {}).get(str(e["pid"]))
                if d and (not e["machine_id"] or not a.get("machine_id") or e["machine_id"] == a["machine_id"]):
                    hit = (a["host"], d)
                    break
            if hit:
                groups.setdefault(e["sessionId"], []).append((e, hit[0], hit[1]))
                continue
            if e["machine_id"] in reached_mids and (mids or not errors):
                result["closed_entries"] += 1
                continue
            path = index.get(e["sessionId"])
            last = os.path.getmtime(path) if path else None
            if mode == "open" and last and now - last < recent_hours * 3600:
                info = parse_transcript(path)
                result["unverified"].append({
                    "session_id": e["sessionId"], "cwd": e.get("cwd", ""), "machine_id": e["machine_id"],
                    "title": info["custom_title"] or info["ai_title"] or e.get("name", ""),
                    "last_message": info["last_message"]})
            else:
                result["closed_entries"] += 1

    if mode == "open":
        for sid, procs in groups.items():
            path = index.get(sid)
            info = parse_transcript(path) if path else None
            # An open tab with no message yet is counted, not listed.
            if info is None or not (info["prompts"] or info["last_reply"]):
                for e, host, d in procs:
                    result["empty"].append({"host": host, "app": app_of(d, e), "cwd": e.get("cwd", ""),
                                            "session_id": sid})
                continue
            result["conversations"].append(build_conv(sid, info, procs, system, path, fallback_system))
        result["conversations"].sort(key=lambda c: -(c["last_message"] or 0))
    else:
        result["conversations"], result["matched"] = find_recent(index, groups, system, fallback_system, opts)
    for (name, _), a in zip(nodes, answers):
        host = a.get("host", name) if "error" not in a else name
        result["nodes"].append({"name": host, "ok": "error" not in a, "error": a.get("error", ""), "auto": auto,
                                "skipped": bool(a.get("skipped")),
                                "open": len(set(c["session_id"] for c in result["conversations"]
                                                for p in c["processes"] if p["host"] == host))})
        if "error" not in a and a.get("machine_id"):
            result["node_ids"][a["machine_id"]] = a.get("listed_name", name)
    return result


# ---------------------------------------------------------------------------------------------------------------------
# Settings -> sites (runs only on the machine where the user asked; needs workhub_config.py next to this file)
# ---------------------------------------------------------------------------------------------------------------------

def load_settings():
    """Import workhub_config.py from this script's folder and return (module, settings dict).

    The import happens here, not at the top of the file, because the copy of this script that runs on another
    machine (through `ssh host python3 -`) has no workhub_config.py next to it and needs no settings.
    Exits with status 2 and the setup hint when the settings file is missing or unreadable.
    """
    script_folder = os.path.dirname(os.path.abspath(__file__))
    if script_folder not in sys.path:
        sys.path.insert(0, script_folder)
    import workhub_config
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        sys.stderr.write(str(error) + "\n")
        sys.exit(2)
    return workhub_config, config


def portable_home_path(setting_value, default_form):
    """A path that means the same folder on another machine.

    The setting itself when it starts at the home folder (~ or $HOME): the other machine expands it to ITS home.
    Otherwise `default_form`, because an absolute path of this machine may not exist elsewhere.
    Example: "~/.claude" -> "~/.claude"; "/fast/disk/claude" -> default_form.
    """
    text = str(setting_value or "").strip()
    if text.startswith(("~", "$HOME", "${HOME}")):
        return text
    return default_form


def claude_folders(wc, config):
    """(this machine's Claude Code folder, the form sent to other sites) from conversations.claude_projects_dir.

    Claude Code keeps projects/ (transcripts) and sessions/ (running processes) side by side in one folder, so the
    folder above the projects folder is the one this script needs ("~/.claude/projects" -> "~/.claude").
    """
    projects_setting = str(wc.get_setting(config, "conversations.claude_projects_dir",
                                          default=DEFAULT_CLAUDE_DIR + "/projects"))
    claude_setting = os.path.dirname(projects_setting.rstrip("/")) or DEFAULT_CLAUDE_DIR
    return wc.expand_path(claude_setting), portable_home_path(claude_setting, DEFAULT_CLAUDE_DIR)


def machine_name(machine):
    """The lower-case name of a workstation or cluster entry of the settings ("" when it has none)."""
    return str(machine.get("name") or "").strip().lower()


def ssh_target_of(machine):
    """What ssh needs to reach a machine of the settings: its ssh_alias, else its name."""
    alias = str(machine.get("ssh_alias") or "").strip()
    return alias or str(machine.get("name") or "").strip()


def build_sites(wc, config):
    """Build the list of sites from the settings (rules in the module docstring, SITES). The home site is first.

    Each site: key, label, kind ("home", "cluster" or "workstation"), local (True for the home site), ssh (target
    for another site), nodes ("auto", "self" or names), node_ssh, system, fallback_system, claude_dir.
    """
    here = wc.system_name(config)
    home_label = str(wc.skill_setting(config, SKILL_NAME, "home_site_label", "Workstations"))
    claude_here, claude_elsewhere = claude_folders(wc, config)
    # Every machine of the settings with its kind, workstations first, in the order the user listed them.
    listed = [("workstation", m) for m in wc.workstations(config)] + [("cluster", c) for c in wc.clusters(config)]
    this_is_a_cluster = any(kind == "cluster" and machine_name(m) == here for kind, m in listed)
    home_nodes = [short_host()]
    home_node_ssh = {}
    other_sites = []
    seen_names = set([here])
    for kind, machine in listed:
        name = machine_name(machine)
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        if machine.get("shares_home_with_this_machine"):
            # Same home folder = same transcript store: a machine of the home site, checked for live processes.
            home_nodes.append(name)
            home_node_ssh[name] = ssh_target_of(machine)
            continue
        if kind == "workstation" and not str(machine.get("ssh_alias") or "").strip():
            # A workstation with its own home but no ssh_alias cannot be reached: not a site of this skill.
            continue
        other_sites.append({"key": name, "label": name, "kind": kind, "local": False, "ssh": ssh_target_of(machine),
                            "nodes": "auto" if kind == "cluster" else "self", "node_ssh": {},
                            "system": name, "fallback_system": name, "claude_dir": claude_elsewhere})
    if this_is_a_cluster:
        home = {"key": here, "label": here, "kind": "cluster", "local": True, "ssh": "", "nodes": "auto",
                "node_ssh": {}, "system": here, "fallback_system": here, "claude_dir": claude_here}
    else:
        home = {"key": "home", "label": home_label, "kind": "home", "local": True, "ssh": "", "nodes": home_nodes,
                "node_ssh": home_node_ssh, "system": "", "fallback_system": here, "claude_dir": claude_here}
    return [home] + other_sites


# ---------------------------------------------------------------------------------------------------------------------
# The machine-id cache of the home site
# ---------------------------------------------------------------------------------------------------------------------

def machine_id_cache_path(wc, config):
    """<state_dir>/active-conversations/machine_ids.json (the folder is created with mode 700)."""
    return os.path.join(wc.state_dir(config, SKILL_NAME), "machine_ids.json")


def read_machine_id_cache(path):
    """{site key: {machine-id: machine name}} from the cache file; {} when it is missing or damaged."""
    try:
        with open(path, encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def write_machine_id_cache(path, cache):
    """Write the cache atomically (temporary file, then rename) with mode 600: it names the user's machines."""
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, indent=1, sort_keys=True)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)


# ---------------------------------------------------------------------------------------------------------------------
# Other sites (through an already open shared SSH connection only)
# ---------------------------------------------------------------------------------------------------------------------

def not_reached(site, reason, code, shareable=False):
    """The result of a site that was not checked; `code` = no_route / no_master / failed (see reach_hint())."""
    return {"site": site["key"], "label": site.get("label", site["key"]), "reached": False, "ssh": site.get("ssh", ""),
            "reason": reason, "reason_code": code, "shareable": shareable,
            "checked_at": time.time(), "checked_from": short_host()}


def collect_remote(site, src, opts):
    """Check another site by sending this script over its open shared SSH connection; never opens a new login."""
    target = site.get("ssh")
    if not target:
        return not_reached(site, "no SSH route from %s" % short_host(), "no_route")
    if not master_is_open(target):
        # Would a shared connection be possible here? (`ssh -G` prints the effective settings for the target.)
        shareable = "controlmaster auto" in run(["ssh", "-G", target], timeout=15).stdout.lower()
        return not_reached(site, "no open shared SSH connection from %s" % short_host(), "no_master", shareable)

    # base64 turns the JSON into letters, digits, "+", "/" and "=": one word that the remote shell leaves alone.
    def b64(obj):
        return base64.b64encode(json.dumps(obj).encode()).decode()

    r = run(SSH + [target, "python3 - --site-b64 %s --opts-b64 %s" % (b64(site), b64(opts))], src, timeout=240)
    answer = last_json_line(r.stdout) if r.returncode == 0 else None
    if answer is None:
        err = (r.stderr.strip().splitlines() or ["no answer (exit %d)" % r.returncode])[-1]
        return not_reached(site, "the check failed: %s" % one_line(err, 160), "failed")
    answer["via"] = "ssh %s" % target
    return answer


def reach_hint(site_result, asked_from, cluster_login_enabled, code=None):
    """One sentence telling the user how to get an unreached site checked next time.

    Parameters
        site_result            a not_reached() result.
        asked_from             the system name of the machine that ran the collector.
        cluster_login_enabled  settings cluster_login.enabled: the kit's cluster-login tool is installed.
        code                   function that formats a command (Markdown back-quotes by default).
    """
    if code is None:
        def code(text):
            return "`%s`" % text
    label = site_result.get("label") or site_result.get("site", "")
    target = site_result.get("ssh") or site_result.get("site", "")
    reason_code = site_result.get("reason_code")
    if reason_code == "no_master" and site_result.get("shareable"):
        if cluster_login_enabled:
            return ("No open shared SSH connection from %s. Run %s in a terminal there (it asks for your login "
                    "once), or ask from a Claude session on %s." % (asked_from, code("cluster-login %s" % target), label))
        return ("No open shared SSH connection from %s. Open one in a terminal there, for example %s, or ask from a "
                "Claude session on %s." % (asked_from, code("ssh -fN %s" % target), label))
    if reason_code == "no_master":
        return ("No shared SSH connection (ControlMaster) is set up for %s on %s. Ask from a Claude session on %s."
                % (target, asked_from, label))
    if reason_code == "no_route":
        return "Cannot be reached from %s. Ask from a Claude session on %s." % (asked_from, label)
    return "The check from %s failed: %s" % (asked_from, site_result.get("reason", ""))


# ---------------------------------------------------------------------------------------------------------------------
# Clock times and the digest
# ---------------------------------------------------------------------------------------------------------------------

def local_clock(zone_name):
    """(tzinfo, label) for the owner's time zone; UTC with a label saying so when zoneinfo or its data is missing."""
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(zone_name), zone_name
    except Exception:  # noqa: BLE001 - no module (Python < 3.9) or no zone data: show UTC and say so
        return datetime.timezone.utc, "UTC (time zone %s not available)" % zone_name


def fmt(epoch, zone, with_date=True):
    """Seconds since 1970 -> "01-08 15:32" (or "15:32") in `zone`; "?" for None."""
    if not epoch:
        return "?"
    moment = datetime.datetime.fromtimestamp(epoch, tz=zone)
    return moment.strftime("%m-%d %H:%M" if with_date else "%H:%M")


def query_text(q):
    """The question of a run in words: "Open now", "Last 10 conversations matching 'x'", ..."""
    if q.get("mode") != "last":
        return "Open now"
    text = "Last %d conversations" % q["last"]
    if q.get("match"):
        text += " matching '%s'" % q["match"]
    if q.get("since_hours"):
        text += " with a message in the last %g h" % q["since_hours"]
    return text


def print_digest(data, cluster_login_enabled, out=sys.stdout):
    """Print the digest: per site, per conversation, the facts Claude needs to write a one-line description."""
    zone, zone_label = local_clock(data.get("timezone") or "UTC")
    w = out.write
    w("%s · checked %s from %s · times in %s\n" % (query_text(data.get("query", {})), fmt(data["generated_at"], zone),
                                                  data["requested_from"]["system"], zone_label))
    for s in data["sites"]:
        if not s.get("reached"):
            w("\n== %s: not reached (%s). %s\n" % (s["label"], s.get("reason", ""),
                                                   reach_hint(s, data["requested_from"]["system"],
                                                              cluster_login_enabled)))
            continue
        nodes = ", ".join(n["name"] for n in s["nodes"] if n["ok"])
        silent = [n["name"] for n in s["nodes"] if not n["ok"]]
        if silent:
            nodes += " (no answer from %s)" % ", ".join(silent)
        found = "%d listed" % len(s["conversations"])
        if s.get("matched") is not None:
            found += " of %d found" % s["matched"]
        w("\n== %s (%s): %s, %d empty tab(s), %d closed entries ignored · machines: %s\n" % (
            s["label"], s.get("via", "this machine"), found, len(s["empty"]), s["closed_entries"],
            nodes or "none checked"))
        for c in s["conversations"]:
            g = c["digest"]
            where = "; ".join("%s on %s%s" % (p["app"], p["host"], " (%s)" % p["tmux"] if p["tmux"] else "")
                              for p in c["processes"]) or "not open"
            w('[%s] "%s" · %s · %s\n' % (c["session_id"][:8], c["title"], c["status"], where))
            w("    cd %s:%s · started %s · last message %s · %d prompts\n" % (
                c["system"], c["cwd"], fmt(c["started"], zone), fmt(c["last_message"], zone), c["prompts"]))
            if g["first_prompt"]:
                w("    first ask: %s\n" % one_line(g["first_prompt"][1], 200))
            for ts, text in g["recent_prompts"]:
                w("    ask [%s]: %s\n" % (fmt(ts, zone), text))
            if g["last_reply"]:
                w("    last reply [%s]: %s\n" % (fmt(g["last_reply"][0], zone), g["last_reply"][1]))
            if g["recent_tools"]:
                w("    tools: %s\n" % " | ".join(t for _, t in g["recent_tools"][-4:]))
            if g["pending_tool"] and c["status"] != "closed":
                w("    tool call without result yet: %s\n" % g["pending_tool"])
            if g["last_notice"]:
                w("    last background notice [%s]: %s\n" % (fmt(g["last_notice"][0], zone), g["last_notice"][1]))
            for sh in c["background"]:
                w("    %s shell: %s\n" % ("running" if c["status"] == "busy" else "background", one_line(sh, 160)))
        for u in s["unverified"]:
            w('    unverified (machine %s not checked): "%s" in %s, last message %s\n' % (
                u["machine_id"][:8] or "?", u["title"], u["cwd"], fmt(u["last_message"], zone)))
        for e in s["empty"]:
            w("    empty tab: %s on %s, %s\n" % (e["app"], e["host"], e["cwd"]))


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------

def parse_arguments():
    """Define and read the command-line options (see the module docstring for examples)."""
    ap = argparse.ArgumentParser(
        description="Find the Claude Code conversations of every reachable site: open now (default) or the most "
                    "recent ones. Sites come from the workhub-claude settings.")
    ap.add_argument("-o", "--output", help="JSON file to write (default: <state_dir>/active-conversations/"
                                           "conversations.json)")
    ap.add_argument("--last", type=int, metavar="N",
                    help="the N conversations with the most recent messages, open or not, across the sites")
    ap.add_argument("--match", metavar="TEXT",
                    help="only conversations whose name, folder, requests or last reply contain TEXT "
                         "(any case); implies --last 20 unless --last is given")
    ap.add_argument("--since", type=float, metavar="HOURS",
                    help="only conversations with a message in the last HOURS; implies --last 50")
    ap.add_argument("--sites", help="comma-separated site keys (see --list-sites; default: all)")
    ap.add_argument("--list-sites", action="store_true", help="print the sites built from the settings as JSON")
    ap.add_argument("--recent-hours", type=float, default=24.0,
                    help="open mode: list entries that cannot be verified if active within this many hours")
    ap.add_argument("--digest", metavar="JSON", help="only print the digest of an earlier run")
    ap.add_argument("--quiet", action="store_true", help="do not print the digest")
    ap.add_argument("--site-b64", help=argparse.SUPPRESS)  # internal: check one site here, print JSON
    ap.add_argument("--opts-b64", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.last is not None and args.last < 1:
        ap.error("--last must be at least 1")
    return args


def main():
    """Collect every site (or run the internal one-site check), write the JSON, print the digest."""
    args = parse_arguments()

    # Internal mode: this copy runs on another site, sent there by collect_remote(). No settings are read.
    if args.site_b64:
        site = json.loads(base64.b64decode(args.site_b64).decode())
        opts = json.loads(base64.b64decode(args.opts_b64).decode()) if args.opts_b64 else {}
        print(json.dumps(collect_site(site, opts)))
        return 0

    wc, config = load_settings()
    cluster_login_enabled = bool(wc.get_setting(config, "cluster_login.enabled", default=False))
    all_sites = build_sites(wc, config)
    here = all_sites[0]
    if args.list_sites:
        print(json.dumps(all_sites, indent=1))
        return 0
    if args.digest:
        with open(args.digest, encoding="utf-8") as f:
            print_digest(json.load(f), cluster_login_enabled)
        return 0

    started = time.time()
    last_mode = bool(args.last or args.match or args.since)
    n = args.last or (20 if args.match else 50 if args.since else None)
    key_login = bool(wc.skill_setting(config, SKILL_NAME, "key_login_to_other_machines", False))
    opts = {"mode": "last" if last_mode else "open", "last": n, "match": args.match,
            "since_epoch": started - args.since * 3600 if args.since else None,
            "recent_hours": args.recent_hours, "key_login": key_login}
    sites = all_sites
    if args.sites:
        keys = [k.strip().lower() for k in args.sites.split(",") if k.strip()]
        sites = [s for s in all_sites if s["key"] in keys]
        unknown_keys = [k for k in keys if k not in [s["key"] for s in all_sites]]
        if unknown_keys:
            sys.stderr.write("unknown site key(s): %s (see --list-sites)\n" % ", ".join(unknown_keys))
            return 2

    # The machine-ids learned on earlier runs let the home site visit only the machines that registry entries name.
    cache_path = machine_id_cache_path(wc, config)
    cache = read_machine_id_cache(cache_path)
    if here["nodes"] != "auto":
        here["machine_ids"] = cache.get(here["key"]) or {}

    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        src = f.read()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for s in sites:
            if s["local"]:
                futures.append(pool.submit(collect_site, s, opts))
            else:
                futures.append(pool.submit(collect_remote, s, src, opts))
        results = [f.result() for f in futures]
    for r in results:
        if r["site"] == here["key"] and r.get("reached"):
            r["via"] = "this machine"
            # Remember which machine-id answered from which machine (home site with listed machines only).
            if here["nodes"] != "auto" and r.get("node_ids"):
                learned = dict(cache.get(here["key"]) or {})
                learned.update(r["node_ids"])
                if learned != cache.get(here["key"]):
                    cache[here["key"]] = learned
                    write_machine_id_cache(cache_path, cache)
    if last_mode:  # keep the N newest across all sites
        newest = sorted(((c["last_message"] or 0, r["site"], c["session_id"]) for r in results
                         if r.get("reached") for c in r["conversations"]), reverse=True)[:n]
        keep = set((site, sid) for _, site, sid in newest)
        for r in results:
            if r.get("reached"):
                r["newest_message"] = max([c["last_message"] or 0 for c in r["conversations"]] or [0]) or None
                r["conversations"] = [c for c in r["conversations"] if (r["site"], c["session_id"]) in keep]
    system = wc.system_name(config)
    data = {"schema": 2, "generated_at": started, "timezone": wc.time_zone(config),
            "query": {"mode": opts["mode"], "last": n, "match": args.match, "since_hours": args.since},
            "requested_from": {"system": system, "host": short_host(), "site": here["key"],
                               "site_label": here.get("label", here["key"]),
                               "skill_dir": os.path.dirname(os.path.dirname(os.path.abspath(__file__)))},
            "site_labels": [s["label"] for s in all_sites],
            "duration_s": round(time.time() - started, 1), "sites": results}
    out = args.output or os.path.join(wc.state_dir(config, SKILL_NAME), "conversations.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    if not args.quiet:
        print_digest(data, cluster_login_enabled)
    print("\nWrote %s (%.1f s)" % (os.path.abspath(out), data["duration_s"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
