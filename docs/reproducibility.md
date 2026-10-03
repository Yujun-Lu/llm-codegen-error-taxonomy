# Reproducibility and artifact guide

This release supports offline inspection of the retained experiments and selected
new analyses. The paper's results are historical: this repository does not
provide a locked environment or a single command that reproduces every original
generation, evaluation, and figure.

## Start with the archive

Run these commands from the repository root with Python 3.10 or newer:

```bash
python scripts/verify_artifacts.py
python scripts/summarize_results.py --section paper
python scripts/summarize_results.py --section archive
python scripts/summarize_results.py --format json --output outputs/summary.json
```

These tools use only the Python standard library. They make no model API calls
and do not execute the generated programs. The verifier checks SHA-256 hashes
and byte counts for 88 archived files, then parses all 90 JSON/JSONL files under
`results/`, including the manifest and the separately transcribed paper tables.
Integrity verification establishes that the released files match the manifest;
it does not establish the correctness of a model's solution or annotation.

The JSON summary keeps `paper_reported` and `archived` separate. Archived pass
fractions use recorded attempts as their denominator. Each retained main-study
run has one attempt per task, so these fractions correspond to pass@1 for that
run. All 36 pass@1 values in Table 2 agree with the retained records when rounded
to the paper's three-decimal display precision. Table 3's error ratios are
transcribed values; the offline summary does not recompute them.

## Released coverage

| Task set | Configurations | Tasks per configuration | Files per configuration |
| --- | ---: | ---: | --- |
| HumanEval / HumanEval+ | 6 | 164 | `eval_results.json`, `minimal_errors.jsonl`, `classified.jsonl` |
| MBPP / MBPP+ | 6 | 378 | `eval_results.json`, `minimal_errors.jsonl`, `classified.jsonl` |
| BigCodeBench Full | 6 | 1,140 | The three files above and `pass_at_k.json` |
| BigCodeBench Hard | 6 | 148 | The three files above and `pass_at_k.json` |
| LiveCodeBench `release_v6` supplement | 4 | 1,055 question IDs | `samples.json` only |

The six main-study configurations are DeepSeek-V3.1, Kimi-K2, Qwen-Plus, and
Qwen3-Coder-Plus in non-thinking mode, plus DeepSeek-V3.1 and Qwen-Plus in
thinking mode. The base and extended EvalPlus tests share tasks; BigCodeBench
Hard is a subset of Full. These rows are not disjoint populations.

The paper's model-description prose sometimes says **Qwen3-Plus**, while its
result tables and the retained run names say **Qwen-Plus** / `qwen-plus`.
This release follows the table and artifact names and does not infer an exact
provider snapshot from that alias.

The LiveCodeBench supplement contains generations for the four non-thinking
model names above. It is outside the paper's main tables. Its files contain
question identifiers and generated-code lists, not a released official
evaluation or a pass@1 score. The presence of a record does not imply that its
code is complete or correct.

## File formats and provenance

- `results/manifest.json` maps each released artifact to its original relative
  source location, byte count, and SHA-256. Raw archived file contents are
  preserved; public directory and file names were standardized.
- `eval_results.json` contains the evaluator's `eval` mapping from task IDs to
  attempts. EvalPlus records include `base_status` and `plus_status`;
  BigCodeBench records include `status` and failure `details`.
- BigCodeBench's `pass_at_k.json` retains evaluator metadata such as `subset`,
  `split`, `calibrated`, and `gt_pass_rate`. Read these fields in the context of
  the upstream evaluator; its `failed_tasks` list is not the complete set of
  failed model solutions. Count model outcomes from `eval_results.json`.
- `minimal_errors.jsonl` contains reduced failure evidence. Records can repeat
  a task ID for different errors or messages. EvalPlus records may also contain
  prompts and candidate/reference code.
- `classified.jsonl` contains `task_id`, `error_type`, `tests`, and a
  `classifications` array with `message_index` and `subclass_id`.
- `results/paper_tables.json` directly transcribes Tables 2 and 3 on PDF pages 6
  and 7. It is deliberately separate from the raw-data manifest.

