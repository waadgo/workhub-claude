#!/usr/bin/env python3
"""Make the folder of a new status figure, and optionally start it from the newest earlier status of that name.

    python new_status_folder.py machines                   # create and print the folder
    python new_status_folder.py machines --copy-latest     # ... and copy the newest earlier status.json into it
    python new_status_folder.py machines --latest          # only print the newest existing folder of that name
    python new_status_folder.py machines --dry-run         # print what would be done, change nothing

Every status figure lives in its own folder, so the folders form a history:

    <paths.outputs_dir>/run-status-figure/<YYYY-MM-DD_HHMM>_<name>/status.json (+ status.png, status.pdf, page.json)

`paths.outputs_dir` comes from the workhub-claude settings file (default ~/workhub/outputs); the time stamp is the
current time in the owner's time zone (`owner.time_zone`, an IANA name such as "Europe/Lisbon"). <name> says what
the figure covers, for example "machines" for the machine view or a project key such as "project-a". A refreshed
figure of the same work starts from the newest earlier status.json of the same name (--copy-latest), which is then
updated from the live state; the earlier folder is never changed.

INPUTS   the settings file (read with the bundled workhub_config.py); the earlier status folders.
OUTPUTS  one new folder (and, with --copy-latest, its status.json). The folder path is printed on the last line.
         An existing status.json is never replaced unless --overwrite is given.
EXIT     0 = success; 2 = bad name, missing settings, or a file that would be replaced without --overwrite.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# workhub_config.py: the bundled copy of the kit's settings reader (same folder as this script).
from workhub_config import ConfigError, expand_path, get_setting, load_config, time_zone

ALLOWED_NAME_CHARACTERS = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def now_in_zone(zone_name):
    """The current time in the IANA zone `zone_name`, as a naive datetime (Python >= 3.9 has zoneinfo; on 3.8
    or with an unknown zone the machine's local time is used)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(zone_name)).replace(tzinfo=None)
    except Exception:                                   # ImportError (3.8) or ZoneInfoNotFoundError
        return datetime.now()


def status_root(config):
    """<paths.outputs_dir>/run-status-figure, the parent of every status folder."""
    outputs_dir = expand_path(get_setting(config, "paths.outputs_dir", default="~/workhub/outputs"))
    return Path(outputs_dir) / "run-status-figure"


def latest_folder(root, name):
    """The newest existing folder '<stamp>_<name>' that holds a status.json, or None.

    The folder names start with YYYY-MM-DD_HHMM, so sorting the names sorts them by time.
    """
    if not root.is_dir():
        return None
    suffix = "_" + name
    candidates = [folder for folder in root.iterdir()
                  if folder.is_dir() and folder.name.endswith(suffix) and (folder / "status.json").is_file()
                  and len(folder.name) == len("YYYY-MM-DD_HHMM") + len(suffix)]
    if not candidates:
        return None
    return sorted(candidates, key=lambda folder: folder.name)[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="what the figure covers: lower-case letters, digits, - and _ (e.g. machines)")
    parser.add_argument("--copy-latest", action="store_true",
                        help="copy the newest earlier status.json of this name into the new folder")
    parser.add_argument("--latest", action="store_true", help="only print the newest existing folder of this name")
    parser.add_argument("--dry-run", action="store_true", help="print what would be done; change nothing")
    parser.add_argument("--overwrite", action="store_true", help="allow replacing an existing status.json")
    args = parser.parse_args()

    if not args.name or set(args.name) - ALLOWED_NAME_CHARACTERS:
        print("the name may only hold lower-case letters, digits, - and _: " + repr(args.name), file=sys.stderr)
        return 2
    try:
        config = load_config(required=True)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2

    root = status_root(config)
    previous = latest_folder(root, args.name)
    if args.latest:
        if previous is None:
            print(f"no earlier status folder named *_{args.name} in {root}", file=sys.stderr)
            return 2
        print(previous)
        return 0

    stamp = now_in_zone(time_zone(config)).strftime("%Y-%m-%d_%H%M")
    folder = root / f"{stamp}_{args.name}"
    target = folder / "status.json"
    source = previous / "status.json" if (args.copy_latest and previous is not None) else None
    if source is not None and source.resolve() == target.resolve():
        source = None                                       # made in the same minute: nothing older to copy
    if source is not None and target.exists() and not args.overwrite:
        print(f"{target} exists; add --overwrite to replace it", file=sys.stderr)
        return 2

    prefix = "would " if args.dry_run else ""
    print(f"{prefix}create {folder}")
    if source is not None:
        print(f"{prefix}copy {source} -> {target}")
    elif args.copy_latest:
        print(f"no earlier status.json named *_{args.name}: start from assets/example_status.json")
    if not args.dry_run:
        folder.mkdir(parents=True, exist_ok=True)
        if source is not None:
            shutil.copy2(source, target)
    print(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
