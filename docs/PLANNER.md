# JIT-Planner

JIT-Planner composes reusable application tools into Python programs. It checks candidate programs against the tools' type and state contracts, estimates their cost, and executes the minimum-cost valid candidate. The supplied libraries let you run the planner without rebuilding tools.

## Use the supplied tool libraries

The registries in `data/registries/` cover Dashdish, Gomail, Omnizon, GitLab, and Reddit. The live runner selects the registry from the task's application. To override it, add this to a planner YAML:

```yaml
tool_registry: runs/my-tools/registry.json
```

The runner writes `registry-input.json` in each trial directory with the registry's hash and tool names. For WebArena, it rewrites the registry's original localhost URL patterns to the configured ports in a per-trial copy; the source registry is not modified.

## Generate candidate plans

To generate plans using an existing tool library, without executing them:

```sh
uv run --extra browser --env-file .env agent-jit generate \
  --task 'What are the first three restaurants listed on the homepage?' \
  --registry data/registries/dashdish.json --state '{"page_type":"home"}' \
  --model gpt-4.1-2025-04-14 --candidates 2 \
  --ledger runs/shared-ledger.json --budget-usd 1 \
  --output runs/generated-plans
```

The initial state is the **abstract state declared by the tool protocol**, not a screenshot or full DOM. Use the registry's keys and values. The live `evaluate` command instead opens the task's starting URL and obtains this state through the application's observation tool.

| Output | Meaning |
| --- | --- |
| `candidate-<index>.py` | Generated candidate program, when generation returns code |
| `generation.json` | Candidate validity, errors, costs, generation time, and selected candidate |
| `selected.py` | Minimum-cost valid candidate, when one exists |

This command runs `CodeGenerator` with one iteration per attempt. It validates every returned candidate and ranks the valid plans once the target has been reached. Use a new output directory for each generation run.

## Validate and rank saved programs

```sh
uv run agent-jit planner check runs/generated-plans/selected.py \
  --registry data/registries/dashdish.json --state '{"page_type":"home"}'
uv run agent-jit planner rank \
  examples/plans/inference.py examples/plans/valid.py \
  --registry data/registries/dashdish.json --state '{"page_type":"home"}'
```

Checks cover syntax, types, and tool-state flow. The cost is a ranking score: tool calls have weight 0.1, `ai_eval` has weight 10, and loop nesting has a factor of 10. The score is not a prediction in seconds. Static validity establishes compatibility with the declared contracts; the benchmark verifier still determines task correctness.

## Generate, execute, and evaluate

```sh
uv run --extra browser --env-file .env agent-jit evaluate \
  --config configs/planner-real-smoke.yaml --output runs/planner-run
uv run agent-jit validate-results runs/planner-run/results.jsonl
```

`evaluate` bundles initial navigation, candidate generation, validation, cost selection, execution, and task evaluation. It generates new candidates for the task; it does not automatically consume the output of the standalone `generate` command.

The selected program runs through the tool executor. The code calls the registry format `SMCP`; a registry stores structured tool schemas, state contracts, and implementations. Calls to `ai_eval` remain available for steps requiring semantic judgment. String results are passed through unchanged; structured results such as dictionaries and lists are serialized as JSON so custom verifiers can parse them directly. The task's verifier checks the answer or final application state.

Important configuration fields:

| Field | Use |
| --- | --- |
| `tasks` | Paper or implementation IDs from [the task list](TASKS.md) |
| `candidates` | Target candidate count (positive integer) |
| `candidate_target` | `valid` (default) collects the requested number of valid plans; `sampled` uses a fixed number of attempts |
| `candidate_temperature` | Sampling temperature for candidates; defaults to 0.5, as in the paper. Inference inside the executed plan uses 0 |
| `max_candidate_attempts` | Attempt limit for valid-plan collection; defaults to four times `candidates` |
| `selection` | `best` by default; `worst` selects the highest-cost valid candidate for the ablation |
| `select_after_valid` | Early selection: choose once this many valid plans have arrived, and cancel the rest (see below) |
| `trials` | Independent repetitions per task |
| `tool_registry` | Optional replacement tool library |
| `model` | Model for plan generation and runtime inference |
| `evaluator` | `historical` for the original REAL parser, `openai` for an OpenAI parser, `json` for structured/custom answers, or `webarena_native` |
| `timeout_s` | Online planning/execution deadline |
| `budget_usd`, `ledger` | Cumulative API spending limit and ledger path |

