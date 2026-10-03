# Fine-grained error taxonomy

This document paraphrases Table 1 and Sections 2.4–2.5 of Yujun Lu's
[How LLMs Fail in Code Generation: A Cross-Benchmark and Unified Fine-Grained Error Taxonomy](https://doi.org/10.1145/3838938.3838941).
The [supplied paper](../paper/paper.pdf) is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Definitions and examples
below are edited for clarity; the 20 subtype identifiers are unchanged.

## Scope and mapping

The taxonomy refines three evaluator outcome categories: **AssertionError /
WrongAnswer**, **ValueError**, and **TypeError**. EvalPlus's `WrongAnswer` and
BigCodeBench's `AssertionError` share the nine `A_` subtypes. The two classifier
scripts retain the evaluator-specific top-level category rather than renaming
both to one exception class.

The paper's main experiments concern HumanEval/HumanEval+, MBPP/MBPP+, and
BigCodeBench Full/Hard. LiveCodeBench material in this release is supplemental
and is not part of the paper's main experiment or Tables 2–3.

A failed task is a generated solution that fails at least one official unit
test after benchmark sanitization. A task can produce several error types
across its failed tests even when the evaluator's task-level verdict reflects
only the first observed failure. The analysis harvests the failed-test evidence
for the three supported top-level categories. The classifier scripts assign a
best-fit subtype to each supplied failure message, indexed by `message_index`;
a task can therefore have several labels.

## The 20 subtypes

These definitions describe observed failure semantics. Use the most specific
supported subtype when several descriptions appear relevant. An assertion
about an output's type belongs to `A_TYPE_CHECK`; a runtime exception caused by
a type mismatch belongs to a `T_` subtype.

| Top-level category | Subtype | Definition | Illustrative evidence |
| --- | --- | --- | --- |
| AssertionError / WrongAnswer | `A_EQ_NEQ` | An equality or inequality comparison fails because the computed value does not satisfy the expected comparison. | An equality assertion expects `3` but receives `4`. |
| AssertionError / WrongAnswer | `A_SEQ_LEN` | A sequence has the wrong length or the wrong elements, including extra or missing elements. | Expected `[0, 0]`, received `[0]`. |
| AssertionError / WrongAnswer | `A_FLOAT_APPROX` | A floating-point or numerical approximation comparison fails because an appropriate tolerance is missing or the approximation is inaccurate. | `0.1 + 0.2 == 0.3` fails under exact comparison. |
| AssertionError / WrongAnswer | `A_NOT_NONE_EMPTY` | An assertion about `None`, emptiness, or non-emptiness is violated. | A test expects a non-empty collection but receives `[]`. |
| AssertionError / WrongAnswer | `A_RANGE_BOUND` | A range or boundary condition is violated, including off-by-one errors and incorrect interval endpoints. | A value of `101` violates an expected interval of `0` through `100`. |
| AssertionError / WrongAnswer | `A_TYPE_CHECK` | The runtime type of an output differs from the type required by an assertion. | `assert isinstance("3", int)` fails. |
| AssertionError / WrongAnswer | `A_STRUCTURE_KEYS` | A dictionary or structured output has a different structure or key set from the expected output. | An output is missing a required `name` key. |
| AssertionError / WrongAnswer | `A_MEMBERSHIP` | A membership or containment relation does not hold. | `assert 3 in [1, 2]` fails. |
| AssertionError / WrongAnswer | `A_EXPECTED_EXCEPTION` | A required exception is not raised, or the raised exception does not match the test's expected exception contract. | A test expects `ValueError`, but the operation completes successfully. |
| ValueError | `V_PARSE_CONVERT` | Parsing or conversion fails because a value has an invalid representation, format, or cast input. | `int("abc")` raises `ValueError`. |
| ValueError | `V_MATH_DOMAIN` | A mathematical operation receives a value outside its supported domain. | `math.sqrt(-1)` raises a domain error for real-valued computation. |
| ValueError | `V_UNPACK` | Sequence unpacking receives too many or too few values for its targets. | `a, b = [1, 2, 3]` raises `ValueError`. |
| ValueError | `V_EMPTY_MINMAX` | `min` or `max` is applied to an empty sequence without a valid default. | `max([])` raises `ValueError`. |
| ValueError | `V_INVALID_OPTION` | An invalid option, mode flag, or unsupported configuration value is supplied. | `open("file.txt", mode="z")` raises `ValueError`. |
| TypeError | `T_BAD_ARGTYPE` | A function receives an argument of an unsupported type. | `len(5)` or `int((1, 2))` raises `TypeError`. |
| TypeError | `T_NOT_ITERABLE` | An object is used in an iteration context but does not support iteration. | `for x in 42: pass` raises `TypeError`. |
| TypeError | `T_NOT_CALLABLE` | An object is called as a function but is not callable. | Assigning `x = 5` and then calling `x()` raises `TypeError`. |
| TypeError | `T_ARGCOUNT` | A call supplies an incompatible number or combination of positional or keyword arguments. | Calling `f(1)` when `f(a, b)` requires two arguments. |
| TypeError | `T_UNSUPPORTED_OPERAND` | An operator or comparison is applied to incompatible operand types. | `"a" + 1` or `3 < "3"` raises `TypeError`. |
| TypeError | `T_UNHASHABLE` | An unhashable object is used where a hashable dictionary key or set element is required. | Using `[1, 2]` as a dictionary key raises `TypeError`. |

The published PDF may extract the label `V_INVALID_OPTION` with a space inside
the word. `V_INVALID_OPTION` is the identifier used by both released classifiers.

## UNKNOWN and labeling limits

`UNKNOWN` is an implementation fallback, **not a 21st subtype from Table 1**.
The prompts request it when the supplied evidence cannot be matched to a
supported subtype. The scripts also use it when a classifier response cannot
be parsed as JSON. An `UNKNOWN` result can therefore represent missing or
ambiguous evidence, an unsupported failure, or a malformed classifier response;
it does not establish a new semantic error category.

An empty `classifications` array is a missing annotation, not an explicit
`UNKNOWN` result. This occurs in three retained MBPP non-thinking
DeepSeek-V3.1 records. Report missing annotations separately from emitted
labels; see [artifact coverage](reproducibility.md#file-formats-and-provenance).

The taxonomy does not cover every Python exception or evaluator outcome.
For example, syntax errors, timeouts, `KeyError`, `AttributeError`, and file or
environment failures have no dedicated subtype here. Retain their original
evaluator metadata; do not force them into an `A_`, `V_`, or `T_` label.
Likewise, model-generated labels and brief rationales are annotations rather
than independently verified root-cause diagnoses. Preserve `UNKNOWN` counts
and state the denominator when reporting subtype distributions. Dropping
unclassified records changes the population being summarized.

## Error ratios are conditional on failed tasks

Let `F` be the set of failed tasks for the model and benchmark under analysis.
For each `t` in `F`, let `N_t` be the total number of official test methods and
`M_t` the number of distinct failed test names. The paper defines:

```text
e_t     = M_t / N_t
E_macro = (1 / |F|) * sum(t in F, e_t)
E_micro = sum(t in F, M_t) / sum(t in F, N_t)
```

Both aggregates use **only the failed-task set `F`**. Macro weights failed
tasks equally; micro weights them by their official test counts. These are
conditional failure-severity measures. The fraction of all evaluated tasks
that fail is a different quantity and is not `E_macro`.

The equations in Section 2.5 control this interpretation. The prose immediately
after them incorrectly describes macro as the fraction of problems with at
least one failing test; that description is not used here. Section 3.4 also
explicitly describes the error ratios as being computed on failed tasks.
When `F` is empty, the conditional averages have no defined denominator;
report that condition explicitly instead of interpreting it as a measured
zero severity.

[results/paper_tables.json](../results/paper_tables.json) transcribes Tables 2
and 3 as printed in the supplied paper, at their displayed three-decimal precision. It contains
**paper-reported values, not results from a fresh reproduction run**.
