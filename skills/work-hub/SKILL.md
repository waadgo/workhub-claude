---
name: work-hub
description: Run the user's Notion Work Hub day to day (Inbox + Tasks database with Task IDs, statuses, priorities, due dates, dependencies, Score, and the Now and Focus views). Files the Inbox and writes the Morning Brief, answers "what should I work on?", runs the Monday check, plans and closes the week, adds, finishes, drops or links tasks, and keeps one topic = one row = one conversation (records the row, writes its Status and topic card, handles a second conversation on an owned row, shows a Done report the user confirms before a row becomes Done). Use it whenever the user says "file my inbox", "morning tidy", "morning brief", "what should I work on?", "plan my week", "close the week", "add a task", "add to Inbox", "new topic", "continue TK-42", "park", "wrap up for today", "X is done" or "drop X", or asks to change tasks in their Work Hub, even without saying Notion. Not for building the hub or changing its settings (work-hub-setup) or for syncing it with recent conversations and jobs (work-hub-cleanup).
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Work Hub

A Work Hub is one Notion page: a 📥 Inbox where the user jots to-dos in any format, a ✅ Tasks database where each
to-do becomes a row with its own Task ID, and a 📚 Reference section explaining the routine. Notion computes
priorities and dependencies by itself; Claude does the clerical work; the user only makes decisions: what is done,
what is this week, what comes first. Each topic the user works on with Claude is one row, worked on by one
conversation at a time.

`<skill>` below is the folder that holds this file. Notion tool names vary between clients (`notion-fetch`,
`fetch`, `mcp__…__notion-fetch`); use the matching one. Payloads: `references/notion_payloads.md`.

## Which part to follow

| The user wants | Section |
|---|---|
| anything below | 0 first, then 1–3 apply |
| this conversation starts or continues a topic: "new topic: …", "continue TK-42", "park", "wrap up for today" | 4 |
| to add, finish, drop, link or move tasks; "add to Inbox: …" | 5 |
| "morning tidy", "file my inbox", "morning brief", or a scheduled morning run | 6 |
| "what should I work on?", "plan my week", the Monday check, "close the week", monthly prune | 7 |
| "clean up / update / sync my work hub" | 8 (another skill) |
| new options, columns or views, Score weights, settings or schedule changes | 9 |
| no hub yet, or no settings on this machine | "set up my work hub" (skill work-hub-setup) |

## 0. Settings check (start of every procedure)

1. Run `python3 <skill>/scripts/workhub_config.py check`. If it fails, tell the user: "Your work hub is not set up
   on this machine yet: say 'set up my work hub'." and stop.
2. Run `python3 <skill>/scripts/hub_context.py`. It prints, with this skill's defaults applied: `now` and `today`
   in the user's time zone, `owner_name`, `system_name`, the Notion addresses, `task_id_prefix`, `projects` and
   `catch_all_project`, the Now limits, `max_active_conversations`, `conversation_names`, `orchestrator_enabled`,
   the brief options, and `missing:` / `warning:` lines.
   - Exit status 3 (a `missing:` line): the hub page or the Tasks data source address is empty. Tell the user to
     say "set up my work hub" and stop.
   - Each `warning:` says what is skipped or done instead; say it in one line when it affects the request.
3. Use these values and nothing else. Below, `<PREFIX>` = `task_id_prefix` (Task IDs look like `TK-42`),
   `<owner>` = `owner_name`, `<today>` = `today` (every date written to Notion is this local date), and
   `<machine>` = `system_name`. Never write the settings file: only the setup skill does.

## 1. Finding the hub

Addresses come only from the settings file, as printed by `hub_context.py`:

| Value | Use |
|---|---|
| `hub_page_url` | the hub page: callout, Inbox, Tasks, Reference, the Morning Brief child page |
| `tasks_database_url` | fetch it for the URLs of all eight views (By project, Blocked, Done …) |
| `tasks_data_source` | `collection://…`: the target of queries; `tasks_data_source_id` (without `collection://`) is the parent of new rows |
| `now_view`, `focus_view` | the `view://…` addresses of the Now and Focus views |

