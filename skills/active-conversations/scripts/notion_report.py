#!/usr/bin/env python3
"""
notion_report.py: turn conversations.json (from collect_conversations.py) into one report for the user's Notion
"Conversation reports": a page in its Reports database, one row per conversation in its Conversations database, and a
view of those rows inside the report page. The script only WRITES JSON FILES; Claude passes them to the Notion tools.

PURPOSE
    Each request to the active-conversations skill becomes one snapshot in Notion that the user can sort and filter
    later (which conversation was open where, in which folder, doing what). This script builds the exact arguments of
    the Notion calls, so that Claude copies them unchanged instead of composing them by hand.

MODES
    --check     Are Notion reports on? Prints "on: ..." and exits 0, or "off: <why>" and exits 3. Off means the skill
                answers in the chat only. Reports are on when notion.conversation_reports.enabled is true AND both data
                sources are set (notion.conversation_reports.data_source and
                skill_settings.active-conversations.conversations_data_source).
    --schema    Prints, as JSON, the statements that create both databases and the ALTER statements that bring their
                site options up to date with the settings (used by references/build_database.md and when Notion
                refuses an unknown site). Needs no data source.
    (default)   First run: writes OUTDIR/report.json, the arguments of notion-create-pages for the report page.
                Second run, with --report-url (the URL or id of the created report page): also writes OUTDIR/rows.json
                (notion-create-pages for the conversation rows, linked to the report) and OUTDIR/view.json
                (notion-create-view for the table inside the report page).

INPUTS
    - conversations.json written by collect_conversations.py (schema 2).
    - --descriptions: JSON object, session id (or its first 8 characters) -> one sentence on what is going on.
    - The workhub-claude settings (~/.config/workhub-claude/config.json or $WORKHUB_CLAUDE_CONFIG) through
      workhub_config.py in this folder. Keys used: notion.conversation_reports.enabled / .data_source,
      skill_settings.active-conversations.conversations_data_source / .home_site_label, cluster_login.enabled, and
      the machines (through build_sites() of collect_conversations.py) for the site options.

OUTPUTS
    report.json, rows.json, view.json in OUTDIR (default: the current folder); the schema JSON on standard output.
    Nothing is sent to Notion by this script.

EXAMPLES
    python3 notion_report.py --check
    python3 notion_report.py --schema
    python3 notion_report.py conversations.json --descriptions descriptions.json -o OUTDIR
    python3 notion_report.py conversations.json --descriptions descriptions.json -o OUTDIR --report-url URL

DEPENDENCIES
    Python >= 3.8 standard library (zoneinfo from 3.9 for local times; UTC with a label otherwise), plus
    workhub_config.py and collect_conversations.py from this folder.

EXIT STATUS
    0 = done; 2 = settings missing or unreadable; 3 = Notion reports are off (answer in the chat only).

LICENSE
    Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

# ---------------------------------------------------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------------------------------------------------
# argparse: command line. datetime: local clock times with their UTC offset. json: every file here. os: paths.
# re: Markdown escaping and page ids. sys: exit codes and the import path of the helper modules.
import argparse
import datetime
import json
import os
import re
import sys

# ---------------------------------------------------------------------------------------------------------------------
# Constants: the shape of the two Notion databases (also printed by --schema, so the schema has one source)
# ---------------------------------------------------------------------------------------------------------------------
SKILL_NAME = "active-conversations"

# Exit status for "Notion reports are off": the SKILL.md tells Claude to answer in the chat only.
EXIT_REPORTS_OFF = 3

# Collector status -> (page icon, State option in Notion, words for the summary line).
STATES = {
    "busy": ("🟢", "🟢 Working", "working"),
    "background": ("⏳", "⏳ Background job", "background job"),
    "idle": ("💬", "💬 Waiting for you", "waiting for you"),
    "closed": ("⚪", "⚪ Closed", "closed"),
}
# The colors of the State options, in the order of STATES.
STATE_COLORS = {"busy": "green", "background": "yellow", "idle": "blue", "closed": "gray"}

# The Kind options of a report and their colors.
KIND_OPTIONS = [("Open now", "green"), ("Last N", "blue"), ("Search", "purple")]

# Colors given to the site options, in the order of the sites; repeated when there are more than ten sites.
# These are the ten color names Notion accepts for select options.
SITE_COLORS = ["blue", "orange", "green", "purple", "pink", "brown", "yellow", "red", "gray", "default"]

# Reports database: property name -> Notion SQL type ("<sites>" is replaced by the site options).
REPORT_PROPERTIES = [
    ("Report", "TITLE"),
    ("Date", "DATE"),
    ("Kind", "SELECT(<kinds>)"),
    ("Query", "RICH_TEXT"),
    ("Requested from", "RICH_TEXT"),
    ("Sites checked", "MULTI_SELECT(<sites>)"),
    ("Not checked", "MULTI_SELECT(<sites>)"),
    ("Found", "NUMBER"),
    ("Open", "NUMBER"),
    ("Working", "NUMBER"),
    ("Waiting for you", "NUMBER"),
]

# Conversations database: property name -> Notion SQL type. The relation names the Reports data source, which exists
# only after the Reports database was created ("<reports data source id>" is filled in by the setup skill).
CONVERSATION_PROPERTIES = [
    ("Conversation", "TITLE"),
    ("State", "SELECT(<states>)"),
    ("Site", "SELECT(<sites>)"),
    ("Machine", "RICH_TEXT"),
    ("Working directory", "RICH_TEXT"),
    ("Opened in", "RICH_TEXT"),
    ("Started", "DATE"),
    ("Last message", "DATE"),
    ("What's going on", "RICH_TEXT"),
    ("Requests", "NUMBER"),
    ("Session ID", "RICH_TEXT"),
    ("Resume", "RICH_TEXT"),
    ("Report", "RELATION('<reports data source id>', DUAL 'Conversations')"),
]

# The columns the per-report table view shows, in this order.
ROW_COLUMNS = ["Conversation", "State", "Site", "Machine", "Working directory", "Started", "Last message",
               "What's going on"]

# Characters with a meaning in Notion-flavored Markdown; esc() puts a backslash before each.
MD_CHARS = re.compile(r"([\\*~`$\[\]<>{}|^])")


# ---------------------------------------------------------------------------------------------------------------------
# Settings and helper modules
# ---------------------------------------------------------------------------------------------------------------------

def load_helpers():
    """Import workhub_config.py and collect_conversations.py from this folder; return (wc, cc, config).

    wc.load_config() reads the settings file; collect_conversations.build_sites() builds the site list from it, so
    the site options here always match what the collector reports. Exits 2 with the setup hint when the settings
    file is missing.
    """
    script_folder = os.path.dirname(os.path.abspath(__file__))
    if script_folder not in sys.path:
        sys.path.insert(0, script_folder)
    import workhub_config
    import collect_conversations
    try:
        config = workhub_config.load_config()
    except workhub_config.ConfigError as error:
        sys.stderr.write(str(error) + "\n")
        sys.exit(2)
    return workhub_config, collect_conversations, config


def reports_status(wc, config):
    """(on, reason, reports data source id, conversations data source id) from the settings.

    Both ids are returned bare (without "collection://"), as notion-create-pages wants them.
    """
    enabled = bool(wc.get_setting(config, "notion.conversation_reports.enabled", default=False))
    reports_id = wc.data_source_id(wc.get_setting(config, "notion.conversation_reports.data_source", default=""))
    conversations_id = wc.data_source_id(wc.skill_setting(config, SKILL_NAME, "conversations_data_source", ""))
    if not enabled:
        return False, "notion.conversation_reports.enabled is false", reports_id, conversations_id
    if not reports_id:
        return False, "notion.conversation_reports.data_source is empty", reports_id, conversations_id
    if not conversations_id:
        return (False, "skill_settings.%s.conversations_data_source is empty" % SKILL_NAME,
                reports_id, conversations_id)
    return True, "", reports_id, conversations_id


def site_option_labels(wc, cc, config):
    """The site names used as select options: every site of build_sites() plus the home site's label.

    The home label is added when this machine is a cluster (whose home site is the cluster itself), so that a report
    made there can list the workstations under "Not checked".
    """
    labels = [site["label"] for site in cc.build_sites(wc, config)]
    home_label = str(wc.skill_setting(config, SKILL_NAME, "home_site_label", "Workstations"))
    if home_label not in labels:
        labels.insert(0, home_label)
    return labels


# ---------------------------------------------------------------------------------------------------------------------
# Schema statements (--schema)
# ---------------------------------------------------------------------------------------------------------------------

def quote_option(name):
    """'name' for a Notion SQL option; a single quote inside the name is doubled (SQL string rule)."""
    return "'%s'" % str(name).replace("'", "''")


def options_text(pairs):
    """[(name, color), ...] -> "'a':blue, 'b':green" (the inside of SELECT(...) or MULTI_SELECT(...))."""
    return ", ".join("%s:%s" % (quote_option(name), color) for name, color in pairs)


def site_option_pairs(labels):
    """Site labels -> [(label, color)], colors taken in turn from SITE_COLORS."""
    return [(label, SITE_COLORS[index % len(SITE_COLORS)]) for index, label in enumerate(labels)]


def create_statement(properties, replacements):
    """CREATE TABLE ("Name" TYPE, ...) with the <placeholders> of the types replaced."""
    columns = []
    for name, sql_type in properties:
        for placeholder, value in replacements.items():
            sql_type = sql_type.replace(placeholder, value)
        columns.append('"%s" %s' % (name, sql_type))
    return "CREATE TABLE (%s)" % ", ".join(columns)


def schema_statements(labels):
    """Everything references/build_database.md needs, as a dict (printed as JSON by --schema).

    Keys: site_options (the labels), reports_create, conversations_create (with the placeholder
    '<reports data source id>' to replace by the bare id of the Reports data source), reports_alter and
    conversations_alter (statements for notion-update-data-source that set the site options; every option is listed,
    because the statement replaces the whole list).
    """
    sites = options_text(site_option_pairs(labels))
    kinds = options_text(KIND_OPTIONS)
    states = options_text([(STATES[key][1], STATE_COLORS[key]) for key in STATES])
    return {
        "site_options": labels,
        "reports_create": create_statement(REPORT_PROPERTIES, {"<sites>": sites, "<kinds>": kinds}),
        "conversations_create": create_statement(CONVERSATION_PROPERTIES, {"<sites>": sites, "<states>": states}),
        "reports_alter": ['ALTER COLUMN "Sites checked" SET MULTI_SELECT(%s)' % sites,
                          'ALTER COLUMN "Not checked" SET MULTI_SELECT(%s)' % sites],
        "conversations_alter": ['ALTER COLUMN "Site" SET SELECT(%s)' % sites],
    }


# ---------------------------------------------------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------------------------------------------------

def esc(text):
    """Plain text -> safe Notion Markdown: white space squeezed, special characters escaped with a backslash."""
    return MD_CHARS.sub(r"\\\1", re.sub(r"\s+", " ", text or "").strip())


def code(text):
    """`text` as inline code (a back-quote inside becomes a single quote)."""
    return "`%s`" % (text or "").replace("`", "'")


def gray(text):
    """Gray text in Notion Markdown."""
    return '<span color="gray">%s</span>' % text


def local_zone(zone_name):
    """(tzinfo, IANA name used, label) for the time zone of conversations.json; UTC when it cannot be loaded."""
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(zone_name), zone_name, "%s time" % zone_name
    except Exception:  # noqa: BLE001 - no zoneinfo module (Python 3.8) or no data for the zone
        return datetime.timezone.utc, "UTC", "UTC (time zone %s not available)" % zone_name


def iso(epoch, zone):
    """Seconds since 1970 -> local time with its UTC offset, e.g. 2026-01-08T15:43:12+00:00 (Notion's date format)."""
    return datetime.datetime.fromtimestamp(epoch, tz=zone).replace(microsecond=0).isoformat()


def when(epoch, zone):
    """Seconds since 1970 -> "Jan 8, 15:43" in `zone`; "?" for None."""
    if not epoch:
        return "?"
    moment = datetime.datetime.fromtimestamp(epoch, tz=zone)
    return "%s %d, %s" % (moment.strftime("%b"), moment.day, moment.strftime("%H:%M"))


def page_id(url_or_id):
    """The page id inside a Notion URL or id, in the 8-4-4-4-12 form.

    The pattern takes 32 hexadecimal digits in groups of 8, 4, 4, 4 and 12, each group optionally followed by a
    hyphen ("-?"), so both "https://www.notion.so/Title-<32 digits>" and "<8>-<4>-<4>-<4>-<12>" work.
    """
    m = re.search(r"([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{12})", url_or_id or "")
    if not m:
        raise SystemExit("No Notion page id in %r" % url_or_id)
    return "-".join(m.groups())


def where_text(conv):
    """The app(s) a conversation is open in, by machine, as plain text ("VS Code + tmux session x on ws01")."""
    by_host = {}
    for p in conv["processes"]:
        if p["app"] == "tmux" and p.get("tmux"):
            app = "tmux session %s" % p["tmux"].split(":")[0]
        else:
            app = p["app"]
        apps = by_host.setdefault(p["host"], [])
        if app not in apps:
            apps.append(app)
    return "; ".join("%s on %s" % (" + ".join(apps), host) for host, apps in by_host.items()) or "not open"


def query_words(q):
    """(Kind option, query text) of a run: ("Open now", "open now"), ("Search", "last 5 matching “x”")."""
    if q.get("mode") != "last":
        return "Open now", "open now"
    text = "last %d" % q["last"]
    if q.get("match"):
        text += " matching “%s”" % q["match"]
    if q.get("since_hours"):
        text += ", messages in the last %g h" % q["since_hours"]
    return ("Search" if q.get("match") else "Last N"), text


def counts_text(convs):
    """"🟢 2 working · 💬 3 waiting for you" for a list of conversations."""
    counts = {}
    for c in convs:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    parts = ["%s %d %s" % (STATES[k][0], counts[k], STATES[k][2]) for k in ("busy", "background", "idle", "closed")
             if counts.get(k)]
    parts += ["%d %s" % (n, k) for k, n in counts.items() if k not in STATES]
    return " · ".join(parts)


# ---------------------------------------------------------------------------------------------------------------------
# The report page
# ---------------------------------------------------------------------------------------------------------------------

def site_notes(site, listed, kind, zone):
    """The Notes cell of one reached site in the Sites table: empty tabs, unverified entries, nothing found."""
    notes = []
    if site.get("matched") is not None and site["matched"] > len(listed):
        notes.append("%d found here, %d among the newest listed" % (site["matched"], len(listed)))
    if site.get("empty"):
        groups = {}
        for e in site["empty"]:
            folder_name = os.path.basename(e["cwd"].rstrip("/")) or e["cwd"]
            groups.setdefault("%s on %s" % (e["app"], e["host"]), []).append(folder_name)
        notes.append("%d tab%s with no message yet (%s)" % (
            len(site["empty"]), "" if len(site["empty"]) == 1 else "s",
            "; ".join("%s: %s" % (k, ", ".join(sorted(v))) for k, v in groups.items())))
    for u in site.get("unverified", []):
        notes.append("could not check machine-id %s: “%s”, last message %s" % (
            u["machine_id"][:8], u["title"], when(u["last_message"], zone)))
    if not listed and not notes:
        if kind == "Open now":
            notes.append("no Claude conversation open")
        elif site.get("newest_message"):
            notes.append("none among the newest; the newest here is from %s" % when(site["newest_message"], zone))
        else:
            notes.append("none found")
    return notes


def report_body(data, convs, kind, query, cc, cluster_login_enabled, zone, zone_name, zone_label):
    """The Markdown body of the report page: summary callout, Sites table, legend, and the "Conversations" heading
    under which notion-create-view later puts the table of this report's rows."""
    req = data["requested_from"]
    moment = datetime.datetime.fromtimestamp(data["generated_at"], tz=zone)
    mention = '<mention-date start="%s" startTime="%s" timeZone="%s"/>' % (
        moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M"), zone_name)
    plural = "" if len(convs) == 1 else "s"
    if kind == "Open now":
        heading, what = "Open now", "**%d conversation%s open**" % (len(convs), plural)
    else:
        heading = re.sub(r"^Last (\d+)", r"Last \1 conversations", esc(query[0].upper() + query[1:]))
        what = "**%d conversation%s found**, newest first" % (len(convs), plural)
    out = ['<callout icon="%s" color="gray_bg">' % ("🧭" if kind == "Open now" else "🔎"),
           "\t**%s**, requested %s from %s (%s)." % (heading, mention, code(req["system"]),
                                                     esc(req.get("site_label", ""))),
           "\t%s%s" % (what, (": " + counts_text(convs)) if convs else "."),
           "\tThey are listed in the table at the end of this page: sort or filter it by Site, State or "
           "Last message. Open a row for its Session ID and the command to resume it.",
           "</callout>",
           "## Sites",
           '<table fit-page-width="true" header-row="true">',
           "\t<tr>"] + ["\t\t<td>%s</td>" % h for h in ("Site", "Checked", "Found", "Machines checked", "Notes")]
    out.append("\t</tr>")
    for s in data["sites"]:
        if not s.get("reached"):
            # The hint names the command that opens the missing shared connection (reach_hint() of the collector).
            escaped_site = dict(s, reason=esc(s.get("reason", "")))
            hint = cc.reach_hint(escaped_site, req["system"], cluster_login_enabled, code)
            cells = [esc(s["label"]), "❌ no", "–", "", hint]
        else:
            listed = [c for c in convs if c.get("_site") == s["site"]]
            machines = sorted((n for n in s.get("nodes", []) if n.get("ok")), key=lambda n: n["name"])
            machine_parts = []
            for n in machines:
                open_here = sum(1 for c in listed if c["status"] != "closed"
                                and any(p["host"] == n["name"] for p in c["processes"]))
                machine_parts.append("%s (%d open)" % (n["name"], open_here) if open_here else n["name"])
            skipped = [n["name"] for n in s.get("nodes", []) if n.get("skipped")]
            notes = site_notes(s, listed, kind, zone)
            if skipped:
                notes.append("not checked (no open shared SSH connection): %s" % ", ".join(skipped))
            checked_at = when(s["checked_at"], zone).split(", ")[-1]
            cells = [esc(s["label"]), "✅ %s" % checked_at, str(len(listed)),
                     esc(", ".join(machine_parts)) or gray("none needed"), esc("; ".join(notes))]
        out += ["\t<tr>"] + ["\t\t<td>%s</td>" % c for c in cells] + ["\t</tr>"]
    out.append("</table>")
    out.append(gray(esc("🟢 Claude is working · ⏳ idle while a background command it started still runs · "
                        "💬 idle, waiting for your next message · ⚪ closed (no window has it open). Times are "
                        "%s. To reopen a conversation, go to its machine and folder and pick it by name in the "
                        "Claude panel of VS Code, or run its Resume command in a terminal there." % zone_label)))
    out.append(gray("Made by the active-conversations skill: %s" % code("%s:%s" % (req["system"], req["skill_dir"]))))
    out.append("## Conversations")
    return "\n".join(out) + "\n"


def description_of(descriptions, conv):
    """The one-line description of a conversation, by full session id or its first 8 characters ("" if none)."""
    return descriptions.get(conv["session_id"]) or descriptions.get(conv["session_id"][:8]) or ""


# ---------------------------------------------------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------------------------------------------------

def parse_arguments():
    """Define and read the command-line options (see MODES in the module docstring)."""
    ap = argparse.ArgumentParser(description="Turn conversations.json into the arguments of the Notion calls that "
                                             "add one report to the user's Conversation reports.")
    ap.add_argument("conversations", nargs="?", help="JSON written by collect_conversations.py")
    ap.add_argument("--descriptions", help="JSON: session id (or its first 8 characters) -> one-line description")
    ap.add_argument("--report-url", help="URL or id of the report page, once created")
    ap.add_argument("-o", "--outdir", default=".", help="folder for report.json, rows.json, view.json")
    ap.add_argument("--check", action="store_true", help="say whether Notion reports are on (exit 0) or off (exit 3)")
    ap.add_argument("--schema", action="store_true", help="print the statements that create and update the databases")
    args = ap.parse_args()
    if not (args.check or args.schema or args.conversations):
        ap.error("give conversations.json, --check or --schema")
    return args


def main():
    """Run the chosen mode (see the module docstring)."""
    args = parse_arguments()
    wc, cc, config = load_helpers()
    on, reason, reports_id, conversations_id = reports_status(wc, config)

    if args.schema:
        print(json.dumps(schema_statements(site_option_labels(wc, cc, config)), indent=1, ensure_ascii=False))
        return 0
    if args.check:
        if on:
            print("on: reports data source %s, conversations data source %s" % (reports_id, conversations_id))
            return 0
        print("off: %s. Answer in the chat only (references/build_database.md sets the reports up)." % reason)
        return EXIT_REPORTS_OFF
    if not on:
        sys.stderr.write("Notion reports are off (%s): answer in the chat only.\n" % reason)
        return EXIT_REPORTS_OFF

    with open(args.conversations, encoding="utf-8") as f:
        data = json.load(f)
    descriptions = {}
    if args.descriptions:
        with open(args.descriptions, encoding="utf-8") as f:
            descriptions = json.load(f)
    zone, zone_name, zone_label = local_zone(data.get("timezone") or wc.time_zone(config))
    cluster_login_enabled = bool(wc.get_setting(config, "cluster_login.enabled", default=False))
    options = site_option_labels(wc, cc, config)
    kind, query = query_words(data.get("query", {}))
    req = data["requested_from"]

    # Every conversation of the reached sites, newest message first, tagged with its site key and label.
    convs = []
    for s in data["sites"]:
        if s.get("reached"):
            for c in s["conversations"]:
                c["_site"], c["_label"] = s["site"], s["label"]
                convs.append(c)
    convs.sort(key=lambda c: -(c["last_message"] or 0))
    reached = [s["label"] for s in data["sites"] if s.get("reached")]
    missed = [s["label"] for s in data["sites"] if not s.get("reached")]
    # Sites the settings know but this run could not even try (for example the workstations, asked from a cluster).
    looked_at = set(s["label"] for s in data["sites"])
    missed += [label for label in options if label not in looked_at]

    # The report page. Select values that are not options would be refused by Notion: they are left out here
    # (run --schema and the ALTER statements to add a new site).
    stamp = datetime.datetime.fromtimestamp(data["generated_at"], tz=zone).strftime("%Y-%m-%d %H:%M")
    title = "%s · %s · %s" % (stamp, "Open now" if kind == "Open now" else query[0].upper() + query[1:],
                             req["system"])
    report = {
        "parent": {"type": "data_source_id", "data_source_id": reports_id},
        "pages": [{
            "icon": "🧭" if kind == "Open now" else "🔎",
            "properties": {
                "Report": title,
                "date:Date:start": iso(data["generated_at"], zone), "date:Date:is_datetime": 1,
                "Kind": kind, "Query": query, "Requested from": req["system"],
                "Sites checked": [x for x in reached if x in options],
                "Not checked": [x for x in missed if x in options],
                "Found": len(convs),
                "Open": sum(1 for c in convs if c["status"] != "closed"),
                "Working": sum(1 for c in convs if c["status"] == "busy"),
                "Waiting for you": sum(1 for c in convs if c["status"] == "idle"),
            },
            "content": report_body(data, convs, kind, query, cc, cluster_login_enabled, zone, zone_name, zone_label),
        }],
    }
    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    print("Wrote %s: report “%s”, %d conversations (%s)" % (
        os.path.abspath(os.path.join(args.outdir, "report.json")), title, len(convs), counts_text(convs) or "none"))
    unknown = sorted(set(reached + missed) - set(options))
    if unknown:
        print("Not a site option in Notion yet (left out): %s. Run --schema and apply its ALTER statements."
              % ", ".join(unknown))

    missing = [c["session_id"][:8] for c in convs if not description_of(descriptions, c)]
    if missing:
        print("No description for: %s" % ", ".join(missing))
    if not args.report_url:
        print("Next: create the report page from report.json, then run again with --report-url <its URL>.")
        return 0

    # The rows, linked to the report page, and the view that shows them inside it.
    rid = page_id(args.report_url)
    rows = []
    for c in convs:
        st = STATES.get(c["status"])
        props = {
            "Conversation": c["title"],
            "State": st[1] if st else None,
            "Site": c["_label"] if c["_label"] in options else None,
            "Machine": ", ".join(sorted(set(p["host"] for p in c["processes"]))),
            "Working directory": "%s:%s" % (c["system"], c["cwd"]),
            "Opened in": where_text(c),
            "What's going on": description_of(descriptions, c) or "(no description written)",
            "Requests": c["prompts"],
            "Session ID": c["session_id"],
            "Resume": "cd %s && claude --resume %s" % (c["cwd"], c["session_id"]),
            "Report": [rid],
        }
        for name, epoch in (("Started", c["started"]), ("Last message", c["last_message"])):
            if epoch:
                props["date:%s:start" % name] = iso(epoch, zone)
                props["date:%s:is_datetime" % name] = 1
        rows.append({"icon": st[0] if st else "❔", "properties": props})
    with open(os.path.join(args.outdir, "rows.json"), "w", encoding="utf-8") as f:
        json.dump({"parent": {"type": "data_source_id", "data_source_id": conversations_id},
                   "pages": rows}, f, indent=1, ensure_ascii=False)
    view = {"parent_page_id": rid, "data_source_id": conversations_id,
            "name": "Conversations in this report", "type": "table",
            "configure": 'FILTER "Report" = "%s"; SORT BY "Last message" DESC; SHOW %s; WRAP CELLS true' % (
                rid, ", ".join('"%s"' % col for col in ROW_COLUMNS))}
    with open(os.path.join(args.outdir, "view.json"), "w", encoding="utf-8") as f:
        json.dump(view, f, indent=1, ensure_ascii=False)
    print("Wrote rows.json (%d rows, linked to report %s) and view.json" % (len(rows), rid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
