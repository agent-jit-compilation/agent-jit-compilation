# Fresh reproduction, October 2026

We reran the four end-to-end methods with Gemini-2.5-Flash on the 27 REAL tasks, three runs per task, using the configurations in `configs/reproduce/`. Answers were parsed with GPT-4.1 (`evaluator: openai`), because the paper's Claude Sonnet 4 parser has been retired.

| Method | Paper latency | Fresh latency | Paper success | Fresh success |
| --- | ---: | ---: | ---: | ---: |
| JIT-Planner | 8.3 s | 10.9 s | 92.6% | 90.1% |
| Browser-Use +cache | 70.2 s | 60.6 s | 76.5% | 66.7% |
| Browser-Use | 96.5 s | 85.7 s | 55.6% | 55.6% |
| JIT-Scheduler | 118.4 s | 69.8 s | 60.5% | 71.6% |

Paper values are the recorded Gemini-2.5-Flash runs on the same tasks. Success is binary. JIT-Planner and Browser-Use +cache runs pass only at full score. Browser-Use and JIT-Scheduler use the harness verdict, as the paper's scheduler figure does.

The paper's conclusions hold:

- JIT-Planner is 5.6× faster than Browser-Use +cache and 7.9× faster than Browser-Use, with higher success than both.
- JIT-Scheduler succeeds more often than serial execution.

Differences from the paper:

- **JIT-Planner latency:** execution time matches (6.7 s fresh vs. 7.0 s in the paper). The difference is in planning time, which is measured differently. The paper counted the selected candidate's own generation time (1.3 s). The live planner waits for all 32 parallel candidates (4.1 s).
- **Browser-Use +cache:** success is 10 points lower. The failures are agent mistakes, such as skipping a vendor or writing results to a file instead of the answer. They are not harness errors.
- **JIT-Scheduler:** faster and more successful than recorded. The paper combined separately recorded runs of each strategy. These are live runs, with faster model serving than in early 2026.

## Fixes from this reproduction

- [#1](https://github.com/agent-jit-compilation/agent-jit-compilation/pull/1) Sample planner candidates at temperature 0.5, as the paper did. At 0.0, three tasks had no valid plan, and JIT-Planner reached 81.5% on one run per task.
- [#2](https://github.com/agent-jit-compilation/agent-jit-compilation/pull/2) Bound screenshot costs by image tokens instead of base64 bytes. Before this fix, a $50 scheduler budget ran out after 4 of 81 trials.
- [#3](https://github.com/agent-jit-compilation/agent-jit-compilation/pull/3) Accept the filler keys that Gemini adds when it calls cached tools without arguments.
- [#4](https://github.com/agent-jit-compilation/agent-jit-compilation/pull/4) Record a verifier's success with partial credit, instead of stopping the campaign.

## GPT-6.1 Terra, October 2026

We ran JIT-Planner and Browser-Use +cache with GPT-6.1 Terra through Amazon Bedrock on the same 27 tasks, three runs each.

| Method | Latency | Success |
| --- | ---: | ---: |
| JIT-Planner | 17.7 s | 95.1% |
| Browser-Use +cache | 39.4 s | 88.9% |

JIT-Planner is 2.2× faster, with higher success. Both pass only at full score. As in the paper, JIT-Planner latency is the selected plan's generation time plus its execution. Per-run records are in [`data/recorded/live/gpt-6.1-terra.jsonl`](../data/recorded/live/gpt-6.1-terra.jsonl).

## Claude Code, October 2026

We ran Claude Code 2.1.316 with Claude Sonnet 5.5 on the same 27 tasks, three runs each (`agent_jit.adapters.claude_code`). It browses with the agent-browser skill, driving the trial's own browser over CDP, and is scored like the other harnesses.

| Method | Latency | Success | Cost per run |
| --- | ---: | ---: | ---: |
| Claude Code (Sonnet 5.5) | 64.7 s | 92.6% | $0.17 |

Cost is Claude Code's reported estimate at API list prices. Per-run records are in [`data/recorded/live/claude-code-sonnet-5.5.jsonl`](../data/recorded/live/claude-code-sonnet-5.5.jsonl). Running it needs the `claude` command, signed in, and the agent-browser skill at `~/.claude/skills/agent-browser`.

## Rerun

Set `GOOGLE_API_KEY` and `OPENAI_API_KEY` in `.env`, then run each configuration:

```sh
for method in planner browser-use browser-use-cache scheduler; do
  uv run --extra browser --env-file .env agent-jit evaluate \
    --config configs/reproduce/$method-gemini-2.5-flash.yaml --output runs/reproduce/$method
done
```

For GPT-6.1 Terra, also set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION`, then add `--extra bedrock` and use the `*-gpt-6.1-terra.yaml` configurations.

Each configuration keeps its own ledger and spending limit. Fresh runs call live model APIs and use the hosted REAL sites, so results vary between runs.