- Never search Notion for another hub and never guess an address.
- The hub page or the data source cannot be fetched (deleted, moved, no access): tell the user, suggest "set up my
  work hub", and stop.
- A view address is empty: take that view's URL from the fetched Tasks database (views are listed by name); still
  none: read the open rows in rows mode, sorted by Priority, then Due, and say so. `tasks_database_url` empty: the
  `<database …>` tag of the fetched hub page gives it.

## 2. How the hub works

### Page layout

1. A blue callout explaining the Inbox and its shorthand.
2. "## 📥 Inbox": a to-do list. A single empty `- [ ]` means it is empty.
3. "## ✅ Tasks": the inline database.
4. "## 📚 Reference": a toggle "How this system works (who does what, routine, rules)".
5. "☀️ Morning Brief": a child page, when the brief goes to Notion.

Never rewrite the hub page. The only edits allowed: a line added to the Inbox ("add to Inbox: …"), filed lines
taken out of it (section 6), the Morning Brief child page, and whatever the user explicitly asks for.

Each row is also a page. Its body holds the **topic card** while the row is worked on (4.3) and the **Done
report** once the user confirmed it (4.6).

### Tasks properties

| Property | Values and meaning | Who changes it |
|---|---|---|
| Task (title) | short imperative description | set on new rows; rename only when asked |
| ID (unique ID) | Task ID `<PREFIX>-42`, numbered by Notion itself, never reused | never written; quote it with the title everywhere ("TK-42 Write the methods") |
| Status | Idea, Backlog, This week, In progress (hands-on work), Queued on a cluster (a submitted job waits in a scheduler queue; nothing left for the user), Running on a computer (a job runs on a cluster or a workstation), Waiting (for a person, an answer or an event), Done | the user; the row's owner conversation (4.2); anyone else only with the user's OK |
| Priority | Top, High, Mid, Low | new rows: a tag in the user's words wins; otherwise High with a Hard due date or when it blocks an open row; otherwise Mid. Existing rows: only when the user asks |
| Effort | ≤1h, Half day, 1 day, 2–3 days, 1 week+ = the user's hands-on time, never compute time | set on new rows |
| Project | the `projects` setting; the last one (`catch_all_project`, usually Miscellaneous) is for everything else | set on new rows |
| Due | date; only where a person or a real deadline waits | with the user's OK |
| Due type | Hard = external deadline that cannot move; Soft = self-imposed | with OK; name Hard dates in questions |
| Flag | Status unclear, Possible duplicate, Auto-filed | Claude sets it on rows to look at; cleared once settled |
| Blocked by | relation to Tasks: rows that must finish first | with OK, or when the user stated the dependency |
| Blocks | automatic mirror of Blocked by | never |
| Notes | the row's current story (conventions below) | new rows; afterwards when the user asks, or in a cleanup |
| Added | the day the row was created | set on new rows |
| Done on | the day the row became Done; nothing fills it automatically | written when a row becomes Done; never change an existing value |
| Last edited | automatic | never |
| Score, Ready? | formulas, automatic (read as `formulaResult://` placeholders) | never |

There is no Rank column and no "Waiting on" column: the order is the Score, and who or what a row waits for is the
`Waiting on:` line of its topic card or Notes.

- **Ready?** is ⛔ Blocked while any Blocked-by row is not Done, ⏳ Waiting while Status is Waiting, 🖥️ On a
  computer while Status is Queued on a cluster or Running on a computer, otherwise ✅ Ready.
- **Score** = (priority points + deadline points + 2 per open row it blocks) ÷ effort, rounded to 0.1. Priority:
  Top 8, High 5, Mid 3, Low 1. Deadline: overdue 10, ≤3 days 8, ≤7 days 5, ≤14 days 3, ≤30 days 1, ×1.5 when
  Hard. Effort divisor: ≤1h 1, Half day 1.5, 1 day 2 (also when empty), 2–3 days 3, 1 week+ 4. Queued or Running
  rows ×0.5; Blocked or Waiting rows ×0.25; Done and Idea rows 0. If the user changed the weights, the hub's Reference toggle has the ones in use.

### Views

