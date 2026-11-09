# Status JSON format (`run-status/1`)

One file describes one moment. Times are ISO 8601 local times (`2026-01-15T10:48:00`), all in the zone named by
`timezone` (normally the owner's zone, `owner.time_zone` in the workhub-claude settings). Fields marked optional can
be left out; the matching panel then disappears. A complete example: `assets/example_status.json`.

```jsonc
{
  "schema": "run-status/1",
  "title": "Project A: model A vs model B",
  "subtitle": "Validation folds only · …",                    // optional, one sentence
  "timezone": "WET",                                         // printed after the time
  "generated": "2026-01-15T10:48:00",                        // "now": the time of the facts
  // "window": {"past_hours": 12, "future_days": 7},         // optional; this is the default span of the axis.
                                                             // "window": "all" fits every run instead (a record
                                                             // of finished work)

  "environment": {                                           // collect_env.py --merge fills most of it
    "system": "ws01", "cluster": "", "user": "alex", "gpu": "RTX 4090 (24.0 GB)",
    "working_dirs": [{"path": "ws01:/home/alex/projects/demo", "role": "Project A code (runs/)"}],
    "sessions": [
      {"kind": "tmux", "name": "demo_queue", "state": "running", "role": "runs the queue: …"},
      {"kind": "slurm", "name": "1001 train_fold3", "state": "running", "role": "2:10:00 of 12:00:00"}
    ]
  },

  "models": [                                                // optional; order = colour order
    {"name": "model A", "params": "103 M parameters",
     "lines": ["ViT-B encoder + decoder", "Input: images at half size"]}
  ],

  "stages": [                                                // timeline rows, top to bottom
    {
      "name": "model A · Part 1",
      "model": "model A",                                    // links the row to a model colour
      "session": "tmux demo_queue",                          // optional, printed under the name
      // "gpu_time": false,                                  // optional: a row that is not GPU work (power cut,
                                                             // copy); shown as "no GPU use", left out of totals
      "runs": [
        {"name": "fold0_part1", "label": "fold 0 · current", "status": "done",
         "start": "2026-01-15T01:37:00", "end": "2026-01-15T02:17:02",
         "scores": {"val": 0.853, "test": 0.765}},           // optional, free-form
        {"name": "fold1_part1", "label": "fold 1 · current", "status": "running",
         "start": "2026-01-15T10:12:00", "estimate_end": "2026-01-15T11:08:00",
         "note": "epoch 33/50"},                             // printed next to the running bar
        {"name": "fold1_part1_ext", "label": "fold 1 · extended", "status": "queued",
         "estimate_start": "2026-01-15T11:08:00", "estimate_end": "2026-01-15T12:33:00"}
      ]
    }
  ],

  "milestones": [                                            // optional "Done so far" list
    {"date": "2026-01-14", "status": "done", "text": "Built the project"},
    {"date": "to decide", "status": "blocked", "text": "A decision the user has to make"},
    {"date": "waiting", "status": "pending", "text": "Something waiting on someone else"}
  ],

  "results": {                                               // optional dot plot
    "title": "Results so far: Part 1 Dice per fold",
    "metric": "Dice (mean over images)",                     // x-axis label
    "facets": [
      {"title": "Validation: own fold (3 images)",
       "points": [{"row": "fold 0", "series": "model A", "variant": "current", "value": 0.853}],
       "summaries": [{"text": "model A mean: current 0.814, extended 0.805"}]}
    ]
  },

  "estimate_method": "mean duration of finished runs of the same kind; …",   // printed in the footer
  "notes": ["Validation = each fold's own 3 images …"],                       // printed in the footer

  "headlines": ["model A leads on every fold so far"],      // optional: extra summary bullets of the Notion page
  "notion": {                                               // used by notion_page.py (all optional)
    "project": "Project A",                                 // an existing Research log "Project" option
    "project_key": "project-a",
    "name": "Project A run status",                         // page name: "<date> <time> — <name>"
    "source_key_prefix": "project-a:status",                // "Source key" = <prefix>/<status folder name>
    "timezone_iana": "Europe/Lisbon",                       // for the date; default: owner.time_zone
    "clusters": ["Mycluster", "ws01"],                      // "Clusters" labels; default: the label of
                                                            // environment.system (see references/notion.md)
    "remake": "bash ws01:/home/alex/projects/demo/status_figure.sh"   // how to make the figure again
  }
}
```

## Run states

| `status` | Drawn as | Times used |
|---|---|---|
| `done` | solid bar | `start`, `end` |
| `running` | solid until now, light until `estimate_end`, with "▶ now: label — note" | `start`, `estimate_end` |
| `queued` | light bar | `estimate_start`, `estimate_end` |
| `failed` | red bar with ✕ | `start`, `end` |
| `stopped` | half-strength bar (started, nothing running it now) | `start`, `estimate_end` |

A running run whose `estimate_end` has passed is drawn as ending now: the estimate was too short.

A `queued` run with `"proposed": true` is a proposal (for example from the `compute-planner` skill) that the user
has not approved yet: it is drawn hatched (legend "proposed (plan)"), so it can never be mistaken for a booking.
Once the user says yes and the run is booked, the flag is dropped. The optional run field `"uses": {"gpu": true,
"gpu_memory_gb": 20, "ram_gb": 40, "threads": 12}` tells the planner what a running or booked run occupies; the
renderer ignores it.

## Session states

`running` (▶), `idle` (•, only a shell left), `missing` (✕). Slurm states are lower-case squeue states (`running`,
`pending`, …).

## Milestone states

`done` (✓ green), `pending` or `waiting` (○ gray), `blocked` (! orange: a decision or an action is needed).

## Results points

- `series` takes the colour of the model with that name, so use the model names.
- `variant` takes a marker shape: the first variant is filled, the others are hollow.
- `row` values are shared across facets, in the order they first appear.
- Keep it to one to three facets and at most about 12 rows.

## Privacy

The figure and its Notion page may be shared. Labels, run names, notes and milestones never contain identifiers of
people, participants, patients or cases (use folds, models, stainings, dataset parts), and the figure never shows
images of research participants.
