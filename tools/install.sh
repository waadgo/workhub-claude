#!/usr/bin/env bash
# =========================================================================================================
# install.sh: install the workhub-claude skills by hand (without the Claude Code plugin system).
#
# PURPOSE
#   Copies every skill of this repository into ~/.claude/skills/<skill>/ (and, with --codex, into
#   ~/.codex/skills/<skill>/), and a copy of the whole kit into ~/.local/share/workhub-claude/kit/, where
#   the work-hub-setup skill finds the rules, hooks, templates and tools. It changes nothing else: the
#   settings, hooks, CLAUDE.md rules and cluster-login are installed later by the work-hub-setup skill,
#   after it has asked its questions ("set up my work hub").
#
# USAGE
#   bash tools/install.sh              # dry run: print what would be copied
#   bash tools/install.sh --apply      # copy
#   bash tools/install.sh --apply --codex
#   bash tools/install.sh --remove --apply   # remove the copied skills and the kit copy
#
# INPUTS
#   This repository (the folder above tools/). Options: --apply, --codex, --remove, --help.
#
# OUTPUTS
#   ~/.claude/skills/<skill>/ for each folder in skills/; ~/.codex/skills/<skill>/ with --codex;
#   ~/.local/share/workhub-claude/kit/. A skill folder that already exists is first moved to
#   ~/.local/share/workhub-claude/backups/<YYYYMMDD-HHMMSS>/ (outside the skills folder, so the old copy is
#   not loaded as a second skill).
#
# EXIT STATUS
#   0 = done (or dry run shown); 2 = bad option or the repository layout is not found.
#
# DEPENDENCIES
#   bash, cp, mv, mkdir, rm (only inside the folders listed above, and only with --remove --apply).
#
# LICENSE
#   Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
# =========================================================================================================

# -e: stop at the first failing command; -u: an unset variable is an error; -o pipefail: a pipe fails when
# any part of it fails.
set -euo pipefail

# ---------------------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------------------
apply=0
codex=0
remove=0
for argument in "$@"; do
    case "$argument" in
        --apply) apply=1 ;;
        --codex) codex=1 ;;
        --remove) remove=1 ;;
        --help|-h) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown option: $argument (see --help)" >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------------------------------------
# The repository is the folder above this script's folder. `cd ... && pwd` turns it into an absolute path.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_home="$HOME/.local/share/workhub-claude"
kit_copy="$install_home/kit"
stamp="$(date +%Y%m%d-%H%M%S)"
backup_folder="$install_home/backups/$stamp"

if [ ! -d "$repo_root/skills" ] || [ ! -f "$repo_root/lib/workhub_config.py" ]; then
    echo "This does not look like the workhub-claude repository: $repo_root" >&2
    exit 2
fi

skill_targets=("$HOME/.claude/skills")
if [ "$codex" -eq 1 ]; then
    skill_targets+=("$HOME/.codex/skills")
fi

# say_or_do prints a command in dry-run mode and runs it with --apply.
say_or_do() {
    if [ "$apply" -eq 1 ]; then
        "$@"
    else
        echo "(dry run) $*"
    fi
}

# ---------------------------------------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------------------------------------
if [ "$remove" -eq 1 ]; then
    for target in "${skill_targets[@]}"; do
        for skill_folder in "$repo_root"/skills/*/; do
            skill_name="$(basename "$skill_folder")"
            if [ -d "$target/$skill_name" ]; then
                say_or_do rm -r "$target/$skill_name"
            fi
        done
    done
    if [ -d "$kit_copy" ]; then
        say_or_do rm -r "$kit_copy"
    fi
    echo "Settings (~/.config/workhub-claude), hooks and the CLAUDE.md rules are removed by the setup skill's"
    echo "undo steps (README, \"Uninstall\"). Your Notion hub is never touched."
    exit 0
fi

# ---------------------------------------------------------------------------------------------------------
# Install the skills
# ---------------------------------------------------------------------------------------------------------
for target in "${skill_targets[@]}"; do
    say_or_do mkdir -p "$target"
    for skill_folder in "$repo_root"/skills/*/; do
        skill_name="$(basename "$skill_folder")"
        if [ -e "$target/$skill_name" ]; then
            say_or_do mkdir -p "$backup_folder/$(basename "$(dirname "$target")")"
            say_or_do mv "$target/$skill_name" "$backup_folder/$(basename "$(dirname "$target")")/$skill_name"
        fi
        say_or_do cp -R "$skill_folder" "$target/$skill_name"
    done
done

# ---------------------------------------------------------------------------------------------------------
# Install the kit copy (rules, hooks, templates, tools, config examples) used by work-hub-setup
# ---------------------------------------------------------------------------------------------------------
if [ -e "$kit_copy" ]; then
    say_or_do mkdir -p "$backup_folder"
    say_or_do mv "$kit_copy" "$backup_folder/kit"
fi
say_or_do mkdir -p "$kit_copy"
for part in .claude-plugin config hooks lib rules skills templates tools README.md LICENSE LICENSE-TEMPLATE.md NOTICE CITATION.cff; do
    if [ -e "$repo_root/$part" ]; then
        say_or_do cp -R "$repo_root/$part" "$kit_copy/$part"
    fi
done

if [ "$apply" -eq 1 ]; then
    echo "Installed. Start Claude Code and say: set up my work hub"
else
    echo "Dry run only. Run again with --apply to install."
fi
