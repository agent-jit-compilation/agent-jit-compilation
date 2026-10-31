# Execute benchmark tasks

Install the browser dependencies and configure model credentials as described in the [README](../README.md#environment-setup). The runner accepts task IDs from the [37-task catalog](TASKS.md), initializes the benchmark, invokes the selected adapter, evaluates the outcome, and records each attempt.

## Planner

```sh
uv run --extra browser --env-file .env agent-jit evaluate \
  --config configs/planner-real-smoke.yaml --output runs/planner-run
```

This loads the supplied application tools, generates candidates, selects a valid program by cost, and executes it. See [JIT-Planner](PLANNER.md) for settings and intermediate files.

## Scheduler

```sh
uv run --extra browser --env-file .env agent-jit evaluate \
  --config configs/scheduler-real-smoke.yaml --output runs/scheduler-run
```

This selects and executes a strategy using the supplied page schemas and profiles. See [JIT-Scheduler](SCHEDULER.md) for fixed-strategy comparisons and cache preparation.

## Browser-Use baselines

```sh
uv run --extra browser --env-file .env agent-jit evaluate \
  --config configs/browser-use-real-smoke.yaml --output runs/browser-use-run
uv run --extra browser --env-file .env agent-jit evaluate \
  --config configs/browser-use-cache-real-smoke.yaml --output runs/browser-use-cache-run
```

The baseline uses an empty application-tool registry; +cache supplies the same reusable tools as the planner. Both invoke the original observe–reason–act loop in `blastai/agents/executor.py`, with its loop prompt, temperature 0.5, 50-step limit, and disabled vision by default. The separate scheduler experiment uses BLAST browser workers with vision enabled.

## Task selection and repetitions

Copy a configuration into `runs/`, edit its `tasks` list and `trials`, and choose a new output directory. Paper IDs and implementation aliases both work. Use REAL tasks and WebArena tasks in separate campaigns because their environment setup and evaluators differ. The paper reports three runs per task; the example configurations use one.

Fresh browser profiles isolate REAL client state. WebArena restores a separate owned container from the populated snapshot for each trial, then generates authentication. Follow [WebArena setup](WEBARENA.md) before selecting its tasks.

## Evaluation settings

| Setting | Behavior |
| --- | --- |
| `evaluator: openai` | Parse a free-form REAL answer into the task's original schema using `parser_model` (default GPT-4.1), then apply its correctness function |
| `evaluator: historical` | Parse REAL answers with the original Claude Sonnet 4 prompt and schema, then apply the original correctness function |
| `evaluator: json` | Accept schema-matching JSON directly and apply the original correctness function |
| `evaluator: webarena_native` | Run the saved WebArena string/DOM checks against the live trial browser |

REAL action tasks use the final application state from `/finish`, read in the acting tab after the original runner's ten-second settling interval. This preserves the tab's action history and initial-state comparison; the wait counts as evaluation time, not online execution. A textual success claim does not substitute for the state check. Choose `evaluator: historical` to use the original Claude Sonnet 4 answer parser and task-specific schema/correctness functions; this requires `ANTHROPIC_API_KEY`. `evaluator: openai` is an alternative parser, while `json` makes no parsing call.

For the original optional WebArena fallback judge, add `llm_judge: true` inside the YAML's `webarena` block. Native checks run first. GPT-4.1-mini may upgrade a failed string match when the answer is semantically correct; DOM checks are unchanged. This option requires `OPENAI_API_KEY` and shares the run's ledger. It defaults to false for strict native evaluation. Keep the evaluator choice consistent across comparisons.

## Choose models

Install `uv sync --locked --extra browser` for provider clients. Select the exact model ID using `--model` on preparation/generation commands or `model` in execution YAML. The supported routes are:

| Provider | Model IDs | Environment variable |
| --- | --- | --- |
| OpenAI | `gpt-4.1`, `gpt-4.1-mini`, `gpt-5`, `gpt-5-mini` (also their dated snapshots) | `OPENAI_API_KEY` |
| Google | `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.0-flash-lite` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| Anthropic | `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| Groq | `openai/gpt-oss-120b` | `GROQ_API_KEY` |

Historical IDs depend on continued availability from the provider; the CLI does not substitute a different model. These are chat/browser-agent clients. Selecting Claude here does not run the paper's native Anthropic CUA baseline, which uses a different action interface. GPT-OSS is text only: use it for code generation or usage prediction, and set `allow_vision: false` for scheduler execution. Screenshot-based schema extraction and tool synthesis require a vision model.

For a Gemini scheduler run, copy `configs/scheduler-real-smoke.yaml` and change:

```yaml
model: gemini-2.5-flash          # Browser workers
mini_model: gemini-2.5-flash     # Auxiliary extraction calls
predictor_model: gemini-2.5-flash # Element-usage prediction
profile_model: gemini-2.5-flash  # Matching measured execution latencies
evaluator: historical           # Original REAL answer parser (Anthropic key)
```

For the planner, change `model` and the evaluator choice in its YAML. `parser_model` applies only to `evaluator: openai` and must be an OpenAI model. A custom task with its own verifier can use `evaluator: json`, avoiding an additional parser provider.

Execution models and latency profiles are different settings: changing `model` does not measure a new profile. The supplied profiles cover GPT-4.1, Gemini-2.5-Flash, and Gemini-2.5-Pro. For another execution model, first collect traces with a fixed strategy, then [fit its latency profile](SCHEDULER.md#prepare-page-schemas-and-latency-profiles). Schema extraction, trace labeling, and usage prediction may use a different model from execution.

GPT-5 requests omit unsupported sampling settings; planner/browser calls use low reasoning effort. Increase `max_output_tokens` when a reasoning model exhausts its output allowance. For a model not listed above, add its provider and token rates in `agent_jit/model_catalog.py`; existing provider routes then use the corresponding client. A new provider additionally needs an adapter in `agent_jit/native_model.py` and credential validation. Provider output limits and request accounting must remain connected to the shared ledger.

## Results and timing

```sh
uv run agent-jit validate-results runs/planner-run/results.jsonl
```

Each result records task, method, model, configuration, score, success, timing, usage, and source revision. Invalid configurations and missing credentials are rejected before starting a trial. Once a trial starts, failed, timed-out, and setup/evaluation-error attempts remain in the results. Report all attempted trials and distinguish partial scores from binary success. A blocked budget/request reservation is recorded as `budget_exhausted`, with `score: null`; the campaign stops and reports unstarted trials. Accuracy excludes these unscored attempts and is null when none were scored. An already exhausted ledger fails before setup.

`online_s` includes the adapter's online planning or scheduling and execution. `setup_s` and `evaluation_s` are separate. `planning_s` and `execution_s` are optional component subintervals; they need not account for all initialization overhead in `online_s`. WebArena container startup is environment setup rather than online latency.

Model calls share the YAML's cumulative `ledger` and `budget_usd` limit. The client reserves costs before requests and retains a conservative reservation if a request's final usage is unknown. For Google, Anthropic, and Groq, the adapter retains conservative input/output reservations, including possible thinking or cache-write costs; these can exceed billed usage. Text is bounded at one token per byte. Inline screenshots are bounded at twice the provider's documented image tokens (Gemini: 258 per 768×768 tile), not their base64 size. The dollar amounts are estimates, not billing invoices. The supplied examples cap spending at $1. Larger campaigns can increase `budget_usd`, `max_requests`, `trials`, and `candidates`; these have no fixed paper-scale upper cap. A reused ledger retains all previous charges. Its request ceiling and dollar limit are cumulative, not a fresh allowance per invocation. Each running client enforces its own configured limit against the shared total; use consistent limits for concurrent clients.

The recorded planner tables report fractional correctness; the scheduler plots report binary success. Fresh output contains both `score` and `success`; choose the matching metric when comparing results.

For new applications, see [custom tasks and verifiers](ADDING_TASKS.md). For a custom method, see [adding another agent](ADDING_AN_AGENT.md).

A JIT scheduler result records `cross_model_profile: true` when its execution model differs from the latency profile (dated aliases are treated as equivalent). Campaign `summary.json` reports `complete: false` when a budget limit interrupts a trial or prevents remaining trials from starting; accuracy then describes only scored attempts. `validate-results` also reports completeness.