The paper samples `k = 32` candidates at temperature 0.5 with `Mmax = 1` generation iteration per attempt, executes the lowest-cost valid one, and runs three trials per task. To reproduce it, set `candidates: 32` and `candidate_target: sampled` (CLI: `--candidate-target sampled`). By default, both `generate` and `evaluate` instead collect the requested number of valid plans, in batches of at most 32. They stop at `max_candidate_attempts` (default `4 × candidates`) or the configured request, dollar, or time limits. If the target is not reached, no plan is selected; attempted candidates remain in the output.

By default the planner waits for every candidate, so the slowest one sets the planning time. With `select_after_valid: N`, all candidates still launch in parallel, but the planner selects as soon as N valid plans have arrived and cancels the rest. To keep a best-of-32 choice while cutting the wait, over-provision: `candidates: 64` with `select_after_valid: 32` runs the cheapest of the first 32 valid plans. Each trial records `selection_wait_s`, the actual wait from launching candidates to choosing one. Cancelled requests stop costing money only with clients that can abort a request; the Bedrock client's calls finish in the background.

Sampling at temperature 0 makes parallel candidates nearly identical, so one systematic error can invalidate all of them. On the 27 REAL tasks with Gemini-2.5-Flash, that left three tasks with no valid plan.

Live generation passes registry tool types to the cost estimator, as `planner rank` does. The original generation code omitted them and so assigned zero cost to registry tool calls. This correction affects only fresh plan selection; the recorded results are unchanged. See [reproduction coverage](PAPER_TO_COMMANDS.md) for other differences from the paper and the original code.

`--max-requests` on `generate`, or `max_requests` in an evaluation YAML, sets the shared ledger's cumulative request ceiling. Spending, candidates, and trials have no built-in caps beyond these configured limits; raise them deliberately for larger sweeps.

