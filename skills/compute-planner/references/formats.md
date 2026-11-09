# File formats of the compute planner

The planner's options come from the workhub-claude settings (§2a). Its own files are JSON, standard library only.
Times are ISO 8601 with an offset (`2030-10-30T17:30:00-04:00`).

| File | Who writes it | Where |
|---|---|---|
| `compute_tasks.json` | `scripts/task_file.py` (after the user's answers in chat) | `<planner_dir>/compute_tasks.json` |
| `machine_facts.json` | the skill, after showing the evidence (§2b) | `<planner_dir>/machine_facts.json` |
| `machines.json` | `scripts/planner_settings.py machines`, built again each run from the settings + the facts | `<planner_dir>/plans/<stamp>/machines.json` |
| commitments = a run-status JSON | the latest timeline (`run-status-figure`), refreshed live | `<planner_dir>/plans/<stamp>/commitments.json` |
| `plan.json`, `plan.md`, `status_plan.json` | `scripts/plan_schedule.py` | `<planner_dir>/plans/<stamp>/` |
| `planner_log.jsonl` | the skill, one line per run (SKILL.md step 8) | `<planner_dir>/planner_log.jsonl` |

`<planner_dir>` = `planner_settings.py get planner_dir` (setting `skill_settings.compute-planner.planner_dir`,
default `<paths.outputs_dir>/compute-planner`; `$COMPUTE_PLANNER_DIR` overrides it for `task_file.py` only).
`<stamp>` = `planner_settings.py stamp` = `YYYY-MM-DD_HHMM` in the owner's time zone.

## 1. `compute_tasks.json` (schema `compute-tasks/1`)

One card per compute task: a thing that occupies a machine for more than a few minutes. Hands-on work (writing,
reading, meetings) never gets a card; it stays in the Work Hub.

```jsonc
{
  "schema": "compute-tasks/1",
  "updated": "2030-10-30T17:30:00-04:00",
  "tasks": [
    {
      "id": "test_set_inference",                    // stable slug: [a-z0-9_]+, never renamed
      "title": "Model A test-set inference",
      "project": "Project A",
      "work_hub": {"url": "https://www.notion.so/...", "title": "Model A test sets"},   // or null
      "rank": 1,                                     // the user's order, 1 = first; null = unranked (listed, never placed)
      "state": "pending",                            // pending | approved | running | done | parked
                                                     // placed: pending, approved. running: drawn from commitments,
                                                     // not placed again. done/parked: listed in the plan's footer only
      "deadline": {"date": "2030-11-04", "hard": true},   // or null; hard = a person or a real deadline waits
      "depends_on": ["mycluster_training"],          // task ids OR commitment run names (see §3); the task starts
                                                     // only after every one of them ends
      "not_before": null,                            // ISO time or null
      "needs": {
        "gpu": true,                                 // false = CPU/RAM/disk only
        "gpu_memory_gb": 20,                         // peak, measured; null if unknown
        "ram_gb": 40,                                // peak, measured; null if unknown
        "threads": 12,
        "disk_gb": 900,                              // output + temp space; null if unknown
        "machines": ["ws02", "ws01"]                 // allowed workstations, in the user's preferred order;
                                                     // [] = any workstation; cluster jobs use "cluster" below
      },
      "cost": {
        "hours": 36.0,                               // wall-clock hours on ONE allowed machine, null when unknown
        "basis": "measured",                         // measured | scaled | estimate | none
                                                     // measured = a finished run of the same kind
                                                     // scaled   = measured rate × size of the work
                                                     // estimate = the user's or a guess; NOT trusted for placement
                                                     // none     = nothing known
        "source": "run log of the previous batch: 18.6 tasks/h on ws02 with 13 workers",
        "measured_on": "ws02",                       // machine the number comes from
        "date": "2030-10-29",
        "per_machine": {"ws01": 30.0}                // optional: hours on machines that differ
      },
      "measure": {"hours": 1.0, "what": "1 task of 60 samples with the production settings"},
                                                     // the profiling block placed first when basis is estimate or
                                                     // none; hours default 1.0
      "accept_estimate": false,                      // true = place the production block on an estimate anyway
                                                     // (drawn with "?" and a warning); the user sets it per task
      "can_pause": true,                             // true = may be split over several free windows (resumable
                                                     // scripts); false = must fit inside one window
      "cluster": null,                               // or {"allowed": ["mycluster"], "gpu": "h100_80gb",
                                                     //     "gpus": 4, "walltime_h": 20.0, "basis": "measured"}
                                                     // `gpu` = a resource key of the cluster (§2b `gpus`), "cpu"
                                                     // for CPU-only work; `gpus` = units the job holds (GPUs, or
                                                     // cores for "cpu"), used by the budget check
                                                     // a task with BOTH needs.machines and cluster is placed where
                                                     // it ends first (CPU work: see prefer_cluster_for_cpu); the
                                                     // other option is reported
      "notes": "Starts after the cluster training ends.",
      "history": [{"date": "2030-10-30T17:30:00-04:00", "event": "added from Work Hub row 'Model A test sets'"}]
    }
  ]
}
```

Rules:

- `rank` values are unique among tasks with a rank. `task_file.py order` renumbers 1..n.
- A task is placed only when `state` is `pending` or `approved` AND `rank` is set.
- Participant, patient or case identifiers never appear in a card (the plan may be published to Notion).

## 2. `machines.json` (schema `compute-machines/1`)

Built by `planner_settings.py machines` from the settings (§2a) and the facts (§2b); never edited by hand (a
change made there is lost at the next run). `assets/example_machines.json` is a real output for the example
settings and facts.

```jsonc
{
  "schema": "compute-machines/1",
  "timezone": "America/New_York",                    // owner.time_zone
  "updated": "2030-10-30T16:50:00-04:00",
  "built_from": {"settings": "<settings file>", "facts": "<facts file>"},
  "prefer_cluster_for_cpu": "mycluster",             // "" when unset or not a listed cluster
  "warnings": [],                                    // e.g. "mycluster: budget use unknown, counted as 0"
  "machines": [
    {
      "name": "ws02",
      "kind": "workstation",                         // workstation | cluster
      "label": "ws02 · RTX 3060",                    // the run-status `model` name of this machine's lane
      "gpu": {"model": "RTX 3060", "memory_gb": 12.0, "count": 1},
                                                     // null for a CPU-only machine; `count` cards, each takes one
                                                     // GPU task; memory_gb = one card (the smallest when mixed)
      "ram_gb": 64.0,
      "threads": 24.0,                               // settings `cpus`
      "ram_heavy_gb": 60,                            // one job at or above this RAM at a time (setting ram_heavy_gb)
      "blocked": [                                   // weekly blocks from usage_windows, local time of `timezone`
        {"days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "from": "09:00", "to": "17:00",
         "reason": "shared with other lab members during the day"}
      ],
      "exceptions": [                                // one-off blocks or openings, from the facts
        {"from": "2030-10-30T17:00:00-04:00", "to": "2030-10-31T09:00:00-04:00", "block": true,
         "reason": "another user's GPU job keeps running tonight (seen with nvidia-smi at 16:20)"}
      ],
      "rules": [                                     // free text, printed in the plan
        "Shared machine: never while another user runs a GPU process or keeps the CPU above 30 %"
      ],
      "ssh_alias": "ws02",
      "shared": true                                 // listed in shared_machines
    },
    {
      "name": "mycluster",
      "kind": "cluster",
      "label": "mycluster",
      "scheduler": "slurm",
      "accounts": ["def-example_cpu", "def-example_gpu"],   // settings slurm_accounts
      "ssh_alias": "mycluster",
      "gpus": {"h100_80gb": {"model": "H100 80 GB"}, "cpu": {"model": "CPU nodes"}},   // keys match tasks[].cluster.gpu
      "queue_wait_hours": {"h100_80gb": {"typical": 36.0, "basis": "measured", "source": "sacct of the last two jobs"}},
      "max_walltime_h": 168.0,
      "outages": [{"from": "2030-11-11T00:00:00-05:00", "to": "2030-11-12T12:00:00-05:00", "reason": "maintenance"}],
      "budget": {"unit": "gpu-hours", "allocated": 1250.0, "used": 1200.0, "as_of": "2030-10-15",
                 "source": "usage report", "applies_to": ["h100_80gb"], "account": "def-example_gpu"},
                                                     // null when no cluster_budgets entry; applies_to [] = all jobs
      "rules": ["Only the first job of a group starts fast"]
    }
  ]
}
```

A workstation with no `blocked` and no `exceptions` is free at all times. `exceptions[].block` false means an
opening inside a blocked period (the user said "you may use ws02 this afternoon").

### 2a. Where each value comes from (the settings)

| machines.json | settings key |
|---|---|
| `timezone` | `owner.time_zone` |
| workstation `name`, `ssh_alias`, `ram_gb`, `threads` | `machines.workstations[].name`, `.ssh_alias`, `.ram_gb`, `.cpus` |
| workstation `gpu` | `machines.workstations[].gpus[]`: `count` summed, `memory_gb` of the smallest card, `model` (several models: joined, with a rule line) |
| workstation `blocked` | `machines.workstations[].usage_windows[]` (`days`, `from`, `to`, `reason`); `rule` must be `blocked` or empty |
| workstation `rules` | `machines.workstations[].notes`, plus a shared-machine line, plus facts `rules` |
| cluster `name`, `ssh_alias`, `scheduler`, `accounts` | `machines.clusters[].name`, `.ssh_alias`, `.scheduler`, `.slurm_accounts` |
| cluster `budget` (allocation part) | `skill_settings.compute-planner.cluster_budgets.<cluster>` |

`usage_windows[].days` accepts `"Mon-Fri"`, `"Fri-Mon"` (wraps over the weekend), `"Mon,Wed,Fri"`, a list
`["Sat", "Sun"]`, or `daily` / `every day` / `all` / `weekdays` / `weekend` / `weekends`. `from` and `to` are
`HH:MM`; `to` before `from` runs over midnight.

The planner's own options, `skill_settings.compute-planner.*` (all optional):

| Key | Meaning | Default |
|---|---|---|
| `planner_dir` | folder of the planner's files | `<paths.outputs_dir>/compute-planner` |
| `horizon_days` | length of a plan, days | `7` |
| `ram_heavy_gb` | jobs at or above this RAM never overlap on one workstation | `null` (no such rule) |
| `shared_machines` | workstations shared with other people (checked live, step 2.3) | `[]` |
| `shared_cpu_percent` | another user's steady CPU use above this blocks a shared machine | `30` |
| `cluster_budgets` | `{"<cluster>": {"unit", "allocated", "applies_to": [resource keys], "account"}}` | `{}` |
| `prefer_cluster_for_cpu` | cluster where CPU-only work goes whenever it can meet its deadline there | `""` |

### 2b. `machine_facts.json` (schema `compute-machine-facts/1`)

What the planner learns while it works and the settings do not hold. The skill writes it after showing the
evidence; every entry carries a `reason` or `source`. A missing file means "no facts yet".

```jsonc
{
  "schema": "compute-machine-facts/1",
  "updated": "2030-10-30T16:30:00-04:00",
  "workstations": {                                  // keyed by settings name; unknown names are ignored (warning)
    "ws02": {
      "exceptions": [{"from": "...", "to": "...", "block": true, "reason": "evidence"}],
      "rules": ["free text printed in the plan"]
    }
  },
  "clusters": {
    "mycluster": {
      "gpus": {"h100_80gb": {"model": "H100 80 GB"}, "cpu": {"model": "CPU nodes"}},
      "queue_wait_hours": {"h100_80gb": {"typical": 36.0, "basis": "measured", "source": "sacct Submit -> Start"}},
      "max_walltime_h": 168,
      "outages": [{"from": "...", "to": "...", "reason": "announced maintenance"}],
      "budget_used": {"used": 1200, "as_of": "2030-10-15", "source": "usage report"},
      "rules": ["Only the first job of a group starts fast"]
    }
  }
}
```

A cluster resource without a measured `queue_wait_hours` entry is not used (the planner never guesses a wait).

## 3. Commitments: a run-status JSON with `uses`

The planner reads what is already running or booked from a `run-status/1` file (the latest timeline,
`references/status_schema.md` of `run-status-figure`), refreshed live. The mapping:

- a stage's `model` must equal the `label` or the `name` of a machine in `machines.json` (for example
  `"ws02 · RTX 3060"` or `"ws02"`);
