---
name: work-hub-cleanup
description: Bring the user's Notion Work Hub (its Tasks database) up to date with what really happened since the last cleanup. It reads the period's Claude Code conversations (and Codex threads when enabled) here and on machines with an open SSH connection, with the row each one recorded; checks what still runs (Slurm jobs, tmux sessions, GPU use); compares that with the open tasks in scope (Now, Focus or all open); asks evidence-backed questions one at a time about status changes, new tasks, due dates, dependencies and flags; shows a Done report (outputs, script) for every row that becomes Done; writes only what the user approved; and records the cleanup so the next one starts where it stopped. Use it whenever the user says "clean up my work hub", "tidy, sync or refresh my work hub", "is my work hub up to date?", "update my tasks from today's sessions", or asks what in the task list should change after recent work, even without saying Notion. Not for adding one dictated task, the morning brief, or a status figure.
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Work Hub cleanup

One pass that makes the Work Hub match reality. You gather the evidence and do the clerical work; the user
only decides. Afterwards every row in the scope has the right status, due date, dependencies and a short,
current note, and every row that became Done has a Done report the user confirmed.

`<skill>` below = this skill's folder (the folder of this SKILL.md). `references/work_hub.md`: where the
Notion addresses come from, the schema, reading and writing recipes. `references/questions.md`: how to ask,
and the Done check. The harness rules (rows, owners, topic cards, the orchestrator) are the block that
work-hub-setup adds to `~/.claude/CLAUDE.md` ("Work Hub harness").

## 0. Settings first

1. Run `python3 <skill>/scripts/workhub_config.py check`. If it fails, tell the user to say "set up my
   work hub" (skill work-hub-setup) and stop.
2. Run `python3 <skill>/scripts/cleanup_log.py settings` and keep its answer for the whole cleanup. It
   prints, already resolved: the Tasks data source, the Now and Focus views, the hub page, the Task ID prefix
   (written `TK` below; use the printed one), the projects and the user's project rules, the Research log,
   the orchestrator folder, the Now limits, the time zone, this machine's name and the cleanup log. Exit
   status 2 = the Tasks data source is empty: stop as in 1.
3. A line that says `(empty: …)` or `(off: …)` names a feature this cleanup skips; say so in one line of
   the report ("Research log: off, not read").

## Ground rules

- **Evidence before questions.** Never ask what a transcript, a Slurm query or a file can answer; every
  question carries its numbers, dates, machines and paths.
- **Facts are written, decisions are asked.** Facts: progress, where results are, findings, decisions the
  user already stated. Decisions: status changes, new rows, drops, due dates, new dependencies, plan changes.
- **Done needs a confirmed Done report** (step 5), also for rows the user closed by hand. Dropped and merged
  rows get none.
- **Owners write their rows.** While the conversation on a row's card Owner line is open (ListAgents, when
  the client has it), it writes that row's Status and card. The cleanup reads the card and never rewrites
  it; a Status that looks wrong there becomes a one-line note to the owner, not a question.
- **The user's fields:** which rows are in Now (at most the printed Now limits: rows, and rows In
  progress), Priority and Effort of existing rows, Hard due dates. Changed only when the user asks. A value
  the user changed is never reverted; if the evidence disagrees, ask.
- **Auto-filed rows are never asked about**; the user settles them at their weekly review.
- **Keep the hub simple:** short notes; no new columns, options or views; never delete a row; never link
  the Work Hub and the Research log.
- **Read Notion live** (view mode, rows mode or fetch; SQL mode can return values days old). **No web
  browsing.**
- **Privacy:** no identifiers of study participants, patients or clients in Notion (counts, folders, group
  names instead). The digests print what the transcripts hold; they stay in `<scratch>`.

## Scopes

