# -*- coding: utf-8 -*-
"""
Usage:
  python report_failures_from_eval_results.py \
    --eval-results /path/to/*_eval_results.json \
    --dataset mbpp \
    --out-csv /path/to/fail_report.csv

It prints a concise summary and (optionally) writes a CSV.
This tool executes model-generated code, including top-level statements, with
normal Python privileges. Use a disposable isolated environment without secrets
or sensitive mounts. Disabling input/print is not a security sandbox.
"""

import argparse
import json
import os
import sys
import inspect
import re
import warnings
from math import comb
from typing import Any, Dict, List, Tuple

# ---- silence noisy SyntaxWarning from candidate strings like "\w", "\."
warnings.filterwarnings("ignore", category=SyntaxWarning)

# ---- evaluation guards ----
MAX_OUT = 200_000  # 超过该规模的组合/排列/笛卡尔积，跳过执行，标记为 HugeCaseSkipped

def strip_fences(s: str) -> str:
    """Remove ```/```python fences if any."""
    if not isinstance(s, str):
        return ""
    s = s.strip()
    s = re.sub(r"^```(?:python)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def truncate(s: str, limit: int) -> str:
    if not isinstance(s, str):
        return s
    return s if len(s) <= limit else s[:limit] + " ...<truncated>..."

def exec_func_from_code(code: str, entry_point: str):
    """Execute candidate source and return its entry point; this is not sandboxed."""
    g: Dict[str, Any] = {
        "__name__": "__not_main__",          # 避免执行 if __name__ == "__main__"
        "__builtins__": __builtins__,
        "input": lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("input() disabled in analysis")
        ),
        # 如不想屏蔽打印可注释下一行
        "print": (lambda *a, **k: None),
    }
    prelude = "from typing import *\n"
    code = strip_fences(code)
    compiled = compile(prelude + code, "<candidate>", "exec")
    exec(compiled, g, g)
    fn = g.get(entry_point)
    if not callable(fn):
        raise RuntimeError(f"Entry point `{entry_point}` not defined as a callable.")
    return fn

def normalize_args_for_sig(raw, sig: inspect.Signature):
    """将失败用例 JSON 转成 (*args, **kwargs)，兼容 [[...]] 包裹与不同元数。"""
    params = [p for p in sig.parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    k = len(params)

    def _unwrap_one(x):
        if isinstance(x, (list, tuple)) and len(x) == 1:
            return x[0]
        return x

    raw1 = _unwrap_one(raw)
    if k <= 1:
        return (raw1,), {}

    if isinstance(raw1, (list, tuple)) and len(raw1) == k:
        return tuple(raw1), {}

    if isinstance(raw, (list, tuple)) and len(raw) == 1 and \
       isinstance(raw[0], (list, tuple)) and len(raw[0]) == k:
        return tuple(raw[0]), {}

    return (raw,), {}

def safe_call(fn, call_args, call_kwargs):
    try:
        out = fn(*call_args, **call_kwargs)
        return ("ok", out, None)
    except Exception as e:
        return ("err", None, e)

def classify_case(canonical_fn, cand_fn, raw_input):
    """Run canonical and candidate, compare; return (status, err_type, exp, got)."""
    sig = inspect.signature(cand_fn)
    call_args, call_kwargs = normalize_args_for_sig(raw_input, sig)

    st_e, exp, err_e = safe_call(canonical_fn, call_args, call_kwargs)
    if st_e == "err":
        exp = f"<canonical raised {type(err_e).__name__}>"

    st_c, got, err_c = safe_call(cand_fn, call_args, call_kwargs)
    if st_c == "err":
        return ("Exception", type(err_c).__name__, exp, None)

    try:
        ok = (got == exp)
    except Exception:
        ok = False
    if ok:
        return ("PassNow", None, exp, got)
    else:
        return ("WrongAnswer", None, exp, got)

def is_task_id(k: str) -> bool:
    if not isinstance(k, str):
        return False
    m = re.match(r'^([A-Za-z\+]+)/(\d+)$', k)  # allow HumanEval/123 or Mbpp/255
    if not m:
        return False
    prefix = m.group(1).lower()
    return prefix in ('humaneval', 'mbpp')

def is_fail_status(v):
    return isinstance(v, str) and v.strip().lower().startswith("fail")

def iter_records(results_obj):
    """
    Yield per-task records from various eval_results.json shapes:
    - list of dicts
    - dict keyed by task_id: {"HumanEval/1":[{...}], ...}
    - dict with container keys: {"eval": {...}}, {"results":[...]}, {"records":[...]}
    """
    # dive into "eval" if present
    if isinstance(results_obj, dict) and isinstance(results_obj.get("eval"), dict):
        for rec in iter_records(results_obj["eval"]):
            yield rec
        return

    if isinstance(results_obj, list):
        for r in results_obj:
            if isinstance(r, dict):
                yield r
        return

    if isinstance(results_obj, dict):
        task_like_keys = [k for k in results_obj.keys() if is_task_id(k)]
        if task_like_keys:
            for tid in task_like_keys:
                v = results_obj[tid]
                if isinstance(v, dict):
                    r = dict(v); r.setdefault("task_id", tid); yield r
                elif isinstance(v, list):
                    for r0 in v:
                        if isinstance(r0, dict):
                            r = dict(r0); r.setdefault("task_id", tid); yield r
            return

        for k in ("results", "records"):
            v = results_obj.get(k)
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict):
                        yield r
                return

        for v in results_obj.values():
            if isinstance(v, dict):
                if all(is_task_id(kk) for kk in v.keys()):
                    for rec in iter_records(v):
                        yield rec
                else:
                    yield v
            elif isinstance(v, list):
                for r in v:
                    if isinstance(r, dict):
                        yield r

