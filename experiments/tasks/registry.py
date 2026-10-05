"""
Task validator registry with unified validation interface.

Maps task IDs to their corresponding validator implementations.
Supports both script-based (state + string matching) and LLM-based (structured output) validation.
Now also supports WebArena evaluation.
"""

import importlib
import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import yaml

from .base import TaskValidator

logger = logging.getLogger(__name__)


# Type alias for script evaluator function
ScriptEvaluator = Callable[[Dict[str, Any], str], Tuple[bool, float, Dict[str, Any]]]


# ============================================================================
# WebArena Evaluator Wrapper
# ============================================================================


def _run_async(coro: Any) -> Any:
    """
    Run an async coroutine synchronously.

    NOTE: This is a fallback for BrowserUsePageAdapter. For program_html
    evaluation, use prefetch_program_html_data() instead to avoid deadlocks.

    This function should only be used when NOT in an async context (i.e.,
    when there's no running event loop).
    """
    import asyncio

    try:
        # Check if there's a running event loop
        asyncio.get_running_loop()
        # If we're here, there's a running loop - this is dangerous!
        # Log a warning and try to run anyway (may deadlock)
        logger.warning(
            "_run_async called from async context - this may deadlock! "
            "Use prefetch_program_html_data() for async pre-fetching instead."
        )
        # Can't safely run from within an async context
        raise RuntimeError("Cannot run async from within running event loop")
    except RuntimeError:
        # No running event loop - safe to use asyncio.run()
        return asyncio.run(coro)


class PreFetchedPageAdapter:
    """
    A page adapter that returns pre-fetched results.

    Used for program_html evaluation where we pre-fetch all the needed data
    asynchronously before calling the synchronous WebArena evaluator.
    """

    def __init__(self, initial_url: str):
        """
        Args:
            initial_url: The starting URL
        """
        self._url = initial_url
        self._content_cache: Dict[str, str] = {}  # url -> content
        self._eval_cache: Dict[
            str, Dict[str, Any]
        ] = {}  # url -> {expression -> result}

    @property
    def url(self) -> str:
        """Return the current URL."""
        return self._url

    def goto(self, url: str) -> None:
        """Navigate to URL (just update internal state)."""
        logger.debug(f"PreFetchedPageAdapter.goto({url}) - switching to cached URL")
        self._url = url

    def content(self) -> str:
        """Return page content from cache."""
        cached = self._content_cache.get(self._url, "")
        logger.debug(
            f"PreFetchedPageAdapter.content() for {self._url}: {len(cached)} chars"
        )
        return cached

    def evaluate(self, expression: str) -> Any:
        """Return evaluation result from cache."""
        url_cache = self._eval_cache.get(self._url, {})
        result = url_cache.get(expression, "")
        logger.debug(
            f"PreFetchedPageAdapter.evaluate({expression[:50]}...) for {self._url}: {result}"
        )
        return result

    def add_prefetched(
        self, url: str, content: str, evaluations: Dict[str, Any]
    ) -> None:
        """Add pre-fetched data for a URL.

        Merges evaluations with any existing cached evaluations for this URL,
        allowing multiple program_html targets with the same URL to all be cached.
        """
        self._content_cache[url] = content
        # Merge evaluations instead of replacing, so multiple targets for same URL work
        if url in self._eval_cache:
            self._eval_cache[url].update(evaluations)
        else:
            self._eval_cache[url] = evaluations
        total_evals = len(self._eval_cache.get(url, {}))
        logger.debug(
            f"PreFetchedPageAdapter: cached data for {url} - {len(content)} chars, {total_evals} total evals"
        )