| Scope | Rows that get questions, in this order | When |
|---|---|---|
| **Now** | pass 1 only: the Now view (This week, In progress, Queued on a cluster, Running on a computer) | a quick cleanup |
| **Focus** (default) | pass 1 = the Now view; pass 2 = the rest of the Focus view (Backlog rows of priority Top, High or Mid) | the weekly review, and unless the user says otherwise |
| **All open** | passes 1 and 2, then pass 3 = every other row not Done (Waiting, Idea, Low or no-priority Backlog) | about monthly, to drop dead rows |

- **Always the Now tab first, then the Focus tab, each from top to bottom.** Query each view in view mode
  (`references/work_hub.md` → "Reading"): Notion returns the rows in the view's own sort (Score, highest
  first), which is the order the user sees in the tab. Pass 2 skips rows already handled in pass 1. Pass 3
  has no view: Priority (Top, High, Mid, Low, none), then Due (earliest first).
- Settle each pass before the next, so a cleanup stopped halfway has settled the rows that matter this week.
- **Show each pass as one list in the tab's exact order**, one line per row, every row included. Each line
  says what happens to the row: nothing to change (and why), a fact written, a heads-up, or a question. A
  row's question sits at its own place in the list; never group rows into "written / left alone /
  heads-ups" sections that break the order.
- **Walk the rows one by one, also those without a question.** Each row gets its own short message at its
  place: what it is, its state now (checked live, e.g. with sacct), what the cleanup did or sent, "nothing
  for you to decide". Then the next row. Never skip a row because it is Auto-filed or owned, and never jump
  to the next pass before every row of this one was shown.
- When a view address is empty in the settings, or a view query fails: use the rows query of that view's
  filter (`references/work_hub.md` → "Reading"), sorted by Priority, then Due, and say so in the report
  (Score itself cannot be read through the connection).
- The scope only decides which rows get questions. Every conversation of the period is still read; a
  finding outside the scope becomes a one-line note in the report, never a question.
- A scope the user names wins.

## Scripts

All read-only Python ≥ 3.9 (standard library), writing only into their output folder or the cleanup log:

- `scripts/cleanup_log.py settings | last | add | next-code`: the resolved settings; where the previous
  cleanup stopped; records this one; the next free question code (CL<n>).
- `scripts/session_digest.py`: conversations active since `--since` on this machine, plus the machines of
  the settings that do not share this home folder (clusters only through an open shared connection;
  workstations through one, or with SSH keys and no prompt). Starts with an index by Work Hub row (the
  Task ID each conversation recorded, or one in its name) and the conversations with no row; then each
  conversation's turns (request, answers, Notion writes, reply).
- `scripts/live_state.py`: Slurm jobs, tmux sessions, GPU use and busy processes on the machines of the
  settings; `--connections-only` lists which cluster connections are open.
- Tests: `python3 -m pytest <skill>/tests -q` (or `python3 <skill>/tests/test_scripts.py`).

## Steps

### 1. First message: the scope and the connections

- `<scratch>` = your scratchpad (else `mktemp -d /tmp/work-hub-cleanup.XXXX`). Read `references/work_hub.md`.
- Run `python3 <skill>/scripts/cleanup_log.py last`, query the Now and Focus views (view mode) and the open
  rows (rows mode, for the "all open" count).
- `python3 <skill>/scripts/live_state.py --connections-only`: one line per cluster of the settings, open or
  not, with the fix. Never log in yourself: a cluster login usually needs the user's password or two-factor
  approval. No cluster in the settings: say nothing about connections.
- Then one short block (the example uses neutral names):

  > Work Hub cleanup from Mon Mar 2 14:41 (where the last one stopped).
  > Scope: **Focus**: first the Now tab (5 rows), then the rest of the Focus tab (12 rows), each top to
  > bottom. Say "Now" for the Now tab only or "all open" for all 31.
  > mycluster is not connected: run `cluster-login mycluster` on ws01 and say "go", or say "go" to skip it.

  Wait only when a cluster is missing; the scope can still be changed before the questions.

### 2. The period

