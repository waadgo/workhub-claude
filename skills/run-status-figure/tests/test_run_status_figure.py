"""Tests of the run-status-figure skill: parsing, labels, folders, the Notion page and the example rendering.

Run:   python3 -m pytest <this skill>/tests -q

Every test uses a temporary settings file (environment variable WORKHUB_CLAUDE_CONFIG) and synthetic data only;
nothing outside pytest's temporary folders is read or written, and no machine is contacted. The rendering test is
skipped when matplotlib is missing.

LICENSE  Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

# The scripts are plain files in ../scripts (not a package): put that folder first on the import path.
SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import collect_env  # noqa: E402

EXAMPLE = SKILL_DIR / "assets" / "example_status.json"


def write_settings(folder, research_log=None, labels=None):
    """Write a small settings file into `folder` and return its path (the kit's format, fictional values)."""
    settings = {
        "schema_version": 1,
        "owner": {"name": "Alex", "time_zone": "Europe/Lisbon"},
        "notion": {
            "projects": ["Project A", "Miscellaneous"],
            "research_log": research_log if research_log is not None else {
                "enabled": True, "parent_page_url": "",
                "data_source": "collection://01234567-89ab-cdef-0123-456789abcdef"},
        },
        "machines": {
            "workstations": [{"name": "ws01", "ssh_alias": ""}, {"name": "ws02", "ssh_alias": "ws02"}],
            "clusters": [{"name": "mycluster", "ssh_alias": "mycluster", "scheduler": "slurm"}],
        },
        "paths": {"outputs_dir": str(folder / "outputs"), "state_dir": str(folder / "state")},
        "skill_settings": {"research-log": {"machine_labels": labels or {}}},
    }
    path = folder / "config.json"
    path.write_text(json.dumps(settings))
    return path


@pytest.fixture
def settings(tmp_path, monkeypatch):
    path = write_settings(tmp_path)
    monkeypatch.setenv("WORKHUB_CLAUDE_CONFIG", str(path))
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------------------------------------
# collect_env.py: pure parsing functions
# ---------------------------------------------------------------------------------------------------------

def test_parse_sections_and_gpus():
    output = "##gpu\nNVIDIA GeForce RTX 4090, 24564, 1200, 35\n##panes\n##end\n"
    sections = collect_env.parse_sections(output)
    assert set(sections) == {"gpu", "panes", "end"}
    gpus = collect_env.parse_gpus(sections["gpu"])
    assert gpus == [{"name": "RTX 4090", "memory_total_gb": 24.0, "memory_used_gb": 1.2, "utilization_percent": 35}]
    assert collect_env.gpu_description(gpus) == "RTX 4090 (24.0 GB)"
    # An error message (a login node without a GPU) is not a GPU row.
    assert collect_env.parse_gpus(["NVIDIA-SMI has failed because it couldn't communicate"]) == []
    assert collect_env.gpu_description([]) == ""


def test_tmux_states_running_idle():
    panes = ["train|100", "shell_only|200", "two panes|300", "two panes|400"]
    ps = [
        "100 1 bash", "101 100 python3",          # train: python below the shell -> running
        "200 1 -bash",                            # shell_only: nothing below the login shell -> idle
        "300 1 zsh", "400 1 zsh", "401 400 /usr/bin/bash", "402 401 rsync",   # rsync two levels down
    ]
    states = collect_env.tmux_states(panes, ps)
    assert states == {"train": "running", "shell_only": "idle", "two panes": "running"}
    sessions = collect_env.tmux_sessions(["train", "gone"], "two", states)
    assert [(s["name"], s["state"]) for s in sessions] == [("train", "running"), ("gone", "missing"),
                                                           ("two panes", "running")]


def test_parse_slurm_and_load():
    jobs = collect_env.parse_slurm(["1001|train_fold3|RUNNING|1:40:00|6:00:00|2026-01-15T12:20:00",
                                    "1003|train_fold4|PENDING|0:00|6:00:00|2026-01-15T19:40:00",
                                    "1004|eval|PENDING|0:00|1:00:00|N/A"])
    assert jobs[0] == {"kind": "slurm", "name": "1001 train_fold3", "state": "running", "role": "1:40:00 of 6:00:00"}
    assert jobs[1]["role"] == "expected start 2026-01-15T19:40:00 (Slurm's guess)"
    assert jobs[2]["role"] == "0:00 of 1:00:00"
    assert collect_env.parse_load(["0.52 0.40 0.35 1/600 1234", "32"]) == {"load_1min": 0.52, "cpus": 32}
    assert collect_env.parse_load([]) == {"load_1min": None, "cpus": None}


def test_machine_label_rules(settings, tmp_path):
    assert collect_env.machine_label(settings, "ws01") == "ws01"              # rule 3: the name itself
    assert collect_env.machine_label(settings, "mycluster") == "Mycluster"    # rule 2: a listed cluster
    labelled = json.loads(write_settings(tmp_path, labels={"ws": "Lab", "ws02": "Lab GPU"}).read_text())
    assert collect_env.machine_label(labelled, "ws01") == "Lab"               # rule 1: prefix
    assert collect_env.machine_label(labelled, "ws02") == "Lab GPU"           # the longest prefix wins
    assert collect_env.machine_label({}, "anything") == "anything"            # no settings at all


def test_unreachable_machines_are_not_contacted(settings, monkeypatch):
    """A machine without ssh_alias is never contacted, and a closed cluster is never logged in to."""
    calls = []

    def fake_run(command, timeout=20, stdin_text=None):
        calls.append(command)
        return 255, ""                            # every ssh attempt fails, as with no open connection

    monkeypatch.setattr(collect_env, "run", fake_run)
    monkeypatch.setattr(collect_env, "system_name", lambda config=None: "elsewhere")
    report = collect_env.machine_report(settings)
    reached = {entry["name"]: entry["reached"] for entry in report["machines"]}
    assert reached == {"ws01": False, "ws02": False, "mycluster": False}
    assert "note" in report                        # this machine is not listed
    # The cluster was only checked with `ssh -O check`, never with a login attempt.
    cluster_calls = [command for command in calls if "mycluster" in command]
    assert cluster_calls == [["ssh", "-O", "check", "mycluster"]]
    assert all("BatchMode=yes" in command for command in calls if command[:2] != ["ssh", "-O"])


def test_merge_replaces_the_old_machine_label(tmp_path, monkeypatch):
    """--merge keeps old values where the live value is empty, but never an old machine's label."""
    settings_path = write_settings(tmp_path)
    settings = json.loads(settings_path.read_text())
    settings["system_name"] = {"override_env": "WORKHUB_TEST_MACHINE", "aliases": {}}
    settings_path.write_text(json.dumps(settings))
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps({"environment": {
        "system": "mycluster", "cluster": "Mycluster", "gpu": "H100 (79.6 GB)",
        "working_dirs": [{"path": "mycluster:/home/alex/projects/demo", "role": "code"}]}}))
    env = dict(os.environ, WORKHUB_CLAUDE_CONFIG=str(settings_path), WORKHUB_TEST_MACHINE="ws01")
    result = run_script("collect_env.py", "--merge", str(status_path), env=env)   # reads only this machine
    assert result.returncode == 0, result.stderr
    environment = json.loads(status_path.read_text())["environment"]
    assert environment["system"] == "ws01" and environment["cluster"] == ""
    assert environment["working_dirs"][0]["role"] == "code"                       # kept: no --dir given


