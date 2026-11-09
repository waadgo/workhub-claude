#!/usr/bin/env bash
# =====================================================================================================================
# install_cluster_login.sh: install the `cluster-login` command and (with --ssh-config) the ssh settings it needs.
#
# This is the entry point the work-hub-setup skill calls. All the work is done by install_cluster_login.py in the same
# folder (Python reads the settings file, makes the diff and edits the marked block of ~/.ssh/config more safely than
# shell text tools); this script only starts it with the same options. Read the header of install_cluster_login.py
# (or run with --help) for what it does and why, including why the socket folder must be on this machine's local disk.
#
# USAGE (dry run unless --apply)
#   bash install_cluster_login.sh                          # plan + the ssh block it would write, as a diff
#   bash install_cluster_login.sh --apply                  # copy cluster-login to the install folder
#   bash install_cluster_login.sh --apply --ssh-config     # also write the managed block in ~/.ssh/config
#   bash install_cluster_login.sh --check                  # exit 0 when installed and current
#   bash install_cluster_login.sh --remove --apply         # remove the block and the installed copy
#   bash install_cluster_login.sh --cluster rorqual --cluster mycluster=login.mycluster.org --user alex \
#        --socket-dir '/run/user/%i' --apply --ssh-config  # without a settings file
#
# DEPENDENCIES
#   bash, python3 >= 3.8.
#
# LICENSE
#   Apache-2.0. Part of workhub-claude by Walter Adame-Gonzalez (https://github.com/waadgo/workhub-claude).
# =====================================================================================================================

# `set -u`: an unset variable is an error.
set -u

# The folder of this script: `${BASH_SOURCE[0]}` is its path, `dirname` removes the file name, and `cd ... && pwd`
# turns the result into an absolute path.
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# python3 must exist; say so clearly instead of a "command not found" further down.
if ! command -v python3 >/dev/null 2>&1; then
  echo "install_cluster_login.sh: python3 is needed and was not found on PATH." >&2
  exit 2
fi

# `exec` replaces this shell with the Python installer, so its exit code is this script's exit code; "$@" passes
# every option on unchanged.
exec python3 "$here/install_cluster_login.py" "$@"
