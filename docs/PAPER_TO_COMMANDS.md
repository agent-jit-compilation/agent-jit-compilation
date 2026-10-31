# Reproduce the main results

The included measurements can be analyzed without API keys or a running benchmark. To run the planner and scheduler live, see the [planner guide](PLANNER.md) and [scheduler guide](SCHEDULER.md).

```sh
uv sync --locked
uv run agent-jit analyze-planner --output runs/recorded
uv run agent-jit analyze --figure all --output runs/recorded
```

| Result | Command | Output |
| --- | --- | --- |
| Table 1: planner latency, accuracy, and Range | `analyze-planner` | `planner-table1.md`, CSV, and JSON; reproduces the 98 primary cells with the historical snapshot and original aggregation |
| Figure 5: protocol effect on plan generation | `analyze --figure 5` | Valid-plan rate versus generation latency for seven models |
| Figure 8: long-horizon GitLab case study | `analyze --figure 8` | Pass@k and estimated Pass@t; includes the 9% → 100% Pass@3 result |
| Figure 10: scheduler accuracy and latency | `analyze --figure 10` | Fixed strategies, JIT-Scheduler, oracle, and CUA baselines from recorded trials |

Figures are written as PNG, PDF, and JSON. The JSON contains measurements, sample counts, and aggregation rules. Table 1 recovers the rounded 10.4× speedup and approximately +28 percentage-point gain; Figure 10 recovers the rounded 2.4× scheduler speedup and approximately +9 percentage-point gain over OpenAI CUA. Its reconstructed coordinates differ from the camera-ready plot by at most 0.0625 s and 0.1531 percentage points. See [recorded-result aggregation](RECORDED_RESULTS.md) for the exact definitions and inputs.

`analyze --figure all` also exports the available Figure 6 planner/Browser-Use latency components, Figure 7 failure categories, and Figure 9 parallel-generation/serial-retry curves. Figure 6 omits CUA timing components because the full corresponding breakdown is not included. Raw trials for the separate GPT-5.4 CUA +cache ablation are not included; the Figure 10 CUA data comes from a different experiment. These commands do not reproduce the appendix significance tests.

## Execute the components yourself

A fresh Gemini-2.5-Flash run of all four end-to-end methods, with its configurations and results, is in [REPRODUCTION.md](REPRODUCTION.md).

Use `configs/planner-real-smoke.yaml`, `configs/planner-webarena-smoke.yaml`, or `configs/scheduler-real-smoke.yaml` after [environment setup](../README.md#environment-setup). Examples run one task and one trial. The paper uses three trials, 32 planner candidates, four scheduler workers, and 1,000 Monte Carlo samples.

The planner defaults to collecting a target number of valid candidates. For the paper's protocol, set `candidates: 32` and `candidate_target: sampled`: the planner samples 32 candidates at temperature 0.5 and executes the lowest-cost valid one, as the original experiment code did. Fresh generation and static ranking both pass registry tool types to the cost model; the original generation code omitted them. Recorded measurements are unchanged.

Use `evaluator: historical` for the original REAL answer parser; optionally set `webarena.llm_judge: true` for the original WebArena string-match fallback. [Live configuration](LIVE_EVALUATION.md) explains provider keys, models, evaluators, and output files. Fresh runs call live model APIs, so their results will vary from the recorded samples.

To run a new instruction or application, start with [custom tasks](ADDING_TASKS.md), then prepare the required tools or scheduler caches.
