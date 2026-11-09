# Settings reference: `~/.config/workhub-claude/config.json`

One JSON file holds everything that differs between users. The **work-hub-setup** skill writes it from your
answers ("set up my work hub"); the other skills only read it. `config.example.json` next to this file shows
every key filled with example values. Another file can be used with the environment variable
`WORKHUB_CLAUDE_CONFIG`. Read a value with `python3 <any skill>/scripts/workhub_config.py get <dotted.key>`.

After editing the file by hand, run
`python3 <work-hub-setup>/scripts/configure.py validate` and, if you changed names, limits or the prefix,
`configure.py render-rules --apply` (it rewrites the rules block in `~/.claude/CLAUDE.md`).

## owner

| Key | Meaning | Default |
|---|---|---|
| `owner.name` | First name used in notes, e.g. "(Alex, Nov 4)" | required |
| `owner.time_zone` | IANA time zone (e.g. `Europe/Lisbon`); every clock time Claude shows uses it | detected |

## system_name

| Key | Meaning | Default |
|---|---|---|
| `system_name.override_env` | Environment variable whose value wins over the host name as this machine's name | `CC_CLUSTER` |
| `system_name.aliases` | `{host name: machine name}`, e.g. `{"login1": "mycluster"}` so all login nodes share one name | `{}` |

## notion

| Key | Meaning | Default |
|---|---|---|
| `notion.hub_parent_page_url` | Page under which setup builds a new hub | `""` |
| `notion.hub_page_url` | The hub page (filled by setup, or given when you adopt an existing hub) | `""` |
| `notion.tasks_database_url` | The Tasks database (filled by setup) | `""` |
| `notion.tasks_data_source` | The Tasks data source, `collection://…` (filled by setup) | `""` |
| `notion.views.now`, `notion.views.focus` | `view://…` addresses of the Now and Focus views (filled by setup) | `""` |
| `notion.task_id_prefix` | Prefix of the automatic Task IDs (`TK` → `TK-42`); unique in the workspace | `TK` |
| `notion.projects` | Project options, in order; `Miscellaneous` is always last | `["Writing and meetings", "Miscellaneous"]` |
| `notion.research_log.enabled` / `parent_page_url` / `data_source` | Optional Research log database (research-log, run-status-figure, weekly-recap publish there) | off |
| `notion.conversation_reports.enabled` / `parent_page_url` / `data_source` | Optional Conversation reports database (active-conversations) | off |

## workflow

| Key | Meaning | Default |
|---|---|---|
| `workflow.conversation_names` | The naming hook names conversations `[Mon-DD HH:MM][machine][UC] TK-42 Topic`; `false` = it stays silent (the row is still recorded for the other skills) | `true` |
| `workflow.orchestrator.enabled` / `folder` | One coordinating conversation; its NOW/QUESTIONS/DECISIONS files live in `folder` | off, `~/workhub/orchestrator` |
| `workflow.morning_brief.enabled` / `days` / `time` / `scheduler` | Morning Inbox filing and brief; `scheduler` = `claude-app`, `cron` or `manual` | off, `Mon-Fri`, `08:00`, `manual` |
| `workflow.now_limits.max_rows` / `max_in_progress` | Size of the Now view | `7` / `3` |
| `workflow.max_active_conversations` | Topic conversations open at once | `4` |
| `workflow.session_records_subdir` | Folder inside each project for session records | `docs/sessions` |

## machines

`machines.workstations[]`, one object per workstation:

| Key | Meaning |
|---|---|
| `name` | Short name used in file paths (`ws01`) |
| `ssh_alias` | `ssh <alias>` reaches it; `""` for this machine |
| `shares_home_with_this_machine` | Same home folder (network share) as this machine |
| `cpus`, `ram_gb` | Size, for the compute planner and the status figure |
| `gpus[]` | `{"model", "memory_gb", "count"}` |
| `usage_windows[]` | `{"days": "Mon-Fri", "from": "09:00", "to": "17:00", "rule": "blocked", "reason": "…"}`; time outside blocked windows is free |
| `notes` | Free text |

`machines.clusters[]`, one object per computing cluster:

| Key | Meaning |
|---|---|
| `name` | Short name (`mycluster`) |
| `ssh_alias` | Host alias in `~/.ssh/config` (cluster-login uses it) |
| `hostname` | Login host, e.g. `login.mycluster.example.org` |
| `user` | Your user name there |
| `scheduler` | `slurm` (others: read-only status only) |
| `slurm_accounts[]` | Accounts you submit with, e.g. `def-pi_cpu` |
| `shares_home_with_this_machine` | Normally `false` |

