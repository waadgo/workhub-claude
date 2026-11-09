---
name: research-log
description: Publish research records (session records, ExecPlans, reports, notes and status snapshots written as local Markdown) to the user's Notion "Research log" database, one page per document, with the full sanitized text, a downloadable .md copy and a Personal notes section that updates never touch; build the log from scratch on first use and keep it consistent on every run (identity keys, duplicates, schema, pending publications). Use it after substantial work, or whenever the user says "publish / export / sync this to Notion", "put the session record in my research log", "update the Notion page", "set up my research log", "check / audit / clean my research log", or "publish what's pending", even without saying Notion.
license: Apache-2.0
metadata:
  author: Walter Adame-Gonzalez
  project: workhub-claude
  url: https://github.com/waadgo/workhub-claude
  version: "1.0.0"
---

# Research log

The local Markdown files in each project (`docs/sessions/…`, plans, reports) are the originals. The Research log in
Notion is their browsable, searchable copy: one page per document, updated in place when the document changes, never
duplicated. Claude does all of it; the user only reads, and writes in each page's Personal notes. Other kit skills
write to the same database: run-status-figure (Type Status) and weekly-recap (Type Report).

In the commands below, `$S` is this skill's folder.

## 0. Before anything

Run `python3 $S/scripts/workhub_config.py check`. If it fails, tell the user to say "set up my work hub" (skill
work-hub-setup) and stop. Then run `python3 $S/scripts/research_log.py settings` and keep its answer: it holds
every setting below, already resolved.

## 1. Settings

All in the workhub-claude settings file (`~/.config/workhub-claude/config.json`), which only the work-hub-setup
skill writes. This skill never edits it.

| Setting | Key | Default |
|---|---|---|
| Research log on or off | `notion.research_log.enabled` | `true` |
| its data source (`collection://…`) | `notion.research_log.data_source` | empty = not built yet |
| page to build it under | `notion.research_log.parent_page_url` | empty = ask (a page URL, or "private") |
| owner's first name (title of the records page) | `owner.name` | none |
| time zone of the "Published" line | `owner.time_zone` | `UTC` |
| projects (the Project options) | `notion.projects` | none |
| project keys | `skill_settings.research-log.project_keys` | `{}` = from the name: "Project A" → `project-a` |
| publish without asking | `skill_settings.research-log.publish_mode` | `"auto"` (`"ask"` = ask once at the end) |
| machine labels | `skill_settings.research-log.machine_labels` | `{}` (see "Machine labels") |
| archive folder | `skill_settings.research-log.archive_dir` | empty = no archive copy |
| other machines' pending queues | `skill_settings.research-log.publish_hosts` | `"auto"` = every listed machine that does not share this home folder |
| where session records go | `workflow.session_records_subdir` | `docs/sessions` |

`settings` prints `state`: `ready` (go on), `not built` (offer section 4), or `disabled` (the user switched the log
off: publish nothing, say so in one line, stop).

## 2. Which part to follow

| The user wants | Section |
|---|---|
| a log (first use), or no log can be found | 4. Building the log |
| substantial work just finished, or "publish / sync / export this" | 6, then 7 |
| "check / audit / clean my research log" | `references/audit.md` |
| "publish what's pending", or Notion is not available | 9 |

Every run that writes to Notion starts with 8 (a short maintenance pass). Sections 3 and 5 apply everywhere.
Layout, conversion, payloads and pitfalls: `references/page_format.md`. Notion tool names vary between clients
(`notion-fetch`, `fetch`, `mcp__…__notion-fetch`); use the matching one.

## 3. How the log works

### Notion structure

A page "<owner name>'s research records" holds a short explanation and the full-page database "Research log",
with six properties (Name, Type, Project, Date, Clusters, Source key) and three views (Sessions, All documents,
Status). The exact schema, which other skills rely on, is in `references/build_database.md` → section 1.

Finding the log: the data source in the settings. When it is empty, search Notion for "Research log" and keep the
database whose data source has those six properties; several matches → ask which; none → offer section 4.

### Identity: one page per original document

- **Source key** identifies a document for its whole life: `project-a:docs/sessions/2026-01-15_1400_qc-run.md`
  (`<project key>:<path relative to the project root>`). The project root is the folder that holds `docs/` (usually
  the git repository root). No machine names, mount prefixes or home folders in the key.
- A saved key is never changed, even if the file moves. Archive copies, symlinks and copies on another machine are
  the same document: they update the same page.
- The local record remembers its page: a last line `Notion page: <url>` (the URL without its `?pvs=…` suffix). This
  line is left out of the page body (it would be a link to itself).
- Status snapshots are the exception: each snapshot is a new page (key `<project key>:status/<folder>`), because
  the view is a history. Weekly recaps use `weekly-recap:YYYY-Www`.

