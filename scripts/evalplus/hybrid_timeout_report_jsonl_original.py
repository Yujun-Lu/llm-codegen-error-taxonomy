# hybrid_timeout_report_jsonl.py
# -*- coding: utf-8 -*-
"""
混合（执行+静态）限时版失败归因脚本（JSONL 输出）

This tool executes model-generated code, including top-level statements. Process
timeouts and resource limits are not a security sandbox. Use a disposable
isolated environment without secrets or sensitive mounts.

特性：
- 每题总预算 --task-seconds（默认 60s），每用例 --case-seconds（默认 5s）
- 按顺序优先执行 base 失败用例，再在剩余预算内最多抽取 --max-plus-cases（默认 20）个 plus 用例
- 先用很小超时 --canon-seconds（默认 1.0s）执行 canonical 得到 expected，再执行候选拿到 got 并判定
- 子进程执行，超时强杀；POSIX 下可选 resource 限制 CPU/内存
- 组合爆炸（combinations/permutations/product）启发式跳过，记为 skipped_huge
- 无法或无需执行（语法错/缺入口/空输出）→ 直接 static_only

输出 JSONL 每行字段:
task_id, which, input, verdict, exc_type, duration_ms, bucket_static,
entry_point, prompt, model_solution, reference_solution, expected, got
"""

import argparse, json, os, re, ast, time, inspect, sys
from typing import Any, Dict, List, Tuple
from multiprocessing import Process, Queue, set_start_method
try:
    import resource  # POSIX only
    HAS_RESOURCE = True
except Exception:
    HAS_RESOURCE = False

# ---- EvalPlus loaders ----
try:
    from evalplus.data import get_human_eval_plus, get_mbpp_plus
except Exception:
    get_human_eval_plus = get_mbpp_plus = None

def load_dataset(name: str) -> Dict[str, Dict[str, Any]]:
    name = name.lower()
    if name in ("humaneval","humaneval+"):
        assert get_human_eval_plus is not None, "pip install -U evalplus"
        return get_human_eval_plus()
    if name in ("mbpp","mbpp+"):
        assert get_mbpp_plus is not None, "pip install -U evalplus"
        return get_mbpp_plus()
    raise ValueError(f"unknown dataset: {name}")

# ---- JSON traversal ----
def is_task_id(k: str) -> bool:
    return isinstance(k, str) and bool(re.match(r'^(HumanEval|MBPP|Mbpp)/\d+$', k))

def iter_records(results_obj):
    if isinstance(results_obj, dict) and isinstance(results_obj.get("eval"), dict):
        results_obj = results_obj["eval"]
    if isinstance(results_obj, list):
        for r in results_obj:
            if isinstance(r, dict): yield r
        return
    if isinstance(results_obj, dict):
        task_like = [k for k in results_obj if is_task_id(k)]
        if task_like:
            for tid in task_like:
                v = results_obj[tid]
                if isinstance(v, dict):
                    r = dict(v); r.setdefault("task_id", tid); yield r
                elif isinstance(v, list):
                    for r0 in v:
                        if isinstance(r0, dict):
                            r = dict(r0); r.setdefault("task_id", tid); yield r
            return
        for k in ("results","records"):
            v = results_obj.get(k)
            if isinstance(v, list):
                for r in v:
                    if isinstance(r, dict): yield r
                return
        for v in results_obj.values():
            if isinstance(v, dict): yield v
            elif isinstance(v, list):
                for r in v:
                    if isinstance(r, dict): yield r

# ---- static helpers ----
IMPORT_PAT = re.compile(
    r'^\s*(?:from\s+([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)\s+import\s+.*|import\s+([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*))',
    re.M
)

def strip_fences(s: str) -> str:
    if not isinstance(s, str): return ""
    s = s.strip()
    s = re.sub(r"^```(?:python)?\s*", "", s, flags=re.I)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()

def extract_entry_from_prompt_or_code(prompt: str, code: str, fallback: str|None) -> str|None:
    if fallback: return fallback
    m = re.search(r'def\s+(\w+)\s*\(', prompt or "", flags=re.I)
    if m: return m.group(1)
    m = re.search(r'def\s+(\w+)\s*\(', code or "", flags=re.I)
    if m: return m.group(1)
    return None