# ---------------------------------------------------------------------------------------------------------
# new_status_folder.py
# ---------------------------------------------------------------------------------------------------------

def run_script(name, *arguments, env=None):
    return subprocess.run([sys.executable, str(SCRIPTS / name), *arguments], capture_output=True, text=True,
                          env=env)


def test_new_status_folder(settings, tmp_path):
    env = dict(os.environ)
    root = Path(settings["paths"]["outputs_dir"]) / "run-status-figure"
    earlier = root / "2026-01-14_0900_machines"
    earlier.mkdir(parents=True)
    (earlier / "status.json").write_text('{"schema": "run-status/1"}')

    dry = run_script("new_status_folder.py", "machines", "--copy-latest", "--dry-run", env=env)
    assert dry.returncode == 0, dry.stderr
    assert "would create" in dry.stdout
    assert sorted(path.name for path in root.iterdir()) == ["2026-01-14_0900_machines"]   # nothing made

    made = run_script("new_status_folder.py", "machines", "--copy-latest", env=env)
    assert made.returncode == 0, made.stderr
    folder = Path(made.stdout.strip().splitlines()[-1])
    assert folder.parent == root and folder.name.endswith("_machines")
    assert (folder / "status.json").read_text() == '{"schema": "run-status/1"}'

    latest = run_script("new_status_folder.py", "machines", "--latest", env=env)
    assert latest.stdout.strip() == str(folder)
    assert run_script("new_status_folder.py", "Bad Name", env=env).returncode == 2


