#!/bin/sh
# Run from a checkout; isolated install, no sudo, no service start, no ADB connection.
set -eu
command -v python3 >/dev/null || { echo 'Install Python 3.11+ first.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
command -v adb >/dev/null || { echo 'Install adb first (on Raspberry Pi OS: sudo apt install adb python3-venv).'; exit 1; }
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
venv_dir="${EASY_TV_VENV:-$HOME/.local/share/easy-tv/venv}"
if [ -e "$venv_dir" ]; then
  echo "Refusing to replace an existing environment: $venv_dir. Set EASY_TV_VENV to a new path."
  exit 1
fi
python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install "$project_dir"
printf '\nInstalled in %s\nNext: %s/bin/easy-tv init\n' "$venv_dir" "$venv_dir"
