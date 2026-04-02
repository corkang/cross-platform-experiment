"""Aggregate per-job EnvBench experiment results into summary files.

Reads per-job artifact directories produced by the envbench-python-native
workflow and outputs:
  - results_all.jsonl   (one row per repo × OS)
  - results_all.csv     (same, for spreadsheet consumption)
  - summary.md          (repo × OS pass/fail table)

Usage:
    python scripts/aggregate_results.py \
        --input-dir all-results \
        --output-dir aggregated
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any


def load_job_metadata(artifact_dir: Path) -> dict[str, Any] | None:
    """Load job_metadata.json from an artifact directory."""
    meta_path = artifact_dir / "job_metadata.json"
    if not meta_path.exists():
        return None
    with open(meta_path) as f:
        return json.load(f)


def load_results_json(artifact_dir: Path) -> dict[str, Any] | None:
    """Load the first *.json result file from an artifact directory.

    The evaluation step produces a per-repo JSON file in the artifacts.
    Fall back to results.jsonl if a single JSON is not found.
    """
    # Try individual JSON result files (from evaluation json_results dir)
    json_files = list(artifact_dir.glob("*.json"))
    # Exclude job_metadata.json
    json_files = [f for f in json_files if f.name != "job_metadata.json"]
    if json_files:
        with open(json_files[0]) as f:
            return json.load(f)

    # Fall back to results.jsonl (first line)
    jsonl_path = artifact_dir / "results.jsonl"
    if jsonl_path.exists():
        with open(jsonl_path) as f:
            line = f.readline().strip()
            if line:
                return json.loads(line)

    return None


def compute_pass(result: dict[str, Any] | None) -> str:
    """Compute pass@1 from a result row.

    pass@1 = (exit_code == 0) and (issues_count == 0)
    """
    if result is None:
        return "error"
    exit_code = result.get("exit_code")
    issues_count = result.get("issues_count")
    if exit_code is None or issues_count is None:
        return "error"
    if exit_code == 0 and issues_count == 0:
        return "pass"
    return "fail"


def main():
    parser = argparse.ArgumentParser(description="Aggregate EnvBench experiment results")
    parser.add_argument("--input-dir", required=True, help="Directory containing per-job artifact directories")
    parser.add_argument("--output-dir", required=True, help="Directory to write aggregated outputs")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)

    rows: list[dict[str, Any]] = []

    # Each subdirectory under input_dir is a per-job artifact (result-{os}-{safe_name})
    for artifact_dir in sorted(input_dir.iterdir()):
        if not artifact_dir.is_dir():
            continue

        metadata = load_job_metadata(artifact_dir)
        if metadata is None:
            print(f"  SKIP {artifact_dir.name}: no job_metadata.json")
            continue

        result = load_results_json(artifact_dir)
        pass_status = compute_pass(result)

        row = {
            "repository": metadata.get("repository", ""),
            "revision": metadata.get("revision", ""),
            "target_os": metadata.get("target_os", ""),
            "runs_on": metadata.get("runs_on", ""),
            "run_name": metadata.get("run_name", ""),
            "exit_code": result.get("exit_code") if result else None,
            "issues_count": result.get("issues_count") if result else None,
            "pass_at_1": pass_status,
        }
        rows.append(row)
        print(f"  {artifact_dir.name}: {pass_status}")

    if not rows:
        print("No results found to aggregate.", file=sys.stderr)
        # Write empty files so the artifact upload doesn't fail
        (output_dir / "results_all.jsonl").touch()
        (output_dir / "results_all.csv").touch()
        (output_dir / "summary.md").write_text("# EnvBench Results\n\nNo results collected.\n")
        return

    # ── Write results_all.jsonl ──────────────────────────────────
    jsonl_path = output_dir / "results_all.jsonl"
    with open(jsonl_path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {jsonl_path} ({len(rows)} rows)")

    # ── Write results_all.csv ────────────────────────────────────
    csv_path = output_dir / "results_all.csv"
    fieldnames = ["repository", "revision", "target_os", "runs_on", "run_name", "exit_code", "issues_count", "pass_at_1"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {csv_path}")

    # ── Write summary.md ─────────────────────────────────────────
    # Build repo × OS table
    os_list = sorted(set(r["target_os"] for r in rows))
    repo_list = sorted(set(r["repository"] for r in rows))

    # Index results for quick lookup
    result_index: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        result_index[(r["repository"], r["target_os"])] = r

    md_lines = [
        "# EnvBench Python Native — Pilot Results",
        "",
        f"**Run ID**: {rows[0].get('run_name', 'N/A').rsplit('-', 2)[0] if rows else 'N/A'}",
        f"**Repos**: {len(repo_list)}  |  **OS**: {', '.join(os_list)}  |  **Total jobs**: {len(rows)}",
        "",
        "## Results Matrix",
        "",
    ]

    # Table header
    header = "| Repository |" + " | ".join(os_list) + " |"
    separator = "|---|" + " | ".join(["---"] * len(os_list)) + " |"
    md_lines.append(header)
    md_lines.append(separator)

    pass_count = 0
    fail_count = 0
    error_count = 0

    for repo in repo_list:
        cells = []
        for os_name in os_list:
            r = result_index.get((repo, os_name))
            if r is None:
                cells.append("-")
                error_count += 1
            elif r["pass_at_1"] == "pass":
                cells.append("PASS")
                pass_count += 1
            elif r["pass_at_1"] == "fail":
                ic = r.get("issues_count", "?")
                cells.append(f"FAIL (ic={ic})")
                fail_count += 1
            else:
                cells.append("ERROR")
                error_count += 1

        short_repo = repo.split("/")[-1] if "/" in repo else repo
        md_lines.append(f"| `{short_repo}` | " + " | ".join(cells) + " |")

    # Total = all cells in the matrix (repos × OSes), including missing
    total_cells = len(repo_list) * len(os_list)

    md_lines.extend([
        "",
        "## Summary",
        "",
        f"- **PASS**: {pass_count}",
        f"- **FAIL**: {fail_count}",
        f"- **ERROR/MISSING**: {error_count}",
        f"- **Total**: {total_cells}",
        "",
        "Metric: `pass@1 = (exit_code == 0) and (issues_count == 0)`",
        "",
        "`issues_count` = number of `reportMissingImports` from pyright.",
    ])

    summary_path = output_dir / "summary.md"
    summary_path.write_text("\n".join(md_lines) + "\n")
    print(f"Wrote {summary_path}")

    # Print summary to console
    print(f"\n{'='*50}")
    print(f"PASS: {pass_count} | FAIL: {fail_count} | ERROR/MISSING: {error_count} | TOTAL: {total_cells}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