def canonicalize_tid(tid: str) -> str:
    m = re.match(r'^([A-Za-z\+]+)/(\d+)$', tid or "")
    if not m:
        return tid
    prefix, num = m.group(1).lower(), m.group(2)
    if prefix == 'mbpp':
        return f"MBPP/{num}"
    if prefix == 'humaneval':
        return f"HumanEval/{num}"
    return tid

def lookup_problem(ds: Dict[str, Any], tid: str):
    """Robust lookup tolerant to different task_id casings."""
    cands = [tid]
    m = re.match(r'^([A-Za-z\+]+)/(\d+)$', tid or "")
    if m:
        pfx, num = m.group(1), m.group(2)
        cands += [f"{pfx}/{num}", f"{pfx.upper()}/{num}", f"{pfx.title()}/{num}"]
    cands.append(canonicalize_tid(tid))
    seen = set()
    for k in cands:
        if k and k not in seen:
            seen.add(k)
            if k in ds:
                return ds[k]
    return None

def is_combinatorial_bomb(candidate_code: str, raw_input) -> Tuple[bool, str]:
    """
    返回 (是否过大, 说明文字)。启发式支持：
    - combinations_with_replacement(items, n)  -> C(m+n-1, n)
    - permutations(items, r)                   -> 近似 P(m, r)
    - product(items, repeat=r)                 -> m**r
    """
    code = candidate_code

    def unwrap1(x):
        if isinstance(x, (list, tuple)) and len(x) == 1:
            return x[0]
        return x

    rin = unwrap1(raw_input)

    # combinations_with_replacement
    if "combinations_with_replacement" in code:
        try:
            vals = unwrap1(rin)
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                items, n = vals[0], vals[1]
                if isinstance(items, (list, tuple)) and isinstance(n, int) and n >= 0:
                    m = len(items)
                    count = comb(m + n - 1, n)  # Python docs: (n+r-1)!/(r!*(n-1)!)
                    if count > MAX_OUT:
                        return True, f"combrep ~ {count:,} items"
        except Exception:
            pass

    # permutations(items, r)
    if "permutations(" in code:
        try:
            vals = unwrap1(rin)
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                items, r = vals[0], vals[1]
                if isinstance(items, (list, tuple)) and isinstance(r, int) and 0 <= r <= len(items):
                    m = len(items)
                    approx = 1
                    for t in range(m - r + 1, m + 1):
                        approx *= t
                    if approx > MAX_OUT:
                        return True, f"perm ~ {approx:,} items"
        except Exception:
            pass

    # product(items, repeat=r)
    if "product(" in code and "repeat=" in code:
        try:
            vals = unwrap1(rin)
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                items, r = vals[0], vals[1]
                if isinstance(items, (list, tuple)) and isinstance(r, int) and r >= 0:
                    m = len(items)
                    approx = m ** r
                    if approx > MAX_OUT:
                        return True, f"product ~ {approx:,} items"
        except Exception:
            pass

    return False, ""

