"use strict";

// Historical values from results/paper_tables.json, Table 2 of the supplied paper.
// They are transcriptions at published precision, not fresh evaluation results.
const benchmarks = ["HumanEval", "HumanEval+", "MBPP", "MBPP+", "BCB(full)", "BCB(hard)"];
const models = [
  { name: "DeepSeek-V3.1", thinking: false, values: [0.927, 0.884, 0.899, 0.741, 0.499, 0.304] },
  { name: "DeepSeek-V3.1", thinking: true, values: [0.951, 0.902, 0.963, 0.810, 0.504, 0.284] },
  { name: "Kimi-K2", thinking: false, values: [0.939, 0.915, 0.910, 0.751, 0.474, 0.270] },
  { name: "Qwen-Plus", thinking: false, values: [0.970, 0.927, 0.937, 0.802, 0.468, 0.277] },
  { name: "Qwen-Plus", thinking: true, values: [0.976, 0.933, 0.971, 0.831, 0.495, 0.284] },
  { name: "Qwen3-Coder-Plus", thinking: false, values: [0.939, 0.884, 0.923, 0.788, 0.496, 0.297] }
];

// Paraphrased definitions and illustrative evidence from docs/taxonomy.md.
const taxonomy = {
  A: [
    ["A_EQ_NEQ", "Equality / inequality", "An equality or inequality comparison fails because the computed value does not satisfy the expected comparison.", "An equality assertion expects 3 but receives 4."],
    ["A_SEQ_LEN", "Sequence mismatch", "A sequence has the wrong length or the wrong elements, including extra or missing elements.", "Expected [0, 0], received [0]."],
    ["A_FLOAT_APPROX", "Numerical approximation", "A floating-point or numerical approximation comparison fails because an appropriate tolerance is missing or the approximation is inaccurate.", "0.1 + 0.2 == 0.3 fails under exact comparison."],
    ["A_NOT_NONE_EMPTY", "None / emptiness", "An assertion about None, emptiness, or non-emptiness is violated.", "A test expects a non-empty collection but receives []."],
    ["A_RANGE_BOUND", "Range / boundary", "A range or boundary condition is violated, including off-by-one errors and incorrect interval endpoints.", "101 violates an expected interval from 0 through 100."],
    ["A_TYPE_CHECK", "Output type assertion", "The runtime type of an output differs from the type required by an assertion.", "assert isinstance(\"3\", int) fails."],
    ["A_STRUCTURE_KEYS", "Structure / keys", "A dictionary or structured output has a different structure or key set from the expected output.", "An output is missing a required name key."],
    ["A_MEMBERSHIP", "Membership", "A membership or containment relation does not hold.", "assert 3 in [1, 2] fails."],
    ["A_EXPECTED_EXCEPTION", "Exception contract", "A required exception is not raised, or the raised exception does not match the test's expected exception contract.", "A test expects ValueError, but the operation completes successfully."]
  ],
  V: [
    ["V_PARSE_CONVERT", "Parsing / conversion", "Parsing or conversion fails because a value has an invalid representation, format, or cast input.", "int(\"abc\") raises ValueError."],
    ["V_MATH_DOMAIN", "Mathematical domain", "A mathematical operation receives a value outside its supported domain.", "math.sqrt(-1) raises a domain error for real-valued computation."],
    ["V_UNPACK", "Unpacking", "Sequence unpacking receives too many or too few values for its targets.", "a, b = [1, 2, 3] raises ValueError."],
    ["V_EMPTY_MINMAX", "Empty min / max", "min or max is applied to an empty sequence without a valid default.", "max([]) raises ValueError."],
    ["V_INVALID_OPTION", "Invalid option", "An invalid option, mode flag, or unsupported configuration value is supplied.", "open(\"file.txt\", mode=\"z\") raises ValueError."]
  ],
  T: [
    ["T_BAD_ARGTYPE", "Argument type", "A function receives an argument of an unsupported type.", "len(5) or int((1, 2)) raises TypeError."],
    ["T_NOT_ITERABLE", "Not iterable", "An object is used in an iteration context but does not support iteration.", "for x in 42: pass raises TypeError."],
    ["T_NOT_CALLABLE", "Not callable", "An object is called as a function but is not callable.", "Assigning x = 5 and then calling x() raises TypeError."],
    ["T_ARGCOUNT", "Argument count", "A call supplies an incompatible number or combination of positional or keyword arguments.", "Calling f(1) when f(a, b) requires two arguments."],
    ["T_UNSUPPORTED_OPERAND", "Incompatible operands", "An operator or comparison is applied to incompatible operand types.", "\"a\" + 1 or 3 < \"3\" raises TypeError."],
    ["T_UNHASHABLE", "Unhashable object", "An unhashable object is used where a hashable dictionary key or set element is required.", "Using [1, 2] as a dictionary key raises TypeError."]
  ]
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderTaxonomy(category) {
  const panel = document.getElementById("taxonomy-panel");
  panel.replaceChildren();
  taxonomy[category].forEach(([id, title, definition, example], index) => {
    const item = element("details", "taxonomy-item");
    item.open = index === 0;
    const summary = element("summary");
    const toggle = element("b", "", "+");
    toggle.setAttribute("aria-hidden", "true");
    summary.append(element("code", "", id), element("span", "", title), toggle);
    const content = element("div", "taxonomy-content");
    const evidence = element("div", "taxonomy-example");
    evidence.append(element("span", "", "EXAMPLE"), element("code", "", example));
    content.append(element("p", "", definition), evidence);
    item.append(summary, content);
    panel.append(item);
  });
}

function renderBenchmark(benchmark) {
  const index = benchmarks.indexOf(benchmark);
  const maximum = Math.max(...models.map(model => model.values[index]));
  const chart = document.getElementById("benchmark-chart");
  const displayName = benchmark === "BCB(full)" ? "BigCodeBench Full" : benchmark === "BCB(hard)" ? "BigCodeBench Hard" : benchmark;
  document.getElementById("chart-title").textContent = displayName;
  chart.replaceChildren();
  models.forEach(model => {
    const value = model.values[index];
    const row = element("div", "bar-row" + (value === maximum ? " best" : ""));
    const label = element("div", "bar-label", model.name);
    if (model.thinking) label.append(element("small", "", "THINKING"));
    if (value === maximum) label.append(element("span", "sr-only", " — highest pass at one in this setting"));
    const track = element("div", "bar-track");
    track.setAttribute("aria-hidden", "true");
    const fill = element("div", "bar-fill");
    fill.style.setProperty("--bar-width", (value * 100) + "%");
    track.append(fill);
    row.append(label, track, element("span", "bar-value", value.toFixed(3)));
    chart.append(row);
  });
}

function initializeTabs(selector, panelId, dataKey, onSelect, vertical) {
  const tabs = [...document.querySelectorAll(selector)];
  const activate = tab => {
    tabs.forEach(item => {
      const selected = item === tab;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    document.getElementById(panelId).setAttribute("aria-labelledby", tab.id);
    onSelect(tab.dataset[dataKey]);
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", event => {
      let next = index;
      if (event.key === (vertical ? "ArrowDown" : "ArrowRight")) next = (index + 1) % tabs.length;
      else if (event.key === (vertical ? "ArrowUp" : "ArrowLeft")) next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      tabs[next].focus();
      activate(tabs[next]);
    });
  });
}