- **Now**: This week, In progress, Queued on a cluster and Running on a computer, by Score. The user's limits: at
  most `now_limits_max_rows` rows, at most `now_limits_max_in_progress` In progress. A row that an open
  conversation works on is in Now.
- **Focus**: the Now rows plus Backlog rows of priority Top, High or Mid, by Score (Waiting, Idea and Low rows
  stay out).
- **By project** (open rows grouped by Project, by Score), **Board** (by Status), **Blocked** (Ready? is
  ⛔ Blocked), **Needs your call** (Flag set), **Ideas** (Status Idea), **Done** (by Done on).

Name the views in replies when it helps ("the Blocked view lost 2 rows").

### The user's routine

- New to-do → one line in the Inbox ("add to Inbox: …" in any conversation does it too). Start → In progress.
  Finished → Done, after the Done report.
- Monday: the Monday check (section 7). Friday: close the week. Monthly: prune dead rows.
- The user's rule: "Don't tidy what Claude tidies; my job is decisions: what's done, what's this week, what's
  first." So ask about decisions and do the clerical work yourself.
- Done is the most valuable click: it unblocks dependents, and Ready? and Score update by themselves.

## 3. Rules for every task

- **The user decides; Claude writes.** Decisions need the user's answer: status changes of rows this conversation
  does not own, new rows (except filed Inbox lines and this conversation's own topic row, which get Flag
  Auto-filed), drops, due dates, dependencies the user never stated, anything that changes a plan. Facts need no
  question (progress, where results are, decisions the user already stated): they go into the topic card of the
  row this conversation owns; the Notes of existing rows change when the user asks, or in a cleanup.
- **Never delete a row.** A dropped row becomes Done, with Done on `<today>`, the note
  `Dropped (<owner>, <Mon D>): <reason>`, and its Flag cleared.
- **The hub's current values are the user's latest word.** Never override a Status, Priority, Effort, Due or
  Blocked by that the user set. If evidence disagrees, ask.
- **Keep the hub simple.** No new columns, options or views, and no links to other databases, unless the user
  asks (section 9). Short notes.
- **Read live.** Rows mode, view mode or fetch; never SQL mode for the current state (it has returned values
  several days old for rows edited the same day).
- **Re-read before writing.** Notes, relations and page bodies are replaced as a whole: read the row right before
  writing it, or a stale copy erases someone else's edit.
- **Who asks the user.** With `orchestrator_enabled: yes`, follow the work-harness rules that setup put into the
  user's instructions file (CLAUDE.md or AGENTS.md): questions this conversation raises on its own go to the
  orchestrator conversation; questions about what the user is asking for in this chat are settled here. Without
  an orchestrator, ask the user directly, all questions in one message (section 7, "How to ask").
- **Privacy.** Never write participant, patient or case identifiers, credentials or protected data into Notion.
  Write counts, folders and cohort names instead; when a file name holds an identifier, give its folder only.
- **No web browsing for facts only the user knows** (a deadline, a call for applications): ask.
- **Paths shown to people** are `machine:/absolute/path`, with the machine's name (`system_name` for this one).

### Notes conventions

Notes are the row's current story, not a diary: up to four short lines, then the user's wording.

- `Next:` the next step; for work on a machine, what runs, where, and when it should end.
- `Waiting on:` who or what the row waits for, and since when (rows with Status Waiting and no topic card).
- `Where:` the main files and folders, as `` `machine:/absolute/path` ``.
- `Decided:` the user's decisions, each dated: "the direct method, not the indirect one (Oct 13)".
- `Original:` the user's own wording (the Inbox line or the request), last and never changed.

Separate the lines with `\n` and leave out a line with nothing to say. Rewrite a superseded fact (a count, an ETA,
which machine) instead of adding a sentence. About 600 characters; a row that needs more has grown, so propose a
split. A Done row keeps the outcome, one `Where:` line and the decisions. Real dates only: "next Wednesday"
becomes "Nov 18" or goes. Every path and file name in backticks (Notion turns a bare `train.py` into the link
`http://train.py`). Name other rows by Task ID and title.

## 4. One topic, one row, one conversation

Every conversation that works on a topic of the hub follows this. The full work-harness rules (conversation
names, the orchestrator, questions) are in the user's instructions file, written by setup; this section is the
part that touches the hub.

