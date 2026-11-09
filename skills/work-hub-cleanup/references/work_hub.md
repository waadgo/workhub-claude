# The Work Hub in Notion

The schema below is the one work-hub-setup builds. If a write fails because of a property or an option,
fetch the Tasks data source again and follow what it says now; never add an option to make a write pass.

## 1. Where things are

Every address comes from the settings file. `python3 <skill>/scripts/cleanup_log.py settings` prints them
all; one value: `python3 <skill>/scripts/workhub_config.py get <key>`. In the payloads below, the words in
angle brackets stand for these values.

| What | Setting | Written below as |
|---|---|---|
| Hub page | `notion.hub_page_url` | `<hub page>` |
| Tasks database (inline on the hub) | `notion.tasks_database_url` | |
| Tasks data source: parent of new rows, target of queries | `notion.tasks_data_source` (`collection://…`) | `<tasks data source>`; for create-pages, `<tasks data source id>` = the same without `collection://` |
| Now view | `notion.views.now` (`view://…`) | `<now view>` |
| Focus view | `notion.views.focus` (`view://…`) | `<focus view>` |
| Research log data source (read-only for this skill) | `notion.research_log.data_source`, used only when `notion.research_log.enabled` is true | `<research log data source>` |
| Task ID prefix | `notion.task_id_prefix` (default `TK`) | `TK` |

An empty optional address switches its feature off: no Now or Focus view → the rows queries of section 4;
no Research log → section 7 is skipped. Say so in one line of the report.

The hub page holds the Inbox (to-do lines the user jots down), the inline Tasks database and a Reference
section. A cleanup never edits the hub page. Each row is also a page: its body holds the topic card
(written by the owner conversation) and, once confirmed, the Done report.

A row's URL ends with its 32-character id (`https://www.notion.so/0123456789abcdef0123456789abcdef`). Use
the id as `page_id` and the full URL in relations.

## 2. Tasks properties

