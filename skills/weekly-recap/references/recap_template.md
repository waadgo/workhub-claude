# The weekly recap: content and Notion page

Written for the user only, in full detail. Plain words, numbers with units, every path as `system:/absolute/path`,
every job with its cluster and Slurm job ID, every row as `TK-n <title>` (the prefix is `notion.task_id_prefix`).

## Rules for the content

- **Facts only from the outputs.** Every number comes from `slurm_week.txt`, `tokens_week.txt`, a Done report, a
  topic card, a log entry or a turn of `rows/TK-n.txt`. Results not confirmed in a Done report or a verified output are
  marked "first look" or "not checked".
- **Paths are checked.** `ls -ld` every output path before writing it (through the open SSH connection for
  clusters, with `-o BatchMode=yes`). A path that no longer exists is written as "(moved or deleted since)".
- **Decisions are the user's words.** Quote them from `decisions.txt` with their time; never paraphrase a decision
  into something stronger.
- **Privacy.** No participant, patient or case identifiers (`sub-…`, scan IDs, file names that contain them) in
  the recap or Notion. Counts, cohort names, folds and folders instead. No participant images.
- **The recap changes nothing.** It reads the Work Hub, never writes it. Rows that look Done, rows with no
  conversation, work with no row: list them under "For the Monday check"; work-hub-cleanup decides.

## recap.md: sections, in this order

```markdown
# Week 2026-W45 (Mon Nov 2 – Sun Nov 8): recap

## In short
3–6 bullets: the main outcomes (finished rows, main results with numbers), the compute used
(core-h, GPU-h, against last week), what failed, the biggest open decision.

## Finished this week
Per row Done in the week (Done on inside the week, or a Done report confirmed in the week):
- **TK-n <title>** (Project): outcome in one sentence; main numbers; output `system:/path`.

## Progress by project
One subsection per Work Hub Project with activity, rows by Priority. Per row:
### TK-n <title> — <state at Sunday 24:00: Done / In progress / Running / Waiting …>
- **What happened:** dated steps (Tue Nov 3 – Thu Nov 5), from the row file and the topic card.
- **Results:** numbers, with "first look" where unverified.
- **Decided:** the user's words with the time (question ID, e.g. Q104).
- **Compute:** cluster, jobs, core-h, GPU-h, failures (from slurm_week.txt), workstation runs (machine, tmux, hours).
- **Outputs:** `system:/path` (checked).
- **Next:** the card's Next line.

## Work outside the Work Hub
Conversations with no row that made files or ran jobs: what they did, where (system:/path), whether a row should
exist. Short question-and-answer conversations: one line in total ("12 short questions: …").

## Compute
### Clusters
| Cluster | Jobs | OK | Failed / timeout | Core-h | GPU-h (model) | GPU-h badly ended | Median wait | Last week core-h / GPU-h |
Per Slurm account when the settings list more than one account for a cluster (from the "accounts:" lines).
### Workstations
| Machine | Run (row) | From → to | Hours | GPU? |
(from the orchestrator log and topic cards; workstations have no accounting)
### Problem jobs
One line each: cluster, job ID, name, state, ran X of Y h, row, cause if known.
### Efficiency
Groups with median CPU efficiency < 50 % or memory use < 30 % of the request, with the row: candidates
for smaller requests next time (the aim: CPU and memory use above 80 % of what was requested).
Figure: the week timeline (PNG).

## Claude usage
Total tokens and the four kinds; share per row (top 8), the orchestrator's share, per model; against last week.
One sentence on the biggest driver (for example a long orchestrator conversation, subagents).

## Decisions of the week
Question ID: the question in a few words → the user's answer, verbatim, with the time. Grouped by row.
(Without an orchestrator: decisions written in the row files or topic cards, or "none recorded".)

## Carried into this week
- Jobs still running or waiting at Sunday 24:00 (cluster, job ID, row).
- Open questions in QUESTIONS.md (question ID, row, what it blocks).
- Rows In progress or Running with no activity this week.
- Due dates in the coming 7 days (Hard ones first).

## For the Monday check (work-hub-cleanup)
Rows that look Done, rows with stale Status, work with no row, Auto-filed rows of the week.

## Coverage
Clusters read and not read (with the fix), Codex not counted in tokens, anything skipped (for example the
Research log or the orchestrator when the settings turn them off).

## Sources
The recap folder and its files, as system:/path; the Research log pages of the week that were read.
```

## The Notion page

Only when `notion.research_log.enabled` is true and `notion.research_log.data_source` is set; otherwise say in one
line that the Research log is not set up and that the recap stays in the local files.

Parent: the Research log data source (`notion.research_log.data_source`; fetch it first, the options may have
changed). Icon 🗓️.

| Property | Value |
|---|---|
| Name | `Weekly recap 2026-W45 (Nov 2 – Nov 8)` |
| Type | `Report` (existing option; no new option) |
| Project | leave empty (the recap covers every project) |
| Date | `date:Date:start` = Monday, `date:Date:end` = Sunday, `date:Date:is_datetime` = 0 |
| Clusters | the machine labels the Research log already uses for the clusters that ran jobs that week, plus the workstations' label when a workstation ran something (existing options; no new option without the user's yes) |
| Source key | `weekly-recap:2026-W45` (look for an existing page with this key first: a re-run edits that page instead of adding a second one) |

Page body: the content of `recap.md` in Notion-flavored Markdown (read `notion://docs/notion-flavored-markdown-spec`
once), with:
- under the title line, a quote block: `Local originals: <system>:<OUT>/` and
  `Made with the weekly-recap skill: <system>:<folder of this skill>`;
- the timeline PNG right after "## Compute" (upload: run-status-figure `references/notion.md` → "Uploading the
  figure"), and the PDF as a `<file>` under "## Sources";
- tables as plain pipe tables (Notion shows the first row as the header) or `<table header-row="true">` blocks;
- every file name such as `recap.md` or `DECISIONS.md` in inline code: Notion turns a bare `name.md` into a
  broken web link (`http://recap.md`);
- a final "## Personal notes" section that re-runs never overwrite.