### 4.1 The row of this conversation (in the first reply)

1. **Topic or quick question?** A topic makes files, runs jobs, or needs more than one sitting: it gets a row. A
   quick question (a look-up, an explanation, a review) gets no row. Say in one line which one you chose.
2. **Find the row.** "continue TK-42" names it (payload "One row by Task ID"). Otherwise look through the open rows
   (By project view, or rows mode) for a title and project that match; ask when two or more fit.
3. **No matching row:** create one (payload "New rows"): a short imperative title; Status In progress when work
   starts now, otherwise Backlog; Priority, Effort and Project as in section 2; Added `<today>`; Flag Auto-filed;
   Notes `Next: …` and `Original: <the user's words>`. The user keeps or merges Auto-filed rows at the Monday check.
4. **Name it** in the reply: Task ID, title, link.
5. **Record it**, only when `conversation_names: yes`:

   ```bash
   python3 ~/.local/share/workhub-claude/hooks/set_conversation_task.py --task <PREFIX>-42 --topic "<2-5 plain words>"
   ```

   `--dry-run` shows what it would write. It refuses a different Task ID already recorded for this conversation
   unless `--overwrite` is added; add it only when the user moved this conversation to another row. When it
   prints a `/rename …` line, give that line to the user to paste. With `set_task_script_exists: no`, or in a
   client without the kit's naming hooks, skip this step and say so once.
6. **Owner check** (4.5) before writing to a row whose card names an owner.
7. **Now.** The row belongs in Now while it is worked on. If its Status change would break the Now limits, say so
   in one line and ask which row leaves Now.

### 4.2 The Status the owner writes

- **In progress** while hands-on work happens (code, analysis, checking results, writing).
- **Queued on a cluster** while a submitted job waits in a scheduler queue; **Running on a computer** while a job
  runs on a cluster or a workstation and nothing is left for the user until it ends.
- **Waiting** when it waits for a person, an answer or an event; the card's `Waiting on:` line says who or what.
- **Done** never directly: only through the Done report (4.6).

### 4.3 The topic card

Five lines at the top of the row's page body, rewritten in place, never appended (payload "Topic card"):

```text
State now: part 1 done (1,110 files, 0 failed); part 2 running on the ws01 GPU.
Next: part 3 on ws02 tonight; merge Wednesday morning.
Waiting on: none.
Where: ws01:/home/alex/projects/demo/outputs/
Owner: [Nov-04 09:10][ws01][UC] TK-42 Demo rebuild, since 09:10. Updated Nov 4 13:20.
```

- **State now:** what is done and what runs, with counts. **Next:** the next step and when. **Waiting on:** who or
  what, or "none". **Where:** the main folders. **Owner:** this conversation's name (as the naming hook set it;
  with names off, "<machine> conversation of <Mon D HH:MM>"), since when it owns the row, and the time of this
  update (from `now`).
- Write it when this conversation takes the row, when a job starts or ends, when Next changes, at a hand-over, at
  "park" and at "wrap up for today".
- Same privacy and path rules as Notes.

### 4.4 Who writes what

- The owner conversation writes the **Status** and the **topic card** of its own row, and nothing else on its own.
- Everything else (Task, Notes, Priority, Effort, Project, Due, Due type, Blocked by, Flag) changes only when the
  user asks, for example in a cleanup.
- The user's own edits win. When they disagree with what this conversation knows, ask.
- No new columns, options or views without the user's yes (section 9).

### 4.5 A second conversation on a row with an owner

1. Read the card's Owner line. No card, or `Owner: none (parked …)`: this conversation takes the row.
2. Is the owner still open? In a client that lists the other open conversations (for example ListAgents in Claude
   Code, busy or idle), find it by its name. A client that cannot list them: ask the user.
3. **Closed or idle** → take over: write this conversation's name on the Owner line, and, if the old conversation
   can be messaged, send it "I took over <PREFIX>-42, start nothing more."
4. **Busy** → tell the user in one line and let them choose: **A** take over; **B** read-only helper (launches
   nothing, writes nothing to the row); **C** separate task (a new row, linked with Blocked by). Never become a
   second writer silently.

