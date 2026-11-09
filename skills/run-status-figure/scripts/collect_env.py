#!/usr/bin/env python3
"""Collect the environment block of a status file, or look at every machine listed in the settings.

    python collect_env.py --dir ~/projects/demo=code --session demo_queue="runs the queue" --merge status.json
    python collect_env.py --tmux-match demo_ --slurm                    # print the block as JSON
    python collect_env.py --machines -o machines.json                   # machine view: every listed machine

  --dir PATH[=ROLE]      a working folder to list (repeatable); the current folder if none is given
  --session NAME[=ROLE]  a tmux session to list, with what it does (repeatable)
  --tmux-match TEXT      also list every tmux session whose name contains TEXT
  --slurm                list your Slurm jobs (squeue) on this machine (a cluster login node)
  --merge FILE           write the block into FILE's "environment". Sessions already listed in FILE keep their role
                         and get their current state; working folders in FILE are kept unless --dir is given.
  --machines             machine view: look at every workstation and cluster of the settings file
                         (machines.workstations[], machines.clusters[]) and print one JSON report: GPUs (model,
                         memory, use), tmux sessions and their state, load, and the user's Slurm jobs.
  -o, --output FILE      with --machines: write the report to FILE instead of printing it.

INPUTS
    The live state of this machine (nvidia-smi, tmux, ps, squeue; each is optional) and, for --machines, the
    workhub-claude settings file (~/.config/workhub-claude/config.json, read with the bundled workhub_config.py).

OUTPUTS
    The environment block as JSON (printed, or merged into the status file: the only file this script changes
    without -o), or the machine report.

HOW OTHER MACHINES ARE READ (--machines)
    This machine (its name = workhub_config.system_name()) is read directly. Another machine is read only when
    it can be reached WITHOUT any prompt:
      - a cluster: only through an SSH connection that is already open (`ssh -O check <ssh_alias>` succeeds; the
        cluster-login tool opens such connections). A closed cluster is reported as not reached, never logged in.
      - a workstation: through an open connection, or else with `ssh -o BatchMode=yes` (key login; BatchMode makes
        ssh fail at once instead of asking for a password).
    The same small read-only shell script (PROBE_SCRIPT) runs on every machine, through `sh -s`, so the remote
    login shell (bash, zsh, tcsh) does not matter.

Session state: "running" when something other than a shell runs inside the tmux session, "idle" when only a shell
is left, "missing" when no session has that name. Standard library only; works on any Linux machine.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import argparse
import getpass
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

# workhub_config.py is the bundled copy of the kit's settings reader (same folder as this script). It gives the
# machine name used in paths (system_name), the listed machines, and the per-skill options.
from workhub_config import clusters, load_config, skill_setting, system_name, workstations

SHELLS = {"bash", "sh", "zsh", "fish", "tcsh", "csh", "dash", "ksh", "login", "tmux"}

# One read-only shell script, run with `sh -s` on this machine or through ssh on another one. Each part starts
# with a "##name" marker line so that parse_sections() can split the output; every command may be missing
# (2>/dev/null), which simply leaves its part empty.
#   gpu:    one CSV line per GPU: name, total MiB, used MiB, utilization % (nounits = plain numbers)
#   panes:  "session|pane pid" for every pane of every tmux session of this user
#   ps:     "pid ppid command" for every process (to find what runs below each pane)
#   load:   /proc/loadavg (1, 5, 15 min) and the number of CPUs
#   slurm:  the user's Slurm jobs: id|name|state|elapsed|time limit|start (expected start when pending)
PROBE_SCRIPT = """
echo '##gpu'
nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader,nounits 2>/dev/null
echo '##panes'
tmux list-panes -a -F '#{session_name}|#{pane_pid}' 2>/dev/null
echo '##ps'
ps -e -o pid=,ppid=,comm= 2>/dev/null
echo '##load'
cat /proc/loadavg 2>/dev/null
nproc 2>/dev/null
echo '##slurm'
squeue -h -u "$USER" -o '%i|%j|%T|%M|%l|%S' 2>/dev/null
echo '##end'
"""


# ------------------------------------------------------------------------------------------------------------
# Running commands
# ------------------------------------------------------------------------------------------------------------

def run(command, timeout=20, stdin_text=None):
    """Run a command; return (exit code, standard output). A missing program or a timeout gives (127, "")."""
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=timeout, input=stdin_text)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return finished.returncode, finished.stdout.strip()


def ssh_base(alias):
    """ssh options that can never prompt: BatchMode fails instead of asking; ConnectTimeout bounds the wait."""
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias]


def run_probe(alias=None):
    """Run PROBE_SCRIPT here (alias None) or on `alias` through ssh; return {part name: [lines]}."""
    command = ["sh", "-s"] if alias is None else ssh_base(alias) + ["sh", "-s"]
    _, output = run(command, timeout=40, stdin_text=PROBE_SCRIPT)
    return parse_sections(output)


# ------------------------------------------------------------------------------------------------------------
# Parsing the probe output (pure functions: the tests feed them synthetic text)
# ------------------------------------------------------------------------------------------------------------

def parse_sections(output):
    """Split the probe output at its "##name" marker lines: {"gpu": [...], "panes": [...], ...}."""
    sections, current = {}, None
    for line in output.splitlines():
        if line.startswith("##"):
            current = line[2:].strip()
            sections[current] = []
        elif current is not None and line.strip():
            sections[current].append(line.strip())
    return sections


def parse_gpus(lines):
    """nvidia-smi CSV lines -> [{"name": "RTX 4090", "memory_total_gb": 24.0, "memory_used_gb": 1.2,
    "utilization_percent": 35}]. Lines that are not GPU rows (an error message on a login node) are skipped."""
    gpus = []
    for line in lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        name = parts[0].replace("NVIDIA ", "").replace("GeForce ", "")
        used = float(parts[2]) / 1024 if parts[2].isdigit() else None
        use = int(parts[3]) if parts[3].isdigit() else None
        gpus.append({"name": name, "memory_total_gb": round(float(parts[1]) / 1024, 1),
                     "memory_used_gb": round(used, 1) if used is not None else None, "utilization_percent": use})
    return gpus


def gpu_description(gpus):
    """The header text of the figure, e.g. 'RTX 4090 (24.0 GB)' or '4 × H100 (79.6 GB)'; '' without a GPU."""
    if not gpus:
        return ""
    count = f"{len(gpus)} × " if len(gpus) > 1 else ""
    return f"{count}{gpus[0]['name']} ({gpus[0]['memory_total_gb']:.1f} GB)"


def command_name(text):
    """'-bash' or '/usr/bin/zsh' -> 'bash' / 'zsh' (login shells start with '-'; some systems give full paths)."""
    return os.path.basename(text.strip().lstrip("-"))


def tmux_states(pane_lines, ps_lines):
    """{session name: "running" | "idle"} from the panes and ps parts of the probe.

    A session is running when any process below one of its pane processes is not a shell (SHELLS); the pane
    process itself is usually the shell that tmux started, so it is not counted.
    """
    children = {}
    commands = {}
    for line in ps_lines:
        fields = line.split(None, 2)
        if len(fields) < 3 or not fields[0].isdigit():
            continue
        pid, parent, command = fields
        commands[pid] = command_name(command)
        children.setdefault(parent, []).append(pid)
    states = {}
    for line in pane_lines:
        session, _, pane_pid = line.rpartition("|")
        if not session:
            continue
        busy = False
        queue = list(children.get(pane_pid.strip(), []))
        while queue and not busy:                          # walk every descendant of the pane process
            pid = queue.pop()
            command = commands.get(pid, "")
            busy = bool(command) and command not in SHELLS
            queue.extend(children.get(pid, []))
        states[session] = "running" if busy or states.get(session) == "running" else "idle"
    return states


def parse_slurm(lines):
    """squeue lines -> session entries {"kind": "slurm", "name": "<id> <job name>", "state", "role"}."""
    jobs = []
    for line in lines:
        fields = line.split("|")
        if len(fields) < 5:
            continue
        job_id, name, state, elapsed, limit = fields[:5]
        start = fields[5] if len(fields) > 5 else ""
        role = f"{elapsed} of {limit}"
        if state.lower() == "pending" and start and start not in ("N/A", "Unknown"):
            role = f"expected start {start} (Slurm's guess)"
        jobs.append({"kind": "slurm", "name": f"{job_id} {name}", "state": state.lower(), "role": role})
    return jobs


def parse_load(lines):
    """['0.52 0.40 0.35 1/600 1234', '32'] -> {"load_1min": 0.52, "cpus": 32}; missing values are None."""
    load, cpus = None, None
    if lines:
        first = lines[0].split()
        if first:
            try:
                load = float(first[0])
            except ValueError:
                load = None
    if len(lines) > 1 and lines[1].isdigit():
        cpus = int(lines[1])
    return {"load_1min": load, "cpus": cpus}


# ------------------------------------------------------------------------------------------------------------
# Machine names and labels
# ------------------------------------------------------------------------------------------------------------

def machine_label(config, system):
    """The label of a machine in the Research log's "Clusters" property and in the figure header.

    The rule (the same as in the research-log skill):
      1. skill_settings.research-log.machine_labels: the entry whose key is the longest prefix of the machine
         name wins, e.g. {"ws": "Lab"} turns ws01 and ws02 into "Lab";
      2. a machine listed in machines.clusters: its name with a capital first letter ("mycluster" -> "Mycluster");
      3. otherwise the machine name itself ("ws01").
    """
    name = str(system or "").lower()
    labels = skill_setting(config, "research-log", "machine_labels", default={})
    if isinstance(labels, dict):
        matches = [prefix for prefix in labels if prefix and name.startswith(str(prefix).lower())]
        if matches:
            longest = max(matches, key=len)
            return str(labels[longest])
    for cluster in clusters(config):
        cluster_name = str(cluster.get("name", ""))
        if cluster_name.lower() == name and cluster_name:
            return cluster_name[:1].upper() + cluster_name[1:]
    return name


# ------------------------------------------------------------------------------------------------------------
# Machine view (--machines)
# ------------------------------------------------------------------------------------------------------------

def connection_open(alias):
    """True when an SSH connection to `alias` is already open (a shared ControlMaster socket)."""
    code, _ = run(["ssh", "-O", "check", alias], timeout=10)
    return code == 0


def how_to_reach(machine, kind, this_system):
    """(alias or None for this machine, how) when the machine can be read without a prompt, else (False, why)."""
    name = str(machine.get("name", "")).lower()
    if name == this_system:
        return None, "this machine"
    alias = str(machine.get("ssh_alias") or "")
    if not alias:
        return False, "no ssh_alias in the settings"
    if connection_open(alias):
        return alias, "open SSH connection"
    if kind == "cluster":
        return False, "no open SSH connection (open it with cluster-login, then run this again)"
    code, _ = run(ssh_base(alias) + ["true"], timeout=15)
    if code == 0:
        return alias, "key login (BatchMode, no prompt)"
    return False, "ssh without a prompt failed (no key login and no open connection)"


def machine_report(config):
    """Look at every listed machine; return the --machines report (a dict, see the module docstring)."""
    this_system = system_name(config)
    listed = [("workstation", machine) for machine in workstations(config)]
    listed += [("cluster", machine) for machine in clusters(config)]
    report = {"generated": datetime.now().isoformat(timespec="seconds"), "this_system": this_system,
              "machines": []}
    for kind, machine in listed:
        alias, how = how_to_reach(machine, kind, this_system)
        entry = {"name": machine.get("name", ""), "kind": kind, "label": machine_label(config, machine.get("name")),
                 "reached": alias is not False, "how": how}
        if alias is not False:
            sections = run_probe(alias)
            entry["gpus"] = parse_gpus(sections.get("gpu", []))
            states = tmux_states(sections.get("panes", []), sections.get("ps", []))
            entry["tmux"] = [{"name": name, "state": state} for name, state in states.items()]
            entry["load"] = parse_load(sections.get("load", []))
            entry["slurm"] = parse_slurm(sections.get("slurm", []))
            entry["probe_complete"] = "end" in sections        # False: the probe was cut off (timeout)
        report["machines"].append(entry)
    if not any(entry["name"].lower() == this_system for entry in report["machines"]):
        report["note"] = (f"this machine ({this_system}) is not listed in machines.workstations or "
                          "machines.clusters of the settings file")
    return report


# ------------------------------------------------------------------------------------------------------------
# Environment block of a status file (--merge, or printed)
# ------------------------------------------------------------------------------------------------------------

def split_pair(text):
    key, _, value = text.partition("=")
    return key, value


def tmux_sessions(wanted, match, states):
    """Session entries for the wanted names (in that order) plus every live session whose name contains `match`."""
    names = list(wanted) + [name for name in states if match and match in name and name not in wanted]
    sessions = []
    for name in names:
        state = states.get(name, "missing")
        sessions.append({"kind": "tmux", "name": name, "state": state})
    return sessions


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", action="append", default=[], help="PATH[=ROLE]")
    parser.add_argument("--session", action="append", default=[], help="NAME[=ROLE]")
    parser.add_argument("--tmux-match", help="List every tmux session whose name contains this text.")
    parser.add_argument("--slurm", action="store_true", help="List your Slurm jobs.")
    parser.add_argument("--merge", type=Path, help="Status JSON file to update.")
    parser.add_argument("--machines", action="store_true", help="Look at every machine of the settings file.")
    parser.add_argument("-o", "--output", type=Path, help="With --machines: write the report here.")
    args = parser.parse_args()

    # The settings are optional for the environment block (the machine name then comes from $CC_CLUSTER or the
    # host name) and required for --machines (it needs the list of machines).
    config = load_config(required=args.machines)

    if args.machines:
        text = json.dumps(machine_report(config), indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(text + "\n")
            print(f"machine report written to {args.output}")
        else:
            print(text)
        return

    system = system_name(config)
    status = json.loads(args.merge.read_text()) if args.merge else {}
    previous = status.get("environment", {})
    roles = {session["name"]: session.get("role", "") for session in previous.get("sessions", [])
             if session.get("kind", "tmux") == "tmux"}
    roles.update(dict(split_pair(item) for item in args.session))
    sections = run_probe(None)
    states = tmux_states(sections.get("panes", []), sections.get("ps", []))
    sessions = tmux_sessions(list(roles), args.tmux_match, states)
    for session in sessions:
        session["role"] = roles.get(session["name"], "")
    if args.slurm:
        sessions += parse_slurm(sections.get("slurm", []))
    folders = [split_pair(item) for item in args.dir]
    if not folders and not previous.get("working_dirs"):
        folders = [(os.getcwd(), "current folder")]
    label = machine_label(config, system)
    environment = {
        "system": system,
        "cluster": label if label != system else "",
        "user": getpass.getuser(),
        "gpu": gpu_description(parse_gpus(sections.get("gpu", []))),
        "working_dirs": [{"path": f"{system}:{Path(path).expanduser().resolve()}", "role": role}
                         for path, role in folders],
        "sessions": sessions,
    }
    if args.merge:
        # Empty live values keep what the file had (no GPU on a login node, no folders given), except the machine
        # label: it belongs to the live system, so an old label from another machine must not survive.
        merged = {**previous, **{key: value for key, value in environment.items() if value}}
        merged["cluster"] = environment["cluster"]
        status["environment"] = merged
        args.merge.write_text(json.dumps(status, indent=2, ensure_ascii=False))
        print(f"environment written into {args.merge}")
    else:
        print(json.dumps(environment, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
