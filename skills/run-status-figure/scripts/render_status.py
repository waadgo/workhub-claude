#!/usr/bin/env python3
"""Render a run-status figure from a status JSON file: what is done, what runs now, what is left.

    python render_status.py status.json                  # status.png and status.pdf next to the JSON file
    python render_status.py status.json -o out/figure    # out/figure.png and out/figure.pdf
    python render_status.py --example                    # the bundled example (assets/example_status.json)
    python render_status.py --example -o ~/tmp/example   # the same, written elsewhere

The figure has:
  - a header: title, time of the status, system, GPU, working folders, tmux/Slurm sessions and their state;
  - a timeline: one row per stage (a group of runs done one after the other), one bar per run: solid = done,
    solid + light = running (elapsed + estimated rest), light = queued (estimated), red = failed; a "now" line.
    It shows the past 12 h and the next 7 days ("window" in the JSON changes that; "window": "all" fits every run);
  - next to it, per stage: runs done/running/queued, GPU time used and left, expected end;
  - below: what was done so far (milestones), the networks, and the results so far (dot plot), each optional.

Inputs: one status JSON file (format: references/status_schema.md). Outputs: <base>.png (200 dpi) and <base>.pdf;
the paths are printed. Without -o, the figure goes next to the JSON file; for --example without -o it goes to
<paths.outputs_dir>/run-status-figure/example/ from the workhub-claude settings file (read with the bundled
workhub_config.py), never into the skill folder. Existing figures with the same name are replaced: a status
figure is a view of its JSON file, which stays untouched.

Needs only Python >= 3.8 and matplotlib >= 3.5.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import argparse
import json
import textwrap
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
FIG_WIDTH, FIG_HEIGHT = 16.0, 12.0          # inches
LEFT, RIGHT = 0.025, 0.975                  # figure margins (fractions)

# Colors: the validated default palette of the dataviz method (light surface). Models take the categorical slots
# in their listed order (identity); state is shown by fill strength plus a glyph, never by color alone.
SURFACE, INK, INK_2, MUTED, GRID, AXIS, CARD = ("#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7",
                                                "#f4f3ef")
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
GLYPH = {"done": "✓", "running": "▶", "queued": "○", "failed": "✕", "stopped": "■", "idle": "•", "missing": "✕",
         "pending": "○", "blocked": "!", "waiting": "○"}
STATE_COLOR = {"done": STATUS["good"], "running": INK, "queued": MUTED, "failed": STATUS["critical"],
               "stopped": STATUS["serious"], "idle": MUTED, "missing": STATUS["critical"], "pending": MUTED,
               "blocked": STATUS["serious"], "waiting": MUTED}
QUEUED_ALPHA, REST_ALPHA = 0.16, 0.32
SHAPES = ["o", "D", "s", "^", "v", "P"]


def set_fonts():
    available = {font.name for font in font_manager.fontManager.ttflist}
    family = [name for name in ("Inter", "Helvetica Neue", "Arial", "Liberation Sans") if name in available]
    plt.rcParams.update({
        "font.family": family + ["DejaVu Sans"],     # DejaVu Sans also supplies the glyphs ✓ ▶ ○ ✕
        "font.size": 9.5, "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "xtick.color": MUTED,
        "ytick.color": MUTED, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "pdf.fonttype": 42, "svg.fonttype": "none",
    })


def parse_time(text):
    """ISO 8601 string -> naive local datetime (the status file is written in one time zone)."""
    return datetime.fromisoformat(text).replace(tzinfo=None) if text else None


def hours(delta):
    return delta.total_seconds() / 3600


def fmt_hours(value):
    return f"{value:.1f} h" if value < 100 else f"{value:.0f} h"


def fmt_when(moment, now):
    """'14:30' today, 'Thu 09:15' within a week, else '12 Oct 09:15'."""
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if abs((moment - now).days) < 6:
        return moment.strftime("%a %H:%M")
    return moment.strftime("%d %b %H:%M")


class Canvas:
    """Text placed in figure coordinates, measured after drawing so that blocks never overlap."""

    def __init__(self, fig):
        self.fig = fig
        self.renderer = fig.canvas.get_renderer()

    def text(self, x, y, string, **style):
        style.setdefault("va", "top")
        return self.fig.text(x, y, string, **style)

    def box(self, artist):
        return artist.get_window_extent(self.renderer).transformed(self.fig.transFigure.inverted())

    def wrap(self, string, width, fontsize, family=None):
        """Wrap a string to a width in figure fractions (estimated from the average character width)."""
        probe = self.fig.text(0, 0, "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJ 0123456789", fontsize=fontsize,
                              family=family or plt.rcParams["font.family"])
        char_width = self.box(probe).width / len(probe.get_text())
        probe.remove()
        chars = max(int(width / char_width), 10)
        return "\n".join(textwrap.wrap(string, chars, break_long_words=False, break_on_hyphens=False)) or string


# ------------------------------------------------------------------------------------------------------------
# Reading the status
# ------------------------------------------------------------------------------------------------------------

def load_status(path):
    status = json.loads(Path(path).read_text())
    now = parse_time(status["generated"])
    colors = {model["name"]: SERIES[index % len(SERIES)] for index, model in enumerate(status.get("models", []))}
    for stage in status["stages"]:
        colors.setdefault(stage.get("model", stage["name"]), SERIES[len(colors) % len(SERIES)])
        for run in stage["runs"]:
            run["_start"] = parse_time(run.get("start") or run.get("estimate_start"))
            run["_end"] = parse_time(run.get("end") or run.get("estimate_end"))
            if run["status"] == "running" and run["_end"] is not None and run["_end"] < now:
                run["_end"] = now                                  # overdue: show it as ending now
    return status, now, colors


def counts_gpu_time(stage):
    """False for a stage with `"gpu_time": false` (a power cut, a copy): shown, but its hours are not GPU time."""
    return stage.get("gpu_time", True) is not False


def stage_summary(stage, now):
    runs = stage["runs"]
    count = {state: sum(run["status"] == state for run in runs) for state in ("done", "running", "queued",
                                                                              "failed", "stopped")}
    used = sum(hours(min(run["_end"], now) - run["_start"]) for run in runs
               if run["status"] in ("done", "running", "failed", "stopped") and run["_start"] and run["_end"])
    left = sum(hours(run["_end"] - max(run["_start"], now)) for run in runs
               if run["status"] in ("running", "queued") and run["_start"] and run["_end"])
    ends = [run["_end"] for run in runs if run["_end"]]
    return count, used, left, (max(ends) if ends else None)


# ------------------------------------------------------------------------------------------------------------
# Header
# ------------------------------------------------------------------------------------------------------------

def draw_header(canvas, status, now, top):
    """Title, time, system, working folders and sessions; returns the lowest y used."""
    environment = status.get("environment", {})
    title = canvas.text(LEFT, top, status["title"], fontsize=19, fontweight="semibold", color=INK)
    stamp = f"Status at {now.strftime('%a %d %b %Y, %H:%M')} {status.get('timezone', '')}".strip()
    canvas.text(RIGHT, top, stamp, fontsize=10.5, fontweight="semibold", color=INK, ha="right")
    system = environment.get("system", "")
    if environment.get("cluster"):
        system += f" ({environment['cluster']})"
    details = "  ·  ".join(line for line in (system, environment.get("gpu"), environment.get("user")) if line)
    detail_text = canvas.text(RIGHT, top - 0.024, details, fontsize=9.5, color=INK_2, ha="right")
    y = canvas.box(title).y0 - 0.008
    if status.get("subtitle"):
        width = canvas.box(detail_text).x0 - LEFT - 0.02
        subtitle = canvas.text(LEFT, y, canvas.wrap(status["subtitle"], width, 10.5), fontsize=10.5, color=INK_2,
                               linespacing=1.3)
        y = canvas.box(subtitle).y0
    y -= 0.018

    folders, sessions = environment.get("working_dirs", []), environment.get("sessions", [])
    column_2 = 0.53
    canvas.text(LEFT, y, "Working folders", fontsize=9, fontweight="semibold", color=INK_2)
    canvas.text(column_2, y, "Sessions", fontsize=9, fontweight="semibold", color=INK_2)
    line_y = y - 0.019
    for folder in folders:
        path = canvas.text(LEFT, line_y, folder["path"], fontsize=8.4, family="monospace", color=INK)
        if folder.get("role"):
            canvas.text(canvas.box(path).x1 + 0.008, line_y, folder["role"], fontsize=8.4, color=MUTED)
        line_y -= 0.0165
    bottom = line_y
    line_y = y - 0.019
    for session in sessions:
        state = session.get("state", "")
        canvas.text(column_2, line_y, GLYPH.get(state, "•"), fontsize=8.4, color=STATE_COLOR.get(state, MUTED))
        name = canvas.text(column_2 + 0.011, line_y, f"{session.get('kind', 'tmux')} {session['name']}",
                           fontsize=8.4, family="monospace", color=INK)
        x = max(canvas.box(name).x1 + 0.008, column_2 + 0.155)
        role = f"{state}" + (f" — {session['role']}" if session.get("role") else "")
        block = canvas.text(x, line_y, canvas.wrap(role, RIGHT - x, 8.4), fontsize=8.4, color=MUTED,
                            linespacing=1.25)
        line_y = canvas.box(block).y0 - 0.006
    bottom = min(bottom, line_y) - 0.006
    canvas.fig.add_artist(Line2D([LEFT, RIGHT], [bottom, bottom], color=GRID, lw=0.8))
    return bottom


# ------------------------------------------------------------------------------------------------------------
# Timeline and stage table
# ------------------------------------------------------------------------------------------------------------

def window_lengths(status):
    """(past hours, future days) the timeline shows: 12 h back and 7 days ahead unless `window` says otherwise
    (a timeline is mainly for seeing the coming days). None for `"window": "all"`: fit every run."""
    window = status.get("window", {})
    if window == "all":
        return None
    return window.get("past_hours", 12), window.get("future_days", 7)


def draw_timeline(ax, status, now, colors):
    stages = status["stages"]
    lengths = window_lengths(status)
    if lengths:                                          # fixed window; the axes clip bars that leave it
        first, last = now - timedelta(hours=lengths[0]), now + timedelta(days=lengths[1])
    else:
        starts = [run["_start"] for stage in stages for run in stage["runs"] if run["_start"]]
        ends = [run["_end"] for stage in stages for run in stage["runs"] if run["_end"]] + [now]
        first, last = min(starts + [now]), max(ends)
    span = max(last - first, timedelta(hours=1))
    left_pad, right_pad = (timedelta(0), timedelta(0)) if lengths else (span * 0.01, span * 0.12)
    ax.set_xlim(mdates.date2num(first - left_pad), mdates.date2num(last + right_pad))
    ax.set_ylim(len(stages) - 0.45, -0.85)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.set_yticks([])

    span_hours = hours(span)
    step = 3 if span_hours <= 36 else 6 if span_hours <= 96 else 12 if span_hours <= 200 else 24
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, step)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.tick_params(axis="x", which="major", labelsize=8, length=3, color=AXIS)
    ax.grid(axis="x", which="major", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    day = datetime(first.year, first.month, first.day) + timedelta(days=1)
    if day - first > span * 0.09:                        # left out when the next day's name would overlap it
        ax.text(mdates.date2num(first - left_pad), -0.72, first.strftime("%a %d %b"), fontsize=8.6, color=INK_2,
                fontweight="semibold", va="center")
    while day <= last + right_pad:                       # day boundaries: a stronger line and the day name
        ax.axvline(mdates.date2num(day), color=AXIS, lw=0.8, zorder=0)
        if day + span * 0.07 < last + right_pad:         # no name where it would run past the axis
            ax.text(mdates.date2num(day + span * 0.004), -0.72, day.strftime("%a %d %b"), fontsize=8.6, color=INK_2,
                    fontweight="semibold", va="center")
        day += timedelta(days=1)

    bar_height = 0.46
    for row, stage in enumerate(stages):
        color = colors[stage.get("model", stage["name"])]
        for run in stage["runs"]:
            if not (run["_start"] and run["_end"]):
                continue
            start, end = mdates.date2num(run["_start"]), mdates.date2num(run["_end"])
            state = run["status"]
            if state == "running":
                elapsed = mdates.date2num(now)
                ax.add_patch(Rectangle((start, row - bar_height / 2), elapsed - start, bar_height, facecolor=color,
                                       edgecolor=SURFACE, lw=1.2))
                ax.add_patch(Rectangle((elapsed, row - bar_height / 2), max(end - elapsed, 0), bar_height,
                                       facecolor=color, alpha=REST_ALPHA, edgecolor=SURFACE, lw=1.2))
                note = f"▶ now: {run.get('label', run['name'])}" + (f" — {run['note']}" if run.get("note") else "")
                ax.annotate(note, xy=(elapsed, row - bar_height / 2), xytext=(4, 3), textcoords="offset points",
                            fontsize=8.2, color=INK, va="bottom", fontweight="semibold")
            else:
                face = STATUS["critical"] if state == "failed" else color
                alpha = {"done": 1.0, "failed": 1.0, "stopped": 0.6}.get(state, QUEUED_ALPHA)
                # A proposed run (compute-planner plan, not yet approved) is a queued bar with a hatch, so a
                # proposal can never be mistaken for a booking. The hatch is drawn in the lane colour.
                hatch = "////" if run.get("proposed") else None
                ax.add_patch(Rectangle((start, row - bar_height / 2), end - start, bar_height, facecolor=face,
                                       alpha=alpha, edgecolor=color if hatch else SURFACE, lw=1.2, hatch=hatch))
                if hatch:
                    # A proposed block is named above its bar (like "▶ now:" for a running one), because one
                    # PLAN row per machine can hold several proposals and the stage name alone does not say
                    # which task each bar is.
                    ax.annotate(run.get("label", run["name"]), xy=(start, row - bar_height / 2), xytext=(4, 3),
                                textcoords="offset points", fontsize=8.2, color=INK, va="bottom",
                                fontweight="semibold", clip_on=True)
                if state == "failed":
                    ax.text((start + end) / 2, row, "✕", color=SURFACE, ha="center", va="center", fontsize=8,
                            clip_on=True)
        count, used, left, stage_end = stage_summary(stage, now)
        if stage_end is not None:
            label = ("done " if not left else "≈ ") + fmt_when(stage_end, now)
            draw_end_label(ax, label, stage_end, row, span, bar_height)

    # Stops under the day names: with the default window "now" sits 12 h in, where today's name is written.
    ax.vlines(mdates.date2num(now), -0.58, len(stages) - 0.45, color=INK, lw=1.1, zorder=5)
    ax.text(mdates.date2num(now), len(stages) - 0.47, f" now {now.strftime('%H:%M')}", fontsize=8.2, color=INK, va="bottom",
            fontweight="semibold")


def draw_end_label(ax, label, stage_end, row, span, bar_height):
    """'done 14:30' or '≈ Thu 09:15' right after the stage's last bar. An end before the window goes at the left
    edge with ←; an end after it, or a label that would run past the right edge, goes above the bar at the right
    edge (with → when the end is outside)."""
    x_min, x_max = ax.get_xlim()
    x = mdates.date2num(stage_end + span * 0.006)
    if x < x_min:                                        # on a background: it reaches past the "now" line
        ax.text(x_min, row, "← " + label, fontsize=8.2, color=INK_2, va="center", zorder=6,
                bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 1.5})
        return
    text = ax.text(x, row, label, fontsize=8.2, color=INK_2, va="center")
    renderer = ax.figure.canvas.get_renderer()
    if x > x_max or text.get_window_extent(renderer).x1 > ax.get_window_extent(renderer).x1:
        text.set_text(("→ " if x > x_max else "") + label)
        text.set_position((x_max, row - bar_height / 2 - 0.03))
        text.set_horizontalalignment("right")
        text.set_verticalalignment("bottom")


def row_y(canvas, ax, row):
    return canvas.fig.transFigure.inverted().transform(ax.transData.transform((ax.get_xlim()[0], row)))[1]


def draw_stage_labels(canvas, ax, status, colors):
    """Stage names left of the timeline, with the model swatch and the session that runs it."""
    for row, stage in enumerate(status["stages"]):
        y = row_y(canvas, ax, row)
        canvas.fig.add_artist(Rectangle((LEFT, y + 0.0005), 0.007, 0.0095, transform=canvas.fig.transFigure,
                                        facecolor=colors[stage.get("model", stage["name"])], edgecolor="none"))
        canvas.text(LEFT + 0.012, y + 0.011, stage["name"], fontsize=9.2, color=INK, fontweight="semibold")
        detail = f"{len(stage['runs'])} run{'s' if len(stage['runs']) != 1 else ''}"
        if stage.get("session"):
            detail += f" · {stage['session']}"
        canvas.text(LEFT + 0.012, y - 0.003, detail, fontsize=8, color=MUTED)


def draw_stage_table(canvas, ax, status, now, x0):
    """Per stage: runs by state, GPU time used and left, expected end; rows aligned with the timeline."""
    header_y = row_y(canvas, ax, -0.72)
    summaries, runs_right = [], x0 + 0.05
    for row, stage in enumerate(status["stages"]):         # column 1 first: its width decides the others
        y = row_y(canvas, ax, row)
        count, used, left, stage_end = stage_summary(stage, now)
        summaries.append((y, used, left, stage_end, counts_gpu_time(stage)))
        x = x0
        for state in ("done", "running", "queued", "failed", "stopped"):
            if count[state]:                                  # the glyph carries the state; the number stays ink
                canvas.text(x, y, GLYPH[state], fontsize=8.6, color=STATE_COLOR[state], va="center")
                number = canvas.text(x + 0.009, y, str(count[state]), fontsize=8.6, color=INK, va="center")
                x = canvas.box(number).x1 + 0.006
        runs_right = max(runs_right, x)
    columns = [x0, runs_right + 0.008, RIGHT]
    for x, title, align in zip(columns, ("Runs", "GPU time", "Ends"), ("left", "left", "right")):
        canvas.text(x, header_y, title, fontsize=8.6, fontweight="semibold", color=INK_2, va="center", ha=align)
    total_used = total_left = 0.0
    for y, used, left, stage_end, gpu_time in summaries:
        if gpu_time:
            total_used, total_left = total_used + used, total_left + left
            used_text = f"{fmt_hours(used)} used" if used else "–"
            canvas.text(columns[1], y + (0.007 if left else 0), used_text, fontsize=8.4, color=INK, va="center")
            if left:
                canvas.text(columns[1], y - 0.008, f"≈ {fmt_hours(left)} left", fontsize=8.4, color=MUTED,
                            va="center")
        else:                                                 # "gpu_time": false: kept out of the totals
            canvas.text(columns[1], y, "no GPU use", fontsize=8.4, color=MUTED, va="center")
        if stage_end is not None:
            canvas.text(columns[2], y, ("" if left else "done ") + fmt_when(stage_end, now), fontsize=8.4,
                        color=INK if left else MUTED, va="center", ha="right")
    canvas.text(x0, row_y(canvas, ax, len(status["stages"]) - 0.45) - 0.006,
                f"All stages: {fmt_hours(total_used)} used, ≈ {fmt_hours(total_left)} left", fontsize=8.6, color=INK,
                fontweight="semibold")


def draw_legend(canvas, x, y):
    # The last entry, "proposed (plan)", is the hatched bar of a compute-planner proposal (run field `proposed`).
    for label, alphas in (("done", (1.0,)), ("running: elapsed + estimated rest", (1.0, REST_ALPHA)),
                          ("queued (estimated)", (QUEUED_ALPHA,)), ("proposed (plan)", (QUEUED_ALPHA,))):
        swatch = 0.018 / len(alphas)
        hatch = "////" if label.startswith("proposed") else None
        for k, alpha in enumerate(alphas):
            canvas.fig.add_artist(Rectangle((x + k * swatch, y - 0.0045), swatch, 0.009,
                                            transform=canvas.fig.transFigure, facecolor=INK_2, alpha=alpha,
                                            hatch=hatch, edgecolor=INK_2 if hatch else "none", lw=0.6))
        text = canvas.text(x + 0.023, y, label, fontsize=8.2, color=INK_2, va="center")
        x = canvas.box(text).x1 + 0.02


# ------------------------------------------------------------------------------------------------------------
# Bottom panels
# ------------------------------------------------------------------------------------------------------------

def draw_milestones(canvas, status, x, top, width, bottom):
    canvas.text(x, top, "Done so far", fontsize=11, fontweight="semibold", color=INK)
    y = top - 0.030
    for milestone in status.get("milestones", []):
        state = milestone.get("status", "done")
        canvas.text(x, y, GLYPH.get(state, "•"), fontsize=9, color=STATE_COLOR.get(state, MUTED))
        canvas.text(x + 0.012, y, milestone.get("date", ""), fontsize=8.2, color=MUTED)
        block = canvas.text(x + 0.062, y, canvas.wrap(milestone["text"], width - 0.064, 8.6), fontsize=8.6,
                            color=INK, linespacing=1.3)
        y = canvas.box(block).y0 - 0.008
        if y < bottom:
            block.set_text(block.get_text().split("\n")[0] + " …")
            break
    return y


def draw_models(canvas, status, colors, x, top, width):
    canvas.text(x, top, "Networks", fontsize=11, fontweight="semibold", color=INK)
    y = top - 0.030
    for model in status.get("models", []):
        name = canvas.text(x + 0.022, y - 0.008, model["name"], fontsize=9.6, fontweight="semibold", color=INK)
        if model.get("params"):
            canvas.text(x + width - 0.008, y - 0.008, model["params"], fontsize=8.4, color=INK_2, ha="right")
        lines = "\n".join(canvas.wrap(line, width - 0.018, 8.2) for line in model.get("lines", []))
        body = canvas.text(x + 0.009, canvas.box(name).y0 - 0.006, lines, fontsize=8.2, color=INK_2,
                           linespacing=1.3)
        card_bottom = canvas.box(body).y0 - 0.008
        canvas.fig.add_artist(FancyBboxPatch((x, card_bottom), width, y - card_bottom,
                                             boxstyle="round,pad=0,rounding_size=0.005",
                                             transform=canvas.fig.transFigure, facecolor=CARD, edgecolor="none",
                                             zorder=-1))
        canvas.fig.add_artist(Rectangle((x + 0.009, y - 0.0195), 0.007, 0.0095, transform=canvas.fig.transFigure,
                                        facecolor=colors[model["name"]], edgecolor="none"))
        y = card_bottom - 0.012
    return y


def draw_results(canvas, status, colors, x, top, width, bottom):
    results = status["results"]
    canvas.text(x, top, results.get("title", "Results so far"), fontsize=11, fontweight="semibold", color=INK)
    facets = results["facets"]
    variants, rows, series = [], [], []
    for facet in facets:
        for point in facet["points"]:
            for collection, key in ((variants, "variant"), (rows, "row"), (series, "series")):
                if point.get(key) is not None and point[key] not in collection:
                    collection.append(point[key])
    shape_of = {variant: SHAPES[index % len(SHAPES)] for index, variant in enumerate(variants)}

    legend_x, legend_y = x, top - 0.032                          # legend: series by color, variants by shape
    entries = [(name, "o", True, colors.get(name, SERIES[0])) for name in series]
    entries += [(variant, shape_of[variant], k == 0, INK_2) for k, variant in enumerate(variants)]
    for label, marker, filled, color in entries:
        canvas.fig.add_artist(Line2D([legend_x + 0.005], [legend_y], marker=marker, markersize=6.5, linestyle="none",
                                     markerfacecolor=color if filled else SURFACE, markeredgecolor=color,
                                     markeredgewidth=1.5, transform=canvas.fig.transFigure))
        text = canvas.text(legend_x + 0.013, legend_y, label, fontsize=8.4, color=INK_2, va="center")
        legend_x = canvas.box(text).x1 + 0.018

    values = [point["value"] for facet in facets for point in facet["points"]]
    low, high = min(values), max(values)
    pad = max((high - low) * 0.12, 0.01)
    label_width, gap = 0.045, 0.025
    facet_width = (width - label_width - gap * (len(facets) - 1)) / len(facets)
    plot_top = legend_y - 0.03
    for index, facet in enumerate(facets):                       # titles and summaries first, to measure them
        left = x + label_width + index * (facet_width + gap)
        block = canvas.text(left, legend_y - 0.024, facet["title"], fontsize=9.2, color=INK, fontweight="semibold")
        for summary in facet.get("summaries", []):
            block = canvas.text(left, canvas.box(block).y0 - 0.005, canvas.wrap(summary["text"], facet_width, 8),
                                fontsize=8, color=INK_2, linespacing=1.25)
        plot_top = min(plot_top, canvas.box(block).y0 - 0.018)
    plot_height = min(plot_top - bottom - 0.045, (0.42 * len(rows) + 0.3) / FIG_HEIGHT)   # ≤ 0.42 inch per row
    for index, facet in enumerate(facets):
        left = x + label_width + index * (facet_width + gap)
        ax = canvas.fig.add_axes([left, plot_top - plot_height, facet_width, plot_height])
        ax.set_xlim(low - pad, high + pad)
        ax.set_ylim(len(rows) - 0.5, -0.5)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels(rows if index == 0 else [], fontsize=8.4, color=INK_2)
        ax.tick_params(axis="y", length=0)
        ax.tick_params(axis="x", labelsize=8, length=3, color=AXIS)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.grid(axis="x", color=GRID, lw=0.6)
        ax.set_axisbelow(True)
        ax.set_xlabel(results.get("metric", ""), fontsize=8.4, color=INK_2)
        facet_series = list(dict.fromkeys(point["series"] for point in facet["points"]))
        offsets = {name: (k - (len(facet_series) - 1) / 2) * 0.24 for k, name in enumerate(facet_series)}
        for point in facet["points"]:
            filled = not point.get("variant") or variants.index(point["variant"]) == 0
            color = colors.get(point["series"], SERIES[0])
            ax.scatter(point["value"], rows.index(point["row"]) + offsets[point["series"]],
                       marker=shape_of.get(point.get("variant"), "o"), s=46,
                       facecolor=color if filled else SURFACE, edgecolor=color, linewidths=1.6, zorder=3)
    return plot_top - plot_height - 0.045                      # below the x-axis label


def draw_footer(canvas, status, source, top):
    notes = list(status.get("notes", []))
    if status.get("estimate_method"):
        notes.insert(0, "Estimates: " + status["estimate_method"])
    lengths = window_lengths(status)
    if lengths:
        notes.insert(0, f"Timeline: past {lengths[0]:g} h and next {lengths[1]:g} day{'s' if lengths[1] != 1 else ''}"
                        " (← / → = an end outside it)")
    canvas.fig.add_artist(Line2D([LEFT, RIGHT], [top + 0.008, top + 0.008], color=GRID, lw=0.8))
    signature = canvas.text(RIGHT, top, f"Made from {source} with run-status-figure", fontsize=7, color=MUTED,
                            ha="right")
    canvas.text(LEFT, top, canvas.wrap("   ·   ".join(notes), canvas.box(signature).x0 - LEFT - 0.02, 7.8),
                fontsize=7.8, color=MUTED, linespacing=1.35)


# ------------------------------------------------------------------------------------------------------------

def render(status_path, output_base):
    set_fonts()
    status, now, colors = load_status(status_path)
    fig = plt.figure(figsize=(FIG_WIDTH, FIG_HEIGHT))
    canvas = Canvas(fig)
    header_bottom = draw_header(canvas, status, now, top=0.985)

    n_stages = len(status["stages"])
    title_y = header_bottom - 0.026
    canvas.text(LEFT, title_y, "Runs", fontsize=11, fontweight="semibold", color=INK, va="center")
    draw_legend(canvas, 0.19, title_y)
    timeline_height = (0.54 * n_stages + 0.75) / FIG_HEIGHT    # 0.54 inch per stage row
    timeline_top = title_y - 0.018
    ax = fig.add_axes([0.19, timeline_top - timeline_height, 0.585, timeline_height])
    draw_timeline(ax, status, now, colors)
    fig.canvas.draw()
    draw_stage_labels(canvas, ax, status, colors)
    draw_stage_table(canvas, ax, status, now, x0=0.79)

    panels = [key for key in ("milestones", "models", "results") if status.get(key)]
    lowest = timeline_top - timeline_height - 0.04
    if panels:
        top = timeline_top - timeline_height - 0.065
        bottom = 0.06                                         # the footer goes under whatever is drawn
        weights = {"milestones": 0.37, "models": 0.22, "results": 0.33}
        gap = 0.03
        scale = (RIGHT - LEFT - gap * (len(panels) - 1)) / sum(weights[key] for key in panels)
        x = LEFT
        for key in panels:
            width = weights[key] * scale
            if key == "milestones":
                lowest = min(lowest, draw_milestones(canvas, status, x, top, width, bottom))
            elif key == "models":
                lowest = min(lowest, draw_models(canvas, status, colors, x, top, width))
            else:
                lowest = min(lowest, draw_results(canvas, status, colors, x, top, width, bottom))
            x += width + gap
    # Not clamped at the figure's bottom edge: with many stages the Networks cards run below it, and a clamped
    # footer would print over them. savefig's tight bounding box keeps whatever is drawn below the edge.
    draw_footer(canvas, status, Path(status_path).name, top=lowest - 0.012)

    output_base = Path(output_base)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, options in ((".png", {"dpi": 200}), (".pdf", {})):
        path = output_base.with_suffix(suffix)
        fig.savefig(path, bbox_inches="tight", pad_inches=0.25, **options)   # trims empty space at the bottom
        written.append(path)
    plt.close(fig)
    return written


def example_output_base():
    """Default output of --example: <paths.outputs_dir>/run-status-figure/example/example_status.

    The skill folder itself is never written to (it may be read-only, and it is shared by every project).
    Raises workhub_config.ConfigError when the settings file is missing, so the caller can ask for -o instead.
    """
    # workhub_config.py is the bundled copy of the kit's settings reader (same folder as this script); it is
    # imported here, not at the top, so that rendering a status file never needs the settings file.
    from workhub_config import expand_path, get_setting, load_config

    config = load_config(required=True)
    outputs_dir = expand_path(get_setting(config, "paths.outputs_dir", default="~/workhub/outputs"))
    return Path(outputs_dir) / "run-status-figure" / "example" / "example_status"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("status", nargs="?", type=Path, help="Status JSON file (references/status_schema.md).")
    parser.add_argument("-o", "--output", type=Path, help="Output path without suffix (default: next to the JSON).")
    parser.add_argument("--example", action="store_true", help="Render assets/example_status.json.")
    args = parser.parse_args()
    status_path = SKILL_DIR / "assets" / "example_status.json" if args.example else args.status
    if status_path is None:
        parser.error("give a status JSON file or --example")
    output = args.output
    if output is None and args.example:
        try:
            output = example_output_base()
        except Exception as error:                   # ConfigError: no settings file on this machine
            parser.error(f"{error} -- or give the output path with -o")
    if output is None:
        output = Path(status_path).with_suffix("")
    for path in render(status_path, output.expanduser()):
        print(path)


if __name__ == "__main__":
    main()
