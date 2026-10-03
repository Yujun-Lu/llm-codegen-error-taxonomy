# -*- coding: utf-8 -*-
from pathlib import Path
import os, json, time, argparse, re, sys
from typing import Dict, List
from datasets import load_dataset
from openai import OpenAI
from openai import BadRequestError, RateLimitError, APIConnectionError

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--release", default=os.environ.get("LCB_RELEASE", "release_v6"))
    p.add_argument("--model",   default=os.environ.get("MODEL", "qwen3-coder-plus"))
    p.add_argument("--in_json", required=True, help="已有的部分输出，例如 qwen-plus_lcb_v6_partial.json")
    p.add_argument("--out_json", default="outputs/livecodebench/samples_resumed.json")
    p.add_argument("--temperature", type=float, default=float(os.environ.get("LCB_TEMPERATURE", "0") or 0))
    p.add_argument("--max_input_tokens", type=int, default=int(os.environ.get("LCB_MAX_INPUT_TOKENS", "129024") or 129024))
    p.add_argument("--checkpoint_every", type=int, default=10)
    args = p.parse_args()
    if args.checkpoint_every < 1:
        p.error("--checkpoint_every must be at least 1")
    return args

SAFE_KEEP = {"question_content", "question", "prompt", "description", "problem_statement", "title"}
BAN_KEYS = re.compile(r"(hidden|private)?_?tests?|judge|checker|solutions?|ground[_-]?truth|inputs?_?outputs?", re.I)

SYS = ("You are a competitive programming assistant. "
       "Write a complete, efficient Python 3 program that reads from standard input and prints to standard output. "
       "Do NOT include explanations or markdown; output only valid Python code.")

def extract_prompt(ex):
    for k in ["question_content","prompt","question","description","problem_statement"]:
        if k in ex and isinstance(ex[k], str) and len(ex[k])>10:
            return ex[k]
    pieces = [ex[k] for k in SAFE_KEEP if k in ex and isinstance(ex[k], str) and len(ex[k])>10]
    if pieces: return "\n\n".join(pieces)
    # fallback: 保守截断，避免过长
    shorts = [v for k,v in ex.items() if isinstance(v,str) and not BAN_KEYS.search(k) and len(v)<=8000]
    return ("\n\n".join(shorts))[:50000]

def clean_code(txt:str)->str:
    if not txt: return ""
    if "```" in txt:
        parts = txt.split("```")
        if len(parts)>=2:
            code = parts[1]
            return re.sub(r"^\s*python", "", code, flags=re.I).lstrip()
    return txt

def ask(client, model, temperature, max_input_tokens, prompt):
    try:
        r = client.chat.completions.create(
            model=model, temperature=temperature, n=1,
            messages=[{"role":"system","content":SYS},{"role":"user","content":prompt}],
            extra_body={"max_input_tokens": max_input_tokens}
        )
        return clean_code(r.choices[0].message.content or "")
    except BadRequestError as e:
        if "Range of input length should be" in str(e):
            trimmed = prompt[:150000]
            r = client.chat.completions.create(
                model=model, temperature=temperature, n=1,
                messages=[{"role":"system","content":SYS},{"role":"user","content":trimmed}],
                extra_body={"max_input_tokens": max_input_tokens}
            )
            return clean_code(r.choices[0].message.content or "")
        raise

def main():
    args = parse_args()
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    # 1) 读取基准题目（必须与 release 匹配）
    ds = load_dataset("livecodebench/code_generation_lite",
                      version_tag=args.release,
                      trust_remote_code=True)  # 脚本型数据集需要显式信任
    split = "test" if "test" in ds else "train"
    problems = list(ds[split])  # 保序

    # 2) 读取已完成的部分输出，建立 question_id -> code
    with open(args.in_json, "r", encoding="utf-8") as f:
        partial = json.load(f)
    have: Dict[str, List[str]] = {str(x["question_id"]): x["code_list"] for x in partial if "question_id" in x and "code_list" in x}

    # 3) 遍历基准顺序，对缺失题补齐
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=os.environ.get("OPENAI_BASE_URL"))
    full_outputs = []
    missing = 0
    for i, ex in enumerate(problems):
        qid = str(ex.get("question_id") or ex.get("id") or ex.get("qid") or f"idx_{i}")
        if qid in have:
            full_outputs.append({"question_id": qid, "code_list": have[qid]})
            continue
        # 生成缺失题
        prompt = extract_prompt(ex)
        for trial in range(4):
            try:
                code = ask(client, args.model, args.temperature, args.max_input_tokens, prompt)
                break
            except (RateLimitError, APIConnectionError):
                time.sleep(2.0*(trial+1))
        else:
            code = ""
        full_outputs.append({"question_id": qid, "code_list": [code]})
        missing += 1
        if missing % args.checkpoint_every == 0:
            tmp = args.out_json + ".partial.json"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(full_outputs, f, ensure_ascii=False, indent=2)
            print(f"[resume] completed {i+1}/{len(problems)}, newly generated={missing}, checkpoint -> {tmp}")

    # 4) 按基准顺序保存完整 JSON（custom_evaluator 要求数量 & 顺序一致）
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(full_outputs, f, ensure_ascii=False, indent=2)
    print(f"[done] wrote full outputs -> {args.out_json}  total={len(full_outputs)}  newly_generated={missing}")

if __name__ == "__main__":
    main()
