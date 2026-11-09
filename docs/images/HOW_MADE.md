# How the README figures were made

Both figures come from the kit's own fictional example files (machines ws01, ws02, mycluster; user alex); no real
data. Run from the repository root with a Python that has matplotlib and Pillow; `<tmp>` is any scratch folder.

```bash
export CC_CLUSTER=ws01 WORKHUB_CLAUDE_CONFIG=skills/compute-planner/assets/example_settings.json

# run-status-figure: the bundled example
python skills/run-status-figure/scripts/render_status.py --example -o <tmp>/status_example

# compute-planner: plan the example tasks, then draw the plan (hatched = proposed)
python skills/compute-planner/scripts/planner_settings.py machines \
  --facts skills/compute-planner/assets/example_machine_facts.json -o <tmp>/plan/machines.json
python skills/compute-planner/scripts/plan_schedule.py --tasks skills/compute-planner/assets/example_tasks.json \
  --machines <tmp>/plan/machines.json --commitments skills/compute-planner/assets/example_commitments.json \
  -o <tmp>/plan --now 2030-10-30T17:00:00-04:00 --days 7
# (title and subtitle of <tmp>/plan/status_plan.json set to "Example: compute plan for the next 7 days" / "Made-up …")
python skills/run-status-figure/scripts/render_status.py <tmp>/plan/status_plan.json -o <tmp>/compute_plan_example
```

The 200-dpi PNGs were then resized to 1800 px wide and reduced to 256 colours with Pillow
(`resize(..., LANCZOS)`, `quantize(256, method=FASTOCTREE, dither=NONE)`), which keeps them under 100 KB.