def guess_nameerror_kind(code: str) -> str|None:
    if re.search(r'\b(List|Dict|Tuple|Set|Deque|DefaultDict|Counter)\[', code) and \
       not re.search(r'\bfrom\s+typing\s+import\b|\bimport\s+typing\b', code):
        return "typing"
    used = {
        "math": r'\bmath\.', "re": r'\bre\.', "itertools": r'\bitertools\.',
        "collections": r'\bcollections\.(Counter|defaultdict|deque)\b',
        "functools": r'\bfunctools\.', "heapq": r'\bheapq\.', "bisect": r'\bbisect\.',
        "statistics": r'\bstatistics\.', "random": r'\brandom\.', "numpy": r'\b(np|numpy)\.',
    }
    present = set()
    for m in IMPORT_PAT.finditer(code):
        mod = (m.group(1) or m.group(2) or "").split('.')[0]
        if mod: present.add(mod)
    for mod, pat in used.items():
        if re.search(pat, code) and (mod not in present):
            return mod
    return None

def static_bucket(code: str, prompt: str, entry_point: str|None):
    if not code.strip(): return ("EmptyOutput", entry_point)
    try:
        ast.parse(code)
    except SyntaxError:
        return ("SyntaxError", entry_point)
    if not entry_point or not re.search(rf'\bdef\s+{re.escape(entry_point)}\s*\(', code):
        return ("EntryPointNotFound", entry_point)
    miss = guess_nameerror_kind(code)
    if miss == "typing": return ("Likely NameError (typing)", entry_point)
    if miss: return (f"Likely NameError (module:{miss})", entry_point)
    return ("Unknown/WrongAnswer", entry_point)

# ---- combinatorial bomb heuristics ----
from math import comb
MAX_OUT = 200_000  # 组合数量阈值（可调）

def unwrap1(x):
    return x[0] if isinstance(x, (list, tuple)) and len(x) == 1 else x

def looks_huge(code: str, raw_input) -> tuple[bool, str]:
    rin = unwrap1(raw_input)
    # combinations_with_replacement(items, n) -> C(m+n-1, n)
    if "combinations_with_replacement" in code:
        try:
            vals = unwrap1(rin)
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                items, n = vals[0], vals[1]
                if isinstance(items, (list, tuple)) and isinstance(n, int) and n >= 0:
                    m = len(items); cnt = comb(m+n-1, n)
                    if cnt > MAX_OUT: return True, f"combrep~{cnt:,}"
        except Exception: pass
    # permutations(items, r) -> m!/(m-r)! ~ 上界 m**r
    if "permutations(" in code:
        try:
            vals = unwrap1(rin)
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                items, r = vals[0], vals[1]
                if isinstance(items, (list, tuple)) and isinstance(r, int) and 0 <= r <= len(items):
                    m = len(items); approx = 1
                    for t in range(m-r+1, m+1): approx *= t
                    if approx > MAX_OUT: return True, f"perm~{approx:,}"
        except Exception: pass
    # product(items, repeat=r) -> m**r
    if "product(" in code and "repeat=" in code:
        try:
            vals = unwrap1(rin)
            if isinstance(vals, (list, tuple)) and len(vals) >= 2:
                items, r = vals[0], vals[1]
                if isinstance(items, (list, tuple)) and isinstance(r, int) and r >= 0:
                    m = len(items); approx = m**r
                    if approx > MAX_OUT: return True, f"product~{approx:,}"
        except Exception: pass
    return False, ""

# ---- child execution（canonical / candidate 通用）----
CHILD_PREAMBLE = """\
from typing import *
import sys
def _blocked_input(*a, **k):
    raise RuntimeError("input() disabled in analysis")
input = _blocked_input
print = (lambda *a, **k: None)
"""

