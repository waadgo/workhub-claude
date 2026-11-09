#!/usr/bin/env python3
"""
week_timeline.py - the week's compute as a timeline figure (PNG + PDF): one lane per cluster and
piece of work, one bar per Slurm job, plus the workstation runs Claude adds by hand.

PURPOSE
    Every timeline or "what did my computers do" answer comes with a run-status-figure PNG. The
    weekly recap's figure shows Monday 00:00 to Sunday 24:00 (owner's time zone): which cluster
    ran what, for which row (Task ID such as TK-42), how long, and what failed. This script turns
    slurm_week.json (from slurm_week.py) and an optional list of workstation runs into the status
    JSON format of the `run-status-figure` skill (references/status_schema.md there), then calls
    that skill's renderer, so the figure looks like every other status figure.

LANES
    One lane per cluster and row, named with the row's biggest project label ("mycluster · TK-42 ·
    Project A"; without a project rule the label is left out: "mycluster · TK-42"); jobs without a
    row get one lane per cluster and label ("mycluster · other: demo"). The lanes are ordered by
    size (core-hours + 50 x GPU-hours, the weight used in slurm_week.py); after --max-lanes lanes,
    the smaller ones of each cluster are merged into "<cluster> · other".
    Jobs that only waited (never started inside the week) are left out of the figure; they are
    in slurm_week.txt.

BAR STATES (run-status-figure names)
    COMPLETED -> done; FAILED, TIMEOUT, OUT_OF_MEMORY, NODE_FAIL ... -> failed (red, with ✕);
    CANCELLED -> stopped (half strength); still running at Sunday 24:00 -> running (the figure's
    "now" line is the end of the week, so its label reads "▶ now: still running at week end").

WORKSTATION RUNS (--extra-runs, optional)
    Workstations (machines.workstations of the settings) have no accounting database, so their
    runs come from the orchestrator log and the topic cards, written by Claude into a JSON list:
        [{"lane": "ws02 GPU · TK-42 Project A", "label": "night part", "status": "done",
          "start": "2026-11-04T22:29:25", "end": "2026-11-05T02:48:27", "gpu": true}]
    Times are in the owner's time zone, without offset. "gpu": false marks CPU or copy work.

INPUTS
    --slurm-json      slurm_week.json (default <paths.outputs_dir>/weekly-recap/<week>/slurm/slurm_week.json)
    --week            which week, to find that default slurm_week.json (default last; not
                      needed when --slurm-json is given)
    --extra-runs      optional JSON list of workstation runs (format above)
    --max-lanes       default 16
    --renderer        default ../../run-status-figure/scripts/render_status.py (next to this skill)
    --python          the Python that runs the renderer (needs matplotlib), default this one
    -o / --output-dir folder for week_timeline.json, .png and .pdf (default
                      <paths.outputs_dir>/weekly-recap/<week of slurm_week.json>/figure);
                      --overwrite to replace them

USAGE
    python3 week_timeline.py --week 2026-W45 \
        --extra-runs ~/workhub/outputs/weekly-recap/2026-W45/extra_runs.json

DEPENDENCIES
    Python >= 3.9 (this script); the renderer needs matplotlib >= 3.5.

EXIT STATUS
    0 = figure written; 1 = missing input, existing output without --overwrite, or the renderer
    failed; 2 = bad arguments or no settings file.

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ----------------------------------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------------------------------

# `argparse` reads the options; `datetime` reads and clips times; `json` reads slurm_week.json
# and writes the status JSON; `os` gives paths; `subprocess` runs the renderer; `sys` gives exit.
import argparse
import datetime
import json
import os
import subprocess
import sys

# `ZoneInfo` gives the time zone's short name (e.g. WET / WEST) for the figure header.
from zoneinfo import ZoneInfo

# The skill's shared helpers (same folder): settings, week window, private files; and the
# settings reader itself (workhub_config.time_zone gives owner.time_zone).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recap_common  # noqa: E402
import workhub_config  # noqa: E402


# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------

# The renderer of the run-status-figure skill: <skills>/run-status-figure/scripts/render_status.py,
# found from this file's place (<skills>/weekly-recap/scripts/week_timeline.py).
SKILLS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_RENDERER = os.path.join(SKILLS_DIR, "run-status-figure", "scripts", "render_status.py")

# Slurm states drawn as failed bars (same list as slurm_week.BAD_STATES).
FAILED_STATES = {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED"}

# Labels that slurm_week.py gives when no project rule matched start with this text.
NO_RULE_PREFIX = "other: "


# ----------------------------------------------------------------------------------------------
# Building the status JSON
# ----------------------------------------------------------------------------------------------

def local_text(moment):
    """Return a moment as the renderer expects it: local time without offset, "2026-11-05T18:51:00"."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def hours_text(hours):
    """Return hours for a lane label: one decimal below 10 h ("0.5"), whole numbers above ("64").

    Whole numbers alone would print a 0.5 GPU-h job as "0 GPU-h", which reads as "no GPU used".
    """
    if hours < 10:
        return "%.1f" % hours
    return "%.0f" % hours