### Machine labels

The "Clusters" property holds machine labels. One rule, shared with run-status-figure
(`python3 $S/scripts/research_log.py label [machine]` applies it):
1. `machine_labels`: the entry whose key is the longest prefix of the machine name (`{"ws": "Lab"}` turns ws01 and
   ws02 into "Lab");
2. else a machine listed in `machines.clusters`: its name with a capital first letter (`mycluster` → `Mycluster`);
3. else the machine name itself (`ws01`).

The machines of a document are those it says the work ran on; when it names none, the machine holding the file. On
an update, Clusters is the union of the page's labels and the new ones, except that a label no longer in use for any
machine (renamed in `machine_labels`) is replaced, not kept.

## 4. Building the log

Follow `references/build_database.md` (the same procedure the work-hub-setup skill uses). Afterwards the data source
must be recorded in `notion.research_log.data_source` by work-hub-setup: give the user the value and tell them to say
"set up my work hub" (it finds the new log by search), or to paste it into the settings file.

## 5. Rules

- **The local Markdown is authoritative.** Publish its full sanitized content plus a downloadable copy; never
  replace it with a summary. Never edit the local original except to add the `Notion page:` line.
- **Sanitize both forms** (page text and attachment): remove credentials, tokens, passwords, identifiers of
  participants, patients or cases (for example `sub-1234567`, medical record numbers, names, dates of birth) and
  other identifying details. Replace them with exactly `[participant ID removed]` or `[secret removed]` (fixed
  wording keeps the SHA-256 stable between runs). Keep file names, paths, commands, formulas, numbers and
  placeholders such as `sub-<id>` unless they identify someone. Disclose redactions on the "Published" line
  ("Redactions: 1 participant ID, 1 token").
- **No participant images.** Images of research participants (scans, photos, figures that show them) are never
  uploaded to Notion, to artifacts or to any other LLM-connected page, and Claude does not open them with its
  image-reading tool. A record that refers to such a figure keeps its path as text; the user looks at it.
- **Scope.** Publish only documents created or updated for the current work, plus documents the user names. Never
  scan and upload every Markdown file. Never upload raw data, participant tables, checkpoints or whole repositories.
- **Never overwrite Personal notes**, never delete a page, never create a second page for a document that has one.
- **Skip unchanged documents** (same SHA-256 as the page).
- **Conflicts.** If two copies of a document differ and neither is clearly the newer version of the other, or two
  pages share one Source key, publish nothing for that document and report it.
- **Verify before claiming.** A publication counts only after the checks of step 7.8 pass. Otherwise it is pending
  (section 9), and the reply says so.

## 6. When to publish, and the local record

Publish (with `publish_mode` `auto` without asking; with `ask`, ask once at the end):
- after substantial work: a multi-step change, an analysis, a pipeline run, a decision with consequences, work
  likely to continue in another session;
- a plan, report or notes file written or updated for the current work;
- whatever the user names.

Do not publish trivial explanations, read-only answers or routine status replies.

