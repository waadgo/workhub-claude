# Building the Research log in Notion

This is the one procedure that creates the Research log. Two skills follow it:
- **work-hub-setup**, during setup, when `notion.research_log.enabled` is true and `notion.research_log.data_source`
  is empty;
- **research-log**, when the user says "set up my research log" (or no log can be found and the user agrees).

No ids are written here: every page, database, data source and view id comes from Notion's answers during the
build. Each step below works with the Notion connector's tools (names vary between clients: `notion-fetch`,
`fetch`, `mcp__…__notion-fetch`; use the matching one).

## 1. The contract: what the other kit skills rely on

The property names, types and Type options are fixed, because several skills write and read the same database.
Never rename or drop them; a later maintenance pass only adds options.

| Property | Notion type | Values | Written by | Read by |
|---|---|---|---|---|
| `Name` | title | `YYYY-MM-DD — concise topic`; plans `YYYY-MM-DD — ExecPlan: …`; status `YYYY-MM-DD HH:MM — <name> run status`; weekly recap `Weekly recap YYYY-Www (<Mon> – <Sun>)` | all writers | all |
| `Type` | select | `Session`, `Plan`, `Report`, `Notes`, `Status` (exactly these five) | research-log (Session, Plan, Report, Notes), run-status-figure and compute-planner (Status), weekly-recap (Report) | views, audits |
| `Project` | select | the user's projects (`notion.projects`); may be empty (a weekly recap or a machine view covers several projects) | all writers | views (grouping) |
| `Date` | date | one day; a range (start–end) when the work spans days; date **and time** for Status pages | all writers | work-hub-cleanup and weekly-recap (sorted by Date, newest first; a range that began earlier still counts) |
| `Clusters` | multi_select | machine labels (rule: `SKILL.md` → "Machine labels"), e.g. `ws01`, `Mycluster` | all writers | readers of the week's work |
| `Source key` | rich_text | `<project key>:<path relative to the project root>`; status `<project key>:status/<folder>`; weekly recap `weekly-recap:YYYY-Www` | all writers | every writer, to find an existing page before writing |

Views (all table views, newest first):

| View | Filter | Group | Sort | Shown |
|---|---|---|---|---|
| Sessions (the renamed default view) | Type = Session | Project | Date desc, Name asc | Name, Date, Clusters |
| All documents | none | Project | Date desc, Name asc | Name, Type, Date, Clusters |
| Status | Type = Status | Project | Date desc, Name asc | Name, Date, Clusters |

Page conventions every writer keeps: icon 🗂️ (Session, Plan, Report, Notes), 📊 (Status), 🗓️ (weekly recap); a
final `## Personal notes` section that automated updates never change; the Source key in backticks.

## 2. Inputs

All from the workhub-claude settings file; `python3 <research-log>/scripts/research_log.py settings` prints them
resolved:

| Input | Settings key (printed field) | When it is empty |
|---|---|---|
| owner's first name | `owner.name` (`owner_name`) | title "Research records" without a name |
| parent page | `notion.research_log.parent_page_url` (`parent_page_url`) | ask once: a Notion page URL, or "private" (a private page) |
| projects and their keys | `notion.projects` + `skill_settings.research-log.project_keys` (`projects`) | one option, `Miscellaneous` |
| machine labels | `machines.*`, `skill_settings.research-log.machine_labels` (`all_labels`) | this machine's label (always present) |

Project names and labels must not contain apostrophes (`'`): they end the quoted option in the schema statement.
Ask the user for another name, or write a typographic apostrophe (`’`) instead.

## 3. Steps

1. **Tools.** Notion search, fetch, create-pages, create-database, update-data-source, create-view, update-view,
   update-page, create-file-upload and query-data-sources must be available, plus a shell with `curl` (uploads).
   Otherwise ask the user to connect Notion to this client and stop.
