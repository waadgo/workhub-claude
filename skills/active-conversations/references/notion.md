# The Notion side: Conversation reports

Optional. When `python3 <skill folder>/scripts/notion_report.py --check` says `off`, the skill answers in the
chat only. To turn it on, the setup skill follows `references/build_database.md`, which creates everything below
and writes the addresses into the settings:

| Setting | What it holds |
|---|---|
| `notion.conversation_reports.enabled` | `true` once the databases exist |
| `notion.conversation_reports.parent_page_url` | the page under which the user wanted the reports |
| `notion.conversation_reports.data_source` | `collection://…` of the **Reports** database (parent of report pages) |
| `skill_settings.active-conversations.conversations_data_source` | `collection://…` of the **Conversations** database (parent of rows) |

`notion_report.py` reads these; never type an id into a call by hand. The hub page holds an explanation callout and
both databases, inline. Do not rewrite the hub.

## Reports (one page per request)

| Property | Type | Value |
|---|---|---|
| Report | title | `2026-01-08 16:00 · Open now · ws01` |
| Date | date and time | when the collector ran |
| Kind | select | Open now, Last N, Search |
| Query | text | `open now`, `last 10`, `last 5 matching “dataset cleaning”` |
| Requested from | text | the system name of the machine that asked (`ws01`, `mycluster`) |
| Sites checked, Not checked | multi-select | the site labels (`--schema` lists them) |
| Found, Open, Working, Waiting for you | number | counts |
| Conversations | relation | two-way with Conversations → Report |

The page body: a callout (what was asked, when, from where, counts), a "Sites" table (checked or not, found,
machines checked, notes such as empty tabs or how to reach an unchecked site), the legend, and a "Conversations"
heading. `notion-create-view` then appends, under that heading, a table view of the Conversations database
filtered on this report. (A page layout that shows the related rows automatically cannot be set through the
Notion tools, hence one view per report.)

## Conversations (one row per conversation per report)

| Property | Type | Value |
|---|---|---|
| Conversation | title | its name |
| State | select | 🟢 Working, ⏳ Background job, 💬 Waiting for you, ⚪ Closed |
| Site | select | the site labels |
| Machine | text | host(s) where it is open; empty when closed |
| Working directory | text | `system:/path` |
| Opened in | text | `VS Code + VS Code Remote-SSH on ws01`, `tmux session x on mycluster2`, `not open` |
| Started, Last message | date and time | from the transcript |
| What's going on | text | the one-line description |
| Requests | number | how many requests the user made in it |
| Session ID | text | full id |
| Resume | text | `cd <folder> && claude --resume <id>` |
| Report | relation | the report it belongs to |

Views: **All conversations** (newest message first; quick filters Site, State, Report) and **By site** (grouped by
Site). The same conversation appears once per report that found it; filter on Report to see one report.

## Rules for the calls

- `notion-create-pages` refuses a select value that is not an option ("the data source must be updated to add
  it"). Site, State, Kind and the multi-selects have fixed options. `notion_report.py` leaves out a site that is
  not an option yet and says so; to add it, run `notion_report.py --schema` and call `notion-update-data-source`
  with its `reports_alter` statements on the Reports data source and its `conversations_alter` statement on the
  Conversations data source. Each statement lists every option, existing ones too, because it replaces the list.
- Multi-select values: a JSON array of option names. Dates: ISO 8601 with offset, plus
  `date:<Name>:is_datetime` = 1; Notion keeps minutes, not seconds.
- Relation values: the page id (or URL) of the report.
- At most 100 pages per `notion-create-pages` call: split `rows.json` when it has more.
- Text written to Notion must hold no participant, patient or case identifiers and no secrets.
