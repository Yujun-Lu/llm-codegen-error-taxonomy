#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compress.py
将 HumanEval+ 汇总 CSV 转为 JSONL（包含 prompt / model_solution / reference_solution），
并默认把“非显式异常”的样本标为 WrongAnswer（可用 --default-error-type 覆盖）。

必需列：task_id, which, input, error, expected, got, entry_point, prompt, model_solution, reference_solution
输出 JSONL 每行包含：
  task_id, error_type, tests, message, prompt, model_solution, reference_solution

用法：
  python compress.py heplus.csv minimal_errors.jsonl
  # 保留 PassNow（默认会跳过）：
  python compress.py heplus.csv minimal_errors.jsonl --keep-passnow
  # 修改默认大类（将非显式异常的默认从 WrongAnswer 改为 AssertionError）：
  python compress.py heplus.csv minimal_errors.jsonl --default-error-type AssertionError
  # 截断长字段到 4000 字符，避免超大 token：
  python compress.py heplus.csv minimal_errors.jsonl --truncate 4000
"""
import argparse, csv, io, json, re
from pathlib import Path

def open_text(path, mode="r"):
    # 读入兼容 UTF-8 BOM；写出为 UTF-8
    if "r" in mode:
        return io.open(path, mode, encoding="utf-8-sig", newline="")
    return io.open(path, mode, encoding="utf-8", newline="")

EXC_PAT = re.compile(r"\b(AssertionError|TypeError|ValueError)\b\s*:", re.I)

def last_exception_line(s: str) -> str:
    if not s:
        return ""
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    # 从后往前找“异常名:”的行
    for ln in reversed(lines):
        if EXC_PAT.search(ln):
            return ln
    # 找不到就返回最后一行
    return lines[-1] if lines else ""

def compact_msg(msg: str, limit=280) -> str:
    msg = re.sub(r"\s+", " ", (msg or "")).strip()
    return msg if len(msg) <= limit else msg[: limit - 12].rstrip() + " ... <omitted>"

def guess_error_type(err_text: str, default_type: str) -> str:
    """优先用 error 文本中的显式异常；否则回退到默认（默认 WrongAnswer）。"""
    low = (err_text or "").strip().lower()
    if "typeerror" in low:       return "TypeError"
    if "valueerror" in low:      return "ValueError"
    if "assertionerror" in low:  return "AssertionError"
    if "wronganswer" in low:     return "WrongAnswer"
    # 其它情况使用默认
    return default_type

def build_message(row: dict, errtype: str) -> str:
    """
    生成 message：
      - 若 error 文本包含显式异常行，取最后一行异常并压缩
      - 否则构造一条简短摘要：
          WrongAnswer: expected=... got=... | input=...
        或
          AssertionError: expected=... got=... | input=...
    """
    err = row.get("error", "") or ""
    if EXC_PAT.search(err):
        return compact_msg(last_exception_line(err))
    exp = str(row.get("expected", "")).strip()
    got = str(row.get("got", "")).strip()
    inp = str(row.get("input", "")).strip()
    head = "WrongAnswer" if errtype == "WrongAnswer" else "AssertionError"
    return compact_msg(f"{head}: expected={exp!r} got={got!r} | input={inp!r}")

def maybe_truncate(s: str, n: int) -> str:
    if not s or n <= 0:
        return s or ""
    return s if len(s) <= n else s[: n - 12] + " ... <omitted>"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", help="HumanEval+ CSV 文件路径")
    ap.add_argument("out", help="输出 JSONL 文件路径")
    ap.add_argument("--keep-passnow", dest="keep_passnow", action="store_true",
                    help="保留 error 为 PassNow 的题目（默认跳过）")
    ap.add_argument("--truncate", type=int, default=0,
                    help="可选：截断 prompt/model_solution/reference_solution 的最大长度（字符）")
    ap.add_argument("--default-error-type", default="WrongAnswer",
                    choices=["WrongAnswer", "AssertionError"],
                    help="无显式异常时的默认错误类型（默认 WrongAnswer）")
    args = ap.parse_args()

    input_path, output_path = Path(args.inp), Path(args.out)
    if (input_path.resolve() == output_path.resolve() or
            (input_path.exists() and output_path.exists() and input_path.samefile(output_path))):
        ap.error("Input and output must be different files")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    with open_text(args.inp, "r") as fin, open_text(args.out, "w") as fout:
        reader = csv.DictReader(fin)
        for row in reader:
            err_raw = (row.get("error") or "").strip()

            # 跳过 PassNow（除非显式保留）
            if (not args.keep_passnow) and err_raw.lower() == "passnow":
                continue

            task_id = (row.get("task_id") or "").strip()
            which   = (row.get("which") or "case").strip()

            # 1) 判定大类（TypeError/ValueError/AssertionError/WrongAnswer）
            errtype = guess_error_type(err_raw, args.default_error_type)

            # 2) 构造 message
            msg = build_message(row, errtype)

            # 3) 透传大字段（可选截断）
            prompt              = maybe_truncate(row.get("prompt") or "", args.truncate)
            model_solution      = maybe_truncate(row.get("model_solution") or "", args.truncate)
            reference_solution  = maybe_truncate(row.get("reference_solution") or "", args.truncate)

            obj = {
                "task_id": task_id,
                "error_type": errtype,
                "tests": [which],
                "message": [msg],
                "prompt": prompt,
                "model_solution": model_solution,
                "reference_solution": reference_solution,
            }
            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            kept += 1

    print(f"[done] wrote {kept} lines -> {args.out}")

if __name__ == "__main__":
    main()