Select `evaluator: historical` for the original Claude parser, or configure `parser_model` with `evaluator: openai`. WebArena can optionally enable its historical string-match judge. See [evaluation settings](LIVE_EVALUATION.md#evaluation-settings) and [model selection](LIVE_EVALUATION.md#choose-models).

## Registry format and tool coverage

`SMCPTool` and `SMCPToolType` in [`blastai/agents/models.py`](../blastai/agents/models.py) define the executable registry format. Each entry in the top-level `tools` list has:

| Field | Meaning |
| --- | --- |
| `name`, `description` | Callable name and semantics exposed to the planner |
| `input_schema`, `output_schema` | JSON Schemas for parameters and returned data |
| `tool_executor_type`, `lang` | `smcp` and `js` for browser tools |
| `type` | One of `observe`, `getFields`, `listItems`, `setFilter`, `setFields`, `gotoField`, `gotoItem` |
| `pre_path` | URL **glob**, such as `*example.test/products/*`, not a regex |
| `pre`, `post` | Required and established abstract-state values; `*` is a wildcard, `a\|b` denotes alternatives, `$field` refers to an input parameter |
| `pre_tools` | Input-parameter names mapped to prerequisite tool names |
| `is_ready`, `execute`, `is_completed` | JavaScript readiness predicate, operation, and completion predicate |

Provide exactly one `type: observe` tool that returns the abstract-state keys referenced by the other tools' contracts. Navigation must leave a page that this observer can recognize. Predicates return `true` or `[false, "reason"]`; `execute` returns data matching the output schema. Check the supplied registries for the complete field structure and test actual transitions, including wrong pages and missing elements.

Use `$field` only for a declared input parameter: the generated contract assertions cannot reference output-only fields. When navigation resolves an input name to a different destination, check the resolved output in `is_completed`, use `*` for that abstract-state value, and let the observation tool record the actual destination. Describe transitions explicitly in tool descriptions too—for example, returning to the directory before opening another item inside a loop.

Conditional outputs need explicit semantics. For example, `list_all_stores(detailed=True)` returns ratings, while the default call omits them. Mark optional/nullable values accurately and state their input requirements in the output field descriptions; static type compatibility does not establish that a requested rating was retrieved. Separating basic and detailed operations can make the distinction easier to check.

Equal-cost plans are common. Ranking preserves the first candidate in input/generation order; it does not infer which tied plan is more accurate. `planner rank`, `generation.json`, and live `selection.json` report the tied candidates and tie-breaking rule. The task verifier remains independent of this ranking.

The paper registries cover its workloads. New tasks may require additional search, pagination, or navigation tools, even on the same application. Review tool coverage before interpreting a failure as evidence about plan optimization.

## Prepare a tool library

The paper's offline tool synthesis is the Codegen stage in Appendix A, following extraction of page/action context. To prepare a library for a new application:

1. Collect representative page states and successful interactions. Include navigation, forms, menus, and dialogs needed by the tasks. The [scheduler preparation guide](SCHEDULER.md#prepare-page-schemas-and-latency-profiles) describes page-schema extraction and trace parsing if you want to reuse those stages.
2. Implement reusable operations and their contracts. You can use Codex, Claude Code, manual authoring, or the synthesis command below.
3. Check the registry's consistency, then test each tool on the relevant browser states.
4. Use the registry for candidate generation and live evaluation.

A tool registry is a JSON object with a `tools` list. Each tool describes its `input_schema`, `output_schema`, `pre` and `post` abstract state, and executable JavaScript. Existing registries also show `pre_tools`, URL constraints, and runtime `is_ready` / `is_completed` predicates. Include observation and navigation operations so the executor can establish the initial state.

Inspect [a supplied registry](../data/registries/dashdish.json) and the [tool-synthesis prompt](prompts/tool_synthesis_prompt.md) for the complete format. Add tools at the application level—for example, list stores, open a store, read a menu, or add an item to a cart—so they can be composed across tasks.

### Optional automatic tool generation

```sh
uv run --extra browser --env-file .env agent-jit synthesize-tools \
  --url https://real-dashdish-1rmq4pjxg-real-sites.vercel.app/ \
  --task 'Create and test a reusable read-only tool that lists restaurant names.' \
  --registry data/registries/dashdish.json \
  --model gpt-4.1-2025-04-14 \
  --ledger runs/shared-ledger.json --budget-usd 1 \
  --output runs/candidate-tools
```

The synthesis agent explores the browser and uses `update_smcp_tool` to produce `candidate-registry.json`. `build.json` records completion and timing. Omit `--registry` to start an empty library; include it to extend one. Partial tools are retained after a failure or timeout. Review and test the candidate library before using it in experiments.

### Check and test the tools

```sh
uv run python experiments/validate_tools.py runs/candidate-tools/candidate-registry.json
```

This local check finds missing/circular tool dependencies and preconditions with no producing tool, and warns about dead ends and navigation inconsistencies. It does not execute JavaScript. Then test the tools' readiness, execution, returned values, and completion predicates in the browser, including repeated use and relevant navigation states.

Set `tool_registry` in your planner YAML to the checked file, and run benchmark tasks through `evaluate`. Keep registry checks, plan checks, runtime predicates, and the independent task verifier as distinct checks: each covers a different part of the workflow.

## Adapt the task and prompts

For another instruction, change `--task` or the catalog's `task_details`; do not edit the planner prompt merely to add a task. For another application, change the registry and initial state. Tool descriptions, parameter schemas, and pre/postconditions in that registry are incorporated into the generated prompt.

For changes to the planning policy itself, edit `RULES_BASE` and the worked examples in `blastai/agents/codegen.py`; `CodeGenerator._generate_candidate` assembles them with tool definitions, the task, and initial state. Preserve the tool signatures and the rule that programs do not mutate `STATE` directly. Run `planner check` on resulting programs before execution. The live implementation is authoritative; `docs/prompts/` contains paper examples, not runtime prompt overrides.

For automatic tool synthesis, customize the instruction passed to `synthesize-tools --task` first. Its system instructions come from `Agent.derive_synthesis_agent` in `blastai/agents/models.py`; tool-authoring actions are in `blastai/agents/tools_synthesis.py`. Manually authored or coding-agent-generated registries use the same validation and execution path.

## Implementation

- `blastai/agents/codegen.py`: candidate generation.
- `blastai/agents/codecheck.py`, `cfg_builder.py`: type and state-flow checks.
- `blastai/agents/codecost.py`: cost estimation.
- `blastai/agents/executor.py`, `local_python_executor.py`: compiled execution.
- `blastai/agents/tools_synthesis.py`: tool-generation agent.
- `agent_jit/adapters/planner.py`: benchmark execution path.

Prompt references are under `docs/prompts/`; executable examples are under `examples/plans/`. [Recorded results](RECORDED_RESULTS.md) describes analysis of the included candidate measurements and end-to-end trials.
