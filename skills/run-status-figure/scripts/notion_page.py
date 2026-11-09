#!/usr/bin/env python3
"""Write the Notion page (properties + content) of a status figure, from its status JSON file.

    python notion_page.py status.json --image file-upload://<png id> --pdf file-upload://<pdf id> -o page.json
    python notion_page.py status.json --image ... --pdf ... --headline "model B leads on every fold so far"

Prints (or writes with -o) one JSON object, ready for the Notion create-pages tool (references/notion.md):

    {"research_log": {"publish": true, "reason": "", "parent": {"type": "data_source_id", "data_source_id": "…"}},
     "properties": {...}, "icon": "📊", "content": "<Notion-flavored Markdown>"}

"research_log" comes from the workhub-claude settings file: the parent is the data source in
notion.research_log.data_source. When the Research log is switched off (notion.research_log.enabled false) or not
built yet (data_source empty), "publish" is false and "reason" says why: the figure is then not published, and the
caller says so in one line. Without --image and --pdf the page refers to the local files only: use that when no
Notion connection is available, keep the output next to the figure (page.json) and publish it later from a session
that has one.

The page is the text twin of the figure: header lines, the local originals, the figure, a summary, one table per
part of the figure (stages, results), where it runs, networks, milestones, decisions, the PDF, and a "Personal
notes" section. Needs only the standard library, render_status.py (for the stage arithmetic; it imports
matplotlib), collect_env.py (for the machine label) and workhub_config.py (the settings reader), all in this folder.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import argparse
import json
import re
import sys
from pathlib import Path

from collect_env import machine_label
from render_status import GLYPH, counts_gpu_time, fmt_hours, fmt_when, load_status, stage_summary
from workhub_config import data_source_id, get_setting, load_config, time_zone

SKILL_PATH = Path(__file__).resolve().parents[1]


FILE_NAME = re.compile(r"(?<![\w/.-])([\w./-]+\.(?:sh|py|R|md|json|yaml|yml|csv|tsv|txt|log|h5|png|pdf|tif))\b")


def esc(text):
    """Escape the characters Notion-flavored Markdown treats as syntax (outside inline code), and put file names
    in inline code: Notion would otherwise turn 'run.sh' into a link to http://run.sh."""
    parts = FILE_NAME.split(str(text))
    return "".join(f"`{part}`" if index % 2 else re.sub(r"([\\*~`$\[\]<>{}|^])", r"\\\1", part)
                   for index, part in enumerate(parts))


def code(text):
    return f"`{text}`"


