# Asking the questions

The user answers quickly when each question stands on its own: it names the row (Task ID, title, link),
gives the evidence with numbers and dates, and says what will be written. The user should never have to
open a transcript or Notion to answer.

## Where questions go

The user runs the cleanup and answers in its chat, so the cleanup asks here. When the orchestrator is on,
this is the harness exception for a conversation whose own topic the user discusses: the answers are taken
here and then sent to the orchestrator. Rules:

- **Tab order:** go down the pass list (Now tab, then Focus tab, top to bottom, exactly as the view
  returns it); a row's question comes at that row's place, never earlier or later.
- **One question per message**, as a **full question block** (below). Never a table, a one-line summary or
  a list of several questions, also when re-asking. End the turn after the block and wait.
- **CL code** on every question (SKILL.md step 5: `cleanup_log.py next-code`), numbered in the order asked.
- **Orchestrator on** (the settings print an orchestrator folder): before writing a block, check its
  `DECISIONS.md` and `QUESTIONS.md`: what was answered is not asked again, what is open is not asked twice.
  Each answer goes to the orchestrator `[ORCH]` (SendMessage, found with ListAgents) word for word with
  its time, for `DECISIONS.md`. Decisions that stop or change running work or machine use go through the
  orchestrator first.
- **Orchestrator off:** the answers stay in this chat; the decisions go into the rows' `Decided:` lines.
- The Done check also runs in this chat (below), one task per message.

## The question block

Every field is filled ("none" is an answer, an empty field is not). Plain words: define a technical term
the first time a block uses it; never use a code the user does not know without the row's Task ID and
title. Every machine by its name, every file and folder as `<machine>:/absolute/path`, every job by its
Slurm job ID or tmux session name.

**CL<n>: <the question, in one plain sentence>**
- **Project and row:** the project; Task ID, row title and Notion link (or "no row").
- **Background:** what this work is for, in 1-3 plain sentences, for a reader who has not seen it before;
  the user's own earlier words with their date when the work came from them.
- **What happened:** what was found or done, with numbers and evidence (file, log as `<machine>:/path`;
  Slurm job ID or tmux session).
- **Where:** the machines and folders involved, and where the proposed work would run.
- **When:** since when it is open; by when an answer is needed and why; what happens if it waits.
- **Why you are asked:** why Claude cannot decide it alone (it is the user's field, a rule needs their
  yes, cost, risk, something only they know); what was already checked.
- **Options:** A / B / C, each with what happens in practice: what is written or changes, and whether it
  can be undone.
- **Constraints:** deadlines, other rows, Now limits; "none" when there are none.
- **Recommendation:** the option, and why, in one or two sentences.
- **Answer expected:** the exact form, for example "CL12 A".

An example (neutral data):

> **CL12: Which Priority should TK-24 "Fit the first survey model" get?**
> - **Project and row:** Project A; TK-24 "Fit the first survey model", https://www.notion.so/0123456789abcdef0123456789abcdef
> - **Background:** the first model of the cleaned survey; you re-opened the row on Mar 2.
> - **What happened:** it has no Priority, so the Focus view (Top, High and Mid Backlog rows) hides it.
> - **Where:** Notion only.
> - **When:** open since Mar 2; needed before the next weekly review, so the row shows in Focus.
> - **Why you are asked:** Priority is your field.
> - **Options:** A Mid (no deadline, blocks nothing); B High; C Low (stays out of Focus). Each is one click
>   to undo.
> - **Constraints:** none.
> - **Recommendation:** A, nothing depends on it yet.
> - **Answer expected:** "CL12 A", "CL12 B" or "CL12 C".

Every cleanup question has its own permanent CL code (CL1, CL2 …, numbered across cleanups, never reused).
It keeps that code at the orchestrator and in `DECISIONS.md`; it gets no Q number as well.

## What a cleanup asks about

Recommended option first; "keep as is" when that is a real choice.

- **Status** of a row without an open owner (Done goes to the Done check): new status with evidence, or keep.
- **New row:** what, why, first step, Project (by the project rules), Priority, or no row; name an open row
  it might duplicate.
- **A plan-changing finding:** new row, note on the affected rows, or nothing for now.
- **A due date that no longer fits:** new date, clear, keep (first when Hard). One date for a group.
- **What is left on a row:** keep open (waiting on what), block it on another row, or Done.
- **A flagged or stale row:** what it is, back to Backlog, Waiting, merge, or drop.

## Done check

A row is Done only after the user has seen where its outputs are and which script made them, and
confirmed it. Show the answers, no quiz.

