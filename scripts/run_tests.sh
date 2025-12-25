#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="${repo_root}/mindtriage/backend"
backend_venv="${backend_dir}/.venv"

if [ ! -d "${backend_venv}" ]; then
  echo "Creating backend venv..."
  python3 -m venv "${backend_venv}"
fi

echo "Installing backend test requirements..."
"${backend_venv}/bin/python" -m pip install -r "${backend_dir}/requirements-dev.txt" >/dev/null

echo "Running pytest..."
"${backend_venv}/bin/python" -m pytest "${backend_dir}/tests"
