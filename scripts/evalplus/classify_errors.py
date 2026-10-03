#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_run_heplus.py
对“含源码上下文”的最小 JSONL（含 prompt/model_solution/reference_solution）做批量推理，
实时打印进度，只落盘精简结果（无推理过程）。

Env:
  DASHSCOPE_BASE_URL   e.g. https://dashscope.aliyuncs.com/compatible-mode/v1
  DASHSCOPE_API_KEY    your api key

Usage:
  python classify_errors.py minimal_errors.jsonl classified.jsonl --model deepseek-r1 --workers 8
  # 发送源码上下文（默认开启），若想关闭减少token：
  python classify_errors.py minimal_errors.jsonl classified.jsonl --model deepseek-r1 --no-src
  # 限制每条发送的源码上下文最大长度（进一步控费）：
  python classify_errors.py minimal_errors.jsonl classified.jsonl --model deepseek-r1 --clip-src 4000
"""

import os, json, argparse, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- 细分分类体系：含 WrongAnswer ----------
SYSTEM_TAXONOMY = {
    "WrongAnswer": {
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
    "你是“代码错误分类助手”。根据输入 JSON 中的字段：'error_type'、'tests'、'message' "
    "以及可用的 'prompt'、'model_solution'、'reference_solution'（三者是**上下文线索**），"
    "把每条 message 细分到下表子类之一；严格只输出 JSON（无多余文本）。\n\n"
    + json.dumps(SYSTEM_TAXONOMY, ensure_ascii=False)
    + "\n\n输出JSON架构：{\n"
      '  "task_id":"...","error_type":"...","tests":["..."],\n'
      '  "classifications":[{"message_index":0,"subclass_id":"..."}]\n'
      "}\n"
      "判定要点：\n"
      "1) 以 'message' 的异常/对比关键词为主；\n"
      "2) 若 error_type 为 WrongAnswer，可参考 expected/got 的差异特征（由 message 概括），并结合 prompt/solutions 佐证（如算法/顺序/去重/精度意图）；\n"
      "3) 无法匹配则标 UNKNOWN。只输出 JSON。"
)

def build_client():
    from openai import OpenAI
    base = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    key  = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("Missing DASHSCOPE_API_KEY")
    return OpenAI(base_url=base, api_key=key)

def clip_text(s: str, n: int) -> str:
    if not s or n <= 0: return s or ""
    return s if len(s) <= n else s[: n - 12] + " ... <omitted>"

def build_messages(record: dict, include_src: bool, clip_src: int):
    # 仅发送必要证据 + 可选上下文（prompt/solutions）
    payload = {
        "record": {
            "task_id": record.get("task_id"),
            "error_type": record.get("error_type"),
            "tests": record.get("tests") or [],
            "message": record.get("message") or []
        }
    }
    if include_src:
        payload["record"]["prompt"] = clip_text(record.get("prompt") or "", clip_src)
        payload["record"]["model_solution"] = clip_text(record.get("model_solution") or "", clip_src)
        payload["record"]["reference_solution"] = clip_text(record.get("reference_solution") or "", clip_src)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
    ]

def infer_once(client, model, rec, include_src=True, clip_src=4000, max_tokens=220):
    # JSON Mode 约束只输出 JSON（结构化输出）：
    resp = client.chat.completions.create(
        model=model,
        messages=build_messages(rec, include_src, clip_src),
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )
    content = resp.choices[0].message.content or "{}"
    try:
        obj = json.loads(content)
    except Exception:
        # 兜底：严格结构，标 UNKNOWN
        obj = {
            "task_id": rec.get("task_id"),
            "error_type": rec.get("error_type"),
            "tests": rec.get("tests") or [],
            "classifications": [{"message_index": i, "subclass_id": "UNKNOWN"} for i in range(len(rec.get("message") or []))]
        }
    return {
        "task_id": obj.get("task_id", rec.get("task_id")),
        "error_type": obj.get("error_type", rec.get("error_type")),
        "tests": obj.get("tests", rec.get("tests") or []),
        "classifications": obj.get("classifications", []),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", type=Path, help="输入 JSONL（每行一个对象，禁止空行）")
    ap.add_argument("out", type=Path, help="输出 JSONL（只含精简结果）")
    ap.add_argument("--model", default="deepseek-r1", help="classifier model (paper: deepseek-r1)")
    ap.add_argument("--workers", type=int, default=6, help="并发工作线程数")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 条（调试）")
    ap.add_argument("--silent", action="store_true", help="不打印逐题进度")
    ap.add_argument("--no-src", dest="include_src", action="store_false",
                    help="不把 prompt/model_solution/reference_solution 发给模型（默认会发送）")
    ap.add_argument("--clip-src", type=int, default=4000,
                    help="发送前截断源码上下文到 N 字符（默认 4000）")
    ap.add_argument("--max-tokens", type=int, default=220, help="每条回复的最大 tokens（JSON Mode下较小即可）")
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
    total = len(records); start = time.time(); done=ok=fail=0

    with ThreadPoolExecutor(max_workers=args.workers) as ex, open(args.out, "w", encoding="utf-8") as fout:
        futmap = {}
        for r in records:
            fut = ex.submit(infer_once, client, args.model, r, args.include_src, args.clip_src, args.max_tokens)
            futmap[fut] = r
        for fut in as_completed(futmap):
            rec = futmap[fut]; tid = rec.get("task_id")
            try:
                res = fut.result()
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
                ok += 1
                subs = sorted({c.get("subclass_id","UNKNOWN") for c in (res.get("classifications") or [])})
                label = ",".join(subs) if subs else "UNKNOWN"
                status = label
            except Exception as e:
                fail += 1
                status = f"ERROR:{type(e).__name__}"
            done += 1
            if not args.silent:
                elapsed = time.time() - start
                rps = done / elapsed if elapsed > 0 else 0.0
                print(f"[{done}/{total} | {elapsed:.1f}s | {rps:.2f} rps] {tid} -> {status}", flush=True)

    if not args.silent:
        elapsed = time.time() - start
        print(f"Done. total={total}, ok={ok}, fail={fail}, time={elapsed:.2f}s, avg={total/elapsed:.2f} rps")

    if fail:
        raise SystemExit(f"Classification failed for {fail} record(s); output contains successful records only.")

if __name__ == "__main__":
    main()
