# lcb_badcases_to_csv.py
import argparse, json, re, csv, sys, pathlib

# === 配置：把文件名改成你的 ===
EVAL_ALL = "qwen-plus_lcb_v6_partial_codegeneration_output_eval_all.json"
OUT_JSON = "qwen-plus_lcb_v6_partial_codegeneration_output.json"
BADCASES_CSV = "badcases.csv"
SUMMARY_CSV = "summary.csv"

def load_json_any(path):
    txt = pathlib.Path(path).read_text(encoding="utf-8")
    txt = txt.strip()
    if txt.startswith("["):
        return json.loads(txt)
    # 兼容 jsonlines（不太可能用到）
    return [json.loads(line) for line in txt.splitlines() if line.strip()]

def safe_meta_list(meta):
    # meta 是 ["{\"execution time\": 0.08}", "{\"output\":..., \"error_message\":\"Wrong answer ...\"}"]
    parsed = []
    for m in (meta or []):
        if isinstance(m, dict):
            parsed.append(m)
            continue
        try:
            parsed.append(json.loads(m))
        except Exception:
            parsed.append({"raw": m})
    return parsed

def classify_failure(question_content:str, code:str, meta_list:list):
    emsg = ""
    # 优先依据 metadata
    for m in meta_list:
        if isinstance(m, dict):
            if "error_message" in m:
                emsg = m["error_message"] or ""
                break
            if "timeout" in m and m["timeout"]:
                return "Timeout", "timeout"
    t = (emsg or "").lower()

    # 明确类别
    if "wrong answer" in t:
        return "WrongAnswer", emsg
    if "time limit" in t or "timeout" in t or "alarm went off" in t:
        return "Timeout", emsg
    if "traceback" in t or "exception" in t or "error:" in t:
        if "syntaxerror" in t:
            return "SyntaxError", emsg
        return "RuntimeError", emsg
    if "syntaxerror" in t:
        return "SyntaxError", emsg
    if "output" in t and "expected" in t and "!=" in t:
        return "WrongAnswer", emsg

    # 代码/题面启发式
    ql = (question_content or "").lower()
    code_l = (code or "").lower()

    # 多测例遗漏
    if ("number of test cases" in ql or re.search(r"\bnumber of test case", ql)
        or re.search(r"\bfirst line.*integer.*t\b", ql)):
        if not re.search(r"for\s+_?\s*in\s+range\(\s*t\s*\)", code_l):
            return "MultiCaseMissing", "likely missing loop over t test cases"

    # I/O 解析错误（非常粗略）
    if ("input" in ql and "output" in ql) and (
        ("readline" in code_l and "split" not in code_l) or
        ("map(int, input())" in code_l)  # 常见错误：少了 .split()
    ):
        return "IOParsing", "suspicious input parsing"

    # Markdown/格式
    if "```" in code or ("print(" not in code and "return" not in code and len(code.strip())<5):
        return "OutputFormat", "markdown or empty/trivial code"

    # 空/截断
    if not code or len(code.strip()) < 2:
        return "EmptyOrTruncated", "empty or too short"

    return "Other", emsg

def main():
    parser = argparse.ArgumentParser(description="Export archived LiveCodeBench failures; does not execute code.")
    parser.add_argument("--eval-all", default=EVAL_ALL, type=pathlib.Path)
    parser.add_argument("--outputs", default=OUT_JSON, type=pathlib.Path)
    parser.add_argument("--badcases-csv", default=BADCASES_CSV, type=pathlib.Path)
    parser.add_argument("--summary-csv", default=SUMMARY_CSV, type=pathlib.Path)
    args = parser.parse_args()
    inputs = [args.eval_all, args.outputs]
    outputs = [args.badcases_csv, args.summary_csv]
    for index, output in enumerate(outputs):
        for other in inputs + outputs[:index]:
            if (output.resolve() == other.resolve() or
                    (output.exists() and other.exists() and output.samefile(other))):
                parser.error("Input and output must be different files, and output files must be distinct")
    eval_all = load_json_any(args.eval_all)
    outs = load_json_any(args.outputs)
    if len(eval_all) != len(outs):
        parser.error("Evaluation and output files must have the same number of records")
    n = len(outs)
    for i, (evaluation, output) in enumerate(zip(eval_all, outs)):
        if ("question_id" in evaluation and "question_id" in output and
                str(evaluation["question_id"]) != str(output["question_id"])):
            parser.error(f"question_id mismatch at record {i}; files must have matching order")
    args.badcases_csv.parent.mkdir(parents=True, exist_ok=True)
    args.summary_csv.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    fail_counts = {}
    total = 0
    passed = 0

    for i in range(n):
        a = eval_all[i]
        b = outs[i]
        title = b.get("question_title")
        qid = b.get("question_id")
        plat = b.get("platform")
        diff = b.get("difficulty")
        date = b.get("contest_date")
        code = (b.get("code_list") or [""])[0]
        meta_list = safe_meta_list(a.get("metadata"))
        passed_i = bool(a.get("pass@1", False))
        total += 1
        if passed_i:
            passed += 1
            continue

        fail_type, reason = classify_failure(b.get("question_content",""), code, meta_list)
        fail_counts[fail_type] = fail_counts.get(fail_type, 0) + 1

        # 尽量取到“最有信息”的一条 metadata
        err_text = ""
        for m in meta_list:
            if isinstance(m, dict) and ("error_message" in m or "output" in m):
                err_text = json.dumps(m, ensure_ascii=False)
                break
        if not err_text and meta_list:
            err_text = json.dumps(meta_list[0], ensure_ascii=False)

        rows.append({
            "question_id": qid,
            "platform": plat,
            "difficulty": diff,
            "contest_date": date,
            "title": title,
            "pass@1": 0,
            "fail_type": fail_type,
            "reason": reason,
            "metadata": err_text,
            "code_preview": (code[:2000] + ("..." if len(code)>2000 else "")),
        })

    # 导出 badcases.csv
    with open(args.badcases_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["question_id","platform","difficulty","contest_date","title","pass@1","fail_type","reason","metadata","code_preview"])
        w.writeheader()
        w.writerows(rows)

    # 导出 summary.csv
    remain = total - passed
    with open(args.summary_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["total_items", total])
        w.writerow(["passed", passed])
        w.writerow(["failed", remain])
        w.writerow([])
        w.writerow(["fail_type","count","ratio_of_all","ratio_of_fails"])
        for k,v in sorted(fail_counts.items(), key=lambda kv: -kv[1]):
            w.writerow([k, v, f"{v/total:.3f}", f"{v/max(remain,1):.3f}"])

    pass_rate = f"{passed/total:.3f}" if total else "n/a (empty input)"
    print(f"Saved {args.badcases_csv} ({len(rows)} fails) and {args.summary_csv}. Pass@1 = {pass_rate}")

if __name__ == "__main__":
    main()