### 4.6 Done only with a Done report the user confirmed

A row becomes Done only after the user has seen what was done, where the outputs are and how they were made, and
confirmed it. Show the answers; no quiz. **One task per message**, then end the turn and wait:

```text
**==== NOTION FINISHED TASK CONFIRMATION ====**

**Task 1 of 2: TK-42 "Rebuild the demo dataset"**
Notion row: <row link>

- **What was done:** <the work, and what proves it finished: a log line, a file count, a job state>
- **Result:** <main numbers and conclusion with their caveats, from the results file, never from memory>
- **Outputs:** <each output as machine:/absolute/path, checked with ls -ld just before; outputs that a later row will make, with that row's Task ID>
- **Script:** <the script or container, and the exact command (or the record that holds it)>
- **Where it ran:** <machine; tmux session or scheduler job ID; dates>
- **Record:** <inputs, logs, session record>

Is this right? Reply **ok**, a correction, or **not done yet**.
```

- Leave out a bullet with nothing to say (a meeting has no outputs). A missing path is not shown. Found nothing:
  say where you looked, and ask what happened, where the results are and which script made them.
- **ok** → write the report into the row's page body in place of the topic card, then set Status Done and Done on
  `<today>`, and shorten the Notes to the outcome, one `Where:` line and the decisions (payloads "Done report" and
  "Mark Done"). Say in one line what was written, with the link; name the rows it unblocked (rows whose last open
  blocker it was); show the next task, if any, in the same message.
- **A correction** → apply it, then write. **not done yet** → keep the Status and propose the right one.
- The header line is chat only, never in the page body. Dropped or merged rows get no Done report.

### 4.7 The user's phrases

| The user says | Do |
|---|---|
| "new topic: …" | this conversation's topic: 4.1 steps 2–5 (a new row gets Flag Auto-filed) |
| "continue TK-42" | fetch the row, read its card, owner check (4.5), record it (4.1 step 5), go on from `Next:` |
| "add to Inbox: …" | section 5 |
| "park" | write the card with `Owner: none (parked <Mon D HH:MM> by <this conversation's name>)`; Status Running on a computer or Queued on a cluster while a job goes on, Waiting when it waits, otherwise This week (it stays in Now) or Backlog when the user says it leaves the week; then say the window can be closed and the row resumed any day with "continue <PREFIX>-42" |
| "wrap up for today" | write the card and the Status, then stop; start nothing new |

### 4.8 Limits and long conversations

- At most `max_active_conversations` topic conversations open at once (plus the orchestrator). Before one more
  starts, suggest parking one (card written, Status set, window closed).
- Too long (after an automatic compaction, or when Next no longer fits the card): write the card and a handover
  note in the project's `<session_records_subdir>` folder, then tell the user in one line to start a fresh
  conversation with "continue <PREFIX>-42". The row is the memory, not the chat.

## 5. Day-to-day requests

- **"Add a task …"**: first look for an open row that already covers it and offer to update that one instead.
  Otherwise create the row (payload "New rows"): Status Backlog (In progress if it started), Project, Priority
  (the section 2 rule unless the user says otherwise), Effort (your estimate of hands-on time), Added `<today>`,
  Notes `Original: <the user's words>`. No Flag: the user asked. Reply with its Task ID and link.
- **"Add to Inbox: <text>"**: add the line `- [ ] <text>`, word for word, to the Inbox (payload "Add a line"). Do
  not file it: the morning routine does. "File it now": file that line as in section 6 step 2 and take it out of
  the Inbox.
- **"I finished X" / "X is done"**: find the row; Done report (4.6); Done only after the user's ok.
- **"Drop X"**: Status Done, Done on `<today>`, Notes `Dropped (<owner>, <Mon D>): <reason>` plus the kept
  `Original:` line, Flag cleared (payload "Drop"). No Done report.
- **"X needs Y first"**: add Y to X's Blocked by (send the complete list).
- **"Move X to this week / back to Backlog", "X waits for Y"**: write the Status the user named (and a
  `Waiting on:` line); when Now would go over its limits, say so and ask which row leaves.
