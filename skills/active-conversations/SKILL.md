---
name: active-conversations
description: Find Claude Code conversations across this machine, the workstations that share its home folder, and every other workstation or cluster in the work-hub settings that has an open shared SSH connection, either the ones open right now, the last N by time, or the ones about a topic, and (when Notion reports are set up) add each request as a new report to the user's Notion "Conversation reports", with one row per conversation giving its site, machine, working directory to cd into, name, start, last message, state and one line on what is going on. Use this skill whenever the user asks which conversations or Claude sessions are active, open or running, where a conversation is or which folder it is in, for their last or most recent N conversations, to find or search conversations about something across machines or clusters, for an overview or snapshot of their conversations, or which conversations are warm (still have work to do), redundant, finished, or safe to close, even if they don't mention Notion.
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Active conversations

Each request gives the user a list of their conversations, per site, with what each one is doing. When Notion
reports are set up, it also adds one report to their Notion "Conversation reports":
- a page in its **Reports** database: when, from which machine, what was asked, which sites were reached;
- one row per conversation in its **Conversations** database, shown as a table at the end of the report page.

Reports are never rewritten: each request is its own snapshot, and the newest is at the top. Do it without asking:
it only reads files and adds pages to those two databases.

| File | What it does |
|---|---|
| `scripts/collect_conversations.py` | finds the conversations on every site it can reach; writes JSON, prints a digest to read |
| `scripts/triage_conversations.py` | proposes warm / look closer / redundant / finished for every open conversation, with the reason (rules in its header) |
| `scripts/notion_report.py` | `--check` (are Notion reports on?), `--schema`, and JSON + your descriptions → the arguments of the Notion calls (`report.json`, `rows.json`, `view.json`) |
| `scripts/workhub_config.py` | reads the settings file (`~/.config/workhub-claude/config.json`) |
| `references/notion.md` | the Notion side: properties, views, rules for the calls |
| `references/build_database.md` | how the setup creates the two Notion databases |
| `tests/` | synthetic tests (pytest) |

Below, `<skill>` is the folder that holds this SKILL.md and `<scratch>` is the session's scratchpad folder (or a
temporary folder of your own).

## 0. Settings

1. `python3 <skill>/scripts/workhub_config.py check`. If it fails, tell the user to say "set up my work hub"
   (skill work-hub-setup) and stop.
2. `python3 <skill>/scripts/notion_report.py --check`: `on` → Notion reports (steps 3–4 below); `off` (exit 3)
   → chat only: skip steps 3–4 and say in one line that Notion reports are not set up
   (`references/build_database.md` sets them up when the user wants them).

## Pick the question

| The user asks | Collector options | Report kind |
|---|---|---|
| which conversations are active, open, running | none | Open now |
| which are warm, still have work, can be closed, are redundant | none, then the triage below | Open now |
| my last 10 conversations, the most recent ones | `--last 10` | Last N |
| what I worked on today, in the last 2 days | `--since 24`, `--since 48` | Last N |
| my conversations about X, where I worked on Y | `--match "x"` (newest 20) or `--match "x" --last 5` | Search |

`--last`, `--since` and `--match` combine. They include closed conversations (no window has them open) and mark the
open ones. `--match` looks in the name, the folder, every request and the last reply, in any case.
`--sites home,mycluster` limits the sites (keys from `--list-sites`).

## Steps

1. **Collect.**
   `python3 <skill>/scripts/collect_conversations.py [options] -o <scratch>/conversations.json`
   - It checks this machine's site, and every other site with an open shared SSH connection. It never opens a new
     login, so there is never a password or two-factor prompt. A few seconds.
   - The digest gives, per conversation: id, name, state, where it is open, folder, start, last message, requests,
     last reply, recent tools, background shells and notices.
2. **Describe each conversation** in `<scratch>/descriptions.json`, keyed by the 8-character id:
   `{"3f9c2e71": "Cleaning the survey table for Project A: step 2 of 3, waiting for the user's choice of filter."}`
   - One line, at most about 20 words: what it is doing or waiting for now, or, if closed, what it was doing when
     last used. Be concrete: model, step, job, machine, file. Don't restate the state.
   - Work from the digest. Read a transcript only when the digest does not say what is happening.
   - No participant, patient or case identifiers, no secrets: it goes to Notion.
   - The conversation running this skill: "This request: …".
   - Chat only (step 0 said `off`): keep the descriptions in your head and go to step 5.
3. **Create the report page.**
   `python3 <skill>/scripts/notion_report.py <scratch>/conversations.json --descriptions <scratch>/descriptions.json -o <scratch>`
   Then call `notion-create-pages` with the `parent` and `pages` of `<scratch>/report.json`, unchanged. Keep the
   id of the new page. If it prints "Not a site option in Notion yet", apply the ALTER statements of
   `notion_report.py --schema` as `references/notion.md` says (next time), and go on.
4. **Add the conversations.** Run the same command with `--report-url <that id>`. It writes:
   - `rows.json`: call `notion-create-pages` with its `parent` and `pages` (split into calls of at most 100
     pages). The result echoes every row; that is expected.
   - `view.json`: call `notion-create-view` with its fields. It puts the table of this report's conversations at
     the end of the report page, under "Conversations".
