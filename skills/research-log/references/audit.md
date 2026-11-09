# Auditing the Research log

Only when the user asks ("check / audit / clean my research log"). The audit changes nothing until the user has
chosen the fixes.

## 1. Read every page

Rows mode, 100 per call (`notion-query-data-sources`, `data_source_url` = `notion.research_log.data_source`), or the
All documents view in view mode, paging with `next_cursor`.

## 2. Report, without changing anything

- pages with an empty Source key, an empty Project (except weekly recaps and machine-view status pages, which cover
  several projects on purpose), or a Name not starting with a date (except weekly recaps, whose names start with
  "Weekly recap" and whose Source key starts with `weekly-recap:`);
- Source keys that Notion turned into links (`[name.md](http://name.md)`): harmless for lookups (the filter compares
  plain text) but noisy; offer to rewrite them in backticks;
- keys used by more than one page (compare normalized keys: remove the backticks and turn `[x](http://x)` back into
  `x`);
- keys whose local file is missing: look at the page's "Local original" path, or, without one, at the key's path
  under the project root when you know it (another machine's file can only be checked through an open SSH
  connection: `ssh -O check <alias>` first, then `ssh -o BatchMode=yes <alias> ls -l <path>`);
- pages without a `## Personal notes` section or with more than one `<file …>` block. This needs a fetch per page:
  do it for all pages up to about 50, otherwise for the 50 newest and say so;
- Type, Project or Clusters options that the settings no longer know (a renamed project or machine label).

## 3. Fix only what the user chose

Ask which fixes to make, make only those, and verify each with a fetch. Never delete a page and never change a
Personal notes section; a duplicate page is reported for the user to merge or delete by hand.