# ---------------------------------------------------------------------------------------------------------
# notion_page.py (needs render_status.py, hence matplotlib)
# ---------------------------------------------------------------------------------------------------------

def test_notion_page_publish_and_skip(settings, tmp_path):
    pytest.importorskip("matplotlib")
    import notion_page

    built = notion_page.page(EXAMPLE, "file-upload://png", "file-upload://pdf", ["extra"], config=settings)
    assert built["research_log"] == {"publish": True, "reason": "", "parent": {
        "type": "data_source_id", "data_source_id": "01234567-89ab-cdef-0123-456789abcdef"}}
    properties = built["properties"]
    assert properties["Type"] == "Status" and properties["Project"] == "Project A"
    assert properties["Name"] == "2026-01-15 14:00 — Project A run status"
    assert properties["date:Date:start"] == "2026-01-15T14:00:00+00:00"       # Lisbon is UTC+0 in January
    assert json.loads(properties["Clusters"]) == ["Mycluster", "ws01"]
    assert properties["Source key"] == "project-a:status/assets"
    assert "## Personal notes" in built["content"] and '<image src="file-upload://png">' in built["content"]

    disabled = json.loads(write_settings(tmp_path, research_log={"enabled": False, "data_source": "x"}).read_text())
    assert notion_page.page(EXAMPLE, None, None, [], config=disabled)["research_log"]["publish"] is False
    empty = json.loads(write_settings(tmp_path, research_log={"enabled": True, "data_source": ""}).read_text())
    skipped = notion_page.page(EXAMPLE, None, None, [], config=empty)["research_log"]
    assert skipped["publish"] is False and "data_source is empty" in skipped["reason"]


def test_notion_page_default_clusters_and_zone(settings, tmp_path):
    pytest.importorskip("matplotlib")
    import notion_page

    status = json.loads(EXAMPLE.read_text())
    del status["notion"]                                       # no notion block at all
    status["environment"]["system"] = "ws01"
    path = tmp_path / "2026-01-15_1400_demo" / "status.json"
    path.parent.mkdir()
    path.write_text(json.dumps(status))
    properties = notion_page.page(path, None, None, [], config=settings)["properties"]
    assert json.loads(properties["Clusters"]) == ["ws01"]
    assert "Project" not in properties                         # left empty rather than guessed
    assert properties["date:Date:start"].endswith("+00:00")    # owner.time_zone = Europe/Lisbon
    assert properties["Source key"] == "status/2026-01-15_1400_demo"


# ---------------------------------------------------------------------------------------------------------
# render_status.py
# ---------------------------------------------------------------------------------------------------------

def test_render_example(settings, tmp_path):
    pytest.importorskip("matplotlib")
    import render_status

    written = render_status.render(EXAMPLE, tmp_path / "figure" / "example")
    assert [path.suffix for path in written] == [".png", ".pdf"]
    assert all(path.stat().st_size > 10_000 for path in written)


def test_render_example_default_output_goes_to_outputs_dir(settings):
    pytest.importorskip("matplotlib")
    result = run_script("render_status.py", "--example", env=dict(os.environ))
    assert result.returncode == 0, result.stderr
    png = Path(settings["paths"]["outputs_dir"]) / "run-status-figure" / "example" / "example_status.png"
    assert result.stdout.splitlines()[0] == str(png) and png.is_file()
    assert not (SKILL_DIR / "assets" / "example_status.png").exists()    # never written into the skill


def test_example_is_valid_and_fictional():
    status = json.loads(EXAMPLE.read_text())
    assert status["schema"] == "run-status/1"
    datetime.fromisoformat(status["generated"])
    names = {stage["model"] for stage in status["stages"]}
    assert names <= {model["name"] for model in status["models"]}
    assert status["environment"]["system"] == "mycluster"
