# Third-party notices and license scope

The [root MIT license](LICENSE), Copyright (c) 2026 Yujun Lu, applies to the
original project code and documentation. The paper and third-party material
retain their separate licenses described below. Inclusion in this repository
does not transfer ownership or replace upstream license terms.

The linked upstream license files and dataset-card license declarations were
checked on October 3, 2026. They describe their respective upstream materials;
they are not a new license grant by this project over third-party content.

## License overview

| Material | Location in this repository | Applicable terms |
| --- | --- | --- |
| Original research and analysis scripts, documentation | `scripts/`, `docs/`, repository documentation | [MIT](LICENSE), for original contributions |
| Research paper | [`paper/paper.pdf`](paper/paper.pdf) | Creative Commons Attribution 4.0 International, as stated in the supplied PDF |
| Paper-derived taxonomy and table descriptions | [`docs/taxonomy.md`](docs/taxonomy.md), [`results/paper_tables.json`](results/paper_tables.json), related documentation | Paper attribution and CC BY 4.0 terms continue to apply to adapted paper material; original documentation contributions are MIT |
| BigCodeBench-derived benchmark material and evaluation excerpts | `results/bigcodebench/` | [Apache License 2.0](licenses/Apache-2.0.txt) for upstream material |
| HumanEval+ and MBPP+-derived benchmark material and evaluation excerpts | `results/evalplus/` | [Apache License 2.0](licenses/Apache-2.0.txt) for EvalPlus material; inherited HumanEval MIT notices also apply where relevant |
| LiveCodeBench evaluation helpers | `scripts/livecodebench/` | Original contributions under MIT; the retained [LiveCodeBench MIT notice](licenses/LiveCodeBench-MIT.txt) applies to reused upstream code |
| Benchmark identifiers, model generations, and research annotations | `results/` | Research artifacts with the provenance below; embedded third-party excerpts keep their source terms |

## Research paper

The supplied [`paper/paper.pdf`](paper/paper.pdf) carries a **Creative Commons
Attribution 4.0 International (CC BY 4.0)** statement. Its licensing is
independent of the software license.

When reusing the paper, credit its named authors and title, provide a link to
the [CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/), and
indicate changes. The full terms are available in the
[Creative Commons legal code](https://creativecommons.org/licenses/by/4.0/legalcode.en).
Retain any separate third-party notices printed within the paper.

## BigCodeBench

The experiments use BigCodeBench and BigCodeBench-Hard. The benchmark's
official dataset card declares **Apache License 2.0**:

- Dataset and licensing statement: [bigcode/bigcodebench](https://huggingface.co/datasets/bigcode/bigcodebench).
- Evaluation framework and attribution: [bigcode-project/bigcodebench](https://github.com/bigcode-project/bigcodebench).
- Upstream license: [BigCodeBench LICENSE](https://github.com/bigcode-project/bigcodebench/blob/main/LICENSE).
- Included full license: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt), sourced from the [Apache Software Foundation](https://www.apache.org/licenses/LICENSE-2.0.txt).

Credit the BigCodeBench authors and contributors for the benchmark tasks and
evaluation framework. The released files are research outputs from local
experiments, including evaluator results, reduced failure records, and error
classifications. Directory and file names were standardized for this release;
some failure records are extracted or reduced from the evaluator outputs.
These transformations and the added classifications do not relicense source
benchmark text, test excerpts, or other upstream material under MIT.

## EvalPlus: HumanEval+ and MBPP+

The experiments use HumanEval+ and MBPP+ through EvalPlus. The official dataset
cards declare **Apache License 2.0**:

- [HumanEval+ dataset](https://huggingface.co/datasets/evalplus/humanevalplus).
- [MBPP+ dataset](https://huggingface.co/datasets/evalplus/mbppplus).
- Evaluation framework and attribution: [evalplus/evalplus](https://github.com/evalplus/evalplus).
- Upstream terms: [EvalPlus LICENSE](https://github.com/evalplus/evalplus/blob/master/LICENSE).
- Included full Apache license: [`licenses/Apache-2.0.txt`](licenses/Apache-2.0.txt).

Credit the EvalPlus authors and contributors for the extended benchmarks and
evaluation framework. As with BigCodeBench, the files in this repository are
local evaluation outputs, reduced failure records, and added classifications.
Source benchmark excerpts retain their upstream terms.

EvalPlus's upstream license also states that files under `evalplus/eval/`
comply with the MIT license because they build on OpenAI's HumanEval work.
The [OpenAI HumanEval license](https://github.com/openai/human-eval/blob/master/LICENSE)
is retained here for any applicable inherited material:

```text
The MIT License

Copyright (c) OpenAI (https://openai.com)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## LiveCodeBench

The experiments use the LiveCodeBench code-generation benchmark. The local
copy of its evaluation harness carries the following notice, retained in
[`licenses/LiveCodeBench-MIT.txt`](licenses/LiveCodeBench-MIT.txt):

> Copyright (c) 2024 LiveCodeBench

- Framework, authors, and contributors: [LiveCodeBench/LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench).
- Upstream software license: [LiveCodeBench LICENSE](https://github.com/LiveCodeBench/LiveCodeBench/blob/main/LICENSE).
- Dataset and provenance: [livecodebench/code_generation_lite](https://huggingface.co/datasets/livecodebench/code_generation_lite).

The harness's MIT license covers software. The dataset card labels its data
license only as `cc`, without identifying a Creative Commons variant, and
describes problems collected from LeetCode, AtCoder, and Codeforces. This
repository does not assign an MIT or CC BY 4.0 license to those problem texts
or test cases. Obtain the benchmark data from its official upstream source
and retain the source terms applicable to that material.

The public release excludes the copied LiveCodeBench framework and raw
benchmark problem/test dumps. Its supplemental LiveCodeBench artifacts are
local generation and resume helpers, a failure-report helper, and model
generation files containing question identifiers and generated code.

## Dependencies and generated artifacts

Python packages and external evaluation frameworks installed by the user
retain their own licenses; their installations are not bundled here.
Generated model outputs and evaluation records may contain source code or
error excerpts associated with benchmark tasks. Preserve the applicable
upstream attribution and licenses when redistributing such excerpts. The
root MIT license is not a blanket grant of rights over third-party content.

In particular, model-generation files are released as retained research
artifacts. This project does not assert exclusive ownership of generated code
or assign it a new blanket license. Any source content, applicable provider
terms, and third-party rights remain separate from the license on the original
analysis tools. No raw benchmark problem/test dump or third-party evaluation
framework is bundled in this release.
