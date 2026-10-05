# Documentation

Agent JIT Compilation provides two workflows: compiling tool use into checked programs, and selecting how browser-agent workers execute a task.

## Get started

- [Installation and first runs](../README.md#environment-setup)
- [JIT-Planner: tools, contracts, plan generation, and execution](PLANNER.md)
- [JIT-Scheduler: schemas, profiles, schedule selection, and execution](SCHEDULER.md)
- [Model configuration, evaluators, and result files](LIVE_EVALUATION.md)
- [Benchmark tasks](TASKS.md)
- [WebArena installation, reset, and login](WEBARENA.md)

## Extend the system

- [Add a task or application](ADDING_TASKS.md)
- [Prepare tools, schemas, and latency profiles](OFFLINE_PREPARATION.md)
- [Add an agent adapter](ADDING_AN_AGENT.md)
- [Planner examples](../examples/README.md)

## Reproduce results

- [Paper results and commands](PAPER_TO_COMMANDS.md)
- [Recorded measurements and aggregation](RECORDED_RESULTS.md)

## Development

Install the core dependencies and run the tests and linter:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
```

Browser tests require the browser extra and Chromium. WebArena evaluator tests also require the prepared source checkout and NLTK data; see [WebArena setup](WEBARENA.md).

```sh
uv sync --locked --extra browser --extra webarena
uv run --extra browser patchright install chromium
uv run agent-jit prepare-webarena --output .downloads/webarena
mkdir -p .downloads/nltk
chmod 700 .downloads/nltk
uv run --extra webarena python -m nltk.downloader -d .downloads/nltk punkt_tab
uv run --extra browser --extra webarena pytest -q
```

Tests use local fixtures and mocked model responses; they do not require API keys or Docker benchmark images. CI runs core tests on Python 3.12 and 3.13, browser/evaluator tests, and recorded-result generation.

To build a source distribution containing the code and supplied data:

```sh
uv build --sdist
```

## License

Agent JIT Compilation is released under the [MIT license](../LICENSE). It includes code adapted from BLAST (MIT), the AGI SDK REAL benchmark (Apache 2.0), and WebArena (Apache 2.0); their licenses are in [third_party/](../third_party/).