async def prefetch_program_html_data(
    config: Dict[str, Any],
    browser_session: Any,
    page: Any,
    current_url: str,
) -> PreFetchedPageAdapter:
    """
    Pre-fetch all data needed for program_html evaluation.

    This runs asynchronously in the event loop context BEFORE calling
    the synchronous WebArena evaluator, avoiding the deadlock that would
    occur if we tried to run async code from within the sync evaluator.

    Args:
        config: WebArena task config dict
        browser_session: browser-use BrowserSession for navigation
        page: browser-use Page object for JS evaluation
        current_url: The current page URL

    Returns:
        PreFetchedPageAdapter with all needed data cached
    """
    import asyncio

    adapter = PreFetchedPageAdapter(current_url)

    eval_config = config.get("eval", {})
    program_html_targets = eval_config.get("program_html", [])

    if not program_html_targets:
        logger.debug("No program_html targets to pre-fetch")
        return adapter

    logger.info(
        f"Pre-fetching data for {len(program_html_targets)} program_html targets"
    )

    for i, target in enumerate(program_html_targets):
        target_url = target.get("url", "last")
        locator = target.get("locator", "")

        # Handle special URL cases
        if target_url.startswith("func:"):
            func = target_url.split("func:")[1]
            func = func.replace("__last_url__", current_url)
            try:
                target_url = eval(func)
            except Exception as e:
                logger.warning(f"Failed to evaluate URL function: {e}")
                continue

        # Navigate to target URL if not "last"
        if target_url != "last":
            logger.info(f"Pre-fetch: navigating to {target_url}")
            try:
                await browser_session.navigate_to(target_url)
                # Wait for page to load
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Pre-fetch navigation failed for {target_url}: {e}")
                continue
        else:
            target_url = current_url

        # Get page content
        try:
            content = await page.evaluate("() => document.documentElement.outerHTML")
            content = content or ""
            logger.info(f"Pre-fetch: got page content ({len(content)} chars)")
        except Exception as e:
            logger.error(f"Pre-fetch content failed for {target_url}: {e}")
            content = ""

        # Evaluate the locator expression if it's a document query
        evaluations = {}
        if locator.startswith("document.") or locator.startswith("[...document."):
            # Handle prep_actions if present
            if "prep_actions" in target:
                for prep_action in target.get("prep_actions", []):
                    try:
                        await page.evaluate(f"() => {prep_action}")
                    except Exception:
                        pass

            # Evaluate the main locator
            expression = f"() => {locator}"
            try:
                result = await page.evaluate(expression)
                evaluations[expression] = result if result is not None else ""
                logger.info(f"Pre-fetch eval '{locator[:80]}' = '{result}'")
            except Exception as e:
                logger.error(f"Pre-fetch evaluate failed for {locator}: {e}")
                evaluations[expression] = ""

                # Debug: try to find status-related elements on the page
                try:
                    debug_result = await page.evaluate("""() => {
                        // Try various selectors that might contain status
                        const selectors = [
                            '.cover-status',
                            '.user-status',
                            '.availability-status',
                            '[data-testid="user-status"]',
                            '.profile-header',
                            '.cover-title',
                            'h1.cover-title'
                        ];
                        const found = {};
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) {
                                found[sel] = el.textContent.substring(0, 100);
                            }
                        }
                        // Also get page title
                        found['title'] = document.title;
                        found['url'] = window.location.href;
                        return JSON.stringify(found, null, 2);
                    }""")
                    logger.info(f"Pre-fetch debug - page elements: {debug_result}")
                except Exception as debug_e:
                    logger.debug(f"Debug selector check failed: {debug_e}")

        # Cache all data for this URL
        adapter.add_prefetched(target_url, content, evaluations)

    logger.info("Pre-fetch complete")
    return adapter


async def llm_judge_answer(
    agent_output: str,
    reference_answers: Dict[str, Any],
    task_intent: str,
) -> Tuple[bool, float, str]:
    """
    Use an LLM to judge if the agent's output matches the reference answers.

    This is more robust than exact string matching since agents often provide
    verbose answers with context.

    Args:
        agent_output: The agent's full response
        reference_answers: Dict from WebArena config (e.g., {"exact_match": "Shawn Allen"})
        task_intent: The original task/question

    Returns:
        Tuple of (is_correct, score, reasoning)
    """
    import os

    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.warning(
            "OpenAI not available for LLM judge, falling back to string match"
        )
        return False, 0.0, "OpenAI not available"

    # Format reference answers for the prompt
    ref_str = ""
    for match_type, value in reference_answers.items():
        if match_type == "exact_match":
            ref_str += f"Expected answer (exact): {value}\n"
        elif match_type == "must_include":
            if isinstance(value, list):
                ref_str += f"Must include all of: {', '.join(value)}\n"
            else:
                ref_str += f"Must include: {value}\n"
        elif match_type == "fuzzy_match":
            ref_str += f"Expected answer (fuzzy): {value}\n"

    prompt = f"""You are evaluating whether an AI agent correctly answered a task.

Task: {task_intent}

Reference Answer:
{ref_str}

Agent's Response:
{agent_output}

Does the agent's response contain the correct answer? The agent may provide additional context or explanation, but the key answer should match the reference.

Respond with:
- "CORRECT" if the agent's answer matches the reference (semantically equivalent)
- "INCORRECT" if the agent's answer does not match or is wrong

Then provide a brief explanation.

Format your response as:
VERDICT: [CORRECT/INCORRECT]
REASON: [brief explanation]"""

    try:
        client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0,
        )

        result = response.choices[0].message.content.strip()

        # Parse the result
        is_correct = "VERDICT: CORRECT" in result.upper()
        score = 1.0 if is_correct else 0.0

        # Extract reasoning
        reason = result
        if "REASON:" in result.upper():
            reason = result.split("REASON:", 1)[-1].strip()

        logger.info(
            f"LLM judge: {'CORRECT' if is_correct else 'INCORRECT'} - {reason[:100]}"
        )
        return is_correct, score, reason

    except Exception as e:
        logger.error(f"LLM judge failed: {e}")
        return False, 0.0, f"LLM judge error: {e}"


