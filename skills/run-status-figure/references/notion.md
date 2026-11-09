# Publishing a status figure to the Research log

Every status figure is published, by default and without asking, as a new page of the user's Notion **Research
log** (the database that the `research-log` skill builds; its build procedure is
`research-log/references/build_database.md`). Each figure becomes its own page, so the pages form a history of the
work. Status pages have **Type = Status**; the Research log's **Status** view lists them, grouped by project,
newest first.

## Where the Research log is

Only in the workhub-claude settings file:

| Setting | Meaning |
|---|---|
| `notion.research_log.enabled` | `false` = never publish; say so in one line and stop at the local files |
| `notion.research_log.data_source` | the Research log's data source, `collection://…` (the page parent) |

`notion_page.py` reads both and writes them into `page.json` → `research_log`:
`{"publish": true, "parent": {"type": "data_source_id", "data_source_id": "…"}}`, or `{"publish": false,
"reason": "…"}`. When `publish` is false, skip publishing and tell the user in one line, using the reason, for
example: "Not published: no Research log is set up yet (notion.research_log.data_source is empty); say 'set up my
research log' to build one." Never search Notion for some other database to publish into.

Fetch the data source before writing: the property names below are fixed, but the options of Project and Clusters
are the user's own.

## The three steps

1. **Upload** the PNG and the PDF: call the Notion create-file-upload tool once per file, then send each file
   (see "Uploading the figure" below). Keep each `markdown_source` (`file-upload://…`).
2. **Write the page:**
   `python scripts/notion_page.py status.json --image <png source> --pdf <pdf source> [--headline "…"] -o page.json`.
   - **Headlines:** add one or two `--headline` bullets with the main finding so far (for example which model leads).
   - **Settings:** the status file's `notion` block sets the project, the page name, the source key, the Clusters
     labels and the remake command; see `status_schema.md`.
3. **Create the page:** call the Notion create-pages tool with
   - parent = `page.json` → `research_log.parent`;
   - one page with the `properties`, `icon` and `content` of `page.json`.

   Fetch the page afterwards and check that the image and the tables are there.

## Properties of a status page

| Property | Value |
|---|---|
| Name | `YYYY-MM-DD HH:MM — <project> run status`, e.g. `2026-01-15 14:00 — Project A run status` |
| Type | `Status` |
| Project | an existing option of the Research log's Project property (the user's projects, `notion.projects`); add a new option only if none fits (the research-log skill's maintenance pass says how); leave it out for a figure that covers several projects (a machine view) |
| Date | the status time: `date:Date:start` = ISO datetime with its offset, `date:Date:is_datetime` = 1 |
| Clusters | the labels of the machines where the work runs, e.g. `["Mycluster"]`, `["ws01"]` (several allowed) |
| Source key | `<project key>:status/<folder>`, e.g. `project-a:status/2026-01-15_1400_project-a` |

Icon: 📊 (session records use 🗂️).

**Machine labels** (the Clusters options) follow one rule, shared with the research-log skill and implemented in
`collect_env.py` → `machine_label()`:
1. `skill_settings.research-log.machine_labels`, a map from a machine-name prefix to a label (the longest matching
   prefix wins; `{"ws": "Lab"}` turns ws01 and ws02 into "Lab");
2. else a machine listed in `machines.clusters`: its name with a capital first letter (`mycluster` → `Mycluster`);
3. else the machine name itself (`ws01`).
A label that is not an option of Clusters yet must be added first (research-log skill, maintenance pass).

## Uploading the figure

1. create-file-upload with `{"filename": "<name>.png"}`.
2. Send the file with one multipart POST, adding every header the tool returned:
   `curl -s -X POST "<upload_url>" -H "<header>: <value>" ... -F "file=@/path/figure.png"`.
3. Put the returned `suggested_markdown` (or `![caption](<markdown_source>)`) in the page content. Do the same
   for the PDF (`<file src="...">`) so the vector version is downloadable.

Uploads expire if they are not placed on a page soon, so create the page right after uploading.

## Page body

`notion_page.py` writes it; this is what it contains and why. It follows the house style of the other Research log
pages:
- **Header lines:** date, machine and project.
- **Quote block:** the local originals, as `system:/absolute/path` in inline code.
- **Sections**, then a "Personal notes" section that automated updates never overwrite.

Template, Notion-flavored Markdown:

```markdown
Date: <mention-date start="2026-01-15" startTime="14:00" timeZone="Europe/Lisbon"/> · Cluster: Mycluster (mycluster) · Type: Status
Project: Project A (key `project-a`)
> Local originals: `mycluster:/home/alex/workhub/outputs/run-status-figure/2026-01-15_1400_project-a/status.png` (also `.pdf`, `status.json`)<br>Made with the run-status-figure skill: `mycluster:/home/alex/.claude/skills/run-status-figure`<br>Redaction: no participant or case identifiers.
![Run status, 2026-01-15 14:00](file-upload://…)
## Summary
- Done: … · Running now: … · Expected end of everything: …
## Runs by stage
<table header-row="true"> … one row per stage: runs done/running/queued, GPU time used, left, expected end … </table>
## Where it runs
- Working folders: `system:/path` (role) …
- Sessions: `tmux <name>`: state, role …
## Results so far
<table header-row="true"> … the numbers behind the dot plot … </table>
## Decisions needed
- …
## Files
<file src="file-upload://…">PDF version</file>
---
## Personal notes
*Reserved for your own notes. Automated updates never overwrite this section.*
```

The tables are the text twin of the figure: every number in the figure can be read without the picture.
Keep identifiers of participants, patients and cases out of the page; use folds, stainings, models. Never upload
an image of a research participant (the status figure never contains one).

## Without a Notion connection

In a session without the Notion tools (for example on a cluster), run `notion_page.py` without `--image`/`--pdf`,
save the output as `page.json` next to the figure, and tell the user it is not published yet, so that a session
with Notion can publish it (the research-log skill's pending queue can carry it:
`python3 <research-log>/scripts/research_log.py pending add <folder>/page.json`).

## Updating instead of adding

For a new status of the same work, create a new page: each status is a snapshot, and the view lists them by date.
Edit an existing status page only to fix a mistake in it.