def table(header, rows):
    lines = ['<table header-row="true">']
    for row in [header] + rows:
        lines.append("\t<tr>")
        lines += [f"\t\t<td>{cell}</td>" for cell in row]
        lines.append("\t</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def research_log_target(config):
    """{"publish", "reason", "parent"} for the Research log, from the settings (see the module docstring)."""
    enabled = get_setting(config, "notion.research_log.enabled", default=True)
    data_source = get_setting(config, "notion.research_log.data_source", default="")
    if enabled is False:
        return {"publish": False, "reason": "the Research log is switched off (notion.research_log.enabled is "
                                            "false in the settings)", "parent": None}
    if not data_source:
        return {"publish": False, "reason": "no Research log is set up yet (notion.research_log.data_source is "
                                            "empty in the settings)", "parent": None}
    parent = {"type": "data_source_id", "data_source_id": data_source_id(data_source)}
    return {"publish": True, "reason": "", "parent": parent}


def aware(moment, zone_name):
    """The naive status time `moment`, read as a time in the IANA zone `zone_name`, as an ISO string with its
    offset ("2026-01-15T14:00:00+00:00"). Python 3.8 (no zoneinfo) or an unknown zone: the machine's zone."""
    try:
        from zoneinfo import ZoneInfo
        return moment.replace(tzinfo=ZoneInfo(zone_name)).isoformat(timespec="seconds")
    except Exception:                                   # ImportError (3.8) or ZoneInfoNotFoundError
        return moment.astimezone().isoformat(timespec="seconds")


def page(status_path, image, pdf, headlines, config=None):
    """Build the page dict for one status file. `config` = the settings dict ({} works: defaults are used)."""
    config = load_config(required=False) if config is None else config
    status, now, _ = load_status(status_path)
    environment = status.get("environment", {})
    notion = status.get("notion", {})
    system = environment.get("system", "")
    folder = f"{system}:{Path(status_path).resolve().parent}" if system else str(Path(status_path).resolve().parent)
    timezone = notion.get("timezone_iana") or time_zone(config)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    name = f"{stamp} — {notion.get('name', status['title'] + ' run status')}"
    cluster = environment.get("cluster", "")
    # "Clusters" (a multi-select of machine labels): the status file's own list when it names several machines
    # (a machine view), else the label of the machine the status was made on.
    clusters = notion.get("clusters") or ([machine_label(config, system)] if system else [])
    properties = {
        "Name": name, "Type": "Status",
        "date:Date:start": aware(now, timezone), "date:Date:is_datetime": 1,
        "Clusters": json.dumps(clusters),
        "Source key": f"{notion.get('source_key_prefix', 'status')}/{Path(status_path).resolve().parent.name}",
    }
    if notion.get("project"):                            # an existing option of the Research log's Project
        properties["Project"] = notion["project"]

    out = []
    mention = f'<mention-date start="{now:%Y-%m-%d}" startTime="{now:%H:%M}" timeZone="{timezone}"/>'
    out.append(f"Date: {mention} · Cluster: {esc(cluster or system)} ({esc(system)}) · Type: Status")
    project_line = f"Project: {esc(properties.get('Project', '–'))}"
    if notion.get("project_key"):
        project_line += f" (key {code(notion['project_key'])})"
    out.append(project_line + f" · {esc(status['title'])}")
    quote = [f"Local originals: {code(folder + '/status.png')} (also {code('status.pdf')} and {code('status.json')})"]
    if notion.get("remake"):
        quote.append(f"Remake it: {code(notion['remake'])}")
    quote.append(f"Made with the run-status-figure skill: {code(f'{system}:{SKILL_PATH}' if system else SKILL_PATH)}")
    quote.append("Redaction: no participant or case identifiers.")
    out.append("> " + "<br>".join(quote))
    if image:
        out.append(f'<image src="{image}">Run status, {esc(stamp)} {esc(status.get("timezone", ""))}</image>')

    # Summary
    runs = [(stage, run) for stage in status["stages"] for run in stage["runs"]]
    done = sum(run["status"] == "done" for _, run in runs)
    running = [(stage, run) for stage, run in runs if run["status"] == "running"]
    queued = sum(run["status"] == "queued" for _, run in runs)
    failed = sum(run["status"] in ("failed", "stopped") for _, run in runs)
    gpu_stages = [stage for stage in status["stages"] if counts_gpu_time(stage)]   # "gpu_time": false left out
    left = sum(stage_summary(stage, now)[2] for stage in gpu_stages)
    used = sum(stage_summary(stage, now)[1] for stage in gpu_stages)
    ends = [run["_end"] for _, run in runs if run["_end"]]
    out.append("## Summary")
    out.append(f"- **Done:** {done} of {len(runs)} runs ({fmt_hours(used)} of GPU time).")
    for stage, run in running:
        note = f" ({esc(run['note'])})" if run.get("note") else ""
        session = f", in {esc(stage['session'])}" if stage.get("session") else ""
        out.append(f"- **Running now:** {esc(stage['name'])}, {esc(run.get('label', run['name']))}{note}{session}.")
    if queued:
        out.append(f"- **Left:** {queued} queued runs, about {fmt_hours(left)} of GPU time. "
                   f"Expected end: **{esc(max(ends).strftime('%a %d %b, about %H:%M'))}** (estimate).")
    else:
        out.append("- **Left:** nothing queued.")
    if failed:
        out.append(f"- **Failed or stopped:** {failed} run{'s' if failed != 1 else ''} (see the table).")
    for headline in list(status.get("headlines", [])) + headlines:
        out.append(f"- {esc(headline)}")

    # Stages
    out.append("## Runs by stage")
    rows = []
    for stage in status["stages"]:
        count, stage_used, stage_left, stage_end = stage_summary(stage, now)
        states = ", ".join(f"{count[s]} {s}" for s in ("done", "running", "queued", "failed", "stopped") if count[s])
        end = ("" if stage_left else "done ") + fmt_when(stage_end, now) if stage_end else "–"
        used_cell = fmt_hours(stage_used) if stage_used else "–"
        left_cell = f"≈ {fmt_hours(stage_left)}" if stage_left else "–"
        if not counts_gpu_time(stage):                       # a power cut or a copy: its hours are not GPU time
            used_cell, left_cell = "no GPU use", "–"
        rows.append([esc(stage["name"]), states, used_cell, left_cell, ("≈ " if stage_left else "") + end,
                     esc(stage.get("session", ""))])
    out.append(table(["Stage", "Runs", "GPU time used", "Left (estimate)", "Ends", "Session"], rows))
    if status.get("estimate_method"):
        out.append(f"Estimates: {esc(status['estimate_method'])}.")

    # Environment
    out.append("## Where it runs")
    machine = ", ".join(esc(x) for x in (f"{system} ({cluster})" if cluster else system, environment.get("gpu"),
                                          f"user {environment['user']}" if environment.get("user") else "") if x)
    out.append(f"- **System:** {machine}")
    if environment.get("working_dirs"):
        out.append("- **Working folders:**")
        out += [f"\t- {code(d['path'])}: {esc(d.get('role', ''))}" for d in environment["working_dirs"]]
    if environment.get("sessions"):
        out.append("- **Sessions:**")
        out += [f"\t- {esc(s.get('kind', 'tmux'))} {code(s['name'])}: {esc(s.get('state', ''))}"
                f"{' — ' + esc(s['role']) if s.get('role') else ''}" for s in environment["sessions"]]

    if status.get("models"):
        out.append("## Networks")
        for model in status["models"]:
            params = f" ({esc(model['params'])})" if model.get("params") else ""
            out.append(f"- **{esc(model['name'])}**{params}: " + " · ".join(esc(line) for line in model.get("lines", [])))

    results = status.get("results")
    if results:
        out.append(f"## {esc(results.get('title', 'Results so far'))}")
        for facet in results["facets"]:
            columns = list(dict.fromkeys((p["series"], p.get("variant")) for p in facet["points"]))
            row_names = list(dict.fromkeys(p["row"] for p in facet["points"]))
            value = {(p["row"], p["series"], p.get("variant")): p["value"] for p in facet["points"]}
            header = [esc(facet["title"])] + [esc(f"{s} {v}" if v else s) for s, v in columns]
            body = [[esc(r)] + [f"{value[(r, s, v)]:.3f}" if (r, s, v) in value else "–" for s, v in columns]
                    for r in row_names]
            out.append(table(header, body))
            out += [esc(summary["text"]) for summary in facet.get("summaries", [])]
        if results.get("metric"):
            out.append(f"Metric: {esc(results['metric'])}.")

    milestones = status.get("milestones", [])
    if any(m.get("status", "done") == "done" for m in milestones):
        out.append("## Done so far")
        out += [f"- {esc(m.get('date', ''))}: {esc(m['text'])}" for m in milestones if m.get("status", "done") == "done"]
    open_items = [m for m in milestones if m.get("status") in ("blocked", "pending", "waiting")]
    if open_items:
        out.append("## Decisions and waiting items")
        out += [f"- {GLYPH.get(m['status'], '•')} **{esc(m.get('date', ''))}:** {esc(m['text'])}" for m in open_items]
    if status.get("notes"):
        out.append("## Notes")
        out += [f"- {esc(note)}" for note in status["notes"]]
    if pdf:
        out.append("## Files")
        out.append(f'<pdf src="{pdf}">Vector version of the figure (PDF)</pdf>')
    out.append("---")
    out.append("## Personal notes")
    out.append("*Reserved for your own notes. Automated updates never overwrite this section.*")
    return {"research_log": research_log_target(config), "properties": properties, "icon": "📊",
            "content": "\n".join(out)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("status", type=Path)
    parser.add_argument("--image", help="markdown_source of the uploaded PNG (file-upload://...).")
    parser.add_argument("--pdf", help="markdown_source of the uploaded PDF (file-upload://...).")
    parser.add_argument("--headline", action="append", default=[], help="Extra summary bullet (repeatable).")
    parser.add_argument("-o", "--output", type=Path, help="Write the JSON here instead of printing it.")
    args = parser.parse_args()
    built = page(args.status, args.image, args.pdf, args.headline)
    result = json.dumps(built, indent=1, ensure_ascii=False)
    if args.output:
        args.output.write_text(result)
        print(args.output)
    else:
        print(result)
    if not built["research_log"]["publish"]:
        print("Research log publishing skipped: " + built["research_log"]["reason"], file=sys.stderr)


if __name__ == "__main__":
    main()
