#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jsonl_to_csv.py
把形如：
{"task_id": "...", "error_type": "...", "tests": ["..."], "classifications": [{"message_index": 0, "subclass_id": "..."}]}
的 JSONL 转成 CSV（多 classification 展开为多行）。

用法：
  python jsonl_to_csv.py input.jsonl output.csv
  # 可选：自定义 tests 连接符
  python jsonl_to_csv.py input.jsonl output.csv --sep "|"
"""
import argparse, json, io, csv, sys
from pathlib import Path

def open_text(path, mode="r"):
    # 兼容含 BOM 的 UTF-8
    if "r" in mode:
        return io.open(path, mode, encoding="utf-8-sig", newline="")
    return io.open(path, mode, encoding="utf-8", newline="")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", type=Path, help="输入 JSONL 文件")
    ap.add_argument("out", type=Path, help="输出 CSV 文件")
    ap.add_argument("--sep", default="|", help="tests 数组的连接符，默认 |")
    args = ap.parse_args()

    input_path, output_path = Path(args.inp), Path(args.out)
    if (input_path.resolve() == output_path.resolve() or
            (input_path.exists() and output_path.exists() and input_path.samefile(output_path))):
        ap.error("Input and output must be different files")

    args.out.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["task_id", "error_type", "tests", "subclass_id", "message_index"]

    with open_text(args.inp, "r") as fin, open_text(args.out, "w") as fout:
        writer = csv.DictWriter(fout, fieldnames=fieldnames)
        writer.writeheader()

        line_no = 0
        for raw in fin:
            line_no += 1
            raw = raw.strip()
            if not raw:
                # 跳过空行（JSONL 规范中空行无效）
                continue
            try:
                obj = json.loads(raw)
            except Exception as e:
                print(f"[warn] line {line_no}: 非法 JSON，已跳过；{e}", file=sys.stderr)
                continue

            task_id = obj.get("task_id", "")
            errtype = obj.get("error_type", "")
            tests = obj.get("tests") or []
            if not isinstance(tests, list):
                tests = [str(tests)]
            tests_joined = args.sep.join(map(str, tests))

            clses = obj.get("classifications") or []
            # 若没有 classifications，就写一行空 subclass_id
            if not isinstance(clses, list) or len(clses) == 0:
                writer.writerow({
                    "task_id": task_id,
                    "error_type": errtype,
                    "tests": tests_joined,
                    "subclass_id": "",
                    "message_index": ""
                })
                continue

            for c in clses:
                subclass_id = (c or {}).get("subclass_id", "")
                msg_idx = (c or {}).get("message_index", "")
                writer.writerow({
                    "task_id": task_id,
                    "error_type": errtype,
                    "tests": tests_joined,
                    "subclass_id": subclass_id,
                    "message_index": msg_idx
                })

    print(f"[done] wrote CSV -> {args.out}")

if __name__ == "__main__":
    main()