Publishing needs a local document. If the work has none, write one first, at
`<project root>/<session_records_subdir>/YYYY-MM-DD_HHMM_short-description.md` (time in the owner's zone):

```markdown
# <Concise topic>

<One or two sentences: the outcome.>

## Objective
## What was done            (decisions and their reasons, short)
## Files created or changed (absolute paths, labelled with the machine)
## Commands                 (exact, runnable)
## Results and checks       (numbers, tests run and their outcome)
## Open items
```

A record is a reviewable summary, not a diary: commands, settings, formulas, paths and output locations must be
inspectable. A plan goes to `docs/exec_plans/`, a report to `docs/reports/` (Type Plan, Report), notes to
`docs/notes/` (Type Notes).

## 7. Publishing a document

1. **Identity.** Project and key: from `settings` → `projects`. An unknown project: look for its key in the
   project's AGENTS.md, CLAUDE.md or README; otherwise ask once, and tell the user that work-hub-setup can add the
   project to `notion.projects` (meanwhile use `research_log.py project-key "<name>"`). Type from the folder
   (`sessions` → Session, `exec_plans`/`plans` → Plan, `reports`/`investigations` → Report, `notes` → Notes).
   Source key from the project root. Clusters from the machines the document says the work ran on (section 3).
   Name from the date in the file name and the document's title.
2. **Find its page**, in this order: the `Notion page:` line of the local file; a rows-mode query on the key
   (`references/page_format.md` → "Find by key"); nothing → new page. Two or more pages for one key → stop, report
   the conflict.
3. **Link the local file.** New document: create its page first with its properties and the single line
   "Publishing…" (`notion-create-pages`, `allow_async: false`). If the local original has no `Notion page:` line (new
   page, or a page found by key), add it as its last line, with the URL stripped of `?pvs=…`. Do this before
   hashing, so the hash covers the final file.
4. **Sanitize** into `<scratch>/upload/<same file name>` (a temporary copy in the session's scratch folder; the
   original stays unchanged) and compute `python3 $S/scripts/research_log.py hash <scratch>/upload/<name>`.
5. **Compare.** Existing page: fetch it. Same SHA-256 on its "Published" line → skip, report "already current".
   Otherwise keep the text from `## Personal notes` to the end of the page, character for character, as fetched.
6. **Upload** the sanitized copy (`references/page_format.md` → "Upload"), then write the body with
   `notion-update-page`, `command: "replace_content"`, `allow_async: false`: the page layout, the converted
   document (without its `Notion page:` line), then the preserved Personal notes (or the empty template for a new
   page). `replace_content` is right here even if the Notion tool discourages it, because the Personal notes are
   copied back unchanged; read the tool's markdown guide first if it asks you to. Update the properties
   (`update_properties`): Date end when the work continued on a later day, Clusters as the union of old and new
   labels, Name only if the topic changed. Keep the saved Source key.
7. **Archive copy** (when `archive_dir` is set): copy the sanitized file there. If a copy with that name exists and
   its SHA-256 matches neither the new nor the previously published version, someone edited it: do not overwrite;
   report it.
8. **Verify** by fetching the page: properties as intended; Source key matches; every heading of the local document
   is present and nothing is cut off; exactly one `<file …>` block; the Personal notes section unchanged; the
   "Published" line shows the new SHA-256. Then remove the document from the pending queue if it was there
   (`research_log.py pending remove <path>`).
9. **Report** one line per document: its title, "created", "updated" or "already current", and the link. Mention
   redactions and anything left pending.

Status snapshots: always a new page (Type Status, icon 📊, Date with time), with the snapshot's tables as text;
images and PDFs uploaded the same way as the `.md` file. The run-status-figure skill builds these pages itself
(`notion_page.py`).

## 8. Maintenance pass (start of every run that writes, a few calls)

1. Find the log (section 3) and fetch its data source.
2. **Schema.** The six properties exist with the right types, and Type has its five options. A project or label
   needed now and missing as an option: add it with
   `ALTER COLUMN "Project" SET SELECT('<existing 1>':blue, …, '<new>':<color>)` or
   `ALTER COLUMN "Clusters" SET MULTI_SELECT(...)`, listing **every** existing option with its current colour plus
   the new one (an omitted option is deleted, with its values). Give the new option the first colour of blue,
   green, purple, orange, red, brown, pink, yellow, gray that the property does not use yet. Never rename or drop
   properties.
3. **Pending.** Read the pending queues (section 9); publish what they list, after the current documents.
4. **Duplicates** for the keys you are about to publish (the "Find by key" query): more than one page → report, do
   not publish that document.

## 9. Without Notion: the pending queue

When the Notion tools are missing, or a publication fails or cannot be verified:
1. `python3 $S/scripts/research_log.py pending add <absolute path> ...` (the queue is
   `<paths.state_dir>/research-log/pending.txt`, default `~/.local/state/workhub-claude/research-log/pending.txt`;
   one path per line, no duplicates; `--dry-run` shows the change).
2. Say: "Not published yet: queued in `<machine>:<pending file>`; the next session with Notion will publish it."

A session with Notion publishes the queue of its own machine (`research_log.py pending list`) and, for each alias
in `settings` → `publish_hosts`, reads that machine's queue **only through a connection that needs no prompt**:
first `ssh -O check <alias>` (an already open connection; for a cluster this is the only way), or for a workstation
`ssh -o BatchMode=yes <alias> true`. Then
`ssh -o BatchMode=yes <alias> cat <remote_pending_path>` (the path printed by `settings`, relative to the remote home
folder), copy each listed file with `scp -o BatchMode=yes <alias>:<path> <scratch>/remote/` and publish it with that
machine's label (the "Local original" line names the remote machine). Never log in for the user: an alias that
fails is reported as not reached ("open it with cluster-login"). After a verified publication, remove the line from
that queue (`research_log.py pending remove` locally; on the remote machine rewrite the file without it through the
same connection).

A queued `page.json` is a status figure of the run-status-figure skill: publish it with that skill's
`references/notion.md` (upload the PNG and PDF from the same folder, rerun its `notion_page.py` with `--image` and
`--pdf`), not as a Markdown record.

## Tests

`python3 -m pytest $S/tests -q`: settings resolution, keys, labels, the pending queue and the hash, with a temporary
settings file (`WORKHUB_CLAUDE_CONFIG`) and synthetic files only.
