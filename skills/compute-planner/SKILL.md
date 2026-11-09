---
name: compute-planner
description: Propose the best use of the user's computers for the next 7 days (workstations and clusters from the workhub-claude settings) from what runs or is booked now, each machine's free windows, and the pending compute tasks ranked together with the user. It fills free GPU hours by rank, lets a task jump ahead only for a hard deadline, puts a short measurement run before any task whose runtime was never measured, picks cluster and submit time with a budget check, draws the proposal hatched on the run-status-figure timeline PNG, and writes a plan file with yes/no decisions; after the user's yes it hands the approved blocks to the orchestrator or the user. It never starts anything. Use it whenever the user asks what to run next, how to fill free GPUs or idle machines, for a compute, resource or machine plan for the coming days, which job to submit where, what to run tonight or this weekend, or whether the timeline could be fuller. Not for a plain status update (run-status-figure) or tidying tasks (work-hub-cleanup).
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Compute planner

The timeline (run-status-figure) shows what runs and what is booked. This skill answers the next question: **what
should run in the gaps, where, and in which order?** It proposes; the user decides; someone else launches (the
orchestrator conversation when the user works with one, otherwise the user or the row's own conversation after the
user's go).

## Ground rules

- **Measured numbers only.** A block is placed on a measured or scaled runtime (`cost.basis`). An unmeasured task
  gets a short **measure** block first; its production block waits for the number. Never invent a duration, a
  worker count or a queue wait: every number in a card or a fact cites its evidence (a log, a summary, a past
  `sacct` line).
- **Rank first, hard deadlines second.** The user's rank is the order; a lower-ranked task jumps ahead only when it
  would otherwise miss a *hard* deadline. Soft deadlines never reorder.
- **Priorities are built together.** The Work Hub Score is a hint, never the order. Each run shows the ranked list
  in chat and the user edits it there ("3 before 1, drop 5"); the result is saved and reused until they change it.
- **The planner never launches, stops or books.** It ends with a hand-off (step 8).
- **Machine rules are the user's.** Standing rules (GPUs, usage windows, accounts, budgets) live in the
  workhub-claude settings, which only the work-hub-setup skill writes: when the user states a new standing rule,
  tell them to say "set up my work hub" to change it. Short-lived facts (a block tonight because someone else uses
  a shared machine, a measured queue wait, an outage, the budget used so far) go into the planner's own
  `machine_facts.json`, with the evidence as `reason` or `source`, after showing them to the user.
- **Work Hub writes only after a yes**, and only Status and the row's topic card (by the row's owner
  conversation), or a new row for an approved compute task that has none. No new properties, options or views.
- **Privacy.** No participant, patient or case identifiers in cards, plans or the figure (the figure may be
  published to Notion). Figures of research images never belong in a plan.
- **Read Notion live** (rows or fetch; never a cached SQL snapshot, which can return stale rows).

## Settings used

Run every command below with `PY=$(python3 $P/scripts/workhub_config.py get runtime.python --default python3)`
where `$P` is this skill's folder and `$K=$($PY $P/scripts/planner_settings.py get skills_root)` is the folder that
holds this skill and its sibling skills.

- `owner.time_zone` (all clock times), `paths.outputs_dir` (default home of the planner folder), `runtime.python`.
- `machines.workstations[]`: name, ssh_alias, cpus, ram_gb, gpus[] (model, memory_gb, count), usage_windows[]
  (days, from, to, rule `blocked`, reason), notes. `machines.clusters[]`: name, ssh_alias, scheduler,
  slurm_accounts[]. Do not list the machines in chat from memory: `$PY $P/scripts/planner_settings.py machines
  -o <scratch>/machines.json --dry-run` prints what the planner sees and writes nothing.
- `workflow.orchestrator.enabled` / `.folder` (the hand-off), `cluster_login.enabled` (how open logins are checked),
  `notion.tasks_data_source` (Work Hub rows), `notion.research_log.*` (publishing the figure).
- `skill_settings.compute-planner.*`: `planner_dir`, `horizon_days`, `ram_heavy_gb`, `shared_machines`,
  `shared_cpu_percent`, `cluster_budgets`, `prefer_cluster_for_cpu`. Meaning and defaults:
  `references/formats.md` §2a; `$PY $P/scripts/planner_settings.py show` prints them resolved.

## Files