function renderTable() {
  const body = document.getElementById("results-table");
  const maxima = benchmarks.map((_, index) => Math.max(...models.map(model => model.values[index])));
  models.forEach(model => {
    const row = element("tr");
    const name = element("th", "", model.name + (model.thinking ? " (Thinking)" : ""));
    name.scope = "row";
    row.append(name);
    model.values.forEach((value, index) => row.append(element("td", value === maxima[index] ? "best" : "", value.toFixed(3))));
    body.append(row);
  });
}

document.querySelectorAll("[data-copy]").forEach(button => {
  let resetTimer;
  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copy);
    const text = target.textContent;
    const original = button.dataset.original || button.innerHTML;
    button.dataset.original = original;
    clearTimeout(resetTimer);
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied ✓";
      document.getElementById("copy-status").textContent = button.dataset.copy === "bibtex" ? "Citation copied to clipboard." : "Quick start commands copied to clipboard.";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(target);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Selected — copy manually";
      document.getElementById("copy-status").textContent = "Text selected. Use your browser's copy command.";
    }
    resetTimer = setTimeout(() => { button.innerHTML = original; }, 3000);
  });
});

initializeTabs(".category-tab", "taxonomy-panel", "category", renderTaxonomy, true);
initializeTabs(".benchmark-tabs button", "benchmark-panel", "benchmark", renderBenchmark, false);
renderTaxonomy("A");
renderBenchmark("HumanEval");
renderTable();
