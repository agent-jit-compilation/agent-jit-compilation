# Prepare planner and scheduler data

The paper's offline pipeline is **Extract → Label → Codegen** (Appendix A, Figure 11). Its outputs are reusable inputs to the two independently evaluated components:

| Component | Cached input | Detailed workflow |
| --- | --- | --- |
| JIT-Planner | Executable application tools with type and state contracts | [Prepare a tool library](PLANNER.md#prepare-a-tool-library) |
| JIT-Scheduler | Page schemas and execution-model-specific element-latency profiles | [Prepare schemas and profiles](SCHEDULER.md#prepare-page-schemas-and-latency-profiles) |

The supplied caches under `data/` can be used directly. For a new application or model, the linked guides cover screenshots, trace collection, extraction, parsing, labeling, fitting, tool authoring/generation, and the commands consuming each intermediate file.

Tools may be authored with Codex, Claude Code, or by hand; the automatic synthesis command is optional. Check a registry locally with:

```sh
uv run python experiments/validate_tools.py path/to/registry.json
```

Registry consistency checks, static program validation, runtime predicates, and benchmark outcome checks serve different purposes. Follow the [planner guide](PLANNER.md#check-and-test-the-tools) to use them together.

Preparing tools and profiles is offline work. Generating task-specific plans and selecting schedules are online work. Record their costs separately when reporting latency.