- every run with `status` `running` or `queued` is a commitment on that machine from its start (`start` or
  `estimate_start`) to its end (`estimate_end`); `done`, `failed` and `stopped` runs are ignored;
- what it consumes comes from the optional run field `"uses": {"gpu": true, "gpu_cards": 1, "gpu_memory_gb": 20,
  "ram_gb": 40, "threads": 12}`. `gpu_cards` defaults to 1 when `gpu` is true. Without `uses`: `gpu` = not
  (`stage.gpu_time` is false), other values 0;
- a commitment's `name` can be named in a task's `depends_on`.

So the timeline stays one file and the planner builds on it: `status_plan.json` is that same file with the proposed
blocks appended.

## 4. Outputs of `plan_schedule.py`

### `plan.json` (schema `compute-plan/1`)

```jsonc
{
  "schema": "compute-plan/1",
  "generated": "2030-10-30T17:40:00-04:00",
  "horizon": {"from": "...", "to": "..."},           // now → now + days
  "inputs": {"tasks": "<path>", "machines": "<path>", "commitments": "<path>"},
  "blocks": [                                        // in start order
    {"task_id": "test_set_inference", "title": "...", "kind": "run",   // run | measure | submit
     "machine": "ws01", "start": "...", "end": "...", "hours": 14.0, "part": "1/3",   // part when split
     "basis": "measured", "deadline_ok": true,
     "reason": "rank 1; earliest end among ws01, ws02",
     "warnings": ["ws02 would end Fri Nov 01 17:00 (20.0 h)"],
     "alternative": {"machine": "mycluster", "end": "..."}} // when a cluster option also existed
  ],
  "unplaced": [{"task_id": "...", "reason": "depends on mycluster_training, which has no end time"}],
  "waiting_for_measurement": [{"task_id": "...", "measure_block": "<block index>"}],
  "deadline_misses": [{"task_id": "...", "deadline": "...", "earliest_end": "..."}],
  "promotions": [{"task_id": "...", "above": ["..."], "reason": "hard deadline 2030-10-31"}],
  "budget_warnings": [{"cluster": "mycluster", "account": "def-example_gpu", "used": 1200, "allocated": 1250, "added": 80}],
  "free_windows": {"ws02": [{"from": "...", "to": "...", "gpu_free": true}]},
  "decisions": ["D1: Model A test-set inference on ws01 Sun Nov 03 08:00 → ... — yes/no", "..."]
}
```

### `plan.md`

The same facts for the user: a table of blocks (rank, task, machine, from → to, hours, basis + source, why here),
then unplaced tasks with reasons, deadline misses, promotions, budget warnings, the free windows left, the machine
rules (cluster accounts included), and the numbered decisions. Paths as `system:/absolute/path`. Short.

### `status_plan.json`

The commitments file plus one stage per task and machine, named `"PLAN · <rank>. <title>"`, on that machine's
lane (`model` = the machine label, `session` = the machine name), holding the task's blocks as runs: `status`
`queued`, `proposed` true, a short `label` written above the bar (`"20.0 h measured"`, `"(1/2)"` for a part,
`"measure 1.0 h"`; `" ?"` appended for an accepted estimate), `estimate_start`/`estimate_end`, `note` = basis.
A cluster job gives two runs on its row: the submit block (queue wait) and the run block; their labels are joined
on the submit bar (`"submit → start ≈ Fri Nov 01 05:00 · 20.0 h measured"`) and the run bar's label is empty, so
the two never overlap after a short wait. `render_status.py` draws `proposed` runs hatched with their label above
and adds the legend entry "proposed (plan)".
