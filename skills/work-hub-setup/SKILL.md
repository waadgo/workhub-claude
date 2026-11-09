---
name: work-hub-setup
description: Set up the workhub-claude kit for a new user from scratch, or change its settings later. Asks every setup question at the start in one message (name, time zone, where the Notion Work Hub should live or which existing hub to use, Task ID prefix, projects, optional Research log and Conversation reports databases, conversation naming, rules, orchestrator, morning brief, machines, clusters, cluster-login), then writes the settings file, builds or connects the Notion hub, installs the hooks, the rules block, the orchestrator files and the cluster-login command, and verifies everything. Use it whenever the user says "set up my work hub", "install workhub-claude", "configure the work hub", "connect my Notion work hub", "change my work hub settings", "add a cluster / machine / project to my work hub settings", or when another workhub-claude skill reports that the settings file is missing.
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Work Hub setup

This skill makes the workhub-claude kit work for one user: it asks **all** questions first, then does
everything without further questions, then reports. Nothing personal is inside the kit; every personal value
goes into one settings file, `~/.config/workhub-claude/config.json` (mode 600), which only this skill writes.

`<skill>` below = this skill's folder (the folder of this SKILL.md). `<kit>` = the kit folder, printed by
`python3 <skill>/scripts/configure.py kit-root`.

## Ground rules

- **One question round.** Every question is in step 2. Later steps never ask, except (a) when an answer turns
  out to be invalid (step 3: one message listing only those answers), and (b) the single "go" of step 4.
  Errors during step 6 are handled with the fallback that step names, and reported at the end.
- **Show before writing.** Every local change is shown as a dry run in step 4; files that are replaced are
  backed up first (`<file>.bak-YYYYMMDD-HHMMSS`). Never use sudo; everything installs in the user's own folders.
- **Never delete** Notion pages, rows or local files. Never print secrets. Never type passwords or two-factor
  codes for the user.
- Plain words. Define a term the first time it appears (row, Task ID, topic card, hook, orchestrator, socket).
- File paths shown to the user: `<machine>:/absolute/path` (machine name from
  `python3 <skill>/scripts/workhub_config.py system-name`, or the D1 answer once given).

## 1. Preflight (no questions yet)

Run together:

```bash
python3 --version                                   # needs 3.8 or newer (3.9+ also checks the time zone)
python3 <skill>/scripts/configure.py kit-root       # where rules/, hooks/, templates/, tools/ are
python3 <skill>/scripts/configure.py detect         # time zone, CPUs, RAM, GPUs, shared home, folders
```

- **Notion connector.** Check that the Notion tools (`notion-fetch`, `notion-create-pages`, …) are available in
  this session. If not, stop and tell the user how to connect it, then to say "set up my work hub" again:
  in Claude Code run `claude mcp add --transport http notion https://mcp.notion.com/mcp`, then `/mcp` → notion →
  authenticate (or connect Notion under claude.ai → Settings → Connectors). This is the only stop before the
  questions.
- **Kit not found** (`kit-root` exits 2): the skill was copied alone. Tell the user to run `bash tools/install.sh`
  from the cloned repository (README "Install"), then come back.
- **Setup ran before** (`settings_file_exists` true): read it (`workhub_config.py show`). Its values become the
  defaults of step 2, and the user may answer "keep" for any group. A hub that already exists is never rebuilt.

## 2. Ask everything (one message)

Send the questions of `references/questionnaire.md`, groups A–G, in ONE message, with every default filled from
step 1. Group G = the skill options listed in "Skill options" below (only for skills that are installed).
Accept short answers ("A1 Sam, B1 new <link>, rest default"). Then wait.

## 3. Turn the answers into the settings file (dry run)

1. Write the answers as JSON in the shape of `<kit>/config/config.example.json` (keys from the table at the end
   of the questionnaire) to a scratch file, for example `<scratch>/workhub_answers.json`. Keys the user did not
   answer are left out (defaults apply). Answers "hub" for B5/B6 are left empty for now.
2. Run `python3 <skill>/scripts/configure.py write --answers <scratch>/workhub_answers.json` (dry run). It
   prints the merged settings and every problem (invalid time zone, a link without a Notion page id, a prefix
   with lower-case letters, an incomplete cluster entry, a socket folder on a network file system, …).