# ---- evalplus dataset loaders ----
def load_dataset(name: str) -> Dict[str, Dict[str, Any]]:
    try:
        from evalplus.data import get_human_eval_plus, get_mbpp_plus
    except ImportError as exc:
        raise RuntimeError("Install requirements-evalplus.txt to use the execution-based reporter") from exc
    name = name.lower()
    if name in ("humaneval", "humaneval+"):
        return get_human_eval_plus()
    if name in ("mbpp", "mbpp+"):
        return get_mbpp_plus()
    raise ValueError(f"Unknown dataset: {name}")

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-results", required=True,
                    help="Path to *_eval_results.json from evalplus")
    ap.add_argument("--dataset", default="humaneval",
                    choices=["humaneval", "humaneval+", "mbpp", "mbpp+"],
                    help="Dataset name")
    ap.add_argument("--out-csv", default=None,
                    help="Optional path to save a CSV summary")
    ap.add_argument("--out-jsonl", default=None, help="Optional path to save a JSONL summary")

    cli = ap.parse_args()

    print("WARNING: This reporter executes candidate code without a security sandbox. "
          "Use a disposable isolated environment without secrets or sensitive mounts.", file=sys.stderr)
    ds = load_dataset(cli.dataset)

    # Debug: show dataset key samples
    if ds:
        first5 = list(ds.keys())[:5]
        print("[debug] sample ds keys:", first5)

    with open(cli.eval_results, "r", encoding="utf-8") as f:
        results = json.load(f)

    # Debug overview
    if isinstance(results, dict) and "eval" in results and isinstance(results["eval"], dict):
        task_map = results["eval"]
        task_like = [k for k in task_map if is_task_id(k)]
        print(f"[debug] top has 'eval'; task-like keys under eval: {len(task_like)}")
    else:
        task_like = [k for k in results if is_task_id(k)]
        print(f"[debug] task-like keys (top-level): {len(task_like)}")

    rows: List[Dict[str, Any]] = []

    num_records = 0
    for rec in iter_records(results):
        num_records += 1
        tid = rec.get("task_id")
        if not is_task_id(tid):
            continue

        base_fail_tests = rec.get("base_fail_tests") or []
        plus_fail_tests = rec.get("plus_fail_tests") or []
        base_status = rec.get("base_status")
        plus_status = rec.get("plus_status")

        base_fail = bool(base_fail_tests) or is_fail_status(base_status)
        plus_fail = bool(plus_fail_tests) or is_fail_status(plus_status)
        if not (base_fail or plus_fail):
            continue

        prob = lookup_problem(ds, tid) or {}
        entry = prob.get("entry_point")
        prompt = prob.get("prompt", "")
        canonical_code = prob.get("canonical_solution", "")

        # Fallback 1: try to extract entry from prompt
        if not entry and prompt:
            m = re.search(r'def\s+(\w+)\s*\(', prompt)
            if m:
                entry = m.group(1)

        # Raw model outputs (for CSV)
        model_solution_src = rec.get("solution") or rec.get("completion") or ""
        reference_solution_src = canonical_code

        # Fallback 2: if still no entry, try from model code
        if not entry:
            m = re.search(r'def\s+(\w+)\s*\(', model_solution_src)
            if m:
                entry = m.group(1)

        # Restore prompt+completion semantics when needed
        candidate_code = model_solution_src
        if entry and not re.search(rf"\bdef\s+{re.escape(entry)}\s*\(", candidate_code) and prompt:
            candidate_code = prompt + "\n" + candidate_code

        # Compile candidate
        try:
            cand_fn = exec_func_from_code(candidate_code, entry)
        except Exception as e:
            rows.append({
                "task_id": tid, "which": "compile", "input": "",
                "error": type(e).__name__, "expected": "", "got": "",
                "entry_point": entry,
                "prompt": truncate(prompt.strip().replace("\n", " "), 800),
                "model_solution": truncate(model_solution_src, 8000),
                "reference_solution": truncate(reference_solution_src, 8000),
            })
            continue

        # Compile canonical
        can_fn = None
        if canonical_code and entry:
            try:
                can_fn = exec_func_from_code(canonical_code, entry)
            except Exception:
                try:
                    can_fn = exec_func_from_code(prompt + "\n" + canonical_code, entry)
                except Exception:
                    can_fn = None

        # Collect failing cases
        fail_cases: List[Tuple[str, Any]] = []
        for case in base_fail_tests:
            fail_cases.append(("base", case))
        for case in plus_fail_tests:
            fail_cases.append(("plus", case))
        if not fail_cases:
            which = "base" if base_fail else "plus"
            fail_cases.append((which, "<unknown-input>"))

        # Classify
        for which, raw_inp in fail_cases:
            # 0) combinatorial bomb guard
            is_bomb, note = is_combinatorial_bomb(model_solution_src, raw_inp)
            if is_bomb:
                rows.append({
                    "task_id": tid, "which": which, "input": repr(raw_inp),
                    "error": f"HugeCaseSkipped({note})",
                    "expected": "", "got": "",
                    "entry_point": entry,
                    "prompt": truncate(prompt.strip().replace("\n", " "), 800),
                    "model_solution": truncate(model_solution_src, 8000),
                    "reference_solution": truncate(reference_solution_src, 8000),
                })
                continue

            if raw_inp == "<unknown-input>":
                st, got, err = safe_call(cand_fn, tuple(), {})
                rows.append({
                    "task_id": tid, "which": which, "input": raw_inp,
                    "error": (type(err).__name__ if st == "err" else "WrongAnswer(?)"),
                    "expected": "", "got": ("" if st == "err" else repr(got)),
                    "entry_point": entry,
                    "prompt": truncate(prompt.strip().replace("\n", " "), 800),
                    "model_solution": truncate(model_solution_src, 8000),
                    "reference_solution": truncate(reference_solution_src, 8000),
                })
                continue

            if can_fn is None:
                sig = inspect.signature(cand_fn)
                call_args, call_kwargs = normalize_args_for_sig(raw_inp, sig)
                st, got, err = safe_call(cand_fn, call_args, call_kwargs)
                rows.append({
                    "task_id": tid, "which": which, "input": repr(raw_inp),
                    "error": (type(err).__name__ if st == "err" else "WrongAnswer(?)"),
                    "expected": "", "got": ("" if st == "err" else repr(got)),
                    "entry_point": entry,
                    "prompt": truncate(prompt.strip().replace("\n", " "), 800),
                    "model_solution": truncate(model_solution_src, 8000),
                    "reference_solution": truncate(reference_solution_src, 8000),
                })
            else:
                status, err_type, exp, got = classify_case(can_fn, cand_fn, raw_inp)
                rows.append({
                    "task_id": tid, "which": which, "input": repr(raw_inp),
                    "error": (err_type or status),
                    "expected": repr(exp), "got": repr(got),
                    "entry_point": entry,
                    "prompt": truncate(prompt.strip().replace("\n", " "), 800),
                    "model_solution": truncate(model_solution_src, 8000),
                    "reference_solution": truncate(reference_solution_src, 8000),
                })

    print(f"[debug] parsed {num_records} records; collected {len(rows)} failing cases.")

    # Print concise summary
    if not rows:
        print("No failing cases found.")
        return

    print(f"Found {len(rows)} failing cases. Showing first 10:")
    for r in rows[:10]:
        print(f"- {r['task_id']} [{r['which']}] input={r['input']} error={r['error']}")

    # Optional CSV
    if cli.out_csv:
        out_dir = os.path.dirname(cli.out_csv)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        from csv import DictWriter
        with open(cli.out_csv, "w", encoding="utf-8", newline="") as f:
            w = DictWriter(f, fieldnames=[
                "task_id", "which", "input", "error", "expected", "got",
                "entry_point", "prompt",
                "model_solution", "reference_solution"
            ])
            w.writeheader()
            w.writerows(rows)
        print("Saved CSV to:", cli.out_csv)

    if cli.out_jsonl:
        out_dir = os.path.dirname(cli.out_jsonl)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(cli.out_jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("Saved JSONL to:", cli.out_jsonl)

if __name__ == "__main__":
    main()