def bar_status(job):
    """Map a job's Slurm state and week status to a run-status-figure state."""
    if job["week_status"] == "running at week end":
        return "running"
    if job["state"] in FAILED_STATES:
        return "failed"
    if job["state"] == "CANCELLED":
        return "stopped"
    return "done"


def lane_key(job):
    """Lane of a job: one per (cluster, row); jobs without a row get one per (cluster, label).

    Returns:
        tuple: ("mycluster", "TK-80") or ("mycluster", "label: Project A").
    """
    if job["task_id"]:
        return (job["cluster"], job["task_id"])
    return (job["cluster"], "label: " + job["label"])


def lane_title(cluster, key, label_hours):
    """Lane name shown in the figure.

    Parameters:
        cluster (str): e.g. "mycluster".
        key (str): the row ("TK-80") or "label: <label>".
        label_hours (dict): {label: core-h + 50 x GPU-h} of the lane's jobs.

    Returns:
        str: "mycluster · TK-80 · Project A" (a row with its biggest rule label),
            "mycluster · TK-80" (a row whose jobs matched no project rule), or
            "mycluster · Project A" (no row).
    """
    if key.startswith("label: "):
        return "%s · %s" % (cluster, key[len("label: "):])
    # `max(..., key=label_hours.get)`: the label with the most hours in this row's lane.
    main_label = max(label_hours, key=label_hours.get)
    if main_label.startswith(NO_RULE_PREFIX):
        # No project rule (or no rules file): the row alone names the lane; its Work Hub
        # Project is given in the recap text, which reads it from Notion.
        return "%s · %s" % (cluster, key)
    return "%s · %s · %s" % (cluster, key, main_label)


def build_lanes(jobs, week_start, week_end, max_lanes):
    """Group the started jobs into lanes, biggest first, merging the small ones.

    Parameters:
        jobs (list of dict): slurm_week.json's jobs (times as ISO texts with offsets).
        week_start, week_end (datetime.datetime): the week.
        max_lanes (int): lanes kept before merging.

    Returns:
        list of dict: {"name", "gpu", "core_hours", "gpu_hours", "runs": [...]} in drawing order.
    """
    lanes = {}
    for job in jobs:
        if not job["start"] or job["hours_in_week"] <= 0:
            continue
        # Clip the bar to the week (a job started the Sunday before is drawn from Monday 00:00).
        start = max(datetime.datetime.fromisoformat(job["start"]), week_start)
        end = datetime.datetime.fromisoformat(job["end"]) if job["end"] else week_end
        end = min(end, week_end)
        status = bar_status(job)
        run = {"name": "%s %s" % (job["job_id"], job["name"]), "label": job["name"], "status": status,
               "start": local_text(start)}
        if status == "running":
            run["estimate_end"] = local_text(week_end)
            run["note"] = "still running at week end"
        else:
            run["end"] = local_text(end)
        key = lane_key(job)
        lane = lanes.setdefault(key, {"cluster": job["cluster"], "key": key[1], "label_hours": {},
                                      "gpu": False, "core_hours": 0.0, "gpu_hours": 0.0, "runs": []})
        lane["runs"].append(run)
        weight = job["core_hours"] + 50 * job["gpu_hours"]
        lane["label_hours"][job["label"]] = lane["label_hours"].get(job["label"], 0.0) + weight
        lane["core_hours"] += job["core_hours"]
        lane["gpu_hours"] += job["gpu_hours"]
        lane["gpu"] = lane["gpu"] or job["gpus"] > 0

    for lane in lanes.values():
        lane["name"] = lane_title(lane["cluster"], lane["key"], lane["label_hours"])

    # Biggest first; the ones after max_lanes go into one "other" lane per cluster.
    ordered = sorted(lanes.values(), key=lambda lane: lane["core_hours"] + 50 * lane["gpu_hours"], reverse=True)
    kept = ordered[:max_lanes]
    merged = {}
    for lane in ordered[max_lanes:]:
        other = merged.setdefault(lane["cluster"], {"name": "%s · other" % lane["cluster"], "cluster": lane["cluster"],
                                                     "gpu": False, "core_hours": 0.0, "gpu_hours": 0.0, "runs": []})
        other["runs"].extend(lane["runs"])
        other["core_hours"] += lane["core_hours"]
        other["gpu_hours"] += lane["gpu_hours"]
        other["gpu"] = other["gpu"] or lane["gpu"]
    return kept + list(merged.values())


