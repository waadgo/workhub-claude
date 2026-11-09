# Work Hub: Notion payloads and pitfalls

Part of the `work-hub` skill (workhub-claude, Apache-2.0). Values in `<angle brackets>` come from
`scripts/hub_context.py` (SKILL.md section 0) or from the row being written. Every id and path below is a
placeholder. If a write fails because of a property or an option, fetch the Tasks data source again and follow
what it says now; never add an option to make a write pass.

## 1. Reading

`notion-query-data-sources`; every argument goes inside `data`. Read live: rows mode, view mode or fetch, never
SQL mode (it can return values several days old).

**Now and Focus, in the order the tab shows** (both views sort by Score, highest first):

```json
{"data": {"mode": "view", "view_url": "<now_view>", "page_size": 100}}
{"data": {"mode": "view", "view_url": "<focus_view>", "page_size": 100}}
```

**Every open row, highest Score first:** the same with the By project view, whose URL is listed in the fetched
Tasks database (`tasks_database_url`). When the answer has `has_more: true`, ask again with
`"start_cursor": "<next_cursor>"`.

**Open rows in rows mode** (full Notes; the fallback when a view address is missing; select sorts follow the
option order, so Top comes first):

```json
{"data": {"mode": "rows", "data_source_url": "<tasks_data_source>",
  "filter": {"type": "group", "operator": "and", "filters": [
    {"type": "property", "property": "Status", "propertyType": "select",
     "operator": "enum_is_not", "value": {"type": "exact", "value": "Done"}}]},
  "sort": [{"property": "Priority", "direction": "ascending"}, {"property": "Due", "direction": "ascending"}],
  "limit": 100}}
```

**One row by Task ID** (`TK-42` → the number 42). Rows mode returns the ID as the bare number in
`userDefined:ID` (`"42"`); show it as `<PREFIX>-42`.

```json
{"data": {"mode": "rows", "data_source_url": "<tasks_data_source>",
  "filter": {"type": "group", "operator": "and", "filters": [
    {"type": "property", "property": "ID", "propertyType": "auto_increment_id",
     "operator": "number_equals", "value": {"type": "exact", "value": 42}}]},
  "limit": 1}}
```

If the filter is refused, query the open rows and match `userDefined:ID`. Then fetch the row's `url` for its page
body (topic card or Done report), its full Notes and its relations.

**Rows edited since a moment** (Done rows included). The date filter needs a **plain date**: a date-and-time value
silently returns no row. Filter on the day, then keep the rows whose "Last edited" (UTC) is at or after the moment
converted to UTC:

```json
{"data": {"mode": "rows", "data_source_url": "<tasks_data_source>",
  "filter": {"type": "group", "operator": "and", "filters": [
    {"type": "property", "property": "Last edited", "propertyType": "last_edited_time",
     "operator": "date_is_on_or_after", "value": {"type": "exact", "value": "2026-11-03"}}]},
  "sort": [{"property": "Last edited", "direction": "descending"}], "limit": 100}}
```

- **Formulas** (Score, Ready?) come back as `formulaResult://…` placeholders. A row is ready when every Blocked by
  row is Done; the Score order comes only from view mode.
- **Who changed a row** is not available: rows mode has no history.

## 2. Writing row properties

`notion-update-page` with `command: "update_properties"` and `allow_async: false` (final when it returns).
`page_id` = the 32 hexadecimal characters at the end of the row's URL; relations take full row URLs.

```json
{"page_id": "0123456789abcdef0123456789abcdef", "command": "update_properties", "allow_async": false,
 "properties": {"Status": "Running on a computer"}}
```

The `properties` value for the usual changes:

```text
Status:        {"Status": "Queued on a cluster"}
Mark Done:     {"Status": "Done", "date:Done on:start": "<today>", "date:Done on:is_datetime": 0,
                "Notes": "Done Nov 4: <outcome>.\nWhere: `ws01:/home/alex/projects/demo/outputs/`\nDecided: <decisions>\nOriginal: <kept>"}
Drop:          {"Status": "Done", "Flag": null, "date:Done on:start": "<today>", "date:Done on:is_datetime": 0,
                "Notes": "Dropped (<owner>, Nov 4): <reason>.\nOriginal: <kept>"}
Due date:      {"date:Due:start": "2026-11-21", "date:Due:is_datetime": 0, "Due type": "Soft"}
Clear due:     {"Due": null}            (if refused: {"date:Due:start": null})
Dependencies:  {"Blocked by": ["<row URL of blocker 1>", "<row URL of blocker 2>"]}
Clear a flag:  {"Flag": null}
Waiting:       {"Status": "Waiting", "Notes": "Waiting on: <who or what>, since Nov 4.\nOriginal: <kept>"}
```

Notes and Blocked by are replaced as a whole: read the row right before, and send the complete text or list.
Notes of an owned row change only when the user asks (SKILL.md 4.4); a row with a topic card keeps its
`Waiting on:` line in the card instead.

## 3. New rows

`notion-create-pages` with the data source as parent; several rows can go in one call. Leave out Flag when the
user dictated the row ("add a task …"); set Auto-filed for filed Inbox lines and a conversation's own topic row.
Fetch the new row afterwards to read its Task ID (the `userDefined:ID` number).

```json
{"parent": {"data_source_id": "<tasks_data_source_id>"}, "allow_async": false,
 "pages": [{"properties": {
   "Task": "Summarize the pilot results for the group meeting",
   "Status": "Backlog", "Priority": "Mid", "Effort": "Half day", "Project": "<one of projects>",
   "date:Added:start": "<today>", "date:Added:is_datetime": 0,
   "Flag": "Auto-filed",
   "Notes": "Next: draft the summary table.\nOriginal: summarize the pilot results for Friday ~2h"}}]}
```