class BrowserUsePageAdapter:
    """
    Adapter to make browser-use Page compatible with WebArena's evaluation.

    WebArena evaluators expect a Playwright Page or PseudoPage with:
    - url: str property
    - goto(url): method
    - content(): method
    - evaluate(js): method

    This adapter wraps browser-use's async CDP-based Page to provide sync equivalents.
    """

    def __init__(self, browser_use_page: Any, current_url: str):
        """
        Args:
            browser_use_page: The browser-use Page object
            current_url: The current URL (since browser-use uses async)
        """
        self._page = browser_use_page
        self._url = current_url
        self._content_cache: Optional[str] = None
        # Get browser session for navigation
        self._browser_session = (
            getattr(browser_use_page, "_browser_session", None)
            if browser_use_page
            else None
        )

    @property
    def url(self) -> str:
        """Return the current URL."""
        return self._url

    def goto(self, url: str) -> None:
        """Navigate to URL using browser-use's async method."""
        if self._browser_session is None:
            logger.warning(
                f"BrowserUsePageAdapter.goto({url}) - no browser session, cannot navigate"
            )
            self._url = url
            return

        try:
            logger.debug(f"BrowserUsePageAdapter.goto({url}) - navigating...")
            _run_async(self._browser_session.navigate_to(url))
            self._url = url
            # Wait for page to load
            import time

            time.sleep(2)
            logger.debug(f"BrowserUsePageAdapter.goto({url}) - navigation complete")
        except Exception as e:
            logger.error(f"BrowserUsePageAdapter.goto({url}) failed: {e}")
            self._url = url

    def content(self) -> str:
        """Return page content using JavaScript evaluation."""
        if self._content_cache:
            return self._content_cache

        if self._page is None:
            logger.warning("BrowserUsePageAdapter.content() - no page, returning empty")
            return ""

        try:
            logger.debug("BrowserUsePageAdapter.content() - fetching page content...")
            # Use evaluate to get the full HTML
            content = _run_async(
                self._page.evaluate("() => document.documentElement.outerHTML")
            )
            self._content_cache = content or ""
            logger.debug(
                f"BrowserUsePageAdapter.content() - got {len(self._content_cache)} chars"
            )
            return self._content_cache
        except Exception as e:
            logger.error(f"BrowserUsePageAdapter.content() failed: {e}")
            return ""

    def evaluate(self, expression: str) -> Any:
        """Evaluate JavaScript using browser-use's async method."""
        if self._page is None:
            logger.warning(
                "BrowserUsePageAdapter.evaluate() - no page, returning empty"
            )
            return ""

        try:
            logger.debug("BrowserUsePageAdapter.evaluate() - executing JS...")
            # WebArena passes expressions like "() => document.querySelector(...)"
            # browser-use expects the same format
            result = _run_async(self._page.evaluate(expression))
            logger.debug(
                f"BrowserUsePageAdapter.evaluate() - result: {str(result)[:100]}..."
            )
            return result
        except Exception as e:
            logger.error(f"BrowserUsePageAdapter.evaluate() failed: {e}")
            return ""

    def set_content_cache(self, content: str) -> None:
        """Set cached content for evaluation."""
        self._content_cache = content