2. **Existing log?** Search Notion for "Research log" and keep the databases whose data source has the six
   properties of section 1. If one exists, ask once: use it, or build a new one. To use it, run the schema check of
   `SKILL.md` → "Maintenance pass" (adds missing options, never renames), then go to step 7.
3. **Records page.** Create the page that holds the log: `notion-create-pages` with
   `parent: {"page_id": <id from parent_page_url>}` (or, for "private", no parent so that it becomes a private page;
   some clients use `creation_mode: "draft"`), `allow_async: false`, icon 🗂️, title
   "<owner_name>'s research records" (or "Research records"), content:

   ```
   <callout icon="🗂️" color="gray_bg">
   	**Research log:** one page per session record, plan, report, note or status snapshot that Claude publishes from my projects. The local Markdown file is the original; each page holds its full text, a downloadable copy and a **Personal notes** section at the bottom that automated updates never touch. Views: **Sessions**, **All documents**, **Status**.
   </callout>
   ```

4. **Database.** `notion-create-database` under that page, title "Research log", schema (one statement; project
   options take the colours blue, purple, green, orange, red, brown, pink, yellow in order, then start again; the
   first label gray, then blue, green, purple, orange, red):

   ```sql
   CREATE TABLE ("Name" TITLE, "Type" SELECT('Session':blue, 'Plan':yellow, 'Report':green, 'Notes':gray, 'Status':orange), "Project" SELECT('<project 1>':blue, '<project 2>':purple), "Date" DATE, "Clusters" MULTI_SELECT('<label 1>':gray, '<label 2>':blue), "Source key" RICH_TEXT COMMENT 'project-key:path/of/the/original/document relative to the project root; identifies the page')
   ```

   Example with the projects "Project A" and "Miscellaneous" and the labels ws01 and Mycluster:
   `"Project" SELECT('Project A':blue, 'Miscellaneous':purple)`, `"Clusters" MULTI_SELECT('ws01':gray, 'Mycluster':blue)`.
5. **Views.** Fetch the new database. Rename its default view with `notion-update-view`, then create the two
   others with `notion-create-view` (`database_id`, `data_source_id`, type table), using these `configure` strings:

   | Name | `configure` |
   |---|---|
   | Sessions (the renamed default) | `FILTER "Type" = "Session"; GROUP BY "Project"; SORT BY "Date" DESC, "Name" ASC; SHOW "Name", "Date", "Clusters"` |
   | All documents | `GROUP BY "Project"; SORT BY "Date" DESC, "Name" ASC; SHOW "Name", "Type", "Date", "Clusters"` |
   | Status | `FILTER "Type" = "Status"; GROUP BY "Project"; SORT BY "Date" DESC, "Name" ASC; SHOW "Name", "Date", "Clusters"` |

   Check each answer's `advancedFilter`: the view tool silently drops a filter it cannot build. Fix and retry once;
   if it still fails, report which view lacks its filter.
6. **Verify** by fetching the database: the six properties with the types of section 1, the five Type options,
   three views (Sessions, All documents, Status), and the database under the records page.
7. **Hand back the address.** The data source URL (`collection://…`, from the fetch) belongs in
   `notion.research_log.data_source`. Only the work-hub-setup skill writes the settings file:
   - during setup, setup writes it (and keeps `notion.research_log.enabled` true);
   - when the research-log skill built the log on its own, give the user the value in one line and tell them to
     say "set up my work hub" to record it (setup finds the log with step 2), or to paste it into the settings file
     (`python3 <research-log>/scripts/workhub_config.py path` prints where that file is).
8. **Report** the link of the records page and of the database, and one line on how it fills: "after substantial
   work, or when you say 'publish this to Notion'; status figures and weekly recaps go there too."

## 4. Later changes

- **A new project or machine label:** the maintenance pass in `SKILL.md` adds the option (listing every existing
  option with its colour, plus the new one).
- **Never** rename or delete a property, a Type option or a view another skill uses, and never delete pages.