3. Problems → ONE message that lists only those answers with the reason, then repeat 3.2. No problems → step 4.

## 4. Show the plan, get one "go"

One message with: the settings summary (not the raw JSON unless asked), and every change the next step makes:

- Notion: "build a hub titled <B2> under <parent page>" or "connect the hub at <link> and add what is missing";
  the Research log / Conversation reports databases if chosen (the Morning Brief page is made by the work-hub
  skill on its first morning run).
- Files: the settings file path; the hooks folder `~/.local/share/workhub-claude/hooks/` and the two hook entries
  in `~/.claude/settings.json` (from `install_hooks.py`, dry run); the rules block in `~/.claude/CLAUDE.md` (C2; show its first lines from
  `configure.py render-rules`); the orchestrator folder (C3); `cluster-login` in the E1 folder and the
  `~/.ssh/config` blocks (E1/E2; show the blocks from the installer's dry run); the crontab line (C4 cron).

Ask: "Reply go to do all of this, or tell me what to change." Changes → back to step 3.2.

## 5. Write the settings file

```bash
python3 <skill>/scripts/configure.py write --answers <scratch>/workhub_answers.json --apply
python3 <skill>/scripts/configure.py validate
```

## 6. Do everything else (no more questions)

Do the parts in this order; each one that does not apply is skipped with one line in the report.

1. **Notion hub.** New hub: `references/notion_build.md` part A. Existing hub: part B. Both end by storing the
   addresses with `configure.py set … --apply` and verifying. If B5/B6 said "hub", now set
   `notion.research_log.parent_page_url` / `notion.conversation_reports.parent_page_url` to the hub page link.
2. **Optional databases.** Research log (B5 yes): follow `<kit>/skills/research-log/references/build_database.md`
   under `notion.research_log.parent_page_url`, then
   `configure.py set notion.research_log.data_source "collection://…" --apply` and
   `configure.py set notion.research_log.enabled true --json --apply`. Conversation reports (B6 yes): follow
   `<kit>/skills/active-conversations/references/build_database.md` under
   `notion.conversation_reports.parent_page_url`; it ends by naming the settings to store (both data sources and
   `enabled`), each stored with `configure.py set … --apply`. The parent page is already known here, so never
   ask for it again. A skill that is not installed → skip with one line.
3. **Hooks** (always; C1 only decides whether the naming hook renames). The installer copies
   `conversation_title.py`, `set_conversation_task.py`, `session_start_system_name.py` and `workhub_config.py` to
   `~/.local/share/workhub-claude/hooks/`, and registers two hooks in `~/.claude/settings.json` (UserPromptSubmit:
   names the conversation; SessionStart: prints this machine's name for file paths). It backs up `settings.json`,
   keeps every other entry and never adds a hook twice:
   `python3 <kit>/hooks/install_hooks.py --apply`, then `python3 <kit>/hooks/install_hooks.py --check` (exit 0 =
   installed and current). An installed copy that differs is replaced only with `--overwrite` (backup first).
4. **Rules block** (C2 yes): `python3 <skill>/scripts/configure.py render-rules --apply`. Codex users (F1 yes)
   may also want it in `~/.codex/AGENTS.md`: `render-rules --target ~/.codex/AGENTS.md --apply` (only if they said
   so in G or C2).
5. **Orchestrator** (C3 yes): `python3 <skill>/scripts/configure.py init-orchestrator --apply` (never overwrites).
6. **cluster-login** (E1 yes): `bash <kit>/tools/install_cluster_login.sh --apply`, plus `--ssh-config` when E2
   is yes (it reads install folder, socket folder, two-factor hint and the clusters from the settings file; the
   SSH block goes at the top of `~/.ssh/config`, ends with `Match all`, and is backed up and shown as a diff
   first). Check with `bash <kit>/tools/install_cluster_login.sh --check`. Inside Claude Code the login itself is
   refused on purpose: tell the user to run `cluster-login <cluster>` in their own terminal (they answer the
   password / two-factor prompt; Claude never does). Show `cluster-login --help` in the report.
7. **Morning brief** (C4 on):
   - *claude-app*: tell the user how to add a scheduled task in the Claude desktop app (Schedule → New task, days
     and time from C4, prompt `morning brief`, working folder = their home).
   - *cron*: add one line to the user's crontab (they said yes in C4), keeping every other line:
     `<minute> <hour> * * <days as 1-5> cd ~ && claude -p "morning brief" >> ~/.local/state/workhub-claude/morning_brief.log 2>&1`.
     Show `crontab -l` before and after.
   - *manual*: one line: say "morning brief" any morning.

## 7. Verify and report

Run `workhub_config.py check`, `configure.py validate`, the hook check of 6.3, and `notion-fetch` of the hub page.
Then report, in plain words, with full `<machine>:/path` paths and Notion links:

- what was created or changed (each item of step 4, done / skipped / fallback used, for example "prefix TK was
  taken, WH used");
- what the user does next: open the hub, write one line in the Inbox, start a new conversation and say
  "new topic: …" (or "file my inbox"), say "status" for a timeline figure;
- how to change a setting later: say "set up my work hub" again (only changed answers are needed), or edit the
  settings file and run `configure.py validate` and `render-rules --apply`;
- how to undo: `configure.py remove-rules --apply`, `install_hooks.py --remove --apply`,
  `install_cluster_login.sh --remove --apply`, delete `~/.config/workhub-claude/`; the Notion hub stays.

## Skill options

Options that single skills keep under `skill_settings.<skill>.<key>`. Ask them as group G (only for installed
skills; every one has a default, so "default" is a valid answer).

Store each answer with `configure.py set skill_settings.<skill>.<key> <value> [--json] --apply` (or in the answers
JSON of step 3). Keys marked "not asked" are filled by a step or by the skill itself.

| Skill | Key | Meaning | Default | Question (G) |
|---|---|---|---|---|
| research-log | `publish_mode` | `auto` = publish after substantial work; `ask` = ask once at the end | `"auto"` | G1 |
| research-log | `project_keys` | `{project: short key}` used in the Source key of each record | `{}` = name in lower case with dashes | G2 |
| research-log | `machine_labels` | `{machine-name prefix: label}` for the Clusters property (also used by run-status-figure) | `{}` = machine names | G3 |
| research-log | `archive_dir` | folder for a copy of each published record | `""` = no copy | G4 |
| research-log | `publish_hosts` | SSH aliases whose pending records get published | `"auto"` | not asked |
| work-hub | `brief_destination` | `notion` = a "Morning Brief" page under the hub; `chat` = reply only | `"notion"` | G5 |
| work-hub | `brief_sources` | extra sources for the brief: `calendar`, `email` | `[]` | G6 (only when those connectors exist) |
| work-hub | `brief_page_url` | the Morning Brief page | `""` = found by title, made on the first morning run | not asked |
| work-hub-cleanup | `project_rules` | sentences saying which work belongs to which project | `[]` = closest project, else Miscellaneous | G7 |
| weekly-recap | `project_rules_file` | JSON file mapping Slurm job names and folders to projects (example: `references/project_rules.example.json`) | `""` = by row or Project | G8 |
| compute-planner | `planner_dir` | folder for the planner's tasks, machine facts, plans and log | `<paths.outputs_dir>/compute-planner` | G9 |
| compute-planner | `horizon_days` | length of a plan in days | `7` | G9 |
| compute-planner | `shared_machines` | workstations shared with other people (checked live before a block is placed) | `[]` | G10 |
| compute-planner | `shared_cpu_percent` | another user's steady CPU use above this makes a shared machine busy | `30` | G10 |
| compute-planner | `ram_heavy_gb` | jobs at or above this RAM never overlap on one workstation | `null` = no such rule | G11 |
| compute-planner | `cluster_budgets` | per cluster: `unit`, `allocated`, `applies_to`, `account` | `{}` | G12 |
| compute-planner | `prefer_cluster_for_cpu` | cluster for CPU-only work whenever it meets the deadline there | `""` | G13 |
| active-conversations | `home_site_label` | name of the group of machines that share this home folder | `"Workstations"` | G14 |
| active-conversations | `key_login_to_other_machines` | also reach other machines with a key-only SSH login (`BatchMode`, never a prompt), not only through an open shared login | `false` | G15 |
| active-conversations | `conversations_data_source` | `collection://…` of the Conversations database | `""` | not asked (step 6.2) |
