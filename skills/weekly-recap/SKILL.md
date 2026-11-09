---
name: weekly-recap
description: Write the user's recap of one finished week (Monday 00:00 to Sunday 24:00 in the user's time zone) from the evidence (the week's Claude Code conversations on this machine and on clusters with an open SSH connection, the Work Hub rows they worked on (Done, progressed, created), the orchestrator log and decisions, the session records, the Slurm jobs (core-hours, GPU-hours, failures, waits, efficiency, against the week before), the workstation runs, and the Claude tokens per row). Produces a full-detail recap.md, a week timeline PNG/PDF and, when the Research log is set up, one Research log page (Type Report). Use it whenever the user says "weekly recap", "recap my week", "recap last week", "what did I do last week", "week in review", or asks what happened or ran on the clusters during a past week. Read-only toward the Work Hub (row changes are work-hub-cleanup). Not for what runs now (run-status-figure) or a compute plan (compute-planner).
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Weekly recap

One recap per finished week, for the user only, in full detail:

- **One Research log page per week** (Type Report) in Notion, plus the local files. Without a Research log in the
  settings, the local files only.
- **Written for the user only**, full detail.
- **The user starts it** ("weekly recap"), usually on Monday. First list the clusters that are not connected and
  wait for "go" or "skip".
- **Claude tokens per row** are included, with the comparison to the week before.

Below, `SKILL_DIR` is the folder that holds this SKILL.md (for example `~/.claude/skills/weekly-recap`, or the
plugin's `skills/weekly-recap`). The sibling skills `work-hub-cleanup` (conversation digest) and `run-status-figure`
(figure renderer, Notion upload recipe) must be installed next to it. `references/recap_template.md` holds the
sections, the content rules and the Notion page properties: read it before step 10.

## Ground rules

- **Read-only.** Never write Work Hub rows, never start, stop or submit jobs, never log in to a cluster: only the
  shared SSH connections that are already open (`ssh -O check <alias>` succeeds), always with `BatchMode=yes`.
- **Evidence only.** Every number and claim in the recap comes from the files the scripts write, a Done report, a
  topic card or a log entry. Unverified results are marked "first look".
- **Privacy.** No participant IDs or participant images anywhere (recap, Notion, figure). The timeline shows jobs
  only.
- **Times** in the user's time zone (`owner.time_zone` of the settings); "now" is printed by step 1 (`NOW`).
- **Paths** in the recap and Notion as `system:/absolute/path` (system name: `SYSTEM` of step 1), checked with
  `ls -ld`.
- **Python:** `runtime.python` of the settings (it needs matplotlib for the figure). The scripts use only the
  standard library otherwise.

## Settings this skill reads

All from the workhub-claude settings file (`python3 $SKILL_DIR/scripts/workhub_config.py path` prints where it is):
`owner.time_zone`; `system_name.*`; `notion.tasks_data_source`, `notion.task_id_prefix`,
`notion.research_log.enabled`, `notion.research_log.data_source`; `workflow.orchestrator.enabled`,
`workflow.orchestrator.folder`, `workflow.session_records_subdir`; `machines.workstations[]`,
`machines.clusters[]` (name, ssh_alias, scheduler, slurm_accounts, shares_home_with_this_machine);
`cluster_login.enabled`; `conversations.claude_projects_dir`, `conversations.include_codex`,
`conversations.codex_sessions_dir`; `paths.outputs_dir`; `runtime.python`; `machines.cluster_hops` (clusters reached through another cluster's login node); and one option of this skill:

| Key | Meaning | Default |
|---|---|---|
| `skill_settings.weekly-recap.project_rules_file` | JSON file that maps job names and folders to project labels (format: `references/project_rules.example.json`) | none: rows are grouped by their Work Hub Project only; jobs with no row get "other: <job-name prefix>" |

## Steps

0. **Settings.** Run `python3 $SKILL_DIR/scripts/workhub_config.py check`. If it fails, tell the user to say
   "set up my work hub" (skill work-hub-setup) and stop.

1. **The week and the folders**, once (`--week` accepts `last` (default), `this`, `YYYY-Www` or any date inside
   the week):
   ```bash
   S=$SKILL_DIR/scripts
   PY=$(python3 $S/workhub_config.py get runtime.python --default python3)
   $PY $S/recap_common.py --week last                 # read it: week, window, NOW, OUT, ORCH, HOSTS
   eval "$($PY $S/recap_common.py --week last --shell)"
   # sets WEEK_ID (e.g. 2026-W45), WEEK_LABEL, WEEK_START, WEEK_END, PREVIOUS_WEEK_ID, NOW, TIME_ZONE,
   # SYSTEM, OUT (<paths.outputs_dir>/weekly-recap/<WEEK_ID>), ORCH (orchestrator folder or empty),
   # HOSTS (clusters whose transcripts are read through SSH, or "none"), TASK_PREFIX
   ```
   The scripts write their parts under `$OUT` (folders mode 700, files mode 600) without `-o`.

2. **Connections.** Run `$PY $S/recap_common.py --connections` (or `cluster-login status` when
   `cluster_login.enabled` is true). Tell the user in one line which clusters are not connected and that their jobs
   and conversations would be missing; then end the turn and wait for **"go"** (they opened them) or **"skip"**
   (recap without them, named under Coverage). With no clusters in the settings, skip this step.

3. **Conversations.** The work-hub-cleanup digest from Monday 00:00 (it reads past Sunday; the next steps cut the
   window):
   ```bash
   $PY $SKILL_DIR/../work-hub-cleanup/scripts/session_digest.py --since "$WEEK_START" --hosts "$HOSTS" \
       --max-turns 400 --prompt-chars 400 --reply-chars 900 -o "$OUT/sessions" --overwrite
   ```

4. **Slurm jobs**, with the week before and the job → row links (the orchestrator logs of the week's months are
   found by themselves):
   ```bash
   $PY $S/slurm_week.py --week "$WEEK_ID" --compare-previous --sessions-json "$OUT/sessions/sessions.json"
   ```
   Add `--overwrite` on a re-run. Some clusters keep only a few weeks of sacct history.

5. **Claude tokens**, this week and the week before (for the comparison):
   ```bash
   $PY $S/claude_tokens_week.py --week "$WEEK_ID" --sessions-json "$OUT/sessions/sessions.json"
   $PY $S/claude_tokens_week.py --week "$PREVIOUS_WEEK_ID" -o "$OUT/tokens_previous"
   ```

6. **Per-row sources:**
   `$PY $S/week_sources.py --week "$WEEK_ID" --sessions-json "$OUT/sessions/sessions.json"`
   Read `$OUT/sources/index.txt` first, then each `rows/TK-n.txt`, `rows/no_row.txt`, `rows/orchestrator.txt` and
   `decisions.txt`. For long rows, read the turns that report results or Done reports, not every turn.

7. **Work Hub and Research log (Notion, read-only).** Tasks data source:
   `python3 $S/workhub_config.py get notion.tasks_data_source` (query recipes: work-hub-cleanup
   `references/work_hub.md`; use rows mode or fetch, never SQL mode, which can lag by days). Fetch: every row in
   `sources/index.txt`; rows with "Done on" inside the week; rows created in the week; rows In progress or Running
   (their topic card = top of the page body); Due dates in the next 7 days. Research log pages dated in the week:
   search `notion.research_log.data_source`. When `notion.research_log.enabled` is false or that data source is
   empty, skip the Research log here and in step 11, and say so in one line.

8. **Workstation runs.** Workstations (`machines.workstations[]`) have no accounting: take the runs (machine, tmux,
   start, end, GPU or not) from the orchestrator log entries and the topic cards, and write `$OUT/extra_runs.json`,
   a list of `{"lane": "ws02 · TK-42", "label": "night part", "status": "done", "start": ISO, "end": ISO, "gpu": true}`
   (times in the user's time zone, without offset). No workstation runs: skip the file.

9. **Timeline figure:**
   `$PY $S/week_timeline.py --week "$WEEK_ID" --extra-runs "$OUT/extra_runs.json"` (leave out `--extra-runs`
   when there is no such file). Look at the PNG (`$OUT/figure/week_timeline.png`; it shows job names and hours only)
   and fix crowded or wrong lanes before using it.

10. **Write `$OUT/recap.md`** with the sections of `references/recap_template.md`: plain words, short
    sentences, numbers with units. Check every output path with `ls -ld`.

11. **Publish the Research log page** (only when the Research log is set up) as in `references/recap_template.md`
    → "The Notion page": look for an existing page with Source key `weekly-recap:<WEEK_ID>` first (a re-run updates
    it and keeps "Personal notes"); upload the PNG and PDF (run-status-figure `references/notion.md` → "Uploading
    the figure"); fetch the page to verify.

12. **Answer the user** in a few lines: the "In short" bullets, the Notion link (if any), `<SYSTEM>:$OUT/recap.md`
    and the PNG, and what Coverage says is missing. Rows that look Done or work with no row go to the "For the
    Monday check" list; offer work-hub-cleanup, do not change rows.

## What the scripts write (under `$OUT`)

| Folder | Files | Contents |
|---|---|---|
| `sessions/` | `sessions.json`, digest | all conversations since Monday (session_digest.py) |
| `slurm/` | `slurm_week.txt`, `slurm_week.json` | totals per cluster and account, the week before, work groups by row, problem jobs |
| `tokens/`, `tokens_previous/` | `tokens_week.txt`, `tokens_week.json` | tokens per row (orchestrator as `ORCH`), model, machine, conversation |
| `sources/` | `index.txt`, `rows/*.txt`, `decisions.txt`, `week_sources.json` | each row's turns, log entries, decisions and session records |
| `figure/` | `week_timeline.json/.png/.pdf` | Slurm and workstation runs of the week, one lane per cluster and row |
| (top) | `extra_runs.json`, `recap.md` | written by Claude in steps 8 and 10 |

## Known limits

- Codex conversations are read by the digest, but their tokens are not counted.
- GPU-hours are allocated hours, not GPU utilisation.
- Jobs are linked to rows by the Task ID in the job name, by a conversation that names the job ID, by the
  orchestrator log, or by the working folder of a linked job; the rest appear as "no row", with a project label
  from the optional project rules file (first match wins: put specific words before general ones).
- A cluster that is not connected is missing entirely; its name goes under Coverage.
- Without an orchestrator (`workflow.orchestrator.enabled` false) there is no log or DECISIONS.md: decisions and
  workstation runs come from the topic cards and conversations only.

## Tests

```bash
python3 -m pytest -q $SKILL_DIR/tests        # synthetic data and a temporary settings file only
python3 -m pyflakes $SKILL_DIR/scripts
```
The figure test also needs the sibling run-status-figure renderer and a Python with matplotlib
(`WEEKLY_RECAP_TEST_PYTHON=/path/to/python`); it is skipped otherwise.
