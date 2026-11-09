# Research log pages: layout, conversion and Notion payloads

Read this before writing or updating a page (`SKILL.md` → "Publishing a document").

## Page layout

```
## Source
Local original: `<machine>:/absolute/path/of/the/record.md`
Archive copy: `<machine>:/archive/path.md`                       (only when archive_dir is set)
Downloadable copy of the local Markdown:
<file src="file-upload://…"></file>
Resume this conversation: `cd <folder> && claude --resume <id>` (on <machine>)   (optional)
Published 2026-01-15 15:40 WET from SHA-256 `3fa8c1d09b2e` · Redactions: none
---
Project: <name> (key `<key>`) · Clusters: <labels, with roles when several> · Dates: <start – end>
<the document's full content, without its first-level title>
---
## Personal notes
*Your own notes go here. Automated updates never change this section.*
```

- `<machine>` is the name of the machine holding the file (`python3 scripts/workhub_config.py system-name`). The
  "Local original" line always shows where the file is now (rewrite it if the file moved; the key stays).
- Leave the file caption empty (`<file …></file>`): Notion shows the file name itself, while a caption such as
  `record.md` is turned into a bogus `http://record.md` link.
- Times on the "Published" line are local, in the owner's zone (`owner.time_zone`), with the zone abbreviation,
  for example `TZ=<owner.time_zone> date '+%Y-%m-%d %H:%M %Z'`.
- Resume line (optional, Claude Code only): the conversation id is the name, without `.jsonl`, of the newest file in
  `<conversations.claude_projects_dir>/<the working folder with "/", "." and "_" replaced by "-">/`. Leave the line
  out when unsure (several conversations open in that folder).
- Icon: 🗂️ for Session, Plan, Report and Notes pages; 📊 for Status.
- If the document opens with a short outcome or summary paragraph (before its first `##`), that paragraph moves
  into a `<callout icon="✅" color="green_bg">` right after the Project line instead of appearing as plain text.
  Never write a summary that is not in the document.
- The SHA-256 line (`python3 scripts/research_log.py hash <sanitized copy>`) is how later runs know whether the page
  is already current. A page without this line is treated as not current.

## Converting the Markdown

- Drop the first-level title (it is the page title); keep `##`/`###` headings, lists, task lists, code blocks and
  links.
- Turn pipe tables into `<table header-row="true"><tr><td>…</td></tr></table>`.
- Turn `####` headings into bold lines.
- Put text with angle brackets (`<eid>`) in backticks so that it is not read as a tag.
- Put file names such as `run.sh` or `notes.md` in backticks: Notion turns a bare `name.md` into a broken web link.
- Leave out the document's own `Notion page:` line (it would be a link to the page itself).

## Find by key

`notion-query-data-sources`:

```json
{"mode": "rows", "data_source_url": "<notion.research_log.data_source>",
 "filter": {"type": "group", "operator": "and", "filters": [
   {"type": "property", "property": "Source key", "propertyType": "text",
    "operator": "string_is", "value": {"type": "exact", "value": "project-a:docs/sessions/2026-01-15_1400_qc-run.md"}}]},
 "limit": 10}
```

The filter compares plain text, so it also finds keys stored in backticks or auto-linked by Notion. Keys returned
by rows mode carry `` ` `` or `[…](http://…)`: normalize them before comparing them yourself (remove the backticks,
turn `[x](http://x)` back into `x`). Do not trust SQL mode for recent pages (it can lag by days).

## Upload

1. `notion-create-file-upload` with `{"filename": "<name>.md"}` (also `.png`, `.pdf`).
2. One POST with every header listed in `upload_headers` (usually only `authorization`):
   `curl -s -X POST "<upload_url>" -H "authorization: <value>" -F "file=@<scratch>/upload/<name>.md"`.
   The JSON answer has `"status": "uploaded"` and `markdown_source` (`file-upload://…`).
3. Place it in the page within minutes (unplaced uploads expire): `<file src="file-upload://…"></file>`. A fetch
   later shows it as `notion-file-block://…`; a new upload replaces it in the next `replace_content`.

## Properties

```json
{"Name": "2026-01-15 — QC of the first 12 images", "Type": "Session", "Project": "Project A",
 "date:Date:start": "2026-01-15", "date:Date:end": "2026-01-16", "date:Date:is_datetime": 0,
 "Clusters": "[\"ws01\", \"Mycluster\"]",
 "Source key": "`project-a:docs/sessions/2026-01-15_1400_qc-run.md`"}
```

- New pages: `notion-create-pages` with `parent: {"data_source_id": "<data source id without collection://>"}`
  (`python3 scripts/workhub_config.py get notion.research_log.data_source`, then drop the `collection://` prefix),
  `icon`, `properties` and `allow_async: false`. Updates: `notion-update-page`, `command: "update_properties"`.
- The Source key goes in backticks so Notion does not turn `name.md` into a web link.
- Leave `date:Date:end` out for a single day. Status pages use a date and time with its offset (or in UTC, e.g.
  `"date:Date:start": "2026-01-15T14:00:00Z"`), with `is_datetime` 1.

## Pitfalls

- `replace_content` replaces the whole body: always include the preserved Personal notes, exactly as fetched
  (mentions and formatting survive the round trip).
- Fetched text is Notion markdown with escapes (`\~`, `\[`, `\*`); keep it as is when copying Personal notes back.
- Page creation and updates can run asynchronously by default: pass `allow_async: false` whenever the next step
  needs the page or its URL. Very long documents may need `allow_async: true`, then `notion-get-async-task` until it
  succeeds, then the verification checks.
- A select or multi-select value must already exist as an option; add it the maintenance-pass way first.
- Elapsed time proves nothing: an upload or write counts only after a fetch shows it.
