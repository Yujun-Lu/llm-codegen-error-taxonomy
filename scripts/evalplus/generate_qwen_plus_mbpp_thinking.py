# -*- coding: utf-8 -*-
"""
Generate MBPP+ samples with qwen-plus (chat + thinking), feeding problem['prompt'] as-is.

Outputs:
- outputs/evalplus/mbpp/<TAG>.jsonl         -> {"task_id","completion"} (for EvalPlus)
- outputs/evalplus/mbpp/<TAG>_raw.jsonl     -> {"task_id","reasoning","answer"} (for analysis)

Notes:
- Thinking mode (enable_thinking) must be used with streaming; read reasoning_content/content from chunks.
- We DO NOT log token usage in this script.
"""

import os, re
from openai import OpenAI
from evalplus.data import get_mbpp_plus, write_jsonl

# ---------- Config ----------
BASE_URL = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
API_KEY  = os.getenv("DASHSCOPE_API_KEY")
MODEL    = os.getenv("MODEL", os.getenv("QWEN_MODEL", "qwen-plus"))

OUT_DIR  = os.getenv("OUT_DIR", os.path.join("outputs", "evalplus", "mbpp"))
TAG      = os.getenv("TAG", f"{MODEL}_thinking_chat_temp_0.0")
SOL_PATH = os.path.join(OUT_DIR, f"{TAG}.jsonl")
RAW_PATH = os.path.join(OUT_DIR, f"{TAG}_raw.jsonl")

# 可选：是否自动补 typing 导入（避免 List/Dict 未导入导致的 NameError），默认关闭以保持分布一致
AUTO_IMPORT_TYPING = os.getenv("AUTO_IMPORT_TYPING", "0") == "1"

# ---------- Helpers ----------
def strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:python)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()

_TYPING_SYMBOLS = ["List", "Optional", "Tuple", "Dict", "Set", "Deque", "DefaultDict", "Counter"]
def ensure_typing_imports(code: str) -> str:
    # 若代码使用了 typing 泛型且没有导入，则插入一行统一导入
    has_import = bool(re.search(r"^\s*from\s+typing\s+import\s+", code, flags=re.MULTILINE)) \
                 or bool(re.search(r"^\s*import\s+typing\b", code, flags=re.MULTILINE))
    if has_import:
        return code
    used = [sym for sym in _TYPING_SYMBOLS if re.search(rf"\b{sym}\[", code)]
    if not used:
        return code
    header = "from typing import " + ", ".join([sym for sym in _TYPING_SYMBOLS if sym in used]) + "\n"
    lines = code.splitlines()
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    if insert_at < len(lines) and re.match(r"^#.*coding[:=]\s*utf-8", lines[insert_at], flags=re.IGNORECASE):
        insert_at += 1
    lines.insert(insert_at, header.rstrip())
    return "\n".join(lines)

def sanitize(code: str) -> str:
    code = strip_fences(code)
    if AUTO_IMPORT_TYPING:
        code = ensure_typing_imports(code)
    return code

# ---------- Generator ----------
# ... 省略 import 与配置

def gen_one(client: OpenAI, task_id: str, prompt: str):
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0, top_p=1,
        stream=True,
        extra_body={"enable_thinking": True},
    )
    think_chunks, answer_chunks = [], []
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        rc = getattr(delta, "reasoning_content", None)
        if rc: think_chunks.append(rc)
        cc = getattr(delta, "content", None)
        if cc: answer_chunks.append(cc)

    return {
        "task_id": task_id,
        "raw_reasoning": "".join(think_chunks),
        "raw_answer": "".join(answer_chunks),
    }

def extract_code(ans: str, entry_point: str | None) -> str:
    if not isinstance(ans, str):
        return ""
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", ans, flags=re.S | re.I)
    if blocks:
        if entry_point:
            ep = re.compile(rf"\bdef\s+{re.escape(entry_point)}\s*\(", re.I)
            cand = [b for b in blocks if ep.search(b)]
            code = (cand[0] if cand else max(blocks, key=len))
        else:
            code = max(blocks, key=len)
        return code.strip()
    if entry_point:
        m = re.search(rf"(^|\n)(?P<code>.*?def\s+{re.escape(entry_point)}\s*\(.*)", ans, flags=re.S | re.I)
        if m:
            return m.group("code").strip()
    s = ans.strip()
    s = re.sub(r"^```(?:python)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def main():
    assert API_KEY, "Please set DASHSCOPE_API_KEY"
    os.makedirs(OUT_DIR, exist_ok=True)
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    dataset = get_mbpp_plus()
    sols, raws = [], []
    for task_id, problem in dataset.items():
        rec = gen_one(client, task_id, problem["prompt"])
        entry = problem.get("entry_point")
        code_only = extract_code(rec["raw_answer"], entry)
        if AUTO_IMPORT_TYPING:
            code_only = ensure_typing_imports(code_only)
        sols.append({"task_id": task_id, "completion": code_only})
        raws.append({"task_id": task_id, "reasoning": rec["raw_reasoning"], "answer": rec["raw_answer"]})

    write_jsonl(SOL_PATH, sols)
    write_jsonl(RAW_PATH, raws)
    print("Wrote:", SOL_PATH)
    print("Wrote:", RAW_PATH)


if __name__ == "__main__":
    main()
