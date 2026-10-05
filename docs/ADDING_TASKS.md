# Add tasks or an application

For a new task on Dashdish, Gomail, or Omnizon, reuse the supplied registry and scheduler caches when they cover the task's actions. A new application needs a planner tool registry and/or scheduler page schemas and latency profiles, depending on which component you want to run. Neither component requires the other's cache.

For additional GitLab/Reddit tasks, use the [WebArena catalog format](WEBARENA.md#add-gitlab-or-reddit-tasks), which preserves resets, login, and native evaluation.

The paper catalog remains the default. For another application, provide a JSON file with a `tasks` list and set `task_catalog: runs/my-tasks.json` in the evaluation YAML. IDs in the YAML's `tasks` list then refer to this catalog.

```json
{
  "tasks": [{
    "id": "shop-count-items",
    "application": "myshop",
    "task_details": "How many items are listed? Return the count as JSON.",
    "initial_url": "http://localhost:8000/",
    "benchmark_revision": "myshop-v1",
    "verifier": {"type": "custom", "callable": "my_verifiers:check_count"}
  }]
}
```

The verifier is a trusted local Python callable. Put `my_verifiers.py` in the repository root, install it as a module, or add its directory to `PYTHONPATH` (for example `export PYTHONPATH="/path/to/my-verifiers${PYTHONPATH:+:$PYTHONPATH}"`):

```python
import json


def check_count(task, answer, final_state):
    try:
        correct = json.loads(answer)["count"] == 12
    except (ValueError, KeyError, TypeError):
        correct = False
    return {"correct": correct, "score": float(correct)}
```

The function receives the task, the agent's answer, and optional final state. It may be async. It must return a boolean `correct` (not a string) and a finite numeric `score` in [0, 1]; a correct result must have score 1. The runner checks that the verifier can be imported before starting paid execution. If the application exposes JSON inside a `<pre>` on a state endpoint, set `verifier.state_url`, for example `/finish`. The runner reads that endpoint in the active agent tab after execution. For a custom task with parallel scheduling, `final_state` is `{"workers": {"worker-id": state, ...}}` for completed workers (including the coordinator); your verifier decides how to combine them. Serial and hedged execution return the selected worker's state directly. Without a state URL, `final_state` is `None`. Use `evaluator: json` for this example: custom verification is performed by your callable, without the built-in answer parser.

For the planner, supply `tool_registry: runs/my-tools/registry.json` with the observation/navigation operations and state contracts described in [PLANNER.md](PLANNER.md). For the scheduler, supply the page schemas, fitted profile, and overheads described in [SCHEDULER.md](SCHEDULER.md#new-applications-and-models). Choose execution and auxiliary models using [model configuration](LIVE_EVALUATION.md#choose-models). A custom latency-profile name labels measurements; it does not register a new model provider.

Tasks using your own verifier are labeled `benchmark: custom`, even when their URL points to a REAL clone. This keeps them distinct from the paper catalog.

Each trial starts a fresh browser profile. This resets client state, not a custom application's server database. Restore server state between trials when the task changes it. Run commands from the repository root so local verifier modules and the research task helpers are importable.

## Run the planner on the new task

Save the catalog above as `runs/my-tasks.json`, implement its verifier, and [prepare the tool registry](PLANNER.md#prepare-a-tool-library). Copy the supplied planner configuration:

```sh
cp configs/planner-real-smoke.yaml runs/myshop-planner.yaml
```

Replace or add these fields in the copy:

```yaml
task_catalog: runs/my-tasks.json
tasks: [shop-count-items]
tool_registry: runs/my-tools/registry.json
model: gpt-4.1
evaluator: json
```

Then generate, check, select, execute, and evaluate:

```sh
uv run --extra browser --env-file .env agent-jit evaluate \
  --config runs/myshop-planner.yaml --output runs/myshop-planner-run
uv run agent-jit validate-results runs/myshop-planner-run/results.jsonl
```

The supplied planner expects its registry's navigation/observation tools to establish an initial abstract state. Check `worker.log`, `candidates.json`, and `selected.py` when diagnosing a failed trial. If generation returns too few valid plans, inspect the validation errors and tool contracts before increasing the attempt limit.

## Run the scheduler on the new task

You can first execute a fixed strategy **without page schemas or latency profiles**. This is also how to collect the traces needed to build those inputs:

```sh
cp configs/scheduler-real-smoke.yaml runs/myshop-scheduler.yaml
```

Replace or add:

```yaml
task_catalog: runs/my-tasks.json
tasks: [shop-count-items]
model: gpt-4.1
mini_model: gpt-4.1-mini
predictor_model: gpt-4.1
evaluator: json
strategy: serial
save_browser_history: true
```

```sh
uv run --extra browser --env-file .env agent-jit evaluate \
  --config runs/myshop-scheduler.yaml --output runs/myshop-traces
```

Collect representative tasks and repetitions, then follow [schema extraction → trace labeling → profile fitting](SCHEDULER.md#prepare-page-schemas-and-latency-profiles). Use `myshop` consistently as the application name; extract schemas into `runs/prep/schemas/myshop/`. The guide shows how to customize the extraction prompt and choose its model.

Once these inputs exist, change the scheduler copy to:

```yaml
strategy: jit
schema_root: runs/prep/schemas
latency_profile: runs/myshop-profile/element_latencies.json
profile_model: gpt-4.1
```

Run `evaluate` again with a new output directory. JIT selection now predicts element usage for the task, estimates strategy costs, and executes its choice. The supplied GPT-4.1 overhead defaults are a starting point; measure and override `overheads_s` when moving to a different environment. For another execution model, collect its own traces and provide explicit overheads as described in [new applications and models](SCHEDULER.md#new-applications-and-models).

A missing element in `usage.json` usually calls for better page coverage or descriptions; a missing latency-profile key calls for trace coverage and relabeling. Inspect `schedule/prediction.log` for schedule selection and `worker.log` for execution. The independent verifier decides whether the requested task was completed; selecting a valid plan or schedule does not establish task success.
