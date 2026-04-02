#!/bin/bash

set -e

# Create output directory
mkdir -p build_output
# 초기 결과 파일
printf '{"pyright": {}}\n' > build_output/results.json

# If a bootstrap script exists, run it first
if [ -f "./bootstrap_script.sh" ]; then
  echo "Bootstrap script contents:"
  cat ./bootstrap_script.sh
  echo "Running bootstrap script..."
  source ./bootstrap_script.sh || true
fi

# Install pyright (use PYRIGHT_VERSION if set, otherwise unpin)
if [ -n "${PYRIGHT_VERSION:-}" ]; then
  python -m pip install --quiet "pyright==${PYRIGHT_VERSION}"
else
  python -m pip install --quiet pyright
fi

# Print which Python is being used
echo "Using $(python --version 2>&1) located at $(which python)"

# Run type checking with pyright
echo "Running type checks..."
if ! command -v pyright &> /dev/null; then
    echo "pyright not found after install"
    exit 1
fi

# Run pyright and capture its output regardless of exit code
python -m pyright . --level error --outputjson > build_output/pyright_output.json 2>/dev/null || true

# Check if pyright output exists
if [ ! -f build_output/pyright_output.json ]; then
    echo "Failed to get valid pyright output"
    exit 1
fi

# jq 대신 Python으로 JSON 집계 수행 (cross-platform)
python -c "
import json, sys, os

pyright_path = 'build_output/pyright_output.json'
results_path = 'build_output/results.json'

# pyright output 읽기
try:
    with open(pyright_path) as f:
        pyright_data = json.load(f)
except (json.JSONDecodeError, FileNotFoundError) as e:
    print(f'Error reading pyright output: {e}', file=sys.stderr)
    # pyright output이 invalid해도 결과는 남기기
    with open(results_path, 'w') as f:
        json.dump({'pyright': {}, 'issues_count': -1}, f)
    sys.exit(1)

# reportMissingImports 개수 집계
diagnostics = pyright_data.get('generalDiagnostics', [])
issue_count = sum(1 for d in diagnostics if d.get('rule') == 'reportMissingImports')

# 기존 results.json 읽기
try:
    with open(results_path) as f:
        results = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    results = {}

# 결과 병합
results['issues_count'] = issue_count
results['pyright'] = pyright_data

with open(results_path, 'w') as f:
    json.dump(results, f)

print(f'Found {issue_count} reportMissingImports issues')
"

exit 0
