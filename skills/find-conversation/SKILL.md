---
name: find-conversation
description: Find a past Claude Code or Codex conversation from what was done in it, in seconds, and answer with its name, start date, last message, folder and how to go back to it. Searches every conversation on this machine (and the machines that share its home folder) and on every other workstation or cluster in the work-hub settings that has an open shared SSH connection, through a small full-text index that only re-reads changed transcripts. Use this whenever the user asks in which conversation something was done, "the conversation where we did X", "find the chat about Y", "when did we talk about Z", "which session ran this command", or wants to resume or go back to an earlier conversation they only half remember, even if they don't say "search" or "find". Not for which conversations are open or warm right now (active-conversations) and not for a weekly recap.
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Find a conversation

One script, one call, usually 1–10 seconds:

`python3 <skill>/scripts/find_conversation.py "<words>" [--after YYYY-MM-DD] [--before YYYY-MM-DD] [--top 8] [--sites all|local|mycluster,ws03]`

`<skill>` is the folder that holds this SKILL.md.

It searches this site's conversations (this machine and every machine that shares its home folder) and every other
site of the settings whose shared SSH connection is already open (never a new login, never a password or two-factor
prompt). It prints the best matches per site:
- the name, and Claude Code or Codex;
- the start, the last message and the number of requests;
- the `system:/folder` and the session id;
- how many of the words were found, and the sentence where they were found.

The conversation that runs the search is left out.

## 0. Settings

`python3 <skill>/scripts/workhub_config.py check`. If it fails, tell the user to say "set up my work hub" (skill
work-hub-setup) and stop. The script reads:
- `conversations.claude_projects_dir` (Claude Code transcripts);
- `conversations.include_codex` and `conversations.codex_sessions_dir` (Codex threads, only when included);
- `paths.state_dir` (the index and the conversation-title state);
- `owner.time_zone` (dates);
- `system_name.*` (this machine's name);
- `workflow.conversation_names` (read the machine from hook names);
- `machines.workstations[]` and `machines.clusters[]` (the other sites).

## Steps

1. **Pick 2–6 distinctive words** from the user's question:
   - tool or command names (`rsync`, `sbatch`);
   - project, model and dataset names, machines and clusters, file names;
   - Task IDs (for example `TK-42`): a conversation that recorded its row is found by the row's ID and topic.

   Leave out generic words ("results", "data", "fix"); the script drops filler words anyway.
   When the user gives a time ("last week", "in March"), add `--after` / `--before`.
2. **Run the script once.** Don't spawn agents and don't grep transcripts yourself first: the index is faster.
3. **Judge the matches.** Conversations that contain more of the words come first, then the most relevant. A word
   in the name counts most, then a word the user typed, then replies and commands.
   - **Clear winner** (all words, and the excerpt or name fits): answer.
   - **Weak or no matches:** run it once or twice more with other forms or synonyms.
     - Words are matched by prefix and stem: "table" finds "tables", "converting" finds "convert".
     - Words are not matched across stems: "conversion" ≠ "convert", "RAM" ≠ "memory".
   - **Several close candidates:** show the 2–3 best and say how they differ (dates, names). Or read a few lines of
     each transcript to decide (the path is in the `--json` output, field `transcript`).
   - **Long-running conversations** (a coordinating conversation, big topics) mention almost everything. Prefer the
     conversation whose name or first request is about the topic.
4. **Answer in a few lines** per conversation:
   - its name exactly as the window shows it, and Claude Code or Codex;
   - start date and time and last message (the times are in the owner's time zone; the script prints it);
   - the folder as `system:/absolute/path`, and the session id;
   - how to go back:
     - in VS Code, the past-conversations list (search for the name);
     - in a terminal, from that folder, `claude --resume <session id>` (Codex: `codex resume <session id>`);
   - one line on why it matches.

   Say which sites were not searched (the script lists them). Opening their shared connection adds them, for
   example with `cluster-login <alias>` when the kit's cluster-login tool is set up, or `ssh -fN <alias>`.

This is a look-up: no Work Hub row, no files written apart from the index.

## Files

| File | What it does |
|---|---|
| `scripts/find_conversation.py` | updates the index and searches it, on this site and on the other open sites (standard library only) |
| `scripts/workhub_config.py` | reads the settings file (`~/.config/workhub-claude/config.json`) |
| `tests/test_find_conversation.py` | synthetic smoke tests: `python3 -m pytest <skill>/tests -q` |

- **Where the index is:** `<paths.state_dir>/find-conversation/index.sqlite` on each site (mode 600, folder mode
  700). On another site it is in the same place relative to its home folder (default
  `~/.local/state/workhub-claude/find-conversation/`).
- **How long it takes:** the first run on a site builds the index (about a minute for a few thousand
  conversations). Later runs re-read only the transcripts that changed. `--rebuild` starts it again from nothing.
- **What is indexed:**
  - what was typed, the replies, and the commands that were run;
  - the row's Task ID and topic, from the conversation-title state (`<paths.state_dir>/conversation_titles/`);
  - not tool outputs, and not sub-agents.

## Limits

- **Machine of a closed conversation.** It is known only from its name tag (for example
  `[Jan-05 11:15][ws02][UC] …`), and only when `workflow.conversation_names` is on. Otherwise the machine running
  the search is shown. The folders are shared, so the path is right.
- **Codex thread names** come from `<codex home>/session_index.jsonl`. A thread with no name shows its first request.
- **Other sites need nothing installed:** the script sends itself over the open connection. The first search on a
  site builds its index there, in its home folder.
- **On another site** the default transcript folder (`~/.claude/projects`) is used when this machine's setting is
  not written from the home folder (`~/…`). Codex threads are searched there only when they are included here.
