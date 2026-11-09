# Planning rules (the algorithm of `plan_schedule.py`)

The rules in the order they are applied. The script is deterministic: the same three input files and the same
`--now` give the same plan.

## 0. Inputs and the clock

- `now` = `--now` or the current time in `machines.timezone` (the owner's time zone; `UTC` when absent). Horizon
  = `now` → `now + --days` (the skill passes `horizon_days`, default 7).
- Tasks considered = `state` in {`pending`, `approved`} and `rank` not null, sorted by `rank`. Unranked tasks are
  listed under "unranked, not placed". `running` tasks are not placed (their commitment is in the timeline).

## 1. Free windows per workstation

For each workstation build the busy timeline inside the horizon from three sources:

1. `blocked` weekly periods (the settings' usage windows, local time; each day's window is built in local time,
   never by adding 24 h, so a daylight-saving change inside the horizon is handled by the zone rules);
2. `exceptions` with `block` true (minus `block` false openings);
3. commitments: every `running`/`queued` run of the stage(s) whose `model` is this machine's `label` or `name`,
   with its `uses` (default: one GPU card used unless `stage.gpu_time` is false).

Resources: GPU cards (`gpu.count`, default 1; each GPU task or GPU commitment holds `gpu_cards`, default 1),
GPU memory (`gpu.memory_gb` × `count` in total), `ram_gb`, `threads`. A GPU task fits at time t when a card is
free and no block holds the machine, and GPU memory, RAM and threads of everything overlapping stay within the
machine's capacity. A CPU task (`needs.gpu` false) ignores the GPU but still respects `ram_gb`, `threads`, and the
rule "one job ≥ `ram_heavy_gb` at a time". Blocked periods block everything.

`free_windows` in `plan.json` lists, per workstation, the intervals with a GPU card free (on a CPU-only machine:
the intervals with nothing blocking it).

## 2. Placement, in rank order (greedy)

For each task:

1. **Earliest start** = max(`now`, `not_before`, end of every `depends_on` item). A dependency is a task id (its
   placed block's end; if that task is unplaced, this one is unplaced too, reason "depends on X, unplaced") or a
   commitment run name (its end; a `queued` commitment without `estimate_end` makes the task unplaced, reason
   "depends on X, which has no end time").
2. **Duration** on a machine m = `cost.per_machine[m]` if given, else `cost.hours`.
3. **What to place:**
   - `cost.basis` in {`measured`, `scaled`}: the production block.
   - `cost.basis` in {`estimate`, `none`} and `accept_estimate` false: a **measure** block of `measure.hours`
     (default 1.0 h) with the same `needs`; the production block is not placed and the task goes to
     `waiting_for_measurement` ("schedule a short profiling slot first").
   - `accept_estimate` true: the production block with warning "estimate, not measured" (label gets "?").
4. **Candidate machines** = `needs.machines` (or every workstation when empty) ∩ machines whose GPU memory (one
   card), RAM and threads capacities are at least the task's needs; plus, when `cluster` is set, each allowed
   cluster whose `gpus` has the task's `gpu` key (`cpu` for CPU work) and that has no outage overlapping the job
   (§3). A measure block is placed on workstations only.
5. **Fit on a workstation:** the earliest time ≥ earliest start where the block fits.
   - `can_pause` false: the whole duration must lie inside one free window.
   - `can_pause` true: the duration is accumulated over consecutive free windows; each piece becomes its own block
     with `part` `k/n`. A piece shorter than 30 min is skipped (not worth a restart).
6. **Choice among candidates:** the earliest end; ties go to the first machine in `needs.machines`, then to the
   smaller GPU (keep the big card free). **CPU preference:** when `prefer_cluster_for_cpu` names a cluster, a
   CPU-only task goes there whenever that cluster is a candidate and (with a deadline) ends by it, even when a
   workstation would end earlier; when it would miss the deadline, the plan says so and the normal choice applies.
   The runner-up of the other kind (cluster vs workstation) is recorded in `alternative`.
7. The placed block consumes the machine's resources for later tasks.

## 3. Cluster jobs

For a candidate cluster c with wait w = `queue_wait_hours[<resource>].typical` (missing → not used on that
cluster, reason "no measured queue wait"):

- estimated start = earliest start + w (a `submit` block from earliest start to that start, kind `submit`, drawn on
  the task's row with the label "submit → start ≈ …"), estimated end = start + `cluster.walltime_h`;
- an `outages` interval overlapping [submit, end] removes c;
- `walltime_h > max_walltime_h` removes c with a warning;
- **budget:** when `budget` is set and the job's resource is in `budget.applies_to` (or `applies_to` is empty),
  `added` = `walltime_h × gpus` (GPUs, or cores for `cpu`); if `used + added > allocated` a `budget_warnings`
  entry names the cluster and the account (the cluster stays a candidate: the user decides). An unknown `used`
  counts as 0 and `machines.json` carries a warning.

Submit timing: a cluster block always starts at the task's earliest start ("submit now", or "submit when X ends"),
so the queue wait overlaps other work. If the cluster end is earlier than every workstation option, the cluster
wins (§2.6) and the plan's decision reads "submit <task> to <c> now → start ≈ …, end ≈ … — yes/no".

## 4. Hard deadlines (the tie rule)

Rank is the order; a lower-ranked task jumps ahead only when it would otherwise miss a **hard** deadline.

After the greedy pass, for each task with `deadline.hard` true whose block ends after `deadline.date` 23:59 (or that
is unplaced for lack of room): move it to the top of the order and run the greedy pass again. If it now meets the
deadline, keep that order and record a `promotions` entry naming the tasks it jumped; otherwise restore the order
and record a `deadline_misses` entry. Promotions are applied one task at a time in rank order. Soft deadlines never
reorder; a soft miss is reported in `deadline_misses` with `"hard": false`.

## 5. Decisions

One numbered decision per block or per group of parts, in start order: "D<n>: <title> on <machine> <from> → <to>
(<hours> h, <basis>) — yes/no". Then one per budget overrun ("accept the overrun? yes/no") and one per unplaced
task that the user could unblock (a missing measurement, a dependency without an end). The skill asks them (or, with
an orchestrator, the orchestrator asks them); only the ones answered yes are handed on.

## 6. What the script never does

It never reads Notion, SSH, tmux or Slurm, never reads the settings for machine facts (only `machines.json`), and
never starts anything. Live facts enter only through the three input files, which the skill fills from the real
state before running it.
