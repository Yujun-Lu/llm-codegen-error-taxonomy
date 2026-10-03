#!/usr/bin/env python3
import argparse, json, re, os
from pathlib import Path
from collections import defaultdict

# ---- helpers ----

def prefix_of(fname: str) -> str:
    """共同前缀：去掉 -sanitized_calibrated 后面的尾巴"""
    for suf in (
        "-sanitized_calibrated_eval_results.json",
        "-sanitized_calibrated_pass_at_k.json",
        "-sanitized_calibrated.jsonl",
    ):
        if fname.endswith(suf):
            return fname[: -len(suf)]
    # 兜底：如果没匹配到已知后缀，就按第一个已知分隔切
    i = fname.find("-sanitized_calibrated")
    return fname if i < 0 else fname[:i]

def parse_model_and_split_from_prefix(prefix: str):
    """
    兼容你改过的命名：…--bigcodebench-instruct--…
    从前缀中解析：
      model_tag: 前半段（--bigcodebench- 之前）
      split: instruct/complete（从 bigcodebench- 之后的那段取）
    """
    # 示例：deepseek-v3.1--main--bigcodebench-instruct--openai-0-1
    parts = prefix.split("--bigcodebench-")
    model_tag = parts[0] if len(parts) >= 2 else prefix
    rest = parts[1] if len(parts) >= 2 else ""
    if rest.startswith("hard-"):
        rest = rest[len("hard-"):]
    split = "instruct" if rest.startswith("instruct") else ("complete" if rest.startswith("complete") else None)
    return model_tag, split

ERR_RE = re.compile(r"(?:^|\n)([A-Za-z_][A-Za-z0-9_]*(Error|Exception))\s*:")

def extract_err_type(status: str, msg: str) -> str:
    if status == "timeout":
        return "timeout"
    if status == "pass":
        return ""
    if not msg:
        return "UnknownError"
    m = ERR_RE.search(msg)
    return m.group(1) if m else "UnknownError"

def load_bcb_dataset(subset: str, version: str = "v0.1.4"):
    """按 subset 加载 HF 数据集（full: bigcode/bigcodebench, hard: bigcode/bigcodebench-hard）"""
    import pandas as pd
    ds_name = "bigcode/bigcodebench-hard" if subset == "hard" else "bigcode/bigcodebench"
    try:
        from datasets import load_dataset
        ds = {version: load_dataset(ds_name, split=version)}
        rows = []
        for split_name in ds:
            for ex in ds[split_name]:
                tid = ex.get("task_id")
                if not tid:
                    continue
                rows.append({
                    "task_id": tid,
                    "instruct_prompt": ex.get("instruct_prompt"),
                    "complete_prompt": ex.get("complete_prompt"),
                    "canonical_solution": ex.get("canonical_solution"),
                    "entry_point": ex.get("entry_point"),
                    "test_code": ex.get("test"),
                })
        df = pd.DataFrame(rows).drop_duplicates("task_id")
        return df.set_index("task_id")
    except Exception as e:
        print(f"[WARN] load_dataset({ds_name}) failed: {e}")
        return pd.DataFrame(index=pd.Index([], name="task_id"))

def read_samples_map(jsonl_path: Path):
    """从样本 jsonl 读取 task_id -> solution/raw_solution"""
    m = {}
    if not jsonl_path.exists():
        return m
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            tid = obj.get("task_id")
            if not tid:
                continue
            m[tid] = {
                "solution": obj.get("solution"),
                "raw_solution": obj.get("raw_solution"),
            }
    return m

def flatten_eval(eval_path: Path):
    """把 *_eval_results.json 展平成行"""
    data = json.loads(eval_path.read_text(encoding="utf-8"))
    dt = data.get("date")
    eval_map = data.get("eval", {})
    rows = []
    for task_id, lst in eval_map.items():
        for item in lst:
            details = item.get("details") or {}
            msgs = []
            for k in sorted(details.keys()):
                msgs.append(f"{k}: {details[k]}")
            rows.append({
                "task_id": task_id,
                "status": item.get("status"),
                "error_msg": "\n\n".join(msgs),
                "eval_solution": item.get("solution"),
                "date": dt,
            })
    return rows

def discover_runs(root: Path):
    """Find original evaluator outputs or the normalized release directories."""
    runs = []
    for eval_path in sorted(root.glob("*-sanitized_calibrated_eval_results.json")):
        prefix = prefix_of(eval_path.name)
        metrics_path = root / f"{prefix}-sanitized_calibrated_pass_at_k.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        model_tag, split = parse_model_and_split_from_prefix(prefix)
        runs.append({"eval_path": eval_path,
                     "samples_path": root / f"{prefix}-sanitized_calibrated.jsonl",
                     "metrics": metrics, "model_tag": model_tag,
                     "split": metrics.get("split") or split,
                     "subset": metrics.get("subset") or "unknown"})
    for eval_path in sorted(root.rglob("eval_results.json")):
        directory = eval_path.parent
        metrics_path = directory / "pass_at_k.json"
        if not metrics_path.exists():
            raise ValueError(f"Normalized archive requires sibling pass_at_k.json: {eval_path}")
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        model_tag = metrics.get("model") or directory.name
        mode = directory.parent.name
        if mode in {"thinking", "non-thinking"}:
            model_tag = f"{model_tag} ({mode})"
        runs.append({"eval_path": eval_path, "samples_path": directory / "samples.jsonl",
                     "metrics": metrics, "model_tag": model_tag,
                     "split": metrics.get("split"), "subset": metrics.get("subset") or "unknown"})
    return runs

