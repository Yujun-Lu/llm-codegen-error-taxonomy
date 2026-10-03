# -*- coding: utf-8 -*-
from pathlib import Path
import os, json, time, argparse, re
from datasets import load_dataset
from openai import OpenAI
from openai import BadRequestError, RateLimitError, APIConnectionError

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--release", default=os.environ.get("LCB_RELEASE", "release_v6"))
    p.add_argument("--model",   default=os.environ.get("MODEL", "deepseek-v3.1"))
    p.add_argument("--out", default=os.environ.get("OUT", "outputs/livecodebench/samples.json"))
    p.add_argument("--temperature", type=float,
                   default=float(os.environ.get("LCB_TEMPERATURE", "0") or 0))
    p.add_argument("--max_input_tokens", type=int,
                   default=int(os.environ.get("LCB_MAX_INPUT_TOKENS", "129024") or 129024))
    p.add_argument("--checkpoint_every", type=int, default=10)
    args = p.parse_args()
    if args.checkpoint_every < 1:
        p.error("--checkpoint_every must be at least 1")
    return args

SAFE_KEEP_KEYS = {"question_content", "question", "prompt", "description", "problem_statement", "title"}
BAN_KEYS_PAT = re.compile(r"(hidden|private)?_?tests?|judge|checker|solutions?|ground[_-]?truth|inputs?_?outputs?", re.I)

def extract_prompt(ex):
    # 优先拿“题面/描述”字段；避免把 tests/solutions 等大字段带进去
    for k in ["question_content", "prompt", "question", "description", "problem_statement"]:
        if k in ex and isinstance(ex[k], str) and len(ex[k]) > 10:
            return ex[k]
    # 兜底：拼接“看起来像题面”的短字段（排除含 test/solution 的键）
    pieces = []
    for k, v in ex.items():
        if isinstance(v, str) and not BAN_KEYS_PAT.search(k) and len(v) > 10 and k in SAFE_KEEP_KEYS:
            pieces.append(v)
    if pieces:
        return "\n\n".join(pieces)
    # 最后兜底：只留较短的文本字段，硬性截断，防止超长
    shorts = [v for k, v in ex.items()
              if isinstance(v, str) and not BAN_KEYS_PAT.search(k) and len(v) <= 8000]
    text = "\n\n".join(shorts) if shorts else ""
    return text[:50000]  # 以字符数粗略兜底

SYS = ("You are a competitive programming assistant. "
       "Write a complete, efficient Python 3 program that reads from standard input "
       "and prints to standard output. Do NOT include explanations or markdown; "
       "output only valid Python code.")

def clean_code(txt: str) -> str:
    if not txt: return ""
    if "```" in txt:
        parts = txt.split("```")
        if len(parts) >= 2:
            code = parts[1]
            code = re.sub(r"^\s*python", "", code, flags=re.I).lstrip()
            return code
    return txt

def ask(client, model, temperature, max_input_tokens, user_prompt):
    # 首次尝试：按设定 max_input_tokens 直接请求
    try:
        r = client.chat.completions.create(
            model=model, temperature=temperature, n=1,
            messages=[{"role":"system","content":SYS},
                      {"role":"user","content":user_prompt}],
            extra_body={"max_input_tokens": max_input_tokens}
        )
        return clean_code(r.choices[0].message.content or "")
    except BadRequestError as e:
        msg = str(e)
        # 命中“输入过长”时：进一步截断再重试
        if "Range of input length should be" in msg:
            # 进一步硬截断（按字符估算，保守取 150k 字符）
            trimmed = user_prompt[:150000]
            r = client.chat.completions.create(
                model=model, temperature=temperature, n=1,
                messages=[{"role":"system","content":SYS},
                          {"role":"user","content":trimmed}],
                extra_body={"max_input_tokens": max_input_tokens}
            )
            return clean_code(r.choices[0].message.content or "")
        raise

def main():
    args = parse_args()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                    base_url=os.environ.get("OPENAI_BASE_URL"))

    # 允许执行数据加载脚本
    ds = load_dataset("livecodebench/code_generation_lite",
                      version_tag=args.release,
                      trust_remote_code=True)  # 关键参数
    split = "test" if "test" in ds else "train"

    outs, seen = [], set()
    partial_path = args.out + ".partial.json"

    for i, ex in enumerate(ds[split]):
        qid = ex.get("question_id") or ex.get("id") or ex.get("qid") or f"idx_{i}"
        if qid in seen:
            continue
        seen.add(qid)

        prompt = extract_prompt(ex)

        # 带简单重试与节流
        for trial in range(4):
            try:
                code = ask(client, args.model, args.temperature, args.max_input_tokens, prompt)
                break
            except (RateLimitError, APIConnectionError):
                time.sleep(2.0 * (trial + 1))
                continue
        else:
            # 多次失败也写个空，保证评测器能跑
            code = ""

        outs.append({"question_id": str(qid), "code_list": [code]})

        if len(outs) % args.checkpoint_every == 0:
            with open(partial_path, "w", encoding="utf-8") as f:
                json.dump(outs, f, ensure_ascii=False, indent=2)
            print(f"[{len(outs)}] checkpoint -> {partial_path}")
            time.sleep(0.2)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(outs, f, ensure_ascii=False, indent=2)
    print(f"Saved -> {args.out}  count={len(outs)}")
    if os.path.exists(partial_path):
        os.remove(partial_path)

if __name__ == "__main__":
    main()