| File | What it does |
|---|---|
| `scripts/planner_settings.py` | settings → the planner's options (`show`, `get`, `stamp`) and `machines.json` (`machines`) |
| `scripts/plan_schedule.py` | tasks + machines + commitments → `plan.json`, `plan.md`, `status_plan.json`. Pure function of its three inputs; reads nothing live |
| `scripts/task_file.py` | keeps `compute_tasks.json`: `list`, `add`, `order`, `rank`, `set`, `measured`, `state` |
| `scripts/workhub_config.py` | the kit's settings reader (identical copy in every skill) |
| `references/formats.md` | the settings keys and the JSON formats. Read it before touching a card or a fact |
| `references/planning_rules.md` | the algorithm (free windows, placement, clusters, deadline promotion, decisions) |
| `assets/example_*.json` | a synthetic example (fictional machines ws01, ws02, mycluster); the tests use synthetic inputs |

Planner folder `<planner_dir>` (`$PY $P/scripts/planner_settings.py get planner_dir`): `compute_tasks.json`,
`machine_facts.json`, `planner_log.jsonl` (one line per run: when, where, what was approved) and
`plans/<stamp>/` (one folder per run: `machines.json`, `commitments.json`, `plan.*`, `status_plan.json`,
`status.png`, `status.pdf`, `page.json`).

Optional sibling skills in `$K` (each step says what to do when one is missing):
`run-status-figure` (`render_status.py`, `notion_page.py`, `references/notion.md`),
`work-hub-cleanup` (`live_state.py`: what runs now; `session_digest.py`: conversations since a time;
`cleanup_log.py last`: the last cleanup).

## Steps

### 1. Set up

- `python3 $P/scripts/workhub_config.py check`. If it fails, tell the user to say "set up my work hub" (skill
  work-hub-setup) and stop.
- `<scratch>` = the session's scratch folder; `<planner_dir>` and `<horizon>` from `planner_settings.py get
  planner_dir` / `get horizon_days`; `mkdir -p <planner_dir>/plans`; `<stamp>` = `planner_settings.py stamp`
  (now as `YYYY-MM-DD_HHMM` in the owner's time zone); `mkdir <planner_dir>/plans/<stamp>`.
- Read `references/formats.md` and `references/planning_rules.md`.
- Last run: `tail -1 <planner_dir>/planner_log.jsonl` gives `since` for the digests. On the first run use
  `$PY $K/work-hub-cleanup/scripts/cleanup_log.py last` when that skill is installed, else the last 7 days.
- Open logins: when `cluster_login.enabled` is true, `cluster-login status`; otherwise
  `ssh -O check <ssh_alias>` per cluster. Never open a login and never answer a prompt: a closed cluster is
  planned from its last known facts and named in the report.

### 2. Commitments: what runs and what is booked, live

1. Start from the newest timeline: when `workflow.orchestrator.enabled` is true, the status file named in
   `<orchestrator folder>/NOW.md`; otherwise the newest `status.json` made by run-status-figure (its folders are
   under `paths.outputs_dir/run-status-figure/`). With no timeline at all, make one first with the run-status-figure
   skill. Copy it to `<planner_dir>/plans/<stamp>/commitments.json`.
2. Refresh it: `$PY $K/work-hub-cleanup/scripts/live_state.py --since <since> -o <scratch>/live` (tmux sessions,
   GPU processes of other users, Slurm queues with start guesses, through open logins only). Without
   work-hub-cleanup, read the same evidence by hand (`tmux ls`, `nvidia-smi`, `squeue -u $USER` on open logins).
   Mark ended runs `done`, update `estimate_end` of running ones from their logs, add bookings that are not drawn
   yet, and add `uses` (GPU cards, GPU memory, RAM, threads) to every running or queued run. A stage's `model` must
   equal the `label` or the `name` of a machine in `machines.json`.
3. Shared machines (`shared_machines`): another user's GPU process, or steady CPU use above `shared_cpu_percent`,
   becomes an `exceptions` entry (block true, with the evidence as reason) in `<planner_dir>/machine_facts.json`
   for as long as it is expected to last; say so in the report.
4. Build the machines file of this run:
   `$PY $P/scripts/planner_settings.py machines --facts <planner_dir>/machine_facts.json -o <planner_dir>/plans/<stamp>/machines.json --print`.
   Its `warnings` (unknown budget use, an unknown preferred cluster, facts about machines not in the settings) go
   into the report.

### 3. Cards: the pending compute tasks

1. `$PY $P/scripts/task_file.py list` (the file defaults to `<planner_dir>/compute_tasks.json`; `--file` before
   the subcommand chooses another).
2. Find compute work that has no card yet: the open Work Hub rows when `notion.tasks_data_source` is set (only rows
   whose work is a run, a job, a copy or a build, not hands-on work), the open questions in
   `<orchestrator folder>/QUESTIONS.md` when the orchestrator is used, and the conversations since `since`
   (`$PY $K/work-hub-cleanup/scripts/session_digest.py --since <since> -o <scratch>/sessions`, when installed).
3. For each new card fill `needs` and `cost` from evidence: run logs, summaries, earlier plans, notes the user
   keeps. Cite the source in `cost.source`. Nothing measured → `basis` `none` and a `measure` description (what one
   short run looks like).
4. Mark finished or dropped cards `done` / `parked` with the evidence.
5. **Round 1 questions (with evidence):** new cards to confirm (title, needs, cost basis, deadline hard/soft,
   dependencies), cards to close, and any machine-rule change seen in the conversations. Write the answers with
   `task_file.py add/set/state`.

### 4. Rank together

Print the ranked list (rank, task, project, deadline, needs, hours + basis) and the unranked cards, and ask the user
for the order in chat. Keep the Work Hub Score beside each row as a hint. Apply the answer with
`task_file.py order id1,id2,...` (or `rank <id> <n>`). An unranked card is listed but never placed.

### 5. Plan

```
$PY $P/scripts/plan_schedule.py --tasks <planner_dir>/compute_tasks.json \
  --machines <planner_dir>/plans/<stamp>/machines.json \
  --commitments <planner_dir>/plans/<stamp>/commitments.json \
  -o <planner_dir>/plans/<stamp> --days <horizon> --print
