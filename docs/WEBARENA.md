# WebArena: pinned setup and isolated trials

The paper's planner evaluation includes five GitLab and five Reddit tasks. The shared runner accepts all ten task IDs from `agent-jit tasks`. Scheduling experiments use REAL only. Additional GitLab/Reddit tasks can be supplied through a custom catalog ([see below](#add-gitlab-or-reddit-tasks)).

## Prepare the evaluator

```sh
uv sync --locked --extra browser --extra webarena
uv run --extra browser patchright install chromium
uv run --extra webarena playwright install chromium
uv run agent-jit prepare-webarena --output .downloads/webarena
mkdir -p .downloads/nltk
chmod 700 .downloads/nltk
uv run --extra webarena python -m nltk.downloader -d .downloads/nltk punkt_tab
```

`prepare-webarena` fetches `web-arena-x/webarena` at `dce04686a56253aefba7b18a4fa0937cf1dc987b`, then applies the five saved files listed in `benchmarks/webarena/overrides.json`. It verifies their SHA-256 checksums and refuses to overwrite an existing directory; evaluation checks the revision and overrides again. Apart from these five files, the upstream checkout is used unchanged. The `webarena` extra pins a current, tested dependency set rather than the historical one: upstream pinned the OpenAI 0.27 SDK, while the provider override uses the v1 interface.

The NLTK tokenizer is needed by the native numeric-answer checks. Set `webarena.nltk_data` to its installation directory. Neither command calls an LLM or starts a benchmark service.

## Load the application snapshots

Run WebArena on an x86_64 Linux host with local Docker. The populated images are amd64; macOS/arm64 emulation is not a supported benchmark configuration. Allow at least 250 GB of free disk for both images and working containers. With Docker's containerd image store, observed disk use was approximately 127 GB for GitLab and 91 GB for Reddit; compressed layers and unpacked copies both consume space. The original images are distributed separately and are large; the artifact does not download them automatically. Follow the [pinned WebArena image instructions](https://github.com/web-arena-x/webarena/blob/dce04686a56253aefba7b18a4fa0937cf1dc987b/environment_docker/README.md#individual-website) to obtain:

```sh
docker load --input gitlab-populated-final-port8023.tar
docker load --input postmill-populated-exposed-withimg.tar
```

Alternatively, the Docker Hub distribution used for these GitLab/Reddit environments can be loaded with:

```sh
docker pull --platform linux/amd64 webarenaimages/gitlab-populated-final
docker pull --platform linux/amd64 webarenaimages/postmill-populated-exposed-withimg
```

Set `webarena.sites.gitlab.image` to `webarenaimages/gitlab-populated-final` and `webarena.sites.reddit.image` to `webarenaimages/postmill-populated-exposed-withimg` when using these images. These names differ from the archive names below; do not assume mirrors are byte-identical. Check `docker image inspect` and retain the image IDs recorded with each run.

The expected local image names are `gitlab-populated-final-port8023` and `postmill-populated-exposed-withimg`. The original backup did not record immutable image digests. Each new trial resolves the configured image to its local immutable ID and records that ID, architecture, and any repository digests. Matching an image name alone does not establish an exact historical environment match.

The runner creates a separate, labeled container from the image for every trial; it mounts no host directories or pre-existing volumes. It binds only to loopback, regenerates the GitLab external URL, waits for readiness, and logs into the synthetic benchmark account in a fresh browser profile. After the trial, it removes the exact container ID it created and its anonymous volumes. It checks the ownership label before removal. Existing containers are never stopped or removed; an occupied port is rejected before the campaign starts. GitLab startup/reconfiguration can take several minutes and is charged to setup time, outside online agent latency.

Configure ports, images, shared memory, and Puma workers under `webarena.sites`:

```yaml
webarena:
  checkout: .downloads/webarena
  nltk_data: .downloads/nltk
  startup_timeout_s: 600
  llm_judge: true
  sites:
    gitlab:
      image: gitlab-populated-final-port8023
      port: 8023
      shm_size: 256m
      puma_workers: 4
    reddit:
      image: postmill-populated-exposed-withimg
      port: 9999
      shm_size: 256m
```

The default four Puma workers avoid scaling to every CPU on large hosts; `shm_size` defaults to `256m`. GitLab is reconfigured and Puma restarted before the runner requires three successful login-page probes five seconds apart. Both browser and evaluator URLs use `127.0.0.1`, matching Docker's published interface and avoiding IPv6 `localhost` ambiguity.

Run on the same machine as Docker; remote Docker contexts and externally managed resets are not currently supported. The planner adapter resolves the original localhost URL patterns to the configured local ports and saves the resolved registry in the trial directory.

## Generate authentication before agent execution

WebArena's [documented auto-login setup](https://github.com/web-arena-x/webarena/blob/dce04686a56253aefba7b18a4fa0937cf1dc987b/README.md#end-to-end-evaluation) generates Playwright storage-state files. After booting the websites and setting their URL environment variables, the upstream command, run from the WebArena checkout, is:

```sh
mkdir -p .auth
python browser_env/auto_login.py
```

