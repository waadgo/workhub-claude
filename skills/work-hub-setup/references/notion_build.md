# Building or adopting the Work Hub in Notion

Used by the work-hub-setup skill, step 5. Every call below was tested with the Notion connector's tools
(`notion-create-pages`, `notion-create-database`, `notion-update-data-source`, `notion-update-view`,
`notion-create-view`, `notion-update-page`, `notion-fetch`). `<PREFIX>` = `notion.task_id_prefix`,
`<DS>` = the Tasks data source id WITHOUT `collection://`, `<DB>` = the Tasks database id.

## A. Build a new hub (the user gave `notion.hub_parent_page_url`)

1. **Hub page.** `notion-create-pages` with `parent: {"type": "page_id", "page_id": <id from the parent URL>}`,
   one page, `icon: "🗂️"`, `properties: {"title": "<hub title>"}`, and this content (keep the credit callout as
   it is; the template's license asks for it):

   ```
   <callout icon="🏷️" color="gray_bg">
   **Work Hub template by Walter Adame-Gonzalez** · Claude skills that run it: [github.com/waadgo/workhub-claude](https://github.com/waadgo/workhub-claude) · Template license: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). You may use, copy and adapt it; please keep this credit line.
   </callout>

   <callout icon="📥" color="blue_bg">
   **Inbox:** jot new to-dos below in any format. Each weekday morning Claude files them into Tasks, keeping your wording and any priority tag, marks them *Auto-filed* and clears the Inbox. Claude never overrides a status or priority you set and never deletes a task. Optional shorthand on any line: `~2h`, `~1d`, `~1w` for effort, `after: <task>` for what it waits on, `due: Oct 30` (add `hard` if it cannot move), `[High priority]`.
   </callout>

   ## 📥 Inbox

   - [ ] 

   ## ✅ Tasks
   ```

   When the morning brief is off, drop the sentence "Each weekday morning Claude files them…" and write
   "Claude files them into Tasks when you ask ('file my inbox')". Keep the page id (`<HUB>`).

2. **Tasks database** (it is placed at the end of the hub page, under "✅ Tasks"). `notion-create-database` with
   `parent: {"type": "page_id", "page_id": "<HUB>"}`, `title: "Tasks"`,
   `description: "One row per task. New items go in the Inbox above; Claude files them here."`, and `schema`
   (fill `<PREFIX>` and the project options: the user's projects in their order, colors in turn blue, purple,
   green, pink, brown, orange, red, gray; "Writing and meetings" yellow if listed; "Miscellaneous" default, last):

   ```sql
   CREATE TABLE ("Task" TITLE, "ID" UNIQUE_ID PREFIX '<PREFIX>' COMMENT 'Automatic Task ID (<PREFIX>-1, <PREFIX>-2 ...). Used in conversation names, topic cards and questions. Never typed, never reused.', "Status" SELECT('Idea':purple, 'Backlog':gray, 'This week':yellow, 'In progress':blue, 'Queued on a cluster':orange, 'Running on a computer':blue, 'Waiting':orange, 'Done':green), "Priority" SELECT('Top':red, 'High':orange, 'Mid':yellow, 'Low':gray), "Effort" SELECT('≤1h':green, 'Half day':pink, '1 day':yellow, '2–3 days':orange, '1 week+':red) COMMENT 'Your hands-on time, not compute time', "Project" SELECT('Project A':blue, 'Project B':purple, 'Writing and meetings':yellow, 'Miscellaneous':default) COMMENT 'Main project the task belongs to (one per row). Every new row gets one.', "Due" DATE, "Due type" SELECT('Hard':red, 'Soft':yellow) COMMENT 'Hard = external deadline that cannot move; Soft = self-imposed target', "Flag" SELECT('Status unclear':yellow, 'Possible duplicate':orange, 'Auto-filed':blue) COMMENT 'Set by Claude for rows to look at; clear it once settled', "Notes" RICH_TEXT COMMENT 'Next / Where / Decided lines, then the original wording', "Added" DATE, "Done on" DATE COMMENT 'Day the row became Done (Claude writes it when it marks a row Done)', "Last edited" LAST_EDITED_TIME)
   ```

   **Prefix already in use.** A Unique-ID prefix must be unique in the whole Notion workspace; Notion answers
   `409 Unique ID prefix is already in use`. Then repeat the call with the fallback prefix the user gave in the
   questionnaire (default `WH`), and store that prefix (step 7). Do not ask again; say so in the final report.

   From the reply keep `<DB>` (database URL) and `<DS>` (the `collection://…` data source).

3. **Dependencies + inline display.** `notion-update-data-source` with `data_source_id: <DS>`, `is_inline: true`,
   `statements`:

   ```sql
   ADD COLUMN "Blocked by" RELATION('<DS>', DUAL 'Blocks' 'blocks') COMMENT 'Tasks that must finish before this one can start'
   ```

   This creates "Blocked by" and its mirror "Blocks" in one call.

4. **Formulas** (a second `notion-update-data-source` call; they need the relation):

   ```sql
   ADD COLUMN "Ready?" FORMULA('if(prop("Blocked by").filter(current.prop("Status") != "Done").length() > 0, "⛔ Blocked", ifs(prop("Status") == "Waiting", "⏳ Waiting", prop("Status") == "Queued on a cluster" or prop("Status") == "Running on a computer", "🖥️ On a computer", "✅ Ready"))') COMMENT 'Automatic: Blocked while any Blocked-by task is not Done'; ADD COLUMN "Score" FORMULA('lets(priorityPoints, ifs(prop("Priority") == "Top", 8, prop("Priority") == "High", 5, prop("Priority") == "Mid", 3, 1), deadlinePoints, if(empty(prop("Due")), 0, lets(daysLeft, dateBetween(prop("Due"), today(), "days"), ifs(daysLeft < 0, 10, daysLeft <= 3, 8, daysLeft <= 7, 5, daysLeft <= 14, 3, daysLeft <= 30, 1, 0) * if(prop("Due type") == "Hard", 1.5, 1))), unblockPoints, 2 * prop("Blocks").filter(current.prop("Status") != "Done").length(), effortDays, ifs(prop("Effort") == "≤1h", 1, prop("Effort") == "Half day", 1.5, prop("Effort") == "1 day", 2, prop("Effort") == "2–3 days", 3, prop("Effort") == "1 week+", 4, 2), isBlocked, prop("Blocked by").filter(current.prop("Status") != "Done").length() > 0, statusFactor, ifs(prop("Status") == "Done" or prop("Status") == "Idea", 0, isBlocked or prop("Status") == "Waiting", 0.25, prop("Status") == "Queued on a cluster" or prop("Status") == "Running on a computer", 0.5, 1), round(10 * statusFactor * (priorityPoints + deadlinePoints + unblockPoints) / effortDays) / 10)') COMMENT '(priority + deadline + open tasks it unblocks) / effort; x0.5 while a job runs or waits in a queue; x0.25 when blocked or waiting; 0 for Done and Idea'
   ```

   Formula text cannot be read back through the connector; the Score column appearing with numbers is the check.

5. **Views.** `notion-fetch` the database: it has one view, "Default view". Rename and configure it with
   `notion-update-view` (name "Now"), then create the others with `notion-create-view`
   (`database_id: <DB>`, `data_source_id: <DS>`), in this order:

   | Name | Type | `configure` |
   |---|---|---|
   | Now | table | `FILTER "Status" IN ("This week", "In progress", "Queued on a cluster", "Running on a computer"); SORT BY "Score" DESC; SHOW "ID", "Project", "Task", "Status", "Notes", "Due", "Due type"; ICON "🎯"` |
   | Focus | table | `FILTER "Status" IN ("This week", "In progress", "Queued on a cluster", "Running on a computer") OR ("Status" = "Backlog" AND "Priority" IN ("Top", "High", "Mid")); SORT BY "Score" DESC; SHOW "ID", "Project", "Task", "Score", "Ready?", "Status", "Priority", "Effort", "Due", "Due type", "Blocked by", "Flag"; ICON "🔭"` |
   | By project | table | `FILTER "Status" != "Done"; GROUP BY "Project"; SORT BY "Score" DESC; SHOW "ID", "Task", "Score", "Ready?", "Status", "Priority", "Effort", "Due", "Due type", "Blocked by", "Added", "Flag"; ICON "🗂️"` |
   | Board | board | `GROUP BY "Status"; SORT BY "Priority" ASC; SHOW "ID", "Task", "Priority", "Project"; ICON "🧱"` |
   | Blocked | table | `FILTER "Ready?" = "⛔ Blocked" AND "Status" != "Done"; SORT BY "Score" DESC; SHOW "ID", "Task", "Blocked by", "Score", "Status", "Priority", "Due", "Project"; ICON "⛔"` |
   | Needs your call | table | `FILTER "Flag" IS NOT EMPTY; SORT BY "Flag" ASC, "Added" ASC; SHOW "ID", "Task", "Flag", "Notes", "Status", "Project", "Added"; ICON "🙋"` |
   | Ideas | table | `FILTER "Status" = "Idea"; SORT BY "Added" DESC; SHOW "ID", "Task", "Project", "Added", "Notes"; ICON "💡"` |
   | Done | table | `FILTER "Status" = "Done"; SORT BY "Done on" DESC; SHOW "ID", "Task", "Project", "Priority", "Done on", "Notes"; ICON "✅"` |

   The view tool silently drops a filter it cannot build. Check that every reply shows an `advancedFilter`
   (Board has none on purpose). Keep the `view://…` addresses of Now and Focus (step 7).

6. **Reference section.** `notion-update-page`, `command: "insert_content"`, `position: {"type": "end"}`, with
   the content of section C below (replace `<PREFIX>`; drop the "Claude, automatic" part when the morning brief is
   off). Write `<toggle>` blocks, not `<details>`.

7. **Store the addresses** (one `configure.py set … --apply` per value; show the dry run first only if the user
   asked to see every write):

   ```bash
   python3 <skill>/scripts/configure.py set notion.hub_page_url "https://www.notion.so/<HUB>" --apply
   python3 <skill>/scripts/configure.py set notion.tasks_database_url "https://www.notion.so/<DB>" --apply
   python3 <skill>/scripts/configure.py set notion.tasks_data_source "collection://<DS>" --apply
   python3 <skill>/scripts/configure.py set notion.views.now "view://<now view id>" --apply
   python3 <skill>/scripts/configure.py set notion.views.focus "view://<focus view id>" --apply
   python3 <skill>/scripts/configure.py set notion.task_id_prefix "<PREFIX actually used>" --apply
   ```

8. **Verify.** `notion-fetch` the hub page: credit callout, Inbox callout, Inbox heading with one empty to-do,
   Tasks heading, the database with `inline="true"`, Reference toggle, in that order. `notion-fetch` the data
   source: 17 properties (Task, ID, Status, Priority, Effort, Project, Due, Due type, Flag, Notes, Added, Done on,
   Last edited, Blocked by, Blocks, Ready?, Score). `notion-fetch` the database: 8 views.

## B. Adopt an existing hub (the user gave `notion.hub_page_url`, for example a copy of the published template)

1. `notion-fetch` the hub page. Find the `<database … inline="true">` whose title is "Tasks"; fetch it for `<DB>`,
   `<DS>` and its views. No such database → tell the user, and offer to build one under this page (part A from
   step 2), which needs no new question.
2. Compare the data source with the 17 properties of A.8 and the Status, Priority, Effort, Due type and Flag
   options of A.2. Add what is missing with the A.2–A.4 statements (only the missing columns). Never drop or
   rename a column the user has; list extra columns in the report as "kept".
3. **Projects.** If the Project options differ from `notion.projects`, set them with
   `ALTER COLUMN "Project" SET SELECT(<the user's projects>, <every option already used by a row>)`. ALTER keeps
   only the options it lists, so first query the rows (`notion-query-data-sources`, `SELECT DISTINCT "Project"`)
   and keep every option in use.
4. **Prefix.** Read the prefix from one row: `notion-fetch` any row page; its `userDefined:ID` reads like
   `WH-1`. If it differs from the prefix the user chose, try
   `ALTER COLUMN "ID" SET UNIQUE_ID PREFIX '<PREFIX>'`; if Notion refuses (prefix in use, or the change is not
   allowed), keep the existing prefix and store that one in step A.7. Report which prefix is in use.
5. **Views.** Map the existing views by name to A.5. Create the missing ones; leave existing ones as they are
   (the user may have tuned them), except that a view named "Now" or "Focus" must exist: keep their addresses.
6. **Example rows.** Rows whose Notes start with "Example row" came with the template: list them in the final
   report and offer to delete them; never delete them without the user's yes.
7. Store the addresses (A.7) and verify (A.8).

## C. Reference section (content for A.6)

```
## 📚 Reference
<toggle>
How this system works (who does what, routine, rules)

	### Who does what
	**🔵 Notion, automatic** (nobody has to do anything)
	- **ID**: every row gets a permanent Task ID (for example <PREFIX>-42). Conversations, topic cards and questions use it. Never typed, never reused.
	- **Score**: (priority + deadline closeness + open tasks it unblocks) ÷ effort. Half while a job runs or waits in a queue, a quarter when Blocked or Waiting, 0 for Done and Ideas.
	- **Ready?**: ⛔ Blocked while any *Blocked by* task is not Done; ⏳ Waiting; 🖥️ On a computer; otherwise ✅ Ready.
	- **Blocks** fills itself as the mirror of *Blocked by*; **Last edited** stamps itself.
	- The views filter and sort themselves: **Now** (this week's rows, by Score), **Focus** (Now plus important Backlog rows), **By project**, **Board**, **Blocked**, **Needs your call** (rows Claude flagged), **Ideas**, **Done**.
	**🟢 You, in Notion**
	- Jot new to-dos in the 📥 Inbox, any format.
	- Mark a row **In progress** when you start and **Done** when you finish (that is what unblocks other rows).
	- Monday: choose this week's rows (status **This week**). Keep **Now** small: at most 7 rows, at most 3 In progress.
	- Fix anything Claude filed wrong, and clear a **Flag** once settled.
	**🟠 Claude, automatic** (the morning brief, if you turned it on)
	- Files every Inbox line into Tasks with Project, Priority, Effort, Due, Blocked by and your original wording in Notes; flags it *Auto-filed* (or *Possible duplicate*); clears the Inbox.
	- Writes a short morning brief: today's focus, what was filed, up to 3 rows that need your call.
	- Never deletes a row and never overrides a Status, Priority or Effort you set.
	**🟣 Claude, when you ask** (in Claude Code, with the workhub-claude skills)
	- One conversation per row: the conversation records its Task ID, keeps the row's **topic card** current, and is named after the row.
	- "Clean up my work hub": reads your recent conversations and jobs, asks you a few questions, then updates statuses, notes, due dates and dependencies.
	- "Weekly recap", "status" (a timeline figure of your runs), "plan my compute", "find the conversation where…".
	- A row becomes **Done** only after you confirm its **Done report** (what was done, result, outputs with full paths, script, where it ran).
	### The topic card (top of a row's page while it is active)
	- **State now:** one line on where the work stands.
	- **Next:** the next step.
	- **Waiting on:** a person, a machine, a question, or "nothing".
	- **Where:** the main folder, as machine:/full/path.
	- **Owner:** the conversation working on the row, and when the card was last updated.
	### Status values
	- **Idea** (not decided yet) → **Backlog** (decided, not this week) → **This week** → **In progress** → **Done**.
	- **Queued on a cluster** / **Running on a computer**: a job does the work now; **Waiting**: waits on a person or an answer.
	### Your routine
	**Every weekday (about 5 min)**
	1. Read the morning brief (or open **Now**).
	2. Glance at what was filed; fix anything wrong (silence = accepted).
	3. Answer one **Needs your call** row, or leave it for Friday.
	4. Start the first row of **Now**. New idea during the day → one line in the Inbox.
	**Monday (about 10 min)**: pick this week's rows; check **Blocked**: if a row waits on *you*, its blocker goes into this week.
	**Friday (about 10 min)**: finished → Done (after its Done report); unfinished → stay, or back to Backlog; settle **Needs your call**.
	**Monthly (about 15 min)**: scan **By project**; mark dead rows Done with a "dropped" note.
	### Rules that keep it working
	- The Inbox is the only way in: if it is only in your head or a terminal tab, it gets lost.
	- **Done** is the most valuable click: dependencies and scores depend on it.
	- Effort = your hands-on time, never compute time (a 3-day training with 30 min of setup is ≤1h plus a dependency).
	- Change Priority rarely; deadlines and dependencies already move rows up.
	### Connect Claude
	- Install the skills from [github.com/waadgo/workhub-claude](https://github.com/waadgo/workhub-claude), then tell Claude Code: "set up my work hub".
</toggle>
```
