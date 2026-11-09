---
name: run-status-figure
description: Make a high-quality status figure (PNG + PDF) of long-running computational work, showing what is done (networks or models trained, milestones, results so far), what is running now and what is queued, with estimated runtimes and end times, the working folders, and the tmux sessions or Slurm jobs that run it. Every figure is published by default as a new page (Type "Status") in the user's Notion Research log when one is set up. Use this skill whenever the user asks for a timeline (of runs, jobs, projects, or of their computers, GPUs or clusters), a status update or "update me", what their computers or GPUs are doing or how they are used, when something will finish, a progress or status figure, an overview of runs, training queues or experiments, "what's done / running / left", ETAs, or a snapshot of a session for a meeting or for Notion, even if they never say "figure" or "PNG". Every timeline answer comes with the PNG. Works the same on workstations and on Slurm clusters.
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Run status figure

One figure answers four questions about long-running work: what was done, what runs now, what is left and when it
ends, and where it runs (machine, folders, tmux sessions or Slurm jobs). The renderer is generic; the facts come from
a small JSON file that you fill from the real state of the work.

**Every timeline gets a PNG.** Whenever the user asks for a timeline, a status update, ETAs, or what their runs,
jobs or computers are doing, make this figure, look at it, and give them the PNG with the short text answer (a few
bullets, decisions as yes/no questions). A text-only timeline is not enough.

**Time window: the past 12 h and the next 7 days.** A timeline is mainly for seeing the coming days, so its axis
always runs from 12 h before "now" to 7 days after it, even when the known work ends sooner (the empty days then
show where machines are free). `render_status.py` does this by default. To fill the window, put in everything that
runs, is queued, is booked or is planned in the next 7 days on the machines or projects in question, not only the
job that was asked about: Slurm jobs pending with a start guess, bookings, planned runs and copies. Work outside the
window is still counted in the stage table; on the axis, an end before the window is written at the left edge as
`← done …` and an end after it at the right edge as `→ ≈ …`. Leave out stages that ended before the window and no
longer matter. Use another span only when the user asks for it (`"window"` in `references/status_schema.md`).

**Machine view** (a question about "my computers", "my GPUs", "my workstations" or a cluster): one colour per
machine, set as each stage's `model` (for example `"ws01 · RTX 4090"`), one or more stages per machine (what ran or
runs on that GPU), and idle machines, disks that are nearly full and pauses in the footer `notes`. The machines are
the ones listed in the settings file (`machines.workstations[]`, `machines.clusters[]`); read them all with
`collect_env.py --machines` (step 3).

## Settings this skill reads

All from the workhub-claude settings file (`~/.config/workhub-claude/config.json`, read with
`scripts/workhub_config.py`; the work-hub-setup skill writes it):

| Setting | Used for |
|---|---|
| `paths.outputs_dir` | status folders go to `<outputs_dir>/run-status-figure/<YYYY-MM-DD_HHMM>_<name>/` |
| `owner.time_zone` | the time stamp of a folder and the time zone of the Notion date |
| `machines.workstations[]`, `machines.clusters[]` | the machine view: which machines to read, through which SSH alias |
| `notion.research_log.enabled`, `notion.research_log.data_source` | where figures are published (empty = skip) |
| `notion.projects` | the Project options of the Research log |
| `skill_settings.research-log.machine_labels` | the Clusters labels (shared with the research-log skill) |
| `runtime.python` | the Python to run the scripts with (default `python3`) |
| `system_name` (`override_env`, `aliases`) | this machine's name in `system:/path` |

## The bundled files

