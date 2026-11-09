#!/usr/bin/env python3
"""
claude_tokens_week.py - how many Claude tokens each Work Hub row used in one Monday-to-Sunday
week, from the Claude Code transcripts on this machine and on the reachable clusters. Read-only.

PURPOSE
    Claude plan limits can run out in a busy week. The weekly recap therefore shows where the
    week's tokens went: per row (Task ID such as TK-42), per conversation, per model and per
    machine. Every API request that Claude Code makes is written into the conversation's
    transcript with its token counts; this script adds them up for the requests made inside the week.

WHAT IS COUNTED
    Each assistant record of a transcript (<claude projects dir>/<folder>/<session>.jsonl) carries
    `message.usage`, with four kinds of tokens:
        input_tokens                 new input that was not cached;
        cache_creation_input_tokens  input written into the prompt cache (billed above input);
        cache_read_input_tokens      input read back from the cache (billed far below input);
        output_tokens                what the model wrote (including thinking).
    One request is written several times while it streams (one record per content block, all
    with the same `requestId`); only the LAST record of each request is kept, so nothing is
    counted twice. Subagent transcripts (<session>/subagents/agent-*.jsonl) are counted under
    their parent conversation, because that conversation launched them.

WHERE (from the workhub-claude settings file)
    This machine: conversations.claude_projects_dir (default ~/.claude/projects). When
    workstations share this home folder (machines.workstations[].shares_home_with_this_machine),
    the one scan covers all of them and is labelled "ws01+ws02".
    Clusters: machines.clusters[] that do not share this home, read through ALREADY-OPEN shared
    SSH connections (BatchMode); the same projects folder setting is used there ("~" = the
    cluster's home).

INPUTS
    --week            last (default), this, 2026-W45 or a day (see recap_common.py)
    --sessions-json   sessions.json of session_digest.py: gives each conversation's row and name
    --hosts           "auto" (default: from the settings), "none", or comma-separated cluster names
    -o / --output-dir folder for the outputs (default <paths.outputs_dir>/weekly-recap/<week>/tokens);
                      --overwrite to replace them

OUTPUTS (in --output-dir; mode 600)
    tokens_week.json  totals, per row, per conversation, per model, per machine;
    tokens_week.txt   the same as a readable table.

USAGE
    python3 claude_tokens_week.py --week 2026-W45 \
        --sessions-json ~/workhub/outputs/weekly-recap/2026-W45/sessions/sessions.json

    Internal remote mode (used by the script itself through SSH; the file is sent on stdin, so
    nothing needs to be installed on the cluster):
        ssh mycluster python3 - --scan-only <start epoch> <end epoch> <projects dir> < claude_tokens_week.py

DEPENDENCIES
    Python >= 3.9, standard library only (remote side: Python >= 3.6 and nothing else).

LIMITS
    - Codex threads are not counted (their logs report running totals per thread, not per request).
    - Conversations in the claude.ai app or on machines not in the settings are not seen.
    - Tokens are not money: the plan counts the four kinds with different weights, so all four
      are reported separately and no single "cost" is invented.

EXIT STATUS
    0 = success; 1 = an output exists without --overwrite, or --week cannot be read;
    2 = bad arguments or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports (standard library only; this file must also run alone on a cluster, see --scan-only)
# ----------------------------------------------------------------------------------------------

# `argparse` reads the command-line options.
import argparse

# `datetime` turns transcript time stamps ("2026-11-09T15:22:04.896Z") into moments.
import datetime

# `glob.glob(pattern, recursive=True)` lists the files matching a pattern with "**".
import glob

# `json` reads transcript lines and writes the outputs.
import json

# `os` gives path helpers and modification times.
import os

# `shlex.quote(text)` makes the projects folder one safe word for the remote shell.
import shlex

# `subprocess` sends this file to a cluster's python3 on stdin (see scan_remote).
import subprocess

# `sys` gives sys.exit, sys.stdout and sys.path.
import sys


# ----------------------------------------------------------------------------------------------
# Scanning transcripts (this part runs locally AND on clusters, so it uses nothing from
# recap_common.py or workhub_config.py)
# ----------------------------------------------------------------------------------------------

# The four token kinds, in the order printed everywhere.
TOKEN_KINDS = ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"]

# The Claude Code projects folder when nothing else is given (remote mode without an argument).
DEFAULT_PROJECTS_DIR = "~/.claude/projects"


def timestamp_to_epoch(text):
    """Turn "2026-11-09T15:22:04.896Z" into Unix seconds; None when unreadable.

    Parameters:
        text (str): an ISO 8601 time stamp, "Z" meaning UTC.

    Returns:
        float or None.
    """
    if not text:
        return None
    try:
        # Python before 3.11 does not read a trailing "Z", so it is replaced by "+00:00" (UTC).
        return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def session_id_of(path):
    """Return the conversation a transcript belongs to.

    Parameters:
        path (str): ".../<folder>/<session>.jsonl" or ".../<folder>/<session>/subagents/agent-x.jsonl".

    Returns:
        tuple (session_id, is_subagent).
    """
    parts = path.split(os.sep)
    if len(parts) >= 3 and parts[-2] == "subagents":
        # The folder above "subagents" is named after the parent conversation.
        return parts[-3], True
    # `os.path.splitext` cuts "abc.jsonl" into ("abc", ".jsonl").
    return os.path.splitext(parts[-1])[0], False


def scan_transcripts(projects_dir, start_epoch, end_epoch):
    """Add up the token use of every request made between start_epoch and end_epoch.

    Parameters:
        projects_dir (str): the Claude Code projects folder, normally ~/.claude/projects
            ("~" is expanded here).
        start_epoch, end_epoch (float): the window in Unix seconds (end excluded).

    Returns:
        dict: {session_id: {"requests", "subagent_requests", <token kinds>, "models": {model: tokens},
               "cwd"}}; tokens per model = the sum of the four kinds.
    """
    pattern = os.path.join(os.path.expanduser(projects_dir), "**", "*.jsonl")
    per_session = {}
    for path in glob.glob(pattern, recursive=True):
        # A file last changed before the week cannot hold a request of the week.
        try:
            if os.path.getmtime(path) < start_epoch:
                continue
        except OSError:
            continue

        session_id, is_subagent = session_id_of(path)
        # requestId -> (usage, model, cwd); a later record of the same request replaces the
        # earlier one, which keeps the final counts of a streamed reply.
        last_record_by_request = {}
        with open(path, encoding="utf-8", errors="replace") as transcript:
            for line in transcript:
                # A cheap text test first: most lines are not assistant replies with usage.
                if '"usage"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                message = record.get("message")
                if record.get("type") != "assistant" or not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                moment = timestamp_to_epoch(record.get("timestamp"))
                if not isinstance(usage, dict) or moment is None or not (start_epoch <= moment < end_epoch):
                    continue
                request_key = record.get("requestId") or message.get("id") or record.get("uuid")
                last_record_by_request[request_key] = (usage, message.get("model") or "unknown",
                                                       record.get("cwd") or "")

        if not last_record_by_request:
            continue
        totals = per_session.setdefault(session_id, {"requests": 0, "subagent_requests": 0,
                                                     "models": {}, "cwd": ""})
        for kind in TOKEN_KINDS:
            totals.setdefault(kind, 0)
        for usage, model, cwd in last_record_by_request.values():
            if is_subagent:
                totals["subagent_requests"] += 1
            else:
                totals["requests"] += 1
                totals["cwd"] = totals["cwd"] or cwd
            request_tokens = 0
            for kind in TOKEN_KINDS:
                # `int(... or 0)`: a missing or null count is 0.
                count = int(usage.get(kind) or 0)
                totals[kind] += count
                request_tokens += count
            totals["models"][model] = totals["models"].get(model, 0) + request_tokens
    return per_session


# ----------------------------------------------------------------------------------------------
# Collecting from every machine
# ----------------------------------------------------------------------------------------------

def scan_remote(host, start_epoch, end_epoch, projects_dir, this_file_text, recap_common):
    """Run this same file on a cluster in --scan-only mode, through the open SSH connection.

    Parameters:
        host (dict): {"name", "ssh_alias"} of recap_common.hosts_from_option.
        start_epoch, end_epoch (float): the window.
        projects_dir (str): the projects folder as written in the settings ("~/..." is expanded
            by the remote python, so it means the cluster's home).
        this_file_text (str): the text of this script, sent on the remote python's stdin.
        recap_common (module): the shared helpers (imported in main).

    Returns:
        tuple (per_session or None, note).
    """
    if not recap_common.connection_is_open(host["ssh_alias"]):
        return None, recap_common.how_to_open(host["ssh_alias"])
    # `python3 -` reads the program from stdin; the words after "-" are its own arguments.
    # `shlex.quote` keeps the folder one word for the remote shell ("~" is NOT expanded by the
    # shell then; the remote python's os.path.expanduser does it).
    remote_line = "python3 - --scan-only %d %d %s" % (int(start_epoch), int(end_epoch), shlex.quote(projects_dir))
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host["ssh_alias"], remote_line]
    # run_command has no stdin argument, so `subprocess` is used directly here.
    try:
        completed = subprocess.run(command, input=this_file_text, capture_output=True, text=True,
                                   timeout=recap_common.COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    if completed.returncode != 0:
        return None, "remote scan failed: " + (completed.stderr.strip().splitlines() or ["?"])[-1][:200]
    try:
        return json.loads(completed.stdout), ""
    except ValueError:
        return None, "remote scan printed no JSON"


def load_session_names(sessions_json_path):
    """Read each conversation's row, name and machine from session_digest.py's sessions.json.

    Returns:
        dict: {session_id: {"task", "title", "system"}}.
    """
    if not sessions_json_path or not os.path.isfile(sessions_json_path):
        return {}
    with open(sessions_json_path, encoding="utf-8") as sessions_file:
        report = json.load(sessions_file)
    names = {}
    for session in report.get("sessions", []):
        names[session.get("id", "")] = {"task": session.get("task") or "",
                                        "title": session.get("title") or "",
                                        "system": session.get("system") or ""}
    return names


def sum_tokens(entry):
    """Total of the four kinds in one dict."""
    return sum(entry.get(kind, 0) for kind in TOKEN_KINDS)


def build_report(per_machine, names, label):
    """Combine the per-machine scans into totals per row, conversation, model and machine.

    Parameters:
        per_machine (dict): {machine: per_session dict from scan_transcripts}.
        names (dict): from load_session_names.
        label (str): the week label.

    Returns:
        dict: the report written to tokens_week.json.
    """
    conversations = []
    by_row = {}
    by_model = {}
    by_machine = {}
    totals = {kind: 0 for kind in TOKEN_KINDS}
    totals["requests"] = 0
    totals["subagent_requests"] = 0

    for machine, per_session in per_machine.items():
        for session_id, counts in per_session.items():
            info = names.get(session_id, {})
            # Conversations the digest did not see (a different week window, or a name) still count.
            row = info.get("task") or ""
            title = info.get("title") or ("(%s, %s)" % (session_id[:8], counts.get("cwd") or "?"))
            conversation = {"id": session_id[:8], "machine": machine, "task": row, "title": title}
            for key in TOKEN_KINDS + ["requests", "subagent_requests"]:
                conversation[key] = counts.get(key, 0)
                totals[key] += counts.get(key, 0)
            conversation["total"] = sum_tokens(conversation)
            conversations.append(conversation)

            # Orchestrator conversations record no row by design (work harness); they get their
            # own line, so "no row" means real work that was never filed.
            if row:
                row_key = row
            elif "[ORCH]" in title or "orchestrator" in title.lower():
                row_key = "ORCH"
            else:
                row_key = "no row"
            row_entry = by_row.setdefault(row_key, {kind: 0 for kind in TOKEN_KINDS})
            row_entry.setdefault("conversations", 0)
            row_entry["conversations"] += 1
            for kind in TOKEN_KINDS:
                row_entry[kind] += counts.get(kind, 0)

            by_machine[machine] = by_machine.get(machine, 0) + conversation["total"]
            for model, tokens in counts.get("models", {}).items():
                by_model[model] = by_model.get(model, 0) + tokens

    for entry in by_row.values():
        entry["total"] = sum_tokens(entry)
    conversations.sort(key=lambda conversation: conversation["total"], reverse=True)
    totals["total"] = sum_tokens(totals)
    return {"schema": "weekly-recap-tokens/1", "week": label, "totals": totals,
            "by_row": by_row, "by_model": by_model, "by_machine": by_machine,
            "conversations": conversations}


def millions(count):
    """Return a token count in millions with one decimal ("12.3 M")."""
    return "%.1f M" % (count / 1e6)


def format_report(report, notes):
    """Readable version of the report (tokens_week.txt)."""
    totals = report["totals"]
    grand = totals["total"] or 1
    lines = ["Claude Code tokens, week %s" % report["week"],
             "Total %s in %d requests (+ %d by subagents): input %s, cache write %s, cache read %s, output %s"
             % (millions(totals["total"]), totals["requests"], totals["subagent_requests"],
                millions(totals["input_tokens"]), millions(totals["cache_creation_input_tokens"]),
                millions(totals["cache_read_input_tokens"]), millions(totals["output_tokens"])),
             ""]
    for note in notes:
        lines.append("Note: " + note)
    lines.append("")
    lines.append("PER ROW (share of all tokens | cache write | output | conversations)")
    rows = sorted(report["by_row"].items(), key=lambda item: item[1]["total"], reverse=True)
    for row, entry in rows:
        lines.append("  %-8s %5.1f %%  %9s | write %8s | out %7s | %d conv."
                     % (row, 100.0 * entry["total"] / grand, millions(entry["total"]),
                        millions(entry["cache_creation_input_tokens"]), millions(entry["output_tokens"]),
                        entry["conversations"]))
    lines.append("")
    lines.append("PER MODEL: " + ", ".join("%s %s" % (model, millions(tokens)) for model, tokens in
                                           sorted(report["by_model"].items(), key=lambda item: -item[1])))
    lines.append("PER MACHINE: " + ", ".join("%s %s" % (machine, millions(tokens)) for machine, tokens in
                                             sorted(report["by_machine"].items(), key=lambda item: -item[1])))
    lines.append("")
    lines.append("TOP 15 CONVERSATIONS")
    for conversation in report["conversations"][:15]:
        lines.append("  %9s  %s %s  %s" % (millions(conversation["total"]), conversation["machine"],
                                          conversation["id"], conversation["title"][:70]))
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def main():
    """Scan this machine and the reachable clusters, then write the outputs."""
    # Remote mode: print the scan of this machine as JSON and stop (no other module needed).
    # Forms: --scan-only START END [PROJECTS_DIR]; without the folder, $CLAUDE_CONFIG_DIR/projects
    # or ~/.claude/projects is used.
    if len(sys.argv) in (4, 5) and sys.argv[1] == "--scan-only":
        if len(sys.argv) == 5:
            projects_dir = sys.argv[4]
        elif os.environ.get("CLAUDE_CONFIG_DIR"):
            projects_dir = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects")
        else:
            projects_dir = DEFAULT_PROJECTS_DIR
        json.dump(scan_transcripts(projects_dir, float(sys.argv[2]), float(sys.argv[3])), sys.stdout)
        return

    # Local mode needs the shared helpers of the skill (same folder): settings, week window,
    # connection checks, private files.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import recap_common
    import workhub_config

    parser = argparse.ArgumentParser(description="Claude Code tokens per Work Hub row for one "
                                                 "Monday-to-Sunday week (this machine + reachable clusters).")
    parser.add_argument("--week", default="last", help="last (default), this, YYYY-Www or YYYY-MM-DD")
    parser.add_argument("--sessions-json", default="", help="sessions.json of session_digest.py (rows and names)")
    parser.add_argument("--hosts", default="auto",
                        help="clusters through open SSH connections: 'auto' (default: from the settings), "
                             "'none', or comma-separated names")
    parser.add_argument("-o", "--output-dir", default="",
                        help="folder for tokens_week.json and .txt "
                             "(default <paths.outputs_dir>/weekly-recap/<week>/tokens)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    parser.add_argument("--print", action="store_true", help="also print the table")
    args = parser.parse_args()

    config = recap_common.load_settings()
    timezone = recap_common.owner_timezone(config)
    try:
        start, end, label = recap_common.week_window(args.week, timezone)
    except ValueError as error:
        print("claude_tokens_week.py: %s" % error, file=sys.stderr)
        sys.exit(1)
    output_dir = args.output_dir or recap_common.output_dir_for(config, recap_common.week_id(start), "tokens")
    json_path = os.path.join(output_dir, "tokens_week.json")
    text_path = os.path.join(output_dir, "tokens_week.txt")
    for path in (json_path, text_path):
        if os.path.exists(path) and not args.overwrite:
            print("claude_tokens_week.py: %s exists; pass --overwrite to replace it" % path, file=sys.stderr)
            sys.exit(1)

    start_epoch = start.timestamp()
    end_epoch = end.timestamp()
    this_system = workhub_config.system_name(config)
    projects_setting = str(workhub_config.get_setting(config, "conversations.claude_projects_dir",
                                                      default=DEFAULT_PROJECTS_DIR))

    # This machine. When workstations share this home folder, this one scan covers them all;
    # the label names them ("ws01+ws02").
    machine_label = recap_common.this_machine_label(config, this_system)
    local_projects_dir = workhub_config.expand_path(projects_setting)
    per_machine = {machine_label: scan_transcripts(local_projects_dir, start_epoch, end_epoch)}
    notes = []

    # The clusters that keep their own transcripts.
    with open(os.path.abspath(__file__), encoding="utf-8") as this_file:
        this_file_text = this_file.read()
    hosts = recap_common.hosts_from_option(args.hosts, config, this_system)
    for host in hosts:
        per_session, note = scan_remote(host, start_epoch, end_epoch, projects_setting, this_file_text, recap_common)
        if per_session is None:
            notes.append("%s not counted: %s" % (host["name"], note))
        else:
            per_machine[host["name"]] = per_session
    notes.append("Codex threads are not counted.")

    report = build_report(per_machine, load_session_names(args.sessions_json), label)
    report["notes"] = notes
    text = format_report(report, notes)
    recap_common.prepare_output_dir(output_dir)
    recap_common.write_private_text(json_path, json.dumps(report, indent=1) + "\n", args.overwrite)
    recap_common.write_private_text(text_path, text, args.overwrite)
    if args.print:
        print(text)
    print("%s tokens in %d conversation(s); table %s" % (millions(report["totals"]["total"]),
                                                        len(report["conversations"]), text_path))


if __name__ == "__main__":
    main()