| Property | Values and meaning | What a cleanup may do |
|---|---|---|
| Task (title) | short imperative description | set on new rows; rename only when asked |
| ID (unique ID, prefix from the settings) | Task ID shown `TK-42`, numbered by Notion itself (SQL name `userDefined:ID`) | never write; quote it everywhere ("TK-42 …") |
| Status | Idea, Backlog, This week, In progress (hands-on work only), Queued on a cluster (a job waits in a cluster queue, nothing left for the user), Running on a computer (a job runs on a cluster or a workstation), Waiting, Done | with the user's OK, on rows without an open owner |
| Priority | Top, High, Mid, Low | set on new rows (Mid unless it blocks something or has a deadline) |
| Effort | ≤1h, Half day, 1 day, 2–3 days, 1 week+ (the user's hands-on time, not compute time) | set on new rows |
| Project | the names in `notion.projects` (Miscellaneous for the rest) | set on every new row, following the project rules below |
| Due, Due type | Due only where a person or a real deadline waits; Hard = external, cannot move; Soft = self-imposed | with OK; name Hard dates in the question |
| Flag | Status unclear, Possible duplicate, Auto-filed | clear a settled flag; set Status unclear when the user defers a row; leave Auto-filed to the user's weekly review |
| Blocked by / Blocks | relation to Tasks / its automatic mirror | Blocked by with OK or when the user stated it; Blocks never |
| Notes | up to three lines, `Next:` / `Where:` / `Decided:`, then `Original: …` | refresh facts freely |
| Added | day the row was created | set on new rows |
| Done on | day the row became Done; no automation fills it | write it when a row becomes Done; fill an empty one from evidence; never change an existing value |
| Last edited, Score, Ready? | automatic; Score and Ready? are formulas the connection cannot read | never |

**Project rules.** `cleanup_log.py settings` prints the user's own rules for which work goes in which
project (`skill_settings.work-hub-cleanup.project_rules`, plain sentences such as "Anything about the
survey goes to Project A"). Apply them in their order. Without rules, take the project whose name fits the
work, else Miscellaneous. When two projects fit, the new-row question names both and recommends one.

Who a row waits for goes in its Notes or card; there is no "Waiting on" column.

**Views:** Now, Focus, By project, Board, Blocked, "Needs your call", Ideas, Done. **Now** shows This week,
In progress, Queued on a cluster and Running on a computer, sorted by Score, within the Now limits of the
settings (`workflow.now_limits`: rows, and rows In progress). **Focus** shows the Now rows plus Backlog rows
of priority Top, High or Mid. Done is sorted by Done on.

## 3. How the user runs the hub

- **The Inbox is the way in.** The work-hub skill's morning tidy (or the orchestrator, when on) files each
  Inbox line as a row (Flag Auto-filed) and clears the Inbox; it never deletes rows or overrides the
  user's values.
- **Conversations** record their row (Task ID) and write its Status and topic card; a topic with no row
  gets one filed by its conversation (Auto-filed).
- **The user** marks rows In progress and Done during the day, fills Now, checks the hub once a week, and
  prunes dead rows about monthly.
- **The rule:** "Don't tidy what Claude tidies; the user's job is decisions: what's done, what's this week,
  what's first."

## 4. Reading

Use view mode, rows mode or fetch, never SQL mode. These are the `data` arguments of
`notion-query-data-sources`.

**Open rows** (Status is not Done):

```json
{"mode": "rows", "data_source_url": "<tasks data source>",
 "filter": {"type": "group", "operator": "and", "filters": [
   {"type": "property", "property": "Status", "propertyType": "select",
    "operator": "enum_is_not", "value": {"type": "exact", "value": "Done"}}]},
 "limit": 100}
```

Use it for the "all open" count, pass 3 of the "all open" scope, and the full Notes of every row.

**The scope rows, in the user's order** (the Now tab first, then the Focus tab, top to bottom). View mode
runs the view's saved filter and sort; both views sort by Score, highest first, so the rows come back in
the order shown in the tab:

```json
{"mode": "view", "view_url": "<now view>", "page_size": 100}
{"mode": "view", "view_url": "<focus view>", "page_size": 100}
```

The first is Now (pass 1), the second Focus (pass 2: drop the rows already in pass 1).

**When a view address is empty or its query fails**, take the open rows above and keep, by hand, the rows
of that view's filter: Now = Status This week, In progress, Queued on a cluster or Running on a computer;
Focus = those plus Backlog rows of Priority Top, High or Mid. Sort them by Priority (Top first), then Due
(earliest first, empty last), and say in the report that this order is not the tab's Score order.

**Rows edited since a moment** (Done included). The date filter needs a **plain date**: a date-and-time
value silently returns no row. `cleanup_log.py last` prints the query to use ("Notion query for those
edits"): filter on the plain UTC date it names, sort newest first, and keep the rows whose "Last edited"
(UTC) is at or after the UTC moment it names:

```json
{"mode": "rows", "data_source_url": "<tasks data source>",
 "filter": {"type": "group", "operator": "and", "filters": [
   {"type": "property", "property": "Last edited", "propertyType": "last_edited_time",
    "operator": "date_is_on_or_after", "value": {"type": "exact", "value": "2026-03-01"}}]},
 "sort": [{"property": "Last edited", "direction": "descending"}],
 "limit": 100}
```

- **Who changed a row:** rows mode has no history. The digest's "NOTION WRITES" lines show which
  conversation changed which row; other edits after the previous cleanup were the user or the morning tidy.
- **Formulas** come back as `formulaResult://…`. A row is ready when every Blocked by row is Done.
- **One row:** fetch its URL for the page body (topic card, Done report) and the full Notes.
- **Quota:** when rows mode is refused for quota, the two view queries still give the scope rows.

## 5. Writing

### Notes conventions

Notes are the row's current story, not a diary: up to three short lines, then the user's wording.

- **Next:** the next step; for work on a machine, what runs, where, and when it should end.
- **Where:** the files and folders.
- **Decided:** the user's decisions, each dated: "keep the 31 answers without age, impute later (Mar 2)".
- **Original:** the user's own wording, last and never changed.

Separate the lines with `\n`. Leave out a line with nothing to say. Rewrite a superseded fact (a count,
an ETA, which machine) instead of adding a sentence; keep no history. One or two sentences per line; a row
that needs more has probably grown, so consider a split. A Done row keeps the outcome, one `Where:` line
and the decisions. Relative dates become real dates or go. Paths as inline code with the machine name
(`` `ws01:/home/alex/projects/survey/` ``), every file name in backticks (Notion turns a bare
`run_queue.py` into `http://run_queue.py`). Name other rows by Task ID and title. No identifiers, no
secrets, no Research log links.

### Topic card (page body, written by the owner)

Five lines at the top of an active row's page body, rewritten in place by its owner conversation. A
cleanup reads them and never rewrites them; the confirmed Done report replaces the card.

```
State now: raw file checked (1,200 answers, 14 duplicates).
Next: remove the duplicates, then write the cleaning report.
Waiting on: nothing.
Where: ws01:/home/alex/projects/survey/
Owner: [Mar-02 09:10][ws01][UC] TK-42 Clean survey data, since 09:10. Updated Mar 2 13:20.
```

### Done report (page body)

A row is Done only when the user knows where its outputs are and how they were made. Draft the report from
evidence, show it in the Done check (`references/questions.md`), and write it only after the user
confirmed it. Leave out a line with nothing to say:

```
## Done report
**Outcome:** survey cleaned: duplicates removed, 31 answers without age kept and marked.
**Result:** 1,186 of 1,200 answers kept (14 exact duplicates removed); no other change. Table: `ws01:/home/alex/projects/survey/reports/cleaning_summary.txt`
**Outputs**
- Clean table: `ws01:/home/alex/projects/survey/clean/survey_clean.csv` (1 file, 2.1 MB)
- First analysis: made by TK-43 'Fit the first survey model' (not yet)

**Made by**
- Script: `ws01:/home/alex/projects/survey/scripts/clean_survey.py`
- Command: `python3 scripts/clean_survey.py --input raw/survey.csv --output clean/`
- Ran on: ws01, tmux `survey_clean`, Mar 2

**Inputs:** `ws01:/home/alex/projects/survey/raw/survey.csv`
**Records:** log `…/logs/clean.log`; session record `…/docs/sessions/2026-03-02_1015_survey_cleaning.md`
**Confirmed by Alex:** Mar 2
```

- Numbers come from the results file, with their caveats. Every path comes from evidence and was checked
  with `ls -ld` just before the Done check (a missing path is not written); folder only when a file name
  holds an identifier. The exact command when it fits on a line, else the script or record holding it.
- The "Confirmed by" line uses the owner's name from the settings (`owner.name`).
- An empty line after each bullet list (or Notion attaches the next bold line to it). About 6–15 lines.
- At the start of the body, in place of the topic card, anything else kept below. A row that already has
  a report gets only that report replaced (`update_content`).

Payload (`notion-update-page`), then "Mark Done" below:

```json
{"page_id": "<row id>", "command": "insert_content",
 "position": {"type": "start"}, "allow_async": false,
 "content": "## Done report\n**Outcome:** …\n**Outputs**\n- …\n\n**Confirmed by Alex:** Mar 2\n"}
```

### Payloads

`notion-update-page` with `command: "update_properties"` and `allow_async: false` (final when it returns).

**Mark Done:**

```json
{"page_id": "<row id>", "command": "update_properties", "allow_async": false,
 "properties": {"Status": "Done", "date:Done on:start": "2026-03-02", "date:Done on:is_datetime": 0,
   "Notes": "Done Mar 2 (Alex, Mar 2): 1,186 answers kept, 14 duplicates removed.\nWhere: `ws01:/home/alex/projects/survey/clean/`\nOriginal: …"}}
```

- **Drop a row** (never delete): `{"Status": "Done", "Flag": null, "date:Done on:start": "2026-03-02",
  "date:Done on:is_datetime": 0, "Notes": "Dropped (Alex, Mar 2): superseded by TK-n '…'. Original: …"}`
- **Move a due date:** `{"date:Due:start": "2026-03-20", "date:Due:is_datetime": 0, "Notes": "…"}`. Clear
  it with `"Due": null` (or `"date:Due:start": null` if refused).
- **Dependencies:** the complete list, because the value is replaced:
  `{"Blocked by": ["https://www.notion.so/<existing row id>", "https://www.notion.so/<new row id>"]}`.
- **New rows** (`notion-create-pages`; several per call):

```json
{"parent": {"data_source_id": "<tasks data source id>"}, "allow_async": false,
 "pages": [{"properties": {
   "Task": "Write the survey cleaning report for the team",
   "Status": "Backlog", "Priority": "Mid", "Effort": "Half day", "Project": "Project A",
   "date:Added:start": "2026-03-02", "date:Added:is_datetime": 0,
   "Notes": "Added Mar 2 (Alex) from the conversation '…'. What, why, where (`<machine>:/path`), first step."}}]}
```

New rows: Status Backlog (In progress when work already started); Due only when the user gave one; an
unknown external deadline → Priority Mid and ask for the date (then Due, Due type Hard, Priority High);
no Flag, because the user approved the row.

### Pitfalls

- SQL mode can lag by days; date filters need a plain date.
- Relations and Notes are replaced as a whole: re-read the row right before writing.
- A select value must already be an option (Status, Priority, Effort, Project, Flag, Due type); a new
  option is a schema change and needs the user's yes.
- Dates are `date:<Property>:start` ("YYYY-MM-DD") plus `date:<Property>:is_datetime` = 0.
- After writing, look for `http://<name>.<ext>` in the Notes and rewrite those with backticks.

## 6. Filing the Inbox (the morning tidy and the orchestrator, not a cleanup)

For reference, so filings look the same: one row per Inbox line, with `Original: <line>` last in Notes;
Project by the project rules; Priority from a `[High priority]`-style tag, else Mid; Effort from `~2h` /
`~1d` / `~1w` (Half day, 1 day, 1 week+); Blocked by from `after: <task>`; Due from `due: Mar 30` (Hard only
if the line says `hard`); Flag Auto-filed, or Possible duplicate when an open row covers it. Then replace
the Inbox lines with one empty `- [ ] ` (`update_content` with the exact old text). An unfinished line
("Maybe ask") is not filed.

## 7. Research log (read-only evidence)

Only when `notion.research_log.enabled` is true and `notion.research_log.data_source` is filled; otherwise
skip this section and say so in one line. The Research log holds one page per published session record,
plan or report, often from machines the scripts cannot reach. Use it only for sessions the scripts could
not read; never link or write it. Take the newest pages and keep those whose Date range touches the period
(a Date filter misses ranges that began earlier):

```json
{"mode": "rows", "data_source_url": "<research log data source>",
 "sort": [{"property": "Date", "direction": "descending"}],
 "limit": 30}
```
