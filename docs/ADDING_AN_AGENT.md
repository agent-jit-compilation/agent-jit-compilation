# Evaluate another agent

Implement `async def run(context) -> AgentOutput` in an importable Python module. Point the configuration's `adapter` at `your_module:run`. The shared runner initializes the benchmark, supplies its exact task text, evaluates the returned answer with the task's original correctness checks, and records every attempted trial. Your adapter does not need the BLAST engine or serving API.

`context.page` is a Patchright asynchronous Page in a fresh browser profile; it has the Playwright API. `context.cdp_url` lets an agent with its own browser driver attach to the same browser. `context.task` contains the paper ID, implementation ID, instruction, application, and verifier metadata. `context.output_dir` is unique to the trial. Return an `AgentOutput(text=...)`; optional `planning_s` and `execution_s` identify subintervals of the runner's online wall time. An adapter that owns additional worker browsers may supply async `state_provider()` and `cleanup()` callbacks: the former gathers benchmark state during evaluation, and the latter releases resources afterward, including when evaluation fails. The scheduler adapter demonstrates this lifecycle.

Start with `agent_jit/adapters/example.py` and `configs/example-real.yaml`. This read-only example uses the saved `list_all_stores` tool to read the homepage and returns the restaurant names. It is task-specific interface scaffolding, not a paper baseline or a general-purpose agent.

```sh
uv sync --locked --extra browser
uv run --extra browser patchright install chromium
uv run --extra browser agent-jit evaluate \
  --config configs/example-real.yaml --output runs/my-first-agent
uv run agent-jit validate-results runs/my-first-agent/results.jsonl
```

For a new method, copy the configuration and provide its actual `method`, `method_revision`, model identifier, resource limits, and adapter path. Preserve the task IDs. Each trial uses a new process and browser profile. The online timer starts immediately before invoking the adapter; include all online planning, scheduling, and browser interaction in that invocation. Offline tool synthesis and profiling are cached inputs, not part of this timer. Setup and evaluation are measured separately.

## Evaluation modes

The original REAL scripts check application state for action tasks. Such tasks require JSON from `/finish`: a textual claim of success cannot replace state evaluation. Answer tasks use the original Pydantic schemas and correctness functions. The runner offers three parsing choices:

- `json`: the adapter returns structured JSON matching the task's schema; no model parses the answer.
- `openai`: GPT-4.1 parses free-form output under the shared budget ledger. This differs from the historical Claude Sonnet output parser and must be disclosed in comparisons.
- `historical`: the original Claude Sonnet 4 prompt and structured-output parser, with task-specific correctness checks and shared budget accounting. Requires `ANTHROPIC_API_KEY`.

For WebArena use `webarena_native` and the WebArena configuration. Its `llm_judge` flag enables the original optional GPT-4.1-mini upgrade of failed native string matches; the smoke example enables it. See [WebArena setup](WEBARENA.md) and keep this setting consistent across comparisons.

## Failure, isolation, and spending

Timeouts and agent/evaluation/setup errors are retained with zero score. Accuracy counts these attempted trials. Requests blocked by the campaign budget or request limit instead produce `budget_exhausted`, a null score, and a stopped campaign; they are excluded from the scored denominator. The output reports both attempted and scored counts. Partial scores are recorded separately from binary success. A process deadline terminates the worker process group, including its browser children, if cooperative timeouts cannot stop the adapter.

Fresh browser profiles isolate cookies and local storage for the REAL clones; they do not reset an arbitrary server-side application. WebArena requires restored server snapshots and separate authentication; the shared runner creates owned per-trial containers and logs in using the synthetic accounts. See [WebArena setup](WEBARENA.md).

The budget limits apply only to calls made through the provided clients; API calls a custom adapter makes on its own are not capped. To keep them under the shared limit, use `agent_jit.budget.Ledger` with `BudgetedModel` for plan generation or `BrowserModel` for browser-agent calls. Both route supported OpenAI, Google, Anthropic, and Groq models and reserve costs before each request. See [model configuration](LIVE_EVALUATION.md#choose-models) for credentials and provider limits. If an adapter makes untracked calls, report their cost separately rather than as zero.

Generated code runs locally through the original interpreter. Static type/contract checks are not a security sandbox. Use an isolated benchmark machine with synthetic benchmark accounts for external adapters and generated programs.