5. **Answer in the chat**, briefly:
   - the link to the report page (when Notion reports are on);
   - per site, the conversations found: name, state, `system:/folder`, last message, so the user can go there
     without opening Notion (for many, the ones that need the user first);
   - sites not checked, with the fix the digest gives (for example "run `cluster-login mycluster` in a terminal").
   - Chat only: also say where `conversations.json` is.

**Without the Notion tools** (a session with no Notion connection, also when step 0 said `on`): skip 3–4 and
answer in the chat from the digest.

## Warm, redundant or finished

When the user asks which conversations still have work ("warm"), which ones are redundant or which ones can be
closed, add a triage between collecting and describing:

1. **Collect** as in step 1, without options (open conversations only).
2. **Triage.**
   `python3 <skill>/scripts/triage_conversations.py <scratch>/conversations.json -o <scratch>/triage.json`
   It prints a proposal per conversation with its reason, from the records the work hub keeps: the Task ID each
   conversation recorded (the conversation-title state folder, `<paths.state_dir>/conversation_titles/`), the last
   reply, and, when the settings turn the orchestrator on (`workflow.orchestrator.enabled`), the orchestrator's
   `NOW.md` ("## Active rows" table, in `workflow.orchestrator.folder`). Task IDs use the prefix of
   `notion.task_id_prefix` (for example `TK-42`).
   - **WARM**: Claude is working in it, a background command still runs, it is the newest `[ORCH]` conversation
     (orchestrator on only), or its row is in NOW.md's active rows.
   - **LOOK CLOSER**: the rules cannot decide: the last reply ends with a question, its row is not in NOW.md, or
     nothing is clear.
   - **REDUNDANT**: an older `[ORCH]` conversation (orchestrator on only), or an older window on the same row as a
     newer one.
   - **FINISHED**: the last reply says the work is over ("you can /clear", "marked Done"), or no message for 2 days
     (`--stale-days`).
   - At the end: rows of NOW.md that have no open window.
3. **Decide every LOOK CLOSER yourself**: read its last reply (digest, or the transcript's tail) and NOW.md's line
   for its row, when there is one. Waiting for the user's answer → warm; covered by another conversation →
   redundant (name which one); done → finished. Overrule any proposal the evidence contradicts: the script is a
   first pass, the answer is yours.
4. **Report** as in steps 2–4. Start each description with `WARM:`, `Redundant (use <id>):` or `Finished:`, then
   the usual line.
5. **Answer** with what was asked. For "the warm ones": their names exactly as the window shows them, grouped by
   what they wait for (Claude working, the user's answer, a job, a time), one short line each. Then, in one line
   each, the redundant and finished ones that can be closed, and the rows with no window. Close nothing yourself:
   closing a window is the user's choice.

Without NOW.md (orchestrator off, or asked from a cluster where the file is not), the row rules are skipped and more
conversations land in LOOK CLOSER. Run `python3 -m pytest <skill>/tests -q` after changing the rules.

## How conversations are found

- **Sites** come from the settings (`--list-sites` prints them):
  - the **home site**: this machine plus every workstation or cluster marked `shares_home_with_this_machine` (one
    home folder, so one transcript store); its label is `skill_settings.active-conversations.home_site_label`
    (default "Workstations"). Asked from a cluster, the home site is that cluster;
  - each workstation with its own home folder and an `ssh_alias`;
  - each cluster (its `ssh_alias`, else its name).
- **Other sites** are read only through a shared SSH connection that is already open (`ssh -O check <alias>`), with
  `-o BatchMode=yes`. The script sends itself over that connection, so nothing needs to be installed there. A site
  without an open connection is listed as not checked, with the command that opens one.
- **Open ones.** Claude Code writes `~/.claude/sessions/<pid>.json` for each running process. Files of killed
  processes stay behind, so an entry counts only if its process id is alive with the same start tick
  (`/proc/<pid>/stat` field 22 = `procStart`) on the machine that runs it. The other machines of a site are asked
  through their open shared connection; with `skill_settings.active-conversations.key_login_to_other_machines` =
  `true`, also with an SSH key (still `BatchMode`, never a prompt). On a cluster, the login nodes are found from the
  host name (`login1` → `login1`…`login9`, the names that resolve). Machines not asked leave their recent entries
  "unverified".
- **All of them.** Each conversation keeps a transcript, `<claude folder>/projects/<folder>/<session id>.jsonl`
  (`conversations.claude_projects_dir`, default `~/.claude/projects`), after it closes. It gives:
  - the start, the last message and the working directory;
  - the name: the user's rename first, then the automatic title;
  - the requests, replies and tool calls for the digest.
- **Same conversation in two windows** (local VS Code and VS Code Remote-SSH): one row.
- **Tabs with no message yet** are counted in the report's Sites table, not listed.
- State: `<paths.state_dir>/active-conversations/` holds the default `conversations.json` and `machine_ids.json`
  (which machine-id belongs to which machine of the home site, learned on each run; safe to delete).

## Limits

- Claude Code conversations only; Codex threads are not included (find-conversation searches both).
- From a cluster, only that cluster and the sites it has open connections to are checked; the report says the rest
  in "Not checked".
- A closed conversation's machine is not known; its working directory is. A VS Code window is located by machine,
  app and folder, not by window.
- `Site`, `Sites checked` and `Not checked` are select properties with fixed options: a new machine in the settings
  needs its option added first (`references/build_database.md`, step 7).
