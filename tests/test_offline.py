"""Offline regression tests: no API calls, dataset downloads or candidate execution."""

import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(relative, mocks=None):
    path = ROOT / "scripts" / relative
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, mocks or {}):
        spec.loader.exec_module(module)
    return module


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class ArtifactVerificationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_script("verify_artifacts.py")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = self.root / "results/sample.jsonl"
        self.artifact.parent.mkdir()
        # Treat program text purely as data, including syntax that would raise.
        self.artifact.write_text(json.dumps({"code": "raise RuntimeError('never execute')"}) + "\n", encoding="utf-8")
        self.manifest = self.root / "results/manifest.json"
        self.refresh_manifest()

    def refresh_manifest(self):
        content = self.artifact.read_bytes()
        write_json(self.manifest, {"artifacts": [{"path": "results/sample.jsonl",
                   "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]})

    def test_valid_data_passes_without_executing_code(self):
        result = self.module.verify(self.root, self.manifest)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["jsonl_records"], 1)

    def test_modified_artifact_fails_integrity_check(self):
        self.artifact.write_text('{"changed": true}\n', encoding="utf-8")
        errors = self.module.verify(self.root, self.manifest)["errors"]
        self.assertTrue(any("SHA256 differs" in error for error in errors))

    def test_invalid_json_fails_even_with_matching_hash(self):
        self.artifact.write_text("{invalid json}\n", encoding="utf-8")
        self.refresh_manifest()
        errors = self.module.verify(self.root, self.manifest)["errors"]
        self.assertTrue(any("line 1" in error for error in errors))

    def test_manifest_cannot_reference_outside_root(self):
        write_json(self.manifest, {"artifacts": [{"path": "../outside.json", "bytes": 0, "sha256": ""}]})
        errors = self.module.verify(self.root, self.manifest)["errors"]
        self.assertTrue(any("escapes repository" in error for error in errors))


class SummaryTests(unittest.TestCase):
    def test_missing_classification_labels_remain_visible(self):
        module = load_script("summarize_results.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "results/evalplus/mbpp/non-thinking/demo/eval_results.json"
            write_json(path, {"eval": {"MBPP/1": [{"base_status": "fail", "plus_status": "fail"}]}})
            path.with_name("classified.jsonl").write_text(
                json.dumps({"classifications": []}) + "\n" +
                json.dumps({"classifications": [{"subclass_id": "UNKNOWN"}]}) + "\n",
                encoding="utf-8")
            row = module.archived_summary(root)[0]
            self.assertEqual(row["classified_record_count"], 2)
            self.assertEqual(row["empty_classification_record_count"], 1)
            self.assertEqual(row["classification_label_counts"], {"UNKNOWN": 1})

    def test_combined_plus_status_requires_base_to_pass(self):
        module = load_script("summarize_results.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "results/evalplus/humaneval/non-thinking/demo/eval_results.json"
            write_json(path, {"eval": {
                "HumanEval/0": [{"base_status": "pass", "plus_status": "pass"}],
                "HumanEval/1": [{"base_status": "fail", "plus_status": "pass"}],
                "HumanEval/2": [{"base_status": "pass", "plus_status": "fail"}],
            }})
            row = module.archived_summary(root)[0]
            self.assertEqual(row["task_count"], 3)
            self.assertEqual(row["base_pass_count"], 2)
            self.assertEqual(row["base_and_plus_pass_count"], 1)
            self.assertAlmostEqual(row["base_and_plus_pass_fraction"], 1 / 3)

    def test_stored_metric_is_separate_from_recorded_pass_fraction(self):
        module = load_script("summarize_results.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "results/bigcodebench/full/non-thinking/demo/eval_results.json"
            write_json(path, {"eval": {"task/0": [{"status": "pass"}], "task/1": [{"status": "fail"}]}})
            write_json(path.with_name("pass_at_k.json"), {"pass@1": 0.6})
            row = module.archived_summary(root)[0]
            self.assertEqual(row["pass_fraction"], 0.5)
            self.assertEqual(row["stored_pass_at_1"], 0.6)


class GeneratorTests(unittest.TestCase):
    def test_livecodebench_preserves_long_question_and_excludes_private_tests(self):
        openai = types.ModuleType("openai")
        openai.OpenAI = object
        openai.BadRequestError = openai.RateLimitError = openai.APIConnectionError = RuntimeError
        datasets = types.ModuleType("datasets")
        datasets.load_dataset = lambda *args, **kwargs: None
        question = "Solve the following problem. " * 400
        record = {"question_content": question, "private_test_cases": "secret tests", "platform": "example"}
        for name in ["gen_lcb_codegen_qwen_openai.py", "resume_lcb_codegen_qwen_openai.py"]:
            with self.subTest(script=name):
                module = load_script("livecodebench/" + name, {"openai": openai, "datasets": datasets})
                self.assertEqual(module.extract_prompt(record), question)

    def test_humaneval_extractor_returns_entry_point_block(self):
        # The optional packages are stubbed solely to import local pure helpers.
        openai = types.ModuleType("openai")
        openai.OpenAI = object
        evalplus = types.ModuleType("evalplus")
        data = types.ModuleType("evalplus.data")
        data.get_human_eval_plus = data.write_jsonl = lambda *args: None
        module = load_script("evalplus/generate_qwen_plus_humaneval_thinking.py",
                             {"openai": openai, "evalplus": evalplus, "evalplus.data": data})
        answer = "```python\ndef helper():\n    return 'a long explanation'\n```\n```python\ndef target():\n    return 1\n```"
        self.assertEqual(module.extract_code(answer, "target"), "def target():\n    return 1")
        self.assertEqual(module.extract_code("print(1)", None), "print(1)")
        self.assertEqual(module.extract_code(None, None), "")


class ConverterTests(unittest.TestCase):
    CONVERTERS = ["evalplus/compress.py", "bigcodebench/compress.py",
                  "evalplus/jsonl_to_csv.py", "bigcodebench/jsonl_to_csv.py",
                  "evalplus/classify_errors.py", "bigcodebench/classify_errors.py"]

    def run_script(self, name, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8")

    def assert_input_preserved(self, inp, out):
        original = inp.read_bytes()
        for script in self.CONVERTERS:
            with self.subTest(script=script):
                result = self.run_script(script, inp, out)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("Input and output must be different files", result.stderr)
                self.assertEqual(inp.read_bytes(), original)

    def test_converters_reject_same_input_output_without_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            inp = Path(temp) / "input.jsonl"
            inp.write_bytes(b'{"task_id": "keep-this-record"}\n')
            self.assert_input_preserved(inp, inp)

    def test_converters_reject_resolved_alias_without_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "subdirectory").mkdir()
            inp = root / "input.jsonl"
            inp.write_bytes(b'{"task_id": "keep-this-record"}\n')
            self.assert_input_preserved(inp, root / "subdirectory" / ".." / "input.jsonl")

    def test_converters_reject_hardlink_output_without_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            inp, out = Path(temp) / "input.jsonl", Path(temp) / "output.csv"
            inp.write_bytes(b'{"task_id": "keep-this-record"}\n')
            try:
                os.link(inp, out)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"Hardlinks unavailable: {exc}")
            self.assert_input_preserved(inp, out)

    def test_bigcodebench_groups_equal_errors_without_merging_distinct_ones(self):
        with tempfile.TemporaryDirectory() as temp:
            inp, out = Path(temp) / "input.jsonl", Path(temp) / "new/output.jsonl"
            message = "\n\n".join([
                "test_a: Traceback (most recent call last):\nTypeError: expected integer",
                "test_b: Traceback (most recent call last):\nTypeError: expected integer",
                "test_c: Traceback (most recent call last):\nValueError: invalid value",
            ])
            inp.write_text(json.dumps({"task_id": "task/1", "error_type": "TypeError", "error_msg": message}), encoding="utf-8")
            result = self.run_script("bigcodebench/compress.py", inp, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["tests"], ["test_a", "test_b"])
            self.assertEqual(rows[1]["tests"], ["test_c"])

    def test_livecodebench_rejects_misaligned_records(self):
        with tempfile.TemporaryDirectory() as temp:
            inp, samples = Path(temp) / "eval.json", Path(temp) / "samples.json"
            write_json(inp, [{"question_id": "one", "pass@1": 0}])
            write_json(samples, [{"question_id": "two", "code_list": ["print(0)"]}])
            result = self.run_script("livecodebench/lcb_badcases_to_csv.py", "--eval-all", inp,
                                     "--outputs", samples, "--badcases-csv", Path(temp) / "bad.csv",
                                     "--summary-csv", Path(temp) / "summary.csv")
            self.assertEqual(result.returncode, 2)
            self.assertIn("question_id mismatch", result.stderr)

    def test_livecodebench_empty_input_has_no_division_by_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            inp, samples = Path(temp) / "eval.json", Path(temp) / "samples.json"
            write_json(inp, [])
            write_json(samples, [])
            out = Path(temp) / "new/bad.csv"
            result = self.run_script("livecodebench/lcb_badcases_to_csv.py", "--eval-all", inp,
                                     "--outputs", samples, "--badcases-csv", out,
                                     "--summary-csv", Path(temp) / "new/summary.csv")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("n/a (empty input)", result.stdout)
            with out.open(encoding="utf-8", newline="") as stream:
                self.assertEqual(list(csv.DictReader(stream)), [])

    def test_livecodebench_rejects_overlapping_output_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inp, samples = root / "eval.json", root / "samples.json"
            write_json(inp, [])
            write_json(samples, [])
            original = {path: path.read_bytes() for path in (inp, samples)}
            (root / "subdirectory").mkdir()
            cases = [(inp, root / "summary.csv"),
                     (root / "bad.csv", samples),
                     (root / "same.csv", root / "same.csv"),
                     (root / "subdirectory" / ".." / "eval.json", root / "summary.csv")]
            alias = root / "hardlink.csv"
            try:
                os.link(samples, alias)
            except (OSError, NotImplementedError):
                pass
            else:
                cases.append((alias, root / "summary.csv"))
            for badcases, summary in cases:
                with self.subTest(badcases=badcases, summary=summary):
                    result = self.run_script("livecodebench/lcb_badcases_to_csv.py",
                                             "--eval-all", inp, "--outputs", samples,
                                             "--badcases-csv", badcases, "--summary-csv", summary)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("Input and output must be different files", result.stderr)
                    for path, content in original.items():
                        self.assertEqual(path.read_bytes(), content)


class BigCodeBenchTests(unittest.TestCase):
    def test_normalized_archive_discovery_preserves_model_mode_and_solution(self):
        module = load_script("bigcodebench/bcb_make_summary.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for mode in ("thinking", "non-thinking"):
                directory = root / "full" / mode / "demo"
                write_json(directory / "pass_at_k.json",
                           {"model": "demo", "subset": "full", "split": "instruct", "pass@1": 0.5})
                write_json(directory / "eval_results.json",
                           {"eval": {"BigCodeBench/1": [{"status": "fail", "solution": "retained code",
                                                         "details": {"test_one": "AssertionError: mismatch"}}]}})
            runs = module.discover_runs(root)
            self.assertEqual(len(runs), 2)
            self.assertEqual({run["model_tag"] for run in runs},
                             {"demo (thinking)", "demo (non-thinking)"})
            for run in runs:
                self.assertEqual(run["subset"], "full")
                self.assertEqual(run["split"], "instruct")
                self.assertEqual(module.read_samples_map(run["samples_path"]), {})
                rows = module.flatten_eval(run["eval_path"])
                self.assertEqual(rows[0]["eval_solution"], "retained code")
                self.assertEqual(module.discover_runs(run["eval_path"].parent), [run])

    def test_original_evaluator_filenames_still_supported(self):
        module = load_script("bigcodebench/bcb_make_summary.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix = "demo--main--bigcodebench-instruct--openai-0-1-sanitized_calibrated"
            write_json(root / f"{prefix}_eval_results.json", {"eval": {}})
            write_json(root / f"{prefix}_pass_at_k.json", {"subset": "hard", "pass@1": 0.5})
            run, = module.discover_runs(root)
            self.assertEqual(run["model_tag"], "demo--main")
            self.assertEqual(run["split"], "instruct")
            self.assertEqual(run["subset"], "hard")
            self.assertEqual(run["samples_path"], root / f"{prefix}.jsonl")

    def test_normalized_archive_requires_metadata(self):
        module = load_script("bigcodebench/bcb_make_summary.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / "eval_results.json", {"eval": {}})
            with self.assertRaisesRegex(ValueError, "pass_at_k.json"):
                module.discover_runs(root)

    def test_error_ratio_deduplicates_test_names(self):
        module = load_script("bigcodebench/calc_bcb_fail_error_ratio_official.py")
        details = {"test_one": "line 1, in test_one\nAssertionError: mismatch",
                   "test_two": "line 2, in test_two\nValueError: invalid"}
        self.assertEqual(module.count_failed_tests(details, 3, "BigCodeBench/1"), 2)

    def test_error_ratio_rejects_missing_or_incompatible_evidence(self):
        module = load_script("bigcodebench/calc_bcb_fail_error_ratio_official.py")
        with self.assertRaisesRegex(ValueError, "no identifiable"):
            module.count_failed_tests({"error": "SyntaxError: invalid syntax"}, 3, "BigCodeBench/1")
        with self.assertRaisesRegex(ValueError, "exceed the official"):
            module.count_failed_tests({"test_one": "fail", "test_two": "fail"}, 1, "BigCodeBench/1")


if __name__ == "__main__":
    unittest.main()
