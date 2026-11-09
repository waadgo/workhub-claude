"""
Tests for configure.py (work-hub-setup). Synthetic settings only; every file lives in pytest's tmp_path.

Run:  python3 -m pytest skills/work-hub-setup/tests -q      (from the repository root)

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import json
import os
import subprocess
import sys

import pytest

SCRIPT_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
CONFIGURE = os.path.join(SCRIPT_FOLDER, "configure.py")
sys.path.insert(0, SCRIPT_FOLDER)
import configure  # noqa: E402

GOOD_ANSWERS = {
    "owner": {"name": "Alex", "time_zone": "Europe/Lisbon"},
    "notion": {
        "hub_parent_page_url": "https://www.notion.so/Home-0123456789abcdef0123456789abcdef",
        "task_id_prefix": "tk",
        "projects": ["Thesis", "Thesis", "Teaching", ""],
    },
    "workflow": {"orchestrator": {"enabled": True, "folder": "ORCH_FOLDER"}},
    "machines": {"workstations": [{"name": "WS01", "ssh_alias": "", "usage_windows": [
        {"days": "Mon-Fri", "from": "09:00", "to": "17:00", "rule": "blocked", "reason": "shared"}]}]},
}


def run_configure(tmp_path, *arguments):
    """Run configure.py as a program with a private settings file and home folder; return the result."""
    environment = dict(os.environ)
    environment["WORKHUB_CLAUDE_CONFIG"] = str(tmp_path / "config.json")
    environment["HOME"] = str(tmp_path / "home")
    return subprocess.run([sys.executable, CONFIGURE] + list(arguments), capture_output=True, text=True,
                          env=environment)


def write_answers(tmp_path, answers):
    """Write an answers file (the orchestrator folder placeholder is replaced by a tmp folder)."""
    text = json.dumps(answers).replace("ORCH_FOLDER", str(tmp_path / "orchestrator"))
    path = tmp_path / "answers.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_shape_matches_example():
    """default_config() and config/config.example.json must have the same keys at every level."""
    example_path = os.path.join(SCRIPT_FOLDER, "..", "..", "..", "config", "config.example.json")
    with open(example_path, encoding="utf-8") as handle:
        example = json.load(handle)

    def key_paths(value, prefix=""):
        found = set()
        if isinstance(value, dict):
            for key, inner in value.items():
                if prefix == "" and key == "skill_settings":
                    found.add("skill_settings")
                    continue
                found.add(prefix + key)
                found |= key_paths(inner, prefix + key + ".")
        return found

    assert key_paths(configure.default_config()) == key_paths(example)


def test_write_dry_run_then_apply(tmp_path):
    answers = write_answers(tmp_path, GOOD_ANSWERS)
    dry = run_configure(tmp_path, "write", "--answers", str(answers))
    assert dry.returncode == 0, dry.stderr
    assert "(dry run)" in dry.stdout
    assert not (tmp_path / "config.json").exists()

    applied = run_configure(tmp_path, "write", "--answers", str(answers), "--apply")
    assert applied.returncode == 0, applied.stderr
    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert oct(os.stat(tmp_path / "config.json").st_mode & 0o777) == "0o600"
    # normalize_settings: capital prefix, duplicates and empty names dropped, Miscellaneous added last.
    assert written["notion"]["task_id_prefix"] == "TK"
    assert written["notion"]["projects"] == ["Thesis", "Teaching", "Miscellaneous"]
    assert written["machines"]["workstations"][0]["name"] == "ws01"
    # Defaults are kept for keys the answers did not give.
    assert written["workflow"]["now_limits"] == {"max_rows": 7, "max_in_progress": 3}


def test_write_rejects_bad_answers(tmp_path):
    bad = json.loads(json.dumps(GOOD_ANSWERS))
    bad["owner"]["time_zone"] = "Mars/Olympus"
    bad["notion"]["task_id_prefix"] = "T-K"
    bad["notion"]["hub_parent_page_url"] = "https://example.org/no-id-here"
    result = run_configure(tmp_path, "write", "--answers", str(write_answers(tmp_path, bad)), "--apply")
    assert result.returncode == 1
    if sys.version_info >= (3, 9):
        assert "owner.time_zone" in result.stderr
    assert "task_id_prefix" in result.stderr
    assert "Notion page id" in result.stderr
    assert not (tmp_path / "config.json").exists()


def test_set_value(tmp_path):
    run_configure(tmp_path, "write", "--answers", str(write_answers(tmp_path, GOOD_ANSWERS)), "--apply")
    dry = run_configure(tmp_path, "set", "notion.views.now", "view://abc")
    assert dry.returncode == 0 and "(dry run)" in dry.stdout
    applied = run_configure(tmp_path, "set", "notion.views.now", "view://abc", "--apply")
    assert applied.returncode == 0, applied.stderr
    written = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert written["notion"]["views"]["now"] == "view://abc"
    as_json = run_configure(tmp_path, "set", "workflow.max_active_conversations", "5", "--json", "--apply")
    assert as_json.returncode == 0
    assert json.loads((tmp_path / "config.json").read_text())["workflow"]["max_active_conversations"] == 5


def test_render_rules_is_idempotent_and_keeps_other_text(tmp_path):
    run_configure(tmp_path, "write", "--answers", str(write_answers(tmp_path, GOOD_ANSWERS)), "--apply")
    target = tmp_path / "CLAUDE.md"
    target.write_text("# My own rules\n\nKeep this line.\n", encoding="utf-8")

    first = run_configure(tmp_path, "render-rules", "--target", str(target), "--apply")
    assert first.returncode == 0, first.stderr
    text_once = target.read_text(encoding="utf-8")
    assert text_once.startswith("# My own rules\n\nKeep this line.\n")
    assert configure.RULES_END in text_once
    assert "{{" not in text_once
    assert "TK-42" in text_once
    # The orchestrator is enabled in GOOD_ANSWERS, so its section is kept.
    assert "The orchestrator [ORCH]" in text_once

    second = run_configure(tmp_path, "render-rules", "--target", str(target), "--apply")
    assert "nothing to change" in second.stdout
    assert target.read_text(encoding="utf-8") == text_once

    removed = run_configure(tmp_path, "remove-rules", "--target", str(target), "--apply")
    assert removed.returncode == 0
    assert target.read_text(encoding="utf-8").rstrip("\n") == "# My own rules\n\nKeep this line."


def test_conditional_block_dropped_when_setting_false():
    config = configure.merge_settings(configure.default_config(), {"owner": {"name": "Alex"}})
    text = "a\n<!-- if workflow.orchestrator.enabled -->\nORCH ONLY\n<!-- endif workflow.orchestrator.enabled -->\nb {{task_id_prefix}}\n"
    assert configure.render_template(text, config) == "a\nb TK\n"


def test_unknown_placeholder_is_refused():
    with pytest.raises(ValueError):
        configure.render_template("{{no_such_value}}", configure.default_config())


def test_init_orchestrator_never_overwrites(tmp_path):
    run_configure(tmp_path, "write", "--answers", str(write_answers(tmp_path, GOOD_ANSWERS)), "--apply")
    first = run_configure(tmp_path, "init-orchestrator", "--apply")
    assert first.returncode == 0, first.stderr
    now_file = tmp_path / "orchestrator" / "NOW.md"
    assert now_file.is_file()
    assert "{{" not in now_file.read_text(encoding="utf-8")
    now_file.write_text("my edits\n", encoding="utf-8")
    second = run_configure(tmp_path, "init-orchestrator", "--apply")
    assert "kept (exists)" in second.stdout
    assert now_file.read_text(encoding="utf-8") == "my edits\n"


def test_check_days():
    assert configure.check_days("Mon-Fri")
    assert configure.check_days("Mon,Wed,Fri")
    assert not configure.check_days("weekdays")
    assert not configure.check_days("")


def test_cluster_hops_and_usage_rules_are_checked():
    config = {"machines": {
        "workstations": [{"name": "ws01", "usage_windows": [
            {"days": "Mon-Fri", "from": "09:00", "to": "17:00", "rule": "light"}]}],
        "clusters": [{"name": "mycluster", "ssh_alias": "mycluster", "hostname": "login.example.org", "user": "alex"}],
        "cluster_hops": [{"name": "mycluster-gpu", "through": "mycluster", "login_node": "gpu-login1"},
                         {"name": "lost-gpu", "through": "nowhere", "login_node": ""}]}}
    problems = []
    configure.check_machines(config, problems)
    text = "\n".join(problems)
    assert "rule should be blocked" in text
    assert "mycluster-gpu" not in text
    assert "lost-gpu: 'through'" in text and "lost-gpu: 'login_node' is empty" in text


def test_detect_runs(tmp_path):
    result = run_configure(tmp_path, "detect")
    assert result.returncode == 0, result.stderr
    facts = json.loads(result.stdout)
    for key in ("host_name", "time_zone", "cpus", "home_is_shared", "suggested_socket_dir"):
        assert key in facts
