# JIT-Scheduler

JIT-Scheduler chooses how to allocate workers for a task. It predicts which page elements the task will use, samples their learned latency distributions, and compares Serial, Parallel, and Hedge. The paper evaluates it on the [27 REAL tasks](TASKS.md#scheduler-task-set), with a four-worker budget.

## Use the supplied caches

Two kinds of input are included:

| Path | Input |
| --- | --- |
| `data/scheduler/saved_schemas/<application>/` | Page schemas, element names, navigation links, and URL mappings |
| `data/scheduler/saved_sites/<application>/element_latencies_<model>.json` | Per-element interaction-latency distributions for the execution model |

Applications are `dashdish`, `gomail`, and `omnizon`. Supplied profile models are `gpt-4.1`, `gemini-2.5-pro`, and `gemini-2.5-flash`. A schema describes an actionable element; the profile estimates the time an agent spends reaching and interacting with it. These times can include exploratory work rather than just the DOM operation itself.

## Predict element usage and select a schedule

```sh
uv run --extra browser --env-file .env agent-jit predict-schedule \
  --task 'For each vendor under the Costume party ready category, visit the store page and get the first three reviews.' \
  --application dashdish --profile-model gpt-4.1 \
  --predictor-model gpt-4.1-2025-04-14 \
  --mode mean --seed 0 --trials 1000 \
  --ledger runs/shared-ledger.json --budget-usd 1 \
  --output runs/schedule-prediction
```

This makes model calls to predict sequential usage and a possible parallel decomposition, then applies the original Monte Carlo cost model. It saves:

| File | Meaning |
| --- | --- |
| `usage.json` | Element counts for sequential execution and, when applicable, parallel worker assignments and rediscovery work |
| `prediction.json` | Strategy, estimated latencies, win rates, overheads, input hashes, and scheduling time |
| `prediction.log` | Prediction and cost-model diagnostics |

`prediction.json` and `profile-coverage.json` list schema elements with no latency observations. The predictor is restricted to profiled elements, so missing coverage can hide necessary work. A warning is emitted when the profile is sparse; collect traces and rebuild it before relying on decisions for uncovered workflows.

`--profile-model` names the model that produced the measured latency profile; `--predictor-model` names the model predicting usage. Choose a new output directory for every prediction.

To rerun cost estimation on saved usage counts without model calls:

```sh
uv run agent-jit scheduler runs/schedule-prediction/usage.json \
  --application dashdish --model gpt-4.1 --mode mean --seed 0 --trials 1000
```

For a self-contained example, replace the input with `examples/scheduler-usage.json`. That file supplies illustrative counts for independent restaurant visits; the latency profiles are the included measurements.

`--mode mean` follows Algorithm 2: choose the strategy with the lowest mean sampled latency. `--mode win_rate` selects the strategy that wins most Monte Carlo trials. Both modes are available in the original analysis code; state which one you use in an experiment.

## Select and execute a strategy

```sh
mkdir -p runs
cp configs/scheduler-real-smoke.yaml runs/scheduler.yaml
uv run --extra browser --env-file .env agent-jit evaluate \
  --config runs/scheduler.yaml --output runs/scheduler-run
uv run agent-jit validate-results runs/scheduler-run/results.jsonl
```

The YAML's `strategy` selects JIT prediction or a fixed strategy:

| Value | Behavior |
| --- | --- |
| `jit` | Predict usage, estimate costs, and execute the selected strategy |
| `serial` | Execute the task serially |
| `parallel` | Decompose independent work among browser-agent workers |
| `hedge` | Run redundant attempts and take the first successful completion |

The BLAST browser-agent engine (`blastai/`) handles task decomposition, worker assignment, and result aggregation. The scheduler adapter does not execute compiled JIT-Planner programs. Use the same task, execution model, evaluator, resource budget, and repetition count when comparing strategies.

| Configuration field | Use |
| --- | --- |
| `tasks` | Any of the 27 REAL task IDs |
| `model` | Browser-agent execution model |
| `mini_model` | Auxiliary extraction model |
| `predictor_model` | Element-usage prediction model |
| `profile_model` | Execution-model label selecting the supplied latency profile |
| `worker_budget` | Four workers in the supplied implementation and paper experiment |
| `selection_mode` | `mean` or `win_rate`; used by `strategy: jit` |
| `monte_carlo_trials`, `seed` | Sampling count and random seed |
| `trials` | Independent benchmark repetitions; three in the paper |
| `save_browser_history` | Save per-worker histories for inspection or offline profiling |
| `schema_root`, `latency_profile` | Optional replacement cache paths |

See [model configuration](LIVE_EVALUATION.md#choose-models) for supported OpenAI, Gemini, Anthropic, and Groq clients. The runner warns if `model` and `profile_model` differ (dated aliases are treated as equivalent). Keep `profile_model` aligned with the execution model whose traces produced the profile; changing the prediction model alone does not require collecting new execution traces.

Each trial writes `worker.log`, `engine-metrics.json`, and collected final states when available. JIT selection additionally writes a `schedule/` directory. With history saving enabled, `browser-histories/` contains a native JSON file per available worker. `results.jsonl` records all attempted trials, including failures and timeouts.

## Prepare page schemas and latency profiles

Follow these stages when profiling another execution model or updating the application's pages. The paper's Extract and Label stages produce the scheduler cache (Appendix A, Figure 11). Reuse existing schemas when their elements and URL mappings still match the application; regenerate the model-specific latency profile from representative runs.

### 1. Capture representative page states

Capture screenshots and HTML using the supplied helper (no API key needed):

```sh
uv run --extra browser python scripts/capture_page.py \
  --url https://real-dashdish-1rmq4pjxg-real-sites.vercel.app/ \
  --name main --wait-for body --output runs/prep/captured-main
```

It writes `page.png`, `page.html`, and a `pages.json` manifest usable directly by `build-schemas --config runs/prep/captured-main/pages.json`. Use `--wait-for` with an application-specific loaded-element selector for dynamic pages. Add `--interactive` to open a visible browser, navigate or open a menu/modal, and press Enter in the terminal when ready to capture. Use a new directory for each page state.

For multiple pages, collect the states needed by the tasks and create the combined manifest below. Screenshots are required; HTML is optional. Neither capturing one page nor schema extraction crawls the application automatically.

For example, organize inputs as:

```text
runs/prep/
  pages.json
  screenshots/
    main/home.png
    store/menu.png
  html/
    main/page.html
```

Create `runs/prep/pages.json` with the actual URLs of the captured pages:

```json
[
  {
    "url": "https://real-dashdish-1rmq4pjxg-real-sites.vercel.app/",
    "image_folder": "screenshots/main",
    "html_folder": "html/main",
    "output_name": "main"
  },
  {
    "url": "https://real-dashdish-1rmq4pjxg-real-sites.vercel.app/store/example",
    "image_folder": "screenshots/store",
    "output_name": "store"
  }
]
```

Replace the example store URL with the URL you captured. Folder paths resolve relative to `pages.json`; `html_folder` is optional. List pages in the order their definitions should be accumulated. The original generator can also read modal hints from `modals.json` in an HTML folder.

### 2. Extract page schemas

```sh
uv run --extra browser --env-file .env agent-jit build-schemas \
  --config runs/prep/pages.json --output runs/prep/schemas/dashdish \
  --model gpt-4.1-2025-04-14 \
  --ledger runs/shared-ledger.json --budget-usd 1
```

The extraction model describes actionable elements, navigation, and cardinality. Output includes per-page schemas, `config.json`, `url_mappings.json`, and `build.json`. The loader expects an application subdirectory such as `schemas/dashdish/`.

Review element names and `opensPage`/`opensModal` relationships against the screenshots. The command checks schema structure but cannot establish whether every element was described accurately. Add `regex:` URL mappings when one schema covers multiple dynamic pages. Every URL later found in a trace must map to a schema. Use a new output directory for each build.

### Customize schema extraction

Start with the actual extraction template, then edit its application-specific instructions or examples:

```sh
uv run python - <<'PYCODE'
from pathlib import Path
from blast_scheduler.page_schema.prompts import SCHEMA_PROMPT
Path("runs/prep/schema-prompt.txt").write_text(SCHEMA_PROMPT)
PYCODE
```

Pass the edited file on the next build:

```sh
uv run --extra browser --env-file .env agent-jit build-schemas \
  --config runs/prep/pages.json --output runs/prep/schemas-v2/dashdish \
  --prompt-file runs/prep/schema-prompt.txt --model gemini-2.5-flash \
  --ledger runs/shared-ledger.json --budget-usd 2
```

For example, add instructions to distinguish a product card from its add-to-cart button, or to use a consistent name for the same action across page variants. Preserve the six placeholders `{url}`, `{plural}`, `{existing_defs_section}`, `{html_section}`, `{known_pages_section}`, and `{modal_hint_section}`. Literal JSON braces in the template must remain doubled (`{{` and `}}`) because the generator formats the template before sending it. The command validates these placeholders before making a request and saves the exact template as `schema-prompt.txt` beside `build.json`.

Use a vision-capable model for this step and set its provider key in `.env`. Extracted schemas must retain `$defs` for actionable elements, the `opensPage` and `opensModal` relationships, and stable names. Manually correct the generated JSON when needed. If you rename elements after fitting a profile, relabel the traces and rebuild that profile so its keys match.

For dynamic URLs, edit `url_mappings.json` after extraction. For example, `"regex:^https://myshop\\.example/products/[^/?]+$": "product"` maps product-detail URLs to a schema named `product`. Use `--application myshop` and output to `schemas/myshop/` for a new application; `--schema-root` always points to the parent `schemas/` directory.

### 3. Collect timed execution traces

Copy a scheduler configuration and set the execution model, representative task list, and these fields:

```yaml
strategy: serial
save_browser_history: true
```

For example:

```sh
cp configs/scheduler-real-smoke.yaml runs/trace-collection.yaml
# Edit runs/trace-collection.yaml as described above.
uv run --extra browser --env-file .env agent-jit evaluate \
  --config runs/trace-collection.yaml --output runs/traces
```

Histories are written under `runs/traces/<trial-id>/browser-histories/`. Collect enough tasks and repetitions to cover the elements you want to profile. All traces in one execution-model profile should use the same execution configuration.

Local history collection needs no telemetry service. Existing Laminar trace exports can also be used. Native histories contain measured model-step durations, not a full event-level breakdown. Failed steps are kept as latency observations; an agent's self-reported success does not decide which steps are sampled.

### 4. Parse the traces

Copy only the saved worker histories into a collection directory:

```sh
uv run python - <<'PYCODE'
from pathlib import Path
from shutil import copy2
output = Path("runs/raw-histories")
output.mkdir(exist_ok=True)
for source in Path("runs/traces").glob("*/browser-histories/*.json"):
    copy2(source, output / source.name)
PYCODE
```

Saved history filenames already include their trial and worker IDs. Do not include `results.jsonl`, metrics, or page schemas in this input directory.

```sh
uv run agent-jit parse-traces \
  --input-dir runs/raw-histories --format browser-history \
  --output runs/parsed-traces
```

This makes no model calls. It writes `steps/<trace-id>.json` and `build.json` with the source-to-parsed index mapping. Each parsed step includes its observed page/context and `total_latency`.

For Laminar exports, use `--format chat-spans` or `--format agent-run` according to the recorded sequence; `auto` uses the original parser's preference. Do not reuse element mappings from a differently indexed representation. The Browser-Use importer excludes the initial-navigation record because its timestamps do not measure the action duration. Missing or invalid measured timings are reported rather than assigned invented latency.

### 5. Label steps with their target elements

```sh
uv run --extra browser --env-file .env agent-jit label-traces \
  --trace-steps runs/parsed-traces/steps \
  --schema-root runs/prep/schemas --application dashdish \
  --model gpt-4.1-2025-04-14 \
  --ledger runs/shared-ledger.json --budget-usd 1 \
  --output runs/labeled-traces
```

The labeler maps each step to its **ultimate target element**. Exploratory actions can therefore contribute to the latency of the element the agent was trying to reach. Successful labeling writes `site_rep.json`, which maps element names to trace IDs and step indices. Diagnostics identify unresolved elements or URLs that need schema coverage.

The paper uses GPT-4.1 for offline extraction, labeling, and tool generation. The model labeling a trace can differ from the execution model whose latency is being measured; keep those roles distinct.

### 6. Fit latency distributions

```sh
uv run agent-jit build-profile \
  --site runs/labeled-traces/site_rep.json \
  --trace-steps runs/parsed-traces/steps \
  --execution-model gpt-4.1 --output runs/new-profile
```

This local stage sums the latencies of an element's mapped steps within each trace, then fits distributions across traces using the original fitter. It writes `element_latencies.json` and `build.json`. With at most three observations, the original fitter uses the first observation as a constant; collect additional representative traces to estimate a distribution.

### 7. Use the new cache

For standalone prediction, add:

```sh
--schema-root runs/prep/schemas \
--latency-profile runs/new-profile/element_latencies.json
```

For live execution, add these fields to the scheduler YAML:

```yaml
schema_root: runs/prep/schemas
latency_profile: runs/new-profile/element_latencies.json
profile_model: gpt-4.1
```

Then run `predict-schedule` or `evaluate` as above. Preserve the execution-model label: it also selects the cost model's calibrated overheads. Schema element names, profile keys, and predicted usage must agree.

## Intermediate files

```text
screenshots + optional HTML
  → page schemas + URL mappings
execution histories
  → parsed steps with measured latency
page schemas + parsed steps
  → site_rep.json (element-to-step mappings)
site_rep.json + parsed steps
  → element_latencies.json (latency profiles)
task + page schemas
  → usage.json (predicted element counts)
usage.json + latency profiles
  → prediction.json (selected execution strategy)
strategy + browser-agent workers
  → results.jsonl (benchmark outcomes)
```

Collecting traces and fitting profiles are offline work. Schedule prediction and browser execution are online work. Keep their costs and timing separate.

The dollar budget and request ceiling apply cumulatively to the selected ledger, including earlier preparation and execution calls. For a larger workflow, set `--max-requests` on `build-schemas`, `label-traces`, and `predict-schedule`, and `max_requests` in execution YAMLs, to the intended total ceiling. Their defaults are 128, 1024, 128, and 128 respectively. Increase `--budget-usd` / `budget_usd` deliberately when needed; changing output directories does not reset either limit.

## Change labeling and scheduling prompts

Adding tasks or pages normally requires changing the task catalog and schemas, not the prompts. For changes to the prediction policy, these are the templates actually consumed by the commands:

| Stage | Runtime prompt location | What to change |
| --- | --- | --- |
| Page extraction | `blast_scheduler/page_schema/prompts.py`: `SCHEMA_PROMPT` | Prefer the `build-schemas --prompt-file` override above |
| Trace labeling | `blast_scheduler/offline/step_processor.py`: `_SYSTEM_PROMPT_PROCESS_STEP`, `_USER_PROMPT_PROCESS_STEP` | How observed exploration is assigned to its eventual target element |
| Sequential usage prediction | `blast_scheduler/online/scheduler.py`: `SYSTEM_PROMPT_COUNT_ELEMENTS`, `USER_PROMPT_COUNT_ELEMENTS` | Which elements the task needs and how often |
| Parallel usage prediction | Same file: `SYSTEM_PROMPT_COUNT_PARALLEL`, `USER_PROMPT_COUNT_PARALLEL` | Independent work, worker counts, and repeated navigation needed by workers |

For labeling or prediction, edit the named Python constants, preserving their format placeholders and required response fields. The labeler returns `Step` from `blast_scheduler/views.py`; usage prediction returns `SchedulerPlan` and `ParallelUsageCounts` from `blast_scheduler/online/scheduler.py`. Descriptions in schemas give these prompts application context. The files in `docs/prompts/` document prompt examples; the Python constants above control execution.

After a labeling-prompt change, rerun `label-traces` and `build-profile`. After a usage-prediction change, rerun `predict-schedule`; inspect `usage.json` and compare it to the operations the task requires before running browser workers. Keep output directories separate so each version's schemas, mappings, and predictions remain available.

## Implementation

- `blast_scheduler/page_schema/generator.py`: page extraction.
- `blast_scheduler/laminar/trace_parser.py`: trace parsing.
- `blast_scheduler/offline/step_processor.py`, `site_graph_builder.py`: labeling.
- `blast_scheduler/offline/element_latencies.py`: distribution fitting.
- `blast_scheduler/online/scheduler.py`: usage prediction.
- `blast_scheduler/online/scheduler_cost_service.py`: Monte Carlo strategy comparison.
- `blastai/engine.py`, `scheduler.py`, `tools.py`: browser-agent execution.
- `agent_jit/adapters/scheduler.py`: benchmark execution path.

## New applications and models

The offline `scheduler` and `predict-schedule` commands accept custom application and profile names. Supply `--schema-root` (the parent of the application directory), `--latency-profile`, and measured overheads with `--overheads '{"hedge":15,"parallel":30,"page_read":10,"repeat_interaction":7}'`. These four example values are in seconds; measure replacements for your environment. A custom model name requires explicit overheads so it cannot silently inherit another model's costs. In an evaluation YAML, use `schema_root`, `latency_profile`, `profile_model`, and `overheads_s` with the same four keys. See [custom tasks](ADDING_TASKS.md) for running the resulting configuration.

The public strategy names are `serial`, `parallel`, and `hedge`, with `jit` for task-specific selection. The earlier names `sequential` and `first_of_n` are still accepted as aliases, and the recorded data keeps those original names.

A fresh usage prediction can select a different strategy from the recorded one, even at temperature zero. The seed fixes Monte Carlo sampling for a given usage prediction; it does not guarantee identical provider responses. Save `usage.json` to repeat schedule estimation with exactly the same input.
