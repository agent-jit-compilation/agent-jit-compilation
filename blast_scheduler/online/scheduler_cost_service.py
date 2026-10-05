import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

from blast_scheduler.offline.element_latencies import (
    ElementLatency,
    LatencyDistributionBuilder,
)
from blast_scheduler.online.scheduler import (
    ElementUsageCounts,
    ParallelUsageCounts,
    SchedulerPlan,
)

SequentialUsage = Union[SchedulerPlan, ElementUsageCounts]

GPT_4_1_FIRST_OF_N_OVERHEAD = 15.0
GPT_4_1_PARALLEL_OVERHEAD = 30.0
GPT_4_1_PAGE_READ_COST = 10.0
GPT_4_1_REPEAT_INTERACTION_COST = 7.0

GEMINI_2_5_PRO_FIRST_OF_N_OVERHEAD = 5
GEMINI_2_5_PRO_PARALLEL_OVERHEAD = 20
GEMINI_2_5_PRO_PAGE_READ_COST = 5
GEMINI_2_5_PRO_REPEAT_INTERACTION_COST = 5

GEMINI_2_5_FLASH_FIRST_OF_N_OVERHEAD = 15.0
GEMINI_2_5_FLASH_PARALLEL_OVERHEAD = 30.0
GEMINI_2_5_FLASH_PAGE_READ_COST = 10.0
GEMINI_2_5_FLASH_REPEAT_INTERACTION_COST = 7.0

FIRST_OF_N_OVERHEAD = GEMINI_2_5_PRO_FIRST_OF_N_OVERHEAD
PARALLEL_OVERHEAD = GEMINI_2_5_PRO_PARALLEL_OVERHEAD
PAGE_READ_COST = GEMINI_2_5_PRO_PAGE_READ_COST
REPEAT_INTERACTION_COST = GEMINI_2_5_PRO_REPEAT_INTERACTION_COST


