# Building the Conversation reports databases

The procedure the **work-hub-setup** skill follows (or Claude, when the user asks to turn the reports on) to create
the Notion side of the active-conversations skill. It creates one hub page with two inline databases, **Reports**
and **Conversations**, sets their views, and writes their addresses into the settings. It holds no ids: every id
comes from the result of the previous call.

Ask the user first, in one question: "Should each 'which conversations are open?' request also be saved as a report
in Notion (a page per request, a row per conversation)? If yes, under which Notion page?" No → set
`notion.conversation_reports.enabled` to `false` and stop; the skill then answers in the chat only.

## 0. Inputs

- The parent page: `notion.conversation_reports.parent_page_url` (ask for it when empty; the Work Hub page itself
  is a good default).
- The site options: `python3 <active-conversations folder>/scripts/notion_report.py --schema` prints JSON with
  `site_options`, `reports_create`, `conversations_create`, `reports_alter` and `conversations_alter`. Use those
  statements exactly; they are the one source of the schema. (It needs the settings file with the machines filled
  in, so run it after the machines step of the setup.)

## 1. The hub page

`notion-create-pages` with the parent page as parent and one page:

- title `Conversation reports`, icon 🧭;
- content: a gray callout that says what this is, for example
  `Each time you ask Claude which conversations are open (or for your last N, or the ones about a topic), the active-conversations skill adds one report below: a page in Reports and one row per conversation in Conversations. Reports are snapshots and are never rewritten.`

Keep the new page's id (the hub).

## 2. The Reports database

`notion-create-database` with the hub as parent page, title `Reports`, and as schema the `reports_create`
statement from step 0. The result holds the new data source in a `<data-source>` tag: keep its `collection://…`
URL (the Reports data source) and its bare id (the same without `collection://`).

## 3. The Conversations database

`notion-create-database` with the hub as parent page, title `Conversations`, and as schema the
`conversations_create` statement with `<reports data source id>` replaced by the bare id of step 2. The relation
`RELATION('<id>', DUAL 'Conversations')` also adds the property "Conversations" to Reports. Keep the new
`collection://…` URL (the Conversations data source).

## 4. Inline

`notion-update-data-source` with `is_inline: true` on both data sources, so both tables show inside the hub page.

## 5. Views

`notion-create-view` on the Conversations database:

1. name `All conversations`, type `table`, configure
   `SORT BY "Last message" DESC; QUICK FILTER "Site"; QUICK FILTER "State"; QUICK FILTER "Report"`.
2. name `By site`, type `table`, configure `GROUP BY "Site"; SORT BY "Last message" DESC`.

`notion-create-view` on the Reports database: name `All reports`, type `table`, configure `SORT BY "Date" DESC`.

If the tool refuses a part of a configure string (the view language can change), create the view with the parts it
accepts and tell the user which part is missing.

## 6. Settings

Write, through the setup skill (which is the only writer of the settings file):

- `notion.conversation_reports.parent_page_url` = the parent page of step 0;
- `notion.conversation_reports.data_source` = the Reports `collection://…` URL of step 2;
- `skill_settings.active-conversations.conversations_data_source` = the Conversations `collection://…` URL of step 3;
- `notion.conversation_reports.enabled` = `true`.

Check: `python3 <active-conversations folder>/scripts/notion_report.py --check` prints `on: …` and exits 0.

## 7. Later: a new machine or cluster

The site options are fixed in Notion. When the user adds a workstation or cluster to the settings, run
`notion_report.py --schema` again and call `notion-update-data-source`:

- on the Reports data source with each statement of `reports_alter`;
- on the Conversations data source with the statement of `conversations_alter`.

Each statement lists every option, existing ones too (it replaces the list). Until then, reports leave the new site
out of the select columns and say so.
