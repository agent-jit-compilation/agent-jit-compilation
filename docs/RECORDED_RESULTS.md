# Analyze recorded results

```sh
uv sync --locked
uv run agent-jit analyze --figure all --output runs/recorded
```

See [Reproducing the paper](PAPER_TO_COMMANDS.md) for the main result commands. The command makes no model calls. Each figure produces PDF, PNG, and JSON. The JSON records the aggregation rule, counts, and SHA-256 hashes of the exact input files.

## Figure 5: tool-protocol plan generation

Inputs are the 169 per-task/model JSON files under `data/recorded/planner/results_{dashdish,gomail,omnizon}/*/`. For each model and protocol condition, sum `codecheck.overall_pass` and divide by all generated candidates, including failures; average `generation_latency` over that same population. Coverage differs by model: four models cover 22 tasks (704 candidates per condition); the three others cover 27 (864 candidates per condition). The axis is labeled “Valid-plan rate” to distinguish static validity from task accuracy; the paper labels it “Accuracy.”

The CLI reproduces the 14 plotted points from these recorded samples. For example, GPT-4.1 with protocol has 776 valid plans out of 864, with mean generation latency 7.3573 seconds. Full generated programs are not required for this analysis.

## Figures 6–7: latency components and failure categories

`analyze --figure 6` reads `latency-components.json`, a metrics-only extraction of saved group-average timings. It plots planning, LLM inference, and tool execution (`max(0, execution − inference)`) for JIT-Planner and the two Browser-Use variants. The CUA component timings are not in this snapshot, so those bars are omitted.

`analyze --figure 7` uses the same available REAL candidates as Figure 5 and the original failure-category mapping. A candidate can contribute more than one failure type. Bar proportions describe failure-type occurrences; candidate failure rates and counts are reported separately in JSON. These outputs are computed from the released candidates, not copied from the paper's headline percentages.

## Figure 8: the long-horizon GitLab case study

Inputs are the `webarena-747` files for Gemini-2.5-Pro, GPT-4.1, and GPT-5. Each has 32 candidates per protocol condition. Pass@k is `1 − choose(n−c,k)/choose(n,k)`. The original plotting code estimates Pass@t as `1 − (1 − F(t)c/n)^8`, where F is the empirical generation-latency CDF. This is an independence-based estimate for eight workers, not an observed eight-worker browser run.

For Gemini-2.5-Pro, valid candidates increase from 1/32 to 32/32; Pass@3 therefore increases from 9.375% to 100% on this task. This is one case study, not an aggregate claim over the benchmark.

## Figure 9: parallel generation and serial retry

`analyze --figure 9` reads the matched single-attempt and serial-retry snapshots in `retry-trials.json`, for GPT-5 and Gemini-2.5-Flash on `webarena-747`. Each condition has 32 protocol-enabled samples. The parallel curve uses the original eight-worker independence estimate; the serial curve is the empirical success-by-time rate for up to three attempts with feedback. These are plan-generation experiments, not scheduler browser-worker trials.

## Figure 10: scheduler accuracy and latency

`data/recorded/scheduler/trials.json` contains a metrics-only extraction of 728 original trials from 243 source files at BLAST backup commit `87a70ec`. Every source file has a SHA-256 checksum and every record retains its source index and experiment ID. One Gemini-Pro parallel condition contains two trials rather than three. Final answer text and browser state are excluded from this compact extraction.

Use binary `evaluated_success` for the paper-style accuracy, not `correctness_pct`. The latter produces partial-credit results found in some saved summary CSVs. Mean task latency includes failed trials. Task means receive equal weight. JIT choices come from the nine saved application/model scheduler files, and their recorded planning overhead is included. Oracle selects the lowest mean-latency strategy per task and adds mean scheduler overhead, as in the camera-ready plot.

The default command uses the recorded trials without parallel-to-serial substitution. To apply the alternative normalization as a sensitivity analysis:

```sh
uv run agent-jit analyze --figure 10 --normalize-non-parallel --output runs/recorded-normalized
```

That option substitutes serial trials when any parallel trial reports only one completed subtask. The output lists every substitution; the source data is never changed.

`cua-trials.json` contains 162 additional baseline trials (27 tasks × 3 runs × 2 CUA models), extracted using the original `load_cua_results` file selection. All 17 Figure 10 points are computed from trials. OpenAI CUA, for example, has 63/81 successful trials and mean latency 258.7263 seconds. No plotted point uses a paper constant as its result.

**Small differences from the camera-ready coordinates remain.** The largest absolute latency difference is 0.0625 seconds; the largest accuracy difference is 0.1531 percentage points, on Gemini-Pro Oracle. Some exceed ordinary nearest rounding at the precision of the supplied coordinates, so we do not claim exact equality or attribute all differences to rounding. The JSON retains each exact delta and the reference coordinates. Use the normalization option above to inspect the sensitivity of the results to that rule.

## Table 1: planner end-to-end results

```sh
uv run agent-jit analyze-planner --output runs/recorded
```

This produces `planner-table1.csv`, `planner-table1.json`, and `planner-table1.md` from the included historical snapshot, `data/recorded/planner/table1-snapshot.json`. The command reproduces all 98 primary Table 1 cells using the original aggregation:

- **Latency:** mean of the saved `avg_timing.total_seconds` values for the task/model groups in each row.
- **Accuracy:** mean of the saved `avg_correctness_pct` values, which use fractional credit.
- **Range:** maximum minus minimum best-cost group latency within the row.
- **Speedup and accuracy gain:** baseline latency divided by best-cost latency, and the difference in accuracy in percentage points.

For example, Dashdish has 9 tasks × 3 models = 27 best-cost groups, each summarizing three trials. Their mean latency is 14.525 s and mean accuracy is 100%. The slowest and fastest group means are 37.271 s and 1.946 s, giving a 35.325 s range. Browser-Use accuracy rounds to 65%, giving a 35 percentage-point gain.

Each saved group receives equal weight, including duplicate groups retained in the original snapshot. The JSON includes group counts, source indices, and input hashes. Browser-Use has 27 GPT-4.1 groups and 36 for each Gemini model; headline means weight the three models equally. Calculations retain full precision before display rounding. The overall model means are 122.122 s for Browser-Use, 80.095 s for Browser-Use +cache, and 11.731 s for JIT-Planner.

The Reddit Browser-Use speedup is 19.1× when computed from unrounded means (81.302 / 4.251). The paper prints 18.9×, consistent with dividing the displayed rounded latencies (81.3 / 4.3). The reproduction retains the calculation from the source measurements.

Rows use the final paper's task tiers: cardinality 1, 2–4, and 5+ elements; length 1–5, 6–8, and 9+ baseline steps. The thresholds and published reference values are in `data/recorded/planner/table1-reference.json`. This analysis is independent of live planner and scheduler execution.

## Source data

Measurements are included under [data/recorded/](../data/recorded/), with source identifiers and checksums in the dataset JSON. Raw browser traces and full generated-program logs are not included in the compact dataset. New trace collection and plan generation are documented in the [scheduler](SCHEDULER.md) and [planner](PLANNER.md) guides.
