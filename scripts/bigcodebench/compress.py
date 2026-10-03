#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Split & compress BigCodeBench error_msg per test case.

Input JSONL (per record): {task_id, error_type, error_msg, ...}

Output JSONL (two modes):
  A) distinct (default):  每个“相同异常签名(etype+msg)”只保留一条，附上共享它的 tests 列表
     {"task_id", "error_type", "tests": [...], "message": ["..."]}
  B) percase:            每个失败用例各一条（不去重）
     {"task_id", "test": "test_xxx", "error_type", "message": ["..."]}

为避免误拼接：
- 以 “<name>: Traceback (most recent call last):” 作为块起点
- 块内仅取 **最后一次** (AssertionError|TypeError|ValueError): 行
- 只拼接该异常行后面的少量解释，遇到空行或下一个块起点即停止
"""

import argparse
import json
import re
from pathlib import Path

# 允许的异常大类；同时兼容拼写错误 AassertionError
ALLOWED = {"AssertionError", "ValueError", "TypeError"}
ALIASES = {"AassertionError": "AssertionError"}

json_kwargs = dict(ensure_ascii=False)

# 匹配用例块起始行：test_case_1: Traceback (most recent call last):
CASE_HEAD = re.compile(
    r'(?m)^(?P<name>[A-Za-z0-9_\.]+):\s*Traceback\s*\(most recent call last\):\s*$'
)

# 匹配块内的异常行（取最后一次）
ERR_LINE = re.compile(r'^(?P<etype>AssertionError|TypeError|ValueError)\s*:\s*(?P<detail>.*)$')

def open_io(path: Path, mode: str):
    if str(path).endswith(".gz"):
        import gzip
        return gzip.open(path, mode + "t", encoding="utf-8")
    return open(path, mode, encoding="utf-8")

def normalize_error_type(err: str) -> str:
    if not err:
        return ""
    return ALIASES.get(err.strip(), err.strip())

def collapse_diffs(s: str, max_brackets: int = 160) -> str:
    # 折叠超长的 [...]/(...) 以减少 Token
    def _shorten(m):
        t = m.group(0)
        return t if len(t) <= max_brackets else t[:1] + "<omitted>" + t[-1:]
    s = re.sub(r"\[.*?\]|\(.*?\)", _shorten, s)
    # 统一 “Diff is N characters long …”
    s = re.sub(r"Diff is \d+ characters long\..*", "Diff omitted.", s)
    # 折叠空白
    s = re.sub(r"\s+", " ", s).strip()
    return s

def split_case_blocks(error_msg: str):
    """
    把整段 error_msg 切成若干 {name, text} 用例块。
    没匹配到任何块时，返回单一块 name=None, text=全文（兜底）。
    """
    if not error_msg:
        return []
    starts = list(CASE_HEAD.finditer(error_msg))
    blocks = []
    if not starts:
        blocks.append({"name": None, "text": error_msg})
        return blocks
    for i, m in enumerate(starts):
        name = m.group("name")
        start = m.end()
        end = starts[i + 1].start() if i + 1 < len(starts) else len(error_msg)
        blocks.append({"name": name, "text": error_msg[start:end]})
    return blocks

def extract_error_from_block(text: str, tail_lines: int = 2, max_chunk: int = 280):
    """
    在用例块内部定位**最后一次**异常行，并拼接其后 0~tail_lines 行解释；
    遇到空行或下一块起点就停止，避免串行。
    返回 (etype, compact_message) 或 (None, None)。
    """
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    # 找出最后一次异常行索引
    last_idx, last_etype = None, None
    for i in range(len(lines) - 1, -1, -1):
        m = ERR_LINE.match(lines[i].strip())
        if m:
            last_idx, last_etype = i, m.group("etype")
            break
    if last_idx is None:
        return None, None

    # 收集异常行 + 后续解释（最多 tail_lines 行；遇到空行停止）
    chunk = [lines[last_idx].strip()]
    k = 0
    for j in range(last_idx + 1, len(lines)):
        if not lines[j].strip():
            break
        # 防御：下一块头的“xxx: Traceback …”若意外出现在块内也直接停
        if CASE_HEAD.match(lines[j]):
            break
        chunk.append(lines[j].strip())
        k += 1
        if k >= tail_lines:
            break

    msg = collapse_diffs(" ".join(chunk))
    if len(msg) > max_chunk:
        msg = msg[: max_chunk - 12].rstrip() + " ... <omitted>"
    return last_etype, msg

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="input errors JSONL")
    ap.add_argument("output", type=Path, help="output minimal JSONL")
    ap.add_argument("--mode", choices=["distinct", "percase"], default="distinct",
                    help="distinct: 按(etype+msg)去重并汇总tests; percase: 每个失败用例各一条")
    args = ap.parse_args()

    input_path, output_path = Path(args.input), Path(args.output)
    if (input_path.resolve() == output_path.resolve() or
            (input_path.exists() and output_path.exists() and input_path.samefile(output_path))):
        ap.error("Input and output must be different files")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    n_in, n_out = 0, 0
    with open_io(args.input, "r") as fin, open_io(args.output, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            n_in += 1

            task_id = rec.get("task_id")
            raw_err_type = normalize_error_type(str(rec.get("error_type", "")))
            error_msg = rec.get("error_msg", "") or ""

            # 1) 切块：每个测试用例一块；若切不到，就整体当一个块
            blocks = split_case_blocks(error_msg)

            # 2) 提取每块的最终异常行
            per_case = []  # [(test_name, etype, msg)]
            for b in blocks:
                etype, msg = extract_error_from_block(b["text"])
                if not etype or etype not in ALLOWED:
                    # 如果块内不是三大类，跳过该块（只做这三类的细分）
                    continue
                per_case.append((b["name"], etype, msg))

            # 若一个块也没提到三大类异常：回退到原记录的大类（若在 ALLOWED）+ 最尾部异常信息
            if not per_case and raw_err_type in ALLOWED:
                etype, msg = extract_error_from_block(error_msg)
                if etype and msg:
                    per_case.append((None, etype, msg))

            if not per_case:
                continue  # 本条记录没有目标大类，跳过

            if args.mode == "percase":
                for name, etype, msg in per_case:
                    out = {
                        "task_id": task_id,
                        "test": name,
                        "error_type": etype,
                        "message": [msg],
                    }
                    fout.write(json.dumps(out, **json_kwargs) + "\n")
                    n_out += 1
            else:
                # distinct 模式：按 (etype + msg) 去重并汇总 tests
                groups = {}
                for name, etype, msg in per_case:
                    key = (etype, msg)
                    groups.setdefault(key, []).append(name)
                for (etype, msg), tests in groups.items():
                    tests = sorted(t for t in tests if t)  # 过滤 None
                    out = {
                        "task_id": task_id,
                        "error_type": etype,
                        "tests": tests,
                        "message": [msg],
                    }
                    fout.write(json.dumps(out, **json_kwargs) + "\n")
                    n_out += 1

    print(f"[done] read={n_in}, wrote={n_out} -> {args.output}")

if __name__ == "__main__":
    main()