`machines.cluster_hops[]` (optional, default `[]`): Slurm clusters that are reached only from another
cluster's login node, for example a separate GPU side with its own Slurm database. Read by work-hub-cleanup and
weekly-recap.

| Key | Meaning |
|---|---|
| `name` | Short name (`mycluster-gpu`) |
| `through` | `name` of the cluster in `machines.clusters` whose shared login is used first |
| `login_node` | Login node reached from there with a second `ssh` |
| `slurm_accounts[]` | Accounts used there (optional) |

Remote machines are read only through SSH without prompts (`-o BatchMode=yes`); clusters only through a shared
login you opened with `cluster-login` (`ssh -O check <alias>` succeeds).

## cluster_login

| Key | Meaning | Default |
|---|---|---|
| `cluster_login.enabled` | Install the `cluster-login` command | off |
| `cluster_login.install_dir` | Where the command goes (should be on your `PATH`) | `~/.local/bin` |
| `cluster_login.socket_dir` | Folder for the shared-login sockets; must be machine-local (not on a network home) | detected |
| `cluster_login.duo_hint` | Reminder printed before the two-factor prompt | `""` |

## conversations, paths, runtime

| Key | Meaning | Default |
|---|---|---|
| `conversations.claude_projects_dir` | Claude Code conversation files | `~/.claude/projects` |
| `conversations.include_codex` / `codex_sessions_dir` | Also read Codex conversations | detected / `~/.codex/sessions` |
| `paths.state_dir` | Small state files (conversation names, indexes), mode 700 | `~/.local/state/workhub-claude` |
| `paths.outputs_dir` | Figures and recaps, one subfolder per skill | `~/workhub/outputs` |
| `runtime.python` | Python used for scripts that need extra packages (matplotlib) | `python3` |

## skill_settings

Options of single skills, as `skill_settings.<skill>.<key>`. Every one has a default in the skill's code.

| Skill | Key | Meaning | Default |
|---|---|---|---|
| research-log | `publish_mode` | `auto` = publish after substantial work; `ask` = ask once at the end | `"auto"` |
| research-log | `project_keys` | `{project: short key}` used in the Source key of each record | `{}` = name in lower case with dashes |
| research-log | `machine_labels` | `{machine-name prefix: label}` for the Clusters property (also used by run-status-figure) | `{}` = machine names |
| research-log | `archive_dir` | folder for a copy of each published record | `""` = no copy |
| research-log | `publish_hosts` | SSH aliases whose pending records get published | `"auto"` |
| work-hub | `brief_destination` | `notion` = a "Morning Brief" page under the hub; `chat` = reply only | `"notion"` |
| work-hub | `brief_sources` | extra sources for the brief: `calendar`, `email` | `[]` |
| work-hub | `brief_page_url` | the Morning Brief page | `""` = found by title, made on the first morning run |
| work-hub-cleanup | `project_rules` | sentences saying which work belongs to which project | `[]` = closest project, else Miscellaneous |
| weekly-recap | `project_rules_file` | JSON file mapping Slurm job names and folders to projects (example: `references/project_rules.example.json`) | `""` = by row or Project |
| compute-planner | `planner_dir` | folder for the planner's tasks, machine facts, plans and log | `<paths.outputs_dir>/compute-planner` |
| compute-planner | `horizon_days` | length of a plan in days | `7` |
| compute-planner | `shared_machines` | workstations shared with other people (checked live before a block is placed) | `[]` |
| compute-planner | `shared_cpu_percent` | another user's steady CPU use above this makes a shared machine busy | `30` |
| compute-planner | `ram_heavy_gb` | jobs at or above this RAM never overlap on one workstation | `null` = no such rule |
| compute-planner | `cluster_budgets` | per cluster: `unit`, `allocated`, `applies_to`, `account` | `{}` |
| compute-planner | `prefer_cluster_for_cpu` | cluster for CPU-only work whenever it meets the deadline there | `""` |
| active-conversations | `home_site_label` | name of the group of machines that share this home folder | `"Workstations"` |
| active-conversations | `key_login_to_other_machines` | also reach other machines with a key-only SSH login (`BatchMode`, never a prompt), not only through an open shared login | `false` |
| active-conversations | `conversations_data_source` | `collection://…` of the Conversations database | `""` |
