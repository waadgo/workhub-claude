"""
Kit-wide checks for workhub-claude: things that must hold across all skills, not inside one of them.

  1. Every copy of workhub_config.py is identical to the master copy lib/workhub_config.py.
  2. The plugin and marketplace manifests are valid JSON and name the author and the license.
  3. Every skill has a SKILL.md whose frontmatter has the folder's name, a description of at most 1024
     characters, the Apache-2.0 license and the author metadata.
  4. There is no hooks/hooks.json (a Claude Code plugin would load it by itself and register the hooks a second
     time; the setup skill registers them in ~/.claude/settings.json instead).
  5. Every script carries the license line.
  6. No file holds a Notion-style id, a UUID, an e-mail address or a /home/<name>/ path (documented
     placeholders excepted), so nothing of the person who set up their own copy can slip into a commit.

Run:  python3 -m pytest tests -q        (from the repository root)

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import json
import os
import re

import pytest

KIT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKIPPED_FOLDERS = {".git", "__pycache__", ".pytest_cache"}
LICENSE_LINE = "Part of workhub-claude by Walter Adame-Gonzalez"
AUTHOR = "Walter Adame-Gonzalez"
REPOSITORY_URL = "https://github.com/waadgo/workhub-claude"


def all_files():
    """Yield the path of every file of the kit, skipping .git and caches."""
    for folder, subfolders, filenames in os.walk(KIT_ROOT):
        subfolders[:] = [name for name in subfolders if name not in SKIPPED_FOLDERS]
        for filename in filenames:
            yield os.path.join(folder, filename)


def skill_folders():
    """Return the folders under skills/ (one per skill)."""
    skills_root = os.path.join(KIT_ROOT, "skills")
    return sorted(os.path.join(skills_root, name) for name in os.listdir(skills_root)
                  if os.path.isdir(os.path.join(skills_root, name)))


def test_helper_copies_identical():
    with open(os.path.join(KIT_ROOT, "lib", "workhub_config.py"), "rb") as handle:
        master = handle.read()
    copies = [path for path in all_files() if os.path.basename(path) == "workhub_config.py"
              and os.path.dirname(path) != os.path.join(KIT_ROOT, "lib")]
    assert copies, "no copies found"
    different = []
    for path in copies:
        with open(path, "rb") as handle:
            if handle.read() != master:
                different.append(os.path.relpath(path, KIT_ROOT))
    assert not different, "copies differ from lib/workhub_config.py: " + ", ".join(different)


def test_every_skill_has_the_helper():
    missing = [os.path.basename(folder) for folder in skill_folders()
               if not os.path.isfile(os.path.join(folder, "scripts", "workhub_config.py"))]
    assert not missing, "skills without scripts/workhub_config.py: " + ", ".join(missing)


def test_manifests():
    with open(os.path.join(KIT_ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
        plugin = json.load(handle)
    assert plugin["name"] == "workhub-claude"
    assert plugin["author"]["name"] == AUTHOR
    assert plugin["license"] == "Apache-2.0"
    assert plugin["repository"] == REPOSITORY_URL
    with open(os.path.join(KIT_ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as handle:
        marketplace = json.load(handle)
    assert marketplace["owner"]["name"] == AUTHOR
    assert marketplace["plugins"][0]["name"] == plugin["name"]
    assert marketplace["plugins"][0]["version"] == plugin["version"]


def read_frontmatter(path):
    """Return the frontmatter lines of a SKILL.md (between the first two '---' lines)."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    assert lines and lines[0] == "---", path + " must start with ---"
    end = lines.index("---", 1)
    return lines[1:end]


@pytest.mark.parametrize("folder", skill_folders(), ids=os.path.basename)
def test_skill_frontmatter(folder):
    skill_file = os.path.join(folder, "SKILL.md")
    assert os.path.isfile(skill_file)
    frontmatter = read_frontmatter(skill_file)
    fields = {}
    for line in frontmatter:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip('"')
    assert fields.get("name") == os.path.basename(folder)
    assert fields.get("license") == "Apache-2.0"
    assert fields.get("author") == AUTHOR
    assert fields.get("url") == REPOSITORY_URL
    description = fields.get("description", "")
    assert 40 <= len(description) <= 1024, "description length " + str(len(description))
    # An unquoted YAML value must not contain ": " (YAML would read it as a second key and the skill would not load).
    raw_description = [line for line in frontmatter if line.startswith("description:")][0]
    is_quoted = raw_description.split(":", 1)[1].strip()[:1] in ("'", '"')
    assert is_quoted or ": " not in description, "unquoted description contains ': '"


def test_no_plugin_hooks_file():
    assert not os.path.exists(os.path.join(KIT_ROOT, "hooks", "hooks.json"))


def test_scripts_carry_license_line():
    missing = []
    for path in all_files():
        relative = os.path.relpath(path, KIT_ROOT)
        is_script = path.endswith(".py") or path.endswith(".sh")
        if not is_script or "/tests/" in "/" + relative or relative.startswith("tests" + os.sep):
            continue
        with open(path, encoding="utf-8") as handle:
            if LICENSE_LINE not in handle.read():
                missing.append(relative)
    assert not missing, "scripts without the license line: " + ", ".join(missing)


# Generic personal-data patterns (the same idea as a private leak scan, without any private word list):
#   hex32: 32 hexadecimal characters = a Notion page or database id without dashes.
#   uuid:  8-4-4-4-12 hexadecimal groups = a Notion id with dashes.
#   email: an e-mail address (name, at sign, domain).
#   home:  /home/<name>/ or /Users/<name>/ other than the documented example user "alex" and placeholders.
PATTERNS = {
    "hex32": re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])"),
    "uuid": re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "home": re.compile(r"/(?:home|Users)/(?!alex/|you/|USER/|user/|<)[A-Za-z0-9._-]+/"),
}
ALLOWED_MATCHES = {
    "0123456789abcdef0123456789abcdef",
    "00000000000000000000000000000000",
    "00000000-0000-0000-0000-000000000000",
    "01234567-89ab-cdef-0123-456789abcdef",
    "41371f09657181cfa7aaebd9631907bc",  # the page id inside the public template link (published on purpose)
}


def test_no_personal_patterns():
    findings = []
    for path in all_files():
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except UnicodeDecodeError:
            continue
        for rule, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                if match.group(0).lower() in ALLOWED_MATCHES:
                    continue
                findings.append(os.path.relpath(path, KIT_ROOT) + ": [" + rule + "] " + match.group(0))
    assert not findings, "possible personal data:\n" + "\n".join(findings[:40])