# ---- main ----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bcb_dir", required=True,
                    help="original evaluator directory or normalized results/bigcodebench tree")
    ap.add_argument("--out_prefix", default="./bcb", help="output prefix")
    ap.add_argument("--version", default="v0.1.4", help="BigCodeBench dataset split used for the archived experiments")
    args = ap.parse_args()
    Path(args.out_prefix).parent.mkdir(parents=True, exist_ok=True)

    root = Path(args.bcb_dir)
    assert root.is_dir(), f"{root} not found"

    try:
        runs = discover_runs(root)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    if not runs:
        ap.error(f"No original or normalized BigCodeBench evaluation files in {root}")
    import pandas as pd

    ds_cache = {}
    rows = []

    for run in runs:
        ef = run["eval_path"]
        subset, model_tag, split = run["subset"], run["model_tag"], run["split"]

        # 对应的 samples
        samples_map = read_samples_map(run["samples_path"])

        # 数据集（按 subset 缓存一次）
        if subset not in ds_cache:
            ds_cache[subset] = load_bcb_dataset(subset, args.version)
        ds_df = ds_cache[subset]

        for it in flatten_eval(ef):
            tid = it["task_id"]

            # 模型解答：优先样本 jsonl，其次 eval 自带 solution
            model_solution = None
            if tid in samples_map:
                model_solution = samples_map[tid].get("solution")
            if not model_solution:
                model_solution = it.get("eval_solution")

            # 元信息
            prompt = entry_point = ref_sol = test_code = None
            if not ds_df.empty and tid in ds_df.index:
                meta_row = ds_df.loc[tid]
                prompt = meta_row["instruct_prompt"] if split == "instruct" else meta_row["complete_prompt"]
                entry_point = meta_row["entry_point"]
                ref_sol = meta_row["canonical_solution"]
                test_code = meta_row["test_code"]

            rows.append({
                "task_id": tid,
                "subset": subset,              # 从 pass_at_k.json 得到
                "model_tag": model_tag,
                "status": it["status"],
                "error_type": extract_err_type(it["status"], it["error_msg"]),
                "error_msg": it["error_msg"],
                "prompt": prompt,
                "entry_point": entry_point,
                "model_solution": model_solution,
                "reference_solution": ref_sol,
                "test_code": test_code,
                "date": it["date"],
            })

    if not rows:
        ap.error("The matched evaluation files contain no evaluation records")
    df = pd.DataFrame(rows)

    # 输出 1：CSV（逐题汇总；保留 pass/fail 全量）
    out_summary = f"{args.out_prefix}_summary.csv"
    df.to_csv(out_summary, index=False)
    print(f"[OK] wrote {out_summary}  rows={len(df)}")

    # 输出 1b：JSONL（只保留 fail）
    out_jsonl = f"{args.out_prefix}_summary.jsonl"
    with open(out_jsonl, "w", encoding="utf-8") as w:
        for rec in df[df["status"] == "fail"].to_dict(orient="records"):
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[OK] wrote {out_jsonl}  rows={len(df[df['status']=='fail'])}")

    # 输出 2：错误类型分布
    if not df.empty:
        grp = ["model_tag","subset"]
        err = (
            df.assign(error_type=df["error_type"].fillna("OK"))
              .groupby(grp + ["status","error_type"], dropna=False)
              .size().reset_index(name="count")
        )
        # 用 transform 对齐索引
        err["ratio"] = err["count"] / err.groupby(grp)["count"].transform("sum")
        out_err = f"{args.out_prefix}_error_stats.csv"
        err.to_csv(out_err, index=False)
        print(f"[OK] wrote {out_err}")

    # 输出 3：pass@1 摘要（同时也能核对 subset）
    pass_rows = []
    for run in runs:
        obj = run["metrics"]
        if not obj:
            continue
        pass_rows.append({
            "model_tag": run["model_tag"],
            "subset": obj.get("subset"),
            "split": obj.get("split"),
            "pass@1": obj.get("pass@1"),
            "gt_pass_rate": obj.get("gt_pass_rate"),
            "failed_tasks": ",".join(obj.get("failed_tasks", [])),
        })
    if pass_rows:
        pdf = pd.DataFrame(pass_rows)
        out_pass = f"{args.out_prefix}_pass_stats.csv"
        pdf.to_csv(out_pass, index=False)
        print(f"[OK] wrote {out_pass}")

if __name__ == "__main__":
    main()
