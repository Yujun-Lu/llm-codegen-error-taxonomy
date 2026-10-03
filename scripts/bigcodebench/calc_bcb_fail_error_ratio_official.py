# calc_bcb_fail_error_ratio_official.py
# -*- coding: utf-8 -*-

import json, re, ast, argparse
from pathlib import Path
from typing import Dict, Set, List
from statistics import mean

# ---------- regex ----------
TEST_NAME_KEY_RE = re.compile(r"^test[\w\d_]*$")            # e.g., test_return_type / test_case_1
TEST_NAME_IN_MSG_RE = re.compile(r"\bin\s+(test[\w\d_]*)\b") # ... "in test_return_type" ...

# ---------- parse official test source ----------
def count_tests_from_source(test_src: str) -> int:
    try:
        node = ast.parse(test_src)
        n = 0
        # class methods
        for cls in [x for x in node.body if isinstance(x, ast.ClassDef)]:
            for f in [m for m in cls.body if isinstance(m, ast.FunctionDef)]:
                if f.name.startswith("test"):
                    n += 1
        # module-level test functions
        for f in [x for x in node.body if isinstance(x, ast.FunctionDef)]:
            if f.name.startswith("test"):
                n += 1
        if n > 0:
            return n
    except SyntaxError:
        pass
    # permissive fallback
    return len(re.findall(r"(?m)^\s*def\s+test[\w\d_]*\s*\(", test_src))

def load_official_counts(dataset: str, version: str, task_ids: Set[str], streaming: bool) -> Dict[str, int]:
    from datasets import load_dataset
    counts: Dict[str, int] = {}
    if streaming:
        ds = load_dataset(dataset, split=version, streaming=True)
        for row in ds:
            tid = row.get("task_id")
            if tid in task_ids:
                counts[tid] = count_tests_from_source(row.get("test") or "")
    else:
        ds = load_dataset(dataset, split=version)
        for row in ds:
            tid = row["task_id"]
            if tid in task_ids:
                counts[tid] = count_tests_from_source(row.get("test") or "")
    return counts

# ---------- read & normalize eval_results ----------
def _as_attempt_list(x) -> List[dict]:
    if isinstance(x, list):
        return [a for a in x if isinstance(a, dict)]
    if isinstance(x, dict):
        return [x]
    return []

def read_eval_results(path: Path) -> Dict[str, List[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))

    # NEW: unwrap {"date": "...", "eval": {...}} if present
    if isinstance(data, dict) and "eval" in data and isinstance(data["eval"], (dict, list)):
        data = data["eval"]

    # normalize to {task_id: [attempts...]}
    if isinstance(data, list):
        norm = {}
        for a in data:
            norm.setdefault(a.get("task_id", "__UNKNOWN__"), []).append(a)
        return norm
    elif isinstance(data, dict):
        norm = {}
        for tid, val in data.items():
            norm[tid] = _as_attempt_list(val)
        return norm
    else:
        raise ValueError("Unsupported eval_results.json structure")

def extract_failed_tests(details: dict) -> Set[str]:
    names: Set[str] = set()
    for k, v in (details or {}).items():
        if isinstance(k, str) and TEST_NAME_KEY_RE.match(k):
            names.add(k)
        if isinstance(v, str):
            for name in TEST_NAME_IN_MSG_RE.findall(v):
                names.add(name)
    return names

def count_failed_tests(details: dict, total: int, task_id: str) -> int:
    """Reject incomplete or incompatible evidence instead of inventing a ratio."""
    failed_count = len(extract_failed_tests(details))
    if failed_count == 0:
        raise ValueError(f"{task_id}: failed attempt has no identifiable failed-test names; "
                         "cannot infer an error ratio from this evidence")
    if failed_count > total:
        raise ValueError(f"{task_id}: {failed_count} failed-test names exceed the official "
                         f"test count ({total}); check dataset version and evaluator evidence")
    return failed_count

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_results_json", type=Path)
    ap.add_argument("--dataset", default="bigcode/bigcodebench",
                    help="HF dataset name (use bigcode/bigcodebench-hard for hard split).")
    ap.add_argument("--version", default="v0.1.4", help="HF dataset split/version tag.")
    ap.add_argument("--streaming", action="store_true", help="Use HF streaming mode.")
    ap.add_argument("--per_attempt", action="store_true",
                    help="Include every failed attempt; default: per-task (first fail only).")
    ap.add_argument("--skip_missing", action="store_true",
                    help="Skip tasks whose official tests cannot be loaded.")
    args = ap.parse_args()

    eval_map = read_eval_results(args.eval_results_json)

    # collect failed attempts by task
    failed = {}
    for tid, attempts in eval_map.items():
        ats_fail = [a for a in attempts if isinstance(a, dict) and a.get("status") == "fail"]
        if ats_fail:
            failed[tid] = ats_fail

    if not failed:
        print("No failed tasks found.")
        return

    needed = set(failed.keys())
    counts = load_official_counts(args.dataset, args.version, needed, args.streaming)

    macro_list = []
    micro_failed = 0
    micro_total = 0
    missing = []

    for tid, attempts in failed.items():
        total = counts.get(tid, 0)
        if total <= 0:
            if args.skip_missing:
                missing.append(tid)
                continue
            raise RuntimeError(f"Cannot find official test count for {tid} — check --dataset/--version.")

        if not args.per_attempt:
            attempts = [attempts[0]]

        ratios = []
        for a in attempts:
            try:
                failed_cnt = count_failed_tests(a.get("details") or {}, total, tid)
            except ValueError as exc:
                ap.error(str(exc))
            ratios.append(failed_cnt / total)
            micro_failed += failed_cnt
            micro_total += total

        macro_list.extend(ratios if args.per_attempt else [mean(ratios)])

    macro_avg = mean(macro_list) if macro_list else 0.0
    micro_avg = (micro_failed / micro_total) if micro_total else 0.0

    print(f"Failed task count (mode={'per_attempt' if args.per_attempt else 'per_task'}): {len(macro_list)}")
    print(f"Macro average error ratio: {macro_avg:.6f}")
    print(f"Micro average error ratio: {micro_avg:.6f}")
    if missing:
        print(f"[WARN] Skipped {len(missing)} tasks without official tests (e.g., {missing[:5]}).")

if __name__ == "__main__":
    main()