class SchedulerCostService:
    def __init__(
        self,
        element_latencies_file: Optional[str] = None,
        site_name: Optional[str] = None,
        model: Optional[str] = None,
        first_of_n_overhead: float = FIRST_OF_N_OVERHEAD,
        parallel_overhead: float = PARALLEL_OVERHEAD,
        page_read_cost: float = PAGE_READ_COST,
        repeat_interaction_cost: float = REPEAT_INTERACTION_COST,
        schema_root: Optional[str] = None,
    ):
        """
        Initialize the SchedulerCostService.

        Args:
            element_latencies_file: Path to pre-computed element latencies JSON with fitted distributions.
                If not provided, will be derived from site_name and model.
            site_name: Name of the site (e.g., 'real-dashdish'). Required if element_latencies_file not provided.
            model: Model name (e.g., 'gpt-4.1') for loading model-specific latencies.
                Ignored if element_latencies_file is provided.
            first_of_n_overhead: Overhead in seconds for first-of-N scheduling
            parallel_overhead: Overhead in seconds for parallel scheduling
            page_read_cost: Fixed cost in seconds for reading each page/modal (default 10s)
            repeat_interaction_cost: Fixed cost in seconds for repeat interactions with the same element (default 10s).
                The first interaction uses sampled cost (finding), subsequent ones use this fixed cost.
        """
        # Resolve element latencies file path
        if element_latencies_file is None:
            if site_name is None:
                raise ValueError(
                    "Either element_latencies_file or site_name must be provided"
                )
            from blast_scheduler.offline.element_latencies import (
                get_element_latencies_path,
            )

            element_latencies_file = get_element_latencies_path(site_name, model)

        if not Path(element_latencies_file).exists():
            raise FileNotFoundError(
                f"Element latencies file not found: {element_latencies_file}\n"
                "Please run the offline element latency computation first using LatencyDistributionBuilder."
            )

        self.element_latencies: Dict[str, ElementLatency] = (
            LatencyDistributionBuilder.load_element_latencies(element_latencies_file)
        )

        self.first_of_n_overhead = first_of_n_overhead
        self.parallel_overhead = parallel_overhead
        self.page_read_cost = page_read_cost
        self.repeat_interaction_cost = repeat_interaction_cost
        self._schema_root = Path(schema_root) if schema_root else Path("saved_schemas")
        self._site_name = site_name
        self._navigation_elements: Set[str] = self._load_navigation_elements(site_name)

    def _load_navigation_elements(self, site_name: Optional[str]) -> Set[str]:
        """Load schemas and identify elements with opensPage/opensModal.

        Args:
            site_name: Name of the site to load schemas for

        Returns:
            Set of element names that trigger page/modal navigation
        """
        if site_name is None:
            return set()

        nav_elements: Set[str] = set()
        schema_dir = self._schema_root / site_name

        if not schema_dir.exists():
            return set()

        for schema_file in schema_dir.glob("*.json"):
            with open(schema_file) as f:
                schema = json.load(f)
            for def_name, def_value in schema.get("$defs", {}).items():
                if not isinstance(def_value, dict):
                    continue
                props = def_value.get("properties", {})
                if "opensPage" in props or "opensModal" in props:
                    nav_elements.add(def_name)

        return nav_elements

    def _find_best_match(self, element_name: str) -> Optional[str]:
        """Find the best matching element name using suffix matching.

        Tries to find an element whose name is a suffix of the requested element,
        or vice versa. For example, 'checkoutQuantityDropdown' would match 'quantityDropdown'.

        Args:
            element_name: Name of the element to find a match for

        Returns:
            Best matching element name, or None if no match found
        """
        element_lower = element_name.lower()
        available_elements = list(self.element_latencies.keys())

        # Try to find elements where:
        # 1. The available element name is a suffix of the requested name (case-insensitive)
        # 2. The requested name is a suffix of an available element name
        best_match = None
        best_match_len = 0

        for avail in available_elements:
            avail_lower = avail.lower()

            # Check if available element is a suffix of requested element
            if element_lower.endswith(avail_lower) and len(avail) > best_match_len:
                best_match = avail
                best_match_len = len(avail)

            # Check if requested element is a suffix of available element
            if avail_lower.endswith(element_lower) and len(element_name) > best_match_len:
                best_match = avail
                best_match_len = len(element_name)

        return best_match

    def _sample_element(self, element_name: str) -> float:
        """Sample latency for a single element interaction.

        Args:
            element_name: Name of the element to sample

        Returns:
            Sampled latency in milliseconds
        """
        from blast_scheduler.online.distributions import CANDIDATES

        if element_name not in self.element_latencies:
            # Try fuzzy matching
            best_match = self._find_best_match(element_name)
            if best_match:
                print(f"Note: Element '{element_name}' not found, using '{best_match}'")
                element_name = best_match
            else:
                print(f"Warning: Element {element_name} not found in latencies, using 0")
                return 0.0

        dist_info = self.element_latencies[element_name]
        dist_name = dist_info.distribution.name
        params = dist_info.distribution.params

        # If single value, use the value directly
        if dist_name == "constant":
            return params[0]
        else:
            # Sample from the distribution
            dist = CANDIDATES[dist_name]
            return dist.rvs(*params)

    def _sample_usage_counts(
        self, element_counts: List, use_repeat_cost: bool = True
    ) -> float:
        """Sample total latency for a list of element usage counts.

        For each element type, the first interaction uses the sampled cost (finding the element),
        while subsequent interactions use a fixed repeat cost (already know where it is).

        Args:
            element_counts: List of ElementCount objects
            use_repeat_cost: If True, use fixed cost for repeat interactions (default).
                If False, sample full cost for every interaction.

        Returns:
            Total sampled latency in seconds
        """
        total = 0.0
        for elem_count in element_counts:
            # Skip read operations - they have zero latency cost
            if getattr(elem_count, "operation_type", "interact") == "read":
                continue

            if elem_count.count <= 0:
                continue

            # First interaction: sample the full cost (finding the element)
            total += self._sample_element(elem_count.element_name)

            # Subsequent interactions: use fixed repeat cost or sample
            if elem_count.count > 1:
                if use_repeat_cost:
                    total += (elem_count.count - 1) * self.repeat_interaction_cost
                else:
                    for _ in range(elem_count.count - 1):
                        total += self._sample_element(elem_count.element_name)

        return total

    def _get_required_elements(self, usage: SequentialUsage) -> List:
        """Get only required elements from usage (those with required=True and count > 0)."""
        required = []
        for elem in usage.elements:
            # Check if element has required attribute (SchedulerPlan) or assume required (ElementUsageCounts)
            if hasattr(elem, "required"):
                if elem.required and elem.count > 0:
                    required.append(elem)
            else:
                # ElementUsageCounts doesn't have required field, use all
                required.append(elem)
        return required

    def _count_navigations(self, usage: SequentialUsage) -> int:
        """Count elements that trigger page/modal navigation.

        Args:
            usage: Element usage counts

        Returns:
            Total number of navigation actions (clicks on elements with opensPage/opensModal)
        """
        count = 0
        for elem in usage.elements:
            if elem.count > 0 and elem.element_name in self._navigation_elements:
                count += elem.count
        return count

    def sample_sequential_cost(self, usage: SequentialUsage) -> float:
        """Sample cost for sequential execution.

        Only uses required elements (required=True with count > 0).
        Includes page read cost: 1 for starting page + count of navigation actions.

        Args:
            usage: Element usage counts for sequential execution (SchedulerPlan or ElementUsageCounts)

        Returns:
            Sampled cost in seconds
        """
        required_elements = self._get_required_elements(usage)
        base_cost = self._sample_usage_counts(required_elements)

        # Add read cost: 1 for starting page + navigations
        num_pages = 1 + self._count_navigations(usage)
        return base_cost + (num_pages * self.page_read_cost)

    def sample_first_of_n_cost(self, usage: SequentialUsage, num_workers: int) -> float:
        """Sample cost for first-of-N execution.

        Takes minimum across num_workers samples (race condition).
        Only uses required elements.
        Includes page read cost: 1 for starting page + count of navigation actions.

        Args:
            usage: Element usage counts (same task repeated by each worker)
            num_workers: Number of parallel workers racing

        Returns:
            Sampled cost in seconds (min + overhead + page read cost)
        """
        required_elements = self._get_required_elements(usage)

        # Add read cost: 1 for starting page + navigations
        num_pages = 1 + self._count_navigations(usage)
        page_cost = num_pages * self.page_read_cost

        samples = [
            self._sample_usage_counts(required_elements) + page_cost
            for _ in range(num_workers)
        ]
        return min(samples) + self.first_of_n_overhead

    def sample_parallel_cost(
        self,
        sequential_usage: SequentialUsage,
        parallel_usage: ParallelUsageCounts,
        batch_size: int = 4,
    ) -> float:
        """Sample total cost for parallel execution strategy.

        Total cost = sequential portion (non-parallel elements) + parallel portion + overhead

        Workers are processed in batches (default 4). Within each batch, workers run
        in parallel (take max). Batches run sequentially (sum of batch maxes).
        E.g., 5 workers = max(workers 0-3) + max(worker 4).

        If no parallelizable elements, returns same as sequential cost (no benefit).
        Only uses required elements from sequential_usage.

        Args:
            sequential_usage: Full sequential element usage
            parallel_usage: Parallelizable element usage counts
            batch_size: Number of workers to run in parallel per batch (default 4)

        Returns:
            Sampled total cost in seconds
        """
        required_elements = self._get_required_elements(sequential_usage)

        # If no parallelizable elements, parallel strategy = sequential (no benefit)
        if not parallel_usage.parallel_elements:
            return self.sample_sequential_cost(sequential_usage)

        # Identify which elements are parallelized (including rediscovery)
        parallel_elem_names = {
            elem.element_name for elem in parallel_usage.parallel_elements
        }
        rediscovery_elem_names = {
            elem.element_name for elem in parallel_usage.rediscovery_elements
        }
        parallelized_names = parallel_elem_names | rediscovery_elem_names

        # 1. Sequential portion: required elements NOT being parallelized
        # Filter to only non-parallelized elements and use _sample_usage_counts
        non_parallel_elements = [
            elem
            for elem in required_elements
            if elem.element_name not in parallelized_names
        ]
        sequential_portion = self._sample_usage_counts(non_parallel_elements)

        # 2. Parallel portion: process workers in batches
        # Within each batch, workers run in parallel (take max)
        # Batches run sequentially (sum the max of each batch)
        worker_costs = []
        for _ in range(parallel_usage.num_workers):
            worker_cost = 0.0

            # Add rediscovery cost if required
            if (
                parallel_usage.requires_rediscovery
                and parallel_usage.rediscovery_elements
            ):
                worker_cost += self._sample_usage_counts(
                    parallel_usage.rediscovery_elements
                )

            # Add parallel elements cost
            if parallel_usage.parallel_elements:
                worker_cost += self._sample_usage_counts(
                    parallel_usage.parallel_elements
                )

            worker_costs.append(worker_cost)

        # Calculate total parallel portion by summing max of each batch
        parallel_portion = 0.0
        for i in range(0, len(worker_costs), batch_size):
            batch = worker_costs[i : i + batch_size]
            parallel_portion += max(batch) if batch else 0.0

        # Total = sequential portion + parallel portion + overhead
        return sequential_portion + parallel_portion + self.parallel_overhead

    def calculate_parallel_savings(
        self, sequential_usage: SequentialUsage, parallel_usage: ParallelUsageCounts
    ) -> float:
        """Calculate latency savings from using parallel execution (single trial).

        Compares full sequential execution vs parallel execution.

        Args:
            sequential_usage: Full sequential element usage
            parallel_usage: Parallel element usage (only parallelizable elements)

        Returns:
            Latency savings in milliseconds (positive = savings, negative = slower)
        """
        # Sequential baseline: full task
        sequential_cost = self.sample_sequential_cost(sequential_usage)

        # Parallel cost: sequential non-parallel + max(workers) + overhead
        parallel_cost = self.sample_parallel_cost(sequential_usage, parallel_usage)

        # Savings = sequential - parallel
        return sequential_cost - parallel_cost

    def calculate_first_of_n_savings(
        self, sequential_usage: SequentialUsage, num_workers: int
    ) -> float:
        """Calculate latency savings from using first-of-N execution (single trial).

        Compares full sequential execution vs first-of-N racing.

        Args:
            sequential_usage: Sequential element usage
            num_workers: Number of workers for first-of-N

        Returns:
            Latency savings in milliseconds (positive = savings, negative = slower)
        """
        # Sequential baseline: full task
        sequential_cost = self.sample_sequential_cost(sequential_usage)

        # First-of-N cost: min of workers + overhead
        first_of_n_cost = self.sample_first_of_n_cost(sequential_usage, num_workers)

        # Savings = sequential - first_of_n
        return sequential_cost - first_of_n_cost

    def compare_strategies(
        self,
        sequential_usage: SequentialUsage,
        parallel_usage: Optional[ParallelUsageCounts] = None,
        num_first_of_n_workers: int = 3,
        num_trials: int = 1000,
    ) -> Dict[str, float]:
        """Alias for estimate_strategy_times for backward compatibility."""
        return self.estimate_strategy_times(
            sequential_usage, parallel_usage, num_first_of_n_workers, num_trials
        )

    def estimate_strategy_times(
        self,
        sequential_usage: SequentialUsage,
        parallel_usage: Optional[ParallelUsageCounts] = None,
        num_first_of_n_workers: int = 3,
        num_trials: int = 1000,
    ) -> Dict[str, float]:
        """Estimate mean execution time for each strategy via Monte Carlo sampling.

        Args:
            sequential_usage: Element usage for sequential execution (SchedulerPlan or ElementUsageCounts)
            parallel_usage: Element usage for parallel execution (optional)
            num_first_of_n_workers: Number of workers for first-of-N strategy
            num_trials: Number of Monte Carlo trials

        Returns:
            Dict with strategy names and their mean estimated time in seconds
        """
        times = {
            "sequential": [],
            "first_of_n": [],
        }
        if parallel_usage is not None:
            times["parallel"] = []

        for _ in range(num_trials):
            # Sequential time
            sequential_time = self.sample_sequential_cost(sequential_usage)
            times["sequential"].append(sequential_time)

            # First-of-N time
            first_of_n_time = self.sample_first_of_n_cost(
                sequential_usage, num_first_of_n_workers
            )
            times["first_of_n"].append(first_of_n_time)

            # Parallel time (if parallel usage provided)
            if parallel_usage is not None:
                parallel_time = self.sample_parallel_cost(
                    sequential_usage, parallel_usage
                )
                times["parallel"].append(parallel_time)

        # Calculate mean times (already in seconds)
        mean_times = {}
        for strategy, samples in times.items():
            mean_times[strategy] = sum(samples) / len(samples)

        return mean_times

    def choose_best_strategy(
        self,
        sequential_usage: SequentialUsage,
        parallel_usage: Optional[ParallelUsageCounts] = None,
        num_first_of_n_workers: int = 4,
        num_trials: int = 1000,
        mode: str = "win_rate",
    ) -> str:
        """Choose the best execution strategy based on Monte Carlo simulation.

        Args:
            sequential_usage: Element usage for sequential execution (SchedulerPlan or ElementUsageCounts)
            parallel_usage: Element usage for parallel execution (optional)
            num_first_of_n_workers: Number of workers for first-of-N strategy
            num_trials: Number of Monte Carlo trials
            mode: Selection mode - "mean" (lowest average latency) or "win_rate" (highest probability of winning)

        Returns:
            Name of the best strategy: "sequential", "first_of_n", or "parallel"
        """
        if mode not in ("mean", "win_rate"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'mean' or 'win_rate'")

        wins = {
            "sequential": 0,
            "first_of_n": 0,
        }
        totals = {
            "sequential": 0.0,
            "first_of_n": 0.0,
        }
        if parallel_usage is not None:
            wins["parallel"] = 0
            totals["parallel"] = 0.0

        for _ in range(num_trials):
            # Sample costs for each strategy
            trial_costs = {
                "sequential": self.sample_sequential_cost(sequential_usage),
                "first_of_n": self.sample_first_of_n_cost(
                    sequential_usage, num_first_of_n_workers
                ),
            }
            if parallel_usage is not None:
                trial_costs["parallel"] = self.sample_parallel_cost(
                    sequential_usage, parallel_usage
                )

            # Accumulate totals for mean calculation
            for strategy, cost in trial_costs.items():
                totals[strategy] += cost

            # Find winner (lowest cost)
            winner = min(trial_costs, key=trial_costs.get)
            wins[winner] += 1

        # Calculate metrics
        mean_times = {
            strategy: total / num_trials for strategy, total in totals.items()
        }
        win_rates = {strategy: count / num_trials for strategy, count in wins.items()}

        # Expose the same simulation for the artifact CLI without a second stochastic run.
        self.last_comparison = {"mean_times": mean_times, "win_rates": win_rates}

        # Print results
        print(f"Strategy comparison ({num_trials} trials, mode={mode}):")
        print("  Mean latencies:")
        for strategy, mean in sorted(mean_times.items(), key=lambda x: x[1]):
            print(f"    {strategy}: {mean:.2f}s")
        print("  Win rates:")
        for strategy, rate in sorted(win_rates.items(), key=lambda x: -x[1]):
            print(f"    {strategy}: {rate:.1%}")

        # Return best strategy based on mode
        if mode == "mean":
            best = min(mean_times, key=mean_times.get)
        else:  # win_rate
            best = max(win_rates, key=win_rates.get)

        print(f"  Selected ({mode}): {best}")
        return best