class WebArenaEvaluatorWrapper:
    """
    Wrapper to use WebArena evaluators with our UnifiedValidator interface.

    WebArena evaluators expect:
    - trajectory: list of (Action, StateInfo) pairs
    - config_file: Path to task config JSON
    - page: Playwright Page object
    - client: CDP session (optional)

    This wrapper adapts our interface (final_result, final_state) to WebArena's.
    """

    def __init__(self, webarena_path: Optional[Path] = None):
        """
        Initialize the WebArena evaluator wrapper.

        Args:
            webarena_path: Path to webarena directory. If None, will try to auto-detect.
        """
        self._webarena_path = webarena_path
        self._evaluator_router = None
        self._initialized = False

    def _ensure_initialized(self):
        """Lazily initialize WebArena imports."""
        if self._initialized:
            return

        if self._webarena_path is None:
            # Try to find webarena in common locations
            possible_paths = [
                Path(__file__).parent.parent.parent / "webarena",
                Path.cwd() / "webarena",
            ]
            for p in possible_paths:
                if (p / "evaluation_harness").exists():
                    self._webarena_path = p
                    break

        if self._webarena_path is None:
            logger.warning(
                "Could not find webarena directory. "
                "Will use simplified evaluation (string_match only)."
            )
            self._initialized = True
            return

        # Add webarena to path for imports
        webarena_str = str(self._webarena_path)
        if webarena_str not in sys.path:
            sys.path.insert(0, webarena_str)

        # Set dummy env vars for WebArena's env_config.py
        # Actual URLs come from task configs, these are just to satisfy import checks
        import os

        dummy_url = "http://localhost:9999"
        for var in [
            "REDDIT",
            "SHOPPING",
            "SHOPPING_ADMIN",
            "GITLAB",
            "WIKIPEDIA",
            "MAP",
            "HOMEPAGE",
        ]:
            if not os.environ.get(var):
                os.environ[var] = dummy_url

        try:
            from evaluation_harness import evaluator_router
            from evaluation_harness.helper_functions import PseudoPage

            # Patch PseudoPage to accept our adapter by making beartype less strict
            # We do this by registering our adapter as a virtual subclass
            try:
                from abc import ABCMeta

                # Register BrowserUsePageAdapter as virtual subclass of PseudoPage's metaclass
                # This makes beartype accept it as a valid PseudoPage-like object
                PseudoPage.__class__ = type("PseudoPageMeta", (ABCMeta,), {})
                PseudoPage.__class__.register(PseudoPage, BrowserUsePageAdapter)
            except Exception as e:
                logger.debug(f"Could not register adapter with PseudoPage: {e}")

            self._evaluator_router = evaluator_router
            self._PseudoPage = PseudoPage
            self._initialized = True
            logger.info(f"WebArena evaluator initialized from {self._webarena_path}")
        except ImportError as e:
            logger.warning(
                f"Could not import WebArena evaluator ({e}). "
                "Will use simplified evaluation."
            )
            self._evaluator_router = None
            self._initialized = True
        except Exception as e:
            logger.warning(
                f"WebArena evaluator init failed: {e}. Using simplified evaluation."
            )
            self._evaluator_router = None
            self._initialized = True

    def evaluate_with_page(
        self,
        config_file: Union[str, Path],
        trajectory: List[Any],
        page: Any,
        client: Any = None,
        page_url: Optional[str] = None,
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Run WebArena evaluation with a Playwright page.

        This is the preferred method when you have access to the browser page.

        Args:
            config_file: Path to WebArena task config JSON
            trajectory: List of (Action, StateInfo) pairs from agent execution
            page: Playwright Page object or browser-use Page
            client: CDP session (optional)
            page_url: Current page URL (required for browser-use pages)

        Returns:
            Tuple of (success, score, details)
        """
        self._ensure_initialized()

        # Check if page is already an adapter (don't re-wrap)
        is_adapter = isinstance(page, (PreFetchedPageAdapter, BrowserUsePageAdapter))

        # Check if this is a browser-use page (not native Playwright)
        is_browser_use_page = (
            page is not None
            and not is_adapter
            and (
                hasattr(page, "_browser_session")
                or page.__class__.__module__.startswith("browser_use")
            )
        )

        # For browser-use pages, create an adapter with current URL
        eval_page = page
        if is_adapter:
            # Already an adapter, use as-is
            logger.debug(f"Using existing adapter: {type(page).__name__}")
            eval_page = page
        elif is_browser_use_page:
            current_url = (
                page_url or getattr(page, "url", "") or getattr(page, "_url", "")
            )
            if current_url:
                logger.debug(
                    f"Browser-use page detected, using adapter with URL: {current_url}"
                )
            else:
                logger.warning(
                    "Browser-use page detected but no URL available - evaluation may fail"
                )
            eval_page = BrowserUsePageAdapter(page, current_url)

        # Use native WebArena evaluator if available
        if self._evaluator_router is not None:
            try:
                evaluator = self._evaluator_router(Path(config_file))
                score = evaluator(trajectory, config_file, eval_page, client)
                return (
                    score == 1.0,
                    float(score),
                    {
                        "webarena_score": score,
                        "config_file": str(config_file),
                        "evaluator": "native_webarena",
                    },
                )
            except Exception as e:
                logger.error(f"WebArena evaluation failed: {e}")
                import traceback

                logger.error(traceback.format_exc())
                # Re-raise the error - no fallback
                raise RuntimeError(f"WebArena evaluation failed: {e}") from e

        # WebArena evaluator not available
        raise RuntimeError(
            "WebArena evaluator not initialized. "
            "Make sure webarena directory exists and dependencies are installed."
        )

    def evaluate_string_match(
        self,
        config: Dict[str, Any],
        final_answer: str,
    ) -> Tuple[bool, float, Dict[str, Any]]:
        """
        Run WebArena string-match evaluation without browser access.

        Uses WebArena's native StringEvaluator.

        Args:
            config: WebArena task config dict (must have 'eval' field)
            final_answer: The agent's final answer string

        Returns:
            Tuple of (success, score, details)
        """
        self._ensure_initialized()

        eval_config = config.get("eval", {})
        eval_types = eval_config.get("eval_types", [])

        # Only handle string_match evaluation here
        if "string_match" not in eval_types:
            return (
                False,
                0.0,
                {
                    "error": f"Cannot evaluate without browser: eval_types={eval_types}",
                    "requires_browser": True,
                },
            )

        try:
            # Import WebArena's StringEvaluator
            # Create a temporary config file for WebArena
            import tempfile

            from evaluation_harness.evaluators import StringEvaluator

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(config, f)
                config_file = f.name

            # Create trajectory with the answer
            trajectory = [{"answer": final_answer}]

            # Use WebArena's StringEvaluator
            evaluator = StringEvaluator()
            score = evaluator(trajectory, config_file, None, None)

            success = score == 1.0
            return success, score, {"evaluator": "webarena_string_evaluator"}

        except Exception as e:
            logger.error(f"WebArena StringEvaluator failed: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False, 0.0, {"error": str(e)}


# Global WebArena evaluator instance
_webarena_evaluator: Optional[WebArenaEvaluatorWrapper] = None


def get_webarena_evaluator(
    webarena_path: Optional[Path] = None,
) -> WebArenaEvaluatorWrapper:
    """Get or create the WebArena evaluator singleton."""
    global _webarena_evaluator
    if _webarena_evaluator is None:
        _webarena_evaluator = WebArenaEvaluatorWrapper(webarena_path)
    return _webarena_evaluator


class TaskValidatorRegistry:
    """Registry for mapping task IDs to validators."""

    def __init__(self):
        self._validators: Dict[str, TaskValidator] = {}
        self._load_validators()

    def _load_validators(self):
        """
        Auto-discover validators in the tasks directory.

        Looks for Python files that export a 'validator' instance.
        The task ID is derived from the filename (e.g., dashdish_deepresearch1.py -> dashdish-deepresearch1).

        Searches both the tasks directory and llm_eval subdirectory.
        """
        tasks_dir = Path(__file__).parent

        # Directories to search for validators
        search_dirs = [
            (tasks_dir, "experiments.tasks"),
            (tasks_dir / "llm_eval", "experiments.tasks.llm_eval"),
        ]

        for search_dir, module_prefix in search_dirs:
            if not search_dir.exists():
                continue

            for py_file in search_dir.glob("*.py"):
                # Skip __init__, base, registry, and other infrastructure files
                if py_file.stem in [
                    "__init__",
                    "base",
                    "registry",
                    "validation",
                    "accuracy",
                ]:
                    continue

                try:
                    # Import the module using the proper module path
                    module_name = f"{module_prefix}.{py_file.stem}"
                    module = importlib.import_module(module_name)

                    # Check if it has a 'validator' attribute
                    if hasattr(module, "validator") and isinstance(
                        module.validator, TaskValidator
                    ):
                        # Convert filename to task ID (underscore to hyphen)
                        task_id = py_file.stem.replace("_", "-")
                        self._validators[task_id] = module.validator
                        logger.info(f"Registered validator for task: {task_id}")

                except Exception as e:
                    logger.warning(f"Failed to load validator from {py_file}: {e}")

    def get_validator(self, task_id: str) -> Optional[TaskValidator]:
        """
        Get validator for a task ID.

        Args:
            task_id: Task identifier (e.g., "dashdish-deepresearch1")

        Returns:
            TaskValidator instance or None if not found
        """
        return self._validators.get(task_id)

    def has_validator(self, task_id: str) -> bool:
        """Check if a validator exists for the given task ID."""
        return task_id in self._validators

    def list_tasks(self) -> list[str]:
        """Return list of all registered task IDs."""
        return sorted(self._validators.keys())


class UnifiedValidator:
    """
    Unified validator supporting script-based, LLM-based, and WebArena validation.

    - Script-based: Uses eval scripts that check browser state + string matching.
                    Requires final_state from /finish endpoint.
    - LLM-based: Uses LLM to parse structured output and validate against ground truth.
    - WebArena: Uses WebArena's built-in evaluators (string_match, url_match, program_html).
    """

    def __init__(self):
        self._task_registry = TaskValidatorRegistry()
        self._eval_types: Dict[str, str] = {}
        self._script_evaluators: Dict[str, ScriptEvaluator] = {}
        self._webarena_configs: Dict[str, Dict[str, Any]] = {}  # task_id -> config
        self._webarena_raw_configs: Dict[
            int, Dict[str, Any]
        ] = {}  # webarena_task_id -> config
        self._load_webarena_raw_configs()
        self._load_task_configs()
        self._load_script_evaluators()

    def _load_webarena_raw_configs(self):
        """Load WebArena configs from test.raw.json."""
        # Find test.raw.json relative to this file
        webarena_config_paths = [
            Path(__file__).parent.parent.parent
            / "webarena"
            / "config_files"
            / "test.raw.json",
            Path(__file__).parent.parent.parent
            / "webarena"
            / "config_files"
            / "test.json",
        ]

        for config_path in webarena_config_paths:
            if config_path.exists():
                try:
                    with open(config_path) as f:
                        configs = json.load(f)

                    # Replace placeholders with default localhost URLs
                    config_str = json.dumps(configs)
                    replacements = {
                        "__GITLAB__": "http://localhost:8023",
                        "__REDDIT__": "http://localhost:9999",
                        "__SHOPPING__": "http://localhost:7770",
                        "__SHOPPING_ADMIN__": "http://localhost:7780",
                        "__WIKIPEDIA__": "http://localhost:8888",
                        "__MAP__": "http://localhost:3000",
                    }
                    for placeholder, url in replacements.items():
                        config_str = config_str.replace(placeholder, url)
                    configs = json.loads(config_str)

                    for config in configs:
                        wa_task_id = config.get("task_id")
                        if wa_task_id is not None:
                            self._webarena_raw_configs[wa_task_id] = config

                    logger.info(
                        f"Loaded {len(self._webarena_raw_configs)} WebArena configs from {config_path}"
                    )
                    break  # Use first found config file
                except Exception as e:
                    logger.warning(
                        f"Failed to load WebArena configs from {config_path}: {e}"
                    )

    def _load_task_configs(self):
        """Load eval_type from task YAML files."""
        tasks_dir = Path(__file__).parent

        for yaml_file in tasks_dir.glob("*.yaml"):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)

                tasks = data.get("tasks", []) if isinstance(data, dict) else data
                if not isinstance(tasks, list):
                    continue

                for task in tasks:
                    task_id = task.get("id")
                    eval_type = task.get("eval_type", "llm")  # Default to llm
                    if task_id:
                        self._eval_types[task_id] = eval_type
                        logger.debug(f"Loaded eval_type for {task_id}: {eval_type}")

                        # For webarena tasks, load the webarena config
                        if eval_type == "webarena":
                            webarena_task_id = task.get("webarena_task_id")
                            if (
                                webarena_task_id is not None
                                and webarena_task_id in self._webarena_raw_configs
                            ):
                                wa_config = self._webarena_raw_configs[webarena_task_id]
                                self._webarena_configs[task_id] = {
                                    "webarena_config": wa_config,
                                    "webarena_task_id": webarena_task_id,
                                }
                                logger.info(
                                    f"Registered WebArena config for {task_id} (wa_task_id={webarena_task_id})"
                                )
                            else:
                                logger.warning(
                                    f"WebArena task {task_id} has webarena_task_id={webarena_task_id} but config not found"
                                )

            except Exception as e:
                logger.warning(f"Failed to load task config from {yaml_file}: {e}")

    def _load_script_evaluators(self):
        """Load script evaluators from eval_scripts and script_eval directories."""
        tasks_dir = Path(__file__).parent

        # Directories to search for script evaluators
        # Format: (directory, module_prefix, filename_pattern, strip_prefix)
        search_configs = [
            # eval_scripts/ uses eval_*.py pattern and strips "eval_" prefix
            (
                tasks_dir / "eval_scripts",
                "experiments.tasks.eval_scripts",
                "eval_*.py",
                "eval_",
            ),
            # script_eval/ uses *.py pattern directly (no prefix to strip)
            (tasks_dir / "script_eval", "experiments.tasks.script_eval", "*.py", None),
        ]

        for search_dir, module_prefix, pattern, strip_prefix in search_configs:
            if not search_dir.exists():
                continue

            for py_file in search_dir.glob(pattern):
                # Skip __init__ and utility files
                if py_file.stem in ["__init__", "eval_utils", "constants"]:
                    continue

                try:
                    # Extract task ID from filename
                    stem = py_file.stem
                    if strip_prefix and stem.startswith(strip_prefix):
                        # Remove prefix and convert underscores to hyphens
                        task_id = stem[len(strip_prefix) :].replace("_", "-")
                    else:
                        # Just convert underscores to hyphens
                        task_id = stem.replace("_", "-")

                    # Import the module
                    module_name = f"{module_prefix}.{py_file.stem}"
                    module = importlib.import_module(module_name)

                    # Check if it has an 'evaluate' function
                    if hasattr(module, "evaluate") and callable(module.evaluate):
                        self._script_evaluators[task_id] = module.evaluate
                        # Also set eval_type to "script" for this task
                        self._eval_types[task_id] = "script"
                        logger.info(f"Loaded script evaluator for task: {task_id}")

                except Exception as e:
                    logger.warning(
                        f"Failed to load script evaluator from {py_file}: {e}"
                    )

    def get_eval_type(self, task_id: str) -> str:
        """Get the evaluation type for a task."""
        return self._eval_types.get(task_id, "llm")

    def has_script_evaluator(self, task_id: str) -> bool:
        """Check if a script evaluator exists for the task."""
        return task_id in self._script_evaluators

    def has_llm_validator(self, task_id: str) -> bool:
        """Check if an LLM validator exists for the task."""
        return self._task_registry.has_validator(task_id)

    def has_webarena_config(self, task_id: str) -> bool:
        """Check if a WebArena config exists for the task."""
        return task_id in self._webarena_configs

    def register_webarena_task(self, task_id: str, config: Dict[str, Any]) -> None:
        """
        Register a WebArena task for validation.

        Args:
            task_id: Task identifier (e.g., "webarena-0")
            config: WebArena task config dict with 'eval' field
        """
        self._webarena_configs[task_id] = config
        self._eval_types[task_id] = "webarena"
        logger.info(f"Registered WebArena task: {task_id}")

    def get_webarena_config(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the WebArena config for a task."""
        return self._webarena_configs.get(task_id)

    async def validate(
        self,
        task_id: str,
        final_result: str,
        final_state: Optional[Dict[str, Any]] = None,
        return_pct: bool = False,
        page: Any = None,
        trajectory: Optional[List[Any]] = None,
        page_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate task output using the appropriate method.

        Args:
            task_id: Task identifier
            final_result: The agent's final output/answer
            final_state: Browser state from /finish endpoint (required for script validation)
            return_pct: If True, include 'correctness_pct' field
            page: Playwright Page object (required for WebArena url_match/program_html)
            trajectory: List of (Action, StateInfo) pairs (for WebArena evaluation)
            page_url: Current page URL (used when page is browser-use Page)

        Returns:
            dict with:
                - success: bool (whether parsing/validation succeeded)
                - correct: bool (whether output is 100% correct)
                - correctness_pct: float (0.0-1.0, only if return_pct=True)
                - eval_type: str ("script", "llm", or "webarena")
                - details: dict with additional info
        """
        eval_type = self.get_eval_type(task_id)

        if eval_type == "script":
            if final_state is None:
                # Script eval requires final_state - fail explicitly rather than
                # silently falling back to LLM eval (which would be incorrect for
                # action-based tasks like placing orders)
                logger.error(
                    f"Task {task_id} requires script eval but final_state is None. "
                    "Cannot validate without browser state from /finish endpoint."
                )
                result = {
                    "success": False,
                    "correct": False,
                    "eval_type": "script",
                    "details": {
                        "error": "Script eval requires final_state from /finish endpoint, but it was not provided"
                    },
                }
                if return_pct:
                    result["correctness_pct"] = 0.0
                return result
            return self._run_script_eval(task_id, final_state, final_result, return_pct)
        elif eval_type == "webarena":
            return await self._run_webarena_eval(
                task_id,
                final_result,
                return_pct,
                page=page,
                trajectory=trajectory,
                page_url=page_url,
            )
        else:
            return await self._run_llm_eval(task_id, final_result, return_pct)

    def _run_script_eval(
        self,
        task_id: str,
        final_state: Dict[str, Any],
        final_result: str,
        return_pct: bool = False,
    ) -> Dict[str, Any]:
        """Run script-based evaluation."""
        if task_id not in self._script_evaluators:
            logger.warning(f"No script evaluator found for task: {task_id}")
            result = {
                "success": False,
                "correct": False,
                "eval_type": "script",
                "details": {"error": f"No script evaluator for task {task_id}"},
            }
            if return_pct:
                result["correctness_pct"] = 0.0
            return result

        try:
            # Debug: Log what we're passing to the evaluator
            logger.debug(
                f"Script eval for {task_id}: final_state keys = {list(final_state.keys())}"
            )
            logger.debug(
                f"Script eval for {task_id}: final_result = {final_result[:200] if final_result else 'None'}..."
            )

            evaluator = self._script_evaluators[task_id]
            success, percentage, details = evaluator(final_state, final_result)

            # Debug: Log evaluation result details
            logger.info(
                f"Script eval for {task_id}: success={success}, pct={percentage:.0%}, details={details}"
            )

            result = {
                "success": True,
                "correct": success,
                "eval_type": "script",
                "details": details,
            }
            if return_pct:
                result["correctness_pct"] = percentage

            return result

        except Exception as e:
            logger.error(f"Script evaluation failed for {task_id}: {e}")
            import traceback

            logger.error(traceback.format_exc())
            result = {
                "success": False,
                "correct": False,
                "eval_type": "script",
                "details": {"error": str(e)},
            }
            if return_pct:
                result["correctness_pct"] = 0.0
            return result

    async def _run_webarena_eval(
        self,
        task_id: str,
        final_result: str,
        return_pct: bool = False,
        page: Any = None,
        trajectory: Optional[List[Any]] = None,
        page_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run WebArena evaluation.

        Uses WebArena's built-in evaluators. If page is available and eval_types
        include url_match or program_html, uses full browser-based evaluation.
        Otherwise falls back to string-match only evaluation.

        For program_html evaluation, pre-fetches all needed data asynchronously
        before calling the synchronous WebArena evaluator to avoid deadlocks.

        Args:
            page_url: Current page URL (required for browser-use pages)
        """
        config = self._webarena_configs.get(task_id)
        if config is None:
            logger.warning(f"No WebArena config found for task: {task_id}")
            result = {
                "success": False,
                "correct": False,
                "eval_type": "webarena",
                "details": {"error": f"No WebArena config for task {task_id}"},
            }
            if return_pct:
                result["correctness_pct"] = 0.0
            return result

        try:
            webarena_eval = get_webarena_evaluator()
            wa_config = config.get("webarena_config", config)
            eval_config = wa_config.get("eval", {})
            eval_types = eval_config.get("eval_types", [])

            # Check if we need browser for evaluation
            needs_browser = any(t in eval_types for t in ["url_match", "program_html"])
            uses_program_html = "program_html" in eval_types

            if needs_browser and page is not None:
                # Full browser-based evaluation
                config_file = config.get("webarena_config_file")
                if config_file is None:
                    # Create a temporary config file
                    import tempfile

                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".json", delete=False
                    ) as f:
                        json.dump(wa_config, f)
                        config_file = f.name

                # Build a minimal trajectory if not provided
                if trajectory is None:
                    # Create a fake stop action with the answer
                    trajectory = [{"answer": final_result}]

                # For program_html, pre-fetch data asynchronously before calling sync evaluator
                eval_page = page
                if uses_program_html:
                    # Check if this is a browser-use page
                    is_browser_use_page = hasattr(
                        page, "_browser_session"
                    ) or page.__class__.__module__.startswith("browser_use")

                    if is_browser_use_page:
                        browser_session = getattr(page, "_browser_session", None)
                        if browser_session:
                            logger.info(
                                "Pre-fetching program_html data asynchronously..."
                            )
                            eval_page = await prefetch_program_html_data(
                                wa_config,
                                browser_session,
                                page,
                                page_url or "",
                            )
                        else:
                            logger.warning(
                                "No browser session for pre-fetch, using adapter fallback"
                            )
                            eval_page = BrowserUsePageAdapter(page, page_url or "")
                    # else: use native Playwright page directly
                else:
                    # For url_match only, just use the adapter
                    is_browser_use_page = hasattr(
                        page, "_browser_session"
                    ) or page.__class__.__module__.startswith("browser_use")
                    if is_browser_use_page:
                        eval_page = BrowserUsePageAdapter(page, page_url or "")

                success, score, details = webarena_eval.evaluate_with_page(
                    config_file, trajectory, eval_page, page_url=page_url
                )
            else:
                # String-match only evaluation (no browser needed)
                # First try WebArena's native evaluator
                success, score, details = webarena_eval.evaluate_string_match(
                    wa_config, final_result
                )

                # If native evaluator fails (score < 1.0), try LLM judge
                # LLM judge is more lenient with verbose agent responses
                if score < 1.0:
                    reference_answers = eval_config.get("reference_answers", {})
                    task_intent = wa_config.get("intent", "")

                    if reference_answers:
                        logger.info("Native string_match failed, trying LLM judge...")
                        try:
                            llm_success, llm_score, llm_reason = await llm_judge_answer(
                                final_result, reference_answers, task_intent
                            )

                            if llm_score > score:
                                success = llm_success
                                score = llm_score
                                details = {
                                    "evaluator": "llm_judge",
                                    "reason": llm_reason,
                                    "native_score": details.get("native_score", 0.0),
                                }
                                logger.info(f"LLM judge upgraded score: {score:.2f}")
                        except Exception as e:
                            logger.warning(f"LLM judge failed, using native score: {e}")

                if needs_browser and page is None:
                    details["warning"] = (
                        f"eval_types={eval_types} require browser access, "
                        "but page was not provided. Only string_match was evaluated."
                    )

            logger.info(
                f"WebArena eval for {task_id}: success={success}, score={score:.2f}"
            )

            result = {
                "success": True,
                "correct": success,
                "eval_type": "webarena",
                "details": details,
            }
            if return_pct:
                result["correctness_pct"] = score

            return result

        except Exception as e:
            logger.error(f"WebArena evaluation failed for {task_id}: {e}")
            import traceback

            logger.error(traceback.format_exc())
            result = {
                "success": False,
                "correct": False,
                "eval_type": "webarena",
                "details": {"error": str(e)},
            }
            if return_pct:
                result["correctness_pct"] = 0.0
            return result

    async def _run_llm_eval(
        self,
        task_id: str,
        final_result: str,
        return_pct: bool = False,
    ) -> Dict[str, Any]:
        """Run LLM-based evaluation."""
        validator = self._task_registry.get_validator(task_id)

        if validator is None:
            logger.warning(f"No LLM validator found for task: {task_id}")
            result = {
                "success": False,
                "correct": False,
                "eval_type": "llm",
                "details": {"error": f"No LLM validator for task {task_id}"},
            }
            if return_pct:
                result["correctness_pct"] = 0.0
            return result

        try:
            validation = await validator.validate(final_result, return_pct=return_pct)
            result = {
                "success": validation.get("success", False),
                "correct": validation.get("correct", False),
                "eval_type": "llm",
                "details": {"parsed_output": validation.get("parsed_output")},
            }
            if return_pct:
                result["correctness_pct"] = validation.get("correctness_pct", 0.0)

            return result

        except Exception as e:
            logger.error(f"LLM evaluation failed for {task_id}: {e}")
            result = {
                "success": False,
                "correct": False,
                "eval_type": "llm",
                "details": {"error": str(e)},
            }
            if return_pct:
                result["correctness_pct"] = 0.0
            return result


# Global registry instances
_registry = TaskValidatorRegistry()
_unified_validator: Optional[UnifiedValidator] = None


def get_validator(task_id: str) -> Optional[TaskValidator]:
    """Get validator for a task ID (LLM-based validators only)."""
    return _registry.get_validator(task_id)


def has_validator(task_id: str) -> bool:
    """Check if a validator exists for the given task ID."""
    return _registry.has_validator(task_id)


def list_tasks() -> list[str]:
    """Return list of all registered task IDs."""
    return _registry.list_tasks()


def get_unified_validator() -> UnifiedValidator:
    """Get the unified validator singleton."""
    global _unified_validator
    if _unified_validator is None:
        _unified_validator = UnifiedValidator()
    return _unified_validator