Classification counts are counts of emitted labels, not unique failed tasks,
test cases, or annotation accuracy. Preserve `UNKNOWN` and disclose missing
labels when reporting a distribution. In particular, the retained MBPP
non-thinking DeepSeek-V3.1 classification file contains 122 records and 119
labels: three records have empty classification arrays. Those missing labels
are distinct from explicit `UNKNOWN` labels. The archive preserves this
limitation rather than fabricating annotations.

The summary reports this explicitly as `empty_classification_record_count`.

## Optional dependencies

The following commands install dependencies for additional research tools;
they are unnecessary for the offline quick start. Use a separate virtual
environment. Python 3.10 or 3.11 is the intended compatibility target for the
optional EvalPlus tools.

```bash
python -m venv .venv
# macOS / Linux:
source .venv/bin/activate
# PowerShell instead:
# .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# Only if using the EvalPlus generation or failure-report tools:
python -m pip install -r requirements-evalplus.txt
```

The requirement ranges describe this release's tooling, not the original
experiment lockfile. Install the official
[EvalPlus](https://github.com/evalplus/evalplus),
[BigCodeBench](https://github.com/bigcode-project/bigcodebench), or
[LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) harness separately
when performing fresh evaluation, and record its commit and dependencies.

## Recompute BigCodeBench error ratios

This tool reads archived logs and downloads official test source to count test
methods; it does not run the candidate programs:

```bash
python scripts/bigcodebench/calc_bcb_fail_error_ratio_official.py results/bigcodebench/full/non-thinking/deepseek-v3.1/eval_results.json --dataset bigcode/bigcodebench --version v0.1.4
python scripts/bigcodebench/calc_bcb_fail_error_ratio_official.py results/bigcodebench/hard/non-thinking/deepseek-v3.1/eval_results.json --dataset bigcode/bigcodebench-hard --version v0.1.4
```

It requires the `datasets` dependency and access to the selected dataset, or
a compatible local cache. `v0.1.4` is the split selected by the retained tool;
it is not a pinned Hugging Face commit. Record the resolved dataset revision
for a new analysis.

The default uses the first failed attempt for each task with `status == "fail"`.
`--per_attempt` changes the population to failed attempts. The tool counts
official test methods and extracts distinct failed test names from the logs.
It refuses a numerical result when a failed record has no identifiable failed
test names or more failed names than official tests. `--skip_missing` excludes
tasks with unavailable official test counts and changes the denominator; always
report those exclusions. Timeouts and other statuses are not automatically
converted into counted failed tests.

Four retained runs contain a failed record without parseable failed-test names,
so the released tool stops on these records instead of treating them as zero
failed tests:

| BigCodeBench run | Affected task |
| --- | --- |
| Hard, Kimi-K2, non-thinking | `BigCodeBench/760` |
| Full, DeepSeek-V3.1, thinking | `BigCodeBench/1064` |
| Full, Qwen-Plus, thinking | `BigCodeBench/365` |
| Full, Qwen3-Coder-Plus, non-thinking | `BigCodeBench/583` |

Table 3 remains the paper's reported result. Exact recomputation for these runs
requires complete failure evidence or an explicitly documented revised
population; this release does not supply invented test names.

Both macro and micro ratios are conditional on the failed-task population:

```text
e_t = failed_test_names_t / official_test_methods_t
macro = mean(e_t over failed tasks)
micro = sum(failed_test_names_t) / sum(official_test_methods_t)
```

An overall task failure rate is a different metric. Section 2.5's equations and
Section 3.4 support the definitions above; a prose sentence after the equations
describes a different macro quantity. See the [taxonomy notes](taxonomy.md) for
this paper-level inconsistency. Fresh counts depend on the selected dataset and
the completeness of archived failure evidence, so compare results explicitly
with the paper rather than overwriting its tables.

## Classify retained failure evidence

These commands send the selected evidence to a model API and may incur charges.
Set `DASHSCOPE_API_KEY` in your environment first. The default endpoint is
`https://dashscope.aliyuncs.com/compatible-mode/v1`; override it with
`DASHSCOPE_BASE_URL` when needed. The scripts do not load `.env` files themselves.

```bash
python scripts/bigcodebench/classify_errors.py results/bigcodebench/full/non-thinking/deepseek-v3.1/minimal_errors.jsonl outputs/bcb_classified.jsonl --model deepseek-r1 --workers 1 --limit 5
python scripts/evalplus/classify_errors.py results/evalplus/humaneval/non-thinking/deepseek-v3.1/minimal_errors.jsonl outputs/humaneval_classified.jsonl --model deepseek-r1 --workers 1 --limit 5
```

The paper reports DeepSeek-R1 labeling at temperature 0. Both helpers preserve
the original Chinese classification prompts and request JSON responses. The
EvalPlus helper sends prompt and candidate/reference source context by default
(clipped to 4,000 characters per source field); `--no-src` omits that context.
BigCodeBench sends error types, test names, and failure messages. New API
responses are new annotations even when temperature is 0. The retained outputs
do not preserve complete classifier responses or reasoning traces, and the
scripts do not establish human-validated annotation accuracy.

To convert existing labels to CSV without model calls:

```bash
python scripts/bigcodebench/jsonl_to_csv.py results/bigcodebench/full/non-thinking/deepseek-v3.1/classified.jsonl outputs/bcb_labels.csv
python scripts/evalplus/jsonl_to_csv.py results/evalplus/humaneval/non-thinking/deepseek-v3.1/classified.jsonl outputs/humaneval_labels.csv
```

## Generation and execution tools

| Tools | Purpose and requirements |
| --- | --- |
| `scripts/evalplus/generate_*_thinking.py` | Four helpers for Qwen-Plus / DeepSeek-V3.1 on HumanEval / MBPP. Require EvalPlus, `openai`, and `DASHSCOPE_API_KEY`; support `MODEL`, `DASHSCOPE_BASE_URL`, `OUT_DIR`, and `TAG`. They request thinking mode and write new samples under `outputs/` by default. |
| `scripts/evalplus/report_failures_from_eval_results.py` | Re-executes candidate and reference code to inspect failures; its results can differ from the original evaluator. |
| `scripts/evalplus/hybrid_timeout_report_jsonl_original.py` | Re-executes selected failures in child processes with time budgets; by default inspects at most 20 plus cases per task. Skipped, timed-out, and static-only outcomes are not a full rerun of all tests. |
| `scripts/bigcodebench/bcb_make_summary.py` | Enriches retained evaluator logs with official benchmark metadata; accepts a single normalized run or the full `results/bigcodebench` tree via `--bcb_dir`. Requires `pandas` and `datasets`, and may download data. |
| `scripts/livecodebench/gen_lcb_codegen_qwen_openai.py` | Generates a new `release_v6` sample file. Requires `OPENAI_API_KEY` and optionally `OPENAI_BASE_URL`; model and output are CLI arguments. |
| `scripts/livecodebench/resume_lcb_codegen_qwen_openai.py` | Retains question IDs already present in `--in_json` and generates missing IDs. An existing ID with empty code is still considered present. |
| `scripts/livecodebench/lcb_badcases_to_csv.py` | Converts separately supplied evaluator output and matching sample records into heuristic failure reports. Required evaluator files are not part of the supplement; its labels differ from the paper taxonomy. |

Generation helpers make paid API requests. Exact historical model snapshots,
all non-thinking generation commands, and the complete original runtime are
not retained in this release. Provider aliases and behavior may have changed.
The LiveCodeBench loaders use `trust_remote_code=True` with the older
script-based dataset interface; inspect and trust the upstream dataset code
before using them. The compatibility range in `requirements.txt` retains that
interface but does not guarantee upstream availability.

**Run evaluation and the two EvalPlus failure reporters only in a disposable,
isolated environment with no credentials or access to personal files.** They
execute generated programs. Python subprocesses, timeouts, and resource limits
do not isolate filesystem or network access. The offline quick-start commands
do not perform this execution.

## Record a new experiment

Write new files under `outputs/`, then record the model/provider identifier,
date, inference mode, prompts and decoding settings, dataset and harness
revisions, dependency versions, sanitization, task/attempt counts, time limits,
missing records, and classification coverage. Keep inputs and output hashes.
Report new results alongside the historical archive, with an explicit
description of any differences.

Offline checks for changes to the repository are:

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
python scripts/verify_artifacts.py
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for corrections and
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for reuse terms.
