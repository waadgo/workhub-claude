# The setup questions (asked all at once, in one message)

Copy these into ONE message. Fill every `[default]` from `configure.py detect` (and from the earlier settings
file when setup runs again). Keep the numbering so the user can answer "A1 Sam, B2 <link>, rest default". Skip a
whole group only where the note says so. Plain words; explain a term the first time it appears.

The user may instead copy `config/config.example.json` of the kit, fill it, and give its path: then read that
file as the answers and ask only what is missing or invalid.

---

**A. You**
- **A1** Your first name, used in notes such as "(Sam, Nov 4)". [no default]
- **A2** Your time zone (IANA name). [detected, e.g. Europe/Lisbon]

**B. Notion**
- **B1** Build a new Work Hub, or use one you already have?
  - *new*: paste the link of the Notion page under which the hub should live (open the page → ⋯ → Copy link).
  - *existing*: paste the link of your hub, for example your copy of the published template
    (https://veiled-clementine-0d4.notion.site/Work-Hub-41371f09657181cfa7aaebd9631907bc → Duplicate).
- **B2** (new hub only) Title of the hub page. [Work Hub]
- **B3** Prefix of the Task IDs (row numbers such as TK-42), 1–6 capital letters, and a second choice in case the
  first is already used by another database in your workspace (Notion requires unique prefixes). [TK, then WH]
- **B4** Your main projects, in order (2–10 names; every row gets one; "Miscellaneous" is added for the rest).
  [Writing and meetings]
- **B5** A Research log database for status figures and weekly recaps? (yes/no; if yes, under which page:
  link, or "hub" for the hub page) [yes, hub]
- **B6** A Conversation reports database, for the lists of open conversations? (yes/no; page link or "hub")
  [no]

**C. How you work**
- **C1** Name each Claude Code conversation after its row, for example `[Nov-04 09:10][ws01][UC] TK-42 Clean
  data`? (The hooks are installed either way; with "no" the naming hook stays silent.) [yes]
- **C2** Add the Work Hub rules (rows, topic cards, Done reports, file paths) to `~/.claude/CLAUDE.md`? They go
  between two marker lines, so the rest of the file is not touched. [yes]
- **C3** Use an orchestrator: one coordinating conversation that collects every question for you and approves
  long jobs, useful when several conversations run in parallel? (yes/no; folder for its files)
  [no, ~/workhub/orchestrator]
- **C4** Morning brief (Claude files the Inbox and writes a short plan): on or off; days; time; how to run it:
  *claude-app* (a scheduled task in the Claude desktop app), *cron* (a crontab line running `claude -p`, added
  only with this yes), or *manual* (you say "morning brief"). [off]
- **C5** Limits: rows in Now [7], of which In progress [3]; topic conversations open at once [4].
- **C6** Folder, inside each project, for the session records Claude writes. [docs/sessions]

**D. Machines** (used by the status figure, the compute planner, the recap and the conversation search)
- **D1** This machine: name in file paths [detected host name]; CPUs/RAM/GPUs [detected]; times it must not be
  used for your jobs, such as "Mon–Fri 09:00–17:00 blocked: shared during the day" (days as Mon-Fri or
  Mon,Wed,Fri; all other times count as free). [none]
- **D2** Other workstations you use: for each, name, SSH alias, same home folder as this one (yes/no), CPUs, RAM,
  GPUs, blocked times. [none]
- **D3** Computing clusters: for each, short name, login host name, your user name there, scheduler [slurm],
  your Slurm accounts (for example def-pi_cpu). Is any cluster reached only from another cluster's login node
  (for example a separate GPU side)? Then its name, the cluster it goes through, and its login node. [none]

**E. cluster-login** (skip when D3 is "none")
- **E1** Install `cluster-login`, a command that opens one shared SSH login per cluster (you answer the
  password or two-factor prompt once; Claude then reuses that login to read job queues, and never types
  passwords itself)? Install folder [~/.local/bin]; folder for the shared-login sockets [detected: machine-local];
  reminder text shown at the two-factor prompt [none]. [yes]
- **E2** May setup add one `Host` block per cluster to `~/.ssh/config`? It shows the blocks first and keeps a
  backup. [yes]

**F. Conversations and folders**
- **F1** Also read Codex conversations (`~/.codex/sessions`)? [detected: yes if that folder exists]
- **F2** Folder for figures and recaps [~/workhub/outputs]; folder for small state files
  [~/.local/state/workhub-claude].

**G. Options of single skills** (only for installed skills; "G default" keeps every default)
- **G1** Research log: publish after substantial work without asking (*auto*), or ask first (*ask*)? [auto]
- **G2** A short key per project for the Research log, e.g. "Survey study → survey"? [derived from the name]
- **G3** Group machines under one label in the Research log and status figures, e.g. "ws → Lab"? [none]
- **G4** Keep a copy of each published Research log record in a folder? Which? [none]
- **G5** (morning brief on) The brief as a Notion page under the hub (*notion*) or only as a chat reply (*chat*)?
  [notion]
- **G6** (only when a calendar or e-mail connector is available) Include today's calendar and/or e-mails that
  need action in the brief? [none]
- **G7** Which kinds of work belong in which project? One sentence per rule, e.g. "Anything about the survey
  data goes to Survey study". [none: the closest project, else Miscellaneous]
- **G8** A JSON file that maps Slurm job names or folders to projects, for the weekly recap? Path. [none]
- **G9** Compute plans: folder [~/workhub/outputs/compute-planner]; days ahead [7].
- **G10** Which workstations do you share with other people, so jobs wait while someone else uses the GPU or
  more than some CPU? Which CPU share counts as busy? [none; 30 %]
- **G11** Only one big-memory job per workstation at a time? From how many GB? [no such rule]
- **G12** Any cluster allocation to stay within? For each: cluster, account, amount, unit (GPU-hours or
  core-hours), which job types it covers. [none]
- **G13** Send CPU-only jobs to a cluster whenever it meets the deadline there? Which cluster? [none]
- **G14** Name for the group of machines that share this home folder, used in conversation lists. [Workstations]
- **G15** May the conversation lists reach your other workstations with your SSH key (never a password prompt),
  not only through a login you opened? [no]

---

## How the answers map to the settings file

| Answer | Settings key(s) |
|---|---|
| A1, A2 | `owner.name`, `owner.time_zone` |
| B1 | `notion.hub_parent_page_url` (new) or `notion.hub_page_url` (existing) |
| B2 | not stored (used once to title the page) |
| B3 | `notion.task_id_prefix`; the second choice is used only when the first is refused |
| B4 | `notion.projects` |
| B5, B6 | `notion.research_log.enabled`/`parent_page_url`, `notion.conversation_reports.enabled`/`parent_page_url` ("hub" = filled with the hub page link after step 5) |
| C1 | `workflow.conversation_names` |
| C2 | not stored (decides step 6.4) |
| C3 | `workflow.orchestrator.enabled`, `workflow.orchestrator.folder` |
| C4 | `workflow.morning_brief.enabled`, `days`, `time`, `scheduler` (`claude-app`, `cron`, `manual`) |
| C5 | `workflow.now_limits.max_rows`, `max_in_progress`, `workflow.max_active_conversations` |
| C6 | `workflow.session_records_subdir` |
| D1, D2 | `machines.workstations[]`: `name`, `ssh_alias` ("" for this machine), `shares_home_with_this_machine`, `cpus`, `ram_gb`, `gpus[]` (`model`, `memory_gb`, `count`), `usage_windows[]` (`days`, `from`, `to`, `rule`, `reason`), `notes` |
| D3 | `machines.clusters[]`: `name`, `ssh_alias` (= name unless the user has one), `hostname`, `user`, `scheduler`, `slurm_accounts[]`, `shares_home_with_this_machine` (false); hops: `machines.cluster_hops[]`: `name`, `through`, `login_node`, `slurm_accounts[]` |
| E1 | `cluster_login.enabled`, `install_dir`, `socket_dir`, `duo_hint` |
| E2 | not stored (decides step 6.6) |
| F1 | `conversations.include_codex` |
| F2 | `paths.outputs_dir`, `paths.state_dir` |
| G1–G15 | `skill_settings.<skill>.<key>`, one key per answer as listed in SKILL.md "Skill options" |

The detected host name is the default machine name. When the home folder is shared with other machines
(detected `home_is_shared`), say so in D1/D2: those machines see the same skills and settings, and the socket
folder must stay machine-local.