## 4. The hub page: Inbox lines

Fetch the hub page first (`hub_page_url`) and copy the Inbox part exactly as fetched, escapes included (`\~`, `\[`).

**Add a line** ("add to Inbox: …"): keep the existing lines; when the Inbox holds only the empty `- [ ] `, the new
line takes its place.

```json
{"page_id": "<hub page id>", "command": "update_content", "allow_async": false,
 "content_updates": [{"old_str": "<exact text from '## 📥 Inbox' through '## ✅ Tasks'>",
                      "new_str": "## 📥 Inbox\n<the existing lines, unchanged>\n- [ ] <the new line>\n## ✅ Tasks"}]}
```

**Clear the filed lines** (morning routine step 3): the same call with `new_str` = `"## 📥 Inbox\n<the unfiled
lines exactly as they were>\n## ✅ Tasks"`, or `"## 📥 Inbox\n- [ ] \n## ✅ Tasks"` when every line was filed.
Never erase a line that was not filed; change nothing else on the page.

## 5. The row page body: topic card and Done report

**Topic card, first write** (the row has no card yet):

```json
{"page_id": "<row id>", "command": "insert_content", "position": {"type": "start"}, "allow_async": false,
 "content": "State now: part 1 done (1,110 files, 0 failed); part 2 running on the ws01 GPU.\nNext: part 3 on ws02 tonight; merge Wednesday morning.\nWaiting on: none.\nWhere: `ws01:/home/alex/projects/demo/outputs/`\nOwner: [Nov-04 09:10][ws01][UC] TK-42 Demo rebuild, since 09:10. Updated Nov 4 13:20.\n"}
```

**Topic card, update:** `update_content` with `old_str` = the five card lines exactly as fetched and `new_str` =
the five new lines. Rewrite in place, never append a second card.

**Done report** (only after the user's ok, SKILL.md 4.6). It replaces the topic card (`update_content`, `old_str` =
the card lines); a row without a card gets it with `insert_content` at the start; a row that already has a report
gets only that report replaced. Leave out a line with nothing to say; put an empty line after each bullet list
(otherwise Notion attaches the next bold line to the list):

```text
## Done report
**Outcome:** the demo dataset was rebuilt; 1,110 files, none failed.
**Result:** the checksums of all files match the source list; the summary table is `ws01:/home/alex/projects/demo/outputs/summary.csv`.
**Outputs**
- Rebuilt files: `ws01:/home/alex/projects/demo/outputs/` (1,110 files, 4.2 GB)
- Figures for the report: made by TK-43 "Plot the demo summary" (not yet)

**Made by**
- Script: `ws01:/home/alex/projects/demo/scripts/rebuild.py`
- Command: `python3 rebuild.py --input raw/ --output outputs/`
- Ran on: ws01, tmux `demo_rebuild`, Nov 3–4

**Inputs:** `ws01:/home/alex/projects/demo/raw/`
**Records:** log `ws01:/home/alex/projects/demo/logs/rebuild.log`; session record `ws01:/home/alex/projects/demo/docs/sessions/2026-11-04_0910_demo_rebuild.md`
**Confirmed by <owner>:** Nov 4
```

Then the properties of "Mark Done" (section 2) in a second call.

## 6. The Morning Brief page

A child page of the hub titled "Morning Brief" with the icon ☀️. **First time** (no `brief_page_url` and no
`<page …>Morning Brief</page>` in the fetched hub page):

```json
{"parent": {"page_id": "<hub page id>"}, "allow_async": false,
 "pages": [{"properties": {"title": "Morning Brief"}, "icon": "☀️",
   "content": "Updated Mon 2026-11-09 07:40 WET\n## Today's focus\n1. TK-42 Rebuild the demo dataset: due Nov 12 (Hard), unblocks TK-43.\n\n## Filed this morning\n- TK-44 Book the meeting room (Auto-filed)\n\n## Needs your call\n- TK-31 Merge with TK-12? Both describe the same figure.\n"}]}
```

**Every later morning:** replace its content, so the link stays the same:

```json
{"page_id": "<brief page id>", "command": "replace_content", "allow_async": false,
 "new_str": "Updated <now>\n## Today's focus\n…"}
```

The brief page has no child pages. If `replace_content` says child content would be deleted, stop and tell the
user instead of retrying with `allow_deleting_content`.

## 7. Pitfalls

- SQL mode can lag by days: read with rows mode, view mode or fetch. Date filters need a plain date.
- Ready? and Score read as `formulaResult://…`: derive readiness from Blocked by; take the Score order from a view.
- A select value (Status, Priority, Effort, Project, Flag, Due type) must already be an option. A new option is a
  schema change and needs the user's yes (SKILL.md section 9); never add one quietly to make a write pass.
- Never write ID, Blocks, Score, Ready? or Last edited.
- Page text comes back escaped (`\~`, `\[`, `\]`, `\*`): unescape it before writing it into a title or Notes; keep it
  escaped in an `old_str`.
- Creating or changing pages may run in the background unless `allow_async: false` is passed; pass it whenever the
  next step needs the page or its URL.
- Dates are `date:<Property>:start` (`"YYYY-MM-DD"`, the user's local date) plus `date:<Property>:is_datetime` = 0.
- After writing Notes or a page body, look for `http://<name>.<ext>`: a bare file name that Notion turned into a
  link. Rewrite it in backticks.
- Notes, relations and page bodies are replaced as a whole: re-read the row right before writing.