- **One task per message.** Show one task, end the turn, wait. Never several tasks in one message, never
  draft files to read, never a question list across tasks. A question about a task comes with that task.
- **Prepare ahead with agents.** While the user reviews one task, background read-only helper agents
  gather the next tasks' facts (outputs, result numbers quoted from their files, later decisions), one
  agent per group of about ten tasks. Their notes are never shown as a batch; every path is re-checked
  before it is shown.
- **First message only:** two or three plain sentences: these tasks look finished; for each one I show
  what I found, you confirm, I write it into the row; nothing is written before your ok.
- **After the answer:** "ok" → write that row now (Done report in the page body, then Status Done and
  Done on; `references/work_hub.md` → "Done report"). A correction → apply it, then write. "not done
  yet" → the row keeps its status; propose the right one. Then say in one line what was written, with the
  row link, and show the next task in the same message.
- **Order:** most recently finished first.
- **Header line, first line of every Done check message:** exactly
  `**==== NOTION FINISHED TASK CONFIRMATION ====**`, always the same text, so the user can spot and
  search these messages among their conversations. The "Task N of M" title follows on the next line. When
  the message first confirms the previous row ("Written: TK-…"), that line comes first and the header
  opens the next task. The header is chat only; it never goes into the row's page body.
- **Notion row link, every time**, right under the title.
- In Claude Code chat every path is a link (`[ws01:/home/alex/x](/home/alex/x)`); in Codex, plain text.

Template, when outputs were found:

> **==== NOTION FINISHED TASK CONFIRMATION ====**
>
> **Task 1 of 3: TK-42 "Clean the survey dataset"**
> Notion row: <https://www.notion.so/<row id>>
>
> - **What was done:** duplicates removed and answers without age marked; finished Mar 2 (the log ends
>   without errors, the clean table exists).
> - **Result:** 1,186 of 1,200 answers kept (14 exact duplicates removed); 31 answers have no age and are
>   kept, marked. One survey wave only. Table:
>   [ws01:/home/alex/projects/survey/reports/cleaning_summary.txt](/home/alex/projects/survey/reports/cleaning_summary.txt)
> - **Outputs:** [ws01:/home/alex/projects/survey/clean/](/home/alex/projects/survey/clean/)
>   (`survey_clean.csv`). The first model will come from the row TK-43 'Fit the first survey model' (not yet).
> - **Script:** [ws01:/home/alex/projects/survey/scripts/clean_survey.py](/home/alex/projects/survey/scripts/clean_survey.py),
>   command `python3 scripts/clean_survey.py --input raw/survey.csv --output clean/`.
> - **Where it ran:** ws01, tmux `survey_clean`, Mar 2.
> - **Inputs:** `ws01:/home/alex/projects/survey/raw/survey.csv`
> - **Record:** `ws01:/home/alex/projects/survey/docs/sessions/2026-03-02_1015_survey_cleaning.md`
>
> Is this right? Reply **ok**, a correction, or **not done yet**.

Template, when nothing was found:

> **==== NOTION FINISHED TASK CONFIRMATION ====**
>
> **Task 2 of 3: TK-31 "Draft the methods paragraph"**
> Notion row: <https://www.notion.so/<row id>>
>
> I found no outputs for this task. I searched <where, one line>; the only lead is <lead, or "none">.
>
> What happened? Where are the results, and which script made them? (Or: no files, e.g. it was a
> message or a meeting.)

- Say what proves the task finished (log line, file count, job state) in "What was done".
- **Result:** the main numbers and the conclusion, with the caveats that change how far it can be
  trusted (one subject, no confidence interval …) and the table or record that holds them, taken from the
  results file, never from memory. A task without numbers says what came out of it. Result not found →
  say so in this bullet and ask.
- Leave out a bullet with nothing to say. Plain words, one or two lines per bullet.

## Codex, or a client without SendMessage

Write the same blocks as one plain-text message, then stop and wait. The Done check works the same. With
the orchestrator on, tell the user in one line that the answers still have to reach `DECISIONS.md`.

## What not to ask

- Facts you can check ("is the job still running?"): check them.
- Questions open in a conversation active in the last 2 hours, or in `DECISIONS.md` / `QUESTIONS.md`
  (orchestrator on).
- The Status of an owned row (send its owner a one-line note), Auto-filed rows (the user's weekly review),
  the Inbox (morning tidy, and the orchestrator when on).
- Priority and Effort of existing rows, unless the evidence shows a clear mistake or a missing value
  hides the row.
- Anything with no consequence in the hub. Outside facts: ask for them, never browse.