`cleanup_log.py last` prints `--since <moment>` (conversations are read from here), "Hub edits by others
since" (rows edited after it were changed by the user, the morning tidy or another conversation) and the
Notion query for those edits in UTC. A period the user names wins. An empty log proposes the last 72 hours.

### 3. Evidence (run these together)

1. **Conversations:** `python3 <skill>/scripts/session_digest.py --since <since> -o <scratch>/sessions`;
   keep the `--window-end` it prints. Read `digest.txt`, index first.
   - **With a row (Task ID):** its topic card is the summary. Read the conversation in detail only when
     the card is missing or its "Updated" time is older than the conversation's last activity.
   - **With no row:** read it in detail and map it to rows yourself:
     `--session <id8>,<id8> --hosts none --prompt-chars 4000 --reply-chars 2500 --max-turns 40 -o <scratch>/detail`
     (`--hosts <machine>` for a conversation on another machine; `--full` when exact words matter). Skip
     short question-and-answer conversations. When the orchestrator is on, `[ORCH]` conversations have no
     row by design; use its `DECISIONS.md`.
   - "NOTION WRITES" lines show rows already changed (`update-page 0a1b2c3d` = start of the row id).
2. **Running work:** `python3 <skill>/scripts/live_state.py --since <since> -o <scratch>/live`.
3. **The hub:** the Now and Focus views in view order, the open rows, and rows edited since "Hub edits by
   others since" (`references/work_hub.md` → "Reading"); fetch the scope rows that had a conversation or
   are in Now, for their card and Notes.
4. **The orchestrator's files**, only when the settings print an orchestrator folder: `DECISIONS.md`,
   `QUESTIONS.md` (answered or open questions are not raised again), `NOW.md`. Off: skip this step.
5. **Machines NOT REACHED:** the Research log pages of the period (read-only), only when the settings print
   a Research log data source (`references/work_hub.md` → "Research log"). Off or empty: say in the report
   that those machines were not covered.
6. **Targeted checks** with bounded read-only commands (output counts, last log lines, exit codes). Never
   poll, start or stop anything.
7. **Done report facts** for each Done candidate and each row closed by hand without a report
   (`references/work_hub.md` → "Done report"), every path checked with `ls -ld`. Many rows: read-only helper
   agents, one per ~10 rows.

### 4. The change list

Write `<scratch>/plan.md`: one line per change, in pass order (Now tab, then Focus tab, top to bottom),
`TK-n title: field before → after`, with the evidence and "fact" or "decision".

Per conversation:

1. Progress → Notes (fact).
2. Status, on rows without an open owner: finished → Done candidate (Done check); work began → In progress
   (hands-on only; a job → Running on a computer, or Queued on a cluster while it waits in the queue); a
   Waiting row whose answer came → no longer waiting.
3. A revealed dependency → Blocked by.
4. Work with no row, or an offered follow-up never done → new row (unless an open row covers it; finished
   one-off work gets none). Its Project follows the printed project rules (`references/work_hub.md` →
   "Tasks properties").
5. A finding that changes a plan → what should the hub say?
6. A decision the user stated → `Decided:` line, dated (fact).
7. An unanswered question: active in the last 2 hours → Heads-ups; in the orchestrator's `QUESTIONS.md`
   (when on) → leave; else raise it.

Per scope row:

8. Due: overdue, due within 3 days with an open blocker, or due before its Effort can fit. Name Hard
   deadlines; never propose a self-imposed date.
9. Stale: In progress or This week, untouched 7 days, no conversation → still active?
10. Flags: settle "Status unclear" and "Possible duplicate"; leave "Auto-filed".
11. Newly unblocked (last blocker Done in the period), and Now over its limits: report only (check
    `DECISIONS.md` when the orchestrator is on).

Rows the user closed by hand (any scope): Notes still describing running work → outcome and `Where:`
(fact); no Done report → draft one; empty Done on → the day it closed, from evidence (fact).

The Inbox belongs to the morning tidy of the work-hub skill (and to the orchestrator, when on); mention
Inbox lines older than a day.

