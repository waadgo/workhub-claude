# Work Hub harness (workhub-claude)

These rules connect Claude Code conversations to {{owner_name}}'s Notion Work Hub ({{hub_page_url}}).
Settings: `{{config_path}}` (read with `python3 {{hooks_dir}}/workhub_config.py get <key>`). Skills that run the
hub: work-hub (daily use), work-hub-cleanup, weekly-recap, run-status-figure, compute-planner, research-log,
active-conversations, find-conversation; work-hub-setup changes this block.

Terms: **row** = one task in the Work Hub's Tasks database. **Task ID** = the row's automatic ID, such as
`{{task_id_prefix}}-42` (Notion numbers rows itself; never typed, never reused). **Topic conversation** = a
conversation that works on one row. **Topic card** = five lines at the top of a row's page body. **Owner** = the
one conversation that works on a row now; its name is on the card's Owner line.

## File paths

Whenever you point {{owner_name}} to a file or folder (in replies, reports and Notion pages), give the machine name
and the full absolute path: `<machine>:/absolute/path`. The machine name is
`python3 {{hooks_dir}}/workhub_config.py system-name`. Expand `~`, `.` and `..`. Paths inside code, config files
and shell commands stay plain.

<!-- if workflow.conversation_names -->
## Conversation names

- Form: `[Nov-04 09:10][ws01][UC] {{task_id_prefix}}-42 Clean survey data` (start time in {{time_zone}}, machine,
  role: `UC` for a topic conversation, `ORCH` for the orchestrator, then the topic).
- A hook names each conversation at {{owner_name}}'s second message. A name {{owner_name}} sets is final.
- **Record the row in the first reply:**
  `python3 {{hooks_dir}}/set_conversation_task.py --task {{task_id_prefix}}-42 --topic "Clean survey data"`
  (2-5 plain words; `--dry-run` shows what it writes).
<!-- endif workflow.conversation_names -->

## Rows and topic cards

- **One topic = one row = one conversation.** In its first reply a topic conversation names its row (Task ID,
  title, link). "continue {{task_id_prefix}}-42" means that row.
- **No matching row** (search by title and project): a *topic* (makes files, runs jobs, or needs more than one
  sitting) gets a new row: Status Backlog or In progress, Flag "Auto-filed", a one-line Note. A *quick question*
  (look-up, explanation, review) gets no row. Say in one line which one you chose.
- **Topic card**, at the top of the row's page body, rewritten in place, never appended:
  ```
  State now: raw file checked (1,200 answers, 14 duplicates).
  Next: remove the duplicates, then write the cleaning report.
  Waiting on: nothing.
  Where: ws01:/home/alex/projects/survey/
  Owner: [Nov-04 09:10][ws01][UC] {{task_id_prefix}}-42 Clean survey data, since 09:10. Updated Nov 4 13:20.
  ```
- **Who writes what:** the owner conversation writes the **Status** and the **topic card** of its own row (when a
  job starts or ends, when Next changes, at hand-over, at "wrap up for today"). Every other property changes only
  when {{owner_name}} asks. {{owner_name}}'s own edits win; when they disagree with what you know, ask.
- **A second conversation on a row with an owner:** if the owner conversation is closed or idle, take over (write
  your name on the Owner line). If it is busy, ask {{owner_name}}: take over, read-only helper (launches and writes
  nothing), or a separate row linked with Blocked by. Never become a second writer silently.
- **Now** (this week's rows): at most {{now_max_rows}} rows, at most {{now_max_in_progress}} In progress. At most
  {{max_active_conversations}} topic conversations open at once; park one first (card written, Status set).
- **Too long:** after an automatic compaction, write the card and a handover note in the project's
  `{{session_records_subdir}}/`, then suggest a fresh conversation with "continue {{task_id_prefix}}-42". The row is
  the memory, not the chat.
- Phrases: "continue {{task_id_prefix}}-42", "new topic: …", "add to Inbox: …", "status", "park",
  "wrap up for today" (write the card and Status, then stop).

## Done only with a confirmed Done report

Before a row becomes Done, show {{owner_name}} a **Done report**, one row per message: the outcome; the result
(main numbers and conclusion, with caveats); every output as `<machine>:/absolute/path` (each checked with `ls`);
the script and the exact command; where it ran (machine, tmux session or job ID, dates); the inputs; the logs or
session record. End with "Is this right? Reply ok, a correction, or not done yet." and wait. Only after the ok:
write the report into the row's page body (it replaces the topic card), set Status Done and the "Done on" date.
Dropped or merged rows get no report.

<!-- if workflow.orchestrator.enabled -->
## The orchestrator [ORCH]

- One coordinating conversation (role tag `ORCH`) keeps the overview: running jobs, machine bookings, open
  questions, decisions. It does no project work itself. Its files are in `{{orchestrator_folder}}/`:
  `NOW.md` (one screen, rewritten in place), `QUESTIONS.md` (open questions with permanent IDs Q101, Q102 …),
  `DECISIONS.md` (each answered question with {{owner_name}}'s words verbatim and the time), `log/YYYY-MM.md`
  (append-only event log). A new orchestrator starts from `START_ORCHESTRATOR_PROMPT.md` there.
- **Questions to {{owner_name}} go through the orchestrator.** A topic conversation sends its open questions to the
  orchestrator (SendMessage; find it with ListAgents) instead of asking in its own reply. Exception: when
  {{owner_name}} discusses a conversation's own topic in that conversation, it may take the answers there and send
  them to the orchestrator afterwards. If no orchestrator is reachable, ask {{owner_name}} directly.
- The orchestrator asks at most 5 questions per round, blocking ones first, each as a full block (question;
  project and row; background; what happened, with evidence; where; when, and by when an answer is needed;
  why {{owner_name}} is asked; options A/B/C with cost, risk and whether they can be undone; recommendation; the
  expected answer form such as "Q104 B").
- **Who launches:** a topic conversation starts a long job, a GPU job, a cluster job or anything that deletes or
  stops something only after the orchestrator's OK, which relays {{owner_name}}'s own words with their time. It
  reports the start (session or job ID, log, outputs) and the end (exit code, runtime) to the orchestrator.
<!-- endif workflow.orchestrator.enabled -->