For only the two applications used here, run these instead (using the artifact's Python environment):

```sh
python browser_env/auto_login.py --site_list gitlab --auth_folder .auth
python browser_env/auto_login.py --site_list reddit --auth_folder .auth
```

They write `.auth/gitlab_state.json` and `.auth/reddit_state.json`. Upstream expects all seven site URL variables to be nonempty, even for a single-site login; set the unused ones to `http://127.0.0.1:1`. Both requested services must be running at their configured `GITLAB`/`REDDIT` URLs. The script uses Playwright's Chromium installation, which is separate from Patchright's.

The runner invokes this same, unchanged upstream script automatically **after each fresh snapshot starts and before the agent runs**. It supplies the resolved URLs, generates private per-trial authentication state, imports that state into the trial browser, and verifies the authenticated profile/account page. Cookies are regenerated after each reset and removed with the temporary profile; a prior snapshot's cookies are not assumed valid. The saved experiment harness also follows reset with `run_auto_login` in `experiments/webarena_env.py`.

`webarena.startup_timeout_s` bounds snapshot initialization in the parent runner (600 seconds in the supplied configuration). `setup_timeout_s` applies to browser navigation/login in the subsequently launched worker (90 seconds). The worker's process deadline starts after snapshot initialization, so these limits cover separate stages.

## Run one task

```sh
uv run --extra browser --extra webarena --env-file /path/to/private.env agent-jit evaluate \
  --config configs/planner-webarena-smoke.yaml --output runs/webarena-smoke
uv run agent-jit validate-results runs/webarena-smoke/results.jsonl
```

This selects the read-only contributor-count task `webarena-308`, two candidates, one trial, and the shared $1 API spending ledger. Docker setup happens before any model call. Change `tasks` to select other paper tasks; do not substitute production GitLab or Reddit accounts/endpoints. The same reset, browser, and evaluator path is available to a new adapter.

`evaluator: webarena_native` runs the saved native evaluator in a separate process attached to the trial's live browser. It retains string matching, DOM assertions, navigation, and the evaluator's own settling delays. Browser-dependent checks use the live DOM, not an output-only approximation. Evaluator failures become `evaluation_error`, and setup/timeout failures remain in the attempted-trial denominator.

The smoke configuration enables the original optional GPT-4.1-mini string-match judge with `webarena.llm_judge: true`. It requires `OPENAI_API_KEY` and shares the run's ledger. Native checks run first; the judge can accept a semantically correct sentence that fails exact matching. For example, native task 308 accepts `Shawn Allen` but rejects `Shawn Allen with 95 commits`. Set `llm_judge: false` for strict native scoring. The chosen mode and native/judge scores are recorded with the result; use the same evaluator setting across comparisons.

## Add GitLab or Reddit tasks

Create `runs/webarena-tasks.json` without editing the paper's task catalog or YAML definitions:

```json
{"tasks": [
  {"id": "gitlab-csvkit-contributor", "benchmark": "WebArena", "webarena_task_id": 312},
  {"id": "reddit-downvoted-comments", "benchmark": "WebArena", "webarena_task_id": 30}
]}
```

Copy the WebArena smoke YAML and set `task_catalog: runs/webarena-tasks.json` and `tasks: [gitlab-csvkit-contributor]`. Instructions, start URLs, and assertions are loaded from the supplied pinned `benchmarks/webarena/overrides/config_files/test.raw.json`; the normal container reset, authentication, endpoint rewriting, and native evaluation still apply. Inspect a task before running:

```sh
uv run agent-jit tasks gitlab-csvkit-contributor --catalog runs/webarena-tasks.json
```

This route supports single-site GitLab/Reddit tasks with exact string, required-substring, URL, or DOM checks. Tasks requiring `fuzzy_match` (including infeasible-task judgments such as task 726) are explicitly rejected **before setup**, even when the fallback judge is enabled. Their native evaluation semantics are not implemented by the fallback judge.

The supplied tools cover the paper workflows, not every task in those applications. `goto_project` accepts a full `namespace/project` path or a partial name such as `a11yproject`. It resolves partial names from project links on the current page, falling back to GitLab's project-search page. Missing or ambiguous matches fail with a diagnostic rather than guessing a namespace. New workflows may still require additional tools; supply an extended library with `tool_registry` and test its runtime predicates.

GitLab tool completion checks resume after a page reload without resubmitting the action, and commit-date filtering uses UTC timestamps. Each run saves its registry hash in `registry-input.json`.

## Check the environment before running

```sh
docker info
docker image inspect gitlab-populated-final-port8023
docker image inspect postmill-populated-exposed-withimg
```

The populated snapshots are separate from this repository and require substantial disk space for both the archive and unpacked image. Obtain them through the linked WebArena distribution instructions. If a download produces a small HTML file instead of a tar archive, the mirror failed; use another mirror listed upstream. Verify that the expected images are installed before starting an evaluation.

The runner checks Docker connectivity, image availability, ports, evaluator support, and configured tokenizer data once before starting trials. It then handles application readiness, fresh authentication, and owned-container cleanup. Inspect the persistent per-trial `auto-login.log`, `worker.log`, the environment/setup metadata, and the trial's status in `results.jsonl` if setup fails. Authentication state is regenerated for each restored snapshot.
