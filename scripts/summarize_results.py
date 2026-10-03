#!/usr/bin/env python3
"""Show paper tables and archived metrics separately, without APIs or code execution."""

import argparse
from collections import Counter
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def archived_summary(root: Path) -> list:
    rows = []
    for path in sorted((root / "results").glob("**/eval_results.json")):
        data = read_json(path)
        records = data["eval"]
        attempts = [record for group in records.values() for record in
                    (group if isinstance(group, list) else [group])]
        row = {"path": path.relative_to(root).as_posix(),
               "task_count": len(records), "attempt_count": len(attempts)}
        if "evalplus" in path.parts:
            base = sum(record.get("base_status") == "pass" for record in attempts)
            plus = sum(record.get("base_status") == "pass" and
                       record.get("plus_status") == "pass" for record in attempts)
            row.update(base_pass_count=base, base_and_plus_pass_count=plus,
                       base_pass_fraction=base / len(attempts) if attempts else None,
                       base_and_plus_pass_fraction=plus / len(attempts) if attempts else None)
        else:
            passed = sum(record.get("status") == "pass" for record in attempts)
            row.update(pass_count=passed,
                       pass_fraction=passed / len(attempts) if attempts else None)
            metrics_path = path.with_name("pass_at_k.json")
            if metrics_path.exists():
                row["stored_pass_at_1"] = read_json(metrics_path).get("pass@1")
        classified = path.with_name("classified.jsonl")
        if classified.exists():
            labels = Counter()
            count = 0
            empty_count = 0
            for line in classified.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                count += 1
                if not record.get("classifications"):
                    empty_count += 1
                labels.update(item.get("subclass_id", "UNKNOWN")
                              for item in record.get("classifications", []))
            row.update(classified_record_count=count,
                       empty_classification_record_count=empty_count,
                       classification_label_counts=dict(sorted(labels.items())))
        rows.append(row)
    for path in sorted((root / "results/livecodebench").glob("**/samples.json")):
        samples = read_json(path)
        rows.append({"path": path.relative_to(root).as_posix(),
                     "sample_records": len(samples),
                     "unique_question_ids": len({str(item["question_id"]) for item in samples}),
                     "note": "Supplemental generated samples only; no evaluation metric inferred."})
    return rows


def build_summary(root: Path, section: str = "all") -> dict:
    summary = {"fresh_rerun": False}
    if section in {"all", "paper"}:
        summary["paper_reported"] = read_json(root / "results/paper_tables.json")
    if section in {"all", "archive"}:
        summary["archive_note"] = (
            "Counts and pass fractions are read from archived statuses, not rerun. "
            "Fractions use recorded attempts as denominator; with one attempt per task they "
            "correspond to pass@1. Paper values are separately transcribed, may differ from "
            "the archive, and are never overwritten by these calculations. "
            "Classification counts describe emitted labels, not unique tasks or accuracy.")
        summary["archived"] = archived_summary(root)
    return summary


def text_summary(summary: dict) -> str:
    lines = ["No fresh evaluation was run. All values below are historical.", ""]
    if "paper_reported" in summary:
        paper = summary["paper_reported"]
        lines.extend(["PAPER-REPORTED TABLES (direct PDF transcription)",
                      paper["provenance"]["title"], ""])
        for name, columns_key in [("table_2", "benchmark_columns"), ("table_3", "column_order")]:
            table = paper[name]
            columns = table[columns_key]
            lines.extend([f"Table {table['table_number']}: {table['caption']}",
                          "Model | " + " | ".join(columns)])
            for row in table["rows"]:
                lines.append(row["model"] + " | " + " | ".join(f"{row[key]:.3f}" for key in columns))
            lines.append("")
    if "archived" in summary:
        lines.extend(["ARCHIVED COVERAGE AND METRICS", summary["archive_note"], ""])
        for row in summary["archived"]:
            lines.append(row["path"])
            if "sample_records" in row:
                lines.append(f"  samples={row['sample_records']}; unique questions={row['unique_question_ids']}; no pass metric")
                continue
            lines.append(f"  tasks={row['task_count']}; attempts={row['attempt_count']}")
            if "base_pass_count" in row:
                lines.append(f"  base passes={row['base_pass_count']}; base+plus passes={row['base_and_plus_pass_count']}")
                lines.append(f"  base fraction={row['base_pass_fraction']}; base+plus fraction={row['base_and_plus_pass_fraction']}")
            else:
                lines.append(f"  passes={row['pass_count']}; fraction={row['pass_fraction']}; stored pass@1={row.get('stored_pass_at_1')}")
            if "classified_record_count" in row:
                lines.append(f"  classified records={row['classified_record_count']}; "
                             f"emitted labels={sum(row['classification_label_counts'].values())}; "
                             f"records without labels={row['empty_classification_record_count']}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--section", choices=["all", "paper", "archive"], default="all")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", type=Path, help="optional output file; otherwise stdout")
    args = parser.parse_args()
    summary = build_summary(args.root, args.section)
    output = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
              if args.format == "json" else text_summary(summary))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
