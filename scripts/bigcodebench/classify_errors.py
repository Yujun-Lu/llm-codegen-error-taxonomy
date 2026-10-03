#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run local 'batch' classification against Aliyun Model Studio (OpenAI-compatible),
PRINTING PROGRESS per record when each finishes.

Input : minimal_errors.jsonl  (one JSON per line: task_id, error_type, tests, message)
Output: classified.jsonl      (one JSON per line: task_id, error_type, tests, classifications)

Env:
  DASHSCOPE_BASE_URL   e.g. https://dashscope.aliyuncs.com/compatible-mode/v1
  DASHSCOPE_API_KEY    your api key

Usage:
  python classify_errors.py minimal_errors.jsonl classified.jsonl --model deepseek-r1 --workers 6
"""

import os, json, argparse, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


SYSTEM_TAXONOMY = {
    "AssertionError": {
        "A_EQ_NEQ": "值相等/不等断言失败",
        "A_SEQ_LEN": "序列/长度或额外/缺失元素",
        "A_FLOAT_APPROX": "浮点近似未用容差",
        "A_NOT_NONE_EMPTY": "空值/非空断言失败",
        "A_RANGE_BOUND": "范围/边界断言失败",
        "A_TYPE_CHECK": "类型断言失败",
        "A_STRUCTURE_KEYS": "结构/键集合不一致",
        "A_MEMBERSHIP": "包含关系失败",
        "A_EXPECTED_EXCEPTION": "期望异常不符"
    },
    "ValueError": {
        "V_PARSE_CONVERT": "解析/转换失败",
        "V_MATH_DOMAIN": "数学定义域错误",
        "V_UNPACK": "序列解包数量不匹配",
        "V_EMPTY_MINMAX": "空序列的 min/max",
        "V_INVALID_OPTION": "非法选项/模式"
    },
    "TypeError": {
        "T_BAD_ARGTYPE": "参数类型不符（如 int() not 'tuple')",
        "T_NOT_ITERABLE": "对象不可迭代",
        "T_NOT_CALLABLE": "对象不可调用",
        "T_ARGCOUNT": "参数个数不匹配",
        "T_UNSUPPORTED_OPERAND": "不支持的运算/比较类型",
        "T_UNHASHABLE": "不可哈希类型作键/集合元素"
    }
}

SYSTEM_PROMPT = (
    "你是“代码错误分类助手”。仅依据给定 JSON 中的 'error_type'、'tests'、'message'，"
    "把每条 message 细分到下表子类之一；严格只输出 JSON（无多余文本）。\n"
    f"{json.dumps(SYSTEM_TAXONOMY, ensure_ascii=False)}\n"
    "输出JSON架构：{\n"
    '  "task_id": "...", "error_type": "...", "tests": ["..."],\n'
    '  "classifications": [ {"message_index":0,"subclass_id":"..."} ... ]\n'
    "}\n"
    "规则：优先依据异常行关键词匹配最具体子类；无法匹配则用 UNKNOWN。只输出 JSON。"
)

def build_client():
    from openai import OpenAI
    base = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    key  = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    return OpenAI(base_url=base, api_key=key)

def build_messages(record: dict):
    user_payload = {
        "record": {
            "task_id": record.get("task_id"),
            "error_type": record.get("error_type"),
            "tests": record.get("tests") or [],
            "message": record.get("message") or []
        }
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}
    ]

def call_once(client, model, record, max_tokens=300):
    # JSON Mode（仅输出 JSON 对象）
    resp = client.chat.completions.create(
        model=model,
        messages=build_messages(record),
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content or "{}"
    try:
        obj = json.loads(content)
    except Exception:
        # 兜底：输出 UNKNOWN，保持结构不变
        obj = {
            "task_id": record.get("task_id"),
            "error_type": record.get("error_type"),
            "tests": record.get("tests") or [],
            "classifications": [
                {"message_index": i, "subclass_id": "UNKNOWN"}
                for i in range(len(record.get("message") or []))
            ],
        }
    # 仅保留必要字段
    return {
        "task_id": obj.get("task_id", record.get("task_id")),
        "error_type": obj.get("error_type", record.get("error_type")),
        "tests": obj.get("tests", record.get("tests") or []),
        "classifications": obj.get("classifications", []),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", type=Path, help="minimal_errors.jsonl")
    ap.add_argument("out", type=Path, help="classified.jsonl")
    ap.add_argument("--model", default="deepseek-r1", help="classifier model (paper: deepseek-r1)")
    ap.add_argument("--workers", type=int, default=6, help="concurrent workers")
    ap.add_argument("--limit", type=int, default=0, help="only first N records")
    ap.add_argument("--silent", action="store_true", help="no per-record log")
    args = ap.parse_args()

    if (args.inp.resolve() == args.out.resolve() or
            (args.inp.exists() and args.out.exists() and args.inp.samefile(args.out))):
        ap.error("Input and output must be different files")
    if args.workers < 1:
        ap.error("--workers must be at least 1")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    client = build_client()
    lines = [ln for ln in args.inp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if args.limit:
        lines = lines[:args.limit]

    records = [json.loads(ln) for ln in lines]
    total = len(records)
    start = time.time()
    done = 0
    ok = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex, open(args.out, "w", encoding="utf-8") as fout:
        fut_to_meta = {}
        for rec in records:
            fut = ex.submit(call_once, client, args.model, rec)
            fut_to_meta[fut] = {"task_id": rec.get("task_id")}

        # 关键点：使用 as_completed 按“完成顺序”获得结果并打印进度。:contentReference[oaicite:2]{index=2}
        for fut in as_completed(fut_to_meta):
            meta = fut_to_meta[fut]
            task_id = meta["task_id"]
            try:
                res = fut.result()
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
                ok += 1
                # 简要提取子类（若多条 message 则拼接）
                subcats = []
                for c in (res.get("classifications") or []):
                    sc = c.get("subclass_id", "UNKNOWN")
                    if sc not in subcats:
                        subcats.append(sc)
                label = ",".join(subcats) if subcats else "UNKNOWN"
                status = label
            except Exception as e:
                fail += 1
                status = f"ERROR:{type(e).__name__}"

            done += 1
            if not args.silent:
                elapsed = time.time() - start
                rps = done / elapsed if elapsed > 0 else 0.0
                print(f"[{done}/{total} | {elapsed:.1f}s | {rps:.2f} rps] {task_id} -> {status}", flush=True)

    if not args.silent:
        elapsed = time.time() - start
        print(f"Done. total={total}, ok={ok}, fail={fail}, time={elapsed:.2f}s, avg={total/elapsed:.2f} rps")

    if fail:
        raise SystemExit(f"Classification failed for {fail} record(s); output contains successful records only.")

if __name__ == "__main__":
    main()