### 5. Ask

Read `references/questions.md`.

- **One question per message, in the tab's order, always as a full block.** Go down the pass list row by
  row: rows with nothing to ask get their one line; at the first row with a question, show that question
  as a full block (every field of `references/questions.md` → "The question block"), end the turn and
  wait. After the answer, write it, then continue down the list from the next row. Never a table or
  one-line summary of questions, never several questions in one message, never a question out of the tab's
  order, also when re-asking. CL codes follow the same order (the first question asked gets the lowest
  number).
- **Every question gets its own CL code:** `CL1`, `CL2`, … numbered across all cleanups, never reused,
  never restarted. `python3 <skill>/scripts/cleanup_log.py next-code` prints the next free one (it searches
  the cleanup log and, when the orchestrator is on, its `DECISIONS.md` and `QUESTIONS.md`). A question
  keeps its CL code when re-asked or sent to the orchestrator (it is not given a Q number as well); answers
  come as "CL12 A". List the codes used in the step 8 `--summary`.
- **Decisions** are asked in this chat, one per message as above. When the orchestrator is on, send each
  answer to the orchestrator `[ORCH]` (SendMessage; find it with ListAgents) word for word with its time,
  for `DECISIONS.md`; decisions that stop or change running work or machine use go through the orchestrator
  first. When it is off, the answers stay in this chat and in the rows' `Decided:` lines.
- **Done check, in this chat, one task per message**, each opening with
  `**==== NOTION FINISHED TASK CONFIRMATION ====**` (`references/questions.md` → "Done check"). After the
  user's ok, write that row and show the next.
- **Dry run** ("just show me"): show `plan.md` and stop: no writes, no questions sent, no log entry.

### 6. Write (`references/work_hub.md` → "Writing")

- Facts now; each decision when its answer arrives (here, or relayed by the orchestrator verbatim with its
  time).
- Re-read a row right before writing: `update_properties` replaces whole values.
- Done, after the ok: Done report into the page body (replacing the card), then Status Done and Done on (no
  automation fills it).
- Dropped: Status Done, Notes "Dropped (<owner name>, <day>): reason", Flag cleared. Blocked by: the
  complete list. Never write Blocks, Score, Ready? or Last edited.

### 7. Verify

Query today's edits (rows mode, plain-date filter) and check each changed row (Status, Due, Blocked by,
Notes, Flag); each new row exists once; each Done report sits once at the top of its page. Fix file names
Notion turned into links (`http://train.py`). Retry a failed write once, then report it.

### 8. Record

After writing, or after the user said there is nothing to change; never in a dry run:

    python3 <skill>/scripts/cleanup_log.py add --window-start <since> --window-end <window end from step 3.1> \
        --client claude-code|codex --summary "<N> conversations; scope Focus; <n> Done, <n> new rows, <n> due dates moved; questions CL<a>–CL<b>"

`add --dry-run` shows the line without writing it.

### 9. Report

First line: the hub link (when the settings have one), the scope, and "the hub now matches the N
conversations since <day time>". Name every row `[TK-n](row link) title` and every change before → after.
Sections (skip empty ones): **Changed**, **Done** (with the main output folder), **New rows**, **Questions
sent** (codes, answers awaited), **Outside the scope**, **Heads-ups** (failed jobs, full disks, open
questions, Auto-filed rows, Inbox lines), **Not covered** (with the fix, usually `cluster-login <cluster>`;
also the features the settings switch off). Paths as `<machine>:/absolute/path`; in Claude Code chat as
links `[ws01:/home/alex/projects/demo](/home/alex/projects/demo)`.

## Without Notion tools

Do steps 2–4 without the hub, write `<scratch>/proposed_changes.md`, tell the user where it is and that a
session with Notion should run the cleanup; do not record the cleanup.

## On a cluster

A cleanup run on a cluster sees only that cluster. A full cleanup runs on a workstation of the settings,
with the shared connections to the clusters open (`cluster-login`, when it is installed).
