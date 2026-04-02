#!/usr/bin/env bash
set -euxo pipefail

TARGET="$1"
SAFE_NAME="${TARGET//\//__}"
REPO_DIR="targets/${SAFE_NAME}"
LOG_DIR="logs/${SAFE_NAME}/macos"
mkdir -p "$LOG_DIR"

{
  cd "$REPO_DIR"

  python -m venv .venv
  source .venv/bin/activate

  python -m pip install --upgrade pip setuptools wheel

  if [ -f requirements.txt ]; then
    pip install -r requirements.txt
  fi

  if [ -f requirements-dev.txt ]; then
    pip install -r requirements-dev.txt
  fi

  if [ -f pyproject.toml ] || [ -f setup.py ]; then
    pip install -e .
  fi
} 2>&1 | tee "${LOG_DIR}/setup.log"