| File | What it does |
|---|---|
| `scripts/render_status.py` | status JSON → PNG (200 dpi) + PDF. Needs only Python ≥ 3.8 and matplotlib ≥ 3.5 |
| `scripts/collect_env.py` | adds system, user, GPU, tmux session states and Slurm jobs to the JSON; `--machines` reads every listed machine (standard library only) |
| `scripts/new_status_folder.py` | makes the folder of a new figure and copies the newest earlier `status.json` of the same name into it |
| `scripts/notion_page.py` | status JSON → the Notion page (properties + content, the text twin of the figure) and its Research log parent |
| `scripts/workhub_config.py` | the kit's settings reader (identical copy in every skill; do not edit it here) |
| `references/status_schema.md` | the JSON format, field by field. Read it before writing a status file |
| `references/notion.md` | how to publish to the Research log (upload, page, labels). Read it at step 8 |
| `assets/example_status.json` | a complete fictional example; `render_status.py --example` draws it |
| `tests/test_run_status_figure.py` | the tests (synthetic data and a temporary settings file only) |

In the commands below, `$S` is this skill's folder and `$PY` the Python from
`python3 $S/scripts/workhub_config.py get runtime.python --default python3`.

## Steps

0. **Settings.** Run `python3 $S/scripts/workhub_config.py check`. If it fails, tell the user to say "set up my work
   hub" (skill work-hub-setup) and stop.
1. **Make the folder.** `$PY $S/scripts/new_status_folder.py <name> --copy-latest`, where `<name>` says what the
   figure covers (`machines` for the machine view, else a short project key such as `project-a`). It prints the new
   folder (last line) and copies the newest earlier `status.json` of the same name into it: start from that file and
   update it from the live state. With no earlier file, start from `assets/example_status.json`'s structure.
2. **Collect the facts; never guess them.** Read the evidence that already exists: queue logs, run folders,
   result and summary files, checkpoint names, `tmux ls`, `squeue -u $USER`, `nvidia-smi`. For every run you need:
   its state (done, running, queued, failed, stopped), its real start and end (or, if not finished, estimates), and
   any score worth showing. If a project already has a status collector script, run it instead of doing this by
   hand. When the figure will be refreshed later, write such a collector in the project: a rerun is then one
   command, and the numbers are reproducible.
3. **Machine view: read every machine.** `$PY $S/scripts/collect_env.py --machines -o <folder>/machines.json`.
   For each listed machine it reports whether it was reached and how, its GPUs (model, memory used, use %), its
   tmux sessions (running or idle), its load and, on clusters, the user's Slurm jobs (with Slurm's start guess for
   pending ones). It never prompts: a cluster is read only through an SSH connection that is already open
   (`ssh -O check <alias>`), a workstation through an open connection or a key login with `BatchMode=yes`. Name
   every machine it could not reach in the reply (for a cluster: "open it with cluster-login, then ask again"),
   and draw unreached machines from their last known state, marked as such in `notes`.
4. **Estimate what is not finished, and say how.** The best estimate is the mean duration of finished runs of the
   same kind (same model, same data size). With none finished, use a measured rate times the size of the work: for
   example, seconds per image per epoch × images × the epoch limit. That is an upper bound when early stopping
   exists; say so. Runs of one queue follow each other, so add the durations from now. Slurm jobs run in parallel,
   so give each its own times. Put the method in `estimate_method`: it is printed under the figure.