def child_runner(code: str, entry: str, raw_input, q: Queue, cpu_sec: int|None, mem_mb: int|None):
    """在子进程里：编译 -> 依据函数签名归一化 raw_input -> 调用并返回结果或异常类型"""
    try:
        if HAS_RESOURCE:
            if cpu_sec is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec+1))
            if mem_mb is not None:
                bytes_ = int(mem_mb * 1024 * 1024)
                try: resource.setrlimit(resource.RLIMIT_AS, (bytes_, bytes_))
                except Exception: pass
        g = {"__name__":"__not_main__", "__builtins__":__builtins__}
        src = CHILD_PREAMBLE + code
        compiled = compile(src, "<candidate>", "exec")
        exec(compiled, g, g)
        fn = g.get(entry)
        if not callable(fn):
            q.put(("compile_error", f"Entry `{entry}` not callable")); return

        # 依据签名归一化参数
        import inspect as _insp
        sig = _insp.signature(fn)
        params = [p for p in sig.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        k = len(params)
        def _unwrap_one(x):
            return x[0] if isinstance(x, (list, tuple)) and len(x) == 1 else x
        r1 = _unwrap_one(raw_input)
        if k <= 1:
            call_args, call_kwargs = (r1,), {}
        elif isinstance(r1, (list, tuple)) and len(r1) == k:
            call_args, call_kwargs = tuple(r1), {}
        elif isinstance(raw_input, (list, tuple)) and len(raw_input) == 1 and \
             isinstance(raw_input[0], (list, tuple)) and len(raw_input[0]) == k:
            call_args, call_kwargs = tuple(raw_input[0]), {}
        else:
            call_args, call_kwargs = (raw_input,), {}

        out = fn(*call_args, **call_kwargs)
        # repr 结果，避免不可序列化
        try:
            out_repr = repr(out)
        except Exception:
            out_repr = "<unreprable>"
        q.put(("ok", out_repr))
    except Exception as e:
        q.put(("exception", type(e).__name__, repr(e)))

def run_in_child(code: str, entry: str, raw_input, timeout_sec: float,
                 posix_cpu_sec: int|None = 2, posix_mem_mb: int|None = 1024):
    q = Queue()
    p = Process(target=child_runner, args=(code, entry, raw_input, q,
                                           (posix_cpu_sec if HAS_RESOURCE else None),
                                           (posix_mem_mb if HAS_RESOURCE else None)))
    p.daemon = True
    t0 = time.time()
    p.start()
    p.join(timeout_sec)
    dur_ms = int((time.time() - t0) * 1000)
    if p.is_alive():
        p.terminate(); p.join(0.2)
        return ("timeout", None, dur_ms)
    try:
        msg = q.get_nowait()
    except Exception:
        return ("unknown", None, dur_ms)
    kind = msg[0]
    if kind == "ok":
        return ("ok", msg[1], dur_ms)
    if kind == "exception":
        return ("exception", msg[1], dur_ms)  # 异常类型名
    if kind == "compile_error":
        return ("compile_error", msg[1], dur_ms)
    return ("unknown", None, dur_ms)

# ---- main ----
def main():
    try:
        set_start_method("spawn", force=True)  # Windows/跨平台更稳
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-results", required=True)
    ap.add_argument("--dataset", default="mbpp", choices=["humaneval","humaneval+","mbpp","mbpp+"])
    ap.add_argument("--out-jsonl", required=True)
    ap.add_argument("--task-seconds", type=float, default=60.0, help="per-task max seconds")
    ap.add_argument("--case-seconds", type=float, default=5.0, help="per-case max seconds")
    ap.add_argument("--canon-seconds", type=float, default=1.0, help="per-case canonical run seconds")
    ap.add_argument("--max-plus-cases", type=int, default=20, help="max plus cases to run per task")
    ap.add_argument("--mem-mb", type=int, default=1024, help="POSIX RLIMIT_AS (child)")
    args = ap.parse_args()

    print("WARNING: This reporter executes candidate code without a security sandbox. "
          "Use a disposable isolated environment without secrets or sensitive mounts.", file=sys.stderr)
    ds = load_dataset(args.dataset)
    with open(args.eval_results, "r", encoding="utf-8") as f:
        results = json.load(f)

    os.makedirs(os.path.dirname(args.out_jsonl) or ".", exist_ok=True)
    out = open(args.out_jsonl, "w", encoding="utf-8")

    def write(obj: Dict[str, Any]):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # 遍历任务
    for rec in iter_records(results):
        tid = rec.get("task_id")
        if not is_task_id(tid):
            continue

        btests = rec.get("base_fail_tests") or []
        ptests = rec.get("plus_fail_tests") or []
        fail_any = bool(btests or ptests) or \
            str(rec.get("base_status","")).lower().startswith("fail") or \
            str(rec.get("plus_status","")).lower().startswith("fail")
        if not fail_any:
            continue

        prob = ds.get(tid) or {}
        prompt = prob.get("prompt","")
        entry  = prob.get("entry_point")
        model_src = rec.get("solution") or rec.get("completion") or ""
        ref_src   = prob.get("canonical_solution","")
        code = strip_fences(model_src)
        if not entry:
            entry = extract_entry_from_prompt_or_code(prompt, code, None)

        # completion 语义：如果没有入口定义，且有 prompt，拼回去再尝试
        if entry and not re.search(rf'\bdef\s+{re.escape(entry)}\s*\(', code) and prompt:
            code_for_exec = prompt + "\n" + code
        else:
            code_for_exec = code

        # 静态桶
        bucket_static, entry_for_static = static_bucket(code_for_exec, prompt, entry)
        entry = entry_for_static or entry

        # 组装执行队列：先 base，后 plus（受 --max-plus-cases 限制）
        cases: List[Tuple[str, Any]] = []
        cases += [("base", c) for c in btests]
        if ptests:
            max_plus = max(0, args.max_plus_cases)
            cases += [("plus", c) for c in ptests[:max_plus]]
        if not cases:
            which = "base" if str(rec.get("base_status","")).lower().startswith("fail") else "plus"
            cases = [(which, "<unknown-input>")]

        t_task0 = time.time()
        for which, raw_inp in cases:
            remaining = args.task_seconds - (time.time() - t_task0)
            if remaining <= 0:
                write({
                    "task_id": tid, "which": which, "input": raw_inp,
                    "verdict": "task_timeout", "exc_type": None, "duration_ms": None,
                    "bucket_static": bucket_static,
                    "entry_point": entry or "", "prompt": prompt,
                    "model_solution": model_src, "reference_solution": ref_src,
                    "expected": None, "got": None
                })
                break

            # 组合爆炸预检
            huge, note = looks_huge(code_for_exec, raw_inp)
            if huge:
                write({
                    "task_id": tid, "which": which, "input": raw_inp,
                    "verdict": "skipped_huge", "exc_type": note, "duration_ms": None,
                    "bucket_static": bucket_static,
                    "entry_point": entry or "", "prompt": prompt,
                    "model_solution": model_src, "reference_solution": ref_src,
                    "expected": None, "got": None
                })
                continue

            # 静态不可执行：直接记录
            if bucket_static in ("SyntaxError","EntryPointNotFound","EmptyOutput"):
                write({
                    "task_id": tid, "which": which, "input": raw_inp,
                    "verdict": "static_only", "exc_type": bucket_static, "duration_ms": None,
                    "bucket_static": bucket_static,
                    "entry_point": entry or "", "prompt": prompt,
                    "model_solution": model_src, "reference_solution": ref_src,
                    "expected": None, "got": None
                })
                continue

            # 在每用例预算与题级剩余额度之间取更小值
            per_case = max(0.1, min(args.case_seconds, remaining))

            # 先跑 canonical（很小 timeout）
            exp_status, exp_payload, exp_ms = ("unknown", None, None)
            if ref_src and entry:
                canon_code = strip_fences(ref_src)
                # 也支持 prompt + canonical，防止有些 canonical 依赖 prompt 中 import
                if not re.search(rf'\bdef\s+{re.escape(entry)}\s*\(', canon_code) and prompt:
                    canon_code = prompt + "\n" + canon_code
                exp_status, exp_payload, exp_ms = run_in_child(
                    canon_code, entry, raw_inp, timeout_sec=args.canon_seconds,
                    posix_cpu_sec=int(min(args.canon_seconds, 3)) if HAS_RESOURCE else None,
                    posix_mem_mb=args.mem_mb if HAS_RESOURCE else None
                )

            # 再跑候选
            cand_status, cand_payload, cand_ms = run_in_child(
                code_for_exec, entry or "", raw_inp, timeout_sec=per_case,
                posix_cpu_sec=int(min(per_case, 5)) if HAS_RESOURCE else None,
                posix_mem_mb=args.mem_mb if HAS_RESOURCE else None
            )

            # 归因
            expected = exp_payload if exp_status == "ok" else None
            got = cand_payload if cand_status == "ok" else None

            if cand_status == "timeout":
                verdict, exc = "timeout", None
            elif cand_status == "compile_error":
                verdict, exc = "compile_error", "EntryPoint"
            elif cand_status == "exception":
                verdict, exc = "exception", cand_payload  # 异常类型
            elif cand_status == "ok":
                if exp_status == "ok":
                    verdict = "PassNow" if (got == expected) else "WrongAnswer"
                    exc = None
                else:
                    # 没拿到 expected，但候选跑通了：仍标记 ran_ok_but_fail
                    verdict, exc = "ran_ok_but_fail", None
            else:
                verdict, exc = "unknown", None

            write({
                "task_id": tid, "which": which, "input": raw_inp,
                "verdict": verdict, "exc_type": exc,
                "duration_ms": {"candidate": cand_ms, "canonical": exp_ms},
                "bucket_static": bucket_static,
                "entry_point": entry or "", "prompt": prompt,
                "model_solution": model_src, "reference_solution": ref_src,
                "expected": expected, "got": got
            })

    out.close()
    print("Saved JSONL to:", args.out_jsonl)

if __name__ == "__main__":
    main()
