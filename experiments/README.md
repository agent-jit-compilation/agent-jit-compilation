# Original experiment implementations

These sources implement the planner experiments and task correctness checks. For environment setup and runnable commands, start with the [repository README](../README.md).

| Source | Purpose | User guide |
| --- | --- | --- |
| `evaluate_codegen.py` | Generate candidate plans and compare protocol conditions | [Planner generation](../docs/PLANNER.md#generate-candidate-plans) |
| `measure_e2e_detailed.py` | Execute selected plans and the original Browser-Use baselines | [Live evaluation](../docs/LIVE_EVALUATION.md) |
| `validate_tools.py` | Check registry dependencies and state contracts | [Tool preparation](../docs/PLANNER.md#prepare-a-tool-library) |
| `tasks/*.yaml` | Benchmark instructions and initial URLs | [All 37 tasks](../docs/TASKS.md) |
| `tasks/script_eval/`, `tasks/llm_eval/` | Application-state and answer correctness functions | [Evaluation settings](../docs/LIVE_EVALUATION.md#evaluation-settings) |
| `task_state_utils.py` | Collect and merge browser-worker final states | [Scheduler execution](../docs/SCHEDULER.md#select-and-execute-a-strategy) |
| `runner.py`, `configs/` | Original scheduler experiment harness (BLAST backup commit `87a70ec`) and an example stage configuration | [Scheduler execution](../docs/SCHEDULER.md#select-and-execute-a-strategy) |

The original generation and measurement scripts retain their research interfaces and provider/path assumptions. For example, `python -m experiments.runner --config experiments/configs/testing-experiment-config.yaml` runs each configured stage through the BLAST engine and writes to `logs/` and `results/` in the working directory. The harness calls models directly, without the `agent-jit` budget ledger; use `agent-jit evaluate` for budgeted runs. Their arguments can be inspected with `--help`; the `agent-jit` CLI supplies the documented setup and output paths. Tool libraries, scheduler profiles, and compact experiment measurements are already included under `data/`.

For the paper-to-command mapping and repetition counts, see [paper experiments](../docs/PAPER_TO_COMMANDS.md).