```

Read `plan.md`. Check every block against what you know: a block on a machine with usage windows lies in a free
window, no block overlaps a booking, dependencies end before their dependants start, a cluster block has a
measured queue wait, and nothing of the user's own interactive work (a session they keep open, their desktop) is in
the way. If the plan is wrong, fix the inputs (a card, a fact, a commitment), never the plan file by hand, and rerun.

### 6. Draw and publish

- Set `title` to "Compute plan <date>" in `status_plan.json` and put the plan's warnings in `notes`, then
  `$PY $K/run-status-figure/scripts/render_status.py <planner_dir>/plans/<stamp>/status_plan.json -o <planner_dir>/plans/<stamp>/status`
  (proposed blocks come out hatched; legend "proposed (plan)"; needs matplotlib). Without run-status-figure, skip
  the figure and say so in one line.
- Look at the PNG (it holds only timelines and numbers): nothing collides, every proposed block is hatched, the
  free windows are visible.
- Publish it as a Status page of the Research log only when `notion.research_log.enabled` is true and its
  `data_source` is set: `$PY $K/run-status-figure/scripts/notion_page.py <planner_dir>/plans/<stamp>/status_plan.json -o <planner_dir>/plans/<stamp>/page.json`,
  then follow `$K/run-status-figure/references/notion.md` (headline = the first decision). Otherwise say in one
  line that it was not published and why.

### 7. Ask

Short reply: the PNG, the plan path, three to six bullets (what fills which gap, what waits for a measurement,
what misses a deadline), then the numbered decisions from `plan.json` as yes/no questions, in one round when
possible.

When `workflow.orchestrator.enabled` is true and this conversation is not the orchestrator, the questions go to the
orchestrator (it is the only conversation that asks the user decision questions): send it the plan path and the
decision lines with `SendMessage` (find it with `ListAgents`; its name carries `[ORCH]`) and say so in one line.
When the user discusses this plan here directly, answers given here count; send them to the orchestrator
afterwards.

### 8. After the answers: hand-off, never launch

1. `task_file.py state <id> approved` for each yes; `parked` or a new rank for a no, as the user says.
2. Hand-off:
   - **Orchestrator used** (`workflow.orchestrator.enabled` true): if this conversation is the orchestrator, add the
     approved blocks to the bookings in `<orchestrator folder>/NOW.md` (one line each: Task ID, owner conversation,
     resources, source `plans/<stamp>/plan.md`), log them in `log/YYYY-MM.md`, and give each owner conversation
     its OK (the owner launches). Otherwise send the plan path and the approved block ids to the orchestrator.
   - **No orchestrator:** list the approved blocks for the user (machine, start, end, what to run, the row) as the
     plan of record. The user launches them, or the row's own conversation does after the user's go for that
     block. This skill starts nothing.
3. Work Hub (only when `notion.tasks_data_source` is set, and after an explicit yes per item): Status "Running on a
   computer" or "Queued on a cluster" when a block starts, and the topic card (what runs, where, expected end;
   written by the row's owner conversation); a new row for an approved compute task that has none.
4. Append one line to `<planner_dir>/planner_log.jsonl`: `{"stamp", "finished", "system", "client", "approved":
   [...], "rejected": [...], "notes"}` (`system` from `workhub_config.py system-name`).
5. When a measure block has run, record the number at once: `task_file.py measured <id> --hours H --on MACHINE
   --source "<log path>"`, then rerun the plan (step 5) so the production block gets placed.

## Report format

Plain words, short; details in `plan.md`, not in the chat. Every path as `system:/absolute/path` (the system name
from `workhub_config.py system-name`); every machine by its name in the settings; every job by its tmux session or
Slurm job ID; decisions as yes/no. Define a technical term the first time it appears.