def extra_lanes(extra_runs):
    """Turn the hand-written workstation runs into lanes (one per "lane" value, in order of appearance)."""
    lanes = {}
    for item in extra_runs:
        lane = lanes.setdefault(item["lane"], {"name": item["lane"], "gpu": bool(item.get("gpu", True)),
                                               "core_hours": 0.0, "gpu_hours": 0.0, "runs": []})
        run = {"name": item.get("label", item["lane"]), "label": item.get("label", ""),
               "status": item.get("status", "done"), "start": item["start"]}
        if run["status"] == "running":
            run["estimate_end"] = item["end"]
        else:
            run["end"] = item["end"]
        lane["runs"].append(run)
    return list(lanes.values())


def build_status(slurm_report, extra_runs, max_lanes, zone_name):
    """Build the run-status/1 JSON of the week (format: run-status-figure/references/status_schema.md).

    Parameters:
        slurm_report (dict): slurm_week.json, read back.
        extra_runs (list of dict): workstation runs (format in the file header).
        max_lanes (int): Slurm lanes before merging.
        zone_name (str): the owner's IANA zone (for the zone's short name in the header).

    Returns:
        dict: the status JSON.
    """
    week = slurm_report["week"]
    week_start = datetime.datetime.fromisoformat(week["start"])
    week_end = datetime.datetime.fromisoformat(week["end"])

    lanes = extra_lanes(extra_runs) + build_lanes(slurm_report["jobs"], week_start, week_end, max_lanes)
    stages = []
    for lane in lanes:
        stage = {"name": lane["name"], "runs": lane["runs"]}
        # The size goes under the lane name, where the renderer prints the "session" field.
        if lane["core_hours"] or lane["gpu_hours"]:
            # The renderer already prints "<n> runs"; the hours are added after it
            # (hours_text: one decimal below 10 h, e.g. "64 core-h · 0.5 GPU-h").
            stage["session"] = "%s core-h · %s GPU-h" % (hours_text(lane["core_hours"]),
                                                          hours_text(lane["gpu_hours"]))
        if not lane["gpu"]:
            # CPU work: shown, but kept out of the figure's GPU-time column.
            stage["gpu_time"] = False
        stages.append(stage)

    reached = [c["cluster"] for c in slurm_report["clusters"] if c["reached"]]
    missing = [c["cluster"] for c in slurm_report["clusters"] if not c["reached"]]
    notes = ["'now' = the end of the week (Sunday 24:00); bars are clipped to Monday 00:00 - Sunday 24:00.",
             "Clusters read: %s%s." % (", ".join(reached) or "none",
                                       ("; NOT read: " + ", ".join(missing)) if missing else "")]
    for cluster in slurm_report["clusters"]:
        if cluster["reached"] and cluster["totals"]["jobs"]:
            totals = cluster["totals"]
            notes.append("%s: %d jobs, %s core-h, %s GPU-h, %s GPU-h in badly ended jobs."
                         % (cluster["cluster"], totals["jobs"], hours_text(totals["core_hours"]),
                            hours_text(totals["gpu_hours"]), hours_text(totals["bad_gpu_hours"])))
    return {"schema": "run-status/1",
            "title": "Week %s: what the clusters and workstations ran" % week["label"],
            "subtitle": "One lane per cluster and Work Hub row (or project), one bar per job; red = failed or timed out.",
            # The zone's short name (e.g. "WET"); the stored offset alone would print as "UTC+00:00".
            "timezone": week_end.astimezone(ZoneInfo(zone_name)).tzname() or "",
            "generated": local_text(week_end),
            "window": "all",
            "environment": {"system": slurm_report["system"], "cluster": ", ".join(reached), "user": "",
                            "working_dirs": [], "sessions": []},
            "stages": stages,
            "estimate_method": "no estimates: a record of finished work (Slurm accounting, sacct)",
            "notes": notes}


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def main():
    """Build the status JSON and render the figure."""
    parser = argparse.ArgumentParser(description="The week's compute as a run-status-figure timeline (PNG + PDF).")
    parser.add_argument("--week", default="last",
                        help="which week, to find the default files: last (default), this, YYYY-Www or YYYY-MM-DD")
    parser.add_argument("--slurm-json", default="",
                        help="slurm_week.json from slurm_week.py (default: the week's slurm folder)")
    parser.add_argument("--extra-runs", default="", help="optional JSON list of workstation runs")
    parser.add_argument("--max-lanes", type=int, default=16, help="Slurm lanes before merging (default 16)")
    parser.add_argument("--renderer", default=DEFAULT_RENDERER, help="render_status.py (default: %(default)s)")
    parser.add_argument("--python", default=sys.executable, help="Python with matplotlib for the renderer")
    parser.add_argument("-o", "--output-dir", default="",
                        help="folder for week_timeline.json/.png/.pdf (default: the week's figure folder)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    args = parser.parse_args()

    config = recap_common.load_settings()
    timezone = recap_common.owner_timezone(config)

    # The input: the given slurm_week.json, or the one in the week's default folder.
    slurm_json = args.slurm_json
    if not slurm_json:
        try:
            start, _, _ = recap_common.week_window(args.week, timezone)
        except ValueError as error:
            print("week_timeline.py: %s" % error, file=sys.stderr)
            sys.exit(1)
        slurm_json = os.path.join(recap_common.output_dir_for(config, recap_common.week_id(start), "slurm"),
                                  "slurm_week.json")
    for path in (slurm_json, args.renderer):
        if not os.path.isfile(path):
            print("week_timeline.py: %s not found" % path, file=sys.stderr)
            sys.exit(1)
    with open(slurm_json, encoding="utf-8") as slurm_file:
        slurm_report = json.load(slurm_file)

    # The outputs: by default in the figure folder of the week that slurm_week.json describes.
    output_dir = args.output_dir or recap_common.output_dir_for(config, slurm_report["week"]["id"], "figure")
    status_path = os.path.join(output_dir, "week_timeline.json")
    figure_stem = os.path.join(output_dir, "week_timeline")
    for path in (status_path, figure_stem + ".png", figure_stem + ".pdf"):
        if os.path.exists(path) and not args.overwrite:
            print("week_timeline.py: %s exists; pass --overwrite to replace it" % path, file=sys.stderr)
            sys.exit(1)

    extra_runs = []
    if args.extra_runs:
        with open(args.extra_runs, encoding="utf-8") as extra_file:
            extra_runs = json.load(extra_file)

    zone_name = workhub_config.time_zone(config)
    status = build_status(slurm_report, extra_runs, args.max_lanes, zone_name)
    recap_common.prepare_output_dir(output_dir)
    recap_common.write_private_text(status_path, json.dumps(status, indent=1) + "\n", args.overwrite)

    # The renderer writes <stem>.png and <stem>.pdf.
    completed = subprocess.run([args.python, args.renderer, status_path, "-o", figure_stem],
                               capture_output=True, text=True)
    if completed.returncode != 0:
        print("week_timeline.py: the renderer failed:\n" + completed.stderr[-2000:], file=sys.stderr)
        sys.exit(1)
    # The figure files are private like the rest (they show job names and folders).
    for suffix in (".png", ".pdf"):
        if os.path.exists(figure_stem + suffix):
            os.chmod(figure_stem + suffix, 0o600)
    print("%d lane(s); figure %s.png and .pdf" % (len(status["stages"]), figure_stem))


if __name__ == "__main__":
    main()