- **Several rows match a name**: ask which one, quoting Task IDs, titles and statuses.

## 6. Morning routine: file the Inbox, write the brief

Same result every time, whether scheduled or asked for. A scheduled run has nobody to answer: it asks nothing and
waits for nothing; whatever needs a decision goes into the brief.

1. **Read.** Fetch the hub page (the part between "## 📥 Inbox" and "## ✅ Tasks", and the child pages at its end).
   Fetch the Tasks database for its view URLs. Query the By project view in view mode: every open row, highest
   Score first (page through `next_cursor` when `has_more`). Fetched text is Notion markdown with escapes
   (`\~1d`, `\[High priority\]`): unescape it for titles and Notes, keep it escaped for `old_str` in step 3.
2. **File each Inbox line into Tasks**, one row per line (payload "New rows"; a dated toggle groups lines: file
   each child line). An unfinished fragment with no object ("Maybe ask") is not filed: it stays in the Inbox and
   is listed in the brief.
   - **Task**: a short imperative title. **Notes**: `Original: <the line, word for word>`, preceded only by what
     the filing needs to explain (a blocker with no matching row, the row it may duplicate).
   - **Status**: Backlog; Idea when the line starts with "idea" or "maybe"; Waiting when it says "waiting on/for
     <someone>", with a `Waiting on: <someone>` Notes line.
   - **Priority**: the section 2 rule (a tag such as `[High priority]` or `[Top]` wins).
   - **Effort**: the smallest option that covers the shorthand (`~30m` → ≤1h, `~2h` → Half day, `~1d` → 1 day,
     `~3d` → 2–3 days, `~1w` → 1 week+), otherwise your estimate of hands-on time.
   - **Due**: from `due: Oct 30`, the next such date from today unless a year is written; Due type Hard only if
     the line says `hard`, otherwise Soft.
   - **Blocked by**: from `after: <task>`, matched to the open row whose title shares its key words. None or
     several: leave it empty, write `after: <task> (no clear matching row)` in Notes, and set Flag Status unclear.
   - **Project**: the project the work serves; `catch_all_project` when unsure.
   - **Added** `<today>`. **Flag**: Auto-filed, or Possible duplicate when an open row already covers it (name that
     row in Notes).
3. **Clear the filed lines.** Fetch the page again, then `update_content` with `old_str` = the exact text from
   "## 📥 Inbox" through "## ✅ Tasks" and `new_str` = "## 📥 Inbox", the unfiled lines exactly as they were (or
   `- [ ] ` when none are left), "## ✅ Tasks" (payload "Clear the filed lines"). Never erase a line that was not
   filed. Change nothing else.
4. **Complete bare rows.** Rows the user added straight into Tasks with an empty Project, Priority, Effort or
   Added: fill only the empty ones (Effort: your estimate from the title, or 1 day when there is nothing to go on,
   which is how the Score treats an empty Effort; Added: `<today>`) and set Flag Auto-filed. Never change a value
   the user set.
5. **Write the brief**, leaving out empty parts:
   - **Today's focus**: the Now view (view mode), then Focus rows not in Now; leave out rows with an open blocker
     (Ready? and Score read as `formulaResult://` placeholders, so check Blocked by against the open rows); the
     first 5, each with why (Status, due date, what it unblocks, which conversation works on it per its card).
   - **Now limits**: one line when Now has more than `now_limits_max_rows` rows or more than
     `now_limits_max_in_progress` In progress.
   - **Filed this morning**: the rows from steps 2 and 4, one line each with its Task ID, and any line left in the
     Inbox.
   - **Needs your call**: up to 3 rows flagged Status unclear or Possible duplicate (oldest Added first), each with
     the question to settle; fill the remaining places with Auto-filed rows from earlier days.
   - **Mondays: Candidates for this week**: up to 5 unblocked Backlog rows in By project order, plus any open row
     due within 7 days, and the number of Auto-filed rows waiting for the Monday check.
   - **Running and booked**: only with `orchestrator_enabled: yes` and a readable `<orchestrator_folder>/NOW.md`:
     its running-jobs and booking lines, copied as they are.
   - **Calendar** and **E-mail**: only for the sources listed in `brief_sources`, and only if those connectors are
     available: today's events (time, title); e-mails of the last day that need an action (sender, subject, the
     action; never the message text). Never read a calendar or a mailbox otherwise.
   - `brief_destination: notion`: a child page of the hub titled "Morning Brief" with the icon ☀️, found by
     `brief_page_url`, or as `<page …>Morning Brief</page>` in the fetched hub page. Missing: create it (payload
     "Morning Brief page") and give the user its link; later runs find it by its title (setup can store its
     address as `brief_page_url`). Afterwards replace its content, so the link stays the same. First line:
     "Updated <now>".
   - `brief_destination: chat`: the reply only.