5. **Write `<folder>/status.json`** following `references/status_schema.md`. Keep labels short ("fold 2 ·
   extended"), and keep identifiers of people, participants, patients or cases out of it: the figure may be
   shared. Use stage names that say the model and the step ("U-Net · Part 1").
6. **Add the environment:**
   `$PY $S/scripts/collect_env.py --merge <folder>/status.json [--dir PATH=ROLE ...] [--session NAME=ROLE ...] [--tmux-match TEXT] [--slurm]`.
   - **Sessions:** those listed in the file keep their role and get their current state (running, idle, missing).
   - **Paths:** working folders are written as `system:/absolute/path`; the system name comes from the settings
     (`python3 $S/scripts/workhub_config.py system-name`).
7. **Render:** `$PY $S/scripts/render_status.py <folder>/status.json` (writes `status.png` and `status.pdf` there;
   `-o <path without suffix>` writes elsewhere).
   - **Python:** any environment with matplotlib ≥ 3.5. On a cluster with environment modules, for example
     `module load python scipy-stack`; elsewhere `pip install --user matplotlib` if it is missing.
   - **Font:** Inter is used when installed, otherwise DejaVu Sans.

   Then **look at the PNG before showing it** (open it with the image-reading tool; the figure holds only
   timelines and numbers), and check:
   - no text collides or runs off the page;
   - the axis runs from 12 h ago to 7 days ahead, and the "now" line sits 12 h in;
   - nothing planned for the next 7 days is missing;
   - the totals agree with what you know;
   - every estimate is plausible.

   If content is wrong, fix the JSON or the collector, not the renderer.
8. **Publish it to the Research log: always, without asking**, unless the user says not to for this figure.
   Run `$PY $S/scripts/notion_page.py <folder>/status.json -o <folder>/page.json` first and read
   `page.json` → `research_log`:
   - `"publish": false` → do not publish; say in one line why (its `reason`), for example "Not published: no
     Research log is set up yet; say 'set up my research log' to build one."
   - `"publish": true` → follow `references/notion.md`: upload the PNG and the PDF, rerun `notion_page.py` with
     `--image <png source> --pdf <pdf source> [--headline TEXT] -o <folder>/page.json`, create the page with
     `research_log.parent` as parent, and fetch it to check.

   **Without a Notion connection** (for example in a session on a cluster): keep `page.json` (made without
   `--image`/`--pdf`) next to the figure and tell the user it is not published yet, so that a session with Notion
   can publish it (`references/notion.md` → "Without a Notion connection").
9. **Report to the user:**
   - **Files:** the PNG and PDF paths, as `system:/absolute/path`.
   - **Notion:** the link to the new page, or the one line saying why it was not published.
   - **Summary:** the headline: done, running now, expected end.
   - **Decisions:** anything the figure shows as blocked or waiting for them.
   - **Not reached:** the machines `--machines` could not read.

## The figure

| Part | Content |
|---|---|
| Header | title and subtitle; time of the status; system, GPU and user; working folders with their role; sessions with their state and role |
| Runs | a timeline from 12 h ago to 7 days ahead, with one row per stage and one bar per run, plus a "now" line. Solid = done; solid + light = running (elapsed + estimated rest, labelled with its progress); light = queued; hatched = proposed, not approved yet; red with ✕ = failed; ← / → = an end outside the window |
| Stage table | beside the timeline: runs by state (✓ ▶ ○ ✕), GPU hours used and left, expected end, and the totals |
| Bottom panels (each optional) | **Done so far** (milestones with ✓, ○ waiting, ! a decision needed), **Networks** (parameters and key settings), **Results so far** (a dot plot per facet: colour = model, shape = variant) |
| Footer | estimate method and notes |

Colours: models take the validated categorical slots in the order they are listed (blue, orange, aqua, …). State is
always shown by fill strength plus a glyph, never by colour alone; status colours (green ✓, red ✕, orange !) are
used only for state.

## Privacy

- No identifiers of people, participants, patients or cases in the status file, the figure or the Notion page.
- A status figure never shows images of research participants. Figures or pages that do show such images never go
  to an LLM: not to Notion pages published through Claude, not to artifacts or chat uploads, and Claude does not open
  them with its image-reading tool; the user checks those figures.

## Other machines

The workhub-claude installer puts this skill on every machine where the kit is installed; nothing here copies it.
Machines that share one home folder share one copy. On a machine without matplotlib, load a Python module that
has it or `pip install --user matplotlib`.

## Tests

`python3 -m pytest $S/tests -q` (needs pytest; the rendering test is skipped when matplotlib is missing). The tests
use a temporary settings file (`WORKHUB_CLAUDE_CONFIG`) and synthetic data only.
