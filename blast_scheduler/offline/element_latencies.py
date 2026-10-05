import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np

from blast_scheduler.laminar.trace_parser import TraceParser
from blast_scheduler.offline.site import SiteRep
from blast_scheduler.online.distributions import find_best_distribution


def get_site_rep_path(site_name: str, model: Optional[str] = None) -> str:
    """Get the path to a site_rep file.

    Args:
        site_name: Name of the site (e.g., 'real-dashdish')
        model: Optional model name (e.g., 'gpt-4.1'). If None, uses default site_rep.json

    Returns:
        Path to the site_rep file
    """
    suffix = f"_{model}" if model else ""
    return f"saved_sites/{site_name}/site_rep{suffix}.json"


def get_element_latencies_path(site_name: str, model: Optional[str] = None) -> str:
    """Get the path to an element_latencies file.

    Args:
        site_name: Name of the site (e.g., 'real-dashdish')
        model: Optional model name (e.g., 'gpt-4.1'). If None, uses default element_latencies.json

    Returns:
        Path to the element_latencies file
    """
    suffix = f"_{model}" if model else ""
    return f"saved_sites/{site_name}/element_latencies{suffix}.json"


@dataclass
class Distribution:
    """Stores a probability distribution with all its parameters and metrics."""

    name: str
    params: List[float]
    bic: float
    ks_stat: float
    ks_p: float
    tail: Dict[str, List[float]]

    @staticmethod
    def _convert_to_native(
        value: Union[np.ndarray, np.number, float, list],
    ) -> Union[float, List[float]]:
        """Convert numpy types to native Python types for JSON serialization."""
        if isinstance(value, np.ndarray):
            return value.tolist()
        elif isinstance(value, (np.number, np.integer, np.floating)):
            return float(value)
        elif isinstance(value, (list, tuple)):
            return [Distribution._convert_to_native(v) for v in value]
        else:
            return value

    def to_dict(self) -> dict:
        """Convert distribution to a JSON-serializable dictionary."""
        return {
            "name": self.name,
            "params": [float(p) for p in self.params],
            "bic": float(self.bic),
            "ks_stat": float(self.ks_stat),
            "ks_p": float(self.ks_p),
            "tail": {
                key: self._convert_to_native(value) for key, value in self.tail.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Distribution":
        """Create a Distribution from a dictionary."""
        return cls(
            name=data["name"],
            params=data["params"],
            bic=data["bic"],
            ks_stat=data["ks_stat"],
            ks_p=data["ks_p"],
            tail=data["tail"],
        )

    @classmethod
    def from_raw_distribution(cls, raw_dist: dict) -> "Distribution":
        """Create a Distribution from the raw output of find_best_distribution."""
        return cls(
            name=raw_dist["name"],
            params=cls._convert_to_native(raw_dist["params"]),
            bic=float(raw_dist["bic"]),
            ks_stat=float(raw_dist["ks_stat"]),
            ks_p=float(raw_dist["ks_p"]),
            tail={
                key: cls._convert_to_native(value)
                for key, value in raw_dist["tail"].items()
            },
        )


@dataclass
class ElementLatency:
    """Stores values and distribution for an element."""

    element_name: str
    values: Optional[List[float]] = field(default_factory=list)
    distribution: Optional[Distribution] = None
    median: Optional[float] = None
    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    percentile_90: Optional[float] = None
    percentile_95: Optional[float] = None
    percentile_99: Optional[float] = None

    def compute_statistics(self) -> None:
        """Compute summary statistics from values."""
        if not self.values:
            return

        arr = np.array(self.values)
        self.mean = float(np.mean(arr))
        self.median = float(np.median(arr))
        self.std = float(np.std(arr))
        self.min = float(np.min(arr))
        self.max = float(np.max(arr))
        self.percentile_90 = float(np.percentile(arr, 90))
        self.percentile_95 = float(np.percentile(arr, 95))
        self.percentile_99 = float(np.percentile(arr, 99))

    def to_dict(self) -> dict:
        return {
            "element_name": self.element_name,
            "values": [float(value) for value in self.values],
            "distribution": self.distribution.to_dict() if self.distribution else None,
            "median": self.median,
            "mean": self.mean,
            "std": self.std,
            "min": self.min,
            "max": self.max,
            "percentile_90": self.percentile_90,
            "percentile_95": self.percentile_95,
            "percentile_99": self.percentile_99,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ElementLatency":
        distribution = None
        if data.get("distribution"):
            distribution = Distribution.from_dict(data["distribution"])
        return cls(
            element_name=data["element_name"],
            values=data.get("values", []),
            distribution=distribution,
            median=data.get("median"),
            mean=data.get("mean"),
            std=data.get("std"),
            min=data.get("min"),
            max=data.get("max"),
            percentile_90=data.get("percentile_90"),
            percentile_95=data.get("percentile_95"),
            percentile_99=data.get("percentile_99"),
        )


class LatencyDistributionBuilder:
    def __init__(self, site_file: str):
        self.site: SiteRep = SiteRep.from_file(site_file)
        self.site_file = Path(site_file)
        self.suffix = self._extract_suffix()
        self.trace_steps_dir = TraceParser.output_dir()
        self.trace_ids = self._get_all_trace_ids()

        self._verify_trace_steps()

        self.element_latencies: Dict[str, ElementLatency] = {}

    def _extract_suffix(self) -> str:
        """Extract suffix from site file name (e.g., 'site_rep_gpt-4.1.json' -> '_gpt-4.1')."""
        stem = self.site_file.stem  # e.g., 'site_rep_gpt-4.1'
        if stem.startswith("site_rep"):
            suffix = stem[len("site_rep") :]  # e.g., '_gpt-4.1' or ''
            return suffix
        return ""

    def _get_all_trace_ids(self) -> set[str]:
        """Get all trace IDs from the site representation."""
        trace_ids = set()
        for element_name in self.site.element_mappings:
            for trace_id in self.site.element_mappings[element_name]:
                trace_ids.add(trace_id)
        return trace_ids

    def _verify_trace_steps(self) -> None:
        """Verify that trace step files exist for all trace IDs in site_rep."""
        missing_traces = []
        for trace_id in self.trace_ids:
            trace_step_file = self.trace_steps_dir / f"{trace_id}.json"
            if not trace_step_file.exists():
                missing_traces.append(trace_id)

        if missing_traces:
            print(
                f"Warning: Missing trace step files for {len(missing_traces)} traces:"
            )
            for trace_id in missing_traces[:5]:  # Show first 5
                print(f"  - {trace_id}")
            if len(missing_traces) > 5:
                print(f"  ... and {len(missing_traces) - 5} more")
            print("\nThese traces exist in site_rep but trace step files are missing.")
            print(f"Trace steps should be in: {self.trace_steps_dir}")
            print("You may need to run TraceParser on the original trace files first.")

    def get_latency(self, trace_id: str, step: int) -> float:
        """Get the total latency for a specific step in a trace."""
        with open(f"{self.trace_steps_dir}/{trace_id}.json", "r") as f:
            data = json.load(f)
        return data[step]["total_latency"]

    def compute_all_element_latencies(self) -> Dict[str, ElementLatency]:
        """Compute latency values for all elements in the site."""
        element_names = self.site.get_element_names()

        element_latency_values: Dict[str, List[float]] = defaultdict(list)

        for element_name in element_names:
            for trace_id, step_ids in self.site.element_mappings[element_name].items():
                total_latency_in_trace = 0
                for step_id in step_ids:
                    total_latency_in_trace += self.get_latency(trace_id, step_id)
                element_latency_values[element_name].append(total_latency_in_trace)

        for element_name, latency_values in element_latency_values.items():
            element_latency = ElementLatency(element_name)
            element_latency.values = latency_values
            element_latency.compute_statistics()
            self.element_latencies[element_name] = element_latency

        return self.element_latencies

    def fit_distributions(self) -> Dict[str, ElementLatency]:
        """Fit probability distributions to all element latencies."""
        if not self.element_latencies:
            self.compute_all_element_latencies()

        constant_count = 0
        fitted_count = 0

        for element_name, element_latency in self.element_latencies.items():
            raw_distribution = find_best_distribution(element_latency.values)
            distribution = Distribution.from_raw_distribution(raw_distribution)
            self.element_latencies[element_name].distribution = distribution

            if distribution.name == "constant":
                constant_count += 1
            else:
                fitted_count += 1

        print("Distribution fitting complete:")
        print(f"  - {fitted_count} elements with fitted distributions")
        print(f"  - {constant_count} elements using constant (insufficient data)")

        return self.element_latencies

    def save_element_latencies(self, output_file: Optional[str] = None) -> str:
        """Save element latencies and distributions to a JSON file."""
        if not self.element_latencies:
            raise ValueError(
                "No element latencies computed. Call compute_all_element_latencies() first."
            )

        if output_file is None:
            sites_dir = Path("saved_sites") / self.site.site_name
            sites_dir.mkdir(parents=True, exist_ok=True)
            # Use suffix from site file (e.g., '_gpt-4.1' -> 'element_latencies_gpt-4.1.json')
            output_file = str(sites_dir / f"element_latencies{self.suffix}.json")

        # Convert to serializable format
        data = {
            "site_name": self.site.site_name,
            "element_latencies": {
                name: latency.to_dict()
                for name, latency in self.element_latencies.items()
            },
        }

        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)

        print(f"Saved element latencies to {output_file}")
        return output_file

    @classmethod
    def load_element_latencies(cls, file_path: str) -> Dict[str, ElementLatency]:
        """
        Load element latencies from a JSON file.

        Args:
            file_path: Path to the element latencies JSON file

        Returns:
            Dictionary mapping element names to ElementLatency objects
        """
        with open(file_path, "r") as f:
            data = json.load(f)

        element_latencies = {}
        for name, latency_dict in data["element_latencies"].items():
            element_latencies[name] = ElementLatency.from_dict(latency_dict)

        return element_latencies

    @classmethod
    def from_site_file(cls, site_file: str) -> "LatencyDistributionBuilder":
        """
        Convenience factory method to create builder from site file.

        Args:
            site_file: Path to the site representation JSON file

        Returns:
            LatencyDistributionBuilder instance
        """
        return cls(site_file=site_file)

    def _get_output_file_path(self, output_file: Optional[str] = None) -> str:
        """Get the output file path for element latencies."""
        if output_file is None:
            sites_dir = Path("saved_sites") / self.site.site_name
            return str(sites_dir / f"element_latencies{self.suffix}.json")
        return output_file

    def _load_existing_latencies(self, output_file: str) -> Dict[str, ElementLatency]:
        """Load existing element latencies if the file exists."""
        if Path(output_file).exists():
            try:
                return self.load_element_latencies(output_file)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Could not load existing latencies: {e}")
                return {}
        return {}

    def build_and_save(
        self, output_file: Optional[str] = None, force_recompute: bool = False
    ) -> str:
        """
        Complete workflow: compute latencies, fit distributions, and save.

        If element latencies already exist on disk, only computes missing elements.

        Args:
            output_file: Optional path to save the file
            force_recompute: If True, recompute all latencies even if cached

        Returns:
            Path to the saved file
        """
        output_path = self._get_output_file_path(output_file)

        # Check for existing latencies
        existing_latencies = {}
        if not force_recompute:
            existing_latencies = self._load_existing_latencies(output_path)
            if existing_latencies:
                print(f"Found {len(existing_latencies)} cached element latencies")

        # Determine which elements need computation
        all_element_names = set(self.site.get_element_names())
        cached_element_names = set(existing_latencies.keys())
        missing_element_names = all_element_names - cached_element_names

        if not missing_element_names and existing_latencies:
            print("All element latencies already cached. Nothing to compute.")
            self.element_latencies = existing_latencies
            return output_path

        if missing_element_names:
            print(
                f"Computing latencies for {len(missing_element_names)} new elements "
                f"(skipping {len(cached_element_names)} cached)..."
            )
        else:
            print(f"Processing {len(self.trace_ids)} traces from site_rep...")
            print("Computing element latencies...")

        # Compute only missing elements
        self._compute_element_latencies_for(missing_element_names)

        # Merge with existing latencies
        self.element_latencies.update(existing_latencies)

        # Fit distributions only for newly computed elements
        if missing_element_names:
            print(f"Fitting distributions for {len(missing_element_names)} elements...")
            self._fit_distributions_for(missing_element_names)

        print("Saving results...")
        return self.save_element_latencies(output_file)

    def _compute_element_latencies_for(self, element_names: set[str]) -> None:
        """Compute latency values for a specific set of elements."""
        if not element_names:
            return

        element_latency_values: Dict[str, List[float]] = defaultdict(list)

        for element_name in element_names:
            if element_name not in self.site.element_mappings:
                continue
            for trace_id, step_ids in self.site.element_mappings[element_name].items():
                # Skip if trace step file doesn't exist
                trace_step_file = self.trace_steps_dir / f"{trace_id}.json"
                if not trace_step_file.exists():
                    continue

                total_latency_in_trace = 0
                for step_id in step_ids:
                    total_latency_in_trace += self.get_latency(trace_id, step_id)
                element_latency_values[element_name].append(total_latency_in_trace)

        for element_name, latency_values in element_latency_values.items():
            element_latency = ElementLatency(element_name)
            element_latency.values = latency_values
            element_latency.compute_statistics()
            self.element_latencies[element_name] = element_latency

    def _fit_distributions_for(self, element_names: set[str]) -> None:
        """Fit probability distributions for a specific set of elements."""
        constant_count = 0
        fitted_count = 0

        for element_name in element_names:
            if element_name not in self.element_latencies:
                continue
            element_latency = self.element_latencies[element_name]
            if not element_latency.values:
                continue

            raw_distribution = find_best_distribution(element_latency.values)
            distribution = Distribution.from_raw_distribution(raw_distribution)
            self.element_latencies[element_name].distribution = distribution

            if distribution.name == "constant":
                constant_count += 1
            else:
                fitted_count += 1

        print("Distribution fitting complete:")
        print(f"  - {fitted_count} elements with fitted distributions")
        print(f"  - {constant_count} elements using constant (insufficient data)")