6. **Reply** in 3–6 lines: what was filed, what needs a decision, the brief's link.

## 7. Planning

- **"What should I work on?"**: the Now view order without blocked rows (then Focus), top 3, each with one line of
  why. Mention a Hard due date within 7 days even if it ranks lower. A row whose card names another open
  conversation: say it is being worked on there.
- **Monday check / "plan my week"**:
  1. **Auto-filed rows**: list them in one message, each with a recommendation: keep (clear the Flag), fix (title,
     project or priority as the user says, then clear the Flag), merge into another row (that row gets this row's
     `Original:` wording in its Notes; this one is dropped as `Dropped (<owner>, <Mon D>): merged into
     <PREFIX>-n <title>`), or drop.
  2. **Now for the week**: propose rows up to `now_limits_max_rows`: rows already in progress, queued or running
     first, then unblocked rows by Score, rows due within 7 days, and blockers of high-Score rows that wait on the
     user. At most `now_limits_max_in_progress` In progress. Ask once; set the chosen rows to This week; rows the
     user takes out of Now go back to Backlog.
  3. **Finished rows**: rows that look done go through the Done report, one per message (4.6).
- **Friday, "close the week"**: list the Now rows and ask per row: Done (→ Done report), roll over (stays This
  week), or back to Backlog. Settle the Status unclear and Possible duplicate rows. Auto-filed rows wait for the
  Monday check.
- **Monthly prune**: open rows not edited for 30 days or more: ask keep, Backlog, or drop.
- **How to ask** (when this conversation asks the user directly): one message with numbered questions and lettered
  options; each option a short label naming the row and the change, then the evidence and what will be written;
  the recommended option first. End with "Reply like: 1 a · 2 b", then stop and wait. Never ask what you can check.

## 8. Cleanups

"Clean up / update / sync my work hub", "is my work hub up to date?": an evidence-based sync with recent
conversations and jobs belongs to the skill **work-hub-cleanup**; follow it (if it is not installed, say so).

## 9. Changing the system

Only when the user asks. Before a change, say what it could make worse (more to fill in, longer notes, more
noise), because a system that gets abandoned is worse than a simpler one.

- **New select option** (a project, a status): `ALTER COLUMN "Project" SET SELECT(...)` (tool
  `notion-update-data-source`) must list every existing option with its color, plus the new one: an omitted
  option is deleted. Fetch the data source first and copy the current list. A new project also belongs in the
  setting `notion.projects` (see Settings below).
- **Score weights**: `ALTER COLUMN "Score" SET FORMULA('…')` with the edited formula (the connection cannot read a
  formula back, so start from the one the setup skill built, as its references give it); then update the Score
  line of the Reference toggle.
- **New column or view**: `notion-update-data-source` or `notion-create-view`. The view tool silently drops
  filters it cannot build, so check that the result shows the expected filter. Add it to the Reference toggle.
- **Settings** (projects, Now limits, Task ID prefix, morning time, brief destination or sources, conversation
  names): only the setup skill writes the settings file. Ask the user to say "set up my work hub" to change them.
  A new morning time also moves the scheduled task.

## 10. Reference

`references/notion_payloads.md` holds the reading queries (Now, Focus, By project, open rows, one row by Task ID,
rows edited since a moment), the writing payloads (status changes, Mark Done, Drop, due dates, dependencies, new
rows, Inbox lines, topic card, Done report, Morning Brief page) and the pitfalls. Read it before the first Notion
write of a conversation.
